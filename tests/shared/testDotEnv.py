"""Local .env: the shell wins over the file, and errors never show a value."""

import os
from pathlib import Path

import pytest

from references.registry.main import createApp
from shared.dotenv import DotEnvError, loadDotEnv, readDotEnv

ROOT = Path(__file__).parents[2]


def writeEnv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def testFileValuesAreReadAsWritten(tmp_path: Path) -> None:
    path = writeEnv(
        tmp_path,
        "﻿# comment\n\nGATEWAY_TRANSPORT=gigachat\r\nexport GIGACHAT_SCOPE = 'GIGACHAT_API_PERS'\n"
        'GIGACHAT_AUTH_KEY="a2V5OnNlY3JldA=="\nEMPTY=\nURL=postgresql://u:p@h/db?x=1#frag\n',
    )

    assert readDotEnv(path) == {
        "GATEWAY_TRANSPORT": "gigachat",
        "GIGACHAT_SCOPE": "GIGACHAT_API_PERS",
        "GIGACHAT_AUTH_KEY": "a2V5OnNlY3JldA==",
        "EMPTY": "",
        "URL": "postgresql://u:p@h/db?x=1#frag",
    }


def testShellValueWins(tmp_path: Path) -> None:
    path = writeEnv(tmp_path, "GATEWAY_TRANSPORT=gigachat\nGIGACHAT_AUTH_KEY=from-file\n")
    environ = {"GATEWAY_TRANSPORT": "fixture"}

    loaded = loadDotEnv(path, environ)

    assert loaded == ["GIGACHAT_AUTH_KEY"]
    assert environ == {"GATEWAY_TRANSPORT": "fixture", "GIGACHAT_AUTH_KEY": "from-file"}


def testMissingFileChangesNothing(tmp_path: Path) -> None:
    environ = {"A": "1"}

    assert loadDotEnv(tmp_path / ".env", environ) == []
    assert environ == {"A": "1"}


@pytest.mark.parametrize("line", ["GIGACHAT_AUTH_KEY secret-value", "1BAD=secret-value", "=secret-value"])
def testBadLineNamesTheLineNotTheValue(tmp_path: Path, line: str) -> None:
    path = writeEnv(tmp_path, f"OK=1\n{line}\n")

    with pytest.raises(DotEnvError, match="line 2 is not NAME=value") as raised:
        readDotEnv(path)

    assert "secret-value" not in str(raised.value)


def testExampleFileParses() -> None:
    values = readDotEnv(ROOT / ".env.example")

    assert values["GATEWAY_TRANSPORT"] == "fixture"
    assert "GIGACHAT_AUTH_KEY" not in values


def testEnvFileStaysOutOfGitAndImages() -> None:
    for ignore in (".gitignore", ".dockerignore"):
        assert ".env" in (ROOT / ignore).read_text(encoding="utf-8").split(), ignore


def testServiceStartedWithoutSettingsReadsTheFile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writeEnv(tmp_path, 'REFERENCES_SERVICE_TOKENS={"from-file": {"serviceName": "grading", "rights": []}}\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {})

    app = createApp()

    identity = app.state.serviceTokens.identify("from-file")
    assert identity is not None and identity.serviceName == "grading"
