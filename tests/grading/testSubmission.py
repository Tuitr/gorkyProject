"""Round-trip solution anchors through Word, the grading worker and PostgreSQL."""

import hashlib
import json
from base64 import b64decode
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import pytest
from docx import Document
from docx.oxml import parse_xml
from redis import Redis
from sqlalchemy import Engine

from contracts.v1.events.checks import DATA_FIELD
from dataset.generate import generateDataset
from grading.assignment import AssignmentTask
from grading.intake import AttemptSubmittedInput, IncomingDocRef
from grading.stream import StreamConsumer
from grading.submission import CellAddress, readSubmission, readSubmissionDetails, resolveAnchor, resolveEquations, resolveObjects
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
    assert [s.markdown for s in steps] == ["$x = 5$", "Ответ: $5$", "$x = 5$", "$x = 5$", "Ответ: $9$"]
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


def testWordPowersAndCoordinatesKeepTheirOriginalSources() -> None:
    word = Document()
    word.add_paragraph("Задание 1.")
    paragraph = word.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
    paragraph.add_run("x")
    paragraph.add_run("2").font.superscript = True
    paragraph.add_run("3").add_break()
    paragraph.add_run("Координаты: (−4; 2)")
    paragraph.add_run().add_break()
    paragraph.add_run("Степень: ")
    ns = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
    power = (
        "<m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e>"
        "<m:sup><m:r><m:t>−12</m:t></m:r></m:sup></m:sSup>"
    )
    paragraph._p.append(parse_xml(f"<m:oMath {ns}>{power}</m:oMath>"))
    paragraph.add_run("3")
    paragraph.add_run().add_break()
    pair = (
        '<m:d><m:dPr><m:sepChr m:val=";"/></m:dPr>'
        "<m:e><m:r><m:t>−4</m:t></m:r></m:e><m:e><m:r><m:t>2</m:t></m:r></m:e></m:d>"
    )
    paragraph._p.append(parse_xml(f"<m:oMathPara {ns}><m:oMath>{pair}</m:oMath></m:oMathPara>"))
    paragraph.add_run().add_break()
    paragraph.add_run("Далее")
    buf = BytesIO()
    word.save(buf)
    document = buf.getvalue()
    steps = readSubmission(submittedInput(document), document, (AssignmentTask("t1", "1", "Вычислите.", ()),))
    assert [step.markdown for step in steps] == [
        "$x^{2}3$", "Координаты: $(-4; 2)$", "Степень: $x^{-12}3$", "$(-4;2)$", "Далее",
    ]
    assert all(step.readStatus == "read" for step in steps)
    assert [resolveAnchor(document, step.sourceAnchor) for step in steps] == [
        "x23", "Координаты: (−4; 2)", "Степень: \ufffc3", "\ufffc", "Далее",
    ]
    original, = resolveEquations(document, steps[2].sourceAnchor)
    assert original.findtext(".//{*}sup/{*}r/{*}t") == "−12"
    assert resolveEquations(document, steps[3].sourceAnchor)[0].find("{*}d") is not None
    # Offsets stored by B2.2 count only Word text, even if the original paragraph contains OMML.
    legacy = replace(steps[-1].sourceAnchor, projection="text", start=steps[-1].sourceAnchor.start - 2,
                     end=steps[-1].sourceAnchor.end - 2)
    assert resolveAnchor(document, legacy) == "Далее"


