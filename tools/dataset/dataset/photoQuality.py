"""Measure automatic photo grading against independently labelled task verdicts."""

import argparse
import json
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen


def fetchCheck(baseUrl: str, checkId: str) -> dict[str, Any] | None:
    try:
        url = f"{baseUrl.rstrip('/')}/v1/checks/{quote(checkId, safe='')}"
        with urlopen(url, timeout=15) as response:
            value = json.load(response)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def decision(check: dict[str, Any] | None, taskId: str, resultRev: int) -> str:
    if check is None or check.get("resultRev") != resultRev or check.get("publishedResultRev") is not None:
        return "unavailable"
    result = next((item for item in check.get("taskResults", []) if item.get("taskId") == taskId), None)
    score = next((item for item in check.get("taskAssessments", []) if item.get("taskId") == taskId), None)
    if result is None or score is None:
        return "unavailable"
    answer = result.get("answer")
    if result.get("needsReview") or score.get("score") is None:
        return "manual"
    if answer == "skipped" and score["score"] == 0:
        return "incorrect"
    return str(answer) if answer in ("correct", "incorrect") else "manual"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--cabinet-url", default="http://127.0.0.1:15173")
    args = parser.parse_args()
    cases = json.loads(args.labels.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        parser.error("labels must be a non-empty JSON array")
    seen: set[tuple[str, str]] = set()
    checks: dict[str, dict[str, Any] | None] = {}
    counts = dict(matched=0, wrong=0, manual=0, unavailable=0)
    failures = []
    for case in cases:
        if not isinstance(case, dict):
            parser.error("each label must be a JSON object")
        checkId, taskId, expected = (case.get(field) for field in ("checkId", "taskId", "expected"))
        revision = case.get("resultRev", 1)
        if not (isinstance(checkId, str) and checkId and isinstance(taskId, str) and taskId
                and expected in ("correct", "incorrect") and type(revision) is int and revision > 0):
            parser.error("labels require checkId, taskId, expected (correct or incorrect) and an optional resultRev")
        if (checkId, taskId) in seen:
            parser.error("duplicate checkId and taskId")
        seen.add((checkId, taskId))
        if checkId not in checks:
            checks[checkId] = fetchCheck(args.cabinet_url, checkId)
        observed = decision(checks[checkId], taskId, revision)
        if observed != expected:
            failures.append({"checkId": checkId, "taskId": taskId, "expected": expected, "observed": observed})
        if observed in ("manual", "unavailable"):
            counts[observed] += 1
        else:
            counts["matched" if observed == expected else "wrong"] += 1
    total = len(cases)
    correct = Fraction(counts["matched"], total)
    automatic = Fraction(counts["matched"] + counts["wrong"], total)
    print(json.dumps({"total": total, **counts, "correctAutomaticPercent": round(100 * float(correct), 3),
                      "automaticPercent": round(100 * float(automatic), 3), "failures": failures}, ensure_ascii=False))
    return 0 if correct >= Fraction(99, 100) else 1


if __name__ == "__main__":
    raise SystemExit(main())
