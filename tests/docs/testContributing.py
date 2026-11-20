"""G4.3: the commands and the commit example in CONTRIBUTING.md agree with the repository configuration."""

import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
GUIDE = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
COMMIT = re.compile(r"^(core|fix|test|docs)\((?P<scope>[^),\s]+)\): [a-z][\x20-\x7e]*$")


def pyproject() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def routine() -> str:
    return GUIDE.split("## Порядок закрытия шага", 1)[1]


def testRoutineRunsTheWholeSuiteFromTheRoot() -> None:
    steps = routine()

    assert "`uv run pytest` из корня, без `-m`" in steps
    assert "`uv run mypy`" in steps
    assert pyproject()["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
    assert not any(option.startswith("-m") for option in pyproject()["tool"]["pytest"]["ini_options"]["addopts"])


def testEveryMarkerInTheGuideIsRegistered() -> None:
    registered = {marker.split(":")[0] for marker in pyproject()["tool"]["pytest"]["ini_options"]["markers"]}
    used = set(re.findall(r'pytest -m "?(?:not )?(\w+)', GUIDE))

    assert used and used <= registered


def testEveryTestPathInTheGuideExists() -> None:
    paths = set(re.findall(r"uv run pytest (tests/[\w/]+(?:\.py)?)", GUIDE))

    assert paths
    assert [path for path in sorted(paths) if not (ROOT / path).exists()] == []


def testCommitExampleFollowsTheFormat() -> None:
    example = re.search(r"Пример сообщения для этого шага:\n\n```\n(.+)\n```", routine())

    assert example is not None
    match = COMMIT.match(example.group(1))
    assert match is not None
    assert match.group("scope") == "workflow"


def testCountedBadExamplesAreRejected() -> None:
    for message in ("docs: update", "core(dataset): генератор", "fix(dataset, auth): mix two steps"):
        assert COMMIT.match(message) is None, message
