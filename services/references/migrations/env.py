"""Alembic environment for the `refs` schema only.

It runs as gorky_references, which owns `refs` and has no rights in `cabinet` or `grading`. The schema
itself is created by the stand administrator (deploy/postgres/init/10-roles.sql), not here.
"""

from alembic import context
from sqlalchemy import create_engine, text

from references.database import allMetadata
from references.settings import DB_SCHEMA, Settings

config = context.config
url = config.attributes.get("databaseUrl") or Settings.fromEnv().databaseUrl


def runMigrations() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        found = connection.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :name"), {"name": DB_SCHEMA})
        if found.first() is None:
            raise RuntimeError(
                f'Schema "{DB_SCHEMA}" does not exist. Apply deploy/postgres/init/10-roles.sql as the stand '
                "administrator first (CONTRIBUTING.md, section «Стенд»)."
            )
        connection.commit()  # end the check's transaction, so begin_transaction() below commits the migrations
        context.configure(
            connection=connection,
            target_metadata=allMetadata(),
            version_table_schema=DB_SCHEMA,
            include_schemas=True,
            include_name=lambda name, kind, parent: kind != "schema" or name == DB_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


runMigrations()
