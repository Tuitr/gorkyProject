"""Apply an edit and save its receipt in the same database transaction."""

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from contracts.v1.grading.checkresult import CheckOutcome
from contracts.v1.grading.reviewedit import (
    RemoveIssue, ReviewEditReceipt, ReviewEditRequest, SetGrade, SetOverallReview, SetTaskScore, SetTeacherComment, SetVerdict,
)
from grading.feedback import composeOverallReview
from grading.resultArtifacts import loadResultSource
from grading.results import latestRevision
from grading.review import anchoredIssues
from grading.scoring import storedOutcome, withScores
from grading.tables import CheckJob, CheckResultRevision, TeacherReviewEdit


class ReviewConflict(ValueError):
    def __init__(self, message: str, resultRev: int | None) -> None:
        super().__init__(message)
        self.resultRev = resultRev


class InvalidReviewChange(ValueError):
    """The edit targets a task or issue absent from the current result."""


class ReviewEditor:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def apply(self, checkId: str, edit: ReviewEditRequest) -> ReviewEditReceipt | None:
        request = edit.model_dump(mode="json", exclude_unset=True)
        with Session(self.engine) as session, session.begin():
            # Shared lock with result allocation and publication, including across processes.
            check = session.get(CheckJob, checkId, with_for_update=True)
            if check is None:
                return None
            previous = session.get(TeacherReviewEdit, (checkId, edit.editId))
            if previous is not None and previous.request == request:
                return ReviewEditReceipt(checkId=checkId, editId=edit.editId, resultRev=previous.resultRev)

            latest = latestRevision(session, checkId)
            currentRev = latest.resultRev if latest is not None else None
            if previous is not None:
                raise ReviewConflict("editId already identifies a different request", currentRev)
            if latest is None or currentRev != edit.baseRev:
                raise ReviewConflict("baseRev does not match the current result revision", currentRev)
            if latest.markdownRef is None:
                raise ReviewConflict("the current result revision is still awaiting Markdown", currentRev)

            prior = session.scalars(
                select(TeacherReviewEdit.request).filter_by(checkId=checkId)
            )
            changes = [change for requestData in prior for change in ReviewEditRequest.model_validate(requestData).changes]
            base = storedOutcome(latest.outcome)
            overridden = {change.taskId for change in changes if isinstance(change, SetTaskScore)}
            outcome = applyChanges(base, edit, overridden)
            changes.extend(edit.changes)
            updatesFeedback = any(not isinstance(change, (SetGrade, SetTaskScore, SetTeacherComment))
                                  for change in edit.changes)
            if updatesFeedback and not any(isinstance(change, SetOverallReview) for change in changes):
                source = loadResultSource(session, check, outcome)
                feedback = composeOverallReview(
                    source.tasks, source.reading, tuple(outcome.taskResults), source.references,
                    teacherVerdicts={change.taskId for change in changes if isinstance(change, SetVerdict)},
                )
                outcome = outcome.model_copy(update={"overallReview": feedback})
            session.add(CheckResultRevision(
                checkId=checkId, resultRev=outcome.resultRev, calculationId=f"teacher:{edit.editId}",
                outcome=outcome.model_dump(mode="json"),
            ))
            session.flush()
            session.add(TeacherReviewEdit(checkId=checkId, editId=edit.editId, resultRev=outcome.resultRev, request=request))
            return ReviewEditReceipt(checkId=checkId, editId=edit.editId, resultRev=outcome.resultRev)


def applyChanges(base: CheckOutcome, edit: ReviewEditRequest, overridden: set[str]) -> CheckOutcome:
    maxima = {a.taskId: a.maxScore for a in base.taskAssessments}
    overrides = {a.taskId: a.score for a in base.taskAssessments if a.taskId in overridden}
    grade = base.grade
    teacherComment = base.teacherComment
    results = {result.taskId: result for result in base.taskResults}
    overall = base.overallReview
    for change in edit.changes:
        if isinstance(change, SetGrade):
            grade = change.grade
            continue
        if isinstance(change, SetTeacherComment):
            teacherComment = change.teacherComment
            continue
        if isinstance(change, SetOverallReview):
            overall = change.overallReview
            continue
        task = results.get(change.taskId)
        if task is None:
            raise InvalidReviewChange("the targeted task is absent from the result")
        if isinstance(change, SetTaskScore):
            if change.score is not None and change.score > maxima[task.taskId]:
                raise InvalidReviewChange("score exceeds the maximum for this task")
            overrides[task.taskId] = change.score
            continue
        if isinstance(change, SetVerdict):
            fields = change.model_dump(exclude={"op", "taskId"}, exclude_none=True)
            results[change.taskId] = task.model_copy(update=fields)
            continue

        if not any(issue.issueId == change.issueId for issue in task.issues):
            raise InvalidReviewChange("the targeted issue is absent from the task")
        if isinstance(change, RemoveIssue):
            remaining = [issue for issue in task.issues if issue.issueId != change.issueId]
            issues = anchoredIssues(remaining, {issue.stepId for issue in remaining})
        else:
            fields = change.model_dump(exclude={"op", "taskId", "issueId"}, exclude_none=True)
            issues = [
                issue.model_copy(update=fields) if issue.issueId == change.issueId else issue
                for issue in task.issues
            ]
        results[change.taskId] = task.model_copy(update={"issues": issues})
    updated = base.model_copy(update={"resultRev": base.resultRev + 1, "taskResults": list(results.values()),
                                      "overallReview": overall, "grade": grade, "teacherComment": teacherComment})
    return withScores(updated, maxima, overrides)
