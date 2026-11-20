"""A6.2: the mini dataset generator gives the same DOCX, ZIP and manifest for the same seed.

python-docx reads the files as an independent Word reader; the generator itself writes the XML by hand.
"""

import hashlib
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import docx
import pytest

from contracts.v1.dataset.manifest import DatasetCase, DatasetManifest
from dataset.archive import FIXED_TIME, UNIX, storedZip
from dataset.generate import DEFAULT_SEED, GeneratedDataset, generateDataset, main, writeDataset
from dataset.tasks import MINUS

RELEASE_DIR = Path(__file__).resolve().parents[2] / "tools" / "dataset" / "releases" / "mini.v1"

KEY_WORDS = ("caseId", "expected", "sourceTaskId", "schemaId", "split", "manifest")
"""Manifest keys that must never appear inside a DOCX or a ZIP."""


@pytest.fixture(scope="module")
def dataset() -> GeneratedDataset:
    return generateDataset(DEFAULT_SEED)


def paragraphsOf(data: bytes) -> list[str]:
    return [paragraph.text for paragraph in docx.Document(io.BytesIO(data)).paragraphs]


def contentsOf(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def tasksOf(data: bytes) -> dict[str, tuple[str, list[str]]]:
    """Task number -> (the header after "Задание N.", the solution lines under it)."""
    tasks: dict[str, tuple[str, list[str]]] = {}
    current: list[str] | None = None
    for text in paragraphsOf(data):
        if text.startswith("Задание "):
            number, _, header = text.removeprefix("Задание ").partition(".")
            current = []
            tasks[number] = (header.strip(), current)
        elif current is not None:
            current.append(text)
    return tasks


def testSameSeedGivesTheSameDataset(dataset: GeneratedDataset) -> None:
    again = generateDataset(DEFAULT_SEED)

    assert [case.caseId for case in again.manifest.cases] == [case.caseId for case in dataset.manifest.cases]
    assert [case.assignmentRef.statement for case in again.manifest.cases] == [
        case.assignmentRef.statement for case in dataset.manifest.cases
    ]
    assert [case.expectedSteps for case in again.manifest.cases] == [
        case.expectedSteps for case in dataset.manifest.cases
    ]
    assert [case.expectedReference for case in again.manifest.cases] == [
        case.expectedReference for case in dataset.manifest.cases
    ]
    assert again.manifestBytes() == dataset.manifestBytes()
    assert again.files == dataset.files


def testTwoRunsWriteByteIdenticalDirectories(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    assert main(["--seed", "7", "--out", str(first)]) == 0
    assert main(["--seed", "7", "--out", str(second)]) == 0

    assert contentsOf(first) == contentsOf(second)
    written = DatasetManifest.model_validate_json(contentsOf(first)["manifest.json"])
    assert written == generateDataset(7).manifest


def testSeparateProcessesWriteTheSameBytes(tmp_path: Path) -> None:
    """Different hash seeds change the order of sets and dicts of strings; the output must not depend on it."""
    roots = []
    for hashSeed in ("1", "2"):
        root = tmp_path / f"hash{hashSeed}"
        environment = os.environ | {"PYTHONHASHSEED": hashSeed}
        command = [sys.executable, "-m", "dataset.generate", "--out", str(root)]
        subprocess.run(command, check=True, env=environment, capture_output=True)
        roots.append(root)

    assert contentsOf(roots[0]) == contentsOf(roots[1])
    assert (roots[0] / "manifest.json").read_bytes() == generateDataset(DEFAULT_SEED).manifestBytes()


def testOtherSeedKeepsTheCasesAndChangesTheNumbers(dataset: GeneratedDataset) -> None:
    other = generateDataset(DEFAULT_SEED + 1)

    assert [case.caseId for case in other.manifest.cases] == [case.caseId for case in dataset.manifest.cases]
    assert [case.assignmentRef.statement for case in other.manifest.cases] != [
        case.assignmentRef.statement for case in dataset.manifest.cases
    ]


def testEverySubmissionOpensInWordAndMatchesItsHash(dataset: GeneratedDataset) -> None:
    for case in dataset.manifest.cases:
        data = dataset.files[case.submissionRef.file]

        assert hashlib.sha256(data).hexdigest() == case.submissionRef.sha256
        assert f"Ученик: {case.submissionRef.studentName}" in paragraphsOf(data)


def testZipEntriesAreTheSeparateFilesByteForByte(dataset: GeneratedDataset) -> None:
    for bundle in dataset.manifest.bundles:
        data = dataset.files[bundle.file]
        assert hashlib.sha256(data).hexdigest() == bundle.sha256

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert archive.namelist() == [entry.name for entry in bundle.entries]
            for entry in bundle.entries:
                assert archive.read(entry.name) == dataset.files[entry.file]


def testEverySubmissionIsInTheZipOfItsVariant(dataset: GeneratedDataset) -> None:
    bundled = {(entry.file, bundle.assignmentFile) for bundle in dataset.manifest.bundles for entry in bundle.entries}

    assert {(case.submissionRef.file, case.assignmentRef.file) for case in dataset.manifest.cases} == bundled


def testArchivesCarryNoClockTimeNoOsAndNoCompression(dataset: GeneratedDataset) -> None:
    """The current time, the OS that wrote an entry and the local zlib would change the bytes."""
    for name, data in dataset.files.items():
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                actual = (name, info.date_time, info.create_system, info.compress_type)
                assert actual == (name, FIXED_TIME, UNIX, zipfile.ZIP_STORED)


def testStoredZipKeepsTheGivenOrderAndRejectsRepeatedNames() -> None:
    data = storedZip([("b.docx", b"2"), ("a.docx", b"1")])

    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == ["b.docx", "a.docx"]
    assert storedZip([("b.docx", b"2"), ("a.docx", b"1")]) == data
    with pytest.raises(ValueError, match="repeat"):
        storedZip([("a.docx", b"1"), ("a.docx", b"2")])


def testWordFilesAndZipsCarryNoKey(dataset: GeneratedDataset) -> None:
    for name, data in dataset.files.items():
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert not any(member.endswith(".json") for member in archive.namelist()), name
            for member in archive.namelist():
                content = archive.read(member)
                assert not [word for word in KEY_WORDS if word.encode() in content], (name, member)


def testSolutionLinesAreTheExpectedSteps(dataset: GeneratedDataset) -> None:
    """Each line under "Задание N." is one expected step; a skipped task has its statement and nothing else."""
    for case in dataset.manifest.cases:
        header, lines = tasksOf(dataset.files[case.submissionRef.file])[case.assignmentRef.taskNumber]

        assert len(lines) == len(case.expectedSteps), case.caseId
        assert header in ("", case.assignmentRef.statement), case.caseId
        if case.expectedVerdict.answer == "skipped":
            assert (header, lines) == (case.assignmentRef.statement, []), case.caseId


def testVariantListsEveryStatementUnderItsNumber(dataset: GeneratedDataset) -> None:
    for case in dataset.manifest.cases:
        paragraphs = paragraphsOf(dataset.files[case.assignmentRef.file])

        assert f"{case.assignmentRef.taskNumber}. {case.assignmentRef.statement}" in paragraphs


def testAnswerKeyIsTheLastLineOfACorrectWork(dataset: GeneratedDataset) -> None:
    for case in dataset.manifest.cases:
        if case.expectedVerdict.answer != "correct":
            continue
        assert case.expectedReference is not None
        _, lines = tasksOf(dataset.files[case.submissionRef.file])[case.assignmentRef.taskNumber]
        exact = str(case.expectedReference.value["exact"])

        assert lines[-1] == f"Ответ: {exact.replace('-', MINUS)}", case.caseId


def isCorrect(case: DatasetCase) -> bool:
    verdict = case.expectedVerdict
    return (verdict.answer, verdict.reasoning, case.expectedIssues) == ("correct", "valid", [])


def hasTwoIndependentErrorsAndOneConsequence(case: DatasetCase) -> bool:
    roots = [issue for issue in case.expectedIssues if issue.causedByIssueId is None]
    consequences = [issue for issue in case.expectedIssues if issue.causedByIssueId is not None]
    return len(roots) == 2 and len(consequences) == 1 and consequences[0].causedByIssueId in {i.issueId for i in roots}


def testSetCoversTheRequiredSituations(dataset: GeneratedDataset) -> None:
    cases = dataset.manifest.cases
    situations = {
        "correct work": [case for case in cases if case.split == "demo" and isCorrect(case)],
        "skip": [case for case in cases if case.expectedVerdict.answer == "skipped"],
        "answer without steps": [
            case
            for case in cases
            if (case.expectedVerdict.answer, case.expectedVerdict.reasoning) == ("correct", "not_shown")
            and len(case.expectedSteps) == 1
        ],
        "two independent errors, one consequence": [
            case for case in cases if hasTwoIndependentErrorsAndOneConsequence(case)
        ],
        "issue the teacher removes (E2E)": [
            case
            for case in cases
            if case.split == "e2e" and case.expectedVerdict.answer == "correct" and len(case.expectedIssues) == 1
        ],
    }

    assert {name: bool(found) for name, found in situations.items()} == dict.fromkeys(situations, True)


def testE2eKitIsOneTaskOfTwoStudents(dataset: GeneratedDataset) -> None:
    """E1.1: one task, two students with correct answers; only the second has an issue."""
    e2e = [case for case in dataset.manifest.cases if case.split == "e2e"]

    assert len({(case.assignmentRef.file, case.assignmentRef.taskNumber) for case in e2e}) == 1
    assert len({case.submissionRef.file for case in e2e}) == 2
    assert [case.expectedVerdict.answer for case in e2e] == ["correct", "correct"]
    assert [len(case.expectedIssues) for case in e2e] == [0, 1]


def testVariantsNeverShareASourceTask(dataset: GeneratedDataset) -> None:
    variantsOf: dict[str, set[str]] = {}
    for case in dataset.manifest.cases:
        variantsOf.setdefault(case.assignmentRef.sourceTaskId, set()).add(case.assignmentRef.file)

    assert [source for source, variants in variantsOf.items() if len(variants) > 1] == []


def testOpenReleaseIsReproducibleAndContainsOnlyReferencedOpenFiles(tmp_path: Path) -> None:
    release = contentsOf(RELEASE_DIR)
    manifest = DatasetManifest.model_validate_json(release["manifest.json"])

    assert main(["--open-only", "--seed", str(manifest.seed), "--out", str(tmp_path)]) == 0
    assert contentsOf(tmp_path) == release
    assert {case.split for case in manifest.cases} == {"demo", "e2e"}
    referenced = {case.assignmentRef.file for case in manifest.cases}
    referenced.update(case.submissionRef.file for case in manifest.cases)
    referenced.update(bundle.file for bundle in manifest.bundles)
    assert set(release) == referenced | {"manifest.json"}


def testSeparateClosedSeedPreservesOpenCasesAndFiles() -> None:
    openDataset = generateDataset(DEFAULT_SEED, openOnly=True)
    first = generateDataset(DEFAULT_SEED, closedSeed=7)
    second = generateDataset(DEFAULT_SEED, closedSeed=8)

    for privateDataset in (first, second):
        openCases = [case for case in privateDataset.manifest.cases if case.split != "closed"]
        closedSources = {
            case.assignmentRef.sourceTaskId for case in privateDataset.manifest.cases if case.split == "closed"
        }
        assert openCases == openDataset.manifest.cases
        assert closedSources.isdisjoint(case.assignmentRef.sourceTaskId for case in openCases)
        assert {name: privateDataset.files[name] for name in openDataset.files} == openDataset.files

    assert first.manifest.cases != second.manifest.cases


def testGeneratorRefusesADirectoryWithOtherFiles(tmp_path: Path, dataset: GeneratedDataset) -> None:
    (tmp_path / "old.docx").write_bytes(b"stale")

    with pytest.raises(FileExistsError, match="not empty"):
        writeDataset(dataset, tmp_path)
    with pytest.raises(SystemExit) as stopped:
        main(["--out", str(tmp_path)])
    assert stopped.value.code == 2
    assert [path.name for path in tmp_path.iterdir()] == ["old.docx"]
