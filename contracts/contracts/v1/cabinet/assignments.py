"""Public responses for assignment uploads and class assignment lists."""

from datetime import date

from pydantic import BaseModel, ConfigDict

from contracts.v1.events.checks import NonEmptyStr, Revision, Subject


class AssignmentVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision


class AssignmentSummary(AssignmentVersion):
    assignmentDate: date
    subject: Subject = "math"
    title: str | None = None
    """Teacher-given title of the latest revision, for example «Тепловые явления»; None when not given."""
