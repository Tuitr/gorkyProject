"""Public responses for submission uploads and their files."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts.v1.cabinet.classes import StudentSummary
from contracts.v1.events.checks import NonEmptyStr


UploadFileStatus = Literal["pending_binding", "needs_confirmation", "bound", "rejected"]


class UploadAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uploadId: NonEmptyStr


class UploadFileProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fileId: NonEmptyStr
    originalName: NonEmptyStr
    status: UploadFileStatus
    candidates: list[StudentSummary] = Field(default_factory=list)
    reason: NonEmptyStr | None = None
    studentId: NonEmptyStr | None = None
    attemptId: NonEmptyStr | None = None
    checkId: NonEmptyStr | None = None


class UploadProgress(UploadAccepted):
    files: list[UploadFileProgress]
