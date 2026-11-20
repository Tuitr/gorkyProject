"""quantity.v1: physical answers with a unit, compared after SI conversion (physics, 2026-11-20)."""

from fractions import Fraction

import pytest
from pydantic import JsonValue, ValidationError

from contracts.v1.references.quantity import Quantity, parseQuantity, parseUnit
from contracts.v1.references.reference import QuantityValueV1, ReferenceReady, answerText

JOULE = (1, 2, -2, 0)


@pytest.mark.parametrize(
    "text",
    ["84000 Дж", "84 000 Дж", "84 кДж", "0,084 МДж", "8,4·10⁴ Дж", "8.4*10^4 J", "84 kJ", "8,4e4 Дж", "84 кдж"],
)
def testEquivalentEnergiesAreOneQuantity(text: str) -> None:
    assert parseQuantity(text) == Quantity(Fraction(84000), JOULE)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("1000 Н", "1 кН"),
        ("500 г", "0,5 кг"),
        ("2,3 МДж/кг", "2 300 000 Дж/кг"),
        ("4200 Дж/(кг·°C)", "4,2 кДж/(кг·К)"),
        ("4200 Дж/кг°С", "4200 Дж/(кг·°C)"),
        ("40 %", "0,4"),
        ("3 кВт·ч", "10,8 МДж"),
        ("2 л", "0,002 м³"),
        ("5 мин", "300 с"),
    ],
)
def testUnitsConvert(first: str, second: str) -> None:
    left, right = parseQuantity(first), parseQuantity(second)
    assert left is not None and right is not None
    assert left.agrees(right, 1e-9)


def testToleranceIsRelativeToTheReference() -> None:
    reference = parseQuantity("84000 Дж")
    assert reference is not None
    for text, expected in (("83 кДж", True), ("85,6 кДж", True), ("82 кДж", False), ("8400 Дж", False)):
        quantity = parseQuantity(text)
        assert quantity is not None and quantity.agrees(reference, 0.02) is expected, text


def testAnotherDimensionNeverAgrees() -> None:
    joules, watts, bare = parseQuantity("84000 Дж"), parseQuantity("84000 Вт"), parseQuantity("84000")
    assert joules is not None and watts is not None and bare is not None
    assert not watts.agrees(joules, 0.02) and not bare.agrees(joules, 0.02)


@pytest.mark.parametrize("text", ["84 попугая", "Дж", "", "84 Дж/кг/°C", "= cmΔt"])
def testNotAQuantity(text: str) -> None:
    assert parseQuantity(text) is None


def testQuantityValueIsCheckedAndShownToTheTeacher() -> None:
    value: dict[str, JsonValue] = {"value": "84000", "unit": "Дж", "relTolerance": 0.02}
    ready = ReferenceReady(
        referenceId="r1", state="ready", source="generated", schemaId="quantity.v1", value=value,
        solverVersion="task0123456789abcdef.v1",
    )
    assert ready.value == value
    assert answerText("quantity.v1", value) == "84000 Дж ± 2 %"
    assert answerText("quantity.v1", {"value": "0.35", "unit": ""}) == "0,35 ± 2 %"
    assert answerText("scalar.v1", {"exact": "-3/4"}) == "-3/4"
    for broken in (
        {"value": "много", "unit": "Дж"},
        {"value": "1", "unit": "попугай"},
        {"value": "1", "unit": "Дж", "relTolerance": 0.5},
        {"value": "1", "unit": "Дж", "display": "1 Дж"},
    ):
        with pytest.raises(ValidationError):
            QuantityValueV1.model_validate(broken)


def testUnitParserKnowsSchoolNotation() -> None:
    assert parseUnit("Дж/(кг·°C)").dimension == (0, 2, -2, -1)
    assert parseUnit("кг/м³").dimension == (1, -3, 0, 0)
    assert parseUnit("").factor == 1
