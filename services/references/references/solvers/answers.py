"""When two independent solvers gave the same answer (scalar.v1, {"exact": "..."}).

The author is asked to write the answer as parts joined by "; " (for example "-2; 1/2"). Two answers agree when
their parts are equal as a multiset: a part is compared as an exact number when it reads as one ("0.5" equals
"1/2", "−3" equals "-3"), otherwise as text without spaces and case. A float is never rounded here: "0.333"
and "1/3" differ, so a solver that only approximates does not slip through by luck.
"""

import re
from collections import Counter
from fractions import Fraction

from pydantic import JsonValue

MINUS = str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", "‒": "-"})
DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")
SPACES = re.compile(r"\s+")
NUMBER = re.compile(r"[+-]?(\d+(\.\d+)?|\.\d+)(/[+-]?\d+(\.\d+)?)?")
MAX_ANSWER_CHARS = 500


def answerParts(exact: str) -> list[str]:
    return [part for part in (piece.strip() for piece in exact.split(";")) if part]


def normalPart(part: str) -> str:
    text = SPACES.sub("", DECIMAL_COMMA.sub(".", part.translate(MINUS))).lower()
    if NUMBER.fullmatch(text):
        numerator, _, denominator = text.partition("/")
        try:
            value = Fraction(numerator) / Fraction(denominator) if denominator else Fraction(numerator)
        except ZeroDivisionError:
            return text
        return f"{value.numerator}/{value.denominator}"
    return text


def answerKey(exact: str) -> Counter[str]:
    return Counter(normalPart(part) for part in answerParts(exact))


def exactOf(value: dict[str, JsonValue]) -> str | None:
    """The scalar.v1 answer text, or None when the value is not a usable answer."""
    exact = value.get("exact")
    if not isinstance(exact, str) or not exact.strip() or len(exact) > MAX_ANSWER_CHARS or set(value) != {"exact"}:
        return None
    return exact.strip()


def answersAgree(first: str, second: str) -> bool:
    return answerKey(first) == answerKey(second)
