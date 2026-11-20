"""Round-trip solution anchors through Word, the grading worker and PostgreSQL."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest
from docx import Document
from redis import Redis
from sqlalchemy import Engine

from contracts.v1.events.checks import DATA_FIELD
from dataset.generate import generateDataset
from grading.assignment import AssignmentTask
from grading.intake import AttemptSubmittedInput, IncomingDocRef
from grading.stream import StreamConsumer
from grading.submission import CellAddress, readSubmission, resolveAnchor
from grading.word import WordReadError
from grading.worker import GradingWorker


def submittedInput(document: bytes) -> AttemptSubmittedInput:
    return AttemptSubmittedInput(
        assignmentId="w1", assignmentRev=1, studentId="s1", attemptId="a1", checkId="c1",
        docRef=IncomingDocRef(bucket="sources", key="works/a1.docx", sha256=hashlib.sha256(document).hexdigest()),
    )


def testIdenticalLinesResolveToTheirOwnParagraphOrCell() -> None:
    word = Document()
    word.add_paragraph("Ученик: Примеров")
    word.add_paragraph("Задание 1. Вычислите сумму.")
    paragraph = word.add_paragraph("  x")
    paragraph.add_run(" = 5  ")
    paragraph.add_run().add_break()
    paragraph.add_run("Ответ: 5")
    table = word.add_table(rows=2, cols=2)
    cell = table.cell(1, 1)
    cell.text = "  x = 5  "
    nested = cell.add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "  x = 5  "
    word.add_paragraph("Задание 2. Ответ: 9")
    buf = BytesIO()
    word.save(buf)
    document = buf.getvalue()
    submitted = submittedInput(document)
    tasks = (AssignmentTask("t1", "1", "Вычислите сумму.", ()), AssignmentTask("t2", "2", "Ответьте.", ()))

    steps = readSubmission(submitted, document, tasks)
    assert steps == readSubmission(submitted, document, tasks)
    assert [(s.taskId, s.stepId) for s in steps] == [("t1", "s1"), ("t1", "s2"), ("t1", "s3"), ("t1", "s4"), ("t2", "s1")]
    assert [s.markdown for s in steps] == ["x = 5", "Ответ: 5", "x = 5", "x = 5", "Ответ: 9"]
    assert all(s.readStatus == "read" and s.sourceAnchor.docRef == submitted.docRef for s in steps)
    anchors = [s.sourceAnchor for s in steps]
    assert anchors[0].cells == ()
    assert anchors[0].paragraphIndex == anchors[1].paragraphIndex == 2
    assert anchors[0].end < anchors[1].start
    assert anchors[2].cells == (CellAddress(0, 1, 1),)
    assert anchors[3].cells == (CellAddress(0, 1, 1), CellAddress(0, 0, 0))
    assert [resolveAnchor(document, a) for a in anchors] == ["  x = 5  ", "Ответ: 5", "  x = 5  ", "  x = 5  ", "Ответ: 9"]
    with pytest.raises(WordReadError, match="checksum"):
        resolveAnchor(document + b"changed", anchors[0])
    with pytest.raises(WordReadError, match="outside the document"):
        resolveAnchor(document, replace(anchors[2], paragraphIndex=-1))
    with pytest.raises(WordReadError, match="outside the paragraph"):
        resolveAnchor(document, replace(anchors[0], end=100))


def testWorkerReadsGeneratedWorkAndRestoresItsAnchors(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any],
) -> None:
    dataset = generateDataset()
    case = next(case for case in dataset.manifest.cases if case.split == "demo")
    assignmentDoc = dataset.files[case.assignmentRef.file]
    submissionDoc = dataset.files[case.submissionRef.file]
    client, streamName = gradingStream
    worker = GradingWorker(StreamConsumer(client, "reader", streamName=streamName), gradingEngine)
    published = contractExample("events/assignmentPublished.json")
    submitted = contractExample("events/attemptSubmitted.json")
    for event, file, doc in (
        (published, case.assignmentRef.file, assignmentDoc), (submitted, case.submissionRef.file, submissionDoc),
    ):
        event["payload"]["docRef"].update(key=file, sha256=hashlib.sha256(doc).hexdigest())
        client.xadd(streamName, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    assignmentLease = worker.jobs.claimAssignment()
    checkLease = worker.jobs.claimCheck()
    assert assignmentLease is not None and checkLease is not None

    def loadDocument(ref: IncomingDocRef) -> bytes:
        return dataset.files[ref.key]

    worker.parseAssignment(assignmentLease, loadDocument)
    steps = worker.parseSubmission(checkLease, loadDocument)
    assert steps
    for item in dataset.manifest.cases:
        if item.submissionRef.file == case.submissionRef.file:
            taskSteps = [step for step in steps if step.taskId == f"t{item.assignmentRef.taskNumber}"]
            assert [step.stepId for step in taskSteps] == [expected.stepId for expected in item.expectedSteps]
    originalLines = [
        p.text for p in Document(BytesIO(submissionDoc)).paragraphs[2:] if not p.text.startswith("Задание ")
    ]
    assert [resolveAnchor(submissionDoc, step.sourceAnchor) for step in steps] == originalLines

    assert worker.jobs.reschedule(checkLease, "queued", datetime(2000, 1, 1, tzinfo=UTC))
    resumed = worker.jobs.claimCheck()
    assert resumed is not None

    def unexpectedLoad(ref: IncomingDocRef) -> bytes:
        raise AssertionError("expected saved submission steps")

    assert worker.parseSubmission(resumed, unexpectedLoad) == steps
