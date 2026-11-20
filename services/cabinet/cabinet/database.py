"""PostgreSQL schema `cabinet` (developer 1). Tables of the cabinet register on Base."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from cabinet.settings import DB_SCHEMA

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"


class Base(DeclarativeBase):
    metadata = MetaData(schema=DB_SCHEMA)


def allMetadata() -> MetaData:
    """Base.metadata after importing every table module."""
    import cabinet.auth.tables  # noqa: F401  (registers the tables on Base)

    import cabinet.classes  # noqa: F401

    return Base.metadata


def buildAlembicConfig(databaseUrl: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["databaseUrl"] = databaseUrl
    return config
