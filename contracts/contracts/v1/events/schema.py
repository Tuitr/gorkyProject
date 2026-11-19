"""Builds the committed JSON Schema of the checks.v1 `data` field from the Pydantic models.

Regenerate after changing a model:  uv run python -m contracts.v1.events.schema
A test fails when the committed file differs from the generated one.
"""

import json
from pathlib import Path
from typing import Any

from contracts.v1.events.checks import STREAM_NAME, checksEventAdapter

SCHEMA_PATH = Path(__file__).parents[1] / "schemas" / "checks.json"

JsonObject = dict[str, Any]


def buildChecksSchema() -> JsonObject:
    schema = checksEventAdapter.json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{STREAM_NAME} event (developer 1)",
        "description": "Value of the field `data` in the Redis stream checks.v1. `type` selects the payload.",
        **schema,
    }


def renderDocument(document: JsonObject) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def writeSchema() -> None:
    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_text(renderDocument(buildChecksSchema()), encoding="utf-8")


if __name__ == "__main__":
    writeSchema()
