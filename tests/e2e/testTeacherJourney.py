"""E1–E5: one teacher journey through the public /v1 API of the full Compose stand.

Everything a teacher does goes over HTTP to the cabinet; the cabinet, outbox publisher, Redis, grading worker,
reference registry, model gateway (recorded fixtures, never the paid model), PostgreSQL and S3 are the real
services of deploy/compose.yaml. Inputs are the published open set mini.v1, variant e2e1: two synthetic
students, one task, both answers correct; the second solution has one `sign` issue at s2 that the teacher
removes. The e2eStand fixture (conftest.py) starts an isolated Compose project with profile "full" and removes
it afterwards:

    uv run pytest tests/e2e

On a failure the message names the step, the last HTTP status, traceId, checkId and the observed state,
never a token, a cookie or the text of a solution.
"""

import hashlib
import json
import os
import re
import time
import uuid
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest

from contracts.v1.cabinet.uploads import UploadProgress
from contracts.v1.dataset.manifest import DatasetCase, DatasetManifest

RELEASE = Path(__file__).parents[2] / "tools" / "dataset" / "releases" / "mini.v1"
VARIANT = "assignments/e2e1.docx"
BUNDLE = "bundles/e2e1.zip"
COOKIE = "__Host-cabinet"
WAIT_S = float(os.environ.get("E2E_WAIT_S", "120"))
TERMINAL = {"failed", "needs_review"}
SECRET = re.compile(r"(?i)(bearer\s+|cookie[=:]\s*|__host-cabinet=|password[\"'=:\s]+)[^\s,;\"']+")


def safeText(text: str) -> str:
    """Diagnostics for CI logs: tokens, cookies and passwords are cut out."""
    return SECRET.sub(lambda match: match.group(1) + "***", text)


@dataclass
class Journey:
    """The public API client of one teacher, with what a failure report needs and nothing more."""

    api: httpx.Client
    step: str = "start"
    lastStatus: int | None = None
    lastTraceId: str | None = None
    checkId: str | None = None
    state: str | None = None
    seen: list[str] = field(default_factory=list)

    def at(self, step: str, checkId: str | None = None) -> None:
        self.step, self.checkId, self.state = step, checkId, None

    def call(self, method: str, path: str, **options: Any) -> httpx.Response:
        traceId = f"e2e-{uuid.uuid4().hex[:12]}"
        headers = {"X-Trace-Id": traceId, **options.pop("headers", {})}
        response = self.api.request(method, path, headers=headers, **options)
        self.lastStatus, self.lastTraceId = response.status_code, response.headers.get("X-Trace-Id", traceId)
        return response

    def expect(self, status: int, method: str, path: str, **options: Any) -> Any:
        response = self.call(method, path, **options)
        if response.status_code != status:
            self.fail(f"expected HTTP {status}, got {response.status_code}: {response.text[:300]}")
        return response.json() if response.headers.get("content-type", "").startswith("application/json") else response

    def fail(self, reason: str) -> NoReturn:
        pytest.fail(diagnosis(self, reason), pytrace=False)

    def until[T](self, what: str, probe: Callable[[], T | None]) -> T:
        """Bounded poll: no fixed sleep, no endless loop."""
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            value = probe()
            if value is not None:
                return value
            time.sleep(0.5)
        self.fail(f"timed out after {WAIT_S:.0f} s waiting for {what}")


def diagnosis(journey: Journey, reason: str) -> str:
    parts = [
        f"E2E step {journey.step} failed: {reason}",
        f"last HTTP status {journey.lastStatus}, traceId {journey.lastTraceId}",
        f"checkId {journey.checkId}, observed state {journey.state}",
        f"states seen: {', '.join(journey.seen[-5:]) or '-'}",
        "logs: docker compose -f deploy/compose.yaml -p {} logs cabinet grading grading-worker gateway references "
        "(E2E_KEEP=1 keeps the stand)".format(os.environ.get("E2E_COMPOSE_PROJECT", "<project>")),
    ]
    return safeText("\n".join(parts))


def testDiagnosisNamesTheStepAndHidesSecrets() -> None:
    journey = Journey(httpx.Client(), step="E3.1", lastStatus=504, lastTraceId="trace-1", checkId="c-1", state="running")
    journey.seen = ["queued", "running"]

    text = diagnosis(journey, "timeout; Cookie: __Host-cabinet=s3cr3t-session, Authorization: Bearer tok-123")

    assert "E2E step E3.1 failed" in text and "HTTP status 504" in text and "trace-1" in text and "c-1" in text
    assert "running" in text
    assert "s3cr3t-session" not in text and "tok-123" not in text


