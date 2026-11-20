"""Extract numbered tasks from a published assignment and apply teacher requirements."""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from grading.intake import AssignmentPublishedInput

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# A6.2 prints numbers in the main document. Require a boundary so 2.5 stays a number.
TASK_HEADER = re.compile(r"^(?:Задание\s+)?([1-9][0-9]*)[.)](?:\s+|$)", re.IGNORECASE)


class AssignmentReadError(ValueError):
    """The document and event cannot be mapped to unambiguous assignment tasks."""


class AssignmentTextReader(Protocol):
    def paragraphs(self, document: bytes) -> Iterable[str]: ...


class DocxTextReader:
    def paragraphs(self, document: bytes) -> Iterable[str]:
        try:
            with ZipFile(BytesIO(document)) as archive, archive.open("word/document.xml") as part:
                body = ET.parse(part).getroot().find(W + "body")
        except (BadZipFile, KeyError, ET.ParseError) as error:
            raise AssignmentReadError("cannot read the DOCX main document") from error
        if body is None:
            raise AssignmentReadError("missing DOCX body")
        for paragraph in body.iter(W + "p"):
            if paragraph.find(f"{W}pPr/{W}numPr") is not None:
                raise AssignmentReadError("expected task numbers printed as text")
            fragments = []
            for node in paragraph.iter():
                if node.tag == W + "t":
                    fragments.append(node.text or "")
                elif node.tag == W + "tab":
                    fragments.append("\t")
                elif node.tag in (W + "br", W + "cr"):
                    fragments.append("\n")
            yield "".join(fragments)


@dataclass(frozen=True)
class AssignmentTask:
    taskId: str
    taskNumber: str
    statement: str
    requirements: tuple[str, ...]


def readAssignment(
    published: AssignmentPublishedInput, document: bytes, *, reader: AssignmentTextReader = DocxTextReader()
) -> tuple[AssignmentTask, ...]:
    """Read bytes referenced by the event. Task IDs are scoped to its assignment ID and revision."""
    if hashlib.sha256(document).hexdigest() != published.docRef.sha256:
        raise AssignmentReadError("document checksum differs from the published assignment")

    statements: dict[str, list[str]] = {}
    current: list[str] | None = None
    for paragraph in reader.paragraphs(document):
        for line in paragraph.splitlines():
            text = line.strip()
            header = TASK_HEADER.match(text)
            if header:
                number = header.group(1)
                if number in statements:
                    raise AssignmentReadError("duplicate printed task number")
                current = []
                statements[number] = current
                text = text[header.end():]
            if current is not None and text:
                current.append(text)

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
        )
        for number, lines in statements.items()
    )
