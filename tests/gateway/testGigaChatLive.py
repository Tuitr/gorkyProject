"""Live smoke test of the real GigaChat API: one paid call. Skipped unless asked for explicitly.

Put GIGACHAT_AUTH_KEY (and GIGACHAT_CA_BUNDLE if needed) in your own .env or the shell, never in git.
The switch comes only from the shell, so a full `uv run pytest` never pays by accident:
    GIGACHAT_LIVE_SMOKE=1 uv run pytest tests/gateway/testGigaChatLive.py -s
It checks sign-in, TLS, the review.v2 prompt and that the answer is a valid taskResult. The verdict itself
is only printed: a live model may judge differently from the recorded fixtures.
"""

import json
import os
import time
from pathlib import Path

import pytest

from contracts.v1 import openapi
from contracts.v1.references.modelreview import ModelReviewRequest
from references.gateway.prompts import PromptCatalog
from references.gateway.sanitize import buildModelCall
from references.gateway.transports.gigachat import GigaChatSettings, GigaChatTransport
from references.gateway.validation import toResponse
from shared.dotenv import readDotEnv

EXCHANGE = Path(openapi.__file__).parent / "examples" / "gateway" / "exchanges" / "correctSolution.json"

pytestmark = pytest.mark.skipif(
    os.environ.get("GIGACHAT_LIVE_SMOKE") != "1", reason="live GigaChat call: set GIGACHAT_LIVE_SMOKE=1 in the shell"
)


def testGigaChatReviewsOneSolution() -> None:
    body = json.loads(EXCHANGE.read_text(encoding="utf-8"))["request"]
    request = ModelReviewRequest.model_validate(body | {"requestId": f"live-smoke-{int(time.time())}"})
    environ = readDotEnv() | dict(os.environ)
    if not environ.get("GIGACHAT_AUTH_KEY", "").strip():
        pytest.fail("GIGACHAT_LIVE_SMOKE=1 needs GIGACHAT_AUTH_KEY in .env or in the shell")
    transport = GigaChatTransport(GigaChatSettings.fromEnv(environ))

    reply = transport.complete(buildModelCall(request, PromptCatalog()))
    response = toResponse(request, reply)

    assert response.taskResult.taskId == request.taskId
    result = response.taskResult
    print(f"\nmodel {reply.modelId}: answer={result.answer} reasoning={result.reasoning} method={result.method}")
    print(f"issues: {[issue.category for issue in result.issues]}")
