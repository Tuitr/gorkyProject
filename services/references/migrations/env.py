"""Alembic environment for the `refs` schema only. Other services' tables are never touched."""

from alembic import context
from sqlalchemy import create_engine, text

from references.registry.tables import Base
from references.settings import DB_SCHEMA, Settings

config = context.config
url = config.attributes.get("databaseUrl") or Settings.fromEnv().databaseUrl


def runMigrations() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"'))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            version_table_schema=DB_SCHEMA,
            include_schemas=True,
            include_name=lambda name, kind, parent: kind != "schema" or name == DB_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


runMigrations()
