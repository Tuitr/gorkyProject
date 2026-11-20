"""A1.2: the cabinet migrates only its own schema, on an empty stand and again on a current one.

The session fixture (tests/conftest.py) already ran every migration on freshly emptied schemas. Here the
cabinet chain runs again, down to nothing and back, and everything outside `cabinet` must stay as it was.
"""

from collections.abc import Callable

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

from cabinet.database import buildAlembicConfig
from cabinet.settings import DB_SCHEMA, Settings

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
