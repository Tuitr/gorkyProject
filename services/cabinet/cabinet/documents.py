"""Validate uploaded documents and store immutable originals in S3."""

import base64
import hashlib
from contextlib import closing
from io import BytesIO
from typing import TYPE_CHECKING
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from zlib import error as ZlibError

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import UploadFile

from cabinet.settings import Settings
from contracts.v1.events.checks import DocRef
from shared.buckets import SOURCES_BUCKET
from shared.httperrors import ApiProblem
from shared.submissionmedia import MAX_SOURCE_BYTES, imageMediaType

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


MAX_DOCX_BYTES = MAX_SOURCE_BYTES
MAX_XML_BYTES = 8 * 1024 * 1024
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAIN_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
TYPES_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
DOCUMENT_TAGS = {
    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
    "{http://purl.oclc.org/ooxml/wordprocessingml/main}document",
}


def readDocxBytes(upload: UploadFile) -> bytes:
    original = upload.file.read(MAX_DOCX_BYTES + 1)
    if len(original) > MAX_DOCX_BYTES:
        raise ApiProblem("file_too_large", "DOCX exceeds 20 MiB", status=413)
    return original


def readDocx(upload: UploadFile) -> bytes:
    original = readDocxBytes(upload)
    validateDocx(original)
    return original


def readPhotoBytes(upload: UploadFile, mediaType: str) -> bytes:
    original = upload.file.read(MAX_SOURCE_BYTES + 1)
    if len(original) > MAX_SOURCE_BYTES:
        raise ApiProblem("file_too_large", "Фото превышает 20 МБ", status=413)
    if imageMediaType(original) != mediaType:
        raise ApiProblem("invalid_input", "Ожидалось фото в формате JPEG, PNG или WebP")
    return original


def validateDocx(original: bytes) -> ElementTree.Element:
    try:
        with ZipFile(BytesIO(original)) as archive:
            types = readXml(archive, "[Content_Types].xml")
            document = readXml(archive, "word/document.xml")
            mainType = any(
                item.get("PartName") == "/word/document.xml" and item.get("ContentType") == MAIN_CONTENT_TYPE
                for item in types.findall(f"{TYPES_NS}Override")
            )
            if mainType and document.tag in DOCUMENT_TAGS:
                return document
    except (BadZipFile, ElementTree.ParseError, KeyError, RuntimeError, OSError, ValueError, EOFError, ZlibError):
        pass
    raise ApiProblem("invalid_input", "Expected a valid DOCX document")


def readXml(archive: ZipFile, name: str) -> ElementTree.Element:
    with archive.open(name) as member:
        data = member.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError("DOCX XML part is too large")
    return ElementTree.fromstring(data)


def buildS3Client(settings: Settings) -> "S3Client":
    return boto3.client(
        "s3",
        endpoint_url=settings.s3EndpointUrl,
        aws_access_key_id=settings.s3AccessKeyId,
        aws_secret_access_key=settings.s3SecretAccessKey,
        region_name=settings.s3Region,
        config=Config(
            connect_timeout=3,
            read_timeout=10,
            retries={"total_max_attempts": 2},
            s3={"addressing_style": "path"},
            proxies={},
        ),
    )


def storeDocx(client: "S3Client", key: str, original: bytes) -> DocRef:
    return storeOriginal(client, key, original, DOCX_MEDIA_TYPE)


def storeOriginal(client: "S3Client", key: str, original: bytes, mediaType: str) -> DocRef:
    digest = hashlib.sha256(original).hexdigest()
    checksum = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
    try:
        client.put_object(
            Bucket=SOURCES_BUCKET,
            Key=key,
            Body=original,
            ContentType=mediaType,
            IfNoneMatch="*",
            ChecksumSHA256=checksum,
        )
    except (ClientError, BotoCoreError) as error:
        raise ApiProblem("unavailable", "Document storage is unavailable") from error
    return DocRef(bucket=SOURCES_BUCKET, key=key, sha256=digest)


def loadOriginal(client: "S3Client", ref: DocRef) -> bytes:
    try:
        with closing(client.get_object(Bucket=ref.bucket, Key=ref.key)["Body"]) as body:
            original = body.read(MAX_SOURCE_BYTES + 1)
    except (ClientError, BotoCoreError) as error:
        raise ApiProblem("unavailable", "Document storage is unavailable") from error
    if len(original) > MAX_SOURCE_BYTES or hashlib.sha256(original).hexdigest() != ref.sha256:
        raise ApiProblem("unavailable", "Stored document does not match its reference")
    return original
