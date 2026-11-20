"""B2.1 acceptance tests for published variants and task-scoped requirements."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest
from docx import Document
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from contracts.v1.events.checks import DATA_FIELD
from dataset.docxwriter import Paragraph, buildDocx
from dataset.generate import generateDataset
from grading.assignment import AssignmentReadError, AssignmentTask, readAssignment
from grading.intake import AssignmentPublishedInput, IncomingDocRef, IncomingRequirement
from grading.stream import StreamConsumer
from grading.tables import AssignmentJob
from grading.worker import GradingWorker


def publishedInput(document: bytes, *requirements: IncomingRequirement) -> AssignmentPublishedInput:
    return AssignmentPublishedInput(
        assignmentId="w1", assignmentRev=2,
        docRef=IncomingDocRef(bucket="sources", key="assignments/w1/2.docx", sha256=hashlib.sha256(document).hexdigest()),
        requirements=list(requirements),
    )


def testTwoTasksKeepStatementsAndPrivateRequirementsAcrossRepeatedReads() -> None:
    word = Document()
    word.add_paragraph("Вариант 7")
    header = word.add_paragraph()
    for fragment in ("1", ". Най", "дите длину."):
        header.add_run(fragment)
    word.add_paragraph("2.5 м — длина стороны.")
    word.add_paragraph("Задание 2.")
    word.add_paragraph("Вычислите сумму.")
    output = BytesIO()
    word.save(output)
    document = output.getvalue()
    published = publishedInput(document, IncomingRequirement(taskNumber="2", text="Покажите ход решения"))
    expected = (
        AssignmentTask("t1", "1", "Найдите длину.\n2.5 м — длина стороны.", ()),
        AssignmentTask("t2", "2", "Вычислите сумму.", ("Покажите ход решения",)),
    )
    assert readAssignment(published, document) == expected
    assert readAssignment(published, document) == expected


def testGeneratedVariantMatchesManifestAndAppliesSharedRequirement() -> None:
    dataset = generateDataset()
    file = "assignments/demo1.docx"
    document = dataset.files[file]
    actual = readAssignment(publishedInput(document, IncomingRequirement(text="Запишите ответ")), document)
    expected = {
        case.assignmentRef.taskNumber: case.assignmentRef.statement
        for case in dataset.manifest.cases if case.assignmentRef.file == file
    }
    assert len(actual) == len(expected) == 3
    assert {task.taskNumber: task.statement for task in actual} == expected
    assert all(task.requirements == ("Запишите ответ",) for task in actual)


def testReplaceableReaderKeepsPrintedNumbers() -> None:
    class RecognizedText:
        def paragraphs(self, document: bytes) -> list[str]:
            return ["7) Первое условие", "12) Второе условие"]

    source = b"source bytes"
    published = publishedInput(
        source, IncomingRequirement(text="Общее"), IncomingRequirement(text="Частное", taskNumber="12")
    )
    tasks = readAssignment(published, source, reader=RecognizedText())
    assert [(task.taskId, task.requirements) for task in tasks] == [("t7", ("Общее",)), ("t12", ("Общее", "Частное"))]


@pytest.mark.parametrize(
    ("paragraphs", "error"),
    [
        (["1. Условие", "1. Другое условие"], "duplicate"),
        (["1.", "2. Условие"], "empty"),
        (["Заголовок"], "missing numbered"),
        (["1. Условие"], "unknown task number"),
    ],
)
def testAmbiguousAssignmentIsRejected(paragraphs: list[str], error: str) -> None:
    document = buildDocx("Вариант", [Paragraph(line) for line in paragraphs])
    published = publishedInput(document, IncomingRequirement(text="Только второе", taskNumber="2"))
    with pytest.raises(AssignmentReadError, match=error):
        readAssignment(published, document)


def testRevisionUsesPublishedDocumentBytes() -> None:
    published = publishedInput(b"invalid")
    with pytest.raises(AssignmentReadError, match="checksum"):
        readAssignment(published, b"changed")
    with pytest.raises(AssignmentReadError, match="DOCX main document"):
        readAssignment(published, b"invalid")


def testWorkerCheckpointsTasksForThePublishedRevision(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any]
) -> None:
    client, streamName = gradingStream
    worker = GradingWorker(StreamConsumer(client, "parser", streamName=streamName), gradingEngine)
    document = buildDocx("Вариант", [Paragraph("1. Первое"), Paragraph("2. Второе")])
    event = contractExample("events/assignmentPublished.json")
    event["payload"]["docRef"]["sha256"] = hashlib.sha256(document).hexdigest()
    client.xadd(streamName, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    lease = worker.jobs.claimAssignment()
    assert lease is not None

    def loadDocument(ref: IncomingDocRef) -> bytes:
        assert ref.model_dump() == event["payload"]["docRef"]
        return document

    first, second = worker.parseAssignment(lease, loadDocument)
    assert first.requirements == ()
    assert second.requirements == (event["payload"]["requirements"][0]["text"],)
    with Session(gradingEngine) as session:
        job = session.get_one(AssignmentJob, lease.key)
        assert job.completedStage == "assignment_parsed"
        assert job.stageData is not None
        assert job.stageData["tasks"] == [
            {"taskId": "t1", "taskNumber": "1", "statement": "Первое", "requirements": []},
            {"taskId": "t2", "taskNumber": "2", "statement": "Второе", "requirements": list(second.requirements)},
        ]

    assert worker.jobs.reschedule(lease, "queued", datetime(2000, 1, 1, tzinfo=UTC))
    resumed = worker.jobs.claimAssignment()
    assert resumed is not None

    def unexpectedLoad(ref: IncomingDocRef) -> bytes:
        raise AssertionError("parsed tasks should be available in the checkpoint")

    assert worker.parseAssignment(resumed, unexpectedLoad) == (first, second)
