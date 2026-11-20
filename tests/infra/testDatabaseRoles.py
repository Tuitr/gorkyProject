"""A1.2: each service logs in as its own PostgreSQL role and can touch only its own schema.

deploy/postgres/init/10-roles.sql creates the roles gorky_cabinet, gorky_grading and gorky_references;
each one owns one schema (cabinet, grading, refs) and gets no rights anywhere else. Every case runs in a
transaction that is rolled back, so the stand keeps nothing.
"""

import os
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

import cabinet.settings
import references.settings

GRADING_DATABASE_URL = "postgresql+psycopg://gorky_grading:gorky_grading@127.0.0.1:15432/gorky?connect_timeout=3"
OWNERS = {"cabinet": "gorky_cabinet", "grading": "gorky_grading", "refs": "gorky_references"}
DENIED = {"42501", "3F000"}
"""insufficient_privilege, invalid_schema_name (nowhere to create an unqualified table)."""


def roleUrl(role: str) -> str:
    return {
        "gorky_cabinet": cabinet.settings.Settings.fromEnv().databaseUrl,
        "gorky_grading": os.environ.get("GRADING_DATABASE_URL", GRADING_DATABASE_URL),
        "gorky_references": references.settings.Settings.fromEnv().databaseUrl,
    }[role]


CASES = [
    ("gorky_grading", "CREATE TABLE grading.probe (id int)", True),
    ("gorky_grading", "SELECT count(*) FROM refs.reference_records", False),
    ("gorky_grading", "SELECT count(*) FROM cabinet.alembic_version", False),
    ("gorky_grading", "CREATE TABLE refs.probe (id int)", False),
    ("gorky_grading", "CREATE TABLE public.probe (id int)", False),
    ("gorky_grading", "CREATE TABLE probe (id int)", False),
    ("gorky_grading", "CREATE SCHEMA probe", False),
    ("gorky_cabinet", "CREATE TABLE cabinet.probe (id int)", True),
    ("gorky_cabinet", "SELECT count(*) FROM refs.reference_records", False),
    ("gorky_cabinet", "CREATE TABLE grading.probe (id int)", False),
    ("gorky_references", "SELECT count(*) FROM refs.reference_records", True),
    ("gorky_references", "SELECT count(*) FROM cabinet.alembic_version", False),
    ("gorky_references", "DROP TABLE cabinet.alembic_version", False),
]


@pytest.mark.parametrize(("role", "statement", "allowed"), CASES, ids=[f"{r}-{i}" for i, (r, _, _) in enumerate(CASES)])
def testRoleReachesOnlyItsSchema(adminEngine: Engine, role: str, statement: str, allowed: bool) -> None:
    engine = create_engine(roleUrl(role))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT current_user")).scalar_one() == role
            try:
                connection.execute(text(statement))
                refusal = None
            except DBAPIError as error:
                refusal = getattr(error.orig, "sqlstate", None)
            connection.rollback()
    finally:
        engine.dispose()

    assert (refusal is None) is allowed, refusal
    assert refusal is None or refusal in DENIED


def testServiceRolesOwnOneSchemaAndHaveNoAdminPowers(adminEngine: Engine) -> None:
    with adminEngine.connect() as connection:
        owners: dict[str, str] = dict(
            connection.execute(
                text("SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = ANY(:names)"),
                {"names": list(OWNERS)},
            ).all()
        )
        powers = connection.execute(
            text(
                "SELECT rolname, rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls "
                "FROM pg_roles WHERE rolname = ANY(:roles)"
            ),
            {"roles": list(OWNERS.values())},
        ).all()

    assert owners == OWNERS
    assert sorted(powers) == sorted((role, False) for role in OWNERS.values())


def testRolesScriptRepairsAnOlderStand(adminEngine: Engine, rolesScript: Callable[[Engine], None]) -> None:
    """Run again on a stand where the administrator once created a table: the script hands it to the service."""
    with adminEngine.begin() as connection:
        connection.execute(text("CREATE TABLE refs.legacy_probe (id int GENERATED ALWAYS AS IDENTITY)"))
    try:
        rolesScript(adminEngine)
        rolesScript(adminEngine)
        with adminEngine.connect() as connection:
            owner = connection.execute(
                text("SELECT tableowner FROM pg_tables WHERE schemaname = 'refs' AND tablename = 'legacy_probe'")
            ).scalar_one()
        engine = create_engine(roleUrl("gorky_references"))
        try:
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO refs.legacy_probe DEFAULT VALUES"))
        finally:
            engine.dispose()
    finally:
        with adminEngine.begin() as connection:
            connection.execute(text("DROP TABLE refs.legacy_probe"))

    assert owner == "gorky_references"
