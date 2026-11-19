"""Prompt texts of developer 2, found by promptVersion in contracts/v1/grading/prompts/.

The gateway never writes or edits a prompt: it reads <promptVersion>.md and sends it as the system message.
"""

import re
from pathlib import Path

from contracts.v1 import grading
from references.gateway.transports.base import RequestRefused

PROMPTS_DIR = Path(grading.__file__).parent / "prompts"
PROMPT_VERSION = re.compile(r"[a-z][A-Za-z0-9]*\.v[1-9][0-9]*")


class PromptCatalog:
    def __init__(self, directory: Path = PROMPTS_DIR) -> None:
        self.directory = directory
        self.loaded: dict[str, str] = {}

    def instructions(self, promptVersion: str) -> str:
        if promptVersion in self.loaded:
            return self.loaded[promptVersion]
        if PROMPT_VERSION.fullmatch(promptVersion) is None:
            raise RequestRefused("invalid_input", f"promptVersion {promptVersion!r} is not of the form name.vN")
        path = self.directory / f"{promptVersion}.md"
        if not path.is_file():
            raise RequestRefused("invalid_input", f"unknown promptVersion {promptVersion!r}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise RequestRefused("internal_error", f"prompt file for {promptVersion!r} is empty")
        self.loaded[promptVersion] = text
        return text
