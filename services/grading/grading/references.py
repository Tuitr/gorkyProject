"""Client of the internal v1 reference registry."""

import os
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import quote, urlsplit

from contracts.v1.references.reference import ReferenceRequest, ReferenceResult, referenceResultAdapter


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
            if response.status not in (200, 202):
                raise RuntimeError(f"reference registry returned HTTP {response.status}")
            result = referenceResultAdapter.validate_json(response.read())
            expectedStatus = 202 if method == "POST" and result.state == "pending" else 200
            if response.status != expectedStatus:
                raise ValueError("reference state differs from the HTTP response status")
            return result
        finally:
            connection.close()
