"""B4 acceptance against the local gateway and its recorded model transport."""

import json
import socket
from dataclasses import replace
from threading import Thread

import pytest
import uvicorn
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1.references.modelreview import ReviewStep
from contracts.v1.references.reference import ReferenceReady
from grading.assignment import AssignmentTask
from grading.intake import IncomingDocRef
from grading.review import buildReviewRequest, reviewTasks
from grading.submission import SolutionStep, SourceAnchor, SubmissionReading, TaskReading
from references.gateway.main import createApp
from references.gateway.settings import GatewaySettings
from references.gateway.transports.fixture import FixtureTransport, ReviewFixture


REF = ReferenceReady(referenceId="r1", state="ready", source="fixture", schemaId="scalar.v1",
                     value={"exact": "10"}, solverVersion=None)
ANCHOR = SourceAnchor(IncomingDocRef(bucket="sources", key="STUDENT_MARKER.docx", sha256="a" * 64), (), 0, 0, 1)
TASK = AssignmentTask("t1", "1", "Вычислите 2 × 5.", ())


def testIndependentVerdictsAcrossGateway(sessionFactory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = (TASK, replace(TASK, taskId="t2", requirements=("Показать сложение.",)),
             replace(TASK, taskId="t3", requirements=("Показать сложение.",)))
    lines = [SolutionStep(task.taskId, "s1", "5 × 2 = 10" if task.taskId != "t3" else "Ответ: 10", ANCHOR, "read")
             for task in tasks]
    reading = SubmissionReading(tuple(lines), (TaskReading("t1", "hasSteps"), TaskReading("t2", "hasSteps"),
                                               TaskReading("t3", "answerOnly")), ())
    expected = [("correct", "valid", "optional"), ("correct", "valid", "unmet"), ("correct", "not_shown", "unconfirmed")]
    fixtures = [
        ReviewFixture(fixtureId=task.taskId, taskId=task.taskId, statement=task.statement,
                      steps=(ReviewStep(stepId="s1", markdown=line.markdown),),
                      content={"answer": answer, "reasoning": reasoning, "method": method, "issues": [], "needsReview": False})
        for task, line, (answer, reasoning, method) in zip(tasks, lines, expected, strict=True)
    ]
    transport = FixtureTransport(fixtures)
    tokens = json.dumps({"test": {"serviceName": "grading", "rights": ["model.review"]}})
    app = createApp(GatewaySettings(transport="fixture", serviceTokensJson=tokens), sessionFactory=sessionFactory, transport=transport)
    server = uvicorn.Server(uvicorn.Config(app, ws="none", log_config=None, access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        monkeypatch.setenv("GRADING_GATEWAY_URL", f"http://127.0.0.1:{listener.getsockname()[1]}")
        monkeypatch.setenv("GRADING_GATEWAY_TOKEN", "test")
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            refs = {task.taskId: REF for task in tasks}
            results = reviewTasks("check-STUDENT_MARKER", tasks, reading, refs)
            assert [(r.answer, r.reasoning, r.method) for r in results] == expected
            assert reviewTasks("check-STUDENT_MARKER", tasks, reading, refs) == results
            assert len(transport.calls) == 3
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


STEPS = [ReviewStep(stepId=f"s{i}", markdown=line) for i, line in enumerate(
    ("(−3; 4) = (3; 4)", "Длина = 3 + 4", "Ответ: 7"), 1)]


def testVersionedBodyContainsOnlyContractFields() -> None:
    request = buildReviewRequest("STUDENT_MARKER", TASK, STEPS, REF)
    assert set(request.model_dump()) == {"requestId", "taskId", "promptVersion", "rubric", "steps", "reference", "statement"}
    assert request.steps == STEPS and "STUDENT_MARKER" not in request.model_dump_json()
    assert request == buildReviewRequest("STUDENT_MARKER", TASK, STEPS, REF)
    assert request.requestId != buildReviewRequest("STUDENT_MARKER", TASK, STEPS, REF, "review.v3").requestId
    assert request.requestId != buildReviewRequest("STUDENT_MARKER", TASK, STEPS[:-1], REF).requestId


def testEmbeddedSignatureGoesToTeacherBeforeTransport() -> None:
    line = SolutionStep("t1", "s1", "Ответ: 10. Выполнил: STUDENT_MARKER", ANCHOR, "read")
    reading = SubmissionReading((line,), (TaskReading("t1", "answerOnly"),), ())
    result, = reviewTasks("check", (TASK,), reading, {"t1": REF}, lambda _: pytest.fail("unexpected model call"))
    assert result.needsReview and not result.issues