@dataclass(frozen=True)
class OpenSet:
    """E1: the inputs and the explicit expectations of the scenario, all from the published manifest."""

    manifest: DatasetManifest
    variant: bytes
    bundle: bytes
    cases: dict[str, DatasetCase]  # studentName -> case
    files: dict[str, bytes]  # studentName -> the submission DOCX, byte for byte the ZIP entry

    @property
    def clean(self) -> DatasetCase:
        return next(case for case in self.cases.values() if not case.expectedIssues)

    @property
    def flagged(self) -> DatasetCase:
        return next(case for case in self.cases.values() if case.expectedIssues)


def readOpenSet() -> OpenSet:
    manifest = DatasetManifest.model_validate_json((RELEASE / "manifest.json").read_bytes())
    cases = [case for case in manifest.cases if case.split == "e2e" and case.assignmentRef.file == VARIANT]
    bundleRef = next(bundle for bundle in manifest.bundles if bundle.file == BUNDLE)
    bundle = (RELEASE / BUNDLE).read_bytes()
    assert hashlib.sha256(bundle).hexdigest() == bundleRef.sha256, f"{manifest.datasetVersion}: {BUNDLE} changed"
    with zipfile.ZipFile(BytesIO(bundle)) as archive:
        entries = {entry.name: archive.read(entry.name) for entry in bundleRef.entries}
        assert "manifest.json" not in archive.namelist()
    files = {}
    for case in cases:
        entry = next(item.name for item in bundleRef.entries if item.file == case.submissionRef.file)
        single = (RELEASE / case.submissionRef.file).read_bytes()
        assert hashlib.sha256(single).hexdigest() == hashlib.sha256(entries[entry]).hexdigest() == case.submissionRef.sha256, (
            f"{manifest.datasetVersion} {case.caseId}: the separate DOCX must equal its ZIP entry"
        )
        files[case.submissionRef.studentName] = single
    return OpenSet(manifest, (RELEASE / VARIANT).read_bytes(), bundle,
                   {case.submissionRef.studentName: case for case in cases}, files)


def testOpenSetFitsTheScenario() -> None:
    openSet = readOpenSet()

    assert openSet.manifest.datasetVersion == "mini.v1"
    assert len(openSet.cases) == 2 and len({case.assignmentRef.taskNumber for case in openSet.cases.values()}) == 1
    assert len(set(openSet.files.values())) == 2, "two different works"
    assert all(case.expectedVerdict.answer == "correct" for case in openSet.cases.values())
    (issue,) = openSet.flagged.expectedIssues
    assert (issue.stepId, issue.category) == ("s2", "sign")
    assert openSet.clean.expectedIssues == []


@contextmanager
def cabinet(baseUrl: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=baseUrl, trust_env=False, timeout=30) as client:
        try:
            client.get("/ready").raise_for_status()
        except httpx.HTTPError:
            pytest.fail(f"The cabinet at {baseUrl} is not ready", pytrace=False)
        yield client


def signIn(journey: Journey) -> None:
    credentials = {"email": f"e2e.{uuid.uuid4().hex[:10]}@example.org", "password": f"pw-{uuid.uuid4().hex}"}
    journey.expect(201, "POST", "/v1/teachers/register", json={**credentials, "displayName": "Учитель E2E"})
    response = journey.call("POST", "/v1/sessions", json=credentials)
    if response.status_code != 200 or COOKIE not in response.cookies:
        journey.fail(f"sign in returned HTTP {response.status_code}")
    # The stand serves HTTP; the __Host- cookie is normally sent over HTTPS only.
    journey.api.headers["Cookie"] = f"{COOKIE}={response.cookies[COOKIE]}"


def waitForBindings(journey: Journey, uploadId: str, students: dict[str, str], expected: int) -> dict[str, tuple[str, str]]:
    """studentId -> (attemptId, checkId) once every file is bound; confirms a suggested match when asked."""

    def probe() -> dict[str, tuple[str, str]] | None:
        progress = UploadProgress.model_validate(journey.expect(200, "GET", f"/v1/uploads/{uploadId}"))
        journey.seen.append(",".join(sorted(item.status for item in progress.files)))
        for item in progress.files:
            if item.status == "rejected":
                journey.fail(f"file {item.originalName} rejected: {item.reason}")
            if item.status == "needs_confirmation" and len(item.candidates) == 1:
                journey.expect(200, "POST", f"/v1/uploads/{uploadId}/bindings",
                               json={"fileId": item.fileId, "studentId": item.candidates[0].studentId})
        bound = {item.studentId: (item.attemptId, item.checkId) for item in progress.files
                 if item.status == "bound" and item.studentId and item.attemptId and item.checkId}
        return bound if len(bound) == expected == len(progress.files) else None

    return journey.until(f"bindings of upload {uploadId}", probe)


