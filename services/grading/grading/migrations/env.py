"""Apply migrations to the grading schema created by the stand administrator."""

from alembic import context
from sqlalchemy import create_engine

import grading.tables
from grading.database import Base
from grading.settings import DB_SCHEMA, Settings

config = context.config
url = config.attributes.get("databaseUrl") or Settings.fromEnv().databaseUrl


def runMigrations() -> None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                version_table_schema=DB_SCHEMA,
                include_schemas=True,
                include_name=lambda name, kind, parent: kind != "schema" or name == DB_SCHEMA,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


runMigrations()
