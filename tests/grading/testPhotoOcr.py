"""OCR preserves visible mistakes, crossed-out text, and uncertain fragments."""

import base64
import hashlib
import json
from collections.abc import Callable
from io import BytesIO
from typing import Any

import pytest
from PIL import Image
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from cabinet.uploads import readSubmission
from contracts.v1.grading.checkresult import MarkdownRef, PhotoBox, PublishedCheck
from contracts.v1.grading.taskresult import Issue, TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from contracts.v1.references.reference import ReferenceReady
from fastapi import UploadFile
from grading import photoOcr
from grading.assignment import AssignmentTask
from grading.feedback import composeOverallReview
from grading.inbox import saveEvent
from grading.intake import AttemptSubmittedInput, InvalidEvent, readEvent
from grading.photoOcr import OcrRejected, OcrUnavailable, TranscribedLine, transcribePhoto
from grading.reports import distribution
from grading.resultArtifacts import parsedTasksAdapter
from grading.review import reviewTasks
from grading.scoring import automaticAssessment
from grading.stream import StreamConsumer
from grading.submission import readPhotoPages, readPhotoSubmission, resolveAnchor
from grading.tables import AssignmentJob
from grading.worker import GradingWorker
from references.gateway.prompts import PromptCatalog
from shared.clock import ManualClock
from shared.httperrors import ApiProblem


def samplePhoto() -> bytes:
    with BytesIO() as file:
        image = Image.new("RGB", (50, 80), "white")
        exif = Image.Exif()
        exif[274] = 6
        exif[305] = "EXIF test"
        image.save(file, "JPEG", exif=exif)
        return file.getvalue()


@pytest.mark.parametrize("assignment", [False, True])
@pytest.mark.parametrize("useJsonSchema", [True, False])
def testPhotoIsReadOnceWithoutExif(monkeypatch: pytest.MonkeyPatch, assignment: bool, useJsonSchema: bool) -> None:
    requests: list[dict[str, Any]] = []

    class Response:
        status = 200

        def read(self, amount: int) -> bytes:
            return json.dumps({"status": "completed", "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps({
                    "lines": [{"kind": "line", "text": "$2 + 2 = 5$"}],
                })}],
            }]}).encode()

    class Connection:
        def __init__(self, host: str, timeout: int) -> None:
            assert host == "ocr.example.org:9443"

        def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
            assert (method, path) == ("POST", "/api/read")
            assert headers["Authorization"] == "Bearer test-key"
            requests.append(json.loads(body))

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setenv("OCR_URL", "https://ocr.example.org:9443/api/read")
    monkeypatch.setenv("OCR_API_KEY", "test-key")
    monkeypatch.setenv("OCR_MODEL", "test-model")
    if useJsonSchema:
        monkeypatch.delenv("OCR_JSON_SCHEMA", raising=False)
    else:
        monkeypatch.setenv("OCR_JSON_SCHEMA", "0")
    monkeypatch.setattr(photoOcr, "HTTPSConnection", Connection)
    assert transcribePhoto(samplePhoto(), "image/jpeg", assignment=assignment) == (TranscribedLine("$2 + 2 = 5$", "line"),)
    assert len(requests) == 1
    for request in requests:
        assert request["model"] == "test-model"
        assert request["store"] is False
        if useJsonSchema:
            assert request["text"]["format"]["strict"] is True
        else:
            assert "text" not in request
        assert "previous_response_id" not in request and "$2 + 2 = 5$" not in json.dumps(request)
        imageUrl = request["input"][0]["content"][1]["image_url"]
        assert imageUrl.startswith("data:image/jpeg;base64,")
        with Image.open(BytesIO(base64.b64decode(imageUrl.split(",", 1)[1]))) as image:
            assert image.size == (80, 50) and not image.getexif()
    expected = photoOcr.ASSIGNMENT_INSTRUCTIONS if assignment else photoOcr.TRANSCRIBE
    assert requests[0]["instructions"].startswith(expected + "\n\n")
    assert '{"lines": [...]}' in requests[0]["instructions"]


def testOcrAcceptsBareLineArrayFromProvider() -> None:
    box = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}
    body = {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps([
            {"kind": "heading", "text": "1. $x + 2 = 5$"},
            {"kind": "line", "text": "$x = 3$", "box": box},
        ])}]}],
    }
    heading, solution = photoOcr.parseResponse(json.dumps(body).encode())
    assert (heading.kind, heading.text, heading.box) == ("heading", "1. $x + 2 = 5$", None)
    assert (solution.kind, solution.text, solution.box) == ("line", "$x = 3$", PhotoBox.model_validate(box))


