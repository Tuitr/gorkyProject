"""Read a grading result only after checking ownership in the cabinet."""

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet.assignments import AssignmentRecord
from cabinet.attempts import CheckRecord
from cabinet.classes import ClassRecord
from cabinet.settings import Settings
from contracts.v1.common.errors import ApiError
from contracts.v1.grading.checks import CheckPending, CheckStatus, checkStatusAdapter
from shared.httperrors import TRACE_HEADER, ApiProblem


def requireOwnedCheck(db: Session, checkId: str, teacherId: str) -> str:
    if len(checkId) != 36 or "\x00" in checkId:
        raise ApiProblem("not_found", "Check not found")
    attemptId = db.scalar(
        select(CheckRecord.attemptId)
        .join(
            AssignmentRecord,
            (AssignmentRecord.assignmentId == CheckRecord.assignmentId)
            & (AssignmentRecord.assignmentRev == CheckRecord.assignmentRev),
        )
        .join(ClassRecord, ClassRecord.classId == AssignmentRecord.classId)
        .where(CheckRecord.checkId == checkId, ClassRecord.teacherId == teacherId)
    )
    if attemptId is None:
        raise ApiProblem("not_found", "Check not found")
    return attemptId


def readGradingCheck(
    settings: Settings, checkId: str, attemptId: str, traceId: str, transport: httpx.BaseTransport | None = None,
) -> CheckStatus:
    if not settings.gradingServiceToken:
        raise ApiProblem("unavailable", "Grading service is unavailable")
    try:
        with httpx.Client(base_url=settings.gradingBaseUrl, timeout=5, trust_env=False, transport=transport) as client:
            response = client.get(
                f"/internal/v1/checks/{checkId}",
                headers={"Authorization": f"Bearer {settings.gradingServiceToken}", TRACE_HEADER: traceId},
            )
    except httpx.TimeoutException:
        raise ApiProblem("timeout", "Grading service timed out") from None
    except (httpx.RequestError, httpx.InvalidURL):
        raise ApiProblem("unavailable", "Grading service is unavailable") from None

    try:
        if response.status_code == 404 and ApiError.model_validate_json(response.content).code == "not_found":
            # The cabinet committed the check, but the worker has not received it yet.
            return CheckPending(checkId=checkId, attemptId=attemptId, state="queued", reason="Проверка ожидает обработки.")
        if response.status_code != 200:
            raise ApiProblem("unavailable", "Grading service is unavailable")
        result = checkStatusAdapter.validate_json(response.content)
    except ValidationError:
        raise ApiProblem("grading_invalid_response", "Invalid response from grading service", status=502) from None
    if result.checkId != checkId or result.attemptId != attemptId:
        raise ApiProblem("grading_invalid_response", "Invalid response from grading service", status=502)
    return result
