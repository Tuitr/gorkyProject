"""The solver author: a short loop around the model that writes the code of one solver for one task.

One approach (direct, independent or tiebreak) gets up to `attempts` model calls. After each call the reply is
read as {"code": "..."}, checked against the code policy and run once in the sandbox; a failure goes back to the
model as previousError on the next call. The author sees only the statement, the constraints and its own
previous attempt: no student work, no assignment or task ids, never the other solvers' code or answers.

Model calls go through the gateway transport (fixture or GigaChat, with the shared provider limit).
A TransportError leaves this module: the builder decides whether to pause or to fail the build.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue

from references.gateway.prompts import PromptCatalog
from references.gateway.transports.base import AuthorApproach, AuthorData, ModelCall, ModelTransport
from references.solvers.answers import exactOf
from references.solvers.policy import CodeRefused, checkSolverCode
from references.solvers.sandbox import Sandbox
from shared.clock import Clock, SystemClock

PROMPT_VERSION = "solverAuthor.v1"
PROMPTS_DIR = Path(__file__).with_name("prompts")
DEFAULT_ATTEMPTS = 3
FENCE = re.compile(r"```(?:[A-Za-z0-9_+-]*)\s*\n(.*?)```", re.DOTALL)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthorTask:
    statement: str
    constraints: dict[str, JsonValue]

    def asInput(self) -> dict[str, JsonValue]:
        """What solve(task) receives in the sandbox."""
        return {"statement": self.statement, "constraints": self.constraints}


@dataclass(frozen=True)
class Candidate:
    approach: AuthorApproach
    code: str
    value: dict[str, JsonValue]
    exact: str
    modelId: str

    @property
    def codeSha256(self) -> str:
        return sha256(self.code)


class AuthorFailed(Exception):
    """Every attempt of one approach failed on format, policy or in the sandbox."""


class BuildTimedOut(Exception):
    """The build deadline passed before the author could finish."""


@dataclass
class AuthorLog:
    """Hashes and codes only, for refs.solver_builds.log."""

    entries: list[dict[str, JsonValue]] = field(default_factory=list)
    modelCalls: int = 0


def readCode(content: str) -> str:
    """The code from {"code": "..."}; a JSON object inside a Markdown fence, or a bare Python fence, is accepted."""
    text = content.strip()
    candidates = [text] + [match.strip() for match in FENCE.findall(text)]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        body = parsed.get("code") if isinstance(parsed, dict) else None
        if isinstance(body, str) and body.strip():
            return body
    for candidate in candidates[1:]:
        if "def solve" in candidate:
            return str(candidate)
    raise CodeRefused('the reply must be a JSON object {"code": "<python code>"}')


class SolverAuthor:
    def __init__(
        self,
        transport: ModelTransport,
        sandbox: Sandbox,
        *,
        prompts: PromptCatalog | None = None,
        attempts: int = DEFAULT_ATTEMPTS,
        clock: Clock | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("the author needs at least one attempt")
        self.transport = transport
        self.sandbox = sandbox
        self.prompts = prompts if prompts is not None else PromptCatalog(PROMPTS_DIR)
        self.attempts = attempts
        self.clock = clock if clock is not None else SystemClock()

    def write(
        self,
        buildId: str,
        task: AuthorTask,
        approach: AuthorApproach,
        deadline: datetime,
        log: AuthorLog,
        keepAlive: Callable[[], None] = lambda: None,
    ) -> Candidate:
        instructions = self.prompts.instructions(PROMPT_VERSION)
        previousCode: str | None = None
        previousError: str | None = None
        for attempt in range(1, self.attempts + 1):
            if self.clock.now() >= deadline:
                raise BuildTimedOut()
            keepAlive()
            call = ModelCall(
                requestId=f"{buildId}:{approach}:{attempt}",
                promptVersion=PROMPT_VERSION,
                taskId=buildId,
                instructions=instructions,
                data=AuthorData(
                    statement=task.statement,
                    constraints=task.constraints,
                    approach=approach,
                    previousCode=previousCode,
                    previousError=previousError,
                ),
                deadline=deadline,
            )
            log.modelCalls += 1
            reply = self.transport.complete(call)
            entry: dict[str, JsonValue] = {"approach": approach, "attempt": attempt, "modelId": reply.modelId}
            log.entries.append(entry)
            try:
                code = readCode(reply.content)
                entry["codeSha256"] = sha256(code)
                checkSolverCode(code)
            except CodeRefused as error:
                entry["outcome"] = "refused"
                previousCode, previousError = reply.content[:20_000], str(error)
                continue
            keepAlive()
            result = self.sandbox.run(code, task.asInput())
            exact = exactOf(result.value) if result.ok and result.value is not None else None
            if exact is None:
                entry["outcome"] = "sandbox_failed" if not result.ok else "bad_answer"
                previousCode = code
                previousError = result.error if not result.ok and result.error else (
                    'solve(task) must return {"exact": "<answer>"} with a non-empty answer text'
                )
                continue
            entry["outcome"] = "ok"
            assert result.value is not None
            return Candidate(approach=approach, code=code, value=dict(result.value), exact=exact, modelId=reply.modelId)
        raise AuthorFailed()
