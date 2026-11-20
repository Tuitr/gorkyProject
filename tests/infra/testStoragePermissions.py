"""A1.2: every service has its own S3 key and sees only its buckets (deploy/seaweedfs/s3.json).

The cabinet writes sources and reads results, grading reads sources and writes results, the reference
service keeps its artifacts to itself. Each allowed operation must work and each forbidden one must be
refused by the storage itself, not by our code. Keys come from the environment (see .env.example).
"""

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from mypy_boto3_s3 import S3Client

from shared.buckets import ARTIFACTS_BUCKET, BUCKETS, RESULTS_BUCKET, SOURCES_BUCKET

S3_CONFIG = Path(__file__).parents[2] / "deploy" / "seaweedfs" / "s3.json"
START_HINT = "Run: docker compose -f deploy/compose.yaml up -d --wait"
ADMIN = "admin"
SERVICE_KEYS = {
    ADMIN: ("S3_ACCESS_KEY_ID", "gorky-dev", "S3_SECRET_ACCESS_KEY", "gorky-dev-secret"),
    "cabinet": ("CABINET_S3_ACCESS_KEY_ID", "gorky-cabinet", "CABINET_S3_SECRET_ACCESS_KEY", "gorky-cabinet-secret"),
    "grading": ("GRADING_S3_ACCESS_KEY_ID", "gorky-grading", "GRADING_S3_SECRET_ACCESS_KEY", "gorky-grading-secret"),
    "references": (
        "REFERENCES_S3_ACCESS_KEY_ID",
        "gorky-references",
        "REFERENCES_S3_SECRET_ACCESS_KEY",
        "gorky-references-secret",
    ),
}

CASES = [
    ("grading", "get", SOURCES_BUCKET, True),
    ("grading", "put", SOURCES_BUCKET, False),
    ("grading", "put", RESULTS_BUCKET, True),
    ("grading", "get", ARTIFACTS_BUCKET, False),
    ("cabinet", "put", SOURCES_BUCKET, True),
    ("cabinet", "get", RESULTS_BUCKET, True),
    ("cabinet", "put", RESULTS_BUCKET, False),
    ("cabinet", "get", ARTIFACTS_BUCKET, False),
    ("references", "put", ARTIFACTS_BUCKET, True),
    ("references", "get", ARTIFACTS_BUCKET, True),
    ("references", "get", SOURCES_BUCKET, False),
    ("references", "put", RESULTS_BUCKET, False),
]


def buildClient(service: str) -> S3Client:
    keyVar, keyDefault, secretVar, secretDefault = SERVICE_KEYS[service]
    client: S3Client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:18333"),
        aws_access_key_id=os.environ.get(keyVar, keyDefault),
        aws_secret_access_key=os.environ.get(secretVar, secretDefault),
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        # The stand is local: never route it through a system proxy (Windows VPN clients set one for 127.0.0.1 too).
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"max_attempts": 1},
            s3={"addressing_style": "path"},
            proxies={"http": "", "https": ""},
        ),
    )
    return client


@pytest.fixture(scope="module")
def probes() -> Iterator[dict[str, str]]:
    """As the administrator: every bucket exists and holds one probe object; all of it is removed afterwards."""
    admin = buildClient(ADMIN)
    try:
        existing = {bucket.get("Name") for bucket in admin.list_buckets().get("Buckets", [])}
    except (BotoCoreError, ClientError) as error:
        pytest.fail(f"S3 is not reachable. {START_HINT}\n{error}", pytrace=False)
    for bucket in BUCKETS:
        if bucket not in existing:
            admin.create_bucket(Bucket=bucket)
    keys = {bucket: f"probe/{uuid.uuid4().hex}.txt" for bucket in BUCKETS}
    for bucket, key in keys.items():
        admin.put_object(Bucket=bucket, Key=key, Body=b"probe")
    yield keys
    for bucket in BUCKETS:
        listed = admin.list_objects_v2(Bucket=bucket, Prefix="probe/").get("Contents", [])
        for item in listed:
            admin.delete_object(Bucket=bucket, Key=item["Key"])


def exists(bucket: str, key: str) -> bool:
    try:
        buildClient(ADMIN).head_object(Bucket=bucket, Key=key)
    except ClientError:
        return False
    return True


@pytest.mark.parametrize(("service", "action", "bucket", "allowed"), CASES, ids=[f"{s}-{a}-{b}" for s, a, b, _ in CASES])
def testServiceKeySeesOnlyItsBuckets(
    probes: dict[str, str], service: str, action: str, bucket: str, allowed: bool
) -> None:
    client = buildClient(service)
    key = probes[bucket] if action == "get" else f"probe/{service}-{uuid.uuid4().hex}.txt"

    try:
        if action == "get":
            client.get_object(Bucket=bucket, Key=key)["Body"].read()
        else:
            client.put_object(Bucket=bucket, Key=key, Body=b"written")
        refusal = None
    except ClientError as error:
        refusal = error.response.get("Error", {}).get("Code")

    assert refusal == (None if allowed else "AccessDenied")
    if action == "put":
        assert exists(bucket, key) is allowed


def testServiceKeysAreScopedToKnownBuckets() -> None:
    """No service key gets an unscoped right ("Read" means every bucket) or a bucket the stand does not know."""
    identities = json.loads(S3_CONFIG.read_text(encoding="utf-8"))["identities"]
    services = {identity["name"]: identity["actions"] for identity in identities if identity["name"] != "gorky"}

    assert set(services) == set(SERVICE_KEYS) - {ADMIN}
    for actions in services.values():
        for action in actions:
            right, _, bucket = action.partition(":")
            assert right in {"Read", "Write", "List"} and bucket in BUCKETS, action
