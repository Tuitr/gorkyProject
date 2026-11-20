"""OCR readings may agree on a wrong calculation, or disagree on a single symbol."""

import base64
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest
from PIL import Image
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from cabinet.uploads import readSubmission
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from contracts.v1.references.reference import ReferenceReady
from fastapi import UploadFile
from grading import photoOcr
from grading.assignment import AssignmentTask
from grading.inbox import saveEvent
from grading.intake import AttemptSubmittedInput, readEvent
from grading.photoOcr import OcrLine, OcrRejected, TranscribedLine, agree, transcribePhoto
from grading.resultArtifacts import parsedTasksAdapter
from grading.review import reviewTasks
from grading.stream import StreamConsumer
from grading.submission import readPhotoSubmission, resolveAnchor
from grading.tables import AssignmentJob
from grading.worker import GradingWorker
from shared.httperrors import ApiProblem


def samplePhoto() -> bytes:
    with BytesIO() as file:
        image = Image.new("RGB", (50, 80), "white")
        exif = Image.Exif()
        exif[274] = 6
        exif[305] = "EXIF test"
        image.save(file, "JPEG", exif=exif)
        return file.getvalue()


def testTwoIndependentReadsOfImageWithoutExif(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(photoOcr, "HTTPSConnection", Connection)
    assert transcribePhoto(samplePhoto(), "image/jpeg") == (TranscribedLine("$2 + 2 = 5$", "line"),)
    assert len(requests) == 2
    for request in requests:
        assert request["model"] == "test-model"
        assert request["store"] is False
        assert request["text"]["format"]["strict"] is True
        assert "previous_response_id" not in request and "$2 + 2 = 5$" not in json.dumps(request)
        imageUrl = request["input"][0]["content"][1]["image_url"]
        assert imageUrl.startswith("data:image/jpeg;base64,")
        with Image.open(BytesIO(base64.b64decode(imageUrl.split(",", 1)[1]))) as image:
            assert image.size == (80, 50) and not image.getexif()
    assert requests[0]["input"] != requests[1]["input"]


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


def testOnlyAgreedReadingsGoToGrader(contractExample: Callable[[str], Any]) -> None:
    original = samplePhoto()
    payload = contractExample("events/attemptSubmitted.json")["payload"]
    payload["docRef"].update({"key": "uploads/page.jpg", "sha256": hashlib.sha256(original).hexdigest()})
    submitted = AttemptSubmittedInput.model_validate(payload)
    agreed = [
        OcrLine(kind="heading", text="1)$2 + 2 = 5$"),
        OcrLine(kind="cancelled", text="$2 + 2 = 7$"),
        OcrLine(kind="heading", text="Задание №2"),
    ]
    lines = agree(
        [*agreed, OcrLine(kind="line", text="$x = -7$")],
        [*agreed, OcrLine(kind="line", text="$x = 7$")],
    )
    tasks = (task("1"), task("2"))
    reading = readPhotoSubmission(submitted, lines, tasks)
    assert reading.transcriptMarkdown == "1)$2 + 2 = 5$\n~~$2 + 2 = 7$~~\nЗадание №2\n[неразборчиво]"
    assert resolveAnchor(original, reading.steps[0].sourceAnchor,
                         transcriptMarkdown=reading.transcriptMarkdown) == "$2 + 2 = 5$"
    requests = []

    def grade(request: ModelReviewRequest) -> ModelReviewResponse:
        requests.append(request)
        return ModelReviewResponse.model_validate(contractExample("gateway/responseAnswerOnly.json"))

    results = reviewTasks(submitted.checkId, tasks, reading, {t.taskId: reference() for t in tasks}, grade)
    assert [(r.taskId, [s.markdown for s in r.steps]) for r in requests] == [("t1", ["$2 + 2 = 5$"])]
    assert results[1].answer == "unreadable" and results[1].needsReview and not results[1].issues


def testMissingHeadingDoesNotMoveStepsToAnotherTask(contractExample: Callable[[str], Any]) -> None:
    payload = contractExample("events/attemptSubmitted.json")["payload"]
    submitted = AttemptSubmittedInput.model_validate(payload)
    start = [OcrLine(kind="heading", text="№1"), OcrLine(kind="line", text="$x = 4$")]
    end = [OcrLine(kind="line", text="$x = 8$"), OcrLine(kind="heading", text="№3 $y = 3$")]
    reading = readPhotoSubmission(
        submitted, agree([*start, OcrLine(kind="heading", text="№2"), *end], [*start, *end]),
        (task("1"), task("2"), task("3")),
    )
    assert all(step.markdown != "$x = 8$" for step in reading.steps)
    assert [item.status for item in reading.taskReadings] == ["needsReview", "needsReview", "answerOnly"]


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
    worker = GradingWorker(StreamConsumer(redis, "photo-test", streamName=streamName), gradingEngine, ocr=fakeOcr)
    lease = worker.jobs.claimCheck()
    assert lease is not None
    first = worker.prepareCheck(lease, lambda _: original)
    assert first is not None and first.reading.transcriptMarkdown == "1. $2 + 2 = 5$"
    pending = worker.results.read(submitted.checkId, None, lambda _: None)
    assert pending is not None and pending.transcriptMarkdown == first.reading.transcriptMarkdown
    assert worker.jobs.reschedule(lease, "queued", datetime.now(UTC))
    retry = worker.jobs.claimCheck()
    assert retry is not None
    second = worker.prepareCheck(retry, lambda _: pytest.fail("OCR must resume from the checkpoint"))
    assert second == first and reads == ["image/jpeg"]
