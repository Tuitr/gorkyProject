"""Exercise the live cabinet → Redis → grading input and S3 handoff."""

import hashlib
import os
import re
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import cast

import httpx
from docx import Document
from redis import Redis
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from cabinet.assignments import AssignmentRecord
from cabinet.attempts import AttemptRecord, CheckRecord
from cabinet.auth.passwords import Passwords
from cabinet.auth.tables import TeacherRecord
from cabinet.classes import ClassRecord
from cabinet.documents import buildS3Client
from cabinet.main import COOKIE, buildAccounts
from cabinet.outbox import OutboxRecord
from cabinet.settings import Settings
from cabinet.uploads import UploadFileRecord, UploadRecord
from contracts.v1.events.checks import DATA_FIELD, STREAM_NAME
from grading.intake import AssignmentPublished, AttemptSubmitted, EventRejected, IncomingEvent, readEvent
from shared.buckets import SOURCES_BUCKET
from shared.clock import SystemClock


@dataclass(frozen=True)
class Delivery:
    entryId: str | bytes
    data: bytes
    event: IncomingEvent


def makeDocx(*paragraphs: str) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def collectDeliveries(
    redis: Redis, cursor: str | bytes, traces: set[str], deliveries: dict[str, Delivery],
) -> None:
    deadline = time.monotonic() + 10
    while traces.difference(deliveries) and time.monotonic() < deadline:
        # With RESP2, redis-py returns stream/entry pairs. Its shared return type also covers RESP3.
        batches = cast(
            list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]],
            redis.xread({STREAM_NAME: cursor}, count=100, block=250),
        )
        for _, entries in batches:
            for entryId, fields in entries:
                cursor = entryId
                raw = fields.get(DATA_FIELD.encode())
                if not isinstance(raw, bytes):
                    continue
                try:
                    event = readEvent(raw)
                except EventRejected:
                    continue
                if event.traceId in traces:
                    deliveries[event.traceId] = Delivery(entryId, raw, event)
    assert deliveries.keys() == traces, f"Missing checks.v1 events for trace IDs: {sorted(traces.difference(deliveries))}"


def removeHandoffRecords(
    sessions: sessionmaker[Session], teacherId: str, traces: set[str],
) -> set[tuple[str, str]]:
    with sessions.begin() as db:
        classes = select(ClassRecord.classId).where(ClassRecord.teacherId == teacherId)
        assignments = select(AssignmentRecord.assignmentId).where(AssignmentRecord.classId.in_(classes))
        uploads = select(UploadRecord.uploadId).where(UploadRecord.assignmentId.in_(assignments))
        uploadRefs = db.execute(
            select(UploadFileRecord.bucket, UploadFileRecord.objectKey).where(UploadFileRecord.uploadId.in_(uploads))
        )
        variantRefs = db.execute(
            select(AssignmentRecord.bucket, AssignmentRecord.objectKey).where(AssignmentRecord.classId.in_(classes))
        )
        refs = {(bucket, key) for bucket, key in uploadRefs}
        refs.update((bucket, key) for bucket, key in variantRefs)
        db.execute(delete(OutboxRecord).where(OutboxRecord.data["traceId"].astext.in_(traces)))
        db.execute(delete(UploadRecord).where(UploadRecord.assignmentId.in_(assignments)))
        db.execute(delete(CheckRecord).where(CheckRecord.assignmentId.in_(assignments)))
        db.execute(delete(AttemptRecord).where(AttemptRecord.assignmentId.in_(assignments)))
        db.execute(delete(AssignmentRecord).where(AssignmentRecord.classId.in_(classes)))
        db.execute(delete(TeacherRecord).where(TeacherRecord.teacherId == teacherId))
    return refs


