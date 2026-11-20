"""Compose the overall feedback from the checked attempt and its stored references."""

from collections import defaultdict
from collections.abc import Collection, Mapping

from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.reference import ReferenceReady, ReferenceResult, ScalarValueV1
from grading.assignment import AssignmentTask
from grading.review import anchoredIssues
from grading.submission import SubmissionReading


def taskFeedback(result: TaskResult, stepIds: list[str], exact: str | None) -> list[str]:
    if result.needsReview or result.answer in ("unreadable", "skipped"):
        return ["Задание требует ручной проверки. Итог пока не подтверждён."]

    answer = "Ответ верный." if result.answer == "correct" else "Ответ отличается от эталона."
    lines = [answer]
    if result.reasoning == "valid":
        lines.append(f"Ход решения верен. Проверенные шаги — {', '.join(stepIds)}." if stepIds else "Ход решения верен.")
    elif result.reasoning == "not_shown":
        lines.append("Записан только ответ. Покажите, как вы его получили.")
    elif result.reasoning == "unknown":
        lines.append("Ход решения пока не подтверждён.")
    else:
        lines.append("Ход решения требует исправления.")

    methodFeedback = {
        "met": "Требуемый способ показан.",
        "unmet": "Повторите решение требуемым способом.",
        "unconfirmed": "Покажите применение требуемого способа.",
    }
    if result.method in methodFeedback:
        lines.append(methodFeedback[result.method])

    issues = anchoredIssues(result.issues, set(stepIds))
    issueSteps = {issue.issueId: issue.stepId for issue in issues}
    for issue in issues:
        if issue.causedByIssueId is None:
            lines.append(f"Шаг {issue.stepId}. {issue.comment.strip()}")
            if exact is not None:
                lines.append(f"Предложение по исправлению шага {issue.stepId} — {issue.correction.strip()}")
        else:
            causeStep = issueSteps[issue.causedByIssueId]
            lines.append(f"Шаг {issue.stepId}. Следствие замечания к шагу {causeStep}.")
    if exact is not None and (result.answer == "incorrect" or result.reasoning == "invalid"):
        lines.append(f"Пересчитайте зависимые шаги и сравните итог с эталоном ({exact}).")
    return lines


def composeOverallReview(
    tasks: tuple[AssignmentTask, ...], reading: SubmissionReading, results: tuple[TaskResult, ...],
    references: Mapping[str, ReferenceResult], *, teacherVerdicts: Collection[str] = (),
) -> str:
    byTask = {result.taskId: result for result in results}
    readings = {task.taskId: task for task in reading.taskReadings}
    steps: dict[str, list[str]] = defaultdict(list)
    for step in reading.steps:
        if step.readStatus == "read":
            steps[step.taskId].append(step.stepId)

    sections = []
    for task in tasks:
        result = byTask.get(task.taskId)
        source = readings.get(task.taskId)
        reference = references.get(task.taskId)
        exact = (
            ScalarValueV1.model_validate(reference.value).exact
            if isinstance(reference, ReferenceReady) and reference.schemaId == "scalar.v1" else None
        )
        if result is not None and task.taskId in teacherVerdicts:
            lines = ["Задание пропущено. Допишите решение."] if result.answer == "skipped" else taskFeedback(
                result, steps[task.taskId], exact
            )
        elif source is None or source.status == "needsReview":
            detail = source.reviewReason if source is not None else None
            lines = ["Запись требует ручной проверки.", *([detail] if detail else [])]
        elif source.status == "skipped":
            lines = ["Задание пропущено. Допишите решение."]
        elif result is None:
            lines = ["Итог проверки ещё не подтверждён."]
        elif not isinstance(reference, ReferenceReady):
            lines = ["Эталон недоступен. Задание требует ручной проверки."]
        else:
            lines = taskFeedback(result, steps[task.taskId], exact)
        sections.append(f"Задание {task.taskNumber}. " + " ".join(lines))
    if reading.unassignedFragments:
        sections.append("Часть записи не удалось отнести к заданию. Эти фрагменты требуют ручной проверки.")
    return "\n\n".join(sections)
