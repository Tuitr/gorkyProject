from cabinet.reports import combineReport
from contracts.v1.cabinet.reports import GradingReport, GradingTaskTotals


def testEmptyClassHasUndefinedShares() -> None:
    summary = GradingReport(
        assignmentId="assignment-1", assignmentRev=1, results=[],
        tasks=[GradingTaskTotals(taskId="1", correct=0, incorrect=0, skipped=0)],
    )

    report = combineReport("assignment-1", 1, [], {}, summary)

    assert report.students == report.notSubmitted == report.waiting == []
    assert report.submittedCount == report.tasks[0].unchecked == 0
    assert report.tasks[0].correctShare is report.tasks[0].incorrectShare is report.tasks[0].skippedShare is None
