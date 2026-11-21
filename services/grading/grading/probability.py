"""Read an explicit probability from an OCR line without reconstructing crossed text."""

import re
from fractions import Fraction
from typing import Literal

from contracts.v1.references.reference import ReferenceReady

OUTSIDE_FORMULA_CROSSOUT = re.compile(r"(\$[^$]+\$)(?:\s*~~[^~]+~~)+")
PROBABILITY_START = re.compile(r"^\s*\$?(?:P\s*\(\s*[A-Za-zА-Яа-я]\s*\)\s*=|(?:ответ|итог)\s*:)", re.I)
PROBABILITY_REQUEST = re.compile(r"(?:найдите|вычислите|определите)\s+вероятность", re.I)
LATEX_FRACTION = re.compile(r"\\(?:d|t)?frac\{([+-]?\d+)\}\{(\d+)\}")
REPEATING = re.compile(r"(\d+)\.(\d*)\((\d+)\)")
NUMBER = re.compile(r"[+-]?\d+(?:[./]\d+)?")
EXTRA_CRITERIA = re.compile(r"обосну|докаж|поясн|рассужд|объясн|ход\s+решения|покаж|округл|запиш", re.I)


def scalar(text: str) -> Fraction | None:
    text = text.strip().replace("{,}", ".").replace("{.}", ".").replace(",", ".")
    if len(text) > 48:
        return None
    try:
        if fraction := LATEX_FRACTION.fullmatch(text):
            return Fraction(int(fraction[1]), int(fraction[2]))
        if repeating := REPEATING.fullmatch(text):
            scale = 10 ** len(repeating[2])
            base = Fraction(int(repeating[1] + repeating[2]), scale)
            return base + Fraction(int(repeating[3]), scale * (10 ** len(repeating[3]) - 1))
        return Fraction(text) if NUMBER.fullmatch(text) else None
    except ZeroDivisionError:
        return None


def writtenProbability(text: str) -> Fraction | None:
    text = text.strip().rstrip(".")
    if detached := OUTSIDE_FORMULA_CROSSOUT.fullmatch(text):
        text = detached[1]
    if "~" in text:
        return None
    match = PROBABILITY_START.match(text)
    if match is None:
        return None
    expression = text[match.end():].strip().removeprefix("$").removesuffix("$")
    first, *later = expression.split("=")
    value = scalar(first)
    if value is None:
        return None
    for part in later:
        other = scalar(part)
        if other != value:
            return None
    return value


def canGradeProbability(statement: str, requirements: tuple[str, ...]) -> bool:
    return (not requirements and statement.casefold().count("вероятност") == 1
            and bool(PROBABILITY_REQUEST.search(statement)) and not EXTRA_CRITERIA.search(statement))


def probabilityVerdict(answer: str | None, reference: ReferenceReady) -> Literal["correct", "incorrect"] | None:
    exact = reference.value.get("exact")
    if answer is None or reference.schemaId != "scalar.v1" or not isinstance(exact, str):
        return None
    expected = scalar(exact)
    written = scalar(answer)
    if expected is None or written is None or not 0 <= expected <= 1:
        return None
    return "correct" if written == expected else "incorrect"
