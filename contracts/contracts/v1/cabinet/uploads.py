"""Public response from a submission upload."""

from pydantic import BaseModel, ConfigDict

from contracts.v1.events.checks import NonEmptyStr


class UploadAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uploadId: NonEmptyStr