def waitForCheck(journey: Journey, checkId: str, done: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    def probe() -> dict[str, Any] | None:
        check: dict[str, Any] = journey.expect(200, "GET", f"/v1/checks/{checkId}")
        journey.state = check["state"]
        if not journey.seen or journey.seen[-1] != check["state"]:
            journey.seen.append(check["state"])
        if check["state"] in TERMINAL and "taskResults" not in check:
            journey.fail(f"the check ended as {check['state']} ({check.get('reason')})")
        return check if done(check) else None

    return journey.until(f"check {checkId}", probe)


def reportOf(journey: Journey, assignmentId: str, rev: int) -> dict[str, Any]:
    report: dict[str, Any] = journey.expect(200, "GET", f"/v1/assignments/{assignmentId}/report", params={"rev": rev})
    return report


def taskOf(report: dict[str, Any]) -> dict[str, Any]:
    task: dict[str, Any]
    (task,) = report["tasks"]
    return task


def affected(task: dict[str, Any], category: str) -> int:
    count: int = task["issuesByCategory"].get(category, 0)
    return count


@pytest.mark.e2e
def testTeacherChecksCorrectsAndDownloadsFeedback(e2eStand: str) -> None:
    openSet = readOpenSet()
    clean, flagged = openSet.clean, openSet.flagged
    (expectedIssue,) = flagged.expectedIssues
    with cabinet(e2eStand) as client:
        journey = Journey(client)

        journey.at("E2.1")
        signIn(journey)
        classId = journey.expect(201, "POST", "/v1/classes", json={"name": "E2E 9Б"})["classId"]
        students = {}
        for name in openSet.cases:
            students[name] = journey.expect(201, "POST", f"/v1/classes/{classId}/students", json={"name": name})["studentId"]
        listed = journey.expect(200, "GET", f"/v1/classes/{classId}/students")
        assert {item["studentId"] for item in listed} == set(students.values())

        journey.at("E2.2")
        version = journey.expect(201, "POST", f"/v1/classes/{classId}/assignments",
                                 data={"requirements": "[]"}, files={"file": ("e2e1.docx", openSet.variant)})
        assignmentId, assignmentRev = version["assignmentId"], version["assignmentRev"]
        upload = journey.expect(202, "POST", f"/v1/assignments/{assignmentId}/uploads",
                                data={"assignmentRev": str(assignmentRev)}, files={"file": ("e2e1.zip", openSet.bundle)})
        pairs = waitForBindings(journey, upload["uploadId"], students, expected=2)
        assert set(pairs) == set(students.values())
        cleanId, flaggedId = students[clean.submissionRef.studentName], students[flagged.submissionRef.studentName]

        journey.at("E2.3")
        repeat = journey.expect(202, "POST", f"/v1/assignments/{assignmentId}/uploads",
                                data={"assignmentRev": str(assignmentRev)},
                                files={"file": ("again.docx", openSet.files[clean.submissionRef.studentName])})
        assert repeat["uploadId"] != upload["uploadId"]
        again = waitForBindings(journey, repeat["uploadId"], students, expected=1)
        assert again == {cleanId: pairs[cleanId]}, "a byte-identical upload reuses the attempt and the check"

        journey.at("E3.1")
        checks = {}
        for studentId, (_, checkId) in pairs.items():
            journey.at("E3.1", checkId)
            checks[studentId] = waitForCheck(journey, checkId, lambda check: check["state"] == "ready")

        journey.at("E3.1", "references")

        def preparedReferences() -> dict[str, Any] | None:
            shown: dict[str, Any] = journey.expect(200, "GET", f"/v1/assignments/{assignmentId}/references",
                                                   params={"rev": assignmentRev})
            return shown if shown["state"] in ("ready", "needs_review", "failed") else None

        shownReferences = journey.until("the reference answers", preparedReferences)
        assert [(task["state"], task["answer"]) for task in shownReferences["tasks"]] == [("ready", "13")]

        journey.at("E3.2")
        cleanCheck, flaggedCheck = checks[cleanId], checks[flaggedId]
        for check, case in ((cleanCheck, clean), (flaggedCheck, flagged)):
            (result,) = check["taskResults"]
            assert result["answer"] == case.expectedVerdict.answer == "correct", case.caseId
            assert result["reasoning"] == case.expectedVerdict.reasoning, case.caseId
            assert check["overallReview"].strip(), case.caseId
        assert cleanCheck["taskResults"][0]["issues"] == []
        matches = [issue for issue in flaggedCheck["taskResults"][0]["issues"]
                   if (issue["stepId"], issue["category"]) == (expectedIssue.stepId, expectedIssue.category)]
        assert len(matches) == 1 == len(flaggedCheck["taskResults"][0]["issues"])
        issueId, taskId = matches[0]["issueId"], flaggedCheck["taskResults"][0]["taskId"]
        baseRev = flaggedCheck["resultRev"]

        journey.at("E3.3")
        before = reportOf(journey, assignmentId, assignmentRev)
        task = taskOf(before)
        assert before["submittedCount"] == 2 and before["notSubmitted"] == before["waiting"] == []
        assert (task["correct"], task["unchecked"], task["correctShare"]) == (2, 0, 1)
        assert affected(task, expectedIssue.category) == 1

        journey.at("E4.1", pairs[flaggedId][1])
        flaggedCheckId = pairs[flaggedId][1]
        edit = {"baseRev": baseRev, "editId": f"edit-{uuid.uuid4().hex}", "changes": [
            {"type": "remove_issue", "issueId": issueId},
            {"type": "update_verdict", "taskId": taskId, "reasoning": "valid"},
        ]}
        receipt = journey.expect(200, "PATCH", f"/v1/checks/{flaggedCheckId}/review", json=edit)
        assert (receipt["checkId"], receipt["editId"]) == (flaggedCheckId, edit["editId"])
        newRev = receipt["resultRev"]
        assert newRev == baseRev + 1
        corrected = waitForCheck(journey, flaggedCheckId, lambda check: check.get("resultRev") == newRev)
        (result,) = corrected["taskResults"]
        assert (result["answer"], result["reasoning"], result["issues"]) == ("correct", "valid", [])

        journey.at("E4.2", flaggedCheckId)
        assert journey.expect(200, "PATCH", f"/v1/checks/{flaggedCheckId}/review", json=edit) == receipt
        assert journey.expect(200, "GET", f"/v1/checks/{flaggedCheckId}")["resultRev"] == newRev

        journey.at("E4.3")

        def correctedReport() -> dict[str, Any] | None:
            report = reportOf(journey, assignmentId, assignmentRev)
            row = next(item for item in report["results"] if item["studentId"] == flaggedId)
            return report if row["resultRev"] == newRev else None

        after = journey.until("the report with the teacher edit", correctedReport)
        task = taskOf(after)
        assert task["correct"] == 2 and affected(task, expectedIssue.category) == 0
        assert {row["studentId"] for row in after["results"]} == {cleanId, flaggedId}
        assert corrected["overallReview"] != flaggedCheck["overallReview"] or not matches[0]["comment"] in corrected["overallReview"]

        journey.at("E5.1")
        published = {}
        for studentId, rev in ((cleanId, cleanCheck["resultRev"]), (flaggedId, newRev)):
            checkId = pairs[studentId][1]
            journey.at("E5.1", checkId)
            answer = journey.expect(200, "POST", f"/v1/checks/{checkId}/publish", json={"resultRev": rev})
            assert (answer["checkId"], answer["resultRev"]) == (checkId, rev)
            assert journey.expect(200, "GET", f"/v1/checks/{checkId}")["publishedResultRev"] == rev
            published[studentId] = rev

        journey.at("E5.2")
        texts = {}
        for studentId, rev in published.items():
            checkId = pairs[studentId][1]
            journey.at("E5.2", checkId)
            response = journey.call("GET", f"/v1/checks/{checkId}/markdown", params={"rev": rev})
            if response.status_code != 200 or not response.headers["content-type"].startswith("text/markdown"):
                journey.fail(f"Markdown download returned {response.status_code} {response.headers.get('content-type')}")
            texts[studentId] = response.text
            assert "<html" not in response.text.lower() and response.text.strip()
        flaggedMarker, cleanMarker = r"\pm 13", r"\sqrt{169} = 13$"
        assert flaggedMarker in texts[flaggedId] and flaggedMarker not in texts[cleanId]
        assert cleanMarker in texts[cleanId]
        assert matches[0]["comment"] not in texts[flaggedId], "the removed issue is not in the published feedback"

        if recordPath := os.environ.get("E2E_RECORD"):
            # E6.3: the traceable chain uploadId -> attempt/check -> resultRev -> Markdown, without any secret.
            record = {
                "datasetVersion": openSet.manifest.datasetVersion,
                "assignment": {"assignmentId": assignmentId, "assignmentRev": assignmentRev},
                "uploadId": upload["uploadId"], "repeatUploadId": repeat["uploadId"],
                "students": [
                    {
                        "caseId": case.caseId, "studentId": studentId, "attemptId": pairs[studentId][0],
                        "checkId": pairs[studentId][1], "firstResultRev": checks[studentId]["resultRev"],
                        "publishedResultRev": published[studentId],
                        "markdownSha256": hashlib.sha256(texts[studentId].encode("utf-8")).hexdigest(),
                    }
                    for case, studentId in ((clean, cleanId), (flagged, flaggedId))
                ],
                "removedIssue": {"issueId": issueId, "stepId": expectedIssue.stepId, "category": expectedIssue.category},
            }
            Path(recordPath).parent.mkdir(parents=True, exist_ok=True)
            Path(recordPath).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
