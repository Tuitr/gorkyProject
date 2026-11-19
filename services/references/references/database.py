"""PostgreSQL schema `refs` shared by the registry and the model gateway (developer 3 only).

Both table modules register on one Base, so Alembic and the drift test see every table.
"""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from references.settings import DB_SCHEMA

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"


class Base(DeclarativeBase):
    metadata = MetaData(schema=DB_SCHEMA)


def allMetadata() -> MetaData:
    """Base.metadata after importing every table module."""
    import references.gateway.tables  # noqa: F401
    import references.registry.tables  # noqa: F401

    return Base.metadata


def buildAlembicConfig(databaseUrl: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["databaseUrl"] = databaseUrl
    return config
