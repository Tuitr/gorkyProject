"""Read student solution lines and anchor them in the original or the OCR transcript."""

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Literal
from xml.etree.ElementTree import Element

from contracts.v1.grading.assessment import OcrStatus, PhotoBox
from contracts.v1.grading.checkresult import PhotoPage
from grading.assignment import AssignmentTask
from grading.formulas import M, MathParagraph, ReadStatus
from grading.intake import AttemptSubmittedInput, IncomingDocRef
from grading.photoOcr import CANCELLED, CROSSOUT, PHOTO_TASK_HEADING, UNREADABLE, TranscribedLine, renderTranscript
from grading.word import W, WordReadError, documentBody, paragraphText

TASK_START = re.compile(r"^\s*Задание\b", re.IGNORECASE)
TASK_HEADING = re.compile(r"^\s*Задание\s+(?:№\s*)?(\d+)(?:[.):](?=\s|$)|(?=\s*$))\s*", re.IGNORECASE)
TEXT_LINE = re.compile(r"[^\r\n]+")
ANSWER_LABEL = re.compile(r"^ответ\s*:\s*", re.IGNORECASE)
SCALAR = r"[+-]?(?:\d+(?:[.,]\d+)?(?:/\d+)?|\\sqrt\{\d+\})"
ANSWER_VALUE = re.compile(rf"(?:[a-zA-Z]\s*=\s*)?(?:{SCALAR}|\(\s*{SCALAR}\s*[;,]\s*{SCALAR}\s*\)|да|нет)", re.I)
PREAMBLE = re.compile(r"^\s*(?:ученик\s*:|самостоятельная работа\b)", re.I)
PHOTO_VARIANT = re.compile(r"^вариант\s*[:№—–-]?\s*\$?\d+\$?\.?$", re.I)
PHOTO_SHEET_NUMBER = re.compile(r"^номер\s*[:№]\s*\$?(\d+)\$?\.?$", re.I)
PHOTO_TASK_LIST = re.compile(
    r"^задани[ея]\s*[:—–-]?\s*№?\s*\d+(?:\s*[,;]\s*№?\s*\d+)+\.?$", re.I,
)
PARTIAL_CROSSOUT = "Часть решения зачёркнута. Читаемые шаги проверяются отдельно. Итог нужно сверить с фото."


@dataclass(frozen=True)
class CellAddress:
    """Zero-based physical indices within the parent body or cell."""

    table: int
    row: int
    cell: int


@dataclass(frozen=True)
class SourceAnchor:
    """Half-open offsets in the text projection used when the step was read.

    mathObjects adds one character for each equation. wordObjects also counts embedded Word objects.
    """

    docRef: IncomingDocRef
    cells: tuple[CellAddress, ...]
    paragraphIndex: int
    start: int
    end: int
    projection: Literal["text", "mathObjects", "wordObjects", "ocr"] = "text"
    pageIndex: int = 0
    box: PhotoBox | None = None


@dataclass(frozen=True)
class SolutionStep:
    taskId: str
    stepId: str
    markdown: str
    sourceAnchor: SourceAnchor
    readStatus: ReadStatus | Literal["crossout"]


@dataclass(frozen=True)
class TaskReading:
    taskId: str
    status: Literal["skipped", "omitted", "answerOnly", "hasSteps", "needsReview", "partialCrossout"]
    reviewReason: str | None = None


@dataclass(frozen=True)
class UnassignedFragment:
    reviewReason: str
    sourceAnchor: SourceAnchor | None


@dataclass(frozen=True)
class SubmissionReading:
    steps: tuple[SolutionStep, ...]
    taskReadings: tuple[TaskReading, ...]
    unassignedFragments: tuple[UnassignedFragment, ...]
    transcriptMarkdown: str | None = None
    photoPages: tuple[PhotoPage, ...] = ()
    ocrStatus: OcrStatus | None = None
    ocrReason: str | None = None

    @property
    def omittedTaskIds(self) -> frozenset[str]:
        return frozenset(task.taskId for task in self.taskReadings if task.status == "omitted")


