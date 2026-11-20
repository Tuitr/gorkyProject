"""A2.1: a teacher logs in with POST /v1/sessions; refusals look the same; attempts are limited.

Tests use a cheap Argon2id (time_cost=1, small memory) so they stay fast; the type is the same as in production.
"""

import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from datetime import date
from io import BytesIO
from typing import Literal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from argon2 import PasswordHasher, Type
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from cabinet import documents
from cabinet.assignments import AssignmentRecord
from cabinet.auth.passwords import Passwords
from cabinet.auth.tables import TeacherRecord
from cabinet.database import allMetadata
from cabinet.documents import buildS3Client
from cabinet.main import buildAccounts, createApp
from cabinet.settings import DB_SCHEMA, Settings
from cabinet.uploads import UploadFileRecord, UploadRecord
from contracts.v1.cabinet.sessions import LOGIN_REFUSED_MESSAGE
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
        tables = ("teachers", "sessions", "login_throttle", "classes", "students", "assignments", "uploads", "upload_files")
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


def login(client: TestClient, login: str, password: str) -> tuple[int, dict[str, object], dict[str, str]]:
    response = client.post("/v1/sessions", json={"login": login, "password": password})
    return response.status_code, response.json(), dict(response.headers)


def testRightCredentialsReturnTheTeacher(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    teacherId = addTeacher(cabinetSessions, "teacher.one")

    status, body, _ = login(client, "  Teacher.One ", PASSWORD)

    assert (status, body) == (200, {"teacherId": teacherId, "displayName": "Учитель Синтетический"})


def testUnknownLoginAndWrongPasswordAreRefusedAlike(
    client: TestClient, cabinetSessions: sessionmaker[Session], hasher: CountingHasher
) -> None:
    addTeacher(cabinetSessions, "teacher.one")

    refusals = []
    for name, password in (("teacher.one", "wrong password"), ("nobody.here", PASSWORD)):
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
    addTeacher(cabinetSessions, "teacher.one")
    addTeacher(cabinetSessions, "teacher.two")
    with cabinetSessions() as session:
        hashes = list(session.scalars(select(TeacherRecord.passwordHash)))

    assert len(set(hashes)) == 2
    assert all(stored.startswith("$argon2id$") for stored in hashes)
    assert len({stored.split("$")[4] for stored in hashes}) == 2, "each hash has its own salt"
    assert all(PasswordHasher().verify(stored, PASSWORD) for stored in hashes), "argon2-cffi checks the stored hash"


def testPasswordAndHashNeverLeaveTheService(
    client: TestClient, cabinetSessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    addTeacher(cabinetSessions, "teacher.one")
    with cabinetSessions() as session:
        stored = session.scalars(select(TeacherRecord.passwordHash)).one()

    caplog.set_level(logging.DEBUG)
    texts = [client.post("/v1/sessions", json={"login": "teacher.one", "password": p}).text for p in (PASSWORD, "bad")]

    for secret in (PASSWORD, stored, stored.split("$")[-1]):
        assert all(secret not in answer for answer in texts)
        assert secret not in caplog.text


def testRepeatedFailuresBlockTheLoginEvenForTheRightPassword(
    client: TestClient, cabinetSessions: sessionmaker[Session], clock: ManualClock
) -> None:
    addTeacher(cabinetSessions, "teacher.one")
    statuses = [login(client, "teacher.one", "wrong")[0] for _ in range(SETTINGS.loginMaxFailures)]

    status, body, headers = login(client, "teacher.one", PASSWORD)

    assert statuses == [401] * SETTINGS.loginMaxFailures
    assert (status, body["code"], body["retryable"]) == (429, "rate_limited", True)
    assert int(headers["retry-after"]) == SETTINGS.loginWindowS
    clock.advance(SETTINGS.loginWindowS)
    assert login(client, "teacher.one", PASSWORD)[0] == 200


def testUnknownLoginIsBlockedTheSameWay(client: TestClient) -> None:
    for _ in range(SETTINGS.loginMaxFailures):
        login(client, "nobody.here", "guess")

    assert login(client, "nobody.here", "guess")[0] == 429


def testSuccessClearsTheFailuresOfTheLogin(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    addTeacher(cabinetSessions, "teacher.one")
    for _ in range(SETTINGS.loginMaxFailures - 1):
        login(client, "teacher.one", "wrong")
    assert login(client, "teacher.one", PASSWORD)[0] == 200

    statuses = [login(client, "teacher.one", "wrong")[0] for _ in range(SETTINGS.loginMaxFailures - 1)]

    assert statuses == [401] * (SETTINGS.loginMaxFailures - 1)


def testOneClientTryingManyLoginsIsBlocked(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    addTeacher(cabinetSessions, "teacher.one")
    for number in range(SETTINGS.clientMaxFailures):
        assert login(client, f"guess{number}", "guess")[0] == 401

    assert login(client, "teacher.one", PASSWORD)[0] == 429


def testThrottleStoresNoLoginsOrAddresses(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    login(client, "mistyped.login", "guess")
    with cabinetSessions() as session:
        keys = list(session.execute(text(f'SELECT throttle_key FROM "{DB_SCHEMA}".login_throttle')).scalars())

    assert len(keys) == 2
    assert not any("mistyped" in key or "testclient" in key for key in keys)


@pytest.mark.parametrize(
    "body", [{"login": "teacher.one"}, {"login": "", "password": "x"}, {"login": "a", "password": "b", "role": "admin"}]
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
    monkeypatch.delenv("CABINET_DEMO_TEACHER_LOGIN", raising=False)

    assert seedteacher.main() == 0
    assert seedteacher.main() == 0

    with cabinetSessions() as session:
        logins = list(session.scalars(select(TeacherRecord.login)))
    assert logins == [seedteacher.DEMO_LOGIN]
    output = capsys.readouterr().out
    assert "Created teacher" in output and "already exists" in output and PASSWORD not in output


def testSessionLifetime(client: TestClient, cabinetSessions: sessionmaker[Session], clock: ManualClock) -> None:
    teacherId = addTeacher(cabinetSessions, "teacher.one")
    path = "/v1/sessions/current"
    assert client.get(path).status_code == 401
    status, _, headers = login(client, "teacher.one", PASSWORD)
    assert status == 200
    assert all(flag in headers["set-cookie"].lower() for flag in ("secure", "httponly", "samesite=strict"))
    cookie = headers["set-cookie"].split(";", 1)[0]
    assert client.get(path, params={"actorId": "other"}).json()["teacherId"] == teacherId
    assert client.delete(path, headers={"Origin": "https://other.example"}).status_code == 403
    assert client.delete(path).status_code == 204
    assert client.get(path, headers={"Cookie": cookie}).status_code == 401
    assert client.delete(path, headers={"Cookie": cookie}).status_code == 401
    assert login(client, "teacher.one", PASSWORD)[0] == 200
    clock.advance(8 * 3600)
    assert client.get(path).status_code == 401


def testTeachersCanOnlyManageTheirOwnClasses(client: TestClient, cabinetSessions: sessionmaker[Session]) -> None:
    addTeacher(cabinetSessions, "teacher.one")
    addTeacher(cabinetSessions, "teacher.two")
    path = "/v1/classes"
    assert client.get(path).status_code == 401
    assert client.post(path, json={"name": "8А"}).status_code == 401
    assert login(client, "teacher.one", PASSWORD)[0] == 200

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

    assert login(client, "teacher.two", PASSWORD)[0] == 200
    assert client.get(path).json() == []
    second = client.post(path, json={"name": "8Б"})
    assert second.status_code == 201
    assert client.get(path).json() == [second.json()]
    assert client.get(roster).status_code == 404
    assert client.post(roster, json={"name": "Новый ученик"}).status_code == 404

    assert login(client, "teacher.one", PASSWORD)[0] == 200
    assert client.get(path).json() == [firstClass]
    assert client.get(f"{path}/{second.json()['classId']}/students").status_code == 404
    assert client.post(roster, json={"name": "   "}).status_code == 422
    assert client.post(roster, json={"name": "Саша", "classId": second.json()["classId"]}).status_code == 422
    assert {student["studentId"] for student in client.get(roster).json()} == studentIds


def assignmentDocx(title: str) -> bytes:
    document = Document()
    document.add_paragraph(title)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def testAssignmentUploadPreservesRevisions(cabinetSessions: sessionmaker[Session], clock: ManualClock) -> None:
    addTeacher(cabinetSessions, "teacher.owner")
    addTeacher(cabinetSessions, "teacher.other")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    firstDocx = assignmentDocx("Задание 1")
    secondDocx = assignmentDocx("Задание 1. Уточнение")
    initial = [{"text": "Обоснуйте решение", "taskNumber": None}]
    updated = [{"text": "Решите через формулу корней", "taskNumber": "1"}]

    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, "teacher.owner", PASSWORD)[0] == 200
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

        assert login(client, "teacher.other", PASSWORD)[0] == 200
        denied = client.post(path, data={"requirements": "[]"}, files={"file": ("first.docx", firstDocx)})
        assert denied.status_code == 404
        assert login(client, "teacher.owner", PASSWORD)[0] == 200
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
    addTeacher(cabinetSessions, "teacher.owner")
    addTeacher(cabinetSessions, "teacher.other")
    settings = Settings.fromEnv()
    app = createApp(settings, sessionFactory=cabinetSessions, passwords=Passwords(CountingHasher()), clock=clock)
    assignmentDoc = assignmentDocx("Задание")
    studentDoc = assignmentDocx("Решение")
    uploadPart = ("Иванов.docx", studentDoc)

    with TestClient(app, base_url="https://testserver") as client:
        assert login(client, "teacher.owner", PASSWORD)[0] == 200
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

        assert login(client, "teacher.other", PASSWORD)[0] == 200
        assert client.post(path, data=form, files={"file": uploadPart}).status_code == 404
        assert login(client, "teacher.owner", PASSWORD)[0] == 200
        assert client.delete("/v1/sessions/current").status_code == 204
        assert client.post(path, data=form, files={"file": uploadPart}).status_code == 401

        assert login(client, "teacher.owner", PASSWORD)[0] == 200
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
            assert stored.sha256 == hashlib.sha256(studentDoc).hexdigest()
            with closing(s3.get_object(Bucket=stored.bucket, Key=stored.objectKey)["Body"]) as body:
                assert body.read() == studentDoc
        finally:
            s3.delete_object(Bucket=stored.bucket, Key=stored.objectKey)
            for assignment in db.scalars(select(AssignmentRecord)):
                s3.delete_object(Bucket=assignment.bucket, Key=assignment.objectKey)
