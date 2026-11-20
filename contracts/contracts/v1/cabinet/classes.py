"""Public models for a teacher's classes and class rosters."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class CreateClass(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^\x00]+$")]


class CreateStudent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00]+$")]


class ClassSummary(CreateClass):
    classId: str


class StudentSummary(CreateStudent):
    studentId: str