def verifiedBody(document: bytes, ref: IncomingDocRef) -> Element:
    if hashlib.sha256(document).hexdigest() != ref.sha256:
        raise WordReadError("document checksum differs from the submission")
    return documentBody(document)


def tableChildren(parent: Element, name: str, properties: tuple[str, ...]) -> Iterator[tuple[int, Element | None]]:
    index = 0
    for child in parent:
        if child.tag == W + name:
            yield index, child
            index += 1
        elif child.tag not in properties:
            yield index, None


def locatedParagraphs(
    container: Element, cells: tuple[CellAddress, ...] = ()
) -> Iterator[tuple[tuple[CellAddress, ...], int, Element | None]]:
    paragraphIndex = 0
    tableIndex = 0
    for block in container:
        if block.tag == W + "p":
            yield cells, paragraphIndex, block
            paragraphIndex += 1
        elif block.tag == W + "tbl":
            for rowIndex, row in tableChildren(block, "tr", (W + "tblPr", W + "tblGrid")):
                if row is None:
                    yield cells, paragraphIndex, None
                    continue
                for cellIndex, cell in tableChildren(row, "tc", (W + "trPr", W + "tblPrEx")):
                    if cell is None:
                        yield cells, paragraphIndex, None
                    else:
                        address = CellAddress(tableIndex, rowIndex, cellIndex)
                        yield from locatedParagraphs(cell, (*cells, address))
            tableIndex += 1
        elif block.tag not in (W + "sectPr", W + "tcPr", W + "bookmarkStart", W + "bookmarkEnd"):
            # A content control or imported block may contain work or another task heading.
            yield cells, paragraphIndex, None


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


def resolveAnchor(document: bytes, anchor: SourceAnchor, *, transcriptMarkdown: str | None = None) -> str:
    if anchor.projection == "ocr":
        if transcriptMarkdown is None or hashlib.sha256(document).hexdigest() != anchor.docRef.sha256:
            raise WordReadError("an OCR anchor requires the original photo and its Markdown transcript")
        lines = transcriptMarkdown.splitlines()
        if not 0 <= anchor.paragraphIndex < len(lines):
            raise WordReadError("source anchor points outside the transcript")
        line = lines[anchor.paragraphIndex]
        if not 0 <= anchor.start < anchor.end <= len(line):
            raise WordReadError("source anchor points outside the transcript line")
        return line[anchor.start:anchor.end]
    paragraph = anchoredParagraph(document, anchor)
    raw = (paragraphText(paragraph) if anchor.projection == "text"
           else MathParagraph.fromWord(paragraph, includeEmbedded=anchor.projection == "wordObjects").source)
    if not 0 <= anchor.start < anchor.end <= len(raw):
        raise WordReadError("source anchor points outside the paragraph")
    return raw[anchor.start:anchor.end]


def resolveObjects(document: bytes, anchor: SourceAnchor) -> tuple[Element, ...]:
    """Return original inline objects, including drawings and unsupported equations."""
    if anchor.projection in ("text", "ocr"):
        return ()
    paragraph = MathParagraph.fromWord(anchoredParagraph(document, anchor), includeEmbedded=anchor.projection == "wordObjects")
    if not 0 <= anchor.start < anchor.end <= len(paragraph.source):
        raise WordReadError("source anchor points outside the paragraph")
    return tuple(eq for pos, eq in paragraph.objects.items() if anchor.start <= pos < anchor.end)


def resolveEquations(document: bytes, anchor: SourceAnchor) -> tuple[Element, ...]:
    return tuple(obj for obj in resolveObjects(document, anchor) if obj.tag == M + "oMath")


