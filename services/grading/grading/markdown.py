"""Render a downloadable report from the checked result and the parsed submission."""

import re
from collections import defaultdict
from string import punctuation

from contracts.v1.grading.checkresult import CheckOutcome
from contracts.v1.grading.taskresult import Issue
from grading.assignment import AssignmentTask
from grading.review import anchoredIssues
from grading.submission import SolutionStep, SubmissionReading

INLINE_MATH = re.compile(r"\$([^\n$]+)\$")
MATH_COMMAND = re.compile(r"\\(?:sqrt|vec|cdot|pm|left|right|frac|times|le|ge)(?![a-zA-Z])")
MATH_TEXT = re.compile(r"[\w\s{}(),.;=+\-−*/^|√±·]+")
ESCAPED_PUNCTUATION = {ord(char): "\\" + char for char in punctuation}


def inlineText(text: str) -> str:
    """Escape source text and keep only the supported inline mathematical syntax."""
    text = " ".join(text.split())
    parts: list[str] = []
    cursor = 0
    for match in INLINE_MATH.finditer(text):
        math = MATH_COMMAND.sub("", match[1])
        if not MATH_TEXT.fullmatch(math):
            continue
        parts.extend((text[cursor:match.start()].translate(ESCAPED_PUNCTUATION), match[0]))
        cursor = match.end()
    parts.append(text[cursor:].translate(ESCAPED_PUNCTUATION))
    return "".join(parts)


def renderCheckMarkdown(outcome: CheckOutcome, reading: SubmissionReading, tasks: tuple[AssignmentTask, ...]) -> str:
    sections = ["# Разбор работы", "## Общий отзыв"]
    sections.extend(inlineText(paragraph) for paragraph in outcome.overallReview.split("\n\n") if paragraph.strip())
    taskNumbers = {task.taskId: task.taskNumber for task in tasks}
    stepsByTask: dict[str, list[SolutionStep]] = defaultdict(list)
    for step in reading.steps:
        stepsByTask[step.taskId].append(step)
    assessments = {a.taskId: a for a in outcome.taskAssessments}
    for result in outcome.taskResults:
        steps = stepsByTask[result.taskId]
        sections.append("## Задание " + inlineText(taskNumbers.get(result.taskId, result.taskId)))
        if assessment := assessments.get(result.taskId):
            score = "ожидает решения" if assessment.score is None else f"{assessment.score:g}"
            sections.append(f"Балл — {score} из {assessment.maxScore:g}.")
        issues = anchoredIssues(result.issues, {step.stepId for step in steps if step.readStatus == "read"})
        byStep: dict[str, list[Issue]] = defaultdict(list)
        causeSteps = {issue.issueId: issue.stepId for issue in issues}
        for issue in issues:
            byStep[issue.stepId].append(issue)
        if not steps:
            sections.append("В записи нет разобранных шагов.")
        for step in steps:
            label = inlineText(step.stepId)
            sections.append(f"### Шаг {label}")
            sections.append(inlineText(step.markdown))
            if step.readStatus != "read":
                sections.append("Этот фрагмент требует ручной проверки.")
            for issue in byStep[step.stepId]:
                if issue.causedByIssueId is None:
                    sections.append("**Замечание.** " + inlineText(issue.comment))
                else:
                    cause = inlineText(causeSteps[issue.causedByIssueId])
                    sections.append(f"**Следствие шага {cause}.** " + inlineText(issue.comment))
                sections.append("**Исправление.** " + inlineText(issue.correction))
        if result.needsReview:
            sections.append("Итог по заданию требует уточнения.")
    if outcome.teacherComment:
        sections.extend(["## Комментарий учителя", inlineText(outcome.teacherComment)])
    if outcome.recommendations:
        sections.append("## Темы для повторения")
        for item in outcome.recommendations:
            sections.append(f"**{inlineText(item.topic)}** — {inlineText(item.text)}")
    if outcome.grade is not None:
        sections.append(f"Оценка — {outcome.grade}")
    return "\n\n".join(sections) + "\n"
