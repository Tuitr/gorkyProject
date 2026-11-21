"""Public responses for submission uploads and their files."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts.v1.cabinet.classes import StudentSummary
from contracts.v1.events.checks import NonEmptyStr, Revision


UploadFileStatus = Literal["pending_binding", "needs_confirmation", "matched", "bound", "rejected"]


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
    isPhoto: Literal[True] | None = None
    groupId: NonEmptyStr | None = None
    pageIndex: Annotated[int, Field(ge=0, strict=True)] | None = None
    reasonCode: Literal["invalid_page_order"] | None = None


class UploadProgress(UploadAccepted):
    files: list[UploadFileProgress]


class AssignmentUploads(BaseModel):
    """Imports that still contain files awaiting a binding for this assignment revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    uploads: list[UploadAccepted]
