from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1.references.reference import ReferenceReady, ReferenceUnsupported
from references.registry.store import ReferenceStore, recordToResult
from references.registry.tables import Base, ReferenceRecord
from references.settings import DB_SCHEMA

from .conftest import buildAlembicConfig


def buildRecord(referenceId: str, **fields: object) -> ReferenceRecord:
    base: dict[str, object] = {
        "referenceId": referenceId,
        "lookupKey": f"k1:{referenceId}",
        "assignmentId": "w1",
        "assignmentRev": 1,
        "taskId": "t1",
        "statement": "Длина вектора (3; 4)",
        "constraints": {},
    }
    return ReferenceRecord(**(base | fields))


def testMigrationsRunTwiceOnlyInOwnSchema(engine: Engine, databaseUrl: str) -> None:
    command.upgrade(buildAlembicConfig(databaseUrl), "head")

    inspector = inspect(engine)
    assert "reference_records" in inspector.get_table_names(schema=DB_SCHEMA)
    assert "reference_records" not in inspector.get_table_names(schema="public")


def onlyOwnSchema(name: str | None, kind: str, parent: dict[str, Any]) -> bool:
    return kind != "schema" or name == DB_SCHEMA


def testTablesMatchMigrations(engine: Engine) -> None:
    """tables.py and the Alembic migrations describe the same columns and indexes."""
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"include_schemas": True, "include_name": onlyOwnSchema, "version_table_schema": DB_SCHEMA}
        )
        assert compare_metadata(context, Base.metadata) == []


def testReadyRecordRoundTrip(sessionFactory: sessionmaker[Session]) -> None:
    store = ReferenceStore(sessionFactory)
    store.add(
        buildRecord(
            "r1", state="ready", source="fixture", schemaId="scalar.v1", value={"exact": "5"}, solverVersion=None
        )
    )

    stored = store.get("r1")

    assert stored is not None
    result = recordToResult(stored)
    assert isinstance(result, ReferenceReady)
    assert result.value == {"exact": "5"}
    assert result.solverVersion is None


def testZeroIsStoredAsAValue(sessionFactory: sessionmaker[Session]) -> None:
    store = ReferenceStore(sessionFactory)
    store.add(buildRecord("r0", state="ready", source="fixture", schemaId="scalar.v1", value={"exact": "0"}))

    stored = store.get("r0")

    assert stored is not None
    assert recordToResult(stored) == ReferenceReady(
        referenceId="r0", state="ready", source="fixture", schemaId="scalar.v1", value={"exact": "0"}, solverVersion=None
    )


def testUnsupportedRecordHasReasonAndNoValue(sessionFactory: sessionmaker[Session]) -> None:
    store = ReferenceStore(sessionFactory)
    store.add(buildRecord("r3", state="unsupported", reasonCode="unknown_task"))

    stored = store.get("r3")

    assert stored is not None
    assert stored.value is None
    assert recordToResult(stored) == ReferenceUnsupported(referenceId="r3", state="unsupported", reasonCode="unknown_task")


@pytest.mark.parametrize(
    "fields",
    [
        {"state": "ready", "source": "fixture", "schemaId": "scalar.v1"},
        {"state": "ready", "source": "fixture", "schemaId": "scalar.v1", "value": None},
        {"state": "unsupported"},
        {"state": "unsupported", "reasonCode": "unknown_task", "value": {"exact": "0"}},
        {"state": "ready", "source": "generated", "schemaId": "scalar.v1", "value": {"exact": "5"}},
        {"state": "done"},
    ],
    ids=["readyWithoutValue", "readyWithNoneValue", "refusalWithoutReason", "refusalWithValue", "generatedWithoutVersion", "unknownState"],
)
def testDatabaseRejectsInconsistentRecords(sessionFactory: sessionmaker[Session], fields: dict[str, object]) -> None:
    store = ReferenceStore(sessionFactory)

    with pytest.raises(IntegrityError):
        store.add(buildRecord("bad", **fields))


def testMissingRecordIsNone(sessionFactory: sessionmaker[Session]) -> None:
    assert ReferenceStore(sessionFactory).get("nope") is None
