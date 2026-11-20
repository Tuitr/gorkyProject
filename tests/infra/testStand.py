"""Smoke checks of the local stand: PostgreSQL, Redis Streams and S3 (SeaweedFS).

Start the stand with:  docker compose -f deploy/compose.yaml up -d --wait
Addresses and test credentials come from the environment (see .env.example); defaults match compose.
A service that is down fails its test with the start command instead of being skipped.
Every check writes under a unique name and removes it afterwards, so reruns start clean.
"""

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import boto3
import pytest
import redis
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from mypy_boto3_s3 import S3Client
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

START_HINT = "Run: docker compose -f deploy/compose.yaml up -d --wait"
PROBE_BUCKET = "stand-probe"


@dataclass(frozen=True)
class StandSettings:
    databaseUrl: str
    redisUrl: str
    s3EndpointUrl: str
    s3AccessKeyId: str
    s3SecretAccessKey: str
    s3Region: str

    @classmethod
    def fromEnv(cls) -> "StandSettings":
        return cls(
            databaseUrl=os.environ.get(
                "DATABASE_URL", "postgresql+psycopg://gorky:gorky@127.0.0.1:15432/gorky?connect_timeout=3"
            ),
            redisUrl=os.environ.get("REDIS_URL", "redis://127.0.0.1:16379/0"),
            s3EndpointUrl=os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:18333"),
            s3AccessKeyId=os.environ.get("S3_ACCESS_KEY_ID", "gorky-dev"),
            s3SecretAccessKey=os.environ.get("S3_SECRET_ACCESS_KEY", "gorky-dev-secret"),
            s3Region=os.environ.get("S3_REGION", "us-east-1"),
        )


@pytest.fixture(scope="module")
def settings() -> StandSettings:
    return StandSettings.fromEnv()


@pytest.fixture
def redisClient(settings: StandSettings) -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(settings.redisUrl, socket_connect_timeout=3, socket_timeout=3)
    try:
        client.ping()
    except redis.ConnectionError as error:
        pytest.fail(f"Redis is not reachable at {settings.redisUrl}. {START_HINT}\n{error}", pytrace=False)
    yield client
    client.close()


@pytest.fixture
def s3Client(settings: StandSettings) -> S3Client:
    client: S3Client = boto3.client(
        "s3",
        endpoint_url=settings.s3EndpointUrl,
        aws_access_key_id=settings.s3AccessKeyId,
        aws_secret_access_key=settings.s3SecretAccessKey,
        region_name=settings.s3Region,
        # The stand is local: never route it through a system proxy (Windows VPN clients set one for 127.0.0.1 too).
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"max_attempts": 1},
            s3={"addressing_style": "path"},
            proxies={"http": "", "https": ""},
        ),
    )
    try:
        client.list_buckets()
    except (BotoCoreError, ClientError) as error:
        pytest.fail(f"S3 is not reachable at {settings.s3EndpointUrl}. {START_HINT}\n{error}", pytrace=False)
    return client


def testPostgresAnswersQuery(settings: StandSettings) -> None:
    engine = create_engine(settings.databaseUrl)
    try:
        with engine.connect() as connection:
            answer = connection.execute(text("SELECT 1")).scalar_one()
    except OperationalError as error:
        pytest.fail(f"PostgreSQL is not reachable at {settings.databaseUrl}. {START_HINT}\n{error.orig}", pytrace=False)
    finally:
        engine.dispose()
    assert answer == 1


def testRedisStreamRoundTrip(redisClient: redis.Redis) -> None:
    stream = f"stand.probe.{uuid.uuid4().hex}"
    try:
        messageId = redisClient.xadd(stream, {"probe": "ping"})
        messages = redisClient.xrange(stream)
    finally:
        redisClient.delete(stream)
    assert messages == [(messageId, {b"probe": b"ping"})]


def testS3ObjectRoundTrip(s3Client: S3Client) -> None:
    existing = {bucket.get("Name") for bucket in s3Client.list_buckets().get("Buckets", [])}
    if PROBE_BUCKET not in existing:
        s3Client.create_bucket(Bucket=PROBE_BUCKET)
    key = f"probe/{uuid.uuid4().hex}.txt"
    try:
        s3Client.put_object(Bucket=PROBE_BUCKET, Key=key, Body=b"ping")
        body = s3Client.get_object(Bucket=PROBE_BUCKET, Key=key)["Body"].read()
    finally:
        s3Client.delete_object(Bucket=PROBE_BUCKET, Key=key)
    assert body == b"ping"
