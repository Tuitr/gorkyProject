"""Task families of the mini dataset and the student solutions written for them.

Vectors are an example of the format (A6.1), not a solver: the generator builds every task around
an answer it already knows, and developer 3 checks the keys independently (C5.1).

Every line of a solution has two forms: the text printed in the student's DOCX and the Markdown that
developer 2's reader should extract from it (expectedSteps). The text uses the typographic minus (−)
and Unicode superscripts as a typed school work does; the Markdown uses LaTeX.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from math import isqrt
from typing import Literal

from contracts.v1.grading.taskresult import AnswerVerdict, ReasoningVerdict

from dataset.seeded import SeededDraws

Scenario = Literal["correct", "answerOnly", "skipped", "twoErrors", "consequence", "rootSign", "productSign"]
"""correct, answerOnly and skipped exist for every family; the others belong to one family each."""

VEC_A = "a⃗"
VEC_B = "b⃗"
MINUS = "−"


@dataclass(frozen=True)
class Line:
    text: str
    """As printed in the student's DOCX."""
    markdown: str
    """As developer 2's reader should extract it."""


@dataclass(frozen=True)
class Mark:
    """An expected issue on one line. causedBy is the index of its root mark in the same solution."""

    line: int
    category: str
    causedBy: int | None = None


@dataclass(frozen=True)
class Solution:
    lines: tuple[Line, ...]
    answer: AnswerVerdict
    reasoning: ReasoningVerdict
    marks: tuple[Mark, ...] = ()


def num(value: int) -> str:
    return f"{MINUS}{-value}" if value < 0 else str(value)


def term(value: int) -> str:
    """A number inside a sum or a product: a negative one goes in parentheses."""
    return f"({num(value)})" if value < 0 else str(value)


def termMd(value: int) -> str:
    return f"({value})" if value < 0 else str(value)


def pair(x: int, y: int) -> str:
    return f"({num(x)}; {num(y)})"


def pairMd(x: int, y: int) -> str:
    return f"({x}; {y})"


def square(value: int) -> str:
    return f"{term(value)}²"


def squareMd(value: int) -> str:
    return f"{termMd(value)}^2"


def isSquare(value: int) -> bool:
    return value >= 0 and isqrt(value) ** 2 == value


def root(value: int) -> str:
    """The final form of √value: an integer when the root is exact."""
    return str(isqrt(value)) if isSquare(value) else f"√{value}"


def rootMd(value: int) -> str:
    return str(isqrt(value)) if isSquare(value) else rf"\sqrt{{{value}}}"


def rootChain(value: int) -> str:
    """√25 = 5, or √24 when the root is not exact."""
    return f"√{value} = {isqrt(value)}" if isSquare(value) else f"√{value}"


def rootChainMd(value: int) -> str:
    return rf"\sqrt{{{value}}} = {isqrt(value)}" if isSquare(value) else rf"\sqrt{{{value}}}"


def signedSum(first: int, second: int) -> str:
    return f"{num(first)} + {second}" if second >= 0 else f"{num(first)} {MINUS} {-second}"


def signedSumMd(first: int, second: int) -> str:
    return f"{first} + {second}" if second >= 0 else f"{first} - {-second}"


def answerLine(text: str, markdown: str) -> Line:
    return Line(f"Ответ: {text}", f"Ответ: ${markdown}$")


class Task(ABC):
    @property
    @abstractmethod
    def sourceTaskId(self) -> str:
        """The original problem; splits are separated by it."""

    @property
    @abstractmethod
    def statement(self) -> str:
        """As printed in the variant, after the task number."""

    @property
    @abstractmethod
    def answer(self) -> int: ...

    @abstractmethod
    def correctLines(self) -> tuple[Line, ...]: ...

    def errorSolution(self, scenario: Scenario) -> Solution | None:
        return None

    def solution(self, scenario: Scenario) -> Solution:
        if scenario == "correct":
            return Solution(self.correctLines(), "correct", "valid")
        if scenario == "answerOnly":
            return Solution((answerLine(num(self.answer), str(self.answer)),), "correct", "not_shown")
        if scenario == "skipped":
            return Solution((), "skipped", "not_shown")
        solution = self.errorSolution(scenario)
        if solution is None:
            raise ValueError(f"{type(self).__name__} has no scenario {scenario}")
        return solution


