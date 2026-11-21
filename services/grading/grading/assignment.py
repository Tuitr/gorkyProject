"""Extract numbered tasks from a published assignment and apply teacher requirements."""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import JsonValue

from grading.intake import AssignmentPublishedInput
from grading.photoOcr import UNREADABLE, transcribePhoto
from shared.submissionmedia import photoMediaType
from grading.word import W, WordReadError, documentBody, paragraphText

SUBJECT_CONSTRAINT = "subject"
"""Reference constraint that names a subject other than math, for example {"subject": "physics"}.

Math tasks keep empty constraints, so their reference keys and solvers did not change when physics came."""


def subjectConstraints(subject: str) -> dict[str, JsonValue]:
    return {} if subject == "math" else {SUBJECT_CONSTRAINT: subject}


def subjectOf(task: "AssignmentTask") -> str:
    subject = task.constraints.get(SUBJECT_CONSTRAINT)
    return subject if isinstance(subject, str) else "math"


# A6.2 prints numbers in the main document. Require a boundary so 2.5 stays a number.
TASK_HEADER = re.compile(r"^(?:Задание\s+)?([1-9][0-9]*)[.)](?:\s+|$)", re.IGNORECASE)


class AssignmentReadError(ValueError):
    """The document and event cannot be mapped to unambiguous assignment tasks."""


class PhotoClarification(AssignmentReadError):
    """The assignment needs a clearer original before references can be prepared."""


PHOTO_HEADER = re.compile(
    r"^(?:(?:задание|задача)\s*)?(?:№\s*)?([1-9][0-9]*)(?:[.):](?=\s|$)|(?=\s|$))", re.IGNORECASE
)


def photoParagraphs(document: bytes, mediaType: str) -> Iterable[tuple[str | None, str]]:
    for line in transcribePhoto(document, mediaType, assignment=True):
        if line.kind in ("uncertain", "cancelled") or UNREADABLE in line.text:
            raise PhotoClarification("Уточните неразборчивое условие, формулу или рисунок.")
        if line.kind == "heading":
            header = PHOTO_HEADER.match(line.text)
            if header is None:
                raise PhotoClarification("Уточните номер задания на фотографии.")
            yield header.group(1), line.text[header.end():].strip()
        else:
            yield None, line.text


class AssignmentTextReader(Protocol):
    def paragraphs(self, document: bytes) -> Iterable[str]: ...


class DocxTextReader:
    def paragraphs(self, document: bytes) -> Iterable[str]:
        try:
            body = documentBody(document)
        except WordReadError as error:
            raise AssignmentReadError(str(error)) from error
        for paragraph in body.iter(W + "p"):
            if paragraph.find(f"{W}pPr/{W}numPr") is not None:
                raise AssignmentReadError("expected task numbers printed as text")
            yield paragraphText(paragraph)


@dataclass(frozen=True)
class AssignmentTask:
    taskId: str
    taskNumber: str
    statement: str
    requirements: tuple[str, ...]
    constraints: dict[str, JsonValue] = field(default_factory=dict)


def readAssignment(
    published: AssignmentPublishedInput, document: bytes, *, reader: AssignmentTextReader | None = None
) -> tuple[AssignmentTask, ...]:
    """Read bytes referenced by the event. Task IDs are scoped to its assignment ID and revision."""
    if hashlib.sha256(document).hexdigest() != published.docRef.sha256:
        raise AssignmentReadError("document checksum differs from the published assignment")

    mediaType = photoMediaType(published.docRef.key)
    if reader is None and mediaType is not None:
        try:
            return numberedTasks(published, photoParagraphs(document, mediaType), requireHeading=True)
        except AssignmentReadError as error:
            raise PhotoClarification(str(error)) from error
    return numberedTasks(published, documentParagraphs(document, reader or DocxTextReader()))


def documentParagraphs(document: bytes, reader: AssignmentTextReader) -> Iterable[tuple[str | None, str]]:
    for paragraph in reader.paragraphs(document):
        for line in paragraph.splitlines():
            text = line.strip()
            header = TASK_HEADER.match(text)
            yield (header.group(1), text[header.end():]) if header else (None, text)


def numberedTasks(
    published: AssignmentPublishedInput, paragraphs: Iterable[tuple[str | None, str]], *, requireHeading: bool = False,
) -> tuple[AssignmentTask, ...]:
    statements: dict[str, list[str]] = {}
    current: list[str] | None = None
    for number, text in paragraphs:
        if number is not None:
            if number in statements:
                raise AssignmentReadError("duplicate printed task number")
            current = []
            statements[number] = current
        if current is not None and text:
            current.append(text)
        elif current is None and requireHeading and text:
            raise PhotoClarification("Уточните номер первого задания на фотографии.")
    if not statements or not all(statements.values()):
        raise AssignmentReadError("missing numbered task or empty statement")
    for requirement in published.requirements:
        if requirement.taskNumber is not None and requirement.taskNumber not in statements:
            raise AssignmentReadError("teacher requirement refers to an unknown task number")
    return tuple(
        AssignmentTask(
            taskId=f"t{number}",
            taskNumber=number,
            statement="\n".join(lines),
            requirements=tuple(item.text for item in published.requirements if item.taskNumber in (None, number)),
            constraints=subjectConstraints(published.subject),
        )
        for number, lines in statements.items()
    )
