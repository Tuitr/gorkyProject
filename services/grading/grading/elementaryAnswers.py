"""Exact final answers for elementary arithmetic and single-variable equations."""

import re
from collections.abc import Sequence
from fractions import Fraction
from typing import Literal

from contracts.v1.references.reference import ReferenceReady
from grading.assignment import AssignmentTask
from grading.photoLayout import EXPRESSION, conditionKey, compactMath
from grading.probability import EXTRA_CRITERIA, scalar

ANSWER_LABEL = re.compile(r"^(?:ответ|итог)\s*:\s*", re.I)
VARIABLE = re.compile(r"^\d*([a-z])", re.I)
INDEXED_ROOT = re.compile(r"([a-z])(?:_(?:\{[12]\}|[12]))?\s*=\s*(.+)", re.I)
FRACTION_WORKING = re.compile(r"/|\\(?:d|t)?frac")


def values(text: str) -> frozenset[Fraction] | None:
    tokens = text.strip().strip("$").strip()
    if tokens.startswith("(") and tokens.endswith(")"):
        tokens = tokens[1:-1]
    numbers: set[Fraction] = set()
    for token in tokens.split(";"):
        token = token.strip().replace("−", "-")
        symmetrical = token.startswith(("±", r"\pm"))
        if symmetrical:
            token = token.removeprefix("±").removeprefix(r"\pm")
        number = scalar(token)
        if number is None:
            return None
        numbers.add(number)
        if symmetrical:
            numbers.add(-number)
    return frozenset(numbers)


def elementaryAnswer(task: AssignmentTask, lines: Sequence[str]) -> str | None:
    if not lines or task.requirements or EXTRA_CRITERIA.search(task.statement):
        return None
    key = conditionKey(task.statement)
    if key is None:
        return None
    last = lines[-1].strip().rstrip(".")
    if label := ANSWER_LABEL.match(last):
        answer = values(last[label.end():])
    elif EXPRESSION.fullmatch(key):
        original, _, tail = last.strip("$").partition("=")
        answer = values(tail) if compactMath(original) == key else None
    elif variable := VARIABLE.match(key):
        roots: set[Fraction] = set()
        for line in reversed(lines):
            root = INDEXED_ROOT.fullmatch(line.strip().strip("$"))
            if not root or root[1].casefold() != variable[1].casefold() or (part := values(root[2])) is None:
                break
            if roots and "^" not in key and FRACTION_WORKING.search(root[2]):
                break
            roots.update(part)
        answer = frozenset(roots) if roots and ("^" in key or len(roots) == 1) else None
    else:
        answer = None
    return "; ".join(str(number) for number in sorted(answer)) if answer else None


def elementaryVerdict(answer: str | None, reference: ReferenceReady) -> Literal["correct", "incorrect"] | None:
    exact = reference.value.get("exact")
    if answer is None or reference.schemaId != "scalar.v1" or not isinstance(exact, str):
        return None
    expected, student = values(exact), values(answer)
    if expected is None or student is None:
        return None
    return "correct" if expected == student else "incorrect"
