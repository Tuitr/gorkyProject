import httpx
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from contracts.v1.common.errors import ApiError
from shared.httperrors import ApiProblem, installErrorHandlers
from shared.servicetokens import ServiceIdentity, ServiceTokenRegistry, requireRight

GRADING_TOKEN = "grading-test-token"
CABINET_TOKEN = "cabinet-test-token"


class EchoBody(BaseModel):
    taskId: str


def buildApp() -> FastAPI:
    app = FastAPI()
    installErrorHandlers(app)
    app.state.serviceTokens = ServiceTokenRegistry(
        {
            GRADING_TOKEN: ServiceIdentity("grading", frozenset({"references.request"})),
            CABINET_TOKEN: ServiceIdentity("cabinet", frozenset()),
        }
    )

    @app.post("/internal/v1/echo")
    async def echo(
        body: EchoBody, caller: ServiceIdentity = Depends(requireRight("references.request"))
    ) -> dict[str, str]:
        return {"taskId": body.taskId, "caller": caller.serviceName}

    @app.post("/internal/v1/conflict")
    async def conflict(caller: ServiceIdentity = Depends(requireRight("references.request"))) -> None:
        raise ApiProblem("conflict", "Same requestId with a different body")

    return app


client = TestClient(buildApp())


def assertContractError(response: httpx.Response, status: int, code: str) -> ApiError:
    assert response.status_code == status
    error = ApiError.model_validate(response.json())
    assert set(response.json()) == {"code", "message", "retryable", "traceId"}
    assert error.code == code
    return error


def testMissingTokenIsRejected() -> None:
    response = client.post("/internal/v1/echo", json={"taskId": "t1"})

    assertContractError(response, 401, "unauthorized")


def testTeacherCookieDoesNotAuthorizeInternalCall() -> None:
    teacherClient = TestClient(buildApp(), cookies={"session": "teacher"})

    response = teacherClient.post("/internal/v1/echo", json={"taskId": "t1"})

    assertContractError(response, 401, "unauthorized")


def testServiceWithoutRightIsForbidden() -> None:
    response = client.post(
        "/internal/v1/echo", json={"taskId": "t1"}, headers={"Authorization": f"Bearer {CABINET_TOKEN}"}
    )

    assertContractError(response, 403, "forbidden")


def testInvalidInputGives422WithoutEchoingTheBody() -> None:
    response = client.post(
        "/internal/v1/echo",
        json={"taskId": 5, "studentName": "Иванов Секрет"},
        headers={"Authorization": f"Bearer {GRADING_TOKEN}", "X-Trace-Id": "t42"},
    )

    error = assertContractError(response, 422, "invalid_input")
    assert error.traceId == "t42"
    assert "Секрет" not in response.text


def testConflictIsTerminalAndAllowedCallSucceeds() -> None:
    headers = {"Authorization": f"Bearer {GRADING_TOKEN}"}

    conflict = assertContractError(client.post("/internal/v1/conflict", headers=headers), 409, "conflict")
    success = client.post("/internal/v1/echo", json={"taskId": "t1"}, headers=headers)

    assert conflict.retryable is False
    assert success.status_code == 200
    assert success.json() == {"taskId": "t1", "caller": "grading"}


def testRetryableCodesAreMarked() -> None:
    app = buildApp()

    @app.get("/internal/v1/busy")
    async def busy() -> None:
        raise ApiProblem("rate_limited", "Provider rate limit", retryAfterS=3)

    response = TestClient(app).get("/internal/v1/busy")

    error = assertContractError(response, 429, "rate_limited")
    assert error.retryable is True
    assert response.headers["Retry-After"] == "3"


def testRegistryFromJson() -> None:
    registry = ServiceTokenRegistry.fromJson('{"abc": {"serviceName": "grading", "rights": ["model.review"]}}')

    assert registry.identify("abc") == ServiceIdentity("grading", frozenset({"model.review"}))
    assert registry.identify("other") is None
