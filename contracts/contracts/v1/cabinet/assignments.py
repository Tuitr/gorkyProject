"""Public response from an assignment upload."""

from pydantic import BaseModel, ConfigDict

from contracts.v1.events.checks import NonEmptyStr, Revision


class AssignmentVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