def testCabinetEventsCarryReadableDocuments(adminEngine: Engine, tmp_path: Path) -> None:
    # adminEngine applies the regular test migrations before the live HTTP calls.
    settings = Settings.fromEnv()
    port = os.environ.get("CABINET_PORT", "18080")
    runId = uuid.uuid4().hex
    variantTrace, attemptTrace = f"handoff-{runId}-variant", f"handoff-{runId}-attempt"
    traces = {variantTrace, attemptTrace}
    loginName, password = f"handoff.{runId}", uuid.uuid4().hex
    studentName = "Тестов Павел"
    variant = makeDocx("Синтетический вариант", "1. Решите уравнение x + 2 = 5.")
    submission = makeDocx("Синтетическая работа", f"Ученик: {studentName}", "1. x = 5 − 2 = 3.")
    gradingSettings = replace(
        settings,
        s3AccessKeyId=os.environ.get("GRADING_S3_ACCESS_KEY_ID", "gorky-grading"),
        s3SecretAccessKey=os.environ.get("GRADING_S3_SECRET_ACCESS_KEY", "gorky-grading-secret"),
    )
    engine = create_engine(settings.databaseUrl)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        teacher = buildAccounts(settings, sessions, Passwords(), SystemClock()).create(
            loginName, "Синтетический учитель", password,
        )
        with (
            Redis.from_url(settings.redisUrl, protocol=2, socket_connect_timeout=3, socket_timeout=3) as redis,
            httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as api,
            closing(buildS3Client(gradingSettings)) as gradingStorage,
            closing(buildS3Client(settings)) as cabinetStorage,
        ):
            deliveries: dict[str, Delivery] = {}
            try:
                tail = redis.xrevrange(STREAM_NAME, count=1)
                cursor = tail[0][0] if tail else "0-0"
                login = api.post("/v1/sessions", json={"login": loginName, "password": password})
                assert login.status_code == 200, login.text
                # The dev server uses HTTP, while the teacher cookie normally requires HTTPS.
                api.headers["Cookie"] = f"{COOKIE}={login.cookies[COOKIE]}"
                classResponse = api.post("/v1/classes", json={"name": "Синтетический 8А"})
                assert classResponse.status_code == 201, classResponse.text
                classPath = f"/v1/classes/{classResponse.json()['classId']}"
                studentResponse = api.post(f"{classPath}/students", json={"name": studentName})
                assert studentResponse.status_code == 201, studentResponse.text
                published = api.post(
                    f"{classPath}/assignments",
                    headers={"X-Trace-Id": variantTrace},
                    data={"requirements": '[{"taskNumber":"1","text":"Покажите ход решения"}]'},
                    files={"file": ("variant.docx", variant)},
                )
                assert published.status_code == 201, published.text
                assignment = published.json()
                uploaded = api.post(
                    f"/v1/assignments/{assignment['assignmentId']}/uploads",
                    headers={"X-Trace-Id": attemptTrace},
                    data={"assignmentRev": str(assignment["assignmentRev"])},
                    files={"file": ("testov-pavel.docx", submission)},
                )
                assert uploaded.status_code == 202, uploaded.text
                progress = api.get(f"/v1/uploads/{uploaded.json()['uploadId']}")
                assert progress.status_code == 200, progress.text
                confirmed = progress.json()["files"][0]
                assert confirmed["status"] == "bound"

                collectDeliveries(redis, cursor, traces, deliveries)
                variantEvent = deliveries[variantTrace].event
                attemptEvent = deliveries[attemptTrace].event
                assert isinstance(variantEvent, AssignmentPublished)
                assert isinstance(attemptEvent, AttemptSubmitted)
                assert variantEvent.payload.requirements[0].text == "Покажите ход решения"
                assert attemptEvent.payload.studentId == studentResponse.json()["studentId"]
                assert attemptEvent.payload.attemptId == confirmed["attemptId"]
                assert attemptEvent.payload.checkId == confirmed["checkId"]
                for trace, original, filename in (
                    (variantTrace, variant, "variant.docx"), (attemptTrace, submission, "submission.docx"),
                ):
                    delivery = deliveries[trace]
                    payload = delivery.event.payload
                    assert payload.assignmentId == assignment["assignmentId"]
                    assert payload.assignmentRev == assignment["assignmentRev"]
                    ref = payload.docRef
                    assert ref.bucket == SOURCES_BUCKET
                    assert re.fullmatch(r"(assignments|uploads)/[0-9a-f/-]+\.docx", ref.key)
                    with closing(gradingStorage.get_object(Bucket=ref.bucket, Key=ref.key)["Body"]) as body:
                        actual = body.read()
                    assert actual == original
                    assert hashlib.sha256(actual).hexdigest() == ref.sha256
                    assert Document(BytesIO(actual)).paragraphs
                    (tmp_path / filename).write_bytes(actual)
                    (tmp_path / f"{delivery.event.type}.json").write_bytes(delivery.data)
            finally:
                for bucket, key in removeHandoffRecords(sessions, teacher.teacherId, traces):
                    cabinetStorage.delete_object(Bucket=bucket, Key=key)
                if deliveries:
                    redis.xdel(STREAM_NAME, *(delivery.entryId for delivery in deliveries.values()))
    finally:
        engine.dispose()
