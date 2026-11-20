"""Manifest of the annotated mini dataset: one record per task of one synthetic submission (A6.1).

Owner: developer 1 (dataset generator, A6.2). Consumers: developer 2 checks the expected steps, verdicts
and issues; developer 3 checks expectedReference against an independent calculation (C5.1).

The manifest is the answer key. It never goes into a student's DOCX or into the ZIP of submissions,
and the closed split never reaches agent prompts or fixtures. The statement (what the student sees)
and expectedReference (the key) are separate fields on purpose. Names are synthetic; no real school data.
File paths are relative to the directory of manifest.json.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from contracts.v1.grading.taskresult import AnswerVerdict, MethodVerdict, ReasoningVerdict
from contracts.v1.references.reference import VALUE_SCHEMAS

MANIFEST_SCHEMA_VERSION = "datasetManifest.v1"

NonEmptyStr = Annotated[str, Field(min_length=1)]
CaseId = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]{0,63}$")]
"""camelCase, stable across regenerations with the same seed, for example "vectorLength34Correct"."""
DatasetVersion = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]{0,31}\.v[1-9][0-9]{0,8}$")]
"""One published version of a dataset, for example "mini.v1"."""
DocxPath = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*\.docx$")]
"""Relative POSIX path of a Word file inside the dataset. No segment starts with a dot, so "." and ".." cannot occur."""
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

Split = Literal["demo", "e2e", "closed"]
"""demo: open examples for development; e2e: the open kit of the end-to-end test with fixed answers;
closed: evaluation only. A source task of a closed case never appears in demo or e2e."""


class AssignmentRef(BaseModel):
    """The task in the teacher's variant, as the student sees it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: DocxPath
    taskNumber: NonEmptyStr
    """The number printed in the DOCX, as in TeacherRequirement.taskNumber of checks.v1."""
    statement: NonEmptyStr
    constraints: dict[str, JsonValue]
    """The same constraints developer 2 sends with the reference request (answer form, bounds)."""
    sourceTaskId: NonEmptyStr
    """The original problem. Splits are separated by it, not by student."""


class SubmissionRef(BaseModel):
    """One synthetic student's DOCX; the ZIP of submissions holds the same bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: DocxPath
    sha256: Sha256Hex
    studentName: NonEmptyStr
    """Synthetic name printed in the work; the teacher binds the file to a student by it."""


class ExpectedReference(BaseModel):
    """The key: what the reference API should answer for this task (a ready value)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaId: NonEmptyStr
    value: dict[str, JsonValue]

    @model_validator(mode="after")
    def checkValue(self) -> Self:
        valueSchema = VALUE_SCHEMAS.get(self.schemaId)
        if valueSchema is None:
            raise ValueError(f"unknown schemaId {self.schemaId!r}")
        valueSchema.model_validate(self.value)
        return self


class ExpectedStep(BaseModel):
    """One step of the solution as developer 2's reader should extract it, in order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stepId: NonEmptyStr
    markdown: NonEmptyStr


class ExpectedVerdict(BaseModel):
    """Answer, reasoning and method are separate dimensions, as in taskResult."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: AnswerVerdict
    reasoning: ReasoningVerdict
    method: MethodVerdict
    needsReview: bool


class ExpectedIssue(BaseModel):
    """One expected error at one step; a consequence points to its root through causedByIssueId."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issueId: NonEmptyStr
    stepId: NonEmptyStr
    category: NonEmptyStr
    causedByIssueId: NonEmptyStr | None = None


class DatasetCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    caseId: CaseId
    split: Split
    assignmentRef: AssignmentRef
    submissionRef: SubmissionRef
    expectedReference: ExpectedReference | None
    """null: no reference is expected, the task goes to the teacher."""
    expectedSteps: list[ExpectedStep]
    expectedVerdict: ExpectedVerdict
    expectedIssues: list[ExpectedIssue]

    @model_validator(mode="after")
    def checkLinks(self) -> Self:
        stepIds = [step.stepId for step in self.expectedSteps]
        issueIds = [issue.issueId for issue in self.expectedIssues]
        if len(set(stepIds)) != len(stepIds):
            raise ValueError(f"case {self.caseId}: stepId repeats")
        if len(set(issueIds)) != len(issueIds):
            raise ValueError(f"case {self.caseId}: issueId repeats")
        causes = {issue.issueId: issue.causedByIssueId for issue in self.expectedIssues}
        for issue in self.expectedIssues:
            if issue.stepId not in stepIds:
                raise ValueError(f"case {self.caseId}: issue {issue.issueId} points to unknown step {issue.stepId}")
            if issue.causedByIssueId is not None and issue.causedByIssueId not in causes:
                raise ValueError(
                    f"case {self.caseId}: issue {issue.issueId} is caused by unknown issue {issue.causedByIssueId}"
                )
            seen = {issue.issueId}
            cause = issue.causedByIssueId
            while cause is not None:
                if cause in seen:
                    raise ValueError(f"case {self.caseId}: issue {issue.issueId} is in a cycle of causes")
                seen.add(cause)
                cause = causes[cause]
        if self.expectedVerdict.answer == "skipped" and (stepIds or issueIds):
            raise ValueError(f"case {self.caseId}: a skipped task has no steps and no issues")
        return self


class DatasetManifest(BaseModel):
    """manifest.json of one dataset version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["datasetManifest.v1"]
    datasetVersion: DatasetVersion
    seed: Annotated[int, Field(ge=0, strict=True)]
    cases: Annotated[list[DatasetCase], Field(min_length=1)]

    @model_validator(mode="after")
    def checkCases(self) -> Self:
        caseIds = [case.caseId for case in self.cases]
        repeated = sorted({caseId for caseId in caseIds if caseIds.count(caseId) > 1})
        if repeated:
            raise ValueError(f"caseId repeats: {', '.join(repeated)}")
        submissions: dict[str, SubmissionRef] = {}
        tasks: dict[tuple[str, str], AssignmentRef] = {}
        for case in self.cases:
            known = submissions.setdefault(case.submissionRef.file, case.submissionRef)
            if known != case.submissionRef:
                raise ValueError(f"case {case.caseId}: {case.submissionRef.file} differs from another case")
            task = case.assignmentRef
            knownTask = tasks.setdefault((task.file, task.taskNumber), task)
            if knownTask != task:
                raise ValueError(f"case {case.caseId}: task {task.taskNumber} of {task.file} differs from another case")
        closed = {case.assignmentRef.sourceTaskId for case in self.cases if case.split == "closed"}
        leaked = sorted(
            {case.caseId for case in self.cases if case.split != "closed" and case.assignmentRef.sourceTaskId in closed}
        )
        if leaked:
            raise ValueError(f"open cases share a source task with the closed split: {', '.join(leaked)}")
        return self
