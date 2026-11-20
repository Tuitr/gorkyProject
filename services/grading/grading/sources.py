"""Read immutable originals that an event points to, from the sources bucket."""

import hashlib
import os
from contextlib import closing
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from grading.intake import IncomingDocRef
from shared.buckets import SOURCES_BUCKET
from shared.submissionmedia import MAX_SOURCE_BYTES

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

class SourceChanged(ValueError):
    """The stored object differs from the sha256 in the event: never grade other bytes."""


class SourceUnavailable(RuntimeError):
    """S3 did not answer or the object is missing for now; the job is repeated later."""


class SourceDocuments:
    def __init__(self, client: "S3Client") -> None:
        self.client = client

    @classmethod
    def fromEnv(cls) -> "SourceDocuments":
        endpoint = os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:18333")
        proxies: dict[str, str] | None = {} if urlsplit(endpoint).hostname in ("localhost", "127.0.0.1", "::1") else None
        return cls(boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.environ.get("GRADING_S3_ACCESS_KEY_ID", "gorky-grading"),
            aws_secret_access_key=os.environ.get("GRADING_S3_SECRET_ACCESS_KEY", "gorky-grading-secret"),
            region_name=os.environ.get("S3_REGION", "us-east-1"),
            config=Config(connect_timeout=3, read_timeout=10, proxies=proxies, s3={"addressing_style": "path"}),
        ))

    def load(self, ref: IncomingDocRef) -> bytes:
        if ref.bucket != SOURCES_BUCKET:
            raise SourceChanged(f"source documents live only in the {SOURCES_BUCKET} bucket")
        try:
            response = self.client.get_object(Bucket=ref.bucket, Key=ref.key)
            with closing(response["Body"]) as body:
                data = body.read(MAX_SOURCE_BYTES + 1)
        except (BotoCoreError, ClientError) as error:
            raise SourceUnavailable("cannot read the source document") from error
        if len(data) > MAX_SOURCE_BYTES or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise SourceChanged("source document bytes differ from the event")
        return data
