"""C5.1: the reference values of the open mini set (demo, e2e) are checked independently of the generator.

references/fixtures/openSetKeys.json holds the keys computed by hand, with their numbers and the arithmetic.
This test reads each statement from the generated Word file with python-docx, takes the numbers out of the
text and computes the value again with plain integer arithmetic. Only then is the manifest compared with the
keys, so a wrong expectedReference in the generator is caught instead of being copied.

The check here is a test of the data set, not a solver: it knows three wordings of the open set and nothing
else, and it lives only in the tests.
"""

import copy
import io
import json
import math
import re
from pathlib import Path
from typing import Any

import docx
import pytest

import references
from contracts.v1.dataset.manifest import DatasetManifest
from dataset.generate import DEFAULT_SEED, GeneratedDataset, generateDataset

KEYS_FILE = Path(references.__file__).parent / "fixtures" / "openSetKeys.json"
OPEN_SPLITS = {"demo", "e2e"}
VECTOR = re.compile(r"([a-z])⃗ = \(([−-]?\d+); ([−-]?\d+)\)")

JsonObject = dict[str, Any]


def loadKeys() -> JsonObject:
    keys: JsonObject = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    return keys


@pytest.fixture(scope="module")
def dataset() -> GeneratedDataset:
    return generateDataset(DEFAULT_SEED)


def whole(text: str) -> int:
    return int(text.replace("−", "-"))


def vectorsOf(statement: str) -> dict[str, list[int]]:
    return {name: [whole(x), whole(y)] for name, x, y in VECTOR.findall(statement)}


def exactRoot(square: int) -> str:
    root = math.isqrt(square)
    if root * root != square:
        raise ValueError(f"√{square} is not a whole number")
    return str(root)


def independentValue(statement: str) -> str:
    """The answer of one open-set statement, from the numbers in its text."""
    vectors = vectorsOf(statement)
    if "скалярное произведение" in statement:
        (ax, ay), (bx, by) = vectors["a"], vectors["b"]
        return str(ax * bx + ay * by)
    if "длину вектора a⃗ + b⃗" in statement:
        (ax, ay), (bx, by) = vectors["a"], vectors["b"]
        return exactRoot((ax + bx) ** 2 + (ay + by) ** 2)
    if "длину вектора" in statement:
        ax, ay = vectors["a"]
        return exactRoot(ax**2 + ay**2)
    raise ValueError(f"not an open-set wording: {statement}")


def assignmentTasks(data: bytes) -> dict[str, str]:
    """Task number -> statement, as a teacher sees it in the Word file of the assignment."""
    tasks: dict[str, str] = {}
    for paragraph in docx.Document(io.BytesIO(data)).paragraphs:
        number, dot, statement = paragraph.text.partition(". ")
        if dot and number.isdigit():
            tasks[number] = statement
    return tasks


def findMismatches(manifest: JsonObject, keys: JsonObject) -> list[str]:
    """One line per open case whose expectedReference differs from the verified key, naming its caseId."""
    expected = {caseId: task["expectedReference"] for task in keys["tasks"] for caseId in task["caseIds"]}
    problems = []
    if (manifest["datasetVersion"], manifest["seed"]) != (keys["datasetVersion"], keys["seed"]):
        problems.append(
            f"manifest is {manifest['datasetVersion']} seed {manifest['seed']}, "
            f"keys are for {keys['datasetVersion']} seed {keys['seed']}"
        )
    seen = set()
    for case in manifest["cases"]:
        if case["split"] not in OPEN_SPLITS:
            continue
        caseId = case["caseId"]
        seen.add(caseId)
        if caseId not in expected:
            problems.append(f"{caseId}: no verified key")
        elif case["expectedReference"] != expected[caseId]:
            problems.append(f"{caseId}: manifest has {case['expectedReference']}, verified key is {expected[caseId]}")
    problems.extend(f"{caseId}: verified key has no case in the manifest" for caseId in sorted(set(expected) - seen))
    return problems


def manifestJson(dataset: GeneratedDataset) -> JsonObject:
    document: JsonObject = dataset.manifest.model_dump(mode="json")
    return document


