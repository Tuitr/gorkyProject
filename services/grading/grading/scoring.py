"""Demo rubric applied to one immutable result revision."""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from contracts.v1.grading.assessment import AssessmentTotal, Recommendation, TaskAssessment
from contracts.v1.grading.checkresult import CheckOutcome
from contracts.v1.grading.taskresult import TaskResult

TOPICS = {
    "arithmetic": "Вычисления", "sign": "Знаки в выражениях", "sign_error": "Знаки в выражениях",
    "formula": "Выбор формулы", "substitution": "Подстановка значений", "missing_stage": "Этапы решения",
    "notation": "Математическая запись", "logic": "Обоснование решения", "units": "Единицы измерения",
}


def automaticAssessment(task: TaskResult, maximum: float, *, omitted: bool = False) -> TaskAssessment:
    score: float | None
    if task.needsReview or task.answer == "unreadable":
        score, comment = None, "Требуется ручная проверка."
    elif task.answer == "skipped" or (task.answer == "incorrect" and omitted):
        score, comment = 0, "Задание пропущено."
    elif task.answer == "incorrect" or task.reasoning == "invalid" or task.method == "unmet":
        score = 0
        comment = ("Ответ неверен." if task.answer == "incorrect" else
                   "В ходе решения есть ошибка." if task.reasoning == "invalid" else "Требуемый метод не применён.")
    elif task.reasoning != "valid" or task.method == "unconfirmed" or task.issues:
        score, comment = None, "Обоснование решения требует проверки."
    else:
        score, comment = maximum, "Задание выполнено верно."
    primary = next((issue for issue in task.issues if issue.causedByIssueId is None), None)
    return TaskAssessment(taskId=task.taskId, score=score, maxScore=maximum, comment=primary.comment if primary else comment)


def points(values: Sequence[float]) -> float:
    return float(sum(Decimal(str(value)) for value in values))


def withScores(outcome: CheckOutcome, maxima: Mapping[str, float], overrides: Mapping[str, float | None],
               *, omittedTasks: frozenset[str] = frozenset()) -> CheckOutcome:
    assessments = []
    suggestions: dict[tuple[str, str], Recommendation] = {}
    for task in outcome.taskResults:
        assessment = automaticAssessment(task, maxima.get(task.taskId, 1), omitted=task.taskId in omittedTasks)
        if task.taskId in overrides:
            assessment = assessment.model_copy(update={"score": overrides[task.taskId]})
        assessments.append(assessment)
        if not task.needsReview and task.answer not in ("skipped", "unreadable"):
            if task.reasoning == "not_shown":
                advice = Recommendation(topic="Обоснование решения", text="Запишите промежуточные шаги, ведущие к ответу.")
                suggestions[advice.topic, advice.text] = advice
            if task.method in ("unmet", "unconfirmed"):
                advice = Recommendation(topic="Требуемый способ", text="Покажите, как применить указанный в задании способ.")
                suggestions[advice.topic, advice.text] = advice
        for issue in task.issues:
            if issue.causedByIssueId is None:
                topic = TOPICS.get(issue.category, "Задание " + task.taskId.removeprefix("t"))
                suggestions[topic, issue.correction] = Recommendation(topic=topic, text=issue.correction)
    total = AssessmentTotal(score=points([a.score for a in assessments if a.score is not None]),
                       maxScore=points([a.maxScore for a in assessments]),
                       pendingCount=sum(a.score is None for a in assessments))
    return outcome.model_copy(update={"taskAssessments": assessments, "total": total,
                                      "recommendations": list(suggestions.values())})


def storedOutcome(raw: dict[str, object]) -> CheckOutcome:
    outcome = CheckOutcome.model_validate(raw)
    return outcome if outcome.total is not None else withScores(outcome, {}, {})