@dataclass(frozen=True)
class SourceLine:
    paragraph: MathParagraph
    anchor: SourceAnchor

    def text(self) -> str:
        return self.paragraph.typed[self.anchor.start:self.anchor.end]


def afterStatement(lines: list[SourceLine], statement: str) -> Iterator[SourceLine]:
    """Drop only a complete leading copy, even across paragraphs or before an inline answer."""
    pattern = r"\s+".join(re.escape(word) for word in statement.split())
    joined = "\n".join(line.text() for line in lines)
    copy = re.match(r"\s*" + pattern + r"(?=\s|$)", joined) if pattern else None
    skip = copy.end() if copy else 0
    for line in lines:
        length = line.anchor.end - line.anchor.start
        if skip < length:
            if skip:
                line = SourceLine(line.paragraph, replace(line.anchor, start=line.anchor.start + skip))
            yield line
        skip = max(0, skip - length - 1)


def taskReading(
    task: AssignmentTask, steps: list[SolutionStep] | None, unassigned: bool, *, endsWithCrossout: bool = False,
) -> TaskReading:
    if steps is None:
        return TaskReading(task.taskId, "needsReview", "В работе не найден заголовок этого задания.")
    if (endsWithCrossout or any(step.readStatus == "crossout" for step in steps)) and any(
        step.readStatus == "read" for step in steps
    ):
        return TaskReading(task.taskId, "partialCrossout", PARTIAL_CROSSOUT)
    if any(step.readStatus != "read" for step in steps):
        return TaskReading(task.taskId, "needsReview", "Часть записи не удалось прочитать. Сверьте отмеченный шаг с оригиналом.")
    if not steps:
        if unassigned:
            return TaskReading(task.taskId, "needsReview", "В работе есть фрагмент без привязки. Уточните, относится ли он к этому заданию.")
        return TaskReading(task.taskId, "skipped")
    if len(steps) == 1:
        value = steps[0].markdown
        if ANSWER_LABEL.match(value) or ANSWER_VALUE.fullmatch(value.removeprefix("$").removesuffix("$")):
            return TaskReading(task.taskId, "answerOnly")
    return TaskReading(task.taskId, "hasSteps")


def readSubmissionDetails(
    submitted: AttemptSubmittedInput, document: bytes, tasks: Sequence[AssignmentTask]
) -> SubmissionReading:
    body = verifiedBody(document, submitted.docRef)
    byNumber = {task.taskNumber: task for task in tasks}
    sections: list[tuple[AssignmentTask, list[SourceLine]]] = []
    current: list[SourceLine] | None = None
    preamble = True
    unassigned: list[UnassignedFragment] = []
    for cells, index, paragraph in locatedParagraphs(body):
        if paragraph is None:
            unassigned.append(UnassignedFragment("В исходном DOCX есть блок с неизвестной структурой. Проверьте его привязку к заданию.", None))
            current = None
            preamble = False
            continue
        content = MathParagraph.fromWord(paragraph, includeEmbedded=True)
        raw = content.source
        projection: Literal["text", "wordObjects"] = "wordObjects" if content.objects else "text"
        for line in TEXT_LINE.finditer(raw):
            start, end = line.span()
            if TASK_START.match(line.group()):
                preamble = False
                heading = TASK_HEADING.match(line.group())
                current = None
                if heading and (task := byNumber.get(heading.group(1))) is not None:
                    current = []
                    sections.append((task, current))
                    start += heading.end()
            if not raw[start:end].strip() or (preamble and PREAMBLE.match(line.group())):
                continue
            anchor = SourceAnchor(submitted.docRef, cells, index, start, end, projection)
            if current is None:
                unassigned.append(UnassignedFragment("Не удалось определить номер задания для этого фрагмента.", anchor))
            else:
                current.append(SourceLine(content, anchor))

    byTask: dict[str, list[SolutionStep]] = {}
    ordered: list[SolutionStep] = []
    for task, lines in sections:
        steps = byTask.setdefault(task.taskId, [])
        for item in afterStatement(lines, task.statement):
            anchor = item.anchor
            markdown, status = item.paragraph.render(anchor.start, anchor.end)
            if not markdown or ANSWER_LABEL.fullmatch(markdown):
                continue
            step = SolutionStep(task.taskId, f"s{len(steps) + 1}", markdown, anchor, status)
            steps.append(step)
            ordered.append(step)
    readings = tuple(taskReading(task, byTask.get(task.taskId), bool(unassigned)) for task in tasks)
    return SubmissionReading(tuple(ordered), readings, tuple(unassigned))


