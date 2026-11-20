"""Read printed student solution lines and locate them again in the immutable DOCX."""

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal
from xml.etree.ElementTree import Element

from grading.assignment import AssignmentTask
from grading.formulas import MathParagraph, ReadStatus
from grading.intake import AttemptSubmittedInput, IncomingDocRef
from grading.word import W, WordReadError, documentBody, paragraphText

TASK_HEADING = re.compile(r"^\s*Задание\s+(\d+)(?:[.)](?=\s|$)|(?=\s*$))\s*", re.IGNORECASE)
TEXT_LINE = re.compile(r"[^\r\n]+")


@dataclass(frozen=True)
class CellAddress:
    """Zero-based physical indices within the parent body or cell."""

    table: int
    row: int
    cell: int


@dataclass(frozen=True)
class SourceAnchor:
    """Half-open source offsets. The mathObjects projection counts each OMML equation as one character."""

    docRef: IncomingDocRef
    cells: tuple[CellAddress, ...]
    paragraphIndex: int
    start: int
    end: int
    projection: Literal["text", "mathObjects"] = "text"


@dataclass(frozen=True)
class SolutionStep:
    taskId: str
    stepId: str
    markdown: str
    sourceAnchor: SourceAnchor
    readStatus: ReadStatus


def verifiedBody(document: bytes, ref: IncomingDocRef) -> Element:
    if hashlib.sha256(document).hexdigest() != ref.sha256:
        raise WordReadError("document checksum differs from the submission")
    return documentBody(document)


def locatedParagraphs(
    container: Element, cells: tuple[CellAddress, ...] = ()
) -> Iterator[tuple[tuple[CellAddress, ...], int, Element]]:
    paragraphIndex = 0
    tableIndex = 0
    for block in container:
        if block.tag == W + "p":
            yield cells, paragraphIndex, block
            paragraphIndex += 1
        elif block.tag == W + "tbl":
            for rowIndex, row in enumerate(block.findall(W + "tr")):
                for cellIndex, cell in enumerate(row.findall(W + "tc")):
                    address = CellAddress(tableIndex, rowIndex, cellIndex)
                    yield from locatedParagraphs(cell, (*cells, address))
            tableIndex += 1


def childAt(parent: Element, name: str, index: int) -> Element:
    children = parent.findall(W + name)
    if not 0 <= index < len(children):
        raise WordReadError("source anchor points outside the document")
    return children[index]


def anchoredParagraph(document: bytes, anchor: SourceAnchor) -> Element:
    container = verifiedBody(document, anchor.docRef)
    for address in anchor.cells:
        table = childAt(container, "tbl", address.table)
        row = childAt(table, "tr", address.row)
        container = childAt(row, "tc", address.cell)
    return childAt(container, "p", anchor.paragraphIndex)


def resolveAnchor(document: bytes, anchor: SourceAnchor) -> str:
    paragraph = anchoredParagraph(document, anchor)
    raw = MathParagraph.fromWord(paragraph).source if anchor.projection == "mathObjects" else paragraphText(paragraph)
    if not 0 <= anchor.start < anchor.end <= len(raw):
        raise WordReadError("source anchor points outside the paragraph")
    return raw[anchor.start:anchor.end]


def resolveEquations(document: bytes, anchor: SourceAnchor) -> tuple[Element, ...]:
    """Return the original equation subtrees in this fragment, including unsupported structures."""
    if anchor.projection == "text":
        return ()
    paragraph = MathParagraph.fromWord(anchoredParagraph(document, anchor))
    if not 0 <= anchor.start < anchor.end <= len(paragraph.source):
        raise WordReadError("source anchor points outside the paragraph")
    return tuple(eq for pos, eq in paragraph.objects.items() if anchor.start <= pos < anchor.end)


def readSubmission(
    submitted: AttemptSubmittedInput, document: bytes, tasks: Sequence[AssignmentTask]
) -> tuple[SolutionStep, ...]:
    body = verifiedBody(document, submitted.docRef)
    byNumber = {task.taskNumber: task for task in tasks}
    current: AssignmentTask | None = None
    counts: dict[str, int] = {}
    steps = []
    for cells, index, paragraph in locatedParagraphs(body):
        content = MathParagraph.fromWord(paragraph)
        raw = content.source
        for line in TEXT_LINE.finditer(raw):
            start, end = line.span()
            heading = TASK_HEADING.match(line.group())
            if heading:
                current = byNumber.get(heading.group(1))
                if current is None:
                    raise WordReadError("submission refers to an unknown task number")
                start += heading.end()
                if raw[start:end].strip() == current.statement:
                    continue
            if current is None or not raw[start:end].strip():
                continue
            markdown, status = content.render(start, end)
            ordinal = counts.get(current.taskId, 0) + 1
            counts[current.taskId] = ordinal
            anchor = SourceAnchor(submitted.docRef, cells, index, start, end, "mathObjects" if content.objects else "text")
            steps.append(SolutionStep(current.taskId, f"s{ordinal}", markdown, anchor, status))
    if current is None:
        raise WordReadError("missing submission task heading")
    return tuple(steps)
