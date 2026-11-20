"""Reference provider with generated solvers (C6.3, REFERENCES_PROVIDER=generating).

Fixtures answer first, exactly as FixtureProvider. A task no fixture knows gets a solver of its own:
- a published, not withdrawn version for the task key -> ready, source=generated, its solverVersion and the value
  the admitted solver computed in the sandbox;
- otherwise the build of the task is opened (one per task key, shared by every assignment with that statement)
  and the reference is pending; the registry asks again after retryAfterS (check());
- a failed build -> its reason: unsupported for personal_data_suspected, validation_failed for the rest.
The provider never calls the model and never runs generated code: solver-builder does both (references/solvers).
"""

from references.providers.base import PendingOutcome, ProviderOutcome, ProviderTask, ReadyOutcome, RefusedOutcome
from references.providers.fixture import UNKNOWN_TASK, FixtureProvider
from references.registry.store import SessionFactory
from references.settings import DEFAULT_BUILD_DEADLINE_S, DEFAULT_BUILD_POLL_S
from references.solvers.store import SolverStore, retryAfterOf, taskKeyOf
from shared.clock import Clock, SystemClock

UNSUPPORTED_REASONS = frozenset({"personal_data_suspected"})
WITHDRAWN = "solver_failed"


class GeneratingProvider:
    def __init__(
        self,
        fixtures: FixtureProvider,
        sessionFactory: SessionFactory,
        clock: Clock | None = None,
        *,
        buildDeadlineS: float = DEFAULT_BUILD_DEADLINE_S,
        pollS: int = DEFAULT_BUILD_POLL_S,
    ) -> None:
        self.fixtures = fixtures
        self.store = SolverStore(sessionFactory)
        self.clock = clock if clock is not None else SystemClock()
        self.buildDeadlineS = buildDeadlineS
        self.pollS = pollS

    def start(self, task: ProviderTask) -> ProviderOutcome:
        outcome = self.fixtures.start(task)
        return self.generated(task) if isUnknown(outcome) else outcome

    def check(self, task: ProviderTask) -> ProviderOutcome:
        outcome = self.fixtures.check(task)
        return self.generated(task) if isUnknown(outcome) else outcome

    def generated(self, task: ProviderTask) -> ProviderOutcome:
        version = self.store.activeVersion(taskKeyOf(task.statement, task.constraints))
        if version is not None:
            return ReadyOutcome(
                source="generated" if version.source == "generated" else "manual",
                schemaId=version.schemaId,
                value=version.value,
                solverVersion=version.solverVersion,
            )
        now = self.clock.now()
        build = self.store.ensureBuild(task.statement, task.constraints, now, self.buildDeadlineS)
        if build.state in ("queued", "running") and now >= build.deadline:
            build = self.store.expireIfAbandoned(build.buildId, now)
        if build.state == "failed":
            assert build.reasonCode is not None
            if build.reasonCode in UNSUPPORTED_REASONS:
                return RefusedOutcome(state="unsupported", reasonCode=build.reasonCode)
            return RefusedOutcome(state="validation_failed", reasonCode=build.reasonCode)
        if build.state == "published":
            # Published, but no active version: it was withdrawn.
            return RefusedOutcome(state="validation_failed", reasonCode=WITHDRAWN)
        return PendingOutcome(retryAfterS=retryAfterOf(build, now, self.pollS))


def isUnknown(outcome: ProviderOutcome) -> bool:
    return isinstance(outcome, RefusedOutcome) and outcome.reasonCode == UNKNOWN_TASK