def testOcrKeepsOnlyNumberAndMarkerForCancelledWork() -> None:
    lines = [{"kind": "cancelled_heading", "text": "Задание №1. $2 + 2 = 7$"},
             {"kind": "cancelled", "text": "$x = 7$"}]
    body = {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps({"lines": lines})},
    ]}]}
    assert [(line.kind, line.text) for line in photoOcr.parseResponse(json.dumps(body).encode())] == [
        ("cancelled_heading", "Задание №1"), ("cancelled", photoOcr.CANCELLED),
    ]


@pytest.mark.parametrize("missing", ["OCR_URL", "OCR_API_KEY", "OCR_MODEL"])
def testOcrRequiresConfiguration(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv("OCR_URL", "https://ocr.example.org/api/read")
    monkeypatch.setenv("OCR_API_KEY", "test-key")
    monkeypatch.setenv("OCR_MODEL", "test-model")
    monkeypatch.delenv(missing)
    with pytest.raises(OcrRejected, match="ocr_not_configured"):
        transcribePhoto(samplePhoto(), "image/jpeg")


def testOcrRejectsIncompleteAndInvalidInput(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_API_KEY", "test-key")
    monkeypatch.setenv("OCR_MODEL", "test-model")
    monkeypatch.setenv("OCR_URL", "http://ocr.example.org/api/read")
    with pytest.raises(OcrRejected, match="ocr_invalid_url"):
        transcribePhoto(samplePhoto(), "image/jpeg")
    with pytest.raises(OcrRejected, match="ocr_incomplete"):
        photoOcr.parseResponse(b'{"status":"incomplete","output":[]}')
    with pytest.raises(OcrRejected, match="ocr_invalid_photo"):
        photoOcr.normalizedImage(b"\xff\xd8\xff broken", "image/jpeg")
    with pytest.raises(ApiProblem):
        readSubmission(UploadFile(BytesIO(samplePhoto()), filename="photo.png"))
    photo = readSubmission(UploadFile(BytesIO(samplePhoto()), filename="photo.JPG"))[0]
    assert photo.mediaType == "image/jpeg" and photo.extension == ".jpg" and photo.signatures == ()


def task(number: str) -> AssignmentTask:
    return AssignmentTask(f"t{number}", number, "Условие", ())


def reference() -> ReferenceReady:
    return ReferenceReady(referenceId="ref", state="ready", source="fixture", schemaId="scalar.v1",
                          value={"exact": "4"}, solverVersion=None)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("~~7~~ $x = 4$", "$x = 4$"),
        ("$x = 4$ ~~7~~", "$x = 4$"),
        ("$x = 4$ ~~[зачёркнуто]~~", "$x = 4$"),
        ("~~$x = 7$~~ $x = 4$", "$x = 4$"),
        ("$x = 4$ ~~[неразборчиво]~~", "$x = 4$"),
        ("$x = ~~7~~4$", None),
        (r"$\frac{1}{~~7~~4}$", None),
        ("x = ~~7~~", None),
        ("~~-~~ $7$", None),
        ("$x = 4$ ~~+ 1~~", None),
        ("1 ~~2~~ 3", None),
        ("$x = 4$ ~~7", None),
        ("$x = [неразборчиво]$", None),
    ],
)
def testCrossoutsPreserveTranscriptAndOnlySafeTextReachesGrader(
    source: str, expected: str | None, contractExample: Callable[[str], Any],
) -> None:
    original = samplePhoto()
    payload = contractExample("events/attemptSubmitted.json")["payload"]
    payload["docRef"].update({"key": "uploads/page.jpg", "sha256": hashlib.sha256(original).hexdigest()})
    submitted = AttemptSubmittedInput.model_validate(payload)
    lines = (TranscribedLine("Задание №2", "heading"), TranscribedLine(source, "line"))
    tasks = (task("2"),)
    reading = readPhotoSubmission(submitted, lines, tasks)
    assert reading.transcriptMarkdown == f"Задание №2\n{source}"
    assert resolveAnchor(original, reading.steps[0].sourceAnchor, transcriptMarkdown=reading.transcriptMarkdown) == source
    requests: list[ModelReviewRequest] = []

    def grade(request: ModelReviewRequest) -> ModelReviewResponse:
        requests.append(request)
        return ModelReviewResponse.model_validate(contractExample("gateway/responseAnswerOnly.json"))

    result, = reviewTasks(submitted.checkId, tasks, reading, {"t2": reference()}, grade)
    if expected is None:
        assert not requests and result.needsReview
    else:
        assert len(requests) == 1
        assert [step.markdown for step in requests[0].steps] == [expected]


