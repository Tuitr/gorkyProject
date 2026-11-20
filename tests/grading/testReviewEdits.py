"""Concurrent teacher PATCH requests and durable idempotency receipts."""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from unittest.mock import Mock

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from contracts.v1.grading.checkresult import MarkdownRef
from contracts.v1.grading.reviewedit import RESULT_REV_HEADER, ReviewEditReceipt
from contracts.v1.grading.taskresult import Issue, TaskResult
from grading.inbox import saveEvent
from grading.intake import incomingEventAdapter
from grading.main import createApp
from grading.markdown import inlineText
from grading.markdownStorage import MarkdownStorage
from grading.results import ResultStore
from grading.settings import Settings
from grading.tables import TeacherReviewEdit


def testConcurrentEditsHaveOneWinnerAndReplayReturnsItsReceipt(
    gradingEngine: Engine, contractExample: Callable[[str], Any],
) -> None:
    with Session(gradingEngine) as session, session.begin():
        saveEvent(session, incomingEventAdapter.validate_python(contractExample("events/attemptSubmitted.json")))
    first = Issue(issueId="i1", stepId="s1", category="sign_error", comment="Исправьте знак", correction="−2")
    consequence = first.model_copy(update={"issueId": "i2", "stepId": "s2", "causedByIssueId": "i1"})
    independent = first.model_copy(update={"issueId": "i3", "stepId": "s3"})
    result = TaskResult(taskId="t1", answer="incorrect", reasoning="invalid", method="optional",
                        issues=[first, consequence, independent], needsReview=True)
    store = ResultStore(gradingEngine)
    original = store.saveDraft("c1", "initial", (result,), "Исходный отзыв.")
    ref = MarkdownRef(bucket="results", key="original.md", sha256="a" * 64)
    store.publish("c1", 1, ref, lambda _: None)

    settings = Settings(serviceTokensJson=json.dumps({
        "teacher-service": {"serviceName": "cabinet", "rights": ["checks.edit"]},
        "reader-service": {"serviceName": "reader", "rights": ["checks.read"]},
    }))
    headers = {"Authorization": "Bearer teacher-service", "X-Trace-Id": "teacher-edit"}
    path = "/internal/v1/checks/c1/review"

    def body(editId: str, actor: str) -> dict[str, Any]:
        return {"baseRev": 1, "editId": editId, "actorId": actor, "changes": [
            {"op": "removeIssue", "taskId": "t1", "issueId": "i1"},
            {"op": "updateIssue", "taskId": "t1", "issueId": "i3", "comment": "Уточните формулу."},
            {"op": "setVerdict", "taskId": "t1", "answer": "correct", "needsReview": False},
            {"op": "setOverallReview", "overallReview": f"Ручной отзыв {actor}."},
        ]}

    requests = (body("edit-1", "teacher-a"), body("edit-2", "teacher-b"))
    storage = Mock(spec=MarkdownStorage)
    storage.save.side_effect = [ref.model_copy(update={"key": f"edited-{rev}.md"}) for rev in (2, 3)]
    with TestClient(createApp(settings, engine=gradingEngine, storage=storage)) as api:
        assert api.patch(path, json=requests[0]).status_code == 401
        assert api.patch(path, headers={"Authorization": "Bearer reader-service"}, json=requests[0]).status_code == 403
        invalid = body("edit-1", "teacher-a")
        invalid["changes"].append({"op": "removeIssue", "taskId": "t1", "issueId": "unknown"})
        assert api.patch(path, headers=headers, json=invalid).status_code == 422
        assert store.findDraft("c1", "teacher:edit-1") is None

        start = Barrier(2, timeout=5)

        def submit(request: dict[str, Any]) -> Response:
            start.wait()
            response: Response = api.patch(path, headers=headers, json=request)
            return response

        with ThreadPoolExecutor(max_workers=2) as editors:
            responses = list(editors.map(submit, requests))
        assert sorted(response.status_code for response in responses) == [200, 409]
        winningIndex = next(index for index, response in enumerate(responses) if response.status_code == 200)
        winner, conflict = responses[winningIndex], responses[1 - winningIndex]
        assert conflict.headers[RESULT_REV_HEADER] == "2"
        assert conflict.json()["code"] == "conflict" and conflict.json()["traceId"] == "teacher-edit"
        applied = requests[winningIndex]
        receipt = ReviewEditReceipt.model_validate(winner.json())
        assert receipt.resultRev == 2 and receipt.editId == applied["editId"]
        replay = api.patch(path, headers=headers, json=applied)
        assert replay.status_code == 200 and replay.json() == winner.json()
        changed = api.patch(path, headers=headers, json={**applied, "actorId": "another-editor"})
        assert changed.status_code == 409 and changed.headers[RESULT_REV_HEADER] == "2"

        checkPath = "/internal/v1/checks/c1"
        readerHeaders = {"Authorization": "Bearer reader-service"}
        winningReview = f"Ручной отзыв {applied['actorId']}."
        reading = api.get(checkPath, headers=readerHeaders)
        assert reading.json()["resultRev"] == 2 and reading.json()["state"] == "ready"
        assert reading.json()["overallReview"] == winningReview
        storage.save.assert_called_once()
        assert inlineText(winningReview) in storage.save.call_args.args[2]

        followup = {"baseRev": 2, "editId": "edit-3", "actorId": applied["actorId"], "changes": [
            {"op": "setVerdict", "taskId": "t1", "reasoning": "valid"},
        ]}
        nextEdit = api.patch(path, headers=headers, json=followup)
        assert nextEdit.status_code == 200 and nextEdit.json()["resultRev"] == 3

        again = api.patch(path, headers=headers, json=applied)
        assert again.status_code == 200 and again.json() == winner.json()
        rejected = api.patch(path, headers=headers, json=requests[1 - winningIndex])
        assert rejected.status_code == 409 and rejected.headers[RESULT_REV_HEADER] == "3"

        latest = api.get(checkPath, headers=readerHeaders).json()
        assert latest["resultRev"] == 3 and latest["overallReview"] == winningReview
        assert latest["taskResults"][0]["reasoning"] == "valid"
        assert api.get(checkPath, headers=readerHeaders, params={"resultRev": 2}).json() == reading.json()
        assert storage.save.call_count == 2
        assert inlineText(winningReview) in storage.save.call_args.args[2]

    saved = store.findDraft("c1", f"teacher:{receipt.editId}")
    assert saved is not None and saved.resultRev == 2
    edited = saved.taskResults[0]
    assert edited.answer == "correct" and edited.reasoning == "invalid" and not edited.needsReview
    assert edited.issues == [independent.model_copy(update={"comment": "Уточните формулу."})]
    assert saved.overallReview == winningReview
    assert store.findDraft("c1", "initial") == original
    with Session(gradingEngine) as session:
        history = session.query(TeacherReviewEdit).all()
        assert {row.resultRev: row.request for row in history} == {2: applied, 3: followup}
