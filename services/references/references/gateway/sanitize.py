"""Builds the outgoing model call from a review request, keeping only what the provider needs.

Allowed out: the statement, the rubric, the solution steps (stepId and markdown) and the reference value.
Not sent: requestId, promptVersion, taskId, referenceId. Extra fields such as a student name or file name
cannot arrive at all: ModelReviewRequest forbids unknown fields.

Policy for personal data that is still inside the text:
- a step that is only a signature line ("Выполнил: Иванов И.", "ФИО: ...") and has no "=" is not part of
  the solution: it is removed, and its stepId is not offered to the model;
- any other suspicious fragment (full name with a patronymic, surname with initials, a labelled name,
  e-mail, phone number) stops the call with personal_data_suspected. The text is never changed, so minus
  signs, coordinates and formulas go out exactly as they came.
Logs and error messages name only the requestId, the field and the kind of finding, never the text.
"""

import re
from collections import Counter

from contracts.v1.references.modelreview import PERSONAL_DATA_SUSPECTED, ModelReviewRequest, ReviewStep
from references.gateway.diagnostics import logEvent
from references.gateway.prompts import PromptCatalog
from references.gateway.transports.base import ModelCall, ProviderReference, RequestRefused, ReviewData

LABEL = (
    r"(?i:ФИО|Ф\.\s?И\.\s?О\.|фамилия|имя|отчество|ученица|ученик|учащаяся|учащийся|"
    r"работу\s+выполнила|работу\s+выполнил|выполнила|выполнил|подпись|автор)"
)
SIGNATURE_LINE = re.compile(rf"\s*{LABEL}\s*[:\-—–]?\s*[А-ЯЁ][^=]*")
SUSPICIOUS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "phone": re.compile(r"(?<![\d.,])(?:\+7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?![\d.,])"),
    "fullName": re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:ич|вна|ична)\b"),
    "initials": re.compile(
        r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.|\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?[А-ЯЁ][а-яё]{2,}"
    ),
    "labelledName": re.compile(rf"(?<![А-ЯЁа-яё]){LABEL}\s*[:\-—–]?\s*[А-ЯЁ][а-яё]+"),
}


def isSignatureLine(markdown: str) -> bool:
    return SIGNATURE_LINE.fullmatch(markdown) is not None


def findPersonalData(text: str) -> str | None:
    """The kind of the first suspicious fragment, or None."""
    for kind, pattern in SUSPICIOUS.items():
        if pattern.search(text) is not None:
            return kind
    return None


def refuse(where: str, kind: str) -> RequestRefused:
    """Logged by the review service with the request ids; the message names the place and kind only."""
    return RequestRefused(
        PERSONAL_DATA_SUSPECTED, f"{where} looks like personal data ({kind}); the model was not called"
    )


def keptSteps(request: ModelReviewRequest, traceId: str) -> tuple[ReviewStep, ...]:
    counts = Counter(step.stepId for step in request.steps)
    duplicates = sorted(stepId for stepId, count in counts.items() if count > 1)
    if duplicates:
        raise RequestRefused("invalid_input", f"stepId must be unique, repeated: {', '.join(duplicates)}")
    kept: list[ReviewStep] = []
    removed: list[str] = []
    for step in request.steps:
        if isSignatureLine(step.markdown):
            removed.append(step.stepId)
            continue
        kind = findPersonalData(step.markdown)
        if kind is not None:
            raise refuse(f"step {step.stepId}", kind)
        kept.append(step)
    if removed:
        logEvent(
            "review.signatureStepsRemoved",
            stage="sanitize",
            requestId=request.requestId,
            traceId=traceId,
            stepIds=removed,
        )
    if not kept:
        raise RequestRefused("invalid_input", "no solution steps are left after removing signature lines")
    return tuple(kept)


def buildModelCall(request: ModelReviewRequest, prompts: PromptCatalog, traceId: str = "-") -> ModelCall:
    instructions = prompts.instructions(request.promptVersion)
    for where, text in [("statement", request.statement)] + [
        (f"rubric[{index}]", line) for index, line in enumerate(request.rubric)
    ]:
        kind = findPersonalData(text)
        if kind is not None:
            raise refuse(where, kind)
    data = ReviewData(
        statement=request.statement,
        rubric=tuple(request.rubric),
        steps=keptSteps(request, traceId),
        reference=ProviderReference(schemaId=request.reference.schemaId, value=request.reference.value),
    )
    return ModelCall(
        requestId=request.requestId,
        promptVersion=request.promptVersion,
        taskId=request.taskId,
        instructions=instructions,
        data=data,
    )