def readSubmission(
    submitted: AttemptSubmittedInput, document: bytes, tasks: Sequence[AssignmentTask]
) -> tuple[SolutionStep, ...]:
    return readSubmissionDetails(submitted, document, tasks).steps


def withoutDetachedCrossouts(text: str) -> str | None:
    """Omit crossed-out alternatives at the edges, preserving anything inside an expression."""
    active = CROSSOUT.sub("", text).strip()
    if not active:
        return ""
    # Strip standalone answers or complete math expressions at the edges.
    if any(
        match[1].strip() not in (CANCELLED, UNREADABLE)
        and not re.fullmatch(r"[+-−]?\d+(?:[.,]\d+)?|\$[^$]+\$", match[1].strip())
        for match in CROSSOUT.finditer(text)
    ):
        return None
    while match := CROSSOUT.match(text):
        if match.end() < len(text) and not text[match.end()].isspace():
            return None
        text = text[match.end():].lstrip()
    for match in CROSSOUT.finditer(text):
        if match.start() and text[match.start() - 1].isspace():
            if not CROSSOUT.sub("", text[match.start():]).strip():
                text = text[:match.start()].rstrip()
                break
    if "~" in text or text.count("$") % 2 or text.endswith(tuple("=+-−*/^_\\{([")):
        return None
    return text


def readPhotoSubmission(
    submitted: AttemptSubmittedInput, lines: Sequence[TranscribedLine], tasks: Sequence[AssignmentTask],
) -> SubmissionReading:
    return readPhotoPages((submitted.docRef,), (lines,), tasks)


def isPhotoPreamble(text: str, byNumber: dict[str, str]) -> bool:
    text = text.strip()
    number = PHOTO_SHEET_NUMBER.fullmatch(text)
    return bool(PHOTO_VARIANT.fullmatch(text) or PHOTO_TASK_LIST.fullmatch(text)
                or (number and number.group(1) not in byNumber))


