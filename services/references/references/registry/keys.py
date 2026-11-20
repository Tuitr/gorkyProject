"""Stable lookup key of a reference request.

The key covers everything that decides which reference applies: assignment, revision, task,
the cleaned statement and the constraints. familyHint is only a hint and does not change the key.

JSON object key order never matters (keys are sorted). Everything else is compared exactly:
list order (for example coordinates), whitespace and number formatting in the statement are part
of the meaning, and no mathematical normalization is attempted here.
"""

import hashlib
import json

from contracts.v1.references.reference import ReferenceRequest

KEY_VERSION = "k1"


def canonicalJson(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def buildLookupKey(request: ReferenceRequest) -> str:
    payload = {
        "assignmentId": request.assignmentId,
        "assignmentRev": request.assignmentRev,
        "taskId": request.taskId,
        "statement": request.statement,
        "constraints": request.constraints,
    }
    digest = hashlib.sha256(canonicalJson(payload).encode("utf-8")).hexdigest()
    return f"{KEY_VERSION}:{digest}"
