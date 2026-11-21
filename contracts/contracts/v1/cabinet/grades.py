"""PUT /v1/checks/{checkId}/grade: the teacher's grade for a checked submission, 1 to 5; null removes it."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from contracts.v1.events.checks import NonEmptyStr

Grade = Annotated[int, Field(ge=1, le=5, strict=True)]


class SetGrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grade: Grade | None


class CheckGrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkId: NonEmptyStr
    grade: Grade | None
