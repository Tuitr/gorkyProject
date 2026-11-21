"""Recognize task labels and match copied conditions on unnumbered photo pages."""

import re
from collections.abc import Sequence

from contracts.v1.grading.assessment import PhotoBox
from grading.assignment import AssignmentTask
from grading.photoOcr import PHOTO_TASK_HEADING, TranscribedLine

VISIBLE_LABEL = re.compile(r"^(?:№|зада(?:ние|ча)\b|\d+[.):]\s*(?=\$|$))", re.I)
MATH_TEXT = re.compile(r"[a-z0-9+=^\-−×*/{}()\s]+", re.I)
NUM = r"\d+(?:[.,]\d+)?"
EXPRESSION = re.compile(rf"{NUM}(?:[+*/-]{NUM})+")
EQUATION = re.compile(rf"(?:\d*[a-z](?:\^\d+)?(?:[+-]{NUM})?)={NUM}", re.I)


def photoHeading(line: TranscribedLine) -> re.Match[str] | None:
    if line.kind in ("heading", "cancelled_heading") or (line.kind == "line" and VISIBLE_LABEL.match(line.text)):
        return PHOTO_TASK_HEADING.match(line.text)
    return None


def compactMath(text: str) -> str:
    return re.sub(r"[\s{}$]", "", text).replace("−", "-").replace("×", "*").casefold().rstrip(".")


def conditionKey(statement: str) -> str | None:
    candidates = {value for segment in MATH_TEXT.findall(statement)
                  if (value := compactMath(segment)) and (EXPRESSION.fullmatch(value) or EQUATION.fullmatch(value))}
    return next(iter(candidates)) if len(candidates) == 1 else None


def horizontalOverlap(first: PhotoBox, second: PhotoBox) -> float:
    return min(first.x + first.w, second.x + second.w) - max(first.x, second.x)


def conditionTaskNumbers(lines: Sequence[TranscribedLine], tasks: Sequence[AssignmentTask]) -> dict[int, str | None]:
    if any(line.kind in ("heading", "cancelled_heading") or photoHeading(line) for line in lines):
        return {}
    numbers: dict[str, list[str]] = {}
    for task in tasks:
        if key := conditionKey(task.statement):
            numbers.setdefault(key, []).append(task.taskNumber)
    seeds: dict[int, str] = {}
    for index, line in enumerate(lines):
        if line.kind != "line" or "~" in line.text:
            continue
        value = compactMath(line.text)
        matches = [items[0] for key, items in numbers.items()
                   if len(items) == 1 and (value == key or value.startswith(key + "="))]
        if len(matches) == 1:
            seeds[index] = matches[0]
    if not seeds:
        return {}
    positioned = [(index, box) for index in seeds if (box := lines[index].box) is not None]
    columns = any(horizontalOverlap(first, second) <= 0 and abs(first.y - second.y) < max(first.h, second.h)
                  for i, (_, first) in enumerate(positioned) for _, second in positioned[i + 1:])
    if not columns:
        return dict(seeds)
    hints: dict[int, str | None] = dict(seeds)
    for index, line in enumerate(lines):
        if index in seeds:
            continue
        box = line.box
        candidates = [(anchor.y, i) for i, anchor in positioned if box is not None and
                      anchor.y <= box.y and horizontalOverlap(anchor, box) > 0]
        hints[index] = seeds[max(candidates)[1]] if candidates else None
    return hints
