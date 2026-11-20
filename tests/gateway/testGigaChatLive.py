"""Live smoke test of the real GigaChat API: one paid call. Skipped unless asked for explicitly.

Run it with your own key (never commit the key):
    GIGACHAT_LIVE_SMOKE=1 GIGACHAT_AUTH_KEY=<key> uv run pytest tests/gateway/testGigaChatLive.py -s
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

EXCHANGE = Path(openapi.__file__).parent / "examples" / "gateway" / "exchanges" / "correctSolution.json"

pytestmark = pytest.mark.skipif(
    os.environ.get("GIGACHAT_LIVE_SMOKE") != "1" or not os.environ.get("GIGACHAT_AUTH_KEY", "").strip(),
    reason="live GigaChat call: set GIGACHAT_LIVE_SMOKE=1 and GIGACHAT_AUTH_KEY to run it",
)


def testGigaChatReviewsOneSolution() -> None:
    body = json.loads(EXCHANGE.read_text(encoding="utf-8"))["request"]
    request = ModelReviewRequest.model_validate(body | {"requestId": f"live-smoke-{int(time.time())}"})
    transport = GigaChatTransport(GigaChatSettings.fromEnv())

    reply = transport.complete(buildModelCall(request, PromptCatalog()))
    response = toResponse(request, reply)

    assert response.taskResult.taskId == request.taskId
    result = response.taskResult
    print(f"\nmodel {reply.modelId}: answer={result.answer} reasoning={result.reasoning} method={result.method}")
    print(f"issues: {[issue.category for issue in result.issues]}")
