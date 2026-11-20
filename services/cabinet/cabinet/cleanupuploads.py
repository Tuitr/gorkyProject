"""Remove unreferenced originals under one upload prefix."""

import argparse
import logging
import re
from contextlib import closing

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from cabinet.attempts import AttemptRecord
from cabinet.documents import buildS3Client
from cabinet.settings import Settings
from cabinet.uploads import UploadFileRecord, lockUploadStorage
from shared.buckets import SOURCES_BUCKET

LOGGER = logging.getLogger(__name__)
UUID_KEY = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"


def cleanUpload(sessions: sessionmaker[Session], settings: Settings, uploadId: str) -> int:
    if not re.fullmatch(UUID_KEY, uploadId):
        raise ValueError("Expected a canonical upload UUID")
    prefix = f"uploads/{uploadId}/"
    removed = 0
    with sessions.begin() as db:
        # If COMMIT lost its response, wait for that transaction before checking references.
        lockUploadStorage(db, uploadId)
        referenced = set(db.scalars(
            select(UploadFileRecord.objectKey).where(
                UploadFileRecord.bucket == SOURCES_BUCKET, UploadFileRecord.objectKey.startswith(prefix, autoescape=True),
            ).union(
                select(AttemptRecord.objectKey).where(
                    AttemptRecord.bucket == SOURCES_BUCKET, AttemptRecord.objectKey.startswith(prefix, autoescape=True),
                )
            )
        ))
        with closing(buildS3Client(settings)) as client:
            pages = client.get_paginator("list_objects_v2").paginate(Bucket=SOURCES_BUCKET, Prefix=prefix)
            for page in pages:
                for item in page.get("Contents", []):
                    key = item.get("Key", "")
                    if key not in referenced and re.fullmatch(f"{prefix}{UUID_KEY}\\.docx", key):
                        client.delete_object(Bucket=SOURCES_BUCKET, Key=key)
                        removed += 1
    return removed


def tryCleanUpload(sessions: sessionmaker[Session], settings: Settings, uploadId: str) -> None:
    try:
        cleanUpload(sessions, settings, uploadId)
    except (SQLAlchemyError, ClientError, BotoCoreError) as error:
        LOGGER.warning("Upload cleanup can be retried uploadId=%s errorType=%s", uploadId, type(error).__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uploadId", help="the UUID in sources/uploads/<uploadId>/")
    args = parser.parse_args()
    settings = Settings.fromEnv()
    engine = create_engine(settings.databaseUrl)
    try:
        removed = cleanUpload(sessionmaker(engine), settings, args.uploadId)
    except ValueError as error:
        parser.error(str(error))
    finally:
        engine.dispose()
    print(f"Removed {removed} unreferenced DOCX objects for {args.uploadId}")


if __name__ == "__main__":
    main()
