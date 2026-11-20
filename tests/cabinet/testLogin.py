"""A2.1: a teacher logs in with POST /v1/sessions; refusals look the same; attempts are limited.

Tests use a cheap Argon2id (time_cost=1, small memory) so they stay fast; the type is the same as in production.
"""

import hashlib
import json
import logging
import os
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import date
from io import BytesIO
from threading import Barrier
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from argon2 import PasswordHasher, Type
from docx import Document
from fastapi.testclient import TestClient
from httpx import MockTransport, Request, Response
from sqlalchemy import Engine, create_engine, event as sqlEvent, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from cabinet import documents, uploads
from cabinet.assignments import AssignmentRecord, storeAssignment
from cabinet.attempts import AttemptRecord, CheckRecord
from cabinet.auth.passwords import Passwords
from cabinet.auth.tables import TeacherRecord
from cabinet.classes import ClassRecord, StudentRecord
from cabinet.cleanupuploads import cleanUpload
from cabinet.database import allMetadata, buildAlembicConfig
from cabinet.documents import buildS3Client
from cabinet.main import buildAccounts, createApp
from cabinet.outbox import EventContext, OutboxRecord
from cabinet.settings import DB_SCHEMA, Settings
from cabinet.uploads import SubmissionDocument, UploadFileRecord, UploadRecord, storeUpload
from contracts.v1.cabinet.sessions import LOGIN_REFUSED_MESSAGE
from contracts.v1.events.checks import AssignmentPublishedEvent, AttemptSubmittedEvent, TeacherRequirement, checksEventAdapter
from shared.buckets import RESULTS_BUCKET, SOURCES_BUCKET
from shared.clock import ManualClock

PASSWORD = "correct horse battery staple"
SETTINGS = Settings(loginMaxFailures=3, clientMaxFailures=6, loginWindowS=600)


class CountingHasher(PasswordHasher):
    """A fast Argon2id hasher that counts verifications."""

    def __init__(self) -> None:
        super().__init__(time_cost=1, memory_cost=8, parallelism=1, type=Type.ID)
        self.verifications = 0

    def verify(self, hash: str | bytes, password: str | bytes) -> Literal[True]:
        self.verifications += 1
        return super().verify(hash, password)


@pytest.fixture(scope="module")
def cabinetEngine(adminEngine: Engine) -> Iterator[Engine]:
    engine = create_engine(Settings.fromEnv().databaseUrl)
    yield engine
    engine.dispose()


@pytest.fixture
def cabinetSessions(cabinetEngine: Engine) -> sessionmaker[Session]:
    with cabinetEngine.begin() as connection:
        tables = (
            "teachers", "sessions", "login_throttle", "classes", "students", "assignments",
            "uploads", "upload_files", "attempts", "attempt_checks", "outbox",
        )
        qualified = ", ".join(f'"{DB_SCHEMA}".{table}' for table in tables)
        connection.execute(text(f"TRUNCATE {qualified}"))
    return sessionmaker(cabinetEngine, expire_on_commit=False)


@pytest.fixture
def hasher() -> CountingHasher:
    return CountingHasher()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def client(cabinetSessions: sessionmaker[Session], hasher: CountingHasher, clock: ManualClock) -> TestClient:
    return TestClient(createApp(SETTINGS, sessionFactory=cabinetSessions, passwords=Passwords(hasher), clock=clock), base_url="https://testserver")


def addTeacher(sessions: sessionmaker[Session], login: str, name: str = "Учитель Синтетический") -> str:
    accounts = buildAccounts(SETTINGS, sessions, Passwords(CountingHasher()), ManualClock())
    return accounts.create(login, name, PASSWORD).teacherId


def login(client: TestClient, email: str, password: str) -> tuple[int, dict[str, object], dict[str, str]]:
    response = client.post("/v1/sessions", json={"email": email, "password": password})
    return response.status_code, response.json(), dict(response.headers)


