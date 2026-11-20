"""Show the teacher the reference answers grading prepared for an assignment revision."""

from pydantic import TypeAdapter
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from contracts.v1.grading.assignmentreferences import AssignmentReferences, PreparationState, TaskReference
from contracts.v1.references.reference import ReferenceReady, ReferenceResult, answerText
from grading.assignment import AssignmentTask
from grading.tables import AssignmentJob

tasksAdapter: TypeAdapter[list[AssignmentTask]] = TypeAdapter(list[AssignmentTask])
referencesAdapter: TypeAdapter[dict[str, ReferenceResult]] = TypeAdapter(dict[str, ReferenceResult])
stateAdapter: TypeAdapter[PreparationState] = TypeAdapter(PreparationState)


def taskReference(task: AssignmentTask, reference: ReferenceResult | None) -> TaskReference:
    shown = TaskReference(taskId=task.taskId, taskNumber=task.taskNumber, statement=task.statement, state="missing")
    if reference is None:
        return shown
    if isinstance(reference, ReferenceReady):
        return shown.model_copy(update={
            "state": "ready", "schemaId": reference.schemaId, "value": reference.value,
            "answer": answerText(reference.schemaId, reference.value),
        })
    return shown.model_copy(update={"state": reference.state, "reasonCode": getattr(reference, "reasonCode", None)})


class ReferenceReader:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def read(self, assignmentId: str, assignmentRev: int | None = None) -> AssignmentReferences | None:
        query = select(AssignmentJob).filter_by(assignmentId=assignmentId)
        if assignmentRev is not None:
            query = query.filter_by(assignmentRev=assignmentRev)
        with Session(self.engine) as session:
            job = session.scalar(query.order_by(AssignmentJob.assignmentRev.desc()).limit(1))
            if job is None:
                return None
            data = job.stageData or {}
            # A failed job keeps only the error in stageData: no tasks to show then.
            tasks = tasksAdapter.validate_python(data["tasks"]) if "tasks" in data else []
            references = referencesAdapter.validate_python(data.get("references", {}))
            return AssignmentReferences(
                assignmentId=job.assignmentId, assignmentRev=job.assignmentRev, state=stateAdapter.validate_python(job.state),
                tasks=[taskReference(task, references.get(task.taskId)) for task in tasks],
            )
