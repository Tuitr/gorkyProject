"""Rebuild a result artifact from the persisted reading of the original submission."""

from dataclasses import dataclass

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from contracts.v1.grading.checkresult import CheckOutcome, PublishedCheck
from contracts.v1.references.reference import ReferenceResult
from grading.assignment import AssignmentTask
from grading.markdown import renderCheckMarkdown
from grading.markdownStorage import MarkdownStorage
from grading.results import ResultStore
from grading.submission import SubmissionReading, TaskReading
from grading.tables import AssignmentJob, CheckJob, CheckResultRevision

parsedTasksAdapter: TypeAdapter[tuple[AssignmentTask, ...]] = TypeAdapter(tuple[AssignmentTask, ...])
referencesAdapter: TypeAdapter[dict[str, ReferenceResult]] = TypeAdapter(dict[str, ReferenceResult])
submissionReadingAdapter = TypeAdapter(SubmissionReading)


@dataclass(frozen=True)
class ResultSource:
    reading: SubmissionReading
    tasks: tuple[AssignmentTask, ...]
    references: dict[str, ReferenceResult]


def loadResultSource(session: Session, check: CheckJob, outcome: CheckOutcome) -> ResultSource:
    checkpoint = check.stageData or {}
    if "steps" in checkpoint and "taskReadings" in checkpoint:
        reading = submissionReadingAdapter.validate_python(checkpoint)
    else:
        reading = SubmissionReading((), tuple(
            TaskReading(task.taskId, "needsReview", "Исходные шаги недоступны.") for task in outcome.taskResults
        ), ())
    assignment = session.get(AssignmentJob, (check.assignmentId, check.assignmentRev))
    prepared = (assignment.stageData or {}) if assignment is not None else {}
    tasks = parsedTasksAdapter.validate_python(prepared.get("tasks", []))
    byId = {task.taskId: task for task in tasks}
    resultTasks = tuple(
        byId.get(result.taskId, AssignmentTask(result.taskId, result.taskId, "", ())) for result in outcome.taskResults
    )
    references = referencesAdapter.validate_python(checkpoint.get("references", prepared.get("references", {})))
    return ResultSource(reading, resultTasks, references)


def publishResultArtifact(store: ResultStore, checkId: str, resultRev: int, storage: MarkdownStorage) -> PublishedCheck:
    existing = store.read(checkId, resultRev, storage.require)
    if isinstance(existing, PublishedCheck):
        return existing
    if existing is None:
        raise ValueError("no result revision to publish")
    with Session(store.engine) as session:
        row = session.get_one(CheckResultRevision, (checkId, resultRev))
        check = session.get_one(CheckJob, checkId)
        outcome = CheckOutcome.model_validate(row.outcome)
        source = loadResultSource(session, check, outcome)
    ref = storage.save(
        checkId, resultRev, renderCheckMarkdown(outcome, source.reading, source.tasks),
        previousMarkdown=lambda: renderCheckMarkdown(outcome, source.reading, source.tasks, preserveReviewLineBreaks=False),
    )
    return store.publish(checkId, resultRev, ref, storage.require)
