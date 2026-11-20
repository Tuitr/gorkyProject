"""Render typed mathematics while keeping positions in the source Word paragraph."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal
from xml.etree.ElementTree import Element

from grading.word import W

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
OBJECT = "\ufffc"  # One source character per inline equation, including empty equations.
REVIEW = "[формула требует уточнения]"
ReadStatus = Literal["read", "ambiguous"]

SUPER = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻"
DIGITS = "0123456789+-"
TO_SUPER = str.maketrans(DIGITS + "−", SUPER + "⁻")
FROM_SUPER = str.maketrans(SUPER, DIGITS)
SUPERSCRIPTS = re.compile(f"[{SUPER}]+")
# A typed math span may occur after a label or in the middle of a sentence.
MATH_SPAN = re.compile(rf"(?:[0-9{SUPER}{OBJECT}⃗√·±−=+*/^|();., \t-]|(?<![a-zA-Z])[a-zA-Z](?![a-zA-Z]))+")
RADICAL = re.compile(rf"√(\((?:[^()√]|\([^()√]*\))*\)|[0-9]+(?:[.,][0-9]+)?|[a-zA-Z])([{SUPER}]*)")


class AmbiguousFormula(ValueError):
    """The supported notation does not determine a faithful rendering."""


def typedLatex(value: str) -> str:
    def radical(match: re.Match[str]) -> str:
        base, power = match.groups()
        if base.startswith("(") and not power:
            base = base[1:-1]
        return rf"\sqrt{{{base}{power}}}"

    def exponent(match: re.Match[str]) -> str:
        digits = match.group().translate(FROM_SUPER)
        if not re.fullmatch(r"[+-]?[0-9]+", digits):
            raise AmbiguousFormula
        if len(digits) == 1 and digits.isdigit() and not match.string[match.end():match.end() + 1].isdigit():
            return "^" + digits
        return "^{" + digits + "}"

    value = RADICAL.sub(radical, value.replace("−", "-"))
    if "√" in value:
        raise AmbiguousFormula
    value = re.sub(r"\^([+-]?[0-9]{2,}|[+-][0-9])", r"^{\1}", value)
    value = SUPERSCRIPTS.sub(exponent, value)
    value = re.sub(r"([A-Za-z])⃗", r"\\vec \1", value)
    value = re.sub(r"· *", r"\\cdot ", value)
    value = re.sub(r"± *", r"\\pm ", value)
    return value


def typedMarkdown(text: str, equations: Iterator[str]) -> str:
    def render(match: re.Match[str]) -> str:
        part = match.group().strip()
        if OBJECT not in part and not any(ch.isalnum() for ch in part):
            return match.group()
        if len(part) == 1 and part.isalpha():
            return match.group()
        latex = re.sub(OBJECT, lambda _: next(equations), typedLatex(part))
        return match.group().replace(part, "$" + latex + "$", 1)

    return MATH_SPAN.sub(render, text)


def ommlLatex(node: Element) -> str:
    """Recognize structural operands explicitly. Unknown nodes invalidate the equation."""
    if node.tag == M + "r":
        runs = []
        for child in node:
            if child.tag == W + "rPr" and child.find(W + "vertAlign") is None:
                continue
            if child.tag == M + "rPr" and all(prop.tag == M + "sty" for prop in child):
                continue
            if child.tag != M + "t" or len(child):
                raise AmbiguousFormula
            runs.append(child.text or "")
        value = "".join(runs)
        if any(char in value for char in "\\{}$&#_%"):
            raise AmbiguousFormula
        return typedLatex(value)

    if node.tag in (M + "oMath", M + "e", M + "sup"):
        value = "".join(ommlLatex(child) for child in node)
        if not value.strip():
            raise AmbiguousFormula
        return value

    if node.tag == M + "sSup":
        parts = list(node)
        if parts and parts[0].tag == M + "sSupPr":
            properties = parts.pop(0)
            if any(prop.tag != M + "ctrlPr" for prop in properties):
                raise AmbiguousFormula
        if [child.tag for child in parts] != [M + "e", M + "sup"]:
            raise AmbiguousFormula
        base, power = (ommlLatex(child) for child in parts)
        if not (base.isdecimal() or len(base) == 1 or (len(parts[0]) == 1 and parts[0][0].tag == M + "d")):
            base = rf"\left({base}\right)"
        return base + "^{" + power + "}"

    if node.tag == M + "d":
        left, right, separator = "(", ")", "|"
        entries = list(node)
        if entries and entries[0].tag == M + "dPr":
            for prop in entries.pop(0):
                if prop.tag in (M + "ctrlPr", M + "grow"):
                    continue
                char = prop.get(M + "val")
                if char not in ("(", ")", "[", "]", "|", ";", ",", ""):
                    raise AmbiguousFormula
                if prop.tag == M + "begChr":
                    left = char
                elif prop.tag == M + "endChr":
                    right = char
                elif prop.tag == M + "sepChr":
                    separator = char
                else:
                    raise AmbiguousFormula
        if not entries or any(child.tag != M + "e" for child in entries):
            raise AmbiguousFormula
        return left + separator.join(ommlLatex(child) for child in entries) + right
    raise AmbiguousFormula


def wordFragments(node: Element, alignment: str | None = None) -> Iterator[tuple[str, str | None, Element | None]]:
    if node.tag == M + "oMath":
        yield OBJECT, OBJECT, node
    elif node.tag == W + "t":
        source = node.text or ""
        if alignment == "superscript" and all(ch in DIGITS + "−" for ch in source):
            yield source, source.translate(TO_SUPER), None
        elif alignment in ("superscript", "subscript"):
            yield source, None, None
        else:
            yield source, source, None
    elif node.tag == W + "tab":
        yield "\t", "\t", None
    elif node.tag in (W + "br", W + "cr"):
        yield "\n", "\n", None
    else:
        if node.tag == W + "r":
            prop = node.find(f"{W}rPr/{W}vertAlign")
            alignment = prop.get(W + "val") if prop is not None else None
        for child in node:
            yield from wordFragments(child, alignment)


@dataclass
class MathParagraph:
    source: str
    typed: str
    objects: dict[int, Element]
    unclear: list[tuple[int, int]]

    @classmethod
    def fromWord(cls, paragraph: Element) -> "MathParagraph":
        raw, typed = [], []
        objects = {}
        unclear = []
        offset = 0
        for source, formatted, equation in wordFragments(paragraph):
            if equation is not None:
                objects[offset] = equation
            if formatted is None and source:
                unclear.append((offset, offset + len(source)))
            raw.append(source)
            typed.append(source if formatted is None else formatted)
            offset += len(source)
        return cls("".join(raw), "".join(typed), objects, unclear)

    def render(self, start: int, end: int) -> tuple[str, ReadStatus]:
        if any(start < stop and pos < end for pos, stop in self.unclear):
            return REVIEW, "ambiguous"
        parts = []
        status: ReadStatus = "read"
        cursor = start
        equations: list[str] = []
        try:
            for match in re.finditer(OBJECT, self.typed[start:end]):
                pos = start + match.start()
                equation = self.objects.get(pos)
                try:
                    if equation is None:
                        raise AmbiguousFormula
                    equations.append(ommlLatex(equation))
                except AmbiguousFormula:
                    parts.append(typedMarkdown(self.typed[cursor:pos], iter(equations)))
                    parts.append(REVIEW)
                    equations.clear()
                    status = "ambiguous"
                    cursor = pos + 1
            parts.append(typedMarkdown(self.typed[cursor:end], iter(equations)))
        except AmbiguousFormula:
            return REVIEW, "ambiguous"
        return "".join(parts).strip(), status
