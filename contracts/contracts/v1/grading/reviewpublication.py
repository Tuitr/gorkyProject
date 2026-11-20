"""Teacher publication pins an explicit result revision for download."""

from pydantic import BaseModel, ConfigDict

from contracts.v1.events.checks import NonEmptyStr, Revision
from contracts.v1.grading.checkresult import MarkdownRef


class PublishReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resultRev: Revision
    actorId: NonEmptyStr | None = None


class PublishReviewReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkId: NonEmptyStr
    resultRev: Revision
    markdownRef: MarkdownRef
