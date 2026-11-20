"""C5.2: developer 2's clients read the reference and review answers that developer 3 publishes.

A small HTTP server replays the exchanges of contracts/v1/examples/{references,gateway}/exchanges byte for byte.
The same files run through the real registry and gateway in tests/references/testServiceFixtures.py, so both
sides of the internal API are tested against one set of examples.
"""

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from contracts.v1 import openapi
from contracts.v1.references.modelreview import ModelReviewRequest
from contracts.v1.references.reference import ReferenceRequest, referenceResultAdapter
from grading.references import ReferenceClient
from grading.review import sendReview, validatedResult
from grading.submission import TaskReading

EXAMPLES = Path(openapi.__file__).parent / "examples"
REFERENCE_EXCHANGES = EXAMPLES / "references" / "exchanges"
REVIEW_EXCHANGES = EXAMPLES / "gateway" / "exchanges"
TOKEN = "grading-test-token"

Reply = tuple[str, str, int, Any]


class Replay:
    """Answers the expected requests in order and records what the client sent."""

    def __init__(self) -> None:
        self.replies: list[Reply] = []
        self.received: list[tuple[str, str, str | None, Any]] = []

    def answer(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length") or 0)
        body = json.loads(handler.rfile.read(length)) if length else None
        self.received.append((handler.command, handler.path, handler.headers.get("Authorization"), body))
        method, path, status, reply = self.replies.pop(0)
        payload = json.dumps(reply, ensure_ascii=False).encode("utf-8")
        handler.send_response(status if (method, path) == (handler.command, handler.path) else 418)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Replay, str]]:
    state = Replay()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.answer(self)

        def do_POST(self) -> None:
            state.answer(self)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("GRADING_GATEWAY_URL", url)
    monkeypatch.setenv("GRADING_GATEWAY_TOKEN", TOKEN)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, url
    finally:
        server.shutdown()
        server.server_close()


def readJson(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(path.name for path in REFERENCE_EXCHANGES.glob("*.json")))
def testReferenceClientReadsEveryPublishedState(replay: tuple[Replay, str], name: str) -> None:
    state, url = replay
    exchange = readJson(REFERENCE_EXCHANGES / name)
    referenceId = exchange["steps"][0]["body"]["referenceId"]
    state.replies = [
        (step["method"], step["path"].format(referenceId=referenceId), step["status"], step["body"])
        for step in exchange["steps"]
    ]
    client = ReferenceClient(url, TOKEN)

    results = [client.request(ReferenceRequest.model_validate(exchange["request"]))]
    results += [client.get(referenceId) for _ in exchange["steps"][1:]]

    assert results == [referenceResultAdapter.validate_python(step["body"]) for step in exchange["steps"]]
    assert state.received[0][2:] == (f"Bearer {TOKEN}", exchange["request"])
    final = results[-1]
    if final.state in ("unsupported", "validation_failed"):
        assert final.reasonCode == exchange["steps"][-1]["body"]["reasonCode"]


@pytest.mark.parametrize("name", sorted(path.name for path in REVIEW_EXCHANGES.glob("*.json")))
def testReviewClientKeepsThePublishedVerdict(replay: tuple[Replay, str], name: str) -> None:
    state, _ = replay
    exchange = readJson(REVIEW_EXCHANGES / name)
    request = ModelReviewRequest.model_validate(exchange["request"])
    state.replies = [("POST", "/internal/v1/model/review", exchange["status"], exchange["response"])]
    answerOnly = len(request.steps) == 1 and request.steps[0].markdown.startswith("Ответ")
    reading = TaskReading(taskId=request.taskId, status="answerOnly" if answerOnly else "hasSteps")

    result = validatedResult(request, sendReview(request).taskResult, reading)

    assert result.model_dump(mode="json") == exchange["response"]["taskResult"]
    assert state.received[0][3] == exchange["request"]
