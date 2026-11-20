"""Physics sample: heat problems of grade 8 (Q = cmΔt, λm, Lm, qm, heat balance, efficiency).

`uv run python -m dataset.heat` writes docs/samples/fizika-teplota/:
- zadachi.json — the bank of 14 problems with hand-checked answers (number, SI unit, tolerance);
- teplovye-yavleniya-kr.docx — a five-problem test made from the bank;
- three student works with typical mistakes and teplovye-yavleniya-raboty.zip with all of them.
The files are written byte for byte the same on every run (docxwriter adds no time or random ids).
"""

import json
from dataclasses import dataclass
from pathlib import Path

from dataset.archive import storedZip
from dataset.docxwriter import Paragraph, buildDocx

SAMPLES = Path(__file__).resolve().parents[3] / "docs" / "samples" / "fizika-teplota"
ASSIGNMENT_FILE = "teplovye-yavleniya-kr.docx"
ARCHIVE_FILE = "teplovye-yavleniya-raboty.zip"
BANK_FILE = "zadachi.json"


@dataclass(frozen=True)
class Problem:
    problemId: str
    topic: str
    statement: str
    value: str
    unit: str
    relTolerance: float = 0.02


BANK = (
    Problem(
        "heating", "нагревание",
        "Какое количество теплоты необходимо для нагревания 2 кг воды от 20 °C до 30 °C? "
        "Удельная теплоёмкость воды 4200 Дж/(кг·°C).",
        "84000", "Дж",
    ),
    Problem(
        "cooling", "остывание",
        "Какое количество теплоты выделится при остывании алюминиевой детали массой 500 г от 120 °C до 20 °C? "
        "Удельная теплоёмкость алюминия 920 Дж/(кг·°C).",
        "46000", "Дж",
    ),
    Problem(
        "melting", "плавление",
        "Какое количество теплоты необходимо, чтобы расплавить 3 кг льда, взятого при температуре плавления? "
        "Удельная теплота плавления льда 3,4·10⁵ Дж/кг.",
        "1020000", "Дж",
    ),
    Problem(
        "boiling", "парообразование",
        "Какое количество теплоты необходимо, чтобы превратить в пар 200 г воды, взятой при температуре кипения? "
        "Удельная теплота парообразования воды 2,3·10⁶ Дж/кг.",
        "460000", "Дж",
    ),
    Problem(
        "burning", "сгорание топлива",
        "Какое количество теплоты выделится при полном сгорании 5 кг каменного угля? "
        "Удельная теплота сгорания каменного угля 2,7·10⁷ Дж/кг.",
        "135000000", "Дж",
    ),
    Problem(
        "fuelMass", "сгорание топлива",
        "Какую массу спирта нужно сжечь, чтобы получить 5,4 МДж теплоты? "
        "Удельная теплота сгорания спирта 2,7·10⁷ Дж/кг.",
        "0.2", "кг",
    ),
    Problem(
        "temperatureChange", "нагревание",
        "На сколько градусов нагреется 4 кг воды, если ей сообщить 168 кДж теплоты? "
        "Удельная теплоёмкость воды 4200 Дж/(кг·°C).",
        "10", "°C",
    ),
    Problem(
        "specificHeat", "нагревание",
        "Для нагревания 2 кг металла на 10 °C потребовалось 9200 Дж теплоты. "
        "Определите удельную теплоёмкость этого металла.",
        "460", "Дж/(кг·°C)",
    ),
    Problem(
        "iceToWater", "нагревание и плавление",
        "Какое количество теплоты необходимо, чтобы 2 кг льда, взятого при температуре −10 °C, превратить в воду "
        "при 0 °C? Удельная теплоёмкость льда 2100 Дж/(кг·°C), удельная теплота плавления льда 3,4·10⁵ Дж/кг.",
        "722000", "Дж",
    ),
    Problem(
        "mixing", "тепловой баланс",
        "Смешали 2 кг воды при температуре 20 °C и 3 кг воды при температуре 70 °C. Какая температура "
        "установится в сосуде? Теплообменом с сосудом и окружающей средой пренебречь.",
        "50", "°C",
    ),
    Problem(
        "waterToSteam", "нагревание и парообразование",
        "Какое количество теплоты необходимо, чтобы 1 кг воды, взятой при 20 °C, нагреть до кипения и полностью "
        "превратить в пар? Удельная теплоёмкость воды 4200 Дж/(кг·°C), удельная теплота парообразования воды "
        "2,3·10⁶ Дж/кг, температура кипения воды 100 °C.",
        "2636000", "Дж",
    ),
    Problem(
        "efficiency", "КПД",
        "На спиртовке нагрели 300 г воды от 20 °C до 80 °C, при этом сгорело 8 г спирта. Найдите КПД спиртовки. "
        "Удельная теплоёмкость воды 4200 Дж/(кг·°C), удельная теплота сгорания спирта 2,7·10⁷ Дж/кг.",
        "35", "%",
    ),
    Problem(
        "crystallization", "кристаллизация",
        "Какое количество теплоты выделится при кристаллизации 400 г свинца, взятого при температуре плавления? "
        "Удельная теплота плавления свинца 0,25·10⁵ Дж/кг.",
        "10000", "Дж",
    ),
    Problem(
        "waterMass", "нагревание",
        "Какую массу воды можно нагреть от 10 °C до 60 °C, сообщив ей 2,1 МДж теплоты? "
        "Удельная теплоёмкость воды 4200 Дж/(кг·°C).",
        "10", "кг",
    ),
)
BY_ID = {problem.problemId: problem for problem in BANK}
TEST = ("heating", "melting", "iceToWater", "mixing", "efficiency")
"""The problems of the sample test, in the order they are printed (task numbers 1–5)."""

