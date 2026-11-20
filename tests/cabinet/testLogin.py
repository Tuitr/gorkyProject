"""A2.1: a teacher logs in with POST /v1/sessions; refusals look the same; attempts are limited.

Tests use a cheap Argon2id (time_cost=1, small memory) so they stay fast; the type is the same as in production.
"""

import logging
from collections.abc import Iterator
from typing import Literal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from argon2 import PasswordHasher, Type
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from cabinet.auth.passwords import Passwords
from cabinet.auth.tables import TeacherRecord
from cabinet.database import allMetadata
from cabinet.main import buildAccounts, createApp
from cabinet.settings import DB_SCHEMA, Settings
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
        connection.execute(text(f'TRUNCATE "{DB_SCHEMA}".teachers, "{DB_SCHEMA}".login_throttle'))
    return sessionmaker(cabinetEngine, expire_on_commit=False)


@pytest.fixture
def hasher() -> CountingHasher:
    return CountingHasher()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def client(cabinetSessions: sessionmaker[Session], hasher: CountingHasher, clock: ManualClock) -> TestClient:
    return TestClient(createApp(SETTINGS, sessionFactory=cabinetSessions, passwords=Passwords(hasher), clock=clock))


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
