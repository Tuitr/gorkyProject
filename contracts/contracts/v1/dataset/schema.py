"""Builds the committed JSON Schema of manifest.json from the Pydantic models.

Regenerate after changing a model:  uv run python -m contracts.v1.dataset.schema
A test fails when the committed file differs from the generated one.
"""

import json
from pathlib import Path
from typing import Any

from contracts.v1.dataset.manifest import DatasetManifest

SCHEMA_PATH = Path(__file__).parents[1] / "schemas" / "dataset.json"

JsonObject = dict[str, Any]


def buildDatasetSchema() -> JsonObject:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "manifest.json of the annotated mini dataset (developer 1)",
        "description": "One record per task of one synthetic submission. The answer key: never inside a student's DOCX.",
        **DatasetManifest.model_json_schema(),
    }


def renderDocument(document: JsonObject) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def writeSchema() -> None:
    SCHEMA_PATH.parent.mkdir(exist_ok=True)
    SCHEMA_PATH.write_text(renderDocument(buildDatasetSchema()), encoding="utf-8")


if __name__ == "__main__":
    writeSchema()
