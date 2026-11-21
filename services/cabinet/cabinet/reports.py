"""Class rosters and submissions stay in the cabinet. Grading selects the results included in its summary."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cabinet.attempts import AttemptRecord, CheckRecord
from cabinet.classes import StudentRecord
from cabinet.gradingclient import invalidGradingResponse
from contracts.v1.cabinet.classes import StudentSummary
from contracts.v1.cabinet.reports import AssignmentReport, ReportResult, ReportTask, ReportTrend
from contracts.v1.grading.assignmentreport import AssignmentReport as GradingReport


def readReportRoster(
    db: Session, classId: str, assignmentId: str, assignmentRev: int,
) -> tuple[list[StudentSummary], dict[str, str]]:
    submittedChecks = (
        select(CheckRecord.checkId.label("checkId"), AttemptRecord.studentId.label("studentId"))
        .join(AttemptRecord, AttemptRecord.attemptId == CheckRecord.attemptId)
        .where(
            CheckRecord.assignmentId == assignmentId, CheckRecord.assignmentRev == assignmentRev,
            AttemptRecord.deletedAt.is_(None),
        )
        .subquery()
    )
    roster: dict[str, StudentSummary] = {}
    checks: dict[str, str] = {}
    for student, checkId in db.execute(
        select(StudentRecord, submittedChecks.c.checkId)
        .outerjoin(submittedChecks, submittedChecks.c.studentId == StudentRecord.studentId)
        .where(StudentRecord.classId == classId, or_(StudentRecord.deletedAt.is_(None), submittedChecks.c.checkId.is_not(None)))
        .order_by(StudentRecord.name, StudentRecord.studentId)
    ):
        roster[student.studentId] = StudentSummary(studentId=student.studentId, name=student.name)
        if checkId is not None:
            checks[checkId] = student.studentId
    return list(roster.values()), checks


def readReportGrades(db: Session, checkIds: list[str]) -> dict[str, int]:
    """Teacher grades of the given checks; checks without a grade are left out."""
    if not checkIds:
        return {}
    return {
        checkId: grade
        for checkId, grade in db.execute(
            select(CheckRecord.checkId, CheckRecord.grade).where(
                CheckRecord.checkId.in_(checkIds),
            )
        )
        if grade is not None and 2 <= grade <= 5
    }


def deletedReportCheckIds(db: Session, assignmentId: str, assignmentRev: int) -> list[str]:
    return list(db.scalars(
        select(CheckRecord.checkId)
        .join(AttemptRecord, AttemptRecord.attemptId == CheckRecord.attemptId)
        .where(
            CheckRecord.assignmentId == assignmentId, CheckRecord.assignmentRev == assignmentRev,
            AttemptRecord.deletedAt.is_not(None),
        )
        .order_by(CheckRecord.checkId)
    ))


def combineReport(
    assignmentId: str, assignmentRev: int, roster: list[StudentSummary], checks: dict[str, str], upstream: GradingReport,
    grades: dict[str, int] | None = None,
) -> AssignmentReport:
    """grading counts its selected result per student; the cabinet adds the roster and the public shares."""
    if (upstream.assignmentId, upstream.assignmentRev) != (assignmentId, assignmentRev):
        raise invalidGradingResponse()
    results = [
        ReportResult(
            studentId=student.studentId, checkId=student.checkId, resultRev=student.resultRev,
            grade=student.grade if "grade" in student.model_fields_set else (grades or {}).get(student.checkId),
        )
        for student in upstream.students
    ]
    reviewed = {result.studentId for result in results}
    if len(reviewed) != len(results) or any(checks.get(result.checkId) != result.studentId for result in results):
        raise invalidGradingResponse()
    if len({task.taskId for task in upstream.tasks}) != len(upstream.tasks):
        raise invalidGradingResponse()

    submitted = set(checks.values())
    total = len(submitted)
    waiting = submitted - reviewed
    tasks = []
    trends: dict[tuple[str, str], ReportTrend] = {
        (trend.taskId, trend.category): ReportTrend.model_validate(trend.model_dump())
        for trend in upstream.trends
    }
    if len(trends) != len(upstream.trends):
        raise invalidGradingResponse()
    repeatedCategories = {
        (task.taskId, trend.category)
        for task in upstream.tasks for trend in task.categories if trend.affectedStudents >= 2
    }
    if not trends.keys() <= repeatedCategories:
        raise invalidGradingResponse()
    for task in upstream.tasks:
        correct, incorrect, skipped = (task.answer.get(verdict, 0) for verdict in ("correct", "incorrect", "skipped"))
        unreadable = task.answer.get("unreadable", 0)
        counted = correct + incorrect + skipped
        affected = {trend.category: trend.affectedStudents for trend in task.categories}
        if counted + unreadable > len(reviewed) or any(count > len(reviewed) for count in affected.values()):
            raise invalidGradingResponse()
        tasks.append(ReportTask(
            taskId=task.taskId, correct=correct, incorrect=incorrect, skipped=skipped,
            issuesByCategory=affected, unchecked=total - counted, unreadable=unreadable,
            correctShare=correct / total if total else None,
            incorrectShare=incorrect / total if total else None,
            skippedShare=skipped / total if total else None,
        ))
        for trend in task.categories:
            if trend.affectedStudents >= 2 and trend.topic is not None and trend.exampleComment is not None:
                trends.setdefault((task.taskId, trend.category), ReportTrend(
                    taskId=task.taskId, category=trend.category, topic=trend.topic, exampleComment=trend.exampleComment,
                ))
    return AssignmentReport(
        assignmentId=assignmentId, assignmentRev=assignmentRev, students=roster, submittedCount=total,
        notSubmitted=[student for student in roster if student.studentId not in submitted],
        waiting=[student for student in roster if student.studentId in waiting],
        results=results, tasks=tasks, trends=list(trends.values()),
    )
