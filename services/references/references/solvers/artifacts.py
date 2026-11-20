"""Immutable objects of published solver versions in the artifacts bucket (only the reference service may use it).

An object is written once (If-None-Match: *). Finding the same bytes already there is fine: a builder that
crashed after the upload and before the database commit repeats the same write. Different bytes under the key
are a conflict, and the builder takes the next version number instead.
"""

import hashlib
import os
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from shared.buckets import ARTIFACTS_BUCKET

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class ArtifactConflict(Exception):
    """Other bytes are already stored under this key."""


class ArtifactStore(Protocol):
    def put(self, key: str, body: bytes, contentType: str) -> None: ...


class MemoryArtifactStore:
    """For tests: the same write-once rule as S3."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, body: bytes, contentType: str) -> None:
        existing = self.objects.get(key)
        if existing is not None and existing != body:
            raise ArtifactConflict(key)
        self.objects[key] = body


class S3ArtifactStore:
    def __init__(self, client: "S3Client", bucket: str = ARTIFACTS_BUCKET) -> None:
        self.client = client
        self.bucket = bucket

    @classmethod
    def fromEnv(cls) -> "S3ArtifactStore":
        import boto3
        from botocore.client import Config

        endpoint = os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:18333")
        local = urlsplit(endpoint).hostname in ("localhost", "127.0.0.1", "::1")
        return cls(
            boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=os.environ.get("REFERENCES_S3_ACCESS_KEY_ID", "gorky-references"),
                aws_secret_access_key=os.environ.get("REFERENCES_S3_SECRET_ACCESS_KEY", "gorky-references-secret"),
                region_name=os.environ.get("S3_REGION", "us-east-1"),
                config=Config(
                    connect_timeout=3, read_timeout=10, proxies={} if local else None, s3={"addressing_style": "path"}
                ),
            )
        )

    def put(self, key: str, body: bytes, contentType: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=body, IfNoneMatch="*", ContentType=contentType)
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise
            stored = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            if hashlib.sha256(stored).digest() != hashlib.sha256(body).digest():
                raise ArtifactConflict(key) from error
