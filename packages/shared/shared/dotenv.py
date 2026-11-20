"""Local settings from a .env file in the working directory (the repository root when run from there).

Only for processes started on a developer machine: uvicorn, python -m and the live smoke test. A variable that
is already set in the environment always wins, so Compose, CI and `$env:NAME=...` in the shell override the file.
The file is never in git (.gitignore) and never in an image (.dockerignore). Error messages name the line,
never its value: the file holds secrets such as GIGACHAT_AUTH_KEY.

Format: NAME=value per line; blank lines and lines starting with # are skipped; `export ` before the name and
one pair of matching quotes around the value are allowed. No variable expansion and no multi-line values.
"""

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

DEFAULT_PATH = Path(".env")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class DotEnvError(ValueError):
    """The .env file has a line that is not NAME=value."""


def readDotEnv(path: Path = DEFAULT_PATH) -> dict[str, str]:
    """The NAME=value pairs of the file; {} when there is no file."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.removeprefix("export ").partition("=")
        name = name.strip()
        if not separator or not NAME.fullmatch(name):
            raise DotEnvError(f"{path}: line {number} is not NAME=value")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def loadDotEnv(path: Path = DEFAULT_PATH, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Copy the file's variables into environ (os.environ by default) unless they are set there already.

    Returns the names copied.
    """
    environ = os.environ if environ is None else environ
    loaded = []
    for name, value in readDotEnv(path).items():
        if name not in environ:
            environ[name] = value
            loaded.append(name)
    return loaded