@pytest.mark.parametrize("unsupported", [
    "<m:f><m:num><m:r><m:t>12</m:t></m:r></m:num><m:den><m:r><m:t>7</m:t></m:r></m:den></m:f>",
    "<m:sSup><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>",
    "",
])
def testUncertainEquationCanBeReopenedWithoutInventingAValue(unsupported: str) -> None:
    word = Document()
    word.add_paragraph("Задание 1.")
    paragraph = word.add_paragraph("Ответ: ")
    ns = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
    paragraph._p.append(parse_xml(f"<m:oMath {ns}>{unsupported}</m:oMath>"))
    paragraph.add_run(", проверка: ")
    paragraph._p.append(parse_xml(f"<m:oMath {ns}><m:r><m:t>−3</m:t></m:r></m:oMath>"))
    buf = BytesIO()
    word.save(buf)
    document = buf.getvalue()
    step, = readSubmission(submittedInput(document), document, (AssignmentTask("t1", "1", "Ответьте.", ()),))
    assert step.readStatus == "ambiguous"
    assert step.markdown == "Ответ: [формула требует уточнения], проверка: $-3$"
    original, neighbor = resolveEquations(document, step.sourceAnchor)
    assert "".join(original.itertext()) == "".join(parse_xml(f"<m:oMath {ns}>{unsupported}</m:oMath>").itertext())
    assert "".join(neighbor.itertext()) == "−3"


def testOpenDatasetStepsMatchTheManifest() -> None:
    dataset = generateDataset()
    cases = [case for case in dataset.manifest.cases if case.split in ("demo", "e2e")]
    for file in dict.fromkeys(case.submissionRef.file for case in cases):
        items = [case for case in cases if case.submissionRef.file == file]
        tasks = [AssignmentTask("t" + ref.taskNumber, ref.taskNumber, ref.statement, ())
                 for ref in (item.assignmentRef for item in items)]
        document = dataset.files[file]
        reading = readSubmissionDetails(submittedInput(document), document, tasks)
        assert not reading.unassignedFragments
        assert [(step.taskId, step.stepId, step.markdown, step.readStatus) for step in reading.steps] == [
            ("t" + item.assignmentRef.taskNumber, expected.stepId, expected.markdown, "read")
            for item in items for expected in item.expectedSteps
        ], file
        for task, item in zip(reading.taskReadings, items, strict=True):
            if item.expectedVerdict.answer == "skipped":
                assert task.status == "skipped"
            elif item.expectedVerdict.reasoning == "not_shown":
                assert task.status == "answerOnly"
            else:
                assert task.status == "hasSteps"
            assert task.reviewReason is None


def testCopiedStatementAnswerOnlyAndUnreadableWorkInTheSameDocument() -> None:
    word = Document()
    tasks = (
        AssignmentTask("t1", "1", "Дан вектор.\nНайдите его длину.", ()),
        AssignmentTask("t2", "2", "Вычислите сумму.", ()),
        AssignmentTask("t3", "3", "Запишите решение.", ()),
        AssignmentTask("t4", "4", "Найдите разность.", ()),
    )
    word.add_paragraph("Задание 1. Дан   вектор.")
    word.add_paragraph("Найдите его длину.")
    word.add_paragraph("Ответ:  ")
    word.add_paragraph("Задание № 2. Вычислите сумму. Ответ: −5")
    word.add_paragraph("Задание 3.")
    cell = word.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "x = 3"
    # A real picture saved inside the DOCX. The original drawing must remain locatable.
    picture = b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")
    cell.add_paragraph().add_run().add_picture(BytesIO(picture))
    word.add_paragraph("Задание 4.")
    word.add_paragraph("7 − 3 = 4")
    word.add_paragraph("Ответ: 4")
    buf = BytesIO()
    word.save(buf)
    document = buf.getvalue()

    reading = readSubmissionDetails(submittedInput(document), document, tasks)
    assert not reading.unassignedFragments
    assert [(task.taskId, task.status) for task in reading.taskReadings] == [
        ("t1", "skipped"), ("t2", "answerOnly"), ("t3", "needsReview"), ("t4", "hasSteps"),
    ]
    assert reading.taskReadings[2].reviewReason
    assert [(step.taskId, step.stepId, step.readStatus) for step in reading.steps] == [
        ("t2", "s1", "read"), ("t3", "s1", "read"), ("t3", "s2", "ambiguous"),
        ("t4", "s1", "read"), ("t4", "s2", "read"),
    ]
    assert resolveAnchor(document, reading.steps[0].sourceAnchor).strip() == "Ответ: −5"
    anchor = reading.steps[2].sourceAnchor
    assert anchor.cells == (CellAddress(0, 0, 0),)
    drawing, = resolveObjects(document, anchor)
    assert drawing.tag.endswith("}drawing") and drawing.find(".//{*}blip") is not None
    assert not resolveEquations(document, anchor)
    assert reading.steps[3].markdown == "$7 - 3 = 4$"