def testRightCredentialsReturnTheTeacher(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    teacherId = addTeacher(cabinetSessions, "teacher.one@example.org")

    status, body, _ = login(client, "  Teacher.One@Example.org ", PASSWORD)

    assert (status, body) == (200, {"teacherId": teacherId, "displayName": "Учитель Синтетический"})


def testTeacherRegistersAndSignsInWithEmail(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    registered = client.post("/v1/teachers/register", json={
        "email": " Teacher.Signup@Example.org ", "displayName": " Анна Петрова ", "password": PASSWORD,
    })

    assert registered.status_code == 201, registered.text
    teacher = registered.json()
    assert teacher == {"teacherId": teacher["teacherId"], "displayName": "Анна Петрова"}
    assert registered.headers["cache-control"] == "no-store"
    with cabinetSessions() as db:
        stored = db.scalars(select(TeacherRecord)).one()
        assert (stored.teacherId, stored.login) == (teacher["teacherId"], "teacher.signup@example.org")
        assert PasswordHasher().verify(stored.passwordHash, PASSWORD)

    assert login(client, "TEACHER.SIGNUP@example.org", PASSWORD)[:2] == (200, teacher)
    assert client.get("/v1/sessions/current").json() == teacher


def testConcurrentRegistrationsCannotReplaceTheAccount(
    cabinetSessions: sessionmaker[Session], clock: ManualClock,
) -> None:
    app = createApp(SETTINGS, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    ready = Barrier(2)

    def register(email: str, name: str, password: str) -> Response:
        with TestClient(app, base_url="https://testserver") as contender:
            ready.wait(timeout=5)
            response: Response = contender.post("/v1/teachers/register", json={
                "email": email, "displayName": name, "password": password,
            })
            return response

    submissions = (
        ("Teacher.Signup@Example.org", "Первый учитель", "first-test-password"),
        (" teacher.signup@example.org ", "Второй учитель", "second-test-password"),
    )
    with ThreadPoolExecutor(max_workers=2) as workers:
        requests = [workers.submit(register, *submission) for submission in submissions]
        outcomes = [request.result(timeout=10) for request in requests]

    assert sorted(response.status_code for response in outcomes) == [201, 409]
    with cabinetSessions() as db:
        stored = db.scalars(select(TeacherRecord)).one()
        winner = next(index for index, response in enumerate(outcomes) if response.status_code == 201)
        assert outcomes[1 - winner].json()["code"] == "conflict"
        assert stored.displayName == submissions[winner][1]
        assert PasswordHasher().verify(stored.passwordHash, submissions[winner][2])


@pytest.mark.parametrize(
    "invalid", [{"email": "not-an-email"}, {"displayName": "   "}, {"password": "short"}, {"teacherId": "forged"}],
    ids=["email", "name", "password", "extra-field"],
)
def testInvalidRegistrationDoesNotCreateAnAccount(
    client: TestClient, cabinetSessions: sessionmaker[Session], invalid: dict[str, str],
) -> None:
    response = client.post("/v1/teachers/register", json={
        "email": "teacher.signup@example.org", "displayName": "Анна Петрова", "password": PASSWORD, **invalid,
    })

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_input"
    assert PASSWORD not in response.text
    with cabinetSessions() as db:
        assert db.scalars(select(TeacherRecord)).all() == []


def testUnknownLoginAndWrongPasswordAreRefusedAlike(
    client: TestClient, cabinetSessions: sessionmaker[Session], hasher: CountingHasher
) -> None:
    addTeacher(cabinetSessions, "teacher.one@example.org")

    refusals = []
    for name, password in (("teacher.one@example.org", "wrong password"), ("nobody.here@example.org", PASSWORD)):
        before = hasher.verifications
        status, body, headers = login(client, name, password)
        headerNames = sorted(set(headers) - {"x-trace-id"})
        refusals.append((status, body | {"traceId": "-"}, headerNames, hasher.verifications - before))

    assert refusals[0] == refusals[1]
    assert refusals[0][:2] == (
        401,
        {"code": "unauthorized", "message": LOGIN_REFUSED_MESSAGE, "retryable": False, "traceId": "-"},
    )
    assert refusals[0][3] == 1, "an unknown login still runs one Argon2 verification"


def testSamePasswordGivesDifferentArgon2idHashes(cabinetSessions: sessionmaker[Session]) -> None:
    addTeacher(cabinetSessions, "teacher.one@example.org")
    addTeacher(cabinetSessions, "teacher.two@example.org")
    with cabinetSessions() as session:
        hashes = list(session.scalars(select(TeacherRecord.passwordHash)))

    assert len(set(hashes)) == 2
    assert all(stored.startswith("$argon2id$") for stored in hashes)
    assert len({stored.split("$")[4] for stored in hashes}) == 2, "each hash has its own salt"
    assert all(PasswordHasher().verify(stored, PASSWORD) for stored in hashes), "argon2-cffi checks the stored hash"


def testPasswordAndHashNeverLeaveTheService(
    client: TestClient, cabinetSessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    addTeacher(cabinetSessions, "teacher.one@example.org")
    with cabinetSessions() as session:
        stored = session.scalars(select(TeacherRecord.passwordHash)).one()

    caplog.set_level(logging.DEBUG)
    texts = [client.post("/v1/sessions", json={"email": "teacher.one@example.org", "password": p}).text for p in (PASSWORD, "bad")]

    for secret in (PASSWORD, stored, stored.split("$")[-1]):
        assert all(secret not in answer for answer in texts)
        assert secret not in caplog.text


def testRepeatedFailuresBlockTheLoginEvenForTheRightPassword(
    client: TestClient, cabinetSessions: sessionmaker[Session], clock: ManualClock
) -> None:
    addTeacher(cabinetSessions, "teacher.one@example.org")
    statuses = [login(client, "teacher.one@example.org", "wrong")[0] for _ in range(SETTINGS.loginMaxFailures)]

    status, body, headers = login(client, "teacher.one@example.org", PASSWORD)

    assert statuses == [401] * SETTINGS.loginMaxFailures
    assert (status, body["code"], body["retryable"]) == (429, "rate_limited", True)
    assert int(headers["retry-after"]) == SETTINGS.loginWindowS
    clock.advance(SETTINGS.loginWindowS)
    assert login(client, "teacher.one@example.org", PASSWORD)[0] == 200


def testUnknownLoginIsBlockedTheSameWay(client: TestClient) -> None:
    for _ in range(SETTINGS.loginMaxFailures):
        login(client, "nobody.here@example.org", "guess")

    assert login(client, "nobody.here@example.org", "guess")[0] == 429


def testSuccessClearsTheFailuresOfTheLogin(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    addTeacher(cabinetSessions, "teacher.one@example.org")
    for _ in range(SETTINGS.loginMaxFailures - 1):
        login(client, "teacher.one@example.org", "wrong")
    assert login(client, "teacher.one@example.org", PASSWORD)[0] == 200

    statuses = [login(client, "teacher.one@example.org", "wrong")[0] for _ in range(SETTINGS.loginMaxFailures - 1)]

    assert statuses == [401] * (SETTINGS.loginMaxFailures - 1)


def testOneClientTryingManyLoginsIsBlocked(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    addTeacher(cabinetSessions, "teacher.one@example.org")
    for number in range(SETTINGS.clientMaxFailures):
        assert login(client, f"guess{number}@example.org", "guess")[0] == 401

    assert login(client, "teacher.one@example.org", PASSWORD)[0] == 429


def testThrottleStoresNoLoginsOrAddresses(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    login(client, "mistyped.login@example.org", "guess")
    with cabinetSessions() as session:
        keys = list(session.execute(text(f'SELECT throttle_key FROM "{DB_SCHEMA}".login_throttle')).scalars())

    assert len(keys) == 2
    assert not any("mistyped" in key or "testclient" in key for key in keys)


@pytest.mark.parametrize(
    "body", [
        {"email": "teacher.one@example.org"},
        {"email": "teacher.one", "password": "x"},
        {"email": "teacher.one@example.org", "password": "x", "role": "admin"},
        {"login": "teacher.one@example.org", "password": "x"},
    ],
)
def testMalformedRequestIsInvalidInput(client: TestClient, body: dict[str, str]) -> None:
    response = client.post("/v1/sessions", json=body)

    assert (response.status_code, response.json()["code"]) == (422, "invalid_input")


def testTablesMatchMigrations(cabinetEngine: Engine) -> None:
    def onlyOwnSchema(name: str | None, kind: str, parent: object) -> bool:
        return kind != "schema" or name == DB_SCHEMA

    with cabinetEngine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"include_schemas": True, "include_name": onlyOwnSchema, "version_table_schema": DB_SCHEMA}
        )
        assert compare_metadata(context, allMetadata()) == []


def testStandTeacherIsCreatedOnce(
    cabinetSessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cabinet import seedteacher

    monkeypatch.setenv("CABINET_DEMO_TEACHER_PASSWORD", PASSWORD)
    monkeypatch.delenv("CABINET_DEMO_TEACHER_EMAIL", raising=False)
    monkeypatch.delenv("CABINET_DEMO_TEACHER_LOGIN", raising=False)

    assert seedteacher.main() == 0
    assert seedteacher.main() == 0

    with cabinetSessions() as session:
        logins = list(session.scalars(select(TeacherRecord.login)))
    assert logins == [seedteacher.DEMO_EMAIL]
    output = capsys.readouterr().out
    assert "Created teacher" in output and "already exists" in output and PASSWORD not in output


def testOldDemoTeacherKeepsItsIdentityWhenLoginChangesToEmail(
    client: TestClient, cabinetSessions: sessionmaker[Session],
) -> None:
    from cabinet import seedteacher

    teacherId = addTeacher(cabinetSessions, "teacher.demo", seedteacher.DEMO_NAME)
    config = buildAlembicConfig(Settings.fromEnv().databaseUrl)
    command.downgrade(config, "0013")
    command.upgrade(config, "head")

    assert login(client, seedteacher.DEMO_EMAIL, PASSWORD)[:2] == (
        200, {"teacherId": teacherId, "displayName": seedteacher.DEMO_NAME},
    )


def testSessionLifetime(client: TestClient, cabinetSessions: sessionmaker[Session], clock: ManualClock) -> None:
    teacherId = addTeacher(cabinetSessions, "teacher.one@example.org")
    path = "/v1/sessions/current"
    assert client.get(path).status_code == 401
    status, _, headers = login(client, "teacher.one@example.org", PASSWORD)
    assert status == 200
    assert all(flag in headers["set-cookie"].lower() for flag in ("secure", "httponly", "samesite=strict"))
    cookie = headers["set-cookie"].split(";", 1)[0]
    assert client.get(path, params={"actorId": "other"}).json()["teacherId"] == teacherId
    assert client.delete(path, headers={"Origin": "https://other.example"}).status_code == 403
    assert client.delete(path).status_code == 204
    assert client.get(path, headers={"Cookie": cookie}).status_code == 401
    assert client.delete(path, headers={"Cookie": cookie}).status_code == 401
    assert login(client, "teacher.one@example.org", PASSWORD)[0] == 200
    clock.advance(8 * 3600)
    assert client.get(path).status_code == 401


def testTeachersCanOnlyManageTheirOwnClasses(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    addTeacher(cabinetSessions, "teacher.one@example.org")
    addTeacher(cabinetSessions, "teacher.two@example.org")
    path = "/v1/classes"
    assert client.get(path).status_code == 401
    assert client.post(path, json={"name": "8А"}).status_code == 401
    assert login(client, "teacher.one@example.org", PASSWORD)[0] == 200

    created = client.post(path, json={"name": " 8А "})
    assert created.status_code == 201
    firstClass = created.json()
    assert firstClass["name"] == "8А"
    assert client.get(path).json() == [firstClass]

    roster = f"{path}/{firstClass['classId']}/students"
    assert client.get(roster).json() == []
    students = [client.post(roster, json={"name": "Саша Иванов"}) for _ in range(2)]
    assert [student.status_code for student in students] == [201, 201]
    studentIds = {student.json()["studentId"] for student in students}
    assert len(studentIds) == 2

    assert login(client, "teacher.two@example.org", PASSWORD)[0] == 200
    assert client.get(path).json() == []
    second = client.post(path, json={"name": "8Б"})
    assert second.status_code == 201
    assert client.get(path).json() == [second.json()]
    assert client.get(roster).status_code == 404
    assert client.post(roster, json={"name": "Новый ученик"}).status_code == 404
    firstClassPath = f"{path}/{firstClass['classId']}"
    targetStudent = students[0].json()["studentId"]
    assert client.delete(firstClassPath).status_code == 404
    assert client.delete(f"{roster}/{targetStudent}").status_code == 404

    assert login(client, "teacher.one@example.org", PASSWORD)[0] == 200
    assert client.get(path).json() == [firstClass]
    assert client.get(f"{path}/{second.json()['classId']}/students").status_code == 404
    assert client.post(roster, json={"name": "   "}).status_code == 422
    assert client.post(roster, json={"name": "Саша", "classId": second.json()["classId"]}).status_code == 422
    assert {student["studentId"] for student in client.get(roster).json()} == studentIds
    assert client.delete(f"{roster}/{targetStudent}", headers={"Origin": "https://other.example.org"}).status_code == 403
    assert client.delete(f"{roster}/{targetStudent}").status_code == 204
    assert client.delete(f"{roster}/{targetStudent}").status_code == 404
    assert {student["studentId"] for student in client.get(roster).json()} == studentIds - {targetStudent}
    assert client.delete(firstClassPath).status_code == 204
    assert client.get(path).json() == []
    assert client.get(roster).status_code == 404
    assert client.post(roster, json={"name": "Новый ученик"}).status_code == 404
    assert client.delete(firstClassPath).status_code == 404


def assignmentDocx(title: str, *signatures: str) -> bytes:
    document = Document()
    document.add_paragraph(title)
    for signature in signatures:
        document.add_paragraph(signature)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def testAssignmentUploadPreservesRevisions(cabinetSessions: sessionmaker[Session], clock: ManualClock) -> None:
    addTeacher(cabinetSessions, "teacher.owner@example.org")
    addTeacher(cabinetSessions, "teacher.other@example.org")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    firstDocx = assignmentDocx("Задание 1")
    secondDocx = assignmentDocx("Задание 1. Уточнение")
    initial = [{"text": "Обоснуйте решение", "taskNumber": None}]
    updated = [{"text": "Решите через формулу корней", "taskNumber": "1"}]

    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        created = client.post("/v1/classes", json={"name": "8А"})
        path = f"/v1/classes/{created.json()['classId']}/assignments"

        malformed = client.post(path, data={"requirements": "[]"}, files={"file": ("bad.docx", b"bad")})
        assert malformed.status_code == 422
        first = client.post(
            path, data={"requirements": json.dumps(initial), "assignmentDate": "2025-12-12"},
            files={"file": ("first.docx", firstDocx)},
        )
        assert first.status_code == 201, first.text
        assignmentId = first.json()["assignmentId"]
        assert first.json()["assignmentRev"] == 1
        second = client.post(
            path, data={"assignmentId": assignmentId, "requirements": json.dumps(updated)},
            files={"file": ("second.docx", secondDocx)},
        )
        assert second.status_code == 201, second.text
        assert second.json() == {"assignmentId": assignmentId, "assignmentRev": 2}

        assert login(client, "teacher.other@example.org", PASSWORD)[0] == 200
        denied = client.post(path, data={"requirements": "[]"}, files={"file": ("first.docx", firstDocx)})
        assert denied.status_code == 404
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        otherClass = client.post("/v1/classes", json={"name": "8Б"})
        otherPath = f"/v1/classes/{otherClass.json()['classId']}/assignments"
        deniedRevision = client.post(
            otherPath, data={"assignmentId": assignmentId, "requirements": "[]"},
            files={"file": ("first.docx", firstDocx)},
        )
        assert deniedRevision.status_code == 404

        failingSettings = replace(settings, s3AccessKeyId="unrecognized-test-credential")
        failingApp = createApp(
            failingSettings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock,
        )
        with TestClient(failingApp, base_url="https://testserver", cookies=client.cookies) as failingClient:
            failed = failingClient.post(
                path, data={"assignmentId": assignmentId, "requirements": "[]"},
                files={"file": ("first.docx", firstDocx)},
            )
            assert failed.status_code == 503

    with cabinetSessions() as db, closing(buildS3Client(settings)) as s3:
        versions = db.scalars(select(AssignmentRecord).order_by(AssignmentRecord.assignmentRev)).all()
        try:
            assert [version.assignmentRev for version in versions] == [1, 2]
            old, new = versions
            assert old.objectKey != new.objectKey
            assert old.requirements == initial
            assert new.requirements == updated
            assert old.assignmentDate == date(2025, 12, 12)
            assert new.assignmentDate == clock.now().date()
            for version, original in zip(versions, (firstDocx, secondDocx), strict=True):
                with closing(s3.get_object(Bucket=version.bucket, Key=version.objectKey)["Body"]) as body:
                    assert body.read() == original
                assert version.sha256 == hashlib.sha256(original).hexdigest()
        finally:
            for version in versions:
                s3.delete_object(Bucket=version.bucket, Key=version.objectKey)


def testIndividualSubmissionUpload(
    cabinetSessions: sessionmaker[Session], clock: ManualClock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    addTeacher(cabinetSessions, "teacher.owner@example.org")
    addTeacher(cabinetSessions, "teacher.other@example.org")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    assignmentDoc = assignmentDocx("Задание")
    studentDoc = assignmentDocx("Решение")
    uploadPart = ("Иванов.docx", studentDoc)

    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        created = client.post("/v1/classes", json={"name": "8А"})
        classPath = f"/v1/classes/{created.json()['classId']}/assignments"
        first = client.post(classPath, data={"requirements": "[]"}, files={"file": ("assignment.docx", assignmentDoc)})
        assert first.status_code == 201, first.text
        assignmentId = first.json()["assignmentId"]
        second = client.post(
            classPath, data={"requirements": "[]", "assignmentId": assignmentId},
            files={"file": ("assignment.docx", assignmentDoc)},
        )
        assert second.status_code == 201, second.text

        path = f"/v1/assignments/{assignmentId}/uploads"
        form = {"assignmentRev": "1"}
        assert client.post(path, data=form, files={"file": ("bad.docx", b"broken")}).status_code == 422
        assert client.post(path, data=form, files=[("file", uploadPart), ("file", uploadPart)]).status_code == 422
        assert client.post(path, data={"assignmentRev": "3"}, files={"file": uploadPart}).status_code == 404
        with monkeypatch.context() as limits:
            limits.setattr(documents, "MAX_DOCX_BYTES", len(studentDoc) - 1)
            assert client.post(path, data=form, files={"file": uploadPart}).status_code == 413

        assert login(client, "teacher.other@example.org", PASSWORD)[0] == 200
        assert client.post(path, data=form, files={"file": uploadPart}).status_code == 404
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        assert client.delete("/v1/sessions/current").status_code == 204
        assert client.post(path, data=form, files={"file": uploadPart}).status_code == 401

        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        failingApp = createApp(
            replace(settings, s3AccessKeyId="unrecognized-test-credential"), sessionFactory=cabinetSessions,
            passwords=Passwords(CountingHasher()), clock=clock,
        )
        with TestClient(failingApp, base_url="https://testserver", cookies=client.cookies) as failingClient:
            assert failingClient.post(path, data=form, files={"file": uploadPart}).status_code == 503
        with cabinetSessions() as db:
            assert db.scalars(select(UploadRecord)).all() == []
            assert db.scalars(select(UploadFileRecord)).all() == []

        accepted = client.post(path, data=form, files={"file": uploadPart})
        assert accepted.status_code == 202, accepted.text
        uploadId = accepted.json()["uploadId"]
        assert accepted.json() == {"uploadId": uploadId}

    with cabinetSessions() as db, closing(buildS3Client(settings)) as s3:
        upload = db.scalars(select(UploadRecord)).one()
        stored = db.scalars(select(UploadFileRecord)).one()
        try:
            assert (upload.uploadId, upload.assignmentId, upload.assignmentRev) == (uploadId, assignmentId, 1)
            assert stored.uploadId == uploadId
            assert stored.originalName == "Иванов.docx"
            assert stored.status == "needs_confirmation"
            assert stored.sha256 == hashlib.sha256(studentDoc).hexdigest()
            with closing(s3.get_object(Bucket=stored.bucket, Key=stored.objectKey)["Body"]) as body:
                assert body.read() == studentDoc
        finally:
            s3.delete_object(Bucket=stored.bucket, Key=stored.objectKey)
            for assignment in db.scalars(select(AssignmentRecord)):
                s3.delete_object(Bucket=assignment.bucket, Key=assignment.objectKey)


def submissionZip(*entries: tuple[str | ZipInfo, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def testArchiveSubmissionUpload(
    cabinetSessions: sessionmaker[Session], clock: ManualClock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    addTeacher(cabinetSessions, "teacher.owner@example.org")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    one = assignmentDocx("Первое решение")
    two = assignmentDocx("Второе решение")
    good = submissionZip(("8А/", b""), ("8А/Иванов.docx", one), ("8Б/Иванов.docx", two))
    symlink = ZipInfo("link.docx")
    symlink.create_system = 3
    symlink.external_attr = 0o120777 << 16
    invalidArchives = [
        submissionZip(("ok.docx", one), (name, two))
        for name in ("../escaped.docx", "/absolute.docx", "C:/drive.docx", "folder\\escape.docx", symlink)
    ]
    invalidArchives += [
        submissionZip(("ok.docx", one), ("nested.zip", good)),
        submissionZip(("ok.docx", one), ("nested.docx", good)),
        submissionZip(("ok.docx", one), ("bad.docx", b"broken")),
        submissionZip(("only-folder/", b"")),
    ]

    with TestClient(app, base_url="https://testserver") as client, closing(buildS3Client(settings)) as s3:
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        created = client.post("/v1/classes", json={"name": "8А"})
        classPath = f"/v1/classes/{created.json()['classId']}/assignments"
        initial = client.post(classPath, data={"requirements": "[]"}, files={"file": ("assignment.docx", one)})
        assert initial.status_code == 201, initial.text
        path = f"/v1/assignments/{initial.json()['assignmentId']}/uploads"
        form = {"assignmentRev": "1"}
        storedBefore = s3.list_objects_v2(Bucket="sources", Prefix="uploads/").get("Contents", [])

        for invalid in invalidArchives:
            refused = client.post(path, data=form, files={"file": ("class.zip", invalid)})
            assert refused.status_code == 422, refused.text
        with monkeypatch.context() as limits:
            limits.setattr(uploads, "MAX_UNPACKED_BYTES", len(one) + len(two) - 1)
            assert client.post(path, data=form, files={"file": ("class.zip", good)}).status_code == 413
        with monkeypatch.context() as limits:
            limits.setattr(uploads, "MAX_DOCX_FILES", 1)
            assert client.post(path, data=form, files={"file": ("class.zip", good)}).status_code == 413

        assert s3.list_objects_v2(Bucket="sources", Prefix="uploads/").get("Contents", []) == storedBefore
        with cabinetSessions() as db:
            assert db.scalars(select(UploadRecord)).all() == []
            assert db.scalars(select(UploadFileRecord)).all() == []

        accepted = client.post(path, data=form, files={"file": ("class.zip", good)})
        assert accepted.status_code == 202, accepted.text
        with cabinetSessions() as db:
            upload = db.scalars(select(UploadRecord)).one()
            records = db.scalars(select(UploadFileRecord)).all()
            try:
                assert upload.uploadId == accepted.json()["uploadId"]
                assert len(records) == 2
                assert {record.uploadId for record in records} == {upload.uploadId}
                assert {record.originalName for record in records} == {"Иванов.docx"}
                assert {record.status for record in records} == {"needs_confirmation"}
                assert len({record.fileId for record in records}) == len({record.objectKey for record in records}) == 2
                saved = set()
                for record in records:
                    with closing(s3.get_object(Bucket=record.bucket, Key=record.objectKey)["Body"]) as body:
                        saved.add(body.read())
                assert saved == {one, two}
            finally:
                for record in records:
                    s3.delete_object(Bucket=record.bucket, Key=record.objectKey)
                for assignment in db.scalars(select(AssignmentRecord)):
                    s3.delete_object(Bucket=assignment.bucket, Key=assignment.objectKey)


def testOnlyOwnerCanReadMixedUploadProgress(
    cabinetSessions: sessionmaker[Session], clock: ManualClock,
) -> None:
    addTeacher(cabinetSessions, "teacher.owner@example.org")
    addTeacher(cabinetSessions, "teacher.other@example.org")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    docx = assignmentDocx("Решение")
    archive = submissionZip(("bound.docx", docx), ("unclear.docx", docx), ("rejected.docx", docx))

    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, "teacher.other@example.org", PASSWORD)[0] == 200
        otherClass = client.post("/v1/classes", json={"name": "8Б"}).json()
        foreign = client.post(f"/v1/classes/{otherClass['classId']}/students", json={"name": "Чужой ученик"}).json()

        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        ownClass = client.post("/v1/classes", json={"name": "8А"}).json()
        classPath = f"/v1/classes/{ownClass['classId']}"
        student = client.post(f"{classPath}/students", json={"name": "Саша Иванов"}).json()
        assignment = client.post(
            f"{classPath}/assignments", data={"requirements": "[]"}, files={"file": ("assignment.docx", docx)},
        )
        accepted = client.post(
            f"/v1/assignments/{assignment.json()['assignmentId']}/uploads", data={"assignmentRev": "1"},
            files={"file": ("class.zip", archive)},
        )
        assert accepted.status_code == 202, accepted.text
        uploadId = accepted.json()["uploadId"]
        path = f"/v1/uploads/{uploadId}"

        unmatched = client.get(path)
        assert unmatched.status_code == 200, unmatched.text
        assert unmatched.headers["cache-control"] == "no-store"
        assert all(
            file["status"] == "needs_confirmation" and file["candidates"] == []
            and "attemptId" not in file and "checkId" not in file
            for file in unmatched.json()["files"]
        )
        with cabinetSessions.begin() as db:
            records = {file.originalName: file for file in db.scalars(select(UploadFileRecord))}
            records["unclear.docx"].status = "needs_confirmation"
            records["unclear.docx"].reason = None
            records["unclear.docx"].candidateStudentIds = [student["studentId"], foreign["studentId"]]
            records["rejected.docx"].status = "rejected"
            records["rejected.docx"].reason = "Документ не читается"
        confirmed = client.post(
            f"{path}/bindings",
            json={"fileId": records["bound.docx"].fileId, "studentId": student["studentId"]},
        )
        assert confirmed.status_code == 200, confirmed.text
        attemptId, checkId = confirmed.json()["attemptId"], confirmed.json()["checkId"]

        response = client.get(path)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["uploadId"] == uploadId
        files = {item["originalName"]: item for item in payload["files"]}
        assert len(files) == 3
        assert files["bound.docx"] == {
            "fileId": records["bound.docx"].fileId, "originalName": "bound.docx", "status": "bound",
            "candidates": [student], "studentId": student["studentId"], "attemptId": attemptId, "checkId": checkId,
        }
        assert files["unclear.docx"] == {
            "fileId": records["unclear.docx"].fileId, "originalName": "unclear.docx",
            "status": "needs_confirmation", "candidates": [student],
        }
        assert files["rejected.docx"] == {
            "fileId": records["rejected.docx"].fileId, "originalName": "rejected.docx",
            "status": "rejected", "candidates": [], "reason": "Документ не читается",
        }

        assert client.delete("/v1/sessions/current").status_code == 204
        assert client.get(path).status_code == 401
        assert login(client, "teacher.other@example.org", PASSWORD)[0] == 200
        forbidden = client.get(path)
        missing = client.get(f"/v1/uploads/{uuid.uuid4()}")
        assert forbidden.status_code == missing.status_code == 404
        assert forbidden.json()["code"] == missing.json()["code"] == "not_found"

    with cabinetSessions() as db, closing(buildS3Client(settings)) as s3:
        for uploadFile in db.scalars(select(UploadFileRecord)):
            s3.delete_object(Bucket=uploadFile.bucket, Key=uploadFile.objectKey)
        for variant in db.scalars(select(AssignmentRecord)):
            s3.delete_object(Bucket=variant.bucket, Key=variant.objectKey)


@pytest.mark.parametrize(
    ("conflictingFilename", "conflictingSignature"),
    [
        ("Иванов Иван.docx", "Выполнил: Иванов Пётр"),
        ("Иванов Пётр.docx", "Ученик: Иванов Иван"),
    ],
    ids=["filename-ivan-signature-petr", "filename-petr-signature-ivan"],
)
def testUploadCreatesAttemptsOnlyForUnambiguousMatches(
    cabinetSessions: sessionmaker[Session], clock: ManualClock, conflictingFilename: str, conflictingSignature: str,
) -> None:
    addTeacher(cabinetSessions, "teacher.owner@example.org")
    addTeacher(cabinetSessions, "teacher.other@example.org")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    unsigned = assignmentDocx("Решение")
    archive = submissionZip(
        ("ivanov-ivan.docx", assignmentDocx("Решение", "Ученик: Иванов Иван")),
        ("Пётр Иванов.docx", unsigned),
        (conflictingFilename, assignmentDocx("Решение", conflictingSignature)),
        ("Иванов.docx", unsigned),
        ("Ким Анна.docx", assignmentDocx("Решение", "ФИО: Анна Ким")),
        ("Кузнецова Мария.docx", assignmentDocx("Решение", "Ученица: Кузнецова Мария")),
        ("multiple.docx", assignmentDocx("Решение", "Ученик: Иванов Иван", "ФИО: Иванов Пётр")),
    )

    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, "teacher.other@example.org", PASSWORD)[0] == 200
        foreignClass = client.post("/v1/classes", json={"name": "8Б"}).json()["classId"]
        assert client.post(f"/v1/classes/{foreignClass}/students", json={"name": "Кузнецова Мария"}).status_code == 201

        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        classId = client.post("/v1/classes", json={"name": "8А"}).json()["classId"]
        classPath = f"/v1/classes/{classId}"
        students = []
        for name in ("Иванов Иван", "Иванов Пётр", "Анна Ким", "Ким Анна"):
            student = client.post(f"{classPath}/students", json={"name": name})
            assert student.status_code == 201
            students.append(student.json()["studentId"])
        ivan, petr, anna, secondAnna = students

        assignment = client.post(
            f"{classPath}/assignments", data={"requirements": "[]"}, files={"file": ("assignment.docx", unsigned)},
        )
        assert assignment.status_code == 201
        assignmentId = assignment.json()["assignmentId"]
        accepted = client.post(
            f"/v1/assignments/{assignmentId}/uploads", data={"assignmentRev": "1"}, files={"file": ("class.zip", archive)},
        )
        assert accepted.status_code == 202, accepted.text
        path = f"/v1/uploads/{accepted.json()['uploadId']}"
        progress = client.get(path)
        assert progress.status_code == 200, progress.text
        files = {file["originalName"]: file for file in progress.json()["files"]}
        assert len(files) == 7
        for filename, matchId in (("ivanov-ivan.docx", ivan), ("Пётр Иванов.docx", petr)):
            assert files[filename]["studentId"] == matchId
            assert files[filename]["status"] == "bound"
            assert files[filename]["attemptId"] and files[filename]["checkId"]
        for filename, candidates in (
            (conflictingFilename, {ivan, petr}), ("Иванов.docx", {ivan, petr}),
            ("Ким Анна.docx", {anna, secondAnna}), ("Кузнецова Мария.docx", set()), ("multiple.docx", {ivan, petr}),
        ):
            file = files[filename]
            assert file["status"] == "needs_confirmation", file
            assert {candidate["studentId"] for candidate in file["candidates"]} == candidates
            assert "studentId" not in file and file["reason"]
            assert "attemptId" not in file and "checkId" not in file
        assert files[conflictingFilename]["reason"] == "Подпись и имя файла не совпадают."

        # A file persisted by A3.4 gets the same match when its owner first reads progress.
        with cabinetSessions.begin() as db:
            old = db.get(UploadFileRecord, files["ivanov-ivan.docx"]["fileId"])
            assert old is not None
            old.status = "pending_binding"
            old.studentId = None
            old.attemptId = old.checkId = None
            old.candidateStudentIds = []

        saved = client.get(path)
        assert saved.status_code == 200
        rematched = next(file for file in saved.json()["files"] if file["originalName"] == "ivanov-ivan.docx")
        assert rematched == files["ivanov-ivan.docx"]

    with cabinetSessions() as db, closing(buildS3Client(settings)) as s3:
        attempts = {attempt.attemptId: attempt.studentId for attempt in db.scalars(select(AttemptRecord))}
        checks = {check.checkId: check.attemptId for check in db.scalars(select(CheckRecord))}
        assert attempts == {
            files[filename]["attemptId"]: studentId
            for filename, studentId in (("ivanov-ivan.docx", ivan), ("Пётр Иванов.docx", petr))
        }
        assert checks == {file["checkId"]: file["attemptId"] for file in files.values() if file["status"] == "bound"}
        for file in db.scalars(select(UploadFileRecord)):
            s3.delete_object(Bucket=file.bucket, Key=file.objectKey)
        for record in db.scalars(select(AssignmentRecord)):
            s3.delete_object(Bucket=record.bucket, Key=record.objectKey)


def testTeacherConfirmsStudentBindingOnce(
    cabinetSessions: sessionmaker[Session], clock: ManualClock,
) -> None:
    addTeacher(cabinetSessions, "teacher.owner@example.org")
    addTeacher(cabinetSessions, "teacher.other@example.org")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    docx = assignmentDocx("Решение")
    archive = submissionZip(("choose.docx", docx), ("create.docx", docx))

    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        classId = client.post("/v1/classes", json={"name": "8А"}).json()["classId"]
        otherClass = client.post("/v1/classes", json={"name": "8Б"}).json()["classId"]
        rosterPath = f"/v1/classes/{classId}/students"
        chosen = client.post(rosterPath, json={"name": "Иванов Иван"}).json()
        foreign = client.post(f"/v1/classes/{otherClass}/students", json={"name": "Ким Анна"}).json()

        assignment = client.post(
            f"/v1/classes/{classId}/assignments", data={"requirements": "[]"}, files={"file": ("assignment.docx", docx)},
        )
        assert assignment.status_code == 201, assignment.text
        uploadPath = f"/v1/assignments/{assignment.json()['assignmentId']}/uploads"
        first = client.post(uploadPath, data={"assignmentRev": "1"}, files={"file": ("class.zip", archive)})
        second = client.post(uploadPath, data={"assignmentRev": "1"}, files={"file": ("other.docx", docx)})
        assert first.status_code == second.status_code == 202
        progressPath = f"/v1/uploads/{first.json()['uploadId']}"
        bindPath = f"{progressPath}/bindings"
        fileIds = {file["originalName"]: file["fileId"] for file in client.get(progressPath).json()["files"]}
        otherFile = client.get(f"/v1/uploads/{second.json()['uploadId']}").json()["files"][0]["fileId"]
        chosenBody = {"fileId": fileIds["choose.docx"], "studentId": chosen["studentId"]}
        createBody = {"fileId": fileIds["create.docx"], "newStudent": {"name": "Петрова Анна"}}

        assert client.post(bindPath, json={"fileId": otherFile, "studentId": chosen["studentId"]}).status_code == 404
        assert client.post(bindPath, json={**chosenBody, "studentId": foreign["studentId"]}).status_code == 404
        assert client.post(bindPath, json={"fileId": fileIds["create.docx"]}).status_code == 422
        assert client.post(bindPath, json={**createBody, "studentId": chosen["studentId"]}).status_code == 422
        assert client.get(rosterPath).json() == [chosen]

        assert login(client, "teacher.other@example.org", PASSWORD)[0] == 200
        assert client.post(bindPath, json=createBody).status_code == 404
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200

        selected = client.post(bindPath, json=chosenBody)
        assert selected.status_code == 200, selected.text
        assert selected.json() == {
            **chosenBody, "status": "bound", "attemptId": selected.json()["attemptId"], "checkId": selected.json()["checkId"],
        }
        assert client.post(bindPath, json=chosenBody).json() == selected.json()

        ready = Barrier(2)

        def createConcurrently() -> Response:
            with TestClient(app, base_url="https://testserver", cookies=client.cookies) as contender:
                ready.wait(timeout=5)
                response: Response = contender.post(bindPath, json=createBody)
                return response

        with ThreadPoolExecutor(max_workers=2) as workers:
            firstRequest = workers.submit(createConcurrently)
            secondRequest = workers.submit(createConcurrently)
            created, repeated = firstRequest.result(timeout=10), secondRequest.result(timeout=10)

        assert created.status_code == repeated.status_code == 200
        assert created.json() == repeated.json() == client.post(bindPath, json=createBody).json()
        createdId = created.json()["studentId"]
        roster = client.get(rosterPath).json()
        assert len(roster) == 2
        assert {"studentId": createdId, "name": "Петрова Анна"} in roster
        assert client.post(
            bindPath, json={**createBody, "newStudent": {"name": "Другое имя"}},
        ).status_code == 409
        assert client.post(bindPath, json={**chosenBody, "studentId": createdId}).status_code == 409
        assert client.get(rosterPath).json() == roster

        progress = {file["fileId"]: file for file in client.get(progressPath).json()["files"]}
        assert progress[fileIds["create.docx"]]["studentId"] == createdId
        assert progress[fileIds["choose.docx"]]["studentId"] == chosen["studentId"]
        assert all(file["status"] == "bound" and file["attemptId"] and file["checkId"] for file in progress.values())

    with cabinetSessions() as db, closing(buildS3Client(settings)) as s3:
        for file in db.scalars(select(UploadFileRecord)):
            s3.delete_object(Bucket=file.bucket, Key=file.objectKey)
        for record in db.scalars(select(AssignmentRecord)):
            s3.delete_object(Bucket=record.bucket, Key=record.objectKey)


@pytest.mark.parametrize("duplicateFilename", ["first.docx", "copy.docx"], ids=["same-filename", "renamed"])
def testDuplicateAttemptsAreSharedAcrossConcurrentConfirmations(
    cabinetSessions: sessionmaker[Session], clock: ManualClock, duplicateFilename: str,
) -> None:
    addTeacher(cabinetSessions, "teacher.owner@example.org")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    firstDocx = assignmentDocx("Первая сдача")
    revisedDocx = assignmentDocx("Исправленная сдача")

    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        classId = client.post("/v1/classes", json={"name": "8А"}).json()["classId"]
        classPath = f"/v1/classes/{classId}"
        studentId = client.post(f"{classPath}/students", json={"name": "Иванов Иван"}).json()["studentId"]
        otherStudentId = client.post(f"{classPath}/students", json={"name": "Иванов Пётр"}).json()["studentId"]
        assignment = client.post(
            f"{classPath}/assignments", data={"requirements": "[]"}, files={"file": ("assignment.docx", firstDocx)},
        )
        assert assignment.status_code == 201, assignment.text
        assignmentId = assignment.json()["assignmentId"]

        def submit(filename: str, content: bytes, revision: int = 1) -> tuple[str, str]:
            uploaded = client.post(
                f"/v1/assignments/{assignmentId}/uploads", data={"assignmentRev": str(revision)},
                files={"file": (filename, content)},
            )
            assert uploaded.status_code == 202, uploaded.text
            path = f"/v1/uploads/{uploaded.json()['uploadId']}"
            file = client.get(path).json()["files"][0]
            assert file["status"] == "needs_confirmation"
            return path, file["fileId"]

        first = submit("first.docx", firstDocx)
        duplicate = submit(duplicateFilename, firstDocx)
        submittedAt = clock.now()
        clock.advance(60)
        ready = Barrier(2)

        def bindConcurrently(upload: tuple[str, str]) -> Response:
            path, fileId = upload
            with TestClient(app, base_url="https://testserver", cookies=client.cookies) as contender:
                ready.wait(timeout=5)
                response: Response = contender.post(f"{path}/bindings", json={"fileId": fileId, "studentId": studentId})
                return response

        with ThreadPoolExecutor(max_workers=2) as workers:
            firstRequest, duplicateRequest = workers.submit(bindConcurrently, first), workers.submit(bindConcurrently, duplicate)
            bound, repeated = firstRequest.result(timeout=10), duplicateRequest.result(timeout=10)
        assert bound.status_code == repeated.status_code == 200
        initial = bound.json()
        assert initial["studentId"] == studentId
        assert repeated.json() == {**initial, "fileId": duplicate[1]}

        def confirm(upload: tuple[str, str], chosenStudentId: str) -> dict[str, str]:
            path, fileId = upload
            response = client.post(f"{path}/bindings", json={"fileId": fileId, "studentId": chosenStudentId})
            assert response.status_code == 200, response.text
            payload: dict[str, str] = response.json()
            return payload

        assert confirm(first, studentId) == initial
        otherUpload = submit("first.docx", firstDocx)
        otherBinding = confirm(otherUpload, otherStudentId)
        assert otherBinding["studentId"] == otherStudentId
        assert otherBinding["attemptId"] != initial["attemptId"]
        assert otherBinding["checkId"] != initial["checkId"]
        revision = client.post(
            f"{classPath}/assignments",
            data={"assignmentId": assignmentId, "requirements": json.dumps([{"text": "Другие требования"}])},
            files={"file": ("assignment.docx", firstDocx)},
        )
        assert revision.status_code == 201 and revision.json()["assignmentRev"] == 2
        changed = confirm(submit("first.docx", revisedDocx), studentId)
        underNewRequirements = confirm(submit("copy.docx", firstDocx, revision=2), studentId)
        assert len({initial["attemptId"], changed["attemptId"], otherBinding["attemptId"]}) == 3
        assert underNewRequirements["attemptId"] == initial["attemptId"]
        assert len({initial["checkId"], changed["checkId"], underNewRequirements["checkId"], otherBinding["checkId"]}) == 4
        assert confirm(first, studentId) == initial
        assert client.get(first[0]).json()["files"][0]["checkId"] == initial["checkId"]

    with cabinetSessions() as db, closing(buildS3Client(settings)) as s3:
        attempts = {attempt.attemptId: attempt for attempt in db.scalars(select(AttemptRecord))}
        firstHash = hashlib.sha256(firstDocx).hexdigest()
        assert {attemptId: (attempt.studentId, attempt.sha256) for attemptId, attempt in attempts.items()} == {
            initial["attemptId"]: (studentId, firstHash),
            changed["attemptId"]: (studentId, hashlib.sha256(revisedDocx).hexdigest()),
            otherBinding["attemptId"]: (otherStudentId, firstHash),
        }
        assert attempts[initial["attemptId"]].submittedAt == submittedAt
        assert {check.checkId: (check.attemptId, check.assignmentRev) for check in db.scalars(select(CheckRecord))} == {
            initial["checkId"]: (initial["attemptId"], 1),
            changed["checkId"]: (changed["attemptId"], 1),
            underNewRequirements["checkId"]: (initial["attemptId"], 2),
            otherBinding["checkId"]: (otherBinding["attemptId"], 1),
        }
        for file in db.scalars(select(UploadFileRecord)):
            s3.delete_object(Bucket=file.bucket, Key=file.objectKey)
        for record in db.scalars(select(AssignmentRecord)):
            s3.delete_object(Bucket=record.bucket, Key=record.objectKey)


def testOnlyOwnerReadsCheckFromGrading(
    cabinetSessions: sessionmaker[Session], clock: ManualClock,
    contractExample: Callable[[str], dict[str, object]],
) -> None:
    owner, other = "teacher.owner@example.org", "teacher.other@example.org"
    addTeacher(cabinetSessions, owner)
    addTeacher(cabinetSessions, other)
    settings = replace(Settings.fromEnv(), gradingBaseUrl="http://grading:8000", gradingServiceToken="test-cabinet")
    requests: list[Request] = []
    gradingResponse = Response(500)

    def readGrading(request: Request) -> Response:
        requests.append(request)
        return gradingResponse

    app = createApp(
        settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock,
        gradingTransport=MockTransport(readGrading),
    )
    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, owner, PASSWORD)[0] == 200
        created = client.post("/v1/classes", json={"name": "8А"})
        assert created.status_code == 201, created.text
        classPath = f"/v1/classes/{created.json()['classId']}"
        assert client.post(f"{classPath}/students", json={"name": "Иванов Иван"}).status_code == 201
        assignment = client.post(
            f"{classPath}/assignments", data={"requirements": "[]"},
            files={"file": ("variant.docx", assignmentDocx("Найдите длину вектора."))},
        )
        assert assignment.status_code == 201, assignment.text
        submission = client.post(
            f"/v1/assignments/{assignment.json()['assignmentId']}/uploads", data={"assignmentRev": "1"},
            files={"file": ("Иванов Иван.docx", assignmentDocx("Длина вектора равна 5."))},
        )
        assert submission.status_code == 202, submission.text
        progress = client.get(f"/v1/uploads/{submission.json()['uploadId']}")
        assert progress.status_code == 200, progress.text
        file = progress.json()["files"][0]
        assert file["status"] == "bound"
        checkId, attemptId = file["checkId"], file["attemptId"]
        path = f"/v1/checks/{checkId}"
        headers = {"X-Trace-Id": "check-read-test"}

        for filename in ("checkQueued.json", "checkNeedsReview.json", "checkReady.json"):
            example = contractExample(f"grading/{filename}")
            expected = {**example, "checkId": checkId, "attemptId": attemptId}
            gradingResponse = Response(200, json={**expected, "markdownRef": {"bucket": "results", "key": "internal"}})
            result = client.get(path, headers=headers)
            assert result.status_code == 200, result.text
            assert result.json() == expected
            assert result.headers["cache-control"] == "no-store"
            assert result.headers["x-trace-id"] == headers["X-Trace-Id"]

        assert len(requests) == 3
        for request in requests:
            assert request.method == "GET"
            assert str(request.url) == f"http://grading:8000/internal/v1/checks/{checkId}"
            assert request.headers["authorization"] == "Bearer test-cabinet"
            assert request.headers["x-trace-id"] == headers["X-Trace-Id"]
            assert "cookie" not in request.headers

        assert login(client, other, PASSWORD)[0] == 200
        denied = client.get(path, headers=headers)
        missing = client.get(f"/v1/checks/{uuid.uuid4()}", headers=headers)
        assert denied.status_code == missing.status_code == 404
        assert denied.json() == missing.json()
        assert client.delete("/v1/sessions/current").status_code == 204
        assert client.get(path).status_code == 401
        assert len(requests) == 3

    with cabinetSessions() as db, closing(buildS3Client(settings)) as s3:
        for upload in db.scalars(select(UploadFileRecord)):
            s3.delete_object(Bucket=upload.bucket, Key=upload.objectKey)
        for variant in db.scalars(select(AssignmentRecord)):
            s3.delete_object(Bucket=variant.bucket, Key=variant.objectKey)


def testClassReportAddsRosterAndCountsStudentsInsteadOfAttempts(
    cabinetSessions: sessionmaker[Session], clock: ManualClock,
) -> None:
    owner = addTeacher(cabinetSessions, "teacher.owner@example.org")
    addTeacher(cabinetSessions, "teacher.other@example.org")
    classId, assignmentId = (str(uuid.uuid4()) for _ in range(2))
    with cabinetSessions.begin() as db:
        db.add(ClassRecord(classId=classId, teacherId=owner, name="8А"))
        db.flush()
        db.add(AssignmentRecord(
            assignmentId=assignmentId, assignmentRev=1, classId=classId, assignmentDate=clock.now().date(),
            requirements=[], bucket="sources", objectKey="test-variant", sha256="0" * 64,
        ))
        for studentId in ("reviewed", "skipped", "waiting", "absent", "removed-without-work"):
            deletedAt = clock.now() if studentId in {"reviewed", "removed-without-work"} else None
            db.add(StudentRecord(classId=classId, studentId=studentId, name=studentId, deletedAt=deletedAt))
        db.flush()
        for checkId, studentId in (("old", "reviewed"), ("new", "reviewed"), ("skip", "skipped"), ("pending", "waiting")):
            db.add(AttemptRecord(
                attemptId=checkId, assignmentId=assignmentId, studentId=studentId, sha256=checkId,
                bucket="sources", objectKey=f"test-{checkId}", submittedAt=clock.now(),
            ))
        db.flush()
        for checkId in ("old", "new", "skip", "pending"):
            db.add(CheckRecord(checkId=checkId, attemptId=checkId, assignmentId=assignmentId, assignmentRev=1))

    requested: list[Request] = []

    def summary(request: Request) -> Response:
        requested.append(request)
        return Response(200, json={
            "assignmentId": assignmentId, "assignmentRev": 1,
            "results": [
                {"checkId": "new", "studentId": "reviewed", "resultRev": 2},
                {"checkId": "skip", "studentId": "skipped", "resultRev": 1},
            ],
            "tasks": [{"taskId": "1", "correct": 1, "incorrect": 0, "skipped": 1, "issuesByCategory": {"sign": 1}}],
        })

    app = createApp(
        replace(Settings.fromEnv(), gradingServiceToken="test-cabinet"), sessionFactory=cabinetSessions,
        passwords=Passwords(CountingHasher()), clock=clock, gradingTransport=MockTransport(summary),
    )
    with TestClient(app, base_url="https://testserver") as client:
        path = f"/v1/assignments/{assignmentId}/report?rev=1"
        assert login(client, "teacher.other@example.org", PASSWORD)[0] == 200
        assert client.get(path).status_code == 404
        assert not requested

        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        response = client.get(path)
        assert response.status_code == 200, response.text
        report = response.json()
        assert {student["studentId"] for student in report["students"]} == {"reviewed", "skipped", "waiting", "absent"}
        assert [student["studentId"] for student in report["notSubmitted"]] == ["absent"]
        assert [student["studentId"] for student in report["waiting"]] == ["waiting"]
        assert report["submittedCount"] == 3
        assert [result["checkId"] for result in report["results"]] == ["new", "skip"]
        assert report["tasks"] == [{
            "taskId": "1", "correct": 1, "incorrect": 0, "skipped": 1, "unchecked": 1,
            "correctShare": 1 / 3, "incorrectShare": 0, "skippedShare": 1 / 3, "issuesByCategory": {"sign": 1},
        }]
        assert len(requested) == 1
        assert requested[0].url.path == f"/internal/v1/assignments/{assignmentId}/report"
        assert requested[0].url.params["rev"] == "1"


@pytest.fixture
def reviewedCheck(cabinetSessions: sessionmaker[Session], clock: ManualClock) -> tuple[str, str]:
    owner = addTeacher(cabinetSessions, "teacher.owner@example.org")
    classId, studentId, assignmentId, attemptId, checkId = (str(uuid.uuid4()) for _ in range(5))
    with cabinetSessions.begin() as db:
        db.add(ClassRecord(classId=classId, teacherId=owner, name="8А"))
        db.flush()
        db.add(StudentRecord(classId=classId, studentId=studentId, name="Иванов Иван"))
        db.add(AssignmentRecord(
            classId=classId, assignmentId=assignmentId, assignmentRev=1, assignmentDate=clock.now().date(),
            requirements=[], bucket="sources", objectKey="test-variant", sha256="0" * 64,
        ))
        db.flush()
        db.add(AttemptRecord(
            attemptId=attemptId, assignmentId=assignmentId, studentId=studentId, sha256="0" * 64,
            bucket="sources", objectKey="test-submission", submittedAt=clock.now(),
        ))
        db.flush()
        db.add(CheckRecord(checkId=checkId, attemptId=attemptId, assignmentId=assignmentId, assignmentRev=1))
    return checkId, owner


def testRemovingRosterEntriesKeepsHistoryAndPreventsNewBindings(
    cabinetSessions: sessionmaker[Session], reviewedCheck: tuple[str, str], clock: ManualClock,
) -> None:
    checkId, _ = reviewedCheck
    with cabinetSessions() as db:
        check = db.get(CheckRecord, checkId)
        assert check is not None
        assignment = db.get(AssignmentRecord, (check.assignmentId, check.assignmentRev))
        attempt = db.get(AttemptRecord, check.attemptId)
        assert assignment is not None and attempt is not None
        assignmentId, classId, attemptId, studentId = assignment.assignmentId, assignment.classId, attempt.attemptId, attempt.studentId
    gradingCalls: list[Request] = []

    def grading(request: Request) -> Response:
        gradingCalls.append(request)
        return Response(200, json={"checkId": checkId, "attemptId": attemptId, "state": "queued", "reason": "Ожидает проверки"})

    settings = replace(Settings.fromEnv(), gradingServiceToken="test-cabinet")
    app = createApp(
        settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock,
        gradingTransport=MockTransport(grading),
    )
    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        classPath = f"/v1/classes/{classId}"
        assert client.delete(f"{classPath}/students/{studentId}").status_code == 204
        assert client.get(f"{classPath}/students").json() == []
        assert client.get(f"/v1/checks/{checkId}").status_code == 200

        uploadPath = f"/v1/assignments/{assignmentId}/uploads"
        files = {"file": ("Иванов Иван.docx", assignmentDocx("Новая работа"))}
        uploaded = client.post(uploadPath, data={"assignmentRev": "1"}, files=files)
        assert uploaded.status_code == 202, uploaded.text
        progressPath = f"/v1/uploads/{uploaded.json()['uploadId']}"
        progress = client.get(progressPath).json()["files"][0]
        assert (progress["status"], progress["candidates"]) == ("needs_confirmation", [])
        assert client.post(f"{progressPath}/bindings", json={
            "fileId": progress["fileId"], "studentId": studentId,
        }).status_code == 404

        with cabinetSessions.begin() as db:
            file = db.get(UploadFileRecord, progress["fileId"])
            assert file is not None
            file.status = "matched"
            file.studentId = studentId
            file.candidateStudentIds = [studentId]
            key = file.objectKey
        try:
            rematched = client.get(progressPath).json()["files"][0]
            assert (rematched["status"], rematched["candidates"]) == ("needs_confirmation", [])
            assert "checkId" not in rematched

            assert client.delete(classPath).status_code == 204
            assert client.get(progressPath).status_code == 404
            assert client.get(f"/v1/checks/{checkId}").status_code == 404
            assert client.get(f"/v1/assignments/{assignmentId}/report?rev=1").status_code == 404
            assert client.post(uploadPath, data={"assignmentRev": "1"}, files=files).status_code == 404
            assert len(gradingCalls) == 1
            with cabinetSessions() as db:
                assert db.scalars(select(AttemptRecord.attemptId)).all() == [attemptId]
                assert db.scalars(select(CheckRecord.checkId)).all() == [checkId]
                assert db.scalars(select(OutboxRecord)).all() == []
        finally:
            with closing(buildS3Client(settings)) as s3:
                s3.delete_object(Bucket=SOURCES_BUCKET, Key=key)


def testTeacherEditsPublishesAndDownloadsAnExplicitRevision(
    cabinetSessions: sessionmaker[Session], reviewedCheck: tuple[str, str], clock: ManualClock,
) -> None:
    checkId, owner = reviewedCheck
    settings = replace(Settings.fromEnv(), gradingServiceToken="test-cabinet")
    adminSettings = replace(
        settings, s3AccessKeyId=os.environ.get("S3_ACCESS_KEY_ID", "gorky-dev"),
        s3SecretAccessKey=os.environ.get("S3_SECRET_ACCESS_KEY", "gorky-dev-secret"),
    )
    original = "# Исходный отзыв\n".encode()
    key = f"review-tests/{checkId}/r1.md"
    edit = {"baseRev": 1, "editId": "edit-1", "changes": [{"type": "remove_issue", "issueId": "i1"}]}

    def grading(request: Request) -> Response:
        assert request.headers["Authorization"] == "Bearer test-cabinet"
        path = f"/internal/v1/checks/{checkId}"
        if request.method == "PATCH":
            assert request.url.path == f"{path}/review"
            body = json.loads(request.content)
            if body["editId"] == "stale-edit":
                return Response(409, json={
                    "code": "conflict", "message": "internal details", "retryable": False,
                    "traceId": "grading", "resultRev": 2,
                })
            assert body == {**edit, "actorId": owner}
            return Response(200, json={"checkId": checkId, "editId": "edit-1", "resultRev": 2})
        if request.method == "POST":
            assert request.url.path == f"{path}/publish"
            assert json.loads(request.content) == {"resultRev": 1, "actorId": owner}
            return Response(200, json={"checkId": checkId, "resultRev": 1})
        assert request.method == "GET" and request.url.path == f"{path}/markdown"
        if request.url.params["rev"] == "2":
            return Response(404, json={
                "code": "not_found", "message": "Revision is a draft", "retryable": False, "traceId": "grading",
            })
        assert request.url.params["rev"] == "1"
        return Response(200, json={
            "checkId": checkId, "resultRev": 1,
            "markdownRef": {"bucket": RESULTS_BUCKET, "key": key, "sha256": hashlib.sha256(original).hexdigest()},
        })

    app = createApp(
        settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock,
        gradingTransport=MockTransport(grading),
    )
    with TestClient(app, base_url="https://testserver") as client, closing(buildS3Client(adminSettings)) as s3:
        if RESULTS_BUCKET not in {bucket.get("Name") for bucket in s3.list_buckets().get("Buckets", [])}:
            s3.create_bucket(Bucket=RESULTS_BUCKET)
        s3.put_object(Bucket=RESULTS_BUCKET, Key=key, Body=original)
        try:
            assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
            path = f"/v1/checks/{checkId}"
            changed = client.patch(f"{path}/review", json=edit)
            assert changed.status_code == 200, changed.text
            assert changed.json() == {"checkId": checkId, "editId": "edit-1", "resultRev": 2}
            stale = client.patch(f"{path}/review", json={**edit, "editId": "stale-edit"})
            assert stale.status_code == 409 and stale.json()["resultRev"] == 2
            assert "internal details" not in stale.text

            published = client.post(f"{path}/publish", json={"resultRev": 1})
            assert published.status_code == 200, published.text
            assert published.json() == {"checkId": checkId, "resultRev": 1}
            assert client.get(f"{path}/markdown?rev=2").status_code == 404
            downloaded = client.get(f"{path}/markdown?rev=1")
            assert downloaded.status_code == 200, downloaded.text
            assert downloaded.headers["content-type"].startswith("text/markdown")
            assert downloaded.headers["content-disposition"].startswith("attachment;")
            assert downloaded.content == original
        finally:
            s3.delete_object(Bucket=RESULTS_BUCKET, Key=key)


def testOtherTeacherCannotReadEditPublishOrDownloadACheck(
    cabinetSessions: sessionmaker[Session], reviewedCheck: tuple[str, str], clock: ManualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkId, _ = reviewedCheck
    addTeacher(cabinetSessions, "teacher.other@example.org")
    with cabinetSessions() as db:
        attemptId = db.execute(select(CheckRecord.attemptId)).scalar_one()
    gradingCalls: list[Request] = []

    def grading(request: Request) -> Response:
        gradingCalls.append(request)
        assert request.method == "GET" and request.url.path == f"/internal/v1/checks/{checkId}"
        return Response(200, json={
            "checkId": checkId, "attemptId": attemptId, "state": "queued", "reason": "Ожидает проверки",
        })

    def unexpectedStorage(*args: object) -> bytes:
        raise AssertionError("A refused download must not read S3")

    monkeypatch.setattr("cabinet.main.loadMarkdown", unexpectedStorage)
    app = createApp(
        replace(Settings.fromEnv(), gradingServiceToken="test-cabinet"), sessionFactory=cabinetSessions,
        passwords=Passwords(CountingHasher()), clock=clock, gradingTransport=MockTransport(grading),
    )
    edit = {"baseRev": 1, "editId": "edit", "changes": [{"type": "remove_issue", "issueId": "i1"}]}
    with TestClient(app, base_url="https://testserver") as client:
        path = f"/v1/checks/{checkId}"
        assert login(client, "teacher.other@example.org", PASSWORD)[0] == 200
        for method, suffix, body in (
            ("GET", "", None),
            ("PATCH", "/review", edit),
            ("POST", "/publish", {"resultRev": 1}),
            ("GET", "/markdown?rev=1", None),
        ):
            refused = client.request(method, f"{path}{suffix}", json=body)
            assert refused.status_code == 404, (method, suffix, refused.text)
            assert refused.json()["code"] == "not_found"
        assert not gradingCalls
        with cabinetSessions() as db:
            assert db.scalars(select(CheckRecord.checkId)).all() == [checkId]
            assert db.scalars(select(AttemptRecord.attemptId)).all() == [attemptId]
            assert db.scalars(select(OutboxRecord)).all() == []

        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        assert client.patch(f"{path}/review", json={**edit, "actorId": "forged"}).status_code == 422
        assert not gradingCalls
        assert client.get(path).status_code == 200
        assert len(gradingCalls) == 1


def testFailedUploadRemovesItsObjectsAndPreservesActiveOriginal(
    cabinetSessions: sessionmaker[Session], reviewedCheck: tuple[str, str], clock: ManualClock,
) -> None:
    checkId, _ = reviewedCheck
    with cabinetSessions() as db:
        assignmentId = db.execute(select(CheckRecord.assignmentId).where(CheckRecord.checkId == checkId)).scalar_one()
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    liveDocx = assignmentDocx("Действующая работа")
    interrupted = submissionZip(
        ("Иванов Иван.docx", assignmentDocx("Новая работа")), ("неизвестно.docx", assignmentDocx("Другая работа")),
    )
    failedUploads: list[str] = []
    path = f"/v1/assignments/{assignmentId}/uploads"
    rev = {"assignmentRev": "1"}

    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client, closing(
        buildS3Client(settings)
    ) as s3:
        assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
        live = client.post(path, data=rev, files={"file": ("Иванов Иван.docx", liveDocx)})
        assert live.status_code == 202, live.text
        liveId = live.json()["uploadId"]
        with cabinetSessions() as db:
            originalKey = db.execute(select(UploadFileRecord.objectKey)).scalar_one()

        def failCommit(db: Session) -> None:
            if failedUploads:
                return
            db.flush()
            uploadId = db.execute(select(UploadRecord.uploadId).where(UploadRecord.uploadId != liveId)).scalar_one()
            failedUploads.append(uploadId)
            staged = s3.list_objects_v2(Bucket=SOURCES_BUCKET, Prefix=f"uploads/{uploadId}/")
            assert len(staged.get("Contents", [])) == 2
            raise SQLAlchemyError("simulated failure before commit")

        strayKey = f"uploads/{liveId}/{uuid.uuid4()}.docx"
        try:
            sqlEvent.listen(cabinetSessions, "before_commit", failCommit)
            try:
                failed = client.post(path, data=rev, files={"file": ("class.zip", interrupted)})
            finally:
                sqlEvent.remove(cabinetSessions, "before_commit", failCommit)

            assert failed.status_code == 500 and "uploadId" not in failed.json()
            assert len(failedUploads) == 1
            failedPrefix = f"uploads/{failedUploads[0]}/"
            assert not s3.list_objects_v2(Bucket=SOURCES_BUCKET, Prefix=failedPrefix).get("Contents")
            with cabinetSessions() as db:
                assert db.scalars(select(UploadRecord.uploadId)).all() == [liveId]
                assert db.scalars(select(UploadFileRecord.objectKey)).all() == [originalKey]
                assert len(db.scalars(select(AttemptRecord)).all()) == 2
                assert len(db.scalars(select(CheckRecord)).all()) == 2
                assert len(db.scalars(select(OutboxRecord)).all()) == 1

            s3.put_object(Bucket=SOURCES_BUCKET, Key=strayKey, Body=b"unused test object")
            assert cleanUpload(cabinetSessions, settings, liveId) == 1
            assert cleanUpload(cabinetSessions, settings, liveId) == 0
            with closing(s3.get_object(Bucket=SOURCES_BUCKET, Key=originalKey)["Body"]) as stored:
                assert stored.read() == liveDocx
        finally:
            s3.delete_object(Bucket=SOURCES_BUCKET, Key=strayKey)
            s3.delete_object(Bucket=SOURCES_BUCKET, Key=originalKey)


def testOutboxRollsBackWithDomainRecordsAndBackfillsExistingChecks(
    cabinetSessions: sessionmaker[Session], clock: ManualClock,
) -> None:
    class AbortUpload(Exception):
        pass

    addTeacher(cabinetSessions, "teacher.owner@example.org")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    docx = assignmentDocx("Задание и работа")
    requirements = [TeacherRequirement(text="Покажите ход решения", taskNumber="1")]
    refs: set[tuple[str, str]] = set()

    with TestClient(app, base_url="https://testserver") as client, closing(buildS3Client(settings)) as s3:
        try:
            assert login(client, "teacher.owner@example.org", PASSWORD)[0] == 200
            classId = client.post("/v1/classes", json={"name": "8А"}).json()["classId"]
            classPath = f"/v1/classes/{classId}"
            studentId = client.post(f"{classPath}/students", json={"name": "Иванов Иван"}).json()["studentId"]

            with pytest.raises(AbortUpload), cabinetSessions.begin() as db:
                event = EventContext("rollback-test", clock.now())
                created = storeAssignment(db, settings, classId, None, clock.now().date(), requirements, docx, event)
                db.flush()
                storeUpload(db, settings, created.assignmentId, created.assignmentRev, classId, [
                    SubmissionDocument("Иванов Иван.docx", docx, ()),
                ], event)
                db.flush()
                refs.update((row.bucket, row.objectKey) for row in db.scalars(select(AssignmentRecord)))
                refs.update((row.bucket, row.objectKey) for row in db.scalars(select(UploadFileRecord)))
                staged = list(db.scalars(select(OutboxRecord).order_by(OutboxRecord.sequence)))
                assert [row.data["type"] for row in staged] == ["assignment.published", "attempt.submitted"]
                assert all(row.data["traceId"] == "rollback-test" and row.publishedAt is None for row in staged)
                raise AbortUpload()

            with cabinetSessions() as db:
                assert db.scalars(select(AssignmentRecord)).all() == []
                assert db.scalars(select(CheckRecord)).all() == []
                assert db.scalars(select(AttemptRecord)).all() == []
                assert db.scalars(select(OutboxRecord)).all() == []

            variant = client.post(
                f"{classPath}/assignments",
                data={"requirements": json.dumps([item.model_dump() for item in requirements])},
                files={"file": ("variant.docx", docx)}, headers={"X-Trace-Id": "variant-request"},
            )
            assert variant.status_code == 201, variant.text
            assignmentId = variant.json()["assignmentId"]
            url = f"/v1/assignments/{assignmentId}/uploads"
            for filename, traceId in (("Иванов Иван.docx", "upload-request"), ("Иванов_Иван.docx", "replay-request")):
                response = client.post(
                    url, data={"assignmentRev": "1"}, files={"file": (filename, docx)}, headers={"X-Trace-Id": traceId},
                )
                assert response.status_code == 202, response.text

            with cabinetSessions() as db:
                refs.update((row.bucket, row.objectKey) for row in db.scalars(select(AssignmentRecord)))
                refs.update((row.bucket, row.objectKey) for row in db.scalars(select(UploadFileRecord)))
                records = list(db.scalars(select(OutboxRecord).order_by(OutboxRecord.sequence)))
                assert len(records) == 2
                events = [checksEventAdapter.validate_json(json.dumps(row.data)) for row in records]
                published, submitted = events
                assert isinstance(published, AssignmentPublishedEvent)
                assert isinstance(submitted, AttemptSubmittedEvent)
                assert published.traceId == "variant-request"
                assert submitted.traceId == "upload-request"
                assert published.payload.requirements == requirements
                assert submitted.payload.studentId == studentId
                assert submitted.payload.checkId == db.scalars(select(CheckRecord.checkId)).one()
                assert submitted.payload.attemptId == db.scalars(select(AttemptRecord.attemptId)).one()
                assert all(row.eventId == eventData.eventId for row, eventData in zip(records, events, strict=True))

            alembicConfig = buildAlembicConfig(settings.databaseUrl)
            command.downgrade(alembicConfig, "0011")
            command.upgrade(alembicConfig, "head")
            with cabinetSessions() as db:
                backfilled = [
                    checksEventAdapter.validate_python(row.data)
                    for row in db.scalars(select(OutboxRecord).order_by(OutboxRecord.sequence))
                ]
                assert [eventData.type for eventData in backfilled] == ["assignment.published", "attempt.submitted"]
                assert [eventData.payload for eventData in backfilled] == [published.payload, submitted.payload]
        finally:
            for bucket, key in refs:
                s3.delete_object(Bucket=bucket, Key=key)
