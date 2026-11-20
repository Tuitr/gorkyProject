"""Generates the annotated mini dataset from a seed (A6.2).

    uv run python -m dataset.generate --seed 20261120 --out <empty directory>

The directory receives:
- assignments/<variant>.docx: the variant the teacher prints;
- submissions/<variant>/<student>.docx: one synthetic work per student;
- bundles/<variant>.zip: the works of one variant, each entry byte for byte its separate file;
- manifest.json: the answer key. It never goes into a DOCX or a ZIP.

The same seed gives the same bytes on every run and OS: values come from SeededDraws, Word files and
ZIPs from storedZip, and variants, students and tasks keep the order of PLAN. Names are synthetic.

What the set covers: a correct work, a skipped task (the statement is copied, nothing else), an
answer without steps, two independent errors with one consequence, and the E2E work whose only
issue (√25 = ±5 with the right answer) the teacher removes by hand in the E2E scenario (E1.1).
"""

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from contracts.v1.dataset.manifest import (
    AssignmentRef,
    BundleEntry,
    BundleRef,
    DatasetCase,
    DatasetManifest,
    ExpectedIssue,
    ExpectedReference,
    ExpectedStep,
    ExpectedVerdict,
    Split,
    SubmissionRef,
)

from dataset.archive import storedZip
from dataset.docxwriter import Paragraph, buildDocx
from dataset.seeded import SeededDraws
from dataset.tasks import FAMILIES, Family, Scenario, Solution, Task

DATASET_VERSION = "mini.v1"
DEFAULT_SEED = 20261120
TITLE = "Самостоятельная работа «Векторы на плоскости»"
INSTRUCTION = "Решите задачи. Запишите решение и ответ."
MANIFEST_NAME = "manifest.json"

NAMES = (
    ("Тестов Пётр", "testov-petr"),
    ("Примерова Анна", "primerova-anna"),
    ("Образцов Илья", "obraztsov-ilya"),
    ("Пробная Мария", "probnaya-mariya"),
    ("Учебнов Глеб", "uchebnov-gleb"),
    ("Задачина Вера", "zadachina-vera"),
    ("Черновиков Олег", "chernovikov-oleg"),
    ("Шаблонова Ева", "shablonova-eva"),
)
"""Synthetic students: the printed name and the file name."""


@dataclass(frozen=True)
class StudentPlan:
    scenarios: tuple[Scenario, ...]
    """One per task of the variant, in task order."""
    copiesStatements: bool
    """Writes the statement after "Задание N." A skipped task always has it: B2.4 tells a skip from a copied
    statement with no solution."""


@dataclass(frozen=True)
class VariantPlan:
    variantId: str
    number: int
    split: Split
    families: tuple[Family, ...]
    students: tuple[StudentPlan, ...]


PLAN = (
    VariantPlan(
        "demo1",
        1,
        "demo",
        ("vectorLength", "vectorSumLength", "dotProduct"),
        (
            StudentPlan(("correct", "correct", "correct"), copiesStatements=True),
            StudentPlan(("answerOnly", "twoErrors", "skipped"), copiesStatements=False),
        ),
    ),
    VariantPlan(
        "e2e1",
        2,
        "e2e",
        ("vectorLength",),
        (
            StudentPlan(("correct",), copiesStatements=True),
            StudentPlan(("rootSign",), copiesStatements=False),
        ),
    ),
    VariantPlan(
        "closed1",
        3,
        "closed",
        ("vectorLength", "dotProduct"),
        (
            StudentPlan(("consequence", "correct"), copiesStatements=True),
            StudentPlan(("correct", "productSign"), copiesStatements=False),
        ),
    ),
)


@dataclass(frozen=True)
class GeneratedDataset:
    manifest: DatasetManifest
    files: Mapping[str, bytes]
    """Every DOCX and ZIP by its path relative to manifest.json."""

    def manifestBytes(self) -> bytes:
        document = self.manifest.model_dump(mode="json")
        return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def drawTask(draws: SeededDraws, label: str, family: Family, used: set[str]) -> Task:
    """A task whose source problem appears nowhere else in the set, so splits never share one."""
    for attempt in range(100):
        task = FAMILIES[family](draws, f"{label}/attempt{attempt}")
        if task.sourceTaskId not in used:
            used.add(task.sourceTaskId)
            return task
    raise RuntimeError(f"{label}: no unused {family} task")


def variantTitle(variant: VariantPlan) -> str:
    return f"{TITLE}. Вариант {variant.number}"


def assignmentDocx(variant: VariantPlan, tasks: Sequence[Task]) -> bytes:
    paragraphs = [Paragraph(variantTitle(variant), bold=True), Paragraph(INSTRUCTION)]
    paragraphs += [Paragraph(f"{number}. {task.statement}") for number, task in enumerate(tasks, 1)]
    return buildDocx(variantTitle(variant), paragraphs)


