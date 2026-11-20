"""Physical quantities in school notation: "84 кДж", "8,4·10⁴ Дж", "4200 Дж/(кг·°C)", "40 %".

Owner: developer 3. Used by the quantity.v1 reference value (reference.py) and by the solver cross-check
(references/solvers/answers.py). The student's answer is judged by the model (review.v3), not by this module.

A quantity is a number times a unit. The unit is reduced to SI: a factor and the powers of kg, m, s and degree.
Two quantities agree when their dimensions are equal and the numbers differ by at most relTolerance of the
reference: 84 кДж equals 84000 Дж, 0,5 кг equals 500 г, 40 % equals 0,4.

Temperature: °C and К are the same degree, without the 273 shift. That is exact for a temperature change
(Δt = 20 °C = 20 К) and for Дж/(кг·°C) = Дж/(кг·К); an absolute temperature written in kelvin does not equal
the same temperature in degrees Celsius here, so such an answer is not accepted as correct by the code.

Russian school notation: everything after one "/" is the denominator ("Дж/кг·°C" is Дж/(кг·°C)); a
multiplication sign between units may be ·, ⋅, *, a dot or nothing ("кВт·ч", "Дж/кг°C").
"""

import re
from dataclasses import dataclass
from fractions import Fraction

DIMENSIONS = ("kg", "m", "s", "deg")
Dimension = tuple[int, int, int, int]
NONE: Dimension = (0, 0, 0, 0)

# symbol -> (factor to SI, dimension). Prefixes are added below only for the units marked prefixable.
BASE_UNITS: dict[str, tuple[Fraction, Dimension]] = {
    "Дж": (Fraction(1), (1, 2, -2, 0)),
    "Вт": (Fraction(1), (1, 2, -3, 0)),
    "Н": (Fraction(1), (1, 1, -2, 0)),
    "Па": (Fraction(1), (1, -1, -2, 0)),
    "г": (Fraction(1, 1000), (1, 0, 0, 0)),
    "т": (Fraction(1000), (1, 0, 0, 0)),
    "м": (Fraction(1), (0, 1, 0, 0)),
    "л": (Fraction(1, 1000), (0, 3, 0, 0)),
    "с": (Fraction(1), (0, 0, 1, 0)),
    "мин": (Fraction(60), (0, 0, 1, 0)),
    "ч": (Fraction(3600), (0, 0, 1, 0)),
    "°C": (Fraction(1), (0, 0, 0, 1)),
    "К": (Fraction(1), (0, 0, 0, 1)),
    "%": (Fraction(1, 100), NONE),
}
PREFIXABLE = ("Дж", "Вт", "Н", "Па", "г", "м", "л", "с")
PREFIXES: dict[str, Fraction] = {
    "Г": Fraction(10**9),
    "М": Fraction(10**6),
    "к": Fraction(1000),
    "д": Fraction(1, 10),
    "с": Fraction(1, 100),
    "м": Fraction(1, 1000),
    "мк": Fraction(1, 10**6),
}
ALIASES = {
    # Latin and look-alike spellings of the same symbols.
    "J": "Дж", "kJ": "кДж", "MJ": "МДж", "W": "Вт", "kW": "кВт", "kg": "кг", "g": "г", "m": "м", "s": "с",
    "°С": "°C", "℃": "°C", "°": "°C", "K": "К", "град": "°C", "мин.": "мин", "сек": "с", "дж": "Дж", "кдж": "кДж",
}


def buildSymbols() -> dict[str, tuple[Fraction, Dimension]]:
    symbols = dict(BASE_UNITS)
    symbols["кг"] = (Fraction(1), (1, 0, 0, 0))
    for unit in PREFIXABLE:
        factor, dimension = BASE_UNITS[unit]
        for prefix, scale in PREFIXES.items():
            symbols.setdefault(prefix + unit, (factor * scale, dimension))
    for alias, target in ALIASES.items():
        symbols[alias] = symbols[target]
    return symbols


