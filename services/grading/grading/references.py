"""Client of the internal v1 reference registry."""

import os
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from urllib.parse import quote, urlsplit

from contracts.v1.references.reference import ReferenceRequest, ReferenceResult, referenceResultAdapter


class ReferenceUnavailableError(RuntimeError):
    """A reference request failed temporarily and may be retried later."""

    def __init__(self, message: str, *, retryAfterS: int = 5) -> None:
        super().__init__(message)
        self.retryAfterS = max(1, retryAfterS)


class ReferenceTerminalError(RuntimeError):
    """A permanent registry response that requires teacher review."""

    def __init__(self, message: str, *, reasonCode: str) -> None:
        super().__init__(message)
        self.reasonCode = reasonCode


@dataclass(frozen=True)
class ReferenceClient:
    baseUrl: str
    token: str = field(repr=False)

    @classmethod
    def fromEnv(cls) -> "ReferenceClient":
        return cls(os.environ["GRADING_REFERENCE_URL"], os.environ["GRADING_REFERENCE_TOKEN"])

    def request(self, task: ReferenceRequest) -> ReferenceResult:
        return self._exchange("POST", "/internal/v1/references", task.model_dump_json(exclude_none=True).encode("utf-8"))

    def get(self, referenceId: str) -> ReferenceResult:
        return self._exchange("GET", "/internal/v1/references/" + quote(referenceId, safe=""))

    def _exchange(self, method: str, path: str, body: bytes | None = None) -> ReferenceResult:
        url = urlsplit(self.baseUrl)
        if url.scheme not in ("http", "https") or not url.hostname or url.query or url.fragment or url.username is not None:
            raise ValueError("expected an HTTP(S) base URL for the reference registry")
        connect = HTTPSConnection if url.scheme == "https" else HTTPConnection
        connection = connect(url.hostname, url.port, timeout=5)
        try:
            connection.request(
                method, url.path.rstrip("/") + path, body=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            )
            response = connection.getresponse()
            if response.status in (408, 425, 429, 500, 502, 503, 504):
                retryAfterS = 5
                retryHeader = response.getheader("Retry-After")
                if retryHeader is not None and retryHeader.isdigit():
                    retryAfterS = max(1, int(retryHeader))
                raise ReferenceUnavailableError(
                    f"reference registry temporarily returned HTTP {response.status}", retryAfterS=retryAfterS,
                )
            if response.status not in (200, 202):
                raise ReferenceTerminalError(
                    f"reference registry returned HTTP {response.status}",
                    reasonCode="reference_not_found" if response.status == 404 else "reference_registry_rejected",
                )
            payload = response.read()
            result = referenceResultAdapter.validate_json(payload)
            expectedStatus = 202 if method == "POST" and result.state == "pending" else 200
            if response.status != expectedStatus:
                raise ValueError("reference state differs from the HTTP response status")
            return result
        except (OSError, HTTPException) as error:
            raise ReferenceUnavailableError("reference registry exchange interrupted") from error
        finally:
            connection.close()
