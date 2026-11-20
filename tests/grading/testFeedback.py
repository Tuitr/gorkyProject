"""Examples of complete, erroneous and partially checked submissions."""

from contracts.v1.grading.taskresult import Issue, TaskResult
from contracts.v1.references.reference import ReferenceReady
from grading.assignment import AssignmentTask
from grading.feedback import composeOverallReview
from grading.intake import IncomingDocRef
from grading.submission import SolutionStep, SourceAnchor, SubmissionReading, TaskReading, UnassignedFragment


REFERENCE = ReferenceReady(referenceId="r1", state="ready", source="fixture", schemaId="scalar.v1",
                           value={"exact": "5"}, solverVersion=None)
SOURCE = SourceAnchor(IncomingDocRef(bucket="sources", key="works/a1.docx", sha256="a" * 64), (), 0, 0, 1)
TASK = AssignmentTask("t1", "1", "Найдите длину вектора (3; 4).", ())


def testCorrectWorkRecognizesTheVerifiedReasoning() -> None:
    reading = SubmissionReading((SolutionStep("t1", "s1", "√(3² + 4²) = 5", SOURCE, "read"),),
                               (TaskReading("t1", "hasSteps"),), ())
    result = TaskResult(taskId="t1", answer="correct", reasoning="valid", method="optional", issues=[], needsReview=False)
    assert composeOverallReview((TASK,), reading, (result,), {"t1": REFERENCE}) == (
        "Задание 1. Ответ верный. Ход решения верен. Проверенные шаги — s1."
    )


def testIncorrectWorkExplainsRootCorrectionsAndTheirConsequences() -> None:
    reading = SubmissionReading((
        SolutionStep("t1", "s1", "3² + 4² = 24", SOURCE, "read"),
        SolutionStep("t1", "s2", "√24 = 24", SOURCE, "read"),
        SolutionStep("t1", "s3", "Длина равна 24", SOURCE, "read"),
    ), (TaskReading("t1", "hasSteps"),), ())
    issues = [
        Issue(issueId="i1", stepId="s1", category="arithmetic", comment="Проверьте сумму квадратов.", correction="3² + 4² = 25"),
        Issue(issueId="i2", stepId="s2", category="formula", comment="Извлеките квадратный корень.", correction="√25 = 5"),
        Issue(issueId="i3", stepId="s3", category="answer", comment="Ошибка итога.", correction="Ответ: 5", causedByIssueId="i2"),
        Issue(issueId="i4", stepId="s99", category="formula", comment="Чужой шаг", correction="Чужая формула"),
    ]
    result = TaskResult(taskId="t1", answer="incorrect", reasoning="invalid", method="optional", issues=issues, needsReview=False)
    feedback = composeOverallReview((TASK,), reading, (result,), {"t1": REFERENCE})
    assert "Предложение по исправлению шага s1 — 3² + 4² = 25" in feedback
    assert "Предложение по исправлению шага s2 — √25 = 5" in feedback
    assert "Шаг s3. Следствие замечания к шагу s2." in feedback
    assert "Предложение по исправлению шага s3" not in feedback
    assert "итог с эталоном (5)" in feedback
    assert "s99" not in feedback and "Чужой" not in feedback


def testPartialWorkNamesUncheckedTasksWithoutAttributingErrors() -> None:
    tasks = (TASK, AssignmentTask("t2", "2", "Найдите сумму.", ()), AssignmentTask("t3", "3", "Найдите разность.", ()))
    reading = SubmissionReading((
        SolutionStep("t1", "s1", "Ответ: 5", SOURCE, "read"),
        SolutionStep("t2", "s2", "Неподдерживаемая формула", SOURCE, "ambiguous"),
    ), (TaskReading("t1", "answerOnly"), TaskReading("t2", "needsReview", "Формулу не удалось прочитать."),
        TaskReading("t3", "hasSteps")), (UnassignedFragment("Неизвестное задание.", SOURCE),))
    results = (
        TaskResult(taskId="t1", answer="correct", reasoning="not_shown", method="optional", issues=[], needsReview=False),
        TaskResult(taskId="t2", answer="unreadable", reasoning="unknown", method="optional", issues=[], needsReview=True),
        TaskResult(taskId="t3", answer="unreadable", reasoning="unknown", method="optional", issues=[], needsReview=True),
    )
    feedback = composeOverallReview(tasks, reading, results, {"t1": REFERENCE, "t2": REFERENCE})
    assert feedback == (
        "Задание 1. Ответ верный. Записан только ответ. Покажите, как вы его получили.\n\n"
        "Задание 2. Запись требует ручной проверки. Формулу не удалось прочитать.\n\n"
        "Задание 3. Эталон недоступен. Задание требует ручной проверки.\n\n"
        "Часть записи не удалось отнести к заданию. Эти фрагменты требуют ручной проверки."
    )
