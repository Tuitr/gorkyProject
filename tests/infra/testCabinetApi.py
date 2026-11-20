"""Smoke test of the public HTTP contract exposed by the Compose cabinet."""

import os

import httpx

from contracts.v1.common.errors import ApiError


def testCabinetServesPublicLoginContract() -> None:
    port = os.environ.get("CABINET_PORT", "18080")
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
        client.get("/ready").raise_for_status()
        response = client.get("/openapi.json")
        response.raise_for_status()
        schema = response.json()
        assert all(path.startswith("/v1/") for path in schema["paths"])
        login = schema["paths"]["/v1/sessions"]["post"]
        checks = schema["paths"]["/v1/checks/{checkId}"]["get"]["responses"]
        assert checks["200"]["content"]["application/json"]["schema"]["discriminator"]["propertyName"] == "state"
        for status in ("401", "422", "429"):
            body = login["responses"][status]["content"]["application/json"]
            assert body["schema"]["$ref"] == "#/components/schemas/ApiError"
        assert set(schema["components"]["schemas"]["ApiError"]["required"]) == {
            "code", "message", "retryable", "traceId"
        }

        invalid = client.post("/v1/sessions", json={}, headers={"X-Trace-Id": "cabinet-smoke"})
        assert invalid.status_code == 422
        error = ApiError.model_validate(invalid.json())
        assert error.code == "invalid_input"
        assert error.traceId == invalid.headers["X-Trace-Id"] == "cabinet-smoke"
