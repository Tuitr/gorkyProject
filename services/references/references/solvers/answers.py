"""When two independent solvers gave the same answer.

scalar.v1 (math), {"exact": "..."}:

The author is asked to write the answer as parts joined by "; " (for example "-2; 1/2"). Two answers agree when
their parts are equal as a multiset: a part is compared as an exact number when it reads as one ("0.5" equals
"1/2", "−3" equals "-3"), otherwise as text without spaces and case. A float is never rounded here: "0.333"
and "1/3" differ, so a solver that only approximates does not slip through by luck.

quantity.v1 (physics), {"value": "...", "unit": "..."}: the number and the unit are reduced to SI
(contracts/v1/references/quantity.py) and agree within AGREEMENT_TOLERANCE. That is much tighter than the
tolerance a student gets (relTolerance, 2 %): two solvers of one task use the same data, so a larger gap means
they disagree about the physics (another constant, a missed stage), not about rounding.
"""

import re
from collections import Counter
from fractions import Fraction

from decimal import Decimal

from pydantic import JsonValue

from contracts.v1.references.quantity import Quantity, UnitError, parseNumber, parseUnit
from contracts.v1.references.reference import DEFAULT_REL_TOLERANCE

SCHEMA_BY_SUBJECT = {"math": "scalar.v1", "physics": "quantity.v1"}
AGREEMENT_TOLERANCE = 0.005
SIGNIFICANT_DIGITS = 6

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


def schemaOf(constraints: dict[str, JsonValue]) -> str:
    """The value schema a solver of this task must return: scalar.v1 unless the task names another subject."""
    subject = constraints.get("subject", "math")
    return SCHEMA_BY_SUBJECT.get(subject, "scalar.v1") if isinstance(subject, str) else "scalar.v1"


def decimalText(number: float) -> str:
    """A plain decimal with at most SIGNIFICANT_DIGITS digits: 83999.99999 -> "84000", 2.7e7 -> "27000000"."""
    text = format(Decimal(f"{number:.{SIGNIFICANT_DIGITS}g}").normalize(), "f")
    return "0" if text in ("-0", "") else text


def quantityOf(value: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
    """A usable quantity.v1 value from what solve(task) returned, or None.

    The solver returns {"value": <number or decimal text>, "unit": "<unit>"}; the value is rewritten as a plain
    decimal with six significant digits, the unit is kept as written once it parses.
    """
    if set(value) != {"value", "unit"}:
        return None
    raw, unit = value["value"], value["unit"]
    if not isinstance(unit, str) or len(unit) > 40:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    number = parseNumber(raw) if isinstance(raw, str) else Fraction(raw)
    if number is None:
        return None
    try:
        parseUnit(unit)
    except UnitError:
        return None
    return {"value": decimalText(float(number)), "unit": unit.strip(), "relTolerance": DEFAULT_REL_TOLERANCE}


def siOf(value: dict[str, JsonValue]) -> Quantity:
    raw, unit = value["value"], value["unit"]
    assert isinstance(raw, str) and isinstance(unit, str)
    number = parseNumber(raw)
    assert number is not None
    parsed = parseUnit(unit)
    return Quantity(number * parsed.factor, parsed.dimension)


def answerOf(schemaId: str, value: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
    """The reference value to publish from a solver's return value, or None when it is not usable."""
    if schemaId == "quantity.v1":
        return quantityOf(value)
    exact = exactOf(value)
    return None if exact is None else {"exact": exact}


def answerHint(schemaId: str) -> str:
    if schemaId == "quantity.v1":
        return 'solve(task) must return {"value": "<number>", "unit": "<SI unit>"}, for example {"value": "84000", "unit": "Дж"}'
    return 'solve(task) must return {"exact": "<answer>"} with a non-empty answer text'


def valuesAgree(schemaId: str, first: dict[str, JsonValue], second: dict[str, JsonValue]) -> bool:
    if schemaId == "quantity.v1":
        return siOf(first).agrees(siOf(second), AGREEMENT_TOLERANCE)
    exactFirst, exactSecond = first.get("exact"), second.get("exact")
    assert isinstance(exactFirst, str) and isinstance(exactSecond, str)
    return answersAgree(exactFirst, exactSecond)


def answerLabel(schemaId: str, value: dict[str, JsonValue]) -> str:
    """Short text of a value for logs and the author's own feedback."""
    if schemaId == "quantity.v1":
        return f"{value['value']} {value['unit']}".strip()
    return str(value.get("exact"))