def testIndependentCheckOnNumbersWorkedOutByHand() -> None:
    assert independentValue("Найдите длину вектора a⃗ = (3; −4).") == "5"
    assert independentValue("Даны векторы a⃗ = (1; 2) и b⃗ = (2; 2). Найдите длину вектора a⃗ + b⃗.") == "5"
    assert independentValue("Найдите скалярное произведение векторов a⃗ = (2; −3) и b⃗ = (4; 1).") == "5"
    with pytest.raises(ValueError):
        independentValue("Найдите длину вектора a⃗ = (1; 1).")


def testKeysAreForThisDatasetVersionAndOnlyTheOpenSplits(dataset: GeneratedDataset) -> None:
    keys = loadKeys()
    manifest = dataset.manifest
    openCases = {case.caseId for case in manifest.cases if case.split in OPEN_SPLITS}
    closedTasks = {case.assignmentRef.sourceTaskId for case in manifest.cases if case.split == "closed"}
    closedStatements = {case.assignmentRef.statement for case in manifest.cases if case.split == "closed"}
    keysText = KEYS_FILE.read_text(encoding="utf-8")

    assert (keys["datasetVersion"], keys["seed"]) == (manifest.datasetVersion, manifest.seed)
    assert {caseId for task in keys["tasks"] for caseId in task["caseIds"]} == openCases
    assert not any(secret in keysText for secret in closedTasks | closedStatements), "closed tasks stay out"


def testEveryKeyMatchesTheWordFileAndAnIndependentCalculation(dataset: GeneratedDataset) -> None:
    for task in loadKeys()["tasks"]:
        statement = assignmentTasks(dataset.files[task["assignmentFile"]])[task["taskNumber"]]

        assert statement == task["statement"], task["sourceTaskId"]
        assert vectorsOf(statement) == task["inputs"], task["sourceTaskId"]
        assert task["check"].endswith(f"= {independentValue(statement)}"), task["sourceTaskId"]
        assert task["expectedReference"] == {
            "schemaId": "scalar.v1",
            "value": {"exact": independentValue(statement)},
        }, task["sourceTaskId"]


def testEveryOpenCaseCarriesTheStatementAndNumbersOfItsKey(dataset: GeneratedDataset) -> None:
    byCase = {caseId: task for task in loadKeys()["tasks"] for caseId in task["caseIds"]}
    for case in dataset.manifest.cases:
        if case.caseId in byCase:
            task = byCase[case.caseId]
            ref = case.assignmentRef
            assert (ref.file, ref.taskNumber, ref.statement, ref.sourceTaskId) == (
                task["assignmentFile"],
                task["taskNumber"],
                task["statement"],
                task["sourceTaskId"],
            ), case.caseId


def testGeneratedManifestAgreesWithTheVerifiedKeys(dataset: GeneratedDataset) -> None:
    assert findMismatches(manifestJson(dataset), loadKeys()) == []


def testCorruptedCopyOfTheKeyIsFoundByCaseId(dataset: GeneratedDataset) -> None:
    corrupted = copy.deepcopy(manifestJson(dataset))
    case = next(case for case in corrupted["cases"] if case["caseId"] == "demo1Student2Task3")
    case["expectedReference"]["value"]["exact"] = "8"
    DatasetManifest.model_validate(corrupted)  # still a valid manifest: only the value is wrong

    problems = findMismatches(corrupted, loadKeys())

    assert len(problems) == 1
    assert problems[0].startswith("demo1Student2Task3: ") and "'8'" in problems[0] and "'7'" in problems[0]


def testMissingOrExtraCasesAndAnotherVersionAreReported(dataset: GeneratedDataset) -> None:
    manifest = manifestJson(dataset)
    withoutCase = copy.deepcopy(manifest)
    withoutCase["cases"] = [case for case in manifest["cases"] if case["caseId"] != "e2e1Student2Task1"]
    otherSeed = manifestJson(generateDataset(DEFAULT_SEED + 1))

    assert findMismatches(withoutCase, loadKeys()) == ["e2e1Student2Task1: verified key has no case in the manifest"]
    assert any("seed" in problem for problem in findMismatches(otherSeed, loadKeys()))
