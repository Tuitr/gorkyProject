"""Read printed student solution lines and locate them again in the immutable DOCX."""

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal
from xml.etree.ElementTree import Element

from grading.assignment import AssignmentTask
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
    """Paragraph index within its container and half-open Unicode character offsets."""

    docRef: IncomingDocRef
    cells: tuple[CellAddress, ...]
    paragraphIndex: int
    start: int
    end: int


@dataclass(frozen=True)
class SolutionStep:
    taskId: str
    stepId: str
    markdown: str
    sourceAnchor: SourceAnchor
    readStatus: Literal["read"]


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


def resolveAnchor(document: bytes, anchor: SourceAnchor) -> str:
    container = verifiedBody(document, anchor.docRef)
    for address in anchor.cells:
        table = childAt(container, "tbl", address.table)
        row = childAt(table, "tr", address.row)
        container = childAt(row, "tc", address.cell)
    raw = paragraphText(childAt(container, "p", anchor.paragraphIndex))
    if not 0 <= anchor.start < anchor.end <= len(raw):
        raise WordReadError("source anchor points outside the paragraph")
    return raw[anchor.start:anchor.end]


def readSubmission(
    submitted: AttemptSubmittedInput, document: bytes, tasks: Sequence[AssignmentTask]
) -> tuple[SolutionStep, ...]:
    body = verifiedBody(document, submitted.docRef)
    byNumber = {task.taskNumber: task for task in tasks}
    current: AssignmentTask | None = None
    counts: dict[str, int] = {}
    steps = []
    for cells, index, paragraph in locatedParagraphs(body):
        raw = paragraphText(paragraph)
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
            markdown = raw[start:end].strip()
            if current is None or not markdown:
                continue
            ordinal = counts.get(current.taskId, 0) + 1
            counts[current.taskId] = ordinal
            anchor = SourceAnchor(submitted.docRef, cells, index, start, end)
            steps.append(SolutionStep(current.taskId, f"s{ordinal}", markdown, anchor, "read"))
    if current is None:
        raise WordReadError("missing submission task heading")
    return tuple(steps)