def testCancelledLineStaysInTranscriptButIsNotGraded(contractExample: Callable[[str], Any]) -> None:
    submitted = AttemptSubmittedInput.model_validate(contractExample("events/attemptSubmitted.json")["payload"])
    lines = (
        TranscribedLine("1)$2 + 2 = 5$", "heading"),
        TranscribedLine("$2 + 2 = 7$", "cancelled"),
        TranscribedLine("Всего событий: [неразборчиво]", "uncertain"),
    )
    reading = readPhotoSubmission(submitted, lines, (task("1"),))
    assert reading.transcriptMarkdown == "1)$2 + 2 = 5$\n~~$2 + 2 = 7$~~\nВсего событий: [неразборчиво]"
    assert [step.markdown for step in reading.steps] == ["$2 + 2 = 5$", "Всего событий: [неразборчиво]"]
    assert [item.status for item in reading.taskReadings] == ["needsReview"]


def testCancelledTaskDoesNotConsumeNeighbouringTasks(contractExample: Callable[[str], Any]) -> None:
    submitted = AttemptSubmittedInput.model_validate(contractExample("events/attemptSubmitted.json")["payload"])
    tasks = (task("1"), task("2"), task("3"))
    reading = readPhotoPages((submitted.docRef, submitted.docRef), (
        (TranscribedLine("№1", "heading"), TranscribedLine(photoOcr.CANCELLED, "cancelled"),
         TranscribedLine("№2", "cancelled_heading"), TranscribedLine("$x = 45$", "line")),
        (TranscribedLine("$x = 56$", "line"), TranscribedLine("№3 $x = 4$", "heading")),
    ), tasks)
    assert [item.status for item in reading.taskReadings] == ["skipped", "skipped", "answerOnly"]
    assert [(step.taskId, step.markdown) for step in reading.steps] == [("t3", "$x = 4$")]
    assert reading.ocrStatus == "readable"
    results = reviewTasks(submitted.checkId, tasks[:2], reading, {"t1": reference(), "t2": reference()},
                          lambda _: pytest.fail("Cancelled work reached the model"))
    assert [(result.answer, result.needsReview) for result in results] == [("skipped", False), ("skipped", False)]
    assert "Решение полностью зачёркнуто." in composeOverallReview(tasks[:2], reading, results, {})


@pytest.mark.parametrize("hasSolution", [True, False])
def testOmittedPhotoTasksAreIncorrectWithoutManualReview(
    contractExample: Callable[[str], Any], hasSolution: bool,
) -> None:
    submitted = AttemptSubmittedInput.model_validate(contractExample("events/attemptSubmitted.json")["payload"])
    tasks = (task("1"), task("2"), task("3"))
    lines = [TranscribedLine("№2", "heading"), TranscribedLine("№3", "heading")]
    if hasSolution:
        lines.append(TranscribedLine("$x = 4$", "line"))
    reading = readPhotoSubmission(submitted, lines, tasks)
    assert [item.status for item in reading.taskReadings] == [
        "omitted", "omitted", "answerOnly" if hasSolution else "omitted",
    ]
    assert reading.ocrStatus == "readable"
    omitted = tasks[:2] if hasSolution else tasks
    results = reviewTasks(submitted.checkId, omitted, reading, {}, lambda _: pytest.fail("Missing work reached the model"))
    for result in results:
        assert result.answer == "incorrect" and not result.needsReview
        assert automaticAssessment(result, 2).score == 0
        report, _ = distribution(result.taskId, [result], studentCount=1, submittedCount=1)
        assert report.answer["incorrect"] == 1 and report.needsReview == 0
    assert "Задание 1. Задание пропущено." in composeOverallReview(omitted, reading, results, {})


