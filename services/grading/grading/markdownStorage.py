"""Write immutable Markdown and verify its bytes in the results bucket."""

import hashlib
import os
from collections.abc import Callable
from contextlib import closing
from io import BytesIO
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from contracts.v1.grading.checkresult import MarkdownRef
from grading.results import ArtifactUnavailable

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class MarkdownStorage:
    def __init__(self, client: "S3Client") -> None:
        self.client = client

    @classmethod
    def fromEnv(cls) -> "MarkdownStorage":
        endpoint = os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:18333")
        proxies: dict[str, str] | None = {} if urlsplit(endpoint).hostname in ("localhost", "127.0.0.1", "::1") else None
        return cls(boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.environ.get("GRADING_S3_ACCESS_KEY_ID", "gorky-grading"),
            aws_secret_access_key=os.environ.get("GRADING_S3_SECRET_ACCESS_KEY", "gorky-grading-secret"),
            region_name=os.environ.get("S3_REGION", "us-east-1"),
            config=Config(connect_timeout=3, read_timeout=5, proxies=proxies, s3={"addressing_style": "path"}),
        ))

    def require(self, ref: MarkdownRef) -> None:
        self._verify(ref)

    def download(self, ref: MarkdownRef) -> bytes:
        with BytesIO() as buffer:
            self._verify(ref, buffer.write)
            return buffer.getvalue()

    def _verify(self, ref: MarkdownRef, writeChunk: Callable[[bytes], object] | None = None) -> None:
        digest = hashlib.sha256()
        try:
            response = self.client.get_object(Bucket=ref.bucket, Key=ref.key)
            with closing(response["Body"]) as body:
                for chunk in body.iter_chunks(chunk_size=65536):
                    digest.update(chunk)
                    if writeChunk is not None:
                        writeChunk(chunk)
        except (BotoCoreError, ClientError) as error:
            raise ArtifactUnavailable("cannot read the result artifact") from error
        if response["ContentLength"] == 0 or digest.hexdigest() != ref.sha256:
            raise ArtifactUnavailable("result artifact bytes differ from the saved reference")

    def save(self, checkId: str, resultRev: int, markdown: str) -> MarkdownRef:
        if resultRev < 1 or not markdown.strip():
            raise ValueError("expected a positive revision and non-empty Markdown")
        body = markdown.encode("utf-8")
        checkKey = hashlib.sha256(checkId.encode("utf-8")).hexdigest()
        ref = MarkdownRef(
            bucket="results", key=f"checks/{checkKey}/{resultRev}.md", sha256=hashlib.sha256(body).hexdigest(),
        )
        try:
            self.client.put_object(
                Bucket=ref.bucket, Key=ref.key, Body=body, IfNoneMatch="*",
                ContentType="text/markdown; charset=utf-8",
                ContentDisposition=f'attachment; filename="review-{resultRev}.md"',
            )
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise ArtifactUnavailable("cannot write the result artifact") from error
            # A previous attempt may have uploaded the file before the database commit.
            self.require(ref)
        except BotoCoreError as error:
            raise ArtifactUnavailable("cannot write the result artifact") from error
        return ref
