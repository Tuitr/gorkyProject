"""An isolated Compose project with every backend service for the E2E scenario (E6.1).

Each pytest session gets its own project name, volumes and free ports, so it never sees the developer stand,
old results or another CI run, and only its own containers and volumes are removed afterwards. The gateway
keeps GATEWAY_TRANSPORT=fixture: no paid model call.

    E2E_CABINET_URL=http://127.0.0.1:18080   use an already running full stand instead (nothing is started)
    E2E_BUILD=0                              reuse the gorky-*:dev images instead of building them
    E2E_KEEP=1                               leave the project running for inspection (prints its name)
    E2E_LOGS=reports/e2e-stand.log           after a failed scenario, save the service logs there (CI uploads it)
"""

import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deploy" / "compose.yaml"
SECRET_VARIABLES = ("GIGACHAT_AUTH_KEY", "GIGACHAT_CA_BUNDLE")


def compose(project: str, *arguments: str, timeoutS: float = 600) -> str:
    environment = {key: value for key, value in os.environ.items() if key not in SECRET_VARIABLES}
    environment |= {
        "POSTGRES_PORT": "0", "REDIS_PORT": "0", "S3_PORT": "0", "CABINET_PORT": "0", "GATEWAY_TRANSPORT": "fixture",
    }
    command = ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", project, "--profile", "full", *arguments]
    finished = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=timeoutS, check=False,
    )
    if finished.returncode != 0:
        raise RuntimeError(f"`{' '.join(command[2:])}` failed:\n{finished.stdout[-2000:]}\n{finished.stderr[-4000:]}")
    return finished.stdout


@pytest.fixture(scope="session")
def e2eStand(request: pytest.FixtureRequest) -> Iterator[str]:
    """The cabinet base URL of a stand that runs every service."""
    if url := os.environ.get("E2E_CABINET_URL"):
        yield url.rstrip("/")
        return
    if shutil.which("docker") is None:
        pytest.fail("The E2E scenario starts its own Docker Compose stand: install Docker or set E2E_CABINET_URL",
                    pytrace=False)
    project = f"gorky-e2e-{uuid.uuid4().hex[:10]}"
    build = ["--build"] if os.environ.get("E2E_BUILD", "1") != "0" else ["--no-build"]
    try:
        try:
            compose(project, "up", "-d", *build, "--wait", "--wait-timeout", "300")
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            try:
                logs = compose(project, "logs", "--no-color", "--tail", "40", timeoutS=60)
            except (RuntimeError, subprocess.TimeoutExpired):
                logs = ""
            pytest.fail(f"The E2E stand {project} did not start: {error}\n{logs[-6000:]}", pytrace=False)
        port = compose(project, "port", "cabinet", "8000").strip().rsplit(":", 1)[1]
        os.environ["E2E_COMPOSE_PROJECT"] = project
        yield f"http://127.0.0.1:{port}"
    finally:
        if request.session.testsfailed and (logPath := os.environ.get("E2E_LOGS")):
            try:
                Path(logPath).parent.mkdir(parents=True, exist_ok=True)
                Path(logPath).write_text(compose(project, "logs", "--no-color", timeoutS=60), encoding="utf-8")
            except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
                print(f"could not save the E2E stand logs: {error}")
        if os.environ.get("E2E_KEEP") == "1":
            print(f"E2E stand kept: docker compose -p {project} --profile full down --volumes")
        else:
            compose(project, "down", "--volumes", "--remove-orphans", timeoutS=180)
