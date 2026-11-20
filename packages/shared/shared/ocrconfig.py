"""Validate the destination used for photo transcription."""

from urllib.parse import SplitResult, urlsplit


def parseOcrUrl(value: str) -> SplitResult:
    url = value.strip()
    endpoint = urlsplit(url)
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.port == 0 or not endpoint.path
            or endpoint.username is not None or endpoint.query or endpoint.fragment or any(c.isspace() for c in url)):
        raise ValueError("Expected a full HTTPS address for OCR")
    return endpoint
