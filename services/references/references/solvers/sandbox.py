"""Running generated solver code: one child process per run, never inside a service process.

runChild starts harness.py with `python -I -S`, an empty environment, a wall-clock timeout and the limits the
harness sets on itself; with a uid it also drops to that user (the sandbox daemon runs as root only to do this).

Two sandboxes:
- DirectorySandbox (stand): the builder writes a job into a directory shared with the solver-sandbox container
  (deploy/compose.yaml: no network, no secrets, read-only, 1 CPU, 256 MB, 64 processes) and waits for result.json.
  The builder holds the GigaChat key, so the code must not run in its container: a child there could read
  /proc/1/environ.
- LocalSandbox (tests, a developer machine): runChild right here. Never use it where a secret is in reach.
"""

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from references.solvers.policy import ALLOWED_IMPORTS
from shared.clock import Clock, SystemClock

HARNESS = Path(__file__).with_name("harness.py")
DEFAULT_RUN_TIMEOUT_S = 10.0
MAX_OUTPUT_BYTES = 64 * 1024
JOB_FILE = "job.json"
RESULT_FILE = "result.json"


class SandboxResult(BaseModel):
    """ok with the value solve(task) returned, or the failure as text that is safe to show to the author."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    value: dict[str, JsonValue] | None = None
    error: str | None = None


class Sandbox(Protocol):
    def run(self, code: str, task: dict[str, JsonValue]) -> SandboxResult: ...


def jobPayload(code: str, task: dict[str, JsonValue], timeoutS: float) -> str:
    return json.dumps(
        {"code": code, "task": task, "allowedImports": sorted(ALLOWED_IMPORTS), "cpuS": max(1, int(timeoutS))},
        ensure_ascii=False,
    )


def childEnvironment() -> dict[str, str]:
    """Empty on Linux. Windows Python cannot start without SYSTEMROOT; nothing secret is in it."""
    if sys.platform == "win32":
        return {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows")}
    return {}


def runChild(payload: str, *, timeoutS: float = DEFAULT_RUN_TIMEOUT_S, uid: int | None = None) -> SandboxResult:
    """One run of harness.py. Any timeout, crash, oversized or malformed output is a failed run."""
    command = [sys.executable, "-I", "-S", "-B", str(HARNESS)]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed command, the code goes in on stdin
            command,
            input=payload.encode("utf-8"),
            capture_output=True,
            env=childEnvironment(),
            cwd="/",
            timeout=timeoutS,
            check=False,
            user=uid,
            group=uid,
            extra_groups=None if uid is None else [],
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(ok=False, error=f"the solver ran longer than {timeoutS:g} s")
    if len(completed.stdout) > MAX_OUTPUT_BYTES:
        return SandboxResult(ok=False, error=f"the solver printed more than {MAX_OUTPUT_BYTES} bytes")
    lines = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
    if completed.returncode != 0 or len(lines) != 1:
        return SandboxResult(ok=False, error=f"the solver process failed (exit code {completed.returncode})")
    try:
        return SandboxResult.model_validate_json(lines[0])
    except ValueError:
        return SandboxResult(ok=False, error="the solver answer is not a JSON object")


class LocalSandbox:
    def __init__(self, timeoutS: float = DEFAULT_RUN_TIMEOUT_S) -> None:
        self.timeoutS = timeoutS

    def run(self, code: str, task: dict[str, JsonValue]) -> SandboxResult:
        return runChild(jobPayload(code, task, self.timeoutS), timeoutS=self.timeoutS)


class DirectorySandbox:
    """Client side of the solver-sandbox container: <dir>/<jobId>/job.json in, <dir>/<jobId>/result.json out."""

    def __init__(
        self, directory: Path, timeoutS: float = DEFAULT_RUN_TIMEOUT_S, *, waitS: float = 60.0,
        pollS: float = 0.2, clock: Clock | None = None,
    ) -> None:
        self.directory = directory
        self.timeoutS = timeoutS
        self.waitS = waitS
        self.pollS = pollS
        self.clock = clock if clock is not None else SystemClock()

    def run(self, code: str, task: dict[str, JsonValue]) -> SandboxResult:
        jobId = uuid.uuid4().hex
        staging = self.directory / f".{jobId}.tmp"
        final = self.directory / jobId
        staging.mkdir(mode=0o755)
        try:
            (staging / JOB_FILE).write_text(jobPayload(code, task, self.timeoutS), encoding="utf-8")
            os.chmod(staging / JOB_FILE, 0o644)
            staging.rename(final)
            return self.waitForResult(final)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(final, ignore_errors=True)

    def waitForResult(self, final: Path) -> SandboxResult:
        waited = 0.0
        result = final / RESULT_FILE
        while not result.is_file():
            if waited >= self.waitS:
                return SandboxResult(ok=False, error="the sandbox did not answer in time")
            self.clock.sleep(self.pollS)
            waited += self.pollS
        try:
            return SandboxResult.model_validate_json(result.read_bytes()[: MAX_OUTPUT_BYTES + 1024])
        except ValueError:
            return SandboxResult(ok=False, error="the sandbox answer is not a JSON object")
