from cabinet.reports import combineReport
from contracts.v1.grading.assignmentreport import AssignmentReport, TaskDistribution


def testEmptyClassHasUndefinedShares() -> None:
    summary = AssignmentReport(
        assignmentId="assignment-1", assignmentRev=1, rev=1, studentCount=0, students=[],
        tasks=[TaskDistribution(taskId="1", answer={}, reasoning={}, method={}, needsReview=0, missing=0,
                                correctShare=None, categories=[])],
    )

    report = combineReport("assignment-1", 1, [], {}, summary)

    assert report.students == report.notSubmitted == report.waiting == []
    assert report.submittedCount == report.tasks[0].unchecked == 0
    assert report.tasks[0].correctShare is report.tasks[0].incorrectShare is report.tasks[0].skippedShare is None
