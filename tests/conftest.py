"""Fixtures shared by all tests.

contractExample reads a published contracts v1 example by its path under contracts/contracts/v1/examples,
for example contractExample("events/attemptSubmitted.json"). Provider and consumer tests read the same
committed file, so a format change cannot pass one side and break the other. Tests keep no copies of examples.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import contracts.v1

EXAMPLES_DIR = Path(contracts.v1.__file__).parent / "examples"


def loadContractExample(relativePath: str) -> Any:
    path = EXAMPLES_DIR / relativePath
    if not path.is_file():
        raise FileNotFoundError(f"No published contracts v1 example {relativePath!r} in {EXAMPLES_DIR}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def contractExample() -> Callable[[str], Any]:
    return loadContractExample