@dataclass(frozen=True)
class VectorLength(Task):
    """|a| for a = (x; y) with an exact root."""

    x: int
    y: int

    def __post_init__(self) -> None:
        if not isSquare(self.total):
            raise ValueError(f"|({self.x}; {self.y})| is not an integer")

    @property
    def total(self) -> int:
        return self.x**2 + self.y**2

    @property
    def sourceTaskId(self) -> str:
        return f"vectorLength:{self.x}:{self.y}"

    @property
    def statement(self) -> str:
        return f"Найдите длину вектора {VEC_A} = {pair(self.x, self.y)}."

    @property
    def answer(self) -> int:
        return isqrt(self.total)

    def formulaLine(self) -> Line:
        return Line(
            f"|{VEC_A}| = √({square(self.x)} + {square(self.y)})",
            rf"$|\vec a| = \sqrt{{{squareMd(self.x)} + {squareMd(self.y)}}}$",
        )

    def valueLine(self, chain: str, chainMd: str) -> Line:
        x2, y2 = self.x**2, self.y**2
        return Line(f"= √({x2} + {y2}) = {chain}", rf"$= \sqrt{{{x2} + {y2}}} = {chainMd}$")

    def correctLines(self) -> tuple[Line, ...]:
        return (
            self.formulaLine(),
            self.valueLine(rootChain(self.total), rootChainMd(self.total)),
            answerLine(str(self.answer), str(self.answer)),
        )

    def errorSolution(self, scenario: Scenario) -> Solution | None:
        if scenario == "consequence":
            wrong = self.total - 1
            lines = (
                self.formulaLine(),
                self.valueLine(rootChain(wrong), rootChainMd(wrong)),
                answerLine(root(wrong), rootMd(wrong)),
            )
            return Solution(lines, "incorrect", "invalid", (Mark(1, "arithmetic"), Mark(2, "answer", causedBy=0)))
        if scenario == "rootSign":
            lines = (
                self.formulaLine(),
                self.valueLine(f"√{self.total} = ±{self.answer}", rf"\sqrt{{{self.total}}} = \pm {self.answer}"),
                answerLine(str(self.answer), str(self.answer)),
            )
            return Solution(lines, "correct", "invalid", (Mark(1, "sign"),))
        return None


@dataclass(frozen=True)
class VectorSumLength(Task):
    """|a + b| for a = (x1; y1), b = (x2; y2) with an exact root."""

    x1: int
    y1: int
    x2: int
    y2: int

    def __post_init__(self) -> None:
        if not isSquare(self.total(self.sumY)):
            raise ValueError("|a + b| is not an integer")

    @property
    def sumX(self) -> int:
        return self.x1 + self.x2

    @property
    def sumY(self) -> int:
        return self.y1 + self.y2

    def total(self, sumY: int) -> int:
        return self.sumX**2 + sumY**2

    @property
    def sourceTaskId(self) -> str:
        return f"vectorSumLength:{self.x1}:{self.y1}:{self.x2}:{self.y2}"

    @property
    def statement(self) -> str:
        return (
            f"Даны векторы {VEC_A} = {pair(self.x1, self.y1)} и {VEC_B} = {pair(self.x2, self.y2)}. "
            f"Найдите длину вектора {VEC_A} + {VEC_B}."
        )

    @property
    def answer(self) -> int:
        return isqrt(self.total(self.sumY))

    def sumLine(self, sumY: int) -> Line:
        x1, y1, x2, y2 = self.x1, self.y1, self.x2, self.y2
        return Line(
            f"{VEC_A} + {VEC_B} = ({num(x1)} + {term(x2)}; {num(y1)} + {term(y2)}) = {pair(self.sumX, sumY)}",
            rf"$\vec a + \vec b = ({x1} + {termMd(x2)}; {y1} + {termMd(y2)}) = {pairMd(self.sumX, sumY)}$",
        )

    def lengthLine(self, sumY: int, *, radicandInParentheses: bool) -> Line:
        x, total = self.sumX, self.total(sumY)
        if radicandInParentheses:
            head, headMd = f"√({square(x)} + {square(sumY)})", rf"\sqrt{{{squareMd(x)} + {squareMd(sumY)}}}"
        else:
            head, headMd = f"√{square(x)} + {square(sumY)}", rf"\sqrt{{{squareMd(x)}}} + {squareMd(sumY)}"
        return Line(
            f"|{VEC_A} + {VEC_B}| = {head} = √({x**2} + {sumY**2}) = {rootChain(total)}",
            rf"$|\vec a + \vec b| = {headMd} = \sqrt{{{x**2} + {sumY**2}}} = {rootChainMd(total)}$",
        )

    def correctLines(self) -> tuple[Line, ...]:
        return (
            self.sumLine(self.sumY),
            self.lengthLine(self.sumY, radicandInParentheses=True),
            answerLine(str(self.answer), str(self.answer)),
        )

    def errorSolution(self, scenario: Scenario) -> Solution | None:
        if scenario != "twoErrors":
            return None
        wrongY = self.sumY + 1
        total = self.total(wrongY)
        lines = (
            self.sumLine(wrongY),
            self.lengthLine(wrongY, radicandInParentheses=False),
            answerLine(root(total), rootMd(total)),
        )
        # Two independent roots: a wrong sum, which spoils the answer, and a radical without parentheses,
        # after which the root of the intended sum is still computed right.
        marks = (Mark(0, "arithmetic"), Mark(1, "notation"), Mark(2, "answer", causedBy=0))
        return Solution(lines, "incorrect", "invalid", marks)


