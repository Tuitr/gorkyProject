"""Authenticated HTTP requests from the cabinet to grading."""

import httpx
from pydantic import BaseModel, ValidationError

from cabinet.settings import Settings
from contracts.v1.common.errors import ApiError
from shared.httperrors import TRACE_HEADER, ApiProblem


def invalidGradingResponse() -> ApiProblem:
    return ApiProblem("grading_invalid_response", "Invalid response from grading service", status=502)


def sendGradingRequest(
    settings: Settings, method: str, path: str, traceId: str, transport: httpx.BaseTransport | None,
    *, params: dict[str, int | list[str]] | None = None, body: dict[str, object] | None = None,
) -> httpx.Response:
    if not settings.gradingServiceToken:
        raise ApiProblem("unavailable", "Grading service is unavailable")
    try:
        with httpx.Client(base_url=settings.gradingBaseUrl, timeout=5, trust_env=False, transport=transport) as client:
            return client.request(
                method, path, params=params, json=body,
                headers={"Authorization": f"Bearer {settings.gradingServiceToken}", TRACE_HEADER: traceId},
            )
    except httpx.TimeoutException:
        raise ApiProblem("timeout", "Grading service timed out") from None
    except (httpx.RequestError, httpx.InvalidURL):
        raise ApiProblem("unavailable", "Grading service is unavailable") from None


def readGradingModel[Model: BaseModel](response: httpx.Response, schema: type[Model]) -> Model:
    try:
        if response.status_code == 404 and ApiError.model_validate_json(response.content).code == "not_found":
            raise ApiProblem("not_found", "Grading result not found")
        if response.status_code != 200:
            raise ApiProblem("unavailable", "Grading service is unavailable")
        return schema.model_validate_json(response.content)
    except ValidationError:
        raise invalidGradingResponse() from None
