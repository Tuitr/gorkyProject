"""Teacher grades range from 2 to 5. A null grade clears the selection."""

from pydantic import BaseModel, ConfigDict

from contracts.v1.events.checks import NonEmptyStr
from contracts.v1.grading.checkresult import SchoolGrade

Grade = SchoolGrade


class SetGrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grade: Grade | None


class CheckGrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkId: NonEmptyStr
    grade: Grade | None