def submissionDocx(
    variant: VariantPlan, studentName: str, student: StudentPlan, tasks: Sequence[Task], solutions: Sequence[Solution]
) -> bytes:
    paragraphs = [Paragraph(variantTitle(variant), bold=True), Paragraph(f"Ученик: {studentName}")]
    for number, (task, scenario, solution) in enumerate(zip(tasks, student.scenarios, solutions, strict=True), 1):
        copies = student.copiesStatements or scenario == "skipped"
        header = f"Задание {number}. {task.statement}" if copies else f"Задание {number}."
        paragraphs.append(Paragraph(header, bold=True))
        paragraphs += [Paragraph(line.text) for line in solution.lines]
    return buildDocx(variantTitle(variant), paragraphs)


def buildCase(
    caseId: str,
    split: Split,
    assignmentRef: AssignmentRef,
    submissionRef: SubmissionRef,
    task: Task,
    solution: Solution,
) -> DatasetCase:
    return DatasetCase(
        caseId=caseId,
        split=split,
        assignmentRef=assignmentRef,
        submissionRef=submissionRef,
        expectedReference=ExpectedReference(schemaId="scalar.v1", value={"exact": str(task.answer)}),
        expectedSteps=[
            ExpectedStep(stepId=f"s{index}", markdown=line.markdown) for index, line in enumerate(solution.lines, 1)
        ],
        expectedVerdict=ExpectedVerdict(
            answer=solution.answer, reasoning=solution.reasoning, method="optional", needsReview=False
        ),
        expectedIssues=[
            ExpectedIssue(
                issueId=f"i{index}",
                stepId=f"s{mark.line + 1}",
                category=mark.category,
                causedByIssueId=None if mark.causedBy is None else f"i{mark.causedBy + 1}",
            )
            for index, mark in enumerate(solution.marks, 1)
        ],
    )


def generateDataset(seed: int = DEFAULT_SEED) -> GeneratedDataset:
    draws = SeededDraws(seed)
    names = iter(draws.shuffled("names", NAMES))
    usedSources: set[str] = set()
    files: dict[str, bytes] = {}
    cases: list[DatasetCase] = []
    bundles: list[BundleRef] = []
    for variant in PLAN:
        tasks = [
            drawTask(draws, f"{variant.variantId}/task{number}", family, usedSources)
            for number, family in enumerate(variant.families, 1)
        ]
        assignmentFile = f"assignments/{variant.variantId}.docx"
        files[assignmentFile] = assignmentDocx(variant, tasks)
        entries: list[BundleEntry] = []
        for studentNumber, student in enumerate(variant.students, 1):
            studentName, slug = next(names)
            solutions = [task.solution(scenario) for task, scenario in zip(tasks, student.scenarios, strict=True)]
            submissionFile = f"submissions/{variant.variantId}/{slug}.docx"
            files[submissionFile] = submissionDocx(variant, studentName, student, tasks, solutions)
            submissionRef = SubmissionRef(
                file=submissionFile, sha256=sha256(files[submissionFile]), studentName=studentName
            )
            entries.append(BundleEntry(name=f"{slug}.docx", file=submissionFile))
            for number, (task, solution) in enumerate(zip(tasks, solutions, strict=True), 1):
                assignmentRef = AssignmentRef(
                    file=assignmentFile,
                    taskNumber=str(number),
                    statement=task.statement,
                    constraints={},
                    sourceTaskId=task.sourceTaskId,
                )
                caseId = f"{variant.variantId}Student{studentNumber}Task{number}"
                cases.append(buildCase(caseId, variant.split, assignmentRef, submissionRef, task, solution))
        bundleFile = f"bundles/{variant.variantId}.zip"
        files[bundleFile] = storedZip([(entry.name, files[entry.file]) for entry in entries])
        bundles.append(
            BundleRef(file=bundleFile, sha256=sha256(files[bundleFile]), assignmentFile=assignmentFile, entries=entries)
        )
    manifest = DatasetManifest(
        schemaVersion="datasetManifest.v1", datasetVersion=DATASET_VERSION, seed=seed, cases=cases, bundles=bundles
    )
    return GeneratedDataset(manifest=manifest, files=files)


def writeDataset(dataset: GeneratedDataset, outDir: Path) -> list[str]:
    """Writes into an empty directory, so files of another seed cannot stay next to the new ones."""
    if outDir.exists() and any(outDir.iterdir()):
        raise FileExistsError(f"{outDir} is not empty: generate into an empty directory")
    written = sorted(dataset.files)
    for relative in written:
        target = outDir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dataset.files[relative])
    outDir.mkdir(parents=True, exist_ok=True)
    (outDir / MANIFEST_NAME).write_bytes(dataset.manifestBytes())
    return [*written, MANIFEST_NAME]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dataset.generate", description="Generate the mini dataset.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"default {DEFAULT_SEED}")
    parser.add_argument("--out", type=Path, required=True, help="an empty or missing directory")
    args = parser.parse_args(argv)
    seed: int = args.seed
    outDir: Path = args.out
    if seed < 0:
        parser.error("--seed must not be negative")
    try:
        written = writeDataset(generateDataset(seed), outDir)
    except FileExistsError as error:
        parser.error(str(error))
    print(f"{DATASET_VERSION}, seed {seed}: {len(written)} files in {outDir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