@dataclass(frozen=True)
class DotProduct(Task):
    """a · b for a = (x1; y1), b = (x2; y2); y1 is negative so a sign can get lost."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def sourceTaskId(self) -> str:
        return f"dotProduct:{self.x1}:{self.y1}:{self.x2}:{self.y2}"

    @property
    def statement(self) -> str:
        return (
            f"Найдите скалярное произведение векторов {VEC_A} = {pair(self.x1, self.y1)} "
            f"и {VEC_B} = {pair(self.x2, self.y2)}."
        )

    @property
    def answer(self) -> int:
        return self.x1 * self.x2 + self.y1 * self.y2

    def productLine(self) -> Line:
        x1, y1, x2, y2 = self.x1, self.y1, self.x2, self.y2
        return Line(
            f"{VEC_A} · {VEC_B} = {num(x1)} · {term(x2)} + {term(y1)} · {term(y2)}",
            rf"$\vec a \cdot \vec b = {x1} \cdot {termMd(x2)} + {termMd(y1)} \cdot {termMd(y2)}$",
        )

    def valueLine(self, second: int) -> Line:
        first = self.x1 * self.x2
        total = first + second
        return Line(f"= {signedSum(first, second)} = {num(total)}", f"$= {signedSumMd(first, second)} = {total}$")

    def correctLines(self) -> tuple[Line, ...]:
        return (
            self.productLine(),
            self.valueLine(self.y1 * self.y2),
            answerLine(num(self.answer), str(self.answer)),
        )

    def errorSolution(self, scenario: Scenario) -> Solution | None:
        second = self.y1 * self.y2
        if scenario != "productSign" or second >= 0:
            return None
        wrong = self.x1 * self.x2 - second
        lines = (self.productLine(), self.valueLine(-second), answerLine(num(wrong), str(wrong)))
        return Solution(lines, "incorrect", "invalid", (Mark(1, "sign"), Mark(2, "answer", causedBy=0)))


PYTHAGOREAN = ((3, 4), (4, 3), (6, 8), (8, 6), (5, 12), (12, 5), (8, 15), (15, 8), (9, 12), (12, 9))
"""Legs with an integer hypotenuse, so every length in the set is an exact integer."""


def drawVectorLength(draws: SeededDraws, label: str) -> Task:
    x, y = draws.choice(f"{label}/legs", PYTHAGOREAN)
    return VectorLength(x * draws.choice(f"{label}/signX", (1, -1)), y * draws.choice(f"{label}/signY", (1, -1)))


def drawVectorSumLength(draws: SeededDraws, label: str) -> Task:
    sumX, sumY = draws.choice(f"{label}/sum", PYTHAGOREAN[:6])
    x1 = draws.choice(f"{label}/x1", [value for value in range(-4, sumX + 5) if value not in (0, sumX)])
    y1 = draws.choice(f"{label}/y1", [value for value in range(-4, sumY + 5) if value not in (0, sumY)])
    return VectorSumLength(x1, y1, sumX - x1, sumY - y1)


def drawDotProduct(draws: SeededDraws, label: str) -> Task:
    x1, x2 = draws.integer(f"{label}/x1", 1, 9), draws.integer(f"{label}/x2", 1, 9)
    y1, y2 = -draws.integer(f"{label}/y1", 1, 9), draws.integer(f"{label}/y2", 1, 9)
    if x1 * x2 + y1 * y2 == 0:
        y2 += 1  # the answer becomes y1, which is not zero
    return DotProduct(x1, y1, x2, y2)


Family = Literal["vectorLength", "vectorSumLength", "dotProduct"]

FAMILIES: dict[Family, Callable[[SeededDraws, str], Task]] = {
    "vectorLength": drawVectorLength,
    "vectorSumLength": drawVectorSumLength,
    "dotProduct": drawDotProduct,
}
