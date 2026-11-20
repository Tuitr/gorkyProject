"""Run grading migrations with `uv run alembic -c services/grading/alembic.ini upgrade head`."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from grading.settings import DB_SCHEMA

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"


class Base(DeclarativeBase):
    metadata = MetaData(schema=DB_SCHEMA)


def buildAlembicConfig(databaseUrl: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["databaseUrl"] = databaseUrl
    return config
