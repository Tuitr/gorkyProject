"""Class report contract, attempt selection and immutable snapshots."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from contracts.v1.grading.assignmentreport import AssignmentReport
from contracts.v1.grading.checkresult import MarkdownRef
from contracts.v1.grading.taskresult import Issue, TaskResult
from grading.inbox import saveEvent
from grading.intake import incomingEventAdapter
from grading.main import createApp
from grading.reports import ReportStore
from grading.results import ResultConflict, ResultStore
from grading.settings import Settings
from grading.tables import AssignmentJob
from shared.clock import ManualClock


def testReportSelectsAcceptedAttemptAndPinsEachRevision(
    gradingEngine: Engine, contractExample: Callable[[str], Any],
) -> None:
    tasks = [{"taskId": "t1", "taskNumber": "1", "statement": "Посчитайте сумму.", "requirements": []}]
    with Session(gradingEngine) as session, session.begin():
        saveEvent(session, incomingEventAdapter.validate_python(contractExample("events/assignmentPublished.json")))
        session.get_one(AssignmentJob, ("w1", 1)).stageData = {"tasks": tasks}
        for index, student in enumerate(("s1", "s1", "s2", "s3"), start=1):
            event = contractExample("events/attemptSubmitted.json")
            event.update(eventId=f"submission-{index}", occurredAt=f"2026-11-20T09:{index:02d}:00Z")
            event["payload"].update(checkId=f"c{index}", attemptId=f"a{index}", studentId=student)
            saveEvent(session, incomingEventAdapter.validate_python(event))

    root = Issue(issueId="i1", stepId="s1", category="sign_error", comment="Потерян знак.", correction="−2")
    second = root.model_copy(update={"issueId": "i2", "stepId": "s2"})
    consequence = root.model_copy(update={"issueId": "i3", "stepId": "s3", "causedByIssueId": "i1"})
    wrong = TaskResult(taskId="t1", answer="incorrect", reasoning="invalid", method="optional",
                       issues=[root, second, consequence], needsReview=False)
    right = TaskResult(taskId="t1", answer="correct", reasoning="valid", method="optional", issues=[], needsReview=False)
    skipped = TaskResult(taskId="t1", answer="skipped", reasoning="not_shown", method="optional", issues=[], needsReview=False)
    clock = ManualClock()
    store = ResultStore(gradingEngine, clock.now)

    for checkId, result in (("c1", wrong), ("c2", right), ("c3", skipped)):
        outcome = store.saveDraft(checkId, "initial", (result,), "")
        if checkId == "c1":
            with pytest.raises(ResultConflict):
                store.recordAcceptance(checkId, outcome.resultRev)
        store.publish(checkId, outcome.resultRev, MarkdownRef(bucket="results", key=checkId, sha256="a" * 64), lambda _: None)

    settings = Settings(serviceTokensJson=json.dumps({
        "test": {"serviceName": "cabinet", "rights": ["reports.read"]}, "no-reports": {"serviceName": "other"},
    }))
    path = "/internal/v1/assignments/w1/report"
    headers = {"Authorization": "Bearer test"}
    with TestClient(createApp(settings, engine=gradingEngine)) as api:
        assert api.get(path).status_code == 401
        assert api.get(path, headers={"Authorization": "Bearer no-reports"}).status_code == 403
        assert api.get(path, params={"rev": 1}, headers=headers).status_code == 404
        response = api.get(path, headers=headers)
        assert response.status_code == 200
        draft = AssignmentReport.model_validate(response.json())
        assert draft.rev == 1 and draft.studentCount == 2
        assert [student.checkId for student in draft.students] == ["c2", "c3"]
        assert draft.tasks[0].correctShare == 1 / 3

        store.recordAcceptance("c1", 1)
        accepted = AssignmentReport.model_validate(api.get(path, headers=headers).json())
        assert accepted == AssignmentReport.model_validate(contractExample("grading/assignmentReport.json"))
        assert api.get(path, headers=headers).json() == accepted.model_dump(mode="json")

        with Session(gradingEngine) as session, session.begin():
            resubmission = contractExample("events/attemptSubmitted.json")
            resubmission.update(eventId="submission-5", occurredAt="2026-11-20T09:05:00Z")
            resubmission["payload"].update(checkId="c5", attemptId="a5", studentId="s1")
            saveEvent(session, incomingEventAdapter.validate_python(resubmission))
        draftResubmission = store.saveDraft("c5", "initial", (right,), "")
        store.publish("c5", draftResubmission.resultRev,
                      MarkdownRef(bucket="results", key="c5", sha256="a" * 64), lambda _: None)
        assert api.get(path, headers=headers).json() == accepted.model_dump(mode="json")

        correction = store.saveDraft("c1", "teacher-edit", (right,), "")
        assert correction.resultRev == 2
        assert api.get(path, headers=headers).json() == accepted.model_dump(mode="json")
        store.publish("c1", 2, MarkdownRef(bucket="results", key="c1-edited", sha256="b" * 64), lambda _: None)
        assert api.get(path, headers=headers).json() == accepted.model_dump(mode="json")
        store.recordAcceptance("c1", 2)
        revised = AssignmentReport.model_validate(api.get(path, headers=headers).json())
        assert revised.rev == 3 and revised.students[0].selection == "accepted"
        assert (revised.students[0].checkId, revised.students[0].resultRev) == ("c1", 2)
        assert revised.tasks[0].answer["correct"] == revised.tasks[0].answer["skipped"] == 1
        assert revised.tasks[0].categories == []
        assert api.get(path, params={"rev": 2}, headers=headers).json() == accepted.model_dump(mode="json")

        with Session(gradingEngine) as session, session.begin():
            event = contractExample("events/assignmentPublished.json")
            event.update(eventId="assignment-revision-2")
            event["payload"]["assignmentRev"] = 2
            saveEvent(session, incomingEventAdapter.validate_python(event))
            session.get_one(AssignmentJob, ("w1", 2)).stageData = {"tasks": tasks}
        empty = api.get(path, headers=headers)
        assert empty.status_code == 200
        assert empty.json() == contractExample("grading/assignmentReportEmpty.json")
        assert api.get(path, params={"rev": 2}, headers=headers).json() == accepted.model_dump(mode="json")


def testPhotoClassTrendsCountDistinctSubmitters(gradingEngine: Engine, contractExample: Callable[[str], Any]) -> None:
    with Session(gradingEngine) as session, session.begin():
        saveEvent(session, incomingEventAdapter.validate_python(contractExample("events/assignmentPublished.json")))
        for index, student in enumerate(("s1", "s1", "s2", "s3", "s4"), 1):
            event = contractExample("events/attemptSubmitted.json")
            event.update(eventId=f"submission-{index}", occurredAt=f"2026-11-20T09:0{index}:00Z")
            event["payload"].update(checkId=f"c{index}", attemptId=f"a{index}", studentId=student)
            saveEvent(session, incomingEventAdapter.validate_python(event))

    root = Issue(issueId="i1", stepId="1", category="sign", comment="Потерян знак.", correction="Проверьте знак.")
    repeat = root.model_copy(update={"issueId": "i2"})
    child = root.model_copy(update={"issueId": "i3", "causedByIssueId": "i1", "category": "arithmetic"})
    wrong = TaskResult(taskId="t1", answer="incorrect", reasoning="invalid", method="optional",
                       issues=[root, repeat, child], needsReview=False)
    unreadable = TaskResult(taskId="t1", answer="unreadable", reasoning="unknown", method="optional",
                            issues=[], needsReview=True)
    store = ResultStore(gradingEngine)
    for check, task in (("c1", wrong), ("c2", wrong), ("c3", wrong), ("c4", unreadable)):
        result = store.saveDraft(check, "initial", (task,), "")
        store.publish(check, result.resultRev, MarkdownRef(bucket="results", key=check, sha256="a" * 64), lambda _: None)

    reports = ReportStore(gradingEngine)
    summary = reports.read("w1", assignmentRev=1)
    assert summary and (summary.submittedCount, summary.studentCount, summary.waitingStudentIds) == (4, 3, ["s4"])
    assert [s.checkId for s in summary.students] == ["c2", "c3", "c4"]
    taskSummary, = summary.tasks
    assert taskSummary.answer == {"incorrect": 2, "correct": 0, "unreadable": 1, "skipped": 0}
    assert (taskSummary.incorrectShare, taskSummary.unchecked, taskSummary.needsReview) == (0.5, 2, 1)
    category, = taskSummary.categories
    assert (category.category, category.affectedStudents, category.affectedShare, category.primaryIssues) == ("sign", 2, 0.5, 4)
    trend, = summary.trends
    assert (trend.taskId, trend.category, trend.topic, trend.exampleComment) == ("t1", "sign", "Знаки в выражениях", root.comment)

    reduced = reports.read("w1", assignmentRev=1, excludedCheckIds=frozenset({"c3", "c5"}))
    assert reduced and reduced.submittedCount == 2 and reduced.waitingStudentIds == [] and reduced.trends == []
    assert reports.read("w1", rev=summary.rev) == summary