def testPhotoCoverFieldsAndTaskListsDoNotHideOmittedSolutions(contractExample: Callable[[str], Any]) -> None:
    submitted = AttemptSubmittedInput.model_validate(contractExample("events/attemptSubmitted.json")["payload"])
    tasks = (task("2"), task("8"), task("10"))
    reading = readPhotoSubmission(submitted, (
        TranscribedLine("Номер: $26$", "heading"),
        TranscribedLine("Вариант: 2.", "heading"),
        TranscribedLine("Задание - 2, 4, 6, 8, 10.", "heading"),
        TranscribedLine("Задание: 8", "heading"),
        TranscribedLine("$x = 4$", "line"),
        TranscribedLine("Задача №10.", "heading"),
        TranscribedLine("$x = 3$", "line"),
    ), tasks)
    assert reading.ocrStatus == "readable" and not reading.unassignedFragments
    assert [(task.taskId, task.status) for task in reading.taskReadings] == [
        ("t2", "omitted"), ("t8", "answerOnly"), ("t10", "answerOnly"),
    ]
    result, = reviewTasks(submitted.checkId, tasks[:1], reading, {},
                          lambda _: pytest.fail("Missing work reached the model"))
    assessment = automaticAssessment(result, 2, omitted=result.taskId in reading.omittedTaskIds)
    assert result.answer == "incorrect" and assessment.score == 0 and assessment.comment == "Задание пропущено."


@pytest.mark.parametrize("unclear", [
    (),
    (TranscribedLine("№[неразборчиво]", "heading"),),
    (TranscribedLine(photoOcr.UNREADABLE, "uncertain"),),
])
def testMissingHeadingCouldBeOnAnUnclearPage(
    contractExample: Callable[[str], Any], unclear: tuple[TranscribedLine, ...],
) -> None:
    submitted = AttemptSubmittedInput.model_validate(contractExample("events/attemptSubmitted.json")["payload"])
    tasks = (task("1"), task("2"))
    readable = (TranscribedLine("№2", "heading"), TranscribedLine("$x = 4$", "line"))
    reading = readPhotoPages((submitted.docRef, submitted.docRef), (readable, unclear), tasks)
    result, = reviewTasks(submitted.checkId, tasks[:1], reading, {}, lambda _: pytest.fail("Unclear work reached the model"))
    assert result.answer == "unreadable" and result.needsReview
    assert automaticAssessment(result, 2).score is None


@pytest.mark.parametrize("cancelledFinalLine", [False, True])
def testPartialCrossoutChecksPrincipleUsingOnlyIntactSteps(
    contractExample: Callable[[str], Any], cancelledFinalLine: bool,
) -> None:
    submitted = AttemptSubmittedInput.model_validate(contractExample("events/attemptSubmitted.json")["payload"])
    tasks = (task("1"),)
    lines = [TranscribedLine("№1", "heading"), TranscribedLine("$4x = 1$", "line")]
    intact = r"$4\cdot\frac{1}{4} = 1$"
    if cancelledFinalLine:
        crossed = photoOcr.CANCELLED
        lines.extend([TranscribedLine(intact, "line"), TranscribedLine(crossed, "cancelled")])
    else:
        crossed = r"$x = \frac{1}{~~7~~4}$"
        lines.extend([TranscribedLine(crossed, "line"), TranscribedLine(intact, "line")])
    reading = readPhotoSubmission(submitted, lines, tasks)
    assert reading.taskReadings[0].status == "partialCrossout"
    assert reading.ocrStatus == "needs_review" and crossed in (reading.transcriptMarkdown or "")

    def grade(request: ModelReviewRequest) -> ModelReviewResponse:
        assert "Оцени принцип решения" in PromptCatalog().instructions(request.promptVersion)
        assert [(step.stepId, step.markdown) for step in request.steps] == [
            ("s1", "$4x = 1$"), ("s2" if cancelledFinalLine else "s3", intact),
        ]
        return ModelReviewResponse(
            schemaVersion="taskResult.v1", modelId="fixture",
            taskResult=TaskResult(taskId="t1", answer="correct", reasoning="valid", method="optional",
                                  issues=[], needsReview=False),
        )

    references = {"t1": reference()}
    result, = reviewTasks(submitted.checkId, tasks, reading, references, grade)
    assert result.answer == "unreadable" and result.reasoning == "valid" and result.needsReview
    assert automaticAssessment(result, 1).score is None
    assert "В читаемых шагах применён верный принцип" in composeOverallReview(tasks, reading, (result,), references)


