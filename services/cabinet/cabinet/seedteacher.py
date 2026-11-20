"""Creates the synthetic teacher of the stand once: uv run python -m cabinet.seedteacher

Email and password come from CABINET_DEMO_TEACHER_EMAIL and CABINET_DEMO_TEACHER_PASSWORD (.env.example).
The password is never printed. Running it again leaves an existing account as it is.
"""

import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cabinet.auth.passwords import Passwords
from cabinet.main import buildAccounts
from cabinet.settings import Settings
from contracts.v1.cabinet.registration import validateEmail
from shared.clock import SystemClock

DEMO_EMAIL = "teacher.demo@example.org"
DEMO_NAME = "Демо Учитель"


def main() -> int:
    password = os.environ.get("CABINET_DEMO_TEACHER_PASSWORD")
    if not password:
        print("Set CABINET_DEMO_TEACHER_PASSWORD (see .env.example)", file=sys.stderr)
        return 2
    legacyLogin = os.environ.get("CABINET_DEMO_TEACHER_LOGIN", "teacher.demo")
    configuredEmail = os.environ.get("CABINET_DEMO_TEACHER_EMAIL", DEMO_EMAIL if legacyLogin == "teacher.demo" else legacyLogin)
    try:
        email = validateEmail(configuredEmail.strip())
    except ValueError:
        print("Set CABINET_DEMO_TEACHER_EMAIL to a valid email address (see .env.example)", file=sys.stderr)
        return 2
    settings = Settings.fromEnv()
    engine = create_engine(settings.databaseUrl)
    try:
        accounts = buildAccounts(settings, sessionmaker(engine, expire_on_commit=False), Passwords(), SystemClock())
        if accounts.find(email) is not None:
            print(f"Teacher {email} already exists")
            return 0
        accounts.create(email, DEMO_NAME, password)
        print(f"Created teacher {email}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
