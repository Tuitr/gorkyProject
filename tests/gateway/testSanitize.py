"""C4.2: only mathematical content leaves the gateway; personal data is removed or stops the call."""

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from contracts.v1 import openapi
from contracts.v1.references.modelreview import ModelReviewRequest
from references.gateway.prompts import PROMPTS_DIR, PromptCatalog
from references.gateway.sanitize import buildModelCall
from references.gateway.transports.base import ModelCall, ModelReply, RequestRefused
from references.gateway.transports.gigachat import GigaChatSettings, GigaChatTransport
from shared.clock import ManualClock

EXAMPLES = Path(openapi.__file__).parent / "examples" / "gateway"
MARKER = "Маркеров"
MARKER_FULL = "Маркеров Тест Тестович"


def requestBody(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    return body | changes


def buildRequest(**changes: Any) -> ModelReviewRequest:
    return ModelReviewRequest.model_validate(requestBody(**changes))


def steps(*lines: str) -> list[dict[str, str]]:
    return [{"stepId": f"s{index}", "markdown": line} for index, line in enumerate(lines, start=1)]


class CountingTransport:
    name = "fixture"

    def __init__(self) -> None:
        self.calls: list[ModelCall] = []

    def complete(self, call: ModelCall) -> ModelReply:
        self.calls.append(call)
        return ModelReply(content="{}", modelId="fixture")


def review(request: ModelReviewRequest, transport: CountingTransport) -> ModelReply:
    return transport.complete(buildModelCall(request, PromptCatalog()))


def captureOutgoingBody(call: ModelCall) -> str:
    """Send the call through the real GigaChat adapter into a fake server and return the chat body."""
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth"):
            return httpx.Response(200, json={"access_token": "t", "expires_at": 4102444800000})
        bodies.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    GigaChatTransport(GigaChatSettings(authKey="key"), clock=ManualClock(), client=client).complete(call)
    (body,) = bodies
    return body


def testOnlyAllowedFieldsGoOut() -> None:
    request = buildRequest()

    call = buildModelCall(request, PromptCatalog())
    sent = json.loads(captureOutgoingBody(call))

    assert [message["role"] for message in sent["messages"]] == ["system", "user"]
    assert sent["messages"][0]["content"] == (PROMPTS_DIR / "review.v1.md").read_text(encoding="utf-8").strip()
    assert json.loads(sent["messages"][1]["content"]) == {
        "statement": request.statement,
        "rubric": request.rubric,
        "steps": [{"stepId": "s1", "markdown": "Ответ: 5"}],
        "reference": {"schemaId": "scalar.v1", "value": {"exact": "5"}},
    }
    assert call.allowedStepIds == {"s1"}


@pytest.mark.parametrize(
    "changes",
    [
        {"studentName": MARKER_FULL},
        {"fileName": f"{MARKER}.docx"},
        {"metadata": {"author": MARKER_FULL}},
        {"steps": [{"stepId": "s1", "markdown": "Ответ: 5", "author": MARKER}]},
        {"reference": {"referenceId": "r1", "schemaId": "scalar.v1", "value": {"exact": "5"}, "owner": MARKER}},
    ],
    ids=["studentName", "fileName", "metadata", "stepAuthor", "referenceOwner"],
)
def testMetadataCannotEnterTheRequest(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ModelReviewRequest.model_validate(requestBody(**changes))


def testSignatureStepIsRemovedAndNotOffered(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    request = buildRequest(steps=steps(f"Выполнил: {MARKER_FULL}, 9Б", "|a| = √(3² + 4²) = 5", "Ответ: 5"))

    call = buildModelCall(request, PromptCatalog())
    body = captureOutgoingBody(call)

    assert [step.stepId for step in call.data.steps] == ["s2", "s3"]
    assert call.allowedStepIds == {"s2", "s3"}
    assert MARKER not in body
    assert MARKER not in caplog.text
    assert "s1" in caplog.text


@pytest.mark.parametrize(
    ("where", "text", "kind"),
    [
        ("step", f"{MARKER_FULL} решил так: |a| = 5", "fullName"),
        ("step", f"Проверил {MARKER} Т. Т., ответ 5", "initials"),
        ("step", f"|a| = 5 (ученик: {MARKER})", "labelledName"),
        ("step", "Ответ 5, пишите marker.test@example.com", "email"),
        ("step", "Ответ 5, звоните +7 912 345-67-89", "phone"),
        ("statement", f"Длина вектора (3; 4). Вариант для {MARKER_FULL}", "fullName"),
        ("rubric", f"Требования учителя: Мария Ивановна {MARKER}", "fullName"),
    ],
    ids=["fullName", "initials", "labelledName", "email", "phone", "statement", "rubric"],
)
def testPersonalDataInFreeTextStopsTheCall(
    where: str, text: str, kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    options: dict[str, dict[str, Any]] = {
        "step": {"steps": steps("Ответ: 5", text)},
        "statement": {"statement": text},
        "rubric": {"rubric": ["Нужна формула длины вектора", text]},
    }
    changes = options[where]
    transport = CountingTransport()

    with pytest.raises(RequestRefused) as raised:
        review(buildRequest(**changes), transport)

    assert raised.value.code == "personal_data_suspected"
    assert not raised.value.retryable
    assert f"({kind})" in raised.value.message
    assert transport.calls == []
    for leaked in (MARKER, "marker.test", "912"):
        assert leaked not in raised.value.message
        assert leaked not in caplog.text


MATH_LINES = [
    "−(3 − 5) = 2",
    "A(−1; 2), B(3; −4)",
    "x = -3,5",
    "|AB| = √((4 − 1)² + (6 − 2)²) = 5",
    "Точка М — середина отрезка АВ, М(2,5; 4)",
    "По теореме Пифагора: AB² = 3² + 4²",
    "Теорема Виета: x₁ + x₂ = −b/a",
    "8 + 9123456789 = 9123456797",
    "Ответ: −2",
]


def testMathIsSentExactlyAsWritten() -> None:
    request = buildRequest(steps=steps(*MATH_LINES), statement="Найдите длину вектора АВ, если А(1; 2), В(4; 6)")

    call = buildModelCall(request, PromptCatalog())
    sent = json.loads(json.loads(captureOutgoingBody(call))["messages"][1]["content"])

    assert [step["markdown"] for step in sent["steps"]] == MATH_LINES
    assert sent["statement"] == "Найдите длину вектора АВ, если А(1; 2), В(4; 6)"


@pytest.mark.parametrize("promptVersion", ["review.v9", "../INDEX", "review", "Review.v1", "review.v1/../x"])
def testUnknownPromptVersionIsRefusedLocally(promptVersion: str) -> None:
    transport = CountingTransport()

    with pytest.raises(RequestRefused) as raised:
        review(buildRequest(promptVersion=promptVersion), transport)

    assert raised.value.code == "invalid_input"
    assert transport.calls == []


def testRepeatedStepIdsAreRefused() -> None:
    request = buildRequest(steps=[{"stepId": "s1", "markdown": "x = 1"}, {"stepId": "s1", "markdown": "x = 2"}])

    with pytest.raises(RequestRefused, match="s1") as raised:
        buildModelCall(request, PromptCatalog())

    assert raised.value.code == "invalid_input"


def testOnlySignatureStepsIsRefused() -> None:
    with pytest.raises(RequestRefused) as raised:
        buildModelCall(buildRequest(steps=steps(f"ФИО: {MARKER_FULL}")), PromptCatalog())

    assert raised.value.code == "invalid_input"
    assert MARKER not in raised.value.message


def testPromptFilesFollowTheVersionPattern() -> None:
    catalog = PromptCatalog()

    for path in PROMPTS_DIR.glob("*.md"):
        assert catalog.instructions(path.stem)