def testUnreadableHeadingDoesNotMoveStepsToAnotherTask(contractExample: Callable[[str], Any]) -> None:
    submitted = AttemptSubmittedInput.model_validate(contractExample("events/attemptSubmitted.json")["payload"])
    reading = readPhotoSubmission(
        submitted,
        (TranscribedLine("№1", "heading"), TranscribedLine("$x = 4$", "line"),
         TranscribedLine("Задание №[неразборчиво]", "heading"), TranscribedLine("$x = 8$", "line"),
         TranscribedLine("№3 $y = 3$", "heading")),
        (task("1"), task("2"), task("3")),
    )
    assert all(step.markdown != "$x = 8$" for step in reading.steps)
    assert [item.status for item in reading.taskReadings] == ["answerOnly", "needsReview", "answerOnly"]


def testPhotoCheckResumesFromPersistedTranscript(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any],
) -> None:
    original = samplePhoto()
    raw = contractExample("events/attemptSubmitted.json")
    raw["payload"]["docRef"].update({"key": "uploads/page.jpg", "sha256": hashlib.sha256(original).hexdigest()})
    submitted = AttemptSubmittedInput.model_validate(raw["payload"])
    assignment = task("1")
    with Session(gradingEngine) as db, db.begin():
        db.add(AssignmentJob(
            assignmentId=submitted.assignmentId, assignmentRev=submitted.assignmentRev, traceId="photo-test",
            state="ready", completedStage="references_requested", payload={},
            stageData={"tasks": parsedTasksAdapter.dump_python((assignment,), mode="json"),
                       "references": {"t1": reference().model_dump(mode="json")}},
        ))
        saveEvent(db, readEvent(json.dumps(raw)))
    reads = []

    def fakeOcr(image: bytes, mediaType: str) -> tuple[TranscribedLine, ...]:
        assert image == original and mediaType == "image/jpeg"
        reads.append(mediaType)
        return (TranscribedLine("1. $2 + 2 = 5$", "heading"),)

    redis, streamName = gradingStream
    worker = GradingWorker(StreamConsumer(redis, "photo-test", streamName=streamName), gradingEngine,
                           clock=ManualClock().now, ocr=fakeOcr)
    lease = worker.jobs.claimCheck()
    assert lease is not None
    first = worker.prepareCheck(lease, lambda _: original)
    assert first is not None and first.reading.transcriptMarkdown == "1. $2 + 2 = 5$"
    pending = worker.results.read(submitted.checkId, None, lambda _: None)
    assert pending is not None and pending.transcriptMarkdown == first.reading.transcriptMarkdown
    assert worker.jobs.reschedule(lease, "queued", worker.now())
    retry = worker.jobs.claimCheck()
    assert retry is not None
    second = worker.prepareCheck(retry, lambda _: pytest.fail("OCR must resume from the checkpoint"))
    assert second == first and reads == ["image/jpeg"]

@pytest.mark.parametrize(("text", "expected"), [
    ("[неразборчиво]", "unreadable"), ("x = [неразборчиво]", "needs_review"),
])
def testOcrDistinguishesUnclearFragmentFromUnreadableWork(
    text: str, expected: str, contractExample: Callable[[str], Any],
) -> None:
    submitted = AttemptSubmittedInput.model_validate(contractExample("events/attemptSubmitted.json")["payload"])
    reading = readPhotoSubmission(submitted, (TranscribedLine("№1", "heading"), TranscribedLine(text, "uncertain")),
                                  (task("1"),))
    assert reading.ocrStatus == expected and reading.ocrReason
    result, = reviewTasks(submitted.checkId, (task("1"),), reading, {}, lambda _: pytest.fail("unexpected model call"))
    assert result.answer == "unreadable" and result.needsReview


