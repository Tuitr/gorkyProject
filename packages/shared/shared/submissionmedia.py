"""Supported formats for original student work stored in the sources bucket."""

from pathlib import PurePosixPath

PHOTO_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
MAX_SOURCE_BYTES = 20 * 1024 * 1024


def photoMediaType(name: str) -> str | None:
    return PHOTO_TYPES.get(PurePosixPath(name).suffix.lower())


def imageMediaType(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None
