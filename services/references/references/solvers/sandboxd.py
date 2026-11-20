"""The solver-sandbox container: python -m references.solvers.sandboxd /jobs

Runs every job the builder put into the shared directory, one at a time, and writes result.json next to it.
The container has no network and no secrets (deploy/compose.yaml). The daemon itself runs as root only so that
each solver runs as SANDBOX_UID, a user that may read job files but write none of them: a solver cannot forge
the result of another job.
"""

import json
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from references.solvers.sandbox import DEFAULT_RUN_TIMEOUT_S, JOB_FILE, RESULT_FILE, SandboxResult, runChild

SANDBOX_UID = 10001
logger = logging.getLogger("references.sandbox")


def pendingJobs(directory: Path) -> list[Path]:
    jobs = [path for path in directory.iterdir() if path.is_dir() and not path.name.startswith(".")]
    return sorted(
        (job for job in jobs if (job / JOB_FILE).is_file() and not (job / RESULT_FILE).exists()),
        key=lambda job: job.stat().st_mtime,
    )


def runJob(job: Path, uid: int | None) -> None:
    try:
        payload = (job / JOB_FILE).read_text(encoding="utf-8")
        timeoutS = float(json.loads(payload).get("cpuS", DEFAULT_RUN_TIMEOUT_S))
        result = runChild(payload, timeoutS=min(timeoutS, DEFAULT_RUN_TIMEOUT_S), uid=uid)
    except (OSError, ValueError):
        result = SandboxResult(ok=False, error="the sandbox could not read the job")
    staging = job / f".{RESULT_FILE}.tmp"
    try:
        staging.write_text(result.model_dump_json(), encoding="utf-8")
        staging.rename(job / RESULT_FILE)
    except OSError:
        # The builder gave up on the job and removed its directory.
        return
    logger.info(json.dumps({"event": "sandbox.run", "job": job.name, "ok": result.ok}))


def serve(
    directory: Path, *, uid: int | None, pollS: float = 0.2, stop: Callable[[], bool] = lambda: False
) -> None:
    while not stop():
        for job in pendingJobs(directory):
            runJob(job, uid)
        time.sleep(pollS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m references.solvers.sandboxd <jobs directory>")
    serve(Path(sys.argv[1]), uid=SANDBOX_UID if os.geteuid() == 0 else None)


if __name__ == "__main__":
    main()
