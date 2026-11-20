"""A1.2: the cabinet migrates only its own schema, on an empty stand and again on a current one.

The session fixture (tests/conftest.py) already ran every migration on freshly emptied schemas. Here the
cabinet chain runs again, down to nothing and back, and everything outside `cabinet` must stay as it was.
"""

import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing
from dataclasses import replace

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from httpx import MockTransport, Request, Response
from psycopg.errors import UniqueViolation
from sqlalchemy import Engine, create_engine, inspect, make_url, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from cabinet.attempts import AttemptRecord, CheckRecord
from cabinet.database import buildAlembicConfig
from cabinet.documents import buildS3Client, storeDocx
from cabinet.main import createApp
from cabinet.settings import DB_SCHEMA, Settings
from cabinet.uploads import UploadFileRecord
from contracts.v1.cabinet.uploads import UploadProgress

OBJECTS = text(
    "SELECT n.nspname, c.relname, c.relkind, pg_get_userbyid(c.relowner) FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = ANY(:schemas) ORDER BY 1, 2"
)


def otherSchemas(adminEngine: Engine) -> list[tuple[object, ...]]:
    """Every table, index and sequence of grading and refs with its owner, and the refs revision."""
    with adminEngine.connect() as connection:
        objects = [tuple(row) for row in connection.execute(OBJECTS, {"schemas": ["grading", "refs"]})]
        revision = connection.execute(text("SELECT version_num FROM refs.alembic_version")).scalar_one()
    return [*objects, ("refs revision", revision)]


def cabinetRevision(adminEngine: Engine) -> str | None:
    with adminEngine.connect() as connection:
        found = connection.execute(text(f'SELECT version_num FROM "{DB_SCHEMA}".alembic_version')).scalar()
    return None if found is None else str(found)


def testMigrationsRunAgainOnACurrentStand(adminEngine: Engine) -> None:
    config = buildAlembicConfig(Settings.fromEnv().databaseUrl)
    head = ScriptDirectory.from_config(config).get_current_head()
    before = otherSchemas(adminEngine)

    assert cabinetRevision(adminEngine) == head
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert cabinetRevision(adminEngine) is None
    command.upgrade(config, "head")

    assert cabinetRevision(adminEngine) == head
    assert otherSchemas(adminEngine) == before


def testEveryCabinetObjectBelongsToTheCabinetRole(adminEngine: Engine) -> None:
    with adminEngine.connect() as connection:
        owners = {row[3] for row in connection.execute(OBJECTS, {"schemas": [DB_SCHEMA]})}

    assert owners == {"gorky_cabinet"}


def testMigrationStopsWithAHintWhenTheSchemaIsMissing(
    adminEngine: Engine, rolesScript: Callable[[Engine], None]
) -> None:
    """The cabinet role cannot create its schema; only the roles script does, so the migration says so."""
    config = buildAlembicConfig(Settings.fromEnv().databaseUrl)
    with adminEngine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{DB_SCHEMA}" CASCADE'))
    try:
        with pytest.raises(RuntimeError, match="10-roles.sql"):
            command.upgrade(config, "head")
    finally:
        rolesScript(adminEngine)
        command.upgrade(config, "head")

    assert cabinetRevision(adminEngine) == ScriptDirectory.from_config(config).get_current_head()


@pytest.fixture
def migrationDatabase(adminDatabaseUrl: str, rolesScript: Callable[[Engine], None]) -> Iterator[Engine]:
    name = f"cabinet_migration_{uuid.uuid4().hex}"
    with ExitStack() as resources:
        admin = create_engine(adminDatabaseUrl, isolation_level="AUTOCOMMIT")
        resources.callback(admin.dispose)
        connection = resources.enter_context(admin.connect())
        connection.execute(text(f'CREATE DATABASE "{name}"'))
        resources.callback(connection.execute, text(f'DROP DATABASE "{name}" WITH (FORCE)'))

        isolatedAdmin = create_engine(admin.url.set(database=name))
        resources.callback(isolatedAdmin.dispose)
        rolesScript(isolatedAdmin)

        isolated = create_engine(make_url(Settings.fromEnv().databaseUrl).set(database=name))
        resources.callback(isolated.dispose)
        yield isolated


