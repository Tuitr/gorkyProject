from cabinet.reports import combineReport
from contracts.v1.cabinet.classes import StudentSummary
from contracts.v1.cabinet.reports import ReportTrend
from contracts.v1.grading.assignmentreport import AssignmentReport, CategoryTrend, ReportStudent, TaskDistribution


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


def testReportKeepsUnreadableAnswersAndRevisionedGradesSeparate() -> None:
    students = [
        ReportStudent(studentId="s1", attemptId="a1", checkId="c1", resultRev=2, selection="accepted", grade=3),
        ReportStudent(studentId="s2", attemptId="a2", checkId="c2", resultRev=1, selection="draft"),
        ReportStudent(studentId="s3", attemptId="a3", checkId="c3", resultRev=1, selection="draft", grade=None),
    ]
    # Omission and explicit clearing must survive storing and reading a grading snapshot.
    summary = AssignmentReport(
        assignmentId="assignment-1", assignmentRev=1, rev=1, studentCount=3, students=students,
        tasks=[TaskDistribution(
            taskId="1", answer={"incorrect": 2, "unreadable": 1}, reasoning={}, method={}, needsReview=1, missing=0,
            correctShare=0, categories=[CategoryTrend(category="sign", affectedStudents=2, primaryIssues=3,
                                                      topic="Уравнения", exampleComment="Проверьте знак.")],
        )],
    )
    restored = AssignmentReport.model_validate_json(summary.model_dump_json())
    roster = [StudentSummary(studentId=f"s{i}", name=f"Ученик {i}") for i in range(1, 5)]
    report = combineReport("assignment-1", 1, roster, {f"c{i}": f"s{i}" for i in range(1, 5)},
                           restored, {"c1": 4, "c2": 5, "c3": 4})

    assert [item.grade for item in report.results] == [3, 5, None]
    assert report.waiting == [roster[3]]
    (task,) = report.tasks
    assert task.unreadable == 1 and task.unchecked == 2 and task.incorrectShare == 0.5
    assert task.issuesByCategory == {"sign": 2}
    assert report.trends == [ReportTrend(taskId="1", category="sign", topic="Уравнения", exampleComment="Проверьте знак.")]