@pytest.mark.parametrize("terminalFailure", [False, True])
def testSecondPageResumesAndMarksUseRevisionAndPageLocalLine(
    terminalFailure: bool, gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any],
) -> None:
    event = contractExample("events/attemptSubmitted.json")
    payload = event["payload"]
    first = dict(payload["docRef"], key="uploads/01.jpg")
    second = dict(first, key="uploads/02.png")
    payload.update(docRef=first, pageRefs=[second, first])
    with pytest.raises(InvalidEvent):
        readEvent(json.dumps(event))
    payload["pageRefs"] = [first, second]
    submitted = AttemptSubmittedInput.model_validate(payload)
    with Session(gradingEngine) as db, db.begin():
        db.add(AssignmentJob(
            assignmentId=submitted.assignmentId, assignmentRev=submitted.assignmentRev, traceId="two-pages",
            state="ready", completedStage="references_requested", payload={},
            stageData={"tasks": parsedTasksAdapter.dump_python((task("1"),), mode="json"),
                       "references": {"t1": reference().model_dump(mode="json")}},
        ))
        saveEvent(db, readEvent(json.dumps(event)))

    calls: list[str] = []
    box = PhotoBox(x=0.1, y=0.2, w=0.3, h=0.1)

    def ocr(document: bytes, media: str) -> tuple[TranscribedLine, ...]:
        calls.append(media)
        if media == "image/jpeg":
            return TranscribedLine("№1", "heading"), TranscribedLine("$x+2=5$", "line")
        if len(calls) == 2:
            raise OcrUnavailable("temporarily unavailable")
        return TranscribedLine("$x=5+2$", "line", box), TranscribedLine("$x=7$", "line")

    redis, stream = gradingStream
    worker = GradingWorker(StreamConsumer(redis, "photo", streamName=stream), gradingEngine,
                           clock=ManualClock().now, ocr=ocr)
    lease = worker.jobs.claimCheck()
    assert lease is not None
    with pytest.raises(OcrUnavailable):
        worker.prepareCheck(lease, lambda ref: ref.key.encode())
    pending = worker.results.read(submitted.checkId, None, lambda _: None)
    assert pending and len(pending.photoPages) == 1 and pending.ocrStatus is None
    if terminalFailure:
        assert worker.jobs.finish(lease, "failed", "ocr_unavailable", {"attempts": 5})
        failed = worker.results.read(submitted.checkId, None, lambda _: None)
        assert failed and failed.state == "failed" and failed.reason == "ocr_unavailable"
        assert failed.ocrStatus is None and failed.photoPages == pending.photoPages
        return
    assert worker.jobs.reschedule(lease, "queued", worker.now())
    retry = worker.jobs.claimCheck()
    assert retry is not None
    prepared = worker.prepareCheck(retry, lambda ref: ref.key.encode())
    assert prepared and calls == ["image/jpeg", "image/png", "image/png"]
    assert [(step.stepId, step.sourceAnchor.pageIndex, step.sourceAnchor.paragraphIndex)
            for step in prepared.reading.steps] == [("s1", 0, 1), ("s2", 1, 0), ("s3", 1, 1)]
    issue = Issue(issueId="i1", stepId="s2", category="sign", comment="Знак", correction="x=5−2")
    result = TaskResult(taskId="t1", answer="incorrect", reasoning="invalid", method="optional",
                        needsReview=False, issues=[issue, issue.model_copy(update={"issueId": "i2", "stepId": "s3"})])
    ref = MarkdownRef(bucket="results", key="feedback", sha256="a"*64)
    for rev, issues in [(1, result.issues), (2, result.issues[:1])]:
        worker.results.saveDraft(submitted.checkId, str(rev), (result.model_copy(update={"issues": issues}),), "Отзыв")
        worker.results.publish(submitted.checkId, rev, ref, lambda _: None)
    for rev, expectedCount in [(1, 2), (2, 1)]:
        shown = worker.results.read(submitted.checkId, rev, lambda _: None)
        assert isinstance(shown, PublishedCheck) and shown.ocrStatus == "readable"
        assert shown.photoPages[1].transcriptMarkdown == "$x=5+2$\n$x=7$"
        assert len(shown.photoMarks) == expectedCount
        mark = shown.photoMarks[0]
        assert (mark.taskId, mark.issueId, mark.pageIndex, mark.lineIndex, mark.box) == ("t1", "i1", 1, 0, box)
        if rev == 1:
            assert shown.photoMarks[1].box is None and shown.photoMarks[1].lineIndex == 1

@pytest.mark.parametrize("geometry", [
    {"x": 0.9, "y": 0.4, "w": 0.3, "h": 0.1}, {"x": 0}, None,
])
def testDubiousGeometryKeepsTheLine(geometry: dict[str, float] | None) -> None:
    content = json.dumps({"lines": [{"kind": "line", "text": "x=3", "box": geometry}]})
    body = json.dumps({"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": content}]},
    ]}).encode()
    line, = photoOcr.parseResponse(body)
    assert line.text == "x=3" and line.box is None
