"""Creates the synthetic teacher of the stand once: uv run python -m cabinet.seedteacher

Login and password come from CABINET_DEMO_TEACHER_LOGIN and CABINET_DEMO_TEACHER_PASSWORD (.env.example).
The password is never printed. Running it again leaves an existing account as it is.
"""

import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cabinet.auth.passwords import Passwords
from cabinet.main import buildAccounts
from cabinet.settings import Settings
from shared.clock import SystemClock

DEMO_LOGIN = "teacher.demo"
DEMO_NAME = "Демо Учитель"


def main() -> int:
    password = os.environ.get("CABINET_DEMO_TEACHER_PASSWORD")
    if not password:
        print("Set CABINET_DEMO_TEACHER_PASSWORD (see .env.example)", file=sys.stderr)
        return 2
    login = os.environ.get("CABINET_DEMO_TEACHER_LOGIN", DEMO_LOGIN)
    settings = Settings.fromEnv()
    engine = create_engine(settings.databaseUrl)
    try:
        accounts = buildAccounts(settings, sessionmaker(engine, expire_on_commit=False), Passwords(), SystemClock())
        if accounts.find(login) is not None:
            print(f"Teacher {login} already exists")
            return 0
        accounts.create(login, DEMO_NAME, password)
        print(f"Created teacher {login}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
