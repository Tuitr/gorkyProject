"""Persist an incoming event and resolve its job inside the caller's transaction."""

from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from grading.intake import AttemptSubmitted, IncomingEvent
from grading.tables import AssignmentJob, CheckJob, InboxEntry


@dataclass(frozen=True)
class SavedCheck:
    checkId: str
    state: str


@dataclass(frozen=True)
class SavedAssignment:
    assignmentId: str
    assignmentRev: int
    state: str


SavedJob = SavedCheck | SavedAssignment


def enqueueJob(session: Session, event: IncomingEvent) -> None:
    payload = event.payload
    values = {
        "assignmentId": payload.assignmentId,
        "assignmentRev": payload.assignmentRev,
        "traceId": event.traceId,
        "payload": payload.model_dump(mode="json"),
    }
    if isinstance(event, AttemptSubmitted):
        statement = (
            insert(CheckJob)
            .values(**values, checkId=event.payload.checkId)
            .on_conflict_do_nothing(index_elements=[CheckJob.checkId])
        )
    else:
        statement = (
            insert(AssignmentJob)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[AssignmentJob.assignmentId, AssignmentJob.assignmentRev])
        )
    session.execute(statement)


def saveEvent(session: Session, event: IncomingEvent) -> SavedJob:
    """Claim the event ID, queue new work and read the current state of the linked job."""
    checkId = event.payload.checkId if isinstance(event, AttemptSubmitted) else None
    entry = session.scalars(
        insert(InboxEntry)
        .values(
            eventId=event.eventId,
            eventType=event.type,
            traceId=event.traceId,
            occurredAt=event.occurredAt,
            checkId=checkId,
            assignmentId=event.payload.assignmentId if checkId is None else None,
            assignmentRev=event.payload.assignmentRev if checkId is None else None,
        )
        .on_conflict_do_nothing(index_elements=[InboxEntry.eventId])
        .returning(InboxEntry)
    ).one_or_none()
    if entry is None:
        # The conflicting insert has committed by the time ON CONFLICT returns.
        entry = session.get_one(InboxEntry, event.eventId)
    else:
        enqueueJob(session, event)

    if entry.checkId is not None:
        check = session.get_one(CheckJob, entry.checkId)
        return SavedCheck(check.checkId, check.state)

    assignment = session.get_one(AssignmentJob, (entry.assignmentId, entry.assignmentRev))
    return SavedAssignment(assignment.assignmentId, assignment.assignmentRev, assignment.state)