@pytest.mark.parametrize("unknown", ["Задание 99.", "Задание ?."])
def testUnknownHeadingLeavesItsLinesForReviewAndResumesAtTheNextHeading(unknown: str) -> None:
    word = Document()
    for line in ("Задание 1.", "2 + 3 = 5", unknown, "Ответ: 88", "Задание 3.", "−4"):
        word.add_paragraph(line)
    buf = BytesIO()
    word.save(buf)
    document = buf.getvalue()
    tasks = [AssignmentTask(f"t{i}", str(i), "Вычислите.", ()) for i in (1, 2, 3)]
    reading = readSubmissionDetails(submittedInput(document), document, tasks)
    assert [task.status for task in reading.taskReadings] == ["hasSteps", "needsReview", "answerOnly"]
    assert reading.taskReadings[1].reviewReason
    assert [(step.taskId, step.markdown) for step in reading.steps] == [("t1", "$2 + 3 = 5$"), ("t3", "$-4$")]
    assert all(fragment.reviewReason for fragment in reading.unassignedFragments)
    assert [
        resolveAnchor(document, fragment.sourceAnchor) for fragment in reading.unassignedFragments
        if fragment.sourceAnchor is not None
    ] == [unknown, "Ответ: 88"]


def testHiddenOrUnboundWorkDoesNotBecomeASkip() -> None:
    word = Document()
    word.add_paragraph("Задание 1.")
    paragraph = word.add_paragraph("Задание 2.")
    paragraph._p.addprevious(parse_xml('<w:altChunk xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'))
    word.add_paragraph("Ответ: \ufffd")
    buf = BytesIO()
    word.save(buf)
    document = buf.getvalue()
    tasks = [AssignmentTask(f"t{i}", str(i), "Вычислите.", ()) for i in (1, 2, 3)]
    reading = readSubmissionDetails(submittedInput(document), document, tasks)
    assert all(task.status == "needsReview" and task.reviewReason for task in reading.taskReadings)
    assert reading.unassignedFragments[0].reviewReason
    assert len(reading.steps) == 1 and reading.steps[0].readStatus == "ambiguous"
    with pytest.raises(WordReadError, match="cannot read"):
        readSubmissionDetails(submittedInput(b"incomplete"), b"incomplete", tasks)


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
    stored = worker.parseSubmissionDetails(resumed, unexpectedLoad)
    assert stored.steps == steps and all(task.status == "hasSteps" for task in stored.taskReadings)

    # Old checkpoints could contain zero steps even when an equation was present.
    assert worker.jobs.checkpoint(resumed, "submission_parsed", {"steps": []})
    assert worker.jobs.reschedule(resumed, "queued", datetime(2000, 1, 1, tzinfo=UTC))
    legacyLease = worker.jobs.claimCheck()
    assert legacyLease is not None
    assert worker.parseSubmission(legacyLease, loadDocument) == steps
    assert worker.jobs.checkpoint(legacyLease, "submission_parsed", {"parserVersion": 2, "steps": []})
    assert worker.jobs.reschedule(legacyLease, "queued", datetime(2000, 1, 1, tzinfo=UTC))
    versionTwo = worker.jobs.claimCheck()
    assert versionTwo is not None
    assert worker.parseSubmissionDetails(versionTwo, loadDocument) == stored
