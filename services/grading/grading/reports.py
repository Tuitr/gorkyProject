"""Select one completed attempt per student and pin the resulting class snapshot."""

from collections import Counter, defaultdict
from typing import get_args

from pydantic import TypeAdapter
from sqlalchemy import Engine, func, select, true
from sqlalchemy.orm import Session

from contracts.v1.grading.assignmentreport import AssignmentReport, CategoryTrend, ReportStudent, ReportTrend, TaskDistribution
from contracts.v1.grading.checkresult import CheckOutcome
from contracts.v1.grading.taskresult import AnswerVerdict, MethodVerdict, ReasoningVerdict, TaskResult
from grading.assignment import AssignmentTask
from grading.intake import AttemptSubmittedInput
from grading.scoring import TOPICS
from grading.tables import AssignmentJob, AssignmentReportSnapshot, CheckJob, CheckResultRevision, InboxEntry

assignmentTasks = TypeAdapter(list[AssignmentTask])


class ReportStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def read(self, assignmentId: str, rev: int | None = None, *, assignmentRev: int | None = None,
             excludedCheckIds: frozenset[str] = frozenset()) -> AssignmentReport | None:
        """With rev, return that snapshot. Otherwise compute the report of assignmentRev (default: the latest
        revision) without the excluded checks, the attempts the cabinet deleted, and pin it as a snapshot."""
        with Session(self.engine) as session, session.begin():
            if rev is not None:
                snapshot = session.get(AssignmentReportSnapshot, (assignmentId, rev))
                return AssignmentReport.model_validate(snapshot.report) if snapshot is not None else None
            # Only one request can allocate a new snapshot number for this assignment at a time.
            session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("grading.report:" + assignmentId, 0))))
            revisions = select(AssignmentJob).filter_by(assignmentId=assignmentId)
            if assignmentRev is not None:
                revisions = revisions.filter_by(assignmentRev=assignmentRev)
            assignment = session.scalar(revisions.order_by(AssignmentJob.assignmentRev.desc()).limit(1))
            if assignment is None:
                return None
            latest = session.scalar(
                select(AssignmentReportSnapshot).filter_by(assignmentId=assignmentId)
                .order_by(AssignmentReportSnapshot.rev.desc()).limit(1)
            )
            report = currentReport(session, assignment, latest.rev if latest is not None else 1, excludedCheckIds)
            if latest is not None:
                if report == AssignmentReport.model_validate(latest.report):
                    return report
                report = report.model_copy(update={"rev": latest.rev + 1})
            session.add(AssignmentReportSnapshot(
                assignmentId=assignmentId, assignmentRev=assignment.assignmentRev, rev=report.rev,
                report=report.model_dump(mode="json"),
            ))
            return report


def currentReport(session: Session, assignment: AssignmentJob, rev: int,
                  excludedCheckIds: frozenset[str] = frozenset()) -> AssignmentReport:
    # Select a whole revision so its acceptance time and its verdicts always agree.
    ready = (
        select(CheckResultRevision.outcome, CheckResultRevision.acceptedAt)
        .where(CheckResultRevision.checkId == CheckJob.checkId, CheckResultRevision.markdownRef.is_not(None))
        .order_by(CheckResultRevision.acceptedAt.desc().nulls_last(), CheckResultRevision.resultRev.desc())
        .limit(1).correlate(CheckJob).lateral()
    )
    submittedAt = select(func.min(InboxEntry.occurredAt)).where(InboxEntry.checkId == CheckJob.checkId).scalar_subquery()
    candidates = (
        select(CheckJob.payload, ready.c.outcome, ready.c.acceptedAt)
        .select_from(CheckJob).outerjoin(ready, true())
        .where(CheckJob.assignmentId == assignment.assignmentId, CheckJob.assignmentRev == assignment.assignmentRev,
               CheckJob.checkId.not_in(excludedCheckIds))
        .order_by(ready.c.acceptedAt.desc().nulls_last(), submittedAt.desc().nulls_last(), CheckJob.checkId.desc())
    )
    submitted: set[str] = set()
    students: dict[str, ReportStudent] = {}
    byTask: dict[str, list[TaskResult]] = defaultdict(list)
    for payload, outcomeData, acceptedAt in session.execute(candidates):
        attempt = AttemptSubmittedInput.model_validate(payload)
        submitted.add(attempt.studentId)
        if outcomeData is None or attempt.studentId in students:
            continue
        outcome = CheckOutcome.model_validate(outcomeData)
        students[attempt.studentId] = ReportStudent(
            studentId=attempt.studentId, attemptId=attempt.attemptId, checkId=outcome.checkId,
            resultRev=outcome.resultRev, grade=outcome.grade, selection="accepted" if acceptedAt is not None else "draft",
        )
        for result in outcome.taskResults:
            byTask[result.taskId].append(result)

    taskIds = dict.fromkeys(task.taskId for task in assignmentTasks.validate_python((assignment.stageData or {}).get("tasks", [])))
    taskIds.update(dict.fromkeys(sorted(byTask)))
    tasks = []
    trends = []
    for taskId in taskIds:
        task, repeated = distribution(taskId, byTask[taskId], len(students), len(submitted))
        tasks.append(task)
        trends.extend(repeated)
    return AssignmentReport(
        assignmentId=assignment.assignmentId, assignmentRev=assignment.assignmentRev, rev=rev, studentCount=len(students),
        submittedCount=len(submitted), waitingStudentIds=sorted(submitted - students.keys()), trends=trends,
        students=[students[studentId] for studentId in sorted(students)],
        tasks=tasks,
    )


def distribution(taskId: str, results: list[TaskResult], studentCount: int,
                 submittedCount: int) -> tuple[TaskDistribution, list[ReportTrend]]:
    answers = Counter(result.answer for result in results)
    reasoning = Counter(result.reasoning for result in results)
    methods = Counter(result.method for result in results)
    affected: Counter[str] = Counter()
    primary: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for result in results:
        categories = set()
        for issue in result.issues:
            if issue.causedByIssueId is None:
                categories.add(issue.category)
                primary[issue.category] += 1
                examples[issue.category] = min(examples.get(issue.category, issue.comment), issue.comment)
        affected.update(categories)
    trends = [
        ReportTrend(taskId=taskId, category=category, topic=TOPICS.get(category, "Задание " + taskId.removeprefix("t")),
                    exampleComment=examples[category])
        for category in sorted(affected) if affected[category] >= 2
    ]
    return TaskDistribution(
        taskId=taskId,
        answer={choice: answers[choice] for choice in get_args(AnswerVerdict)},
        reasoning={choice: reasoning[choice] for choice in get_args(ReasoningVerdict)},
        method={choice: methods[choice] for choice in get_args(MethodVerdict)},
        missing=studentCount - len(results),
        needsReview=sum(result.needsReview for result in results),
        unchecked=submittedCount - answers["correct"] - answers["incorrect"] - answers["skipped"],
        correctShare=answers["correct"] / submittedCount if submittedCount else None,
        incorrectShare=answers["incorrect"] / submittedCount if submittedCount else None,
        categories=[CategoryTrend(category=category, affectedStudents=affected[category], primaryIssues=primary[category],
                                  affectedShare=affected[category] / submittedCount)
                    for category in sorted(primary)],
    ), trends