WORKS: dict[str, tuple[str, tuple[tuple[str, ...], ...]]] = {
    # Every answer is right; the units vary on purpose: кДж, МДж and % must count as the reference.
    "ivanova-olga": ("Иванова Ольга", (
        ("Q = c·m·(t₂ − t₁)", "Q = 4200 · 2 · (30 − 20) = 84 000 Дж", "Ответ: 84 кДж"),
        ("Q = λ·m", "Q = 3,4·10⁵ · 3 = 10,2·10⁵ Дж", "Ответ: 1,02 МДж"),
        (
            "Q₁ = c·m·(0 − t₁) = 2100 · 2 · 10 = 42 000 Дж",
            "Q₂ = λ·m = 3,4·10⁵ · 2 = 680 000 Дж",
            "Q = Q₁ + Q₂ = 42 000 + 680 000 = 722 000 Дж",
            "Ответ: 722 кДж",
        ),
        (
            "c·m₁·(t − t₁) = c·m₂·(t₂ − t)",
            "2 · (t − 20) = 3 · (70 − t)",
            "5t = 250, t = 50 °C",
            "Ответ: 50 °C",
        ),
        (
            "Qполезн = c·m·(t₂ − t₁) = 4200 · 0,3 · 60 = 75 600 Дж",
            "Qзатр = q·m = 2,7·10⁷ · 0,008 = 216 000 Дж",
            "η = 75 600 / 216 000 = 0,35",
            "Ответ: 35 %",
        ),
    )),
    # Task 1: the final temperature instead of the change; task 3: the ice is not heated to 0 °C first;
    # task 5: 8 г of alcohol are not converted to kilograms.
    "smirnov-dmitriy": ("Смирнов Дмитрий", (
        ("Q = c·m·t", "Q = 4200 · 2 · 30 = 252 000 Дж", "Ответ: 252 000 Дж"),
        ("Q = λ·m = 3,4·10⁵ · 3 = 1 020 000 Дж", "Ответ: 1 020 000 Дж"),
        ("Q = λ·m = 3,4·10⁵ · 2 = 680 000 Дж", "Ответ: 680 000 Дж"),
        (
            "Q₁ = Q₂",
            "2 · (t − 20) = 3 · (70 − t)",
            "t = 50 °C",
            "Ответ: 50 °C",
        ),
        (
            "Qполезн = 4200 · 0,3 · 60 = 75 600 Дж",
            "Qзатр = 2,7·10⁷ · 8 = 216 000 000 Дж",
            "η = 75 600 / 216 000 000 = 0,00035",
            "Ответ: 0,035 %",
        ),
    )),
    # Task 1: an arithmetic slip; task 2: the answer without a unit; task 4: a rounded answer within the
    # tolerance; task 5: the efficiency upside down (Qзатр / Qполезн).
    "kuznetsova-anna": ("Кузнецова Анна", (
        ("Q = c·m·Δt", "Δt = 30 − 20 = 10 °C", "Q = 4200 · 2 · 10 = 8400 Дж", "Ответ: 8400 Дж"),
        ("Q = λ·m = 340 000 · 3 = 1 020 000", "Ответ: 1 020 000"),
        (
            "Q₁ = c·m·Δt = 2100 · 2 · 10 = 42 000 Дж",
            "Q₂ = λ·m = 340 000 · 2 = 680 000 Дж",
            "Q = 722 000 Дж",
            "Ответ: 722 000 Дж",
        ),
        (
            "Qотд = Qпол",
            "4200 · 3 · (70 − t) = 4200 · 2 · (t − 20)",
            "t ≈ 50,0 °C",
            "Ответ: ≈ 50 °C",
        ),
        (
            "Qполезн = 4200 · 0,3 · 60 = 75 600 Дж",
            "Qзатр = 2,7·10⁷ · 0,008 = 216 000 Дж",
            "η = 216 000 / 75 600 ≈ 2,86",
            "Ответ: 286 %",
        ),
    )),
}


def assignmentDocx() -> bytes:
    paragraphs = [
        Paragraph("Контрольная работа «Тепловые явления». Вариант 1", bold=True),
        Paragraph("Решите задачи. Запишите формулу, переведите величины в СИ, подставьте значения и запишите ответ."),
    ]
    paragraphs += [Paragraph(f"{number}. {BY_ID[problemId].statement}") for number, problemId in enumerate(TEST, 1)]
    return buildDocx("Тепловые явления", paragraphs)


def workDocx(name: str, solutions: tuple[tuple[str, ...], ...]) -> bytes:
    paragraphs = [Paragraph(f"Ученик: {name}")]
    for number, lines in enumerate(solutions, 1):
        paragraphs.append(Paragraph(f"Задание {number}."))
        paragraphs += [Paragraph(line) for line in lines]
    return buildDocx("Работа ученика", paragraphs)


def bankJson() -> bytes:
    entries = [
        {
            "problemId": problem.problemId,
            "topic": problem.topic,
            "statement": problem.statement,
            "answer": {"value": problem.value, "unit": problem.unit, "relTolerance": problem.relTolerance},
        }
        for problem in BANK
    ]
    return (json.dumps(entries, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sampleFiles() -> dict[str, bytes]:
    works = {f"{slug}.docx": workDocx(name, solutions) for slug, (name, solutions) in WORKS.items()}
    return {
        BANK_FILE: bankJson(),
        ASSIGNMENT_FILE: assignmentDocx(),
        **works,
        ARCHIVE_FILE: storedZip(sorted(works.items())),
    }


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    for name, body in sampleFiles().items():
        (SAMPLES / name).write_bytes(body)
        print(SAMPLES / name)


if __name__ == "__main__":
    main()
