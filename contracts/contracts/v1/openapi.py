"""Builds the committed OpenAPI documents of contracts v1 from the Pydantic models.

Regenerate after changing a model:  uv run python -m contracts.v1.openapi
A test fails when the committed file differs from the generated one.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from contracts.v1.common.errors import ApiError
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from contracts.v1.references.reference import (
    ReferencePending,
    ReferenceReady,
    ReferenceRequest,
    ReferenceUnsupported,
    ReferenceValidationFailed,
)

OPENAPI_DIR = Path(__file__).parent / "openapi"
REF_TEMPLATE = "#/components/schemas/{model}"

JsonObject = dict[str, Any]


def buildComponents(models: list[type[BaseModel]]) -> JsonObject:
    _, schema = models_json_schema(
        [(model, "validation") for model in models], ref_template=REF_TEMPLATE
    )
    components: JsonObject = schema.get("$defs", {})
    return components


def schemaRef(model: type[BaseModel]) -> JsonObject:
    return {"$ref": REF_TEMPLATE.format(model=model.__name__)}


def jsonContent(schema: JsonObject) -> JsonObject:
    return {"application/json": {"schema": schema}}


def errorResponse(description: str) -> JsonObject:
    return {"description": description, "content": jsonContent(schemaRef(ApiError))}


def finalResult() -> JsonObject:
    return {
        "oneOf": [
            schemaRef(ReferenceReady),
            schemaRef(ReferenceUnsupported),
            schemaRef(ReferenceValidationFailed),
        ],
        "discriminator": {"propertyName": "state"},
    }


def anyResult() -> JsonObject:
    result = finalResult()
    result["oneOf"].insert(1, schemaRef(ReferencePending))
    return result


def buildReferencesOpenApi() -> JsonObject:
    models: list[type[BaseModel]] = [
        ReferenceRequest,
        ReferenceReady,
        ReferencePending,
        ReferenceUnsupported,
        ReferenceValidationFailed,
        ApiError,
    ]
    commonErrors = {
        "401": errorResponse("Service token is missing or invalid"),
        "403": errorResponse("Calling service has no right for this operation"),
        "422": errorResponse("Request does not match the schema"),
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Reference API (developer 3)", "version": "1.0.0"},
        "paths": {
            "/internal/v1/references": {
                "post": {
                    "operationId": "requestReference",
                    "summary": "Find or create the reference for one task of an assignment revision",
                    "description": (
                        "Idempotent: the same assignment, revision, task, statement and constraints "
                        "return the same referenceId."
                    ),
                    "security": [{"serviceToken": []}],
                    "requestBody": {"required": True, "content": jsonContent(schemaRef(ReferenceRequest))},
                    "responses": {
                        "200": {"description": "Final state", "content": jsonContent(finalResult())},
                        "202": {"description": "Still preparing", "content": jsonContent(schemaRef(ReferencePending))},
                        **commonErrors,
                    },
                }
            },
            "/internal/v1/references/{referenceId}": {
                "get": {
                    "operationId": "getReference",
                    "summary": "Read the stored state of a reference",
                    "security": [{"serviceToken": []}],
                    "parameters": [
                        {"name": "referenceId", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {
                        "200": {"description": "Stored state", "content": jsonContent(anyResult())},
                        "404": errorResponse("No reference with this id"),
                        **commonErrors,
                    },
                }
            },
        },
        "components": {
            "schemas": buildComponents(models),
            "securitySchemes": {"serviceToken": {"type": "http", "scheme": "bearer"}},
        },
    }


def buildGatewayOpenApi() -> JsonObject:
    models: list[type[BaseModel]] = [ModelReviewRequest, ModelReviewResponse, ApiError]
    return {
        "openapi": "3.1.0",
        "info": {"title": "Model gateway (developer 3)", "version": "1.0.0"},
        "paths": {
            "/internal/v1/model/review": {
                "post": {
                    "operationId": "reviewTask",
                    "summary": "Check one task of one attempt with the model",
                    "description": (
                        "taskResult follows contracts.v1.grading.taskresult (owner: developer 2). "
                        "The same requestId with the same body returns the stored answer."
                    ),
                    "security": [{"serviceToken": []}],
                    "requestBody": {"required": True, "content": jsonContent(schemaRef(ModelReviewRequest))},
                    "responses": {
                        "200": {"description": "Valid taskResult", "content": jsonContent(schemaRef(ModelReviewResponse))},
                        "401": errorResponse("Service token is missing or invalid"),
                        "403": errorResponse("Calling service has no right for this operation"),
                        "409": errorResponse("Same requestId with a different body"),
                        "422": errorResponse(
                            "code=invalid_input: schema error or unknown promptVersion; "
                            "code=personal_data_suspected: the text looks like personal data, the model was not called"
                        ),
                        "429": errorResponse("Provider rate limit; retry after Retry-After with the same requestId"),
                        "502": errorResponse("code=model_invalid_output: provider answer failed the schema after retries"),
                        "503": errorResponse("Provider unavailable; retryable"),
                    },
                }
            }
        },
        "components": {
            "schemas": buildComponents(models),
            "securitySchemes": {"serviceToken": {"type": "http", "scheme": "bearer"}},
        },
    }


def buildAll() -> dict[str, JsonObject]:
    return {"references.json": buildReferencesOpenApi(), "gateway.json": buildGatewayOpenApi()}


def renderDocument(document: JsonObject) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def writeAll() -> None:
    OPENAPI_DIR.mkdir(exist_ok=True)
    for fileName, document in buildAll().items():
        (OPENAPI_DIR / fileName).write_text(renderDocument(document), encoding="utf-8")


if __name__ == "__main__":
    writeAll()