def readPhotoPages(
    refs: Sequence[IncomingDocRef], pages: Sequence[Sequence[TranscribedLine]], tasks: Sequence[AssignmentTask],
) -> SubmissionReading:
    byNumber = {task.taskNumber: task.taskId for task in tasks}
    byTask: dict[str, list[SolutionStep]] = {}
    unassigned: list[UnassignedFragment] = []
    ordered: list[SolutionStep] = []
    current = None
    cancelledSection = False
    endsWithCrossout: dict[str, bool] = {}
    reason = "Часть записи нужно сверить с фотографией."

    hasReadableContent = False
    for pageIndex, (ref, lines) in enumerate(zip(refs, pages, strict=True)):
        if not lines:
            # An unreadable page may contain a new task heading.
            anchor = SourceAnchor(ref, (), 0, 0, len(UNREADABLE), "ocr", pageIndex)
            unassigned.append(UnassignedFragment("На странице не прочитано ни одной строки.", anchor))
            if current is not None and not cancelledSection:
                steps = byTask[current]
                step = SolutionStep(current, f"s{len(steps) + 1}", UNREADABLE, anchor, "ambiguous")
                steps.append(step)
                ordered.append(step)
            current = None
            cancelledSection = False
        for index, line in enumerate(lines):
            text = line.text
            if not byTask and isPhotoPreamble(text, byNumber):
                continue
            if line.kind == "cancelled":
                if current is not None:
                    endsWithCrossout[current] = True
                continue
            anchor = SourceAnchor(ref, (), index, 0, len(text), "ocr", pageIndex, line.box)
            if line.kind in ("heading", "cancelled_heading"):
                cancelledSection = line.kind == "cancelled_heading"
                heading = PHOTO_TASK_HEADING.match(text)
                current = byNumber.get(heading.group(1) or heading.group(2)) if heading else None
                if current is None:
                    unassigned.append(UnassignedFragment("Номер задания на фото нужно сверить.", anchor))
                    continue
                assert heading is not None
                byTask.setdefault(current, [])
                if cancelledSection:
                    endsWithCrossout[current] = True
                anchor = replace(anchor, start=heading.end())
                text = text[heading.end():]
            if cancelledSection:
                continue
            active = withoutDetachedCrossouts(text) if "~~" in text else text
            readStatus: ReadStatus | Literal["crossout"] = "read"
            if active is None:
                readStatus = "crossout"
            elif line.kind == "uncertain" or UNREADABLE in active:
                readStatus = "ambiguous"
            if active == "" and "~~" in text and current is not None:
                endsWithCrossout[current] = True
            text = text if active is None else active
            if not text or ANSWER_LABEL.fullmatch(text):
                continue
            if re.search(r"\w", CROSSOUT.sub("", text).replace(UNREADABLE, "")):
                hasReadableContent = True
            if current is None:
                unassigned.append(UnassignedFragment("Не удалось определить номер задания для строки на фото.", anchor))
            else:
                if current in endsWithCrossout:
                    endsWithCrossout[current] = False
                steps = byTask[current]
                markdown = "Формулу с зачёркиванием нужно сверить с фото." if readStatus == "crossout" else text
                step = SolutionStep(current, f"s{len(steps) + 1}", markdown, anchor, readStatus)
                steps.append(step)
                ordered.append(step)

    # An unresolved page or heading can hide a task that was actually attempted.
    completeReading = bool(byTask) and not unassigned and not any(
        step.readStatus == "ambiguous" and step.markdown == UNREADABLE for step in ordered
    )
    readings = []
    for task in tasks:
        taskSteps = byTask.get(task.taskId)
        if not taskSteps and task.taskId in endsWithCrossout:
            reading = TaskReading(task.taskId, "skipped", "Решение полностью зачёркнуто.")
        elif not taskSteps and completeReading:
            reading = TaskReading(task.taskId, "omitted")
        else:
            reading = taskReading(task, taskSteps, bool(unassigned), endsWithCrossout=endsWithCrossout.get(task.taskId, False))
            if reading.status in ("skipped", "needsReview"):
                reading = TaskReading(task.taskId, "needsReview", reason)
        readings.append(reading)
    transcripts = tuple(PhotoPage(pageIndex=i, transcriptMarkdown=renderTranscript(page)) for i, page in enumerate(pages))
    status: OcrStatus = "readable"
    detail = None
    if not hasReadableContent and not endsWithCrossout and not completeReading:
        status, detail = "unreadable", "На снимках не удалось прочитать решение."
    elif unassigned or any(item.status in ("needsReview", "partialCrossout") for item in readings):
        status = "needs_review"
        if unassigned:
            fragment = unassigned[0]
            page = fragment.sourceAnchor.pageIndex + 1 if fragment.sourceAnchor else 1
            detail = f"Страница {page}. {fragment.reviewReason}"
        else:
            item = next(item for item in readings if item.status in ("needsReview", "partialCrossout"))
            number = next(task.taskNumber for task in tasks if task.taskId == item.taskId)
            detail = f"Задание № {number}. {item.reviewReason or reason}"
    return SubmissionReading(tuple(ordered), tuple(readings), tuple(unassigned),
                             "\n".join(page.transcriptMarkdown for page in transcripts), transcripts, status, detail)
