"""PUT /v1/checks/{checkId}/grade: the teacher's grade for a checked submission, 1 to 5; null removes it."""

from pydantic import BaseModel, ConfigDict

from contracts.v1.events.checks import NonEmptyStr
from contracts.v1.grading.assessment import Grade as Grade


class SetGrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grade: Grade | None


class CheckGrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkId: NonEmptyStr
    grade: Grade | None