def testMigration0016PreservesExistingWorkAndRollsBackFailedDowngrade(
    migrationDatabase: Engine, handoffDocuments: tuple[bytes, bytes],
) -> None:
    engine = migrationDatabase
    settings = replace(
        Settings.fromEnv(), databaseUrl=engine.url.render_as_string(hide_password=False), gradingServiceToken="test-cabinet",
    )
    config = buildAlembicConfig(settings.databaseUrl)
    command.upgrade(config, "0015")
    assert cabinetRevision(engine) == "0015"
    assignmentId, attemptId, checkId, uploadId, fileId = (str(uuid.uuid4()) for _ in range(5))
    assignmentKey = f"assignments/{assignmentId}/1/{uuid.uuid4().hex}.docx"
    submissionKey = f"uploads/{uploadId}/{fileId}.docx"
    variantDocx, original = handoffDocuments
    assignmentPath = f"/v1/assignments/{assignmentId}"
    oldUpload = f"/v1/uploads/{uploadId}"
    oldCheck = f"/v1/checks/{checkId}"

    def grading(request: Request) -> Response:
        return Response(404, json={
            "code": "not_found", "message": "Check not found", "retryable": False, "traceId": request.headers["X-Trace-Id"],
        })

    sessions = sessionmaker(engine, expire_on_commit=False)
    app = createApp(settings, sessionFactory=sessions, gradingTransport=MockTransport(grading))
    with TestClient(app, base_url="https://testserver") as client, closing(buildS3Client(settings)) as storage:
        credentials = {"email": "migration.teacher@example.org", "password": uuid.uuid4().hex}
        assert client.post("/v1/teachers/register", json={**credentials, "displayName": "Учитель"}).status_code == 201
        assert client.post("/v1/sessions", json=credentials).status_code == 200
        roster = client.post("/v1/classes", json={"name": "8А"})
        assert roster.status_code == 201
        classId = roster.json()["classId"]
        student = client.post(f"/v1/classes/{classId}/students", json={"name": "Тестов Павел"})
        assert student.status_code == 201

        try:
            variant = storeDocx(storage, assignmentKey, variantDocx)
            submission = storeDocx(storage, submissionKey, original)
            # Insert the records as 0015 stored them, before the two deleted_at columns existed.
            data = {
                "assignment": assignmentId, "class": classId, "student": student.json()["studentId"],
                "attempt": attemptId, "check": checkId, "upload": uploadId, "file": fileId,
                "variantKey": variant.key, "variantSha": variant.sha256, "bucket": submission.bucket,
                "submissionKey": submission.key, "submissionSha": submission.sha256,
            }
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO cabinet.assignments"
                    " (assignment_id, assignment_rev, class_id, assignment_date, requirements, bucket, object_key, sha256)"
                    " VALUES (:assignment, 1, :class, '2025-12-12', '[]', :bucket, :variantKey, :variantSha)"
                ), data)
                connection.execute(text(
                    "INSERT INTO cabinet.attempts"
                    " (attempt_id, assignment_id, student_id, bucket, object_key, sha256, submitted_at)"
                    " VALUES (:attempt, :assignment, :student, :bucket, :submissionKey, :submissionSha, now())"
                ), data)
                connection.execute(text(
                    "INSERT INTO cabinet.attempt_checks (check_id, attempt_id, assignment_id, assignment_rev)"
                    " VALUES (:check, :attempt, :assignment, 1)"
                ), data)
                connection.execute(text(
                    "INSERT INTO cabinet.uploads (upload_id, assignment_id, assignment_rev, uploaded_at)"
                    " VALUES (:upload, :assignment, 1, now())"
                ), data)
                connection.execute(text(
                    "INSERT INTO cabinet.upload_files"
                    " (file_id, upload_id, original_name, bucket, object_key, sha256, status, student_id, attempt_id, check_id)"
                    " VALUES (:file, :upload, 'testov-pavel.docx', :bucket, :submissionKey, :submissionSha,"
                    " 'bound', :student, :attempt, :check)"
                ), data)

            command.upgrade(config, "0016")
            assert cabinetRevision(engine) == "0016"
            before = client.get(oldUpload)
            assert before.status_code == 200, before.text
            (existing,) = UploadProgress.model_validate_json(before.content).files
            assert (existing.attemptId, existing.checkId) == (attemptId, checkId)
            assert client.get(oldCheck).status_code == 200
            assert client.delete(f"/v1/attempts/{attemptId}?confirm=true").status_code == 204

            uploaded = client.post(
                f"{assignmentPath}/uploads", data={"assignmentRev": "1"}, files={"file": ("testov-pavel.docx", original)},
            )
            assert uploaded.status_code == 202, uploaded.text
            newUpload = f"/v1/uploads/{uploaded.json()['uploadId']}"
            progress = client.get(newUpload)
            assert progress.status_code == 200, progress.text
            (current,) = UploadProgress.model_validate_json(progress.content).files
            assert current.status == "bound" and current.attemptId and current.attemptId != attemptId
            assert current.checkId and current.checkId != checkId
            assert current.studentId == existing.studentId

            attempts = select(
                AttemptRecord.attemptId, AttemptRecord.deletedAt, AttemptRecord.sha256, AttemptRecord.objectKey,
            ).order_by(AttemptRecord.attemptId)
            checks = select(CheckRecord.checkId, CheckRecord.attemptId)
            with sessions() as db:
                rows = db.execute(attempts).all()
                byId = {row.attemptId: row for row in rows}
                checkPairs = set(db.execute(checks).all())
            assert byId.keys() == {attemptId, current.attemptId}
            assert byId[attemptId].deletedAt is not None and byId[current.attemptId].deletedAt is None
            assert byId[attemptId].objectKey == submissionKey
            assert byId[attemptId].sha256 == byId[current.attemptId].sha256 == submission.sha256
            assert checkPairs == {(checkId, attemptId), (current.checkId, current.attemptId)}

            with pytest.raises(IntegrityError) as refusal:
                command.downgrade(config, "0015")
            assert isinstance(refusal.value.orig, UniqueViolation)
            assert refusal.value.orig.diag.constraint_name == "uq_attempts_submission"

            assert cabinetRevision(engine) == "0016"
            inspector = inspect(engine)
            for table in ("assignments", "attempts"):
                assert "deleted_at" in {column["name"] for column in inspector.get_columns(table, schema=DB_SCHEMA)}
            (index,) = [i for i in inspector.get_indexes("attempts", schema=DB_SCHEMA) if i["name"] == "uq_attempts_submission"]
            assert index["unique"] and index["column_names"] == ["assignment_id", "student_id", "sha256"]
            assert index["dialect_options"]["postgresql_where"] == "(deleted_at IS NULL)"
            with sessions() as db:
                assert db.execute(attempts).all() == rows
                assert set(db.execute(checks).all()) == checkPairs

            hidden = client.get(oldUpload)
            assert hidden.status_code == 200 and hidden.json()["files"] == []
            assert client.get(oldCheck).status_code == 404
            assert client.get(newUpload).json() == progress.json()
            latest = client.get(f"/v1/checks/{current.checkId}")
            assert latest.status_code == 200 and latest.json()["attemptId"] == current.attemptId
        finally:
            with engine.connect() as connection:
                keys = set(connection.scalars(select(UploadFileRecord.objectKey)))
            for key in keys | {assignmentKey, submissionKey}:
                storage.delete_object(Bucket="sources", Key=key)