SYMBOLS = buildSymbols()
SYMBOL_PATTERN = "|".join(re.escape(symbol) for symbol in sorted(SYMBOLS, key=len, reverse=True))
SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
ATOM = re.compile(rf"({SYMBOL_PATTERN})(?:\^?(-?[1-3])|([²³]|⁻[¹²³]))?")
UNIT_GLUE = re.compile(r"[\s·⋅*.()\[\]]+")

MINUS = str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", "‒": "-"})
THOUSANDS = "[ \u00a0\u2009\u202f]"
NUMBER = re.compile(
    r"(?P<sign>[+-]?)\s*"
    rf"(?P<mantissa>\d{{1,3}}(?:{THOUSANDS}\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?|[.,]\d+)"
    r"(?:\s*[·⋅*×xх]\s*10\s*(?:\^|\*\*)?\s*(?P<power>[+-]?\d{1,3}|[⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3})"
    r"|\s*[eE](?P<epower>[+-]?\d{1,3}))?"
)


class UnitError(ValueError):
    """The text is not a unit this module knows."""


@dataclass(frozen=True)
class Unit:
    factor: Fraction
    dimension: Dimension


@dataclass(frozen=True)
class Quantity:
    """A value already reduced to SI: `si` in the base units of `dimension` (kg, m, s, degree)."""

    si: Fraction
    dimension: Dimension

    def agrees(self, reference: "Quantity", relTolerance: float) -> bool:
        if self.dimension != reference.dimension:
            return False
        if reference.si == 0:
            return self.si == 0
        return abs(float(self.si - reference.si)) <= relTolerance * abs(float(reference.si))


def parseUnit(text: str) -> Unit:
    """'' is a pure number. Raises UnitError for anything that is not a product or ratio of known units."""
    body = text.strip().translate(MINUS)
    factor, powers = Fraction(1), [0, 0, 0, 0]
    if not body:
        return Unit(factor, NONE)
    numerator, slash, denominator = body.partition("/")
    if "/" in denominator:
        raise UnitError(f"more than one '/' in unit {text!r}")
    for part, sign in ((numerator, 1), (denominator, -1)):
        rest = UNIT_GLUE.sub("", part)
        if slash and not rest:
            raise UnitError(f"empty side of '/' in unit {text!r}")
        position = 0
        while position < len(rest):
            match = ATOM.match(rest, position)
            if match is None:
                raise UnitError(f"unknown unit {rest[position:]!r} in {text!r}")
            symbol, plain, superscript = match.groups()
            exponent = int(plain) if plain else int(superscript.translate(SUPERSCRIPTS)) if superscript else 1
            unitFactor, dimension = SYMBOLS[symbol]
            factor *= unitFactor ** (exponent * sign)
            for index, power in enumerate(dimension):
                powers[index] += power * exponent * sign
            position = match.end()
    return Unit(factor, (powers[0], powers[1], powers[2], powers[3]))


def parseNumber(text: str) -> Fraction | None:
    match = NUMBER.fullmatch(text.strip().translate(MINUS))
    return None if match is None else numberOf(match)


def numberOf(match: re.Match[str]) -> Fraction:
    mantissa = re.sub(r"[    ]", "", match.group("mantissa")).replace(",", ".")
    value = Fraction(mantissa)
    power = match.group("power") or match.group("epower")
    if power:
        value *= Fraction(10) ** int(power.translate(SUPERSCRIPTS).replace("⁺", "+"))
    return -value if match.group("sign") == "-" else value


def parseQuantity(text: str) -> Quantity | None:
    """'84 кДж' -> 84000 J. None when the text is not exactly one number with an optional known unit."""
    body = text.strip().translate(MINUS).rstrip(".;")
    match = NUMBER.match(body)
    if match is None:
        return None
    try:
        unit = parseUnit(body[match.end():])
    except UnitError:
        return None
    return Quantity(numberOf(match) * unit.factor, unit.dimension)


def formatQuantity(si: Fraction, unit: str) -> str:
    """A short decimal for the teacher: at most 6 significant digits, decimal comma."""
    number = float(si / parseUnit(unit).factor)
    text = f"{number:.6g}"
    if "e" in text:
        mantissa, power = text.split("e")
        text = f"{mantissa}·10^{int(power)}"
    return f"{text.replace('.', ',')} {unit}".strip()
