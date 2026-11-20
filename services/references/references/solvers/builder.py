"""solver-builder: python -m references.solvers.builder

Takes one open build at a time from refs.solver_builds and turns it into a published solver version or a failure.

Admission (decided 2026-11-20, "солвер на задание"): two solvers written independently for the same task, one
with the direct approach and one with an independent approach, must give the same answer in the sandbox
(answers.answersAgree). If they differ, a third (tiebreak) solver decides by majority. Only then is the version
published: code, the cross-check and SolverArtifact metadata in S3 under references/solvers/<solverVersion>/,
and a row in refs.solver_versions with the agreed value.

Failures, all final for the task key:
- generation_failed: an author used up its attempts (format, policy or sandbox errors), or the provider refused;
- independent_tests_failed: the independent solvers did not agree;
- generation_timeout: the build deadline passed;
- personal_data_suspected: the statement looks like it holds personal data; the model is never called.
A rate limit or an unavailable provider only pauses the build until its deadline.
"""

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import JsonValue

from contracts.v1.references.solverartifact import (
    GenerationTrace,
    SolverArtifact,
    SolverScope,
    StoredObject,
    metadataKey,
    versionPrefix,
)
from references.gateway.sanitize import findPersonalData
from references.gateway.transports.base import TransportError
from references.solvers.answers import answersAgree
from references.solvers.artifacts import ArtifactConflict, ArtifactStore
from references.solvers.sandbox import Sandbox
from references.solvers.author import AuthorFailed, AuthorLog, AuthorTask, BuildTimedOut, Candidate, SolverAuthor, sha256
from references.solvers.store import BuildView, Lease, NewVersion, SolverStore, familyIdOf
from shared.clock import Clock, SystemClock

SCHEMA_ID = "scalar.v1"
DEFAULT_LEASE_S = 180.0
DEFAULT_PAUSE_S = 30.0
PAUSED_CODES = frozenset({"rate_limited", "unavailable", "timeout"})
MAX_VERSION_TRIES = 5
logger = logging.getLogger("references.solvers")


@dataclass(frozen=True)
class Admitted:
    winner: Candidate
    agreeing: tuple[Candidate, ...]
    others: tuple[Candidate, ...]


class LeaseLost(Exception):
    """Another builder took the build after our lease ran out."""


def admit(direct: Candidate, independent: Candidate, tiebreak: Candidate | None) -> Admitted | None:
    if answersAgree(direct.exact, independent.exact):
        return Admitted(direct, (direct, independent), ())
    if tiebreak is None:
        return None
    for chosen, other in ((direct, independent), (independent, direct)):
        if answersAgree(chosen.exact, tiebreak.exact):
            return Admitted(chosen, (chosen, tiebreak), (other,))
    return None


class SolverBuilder:
    def __init__(
        self,
        store: SolverStore,
        author: SolverAuthor,
        artifacts: ArtifactStore,
        *,
        clock: Clock | None = None,
        leaseS: float = DEFAULT_LEASE_S,
    ) -> None:
        self.store = store
        self.author = author
        self.artifacts = artifacts
        self.clock = clock if clock is not None else SystemClock()
        self.leaseS = leaseS

    def runOnce(self) -> bool:
        """Work on one build if one is open. False when there was nothing to do."""
        claimed = self.store.claim(self.clock.now(), self.leaseS)
        if claimed is None:
            return False
        lease, build = claimed
        self.build(lease, build)
        return True

    def build(self, lease: Lease, build: BuildView) -> None:
        log = AuthorLog(entries=list(build.log))
        task = AuthorTask(build.statement, build.constraints)
        try:
            if self.clock.now() >= build.deadline:
                raise BuildTimedOut()
            kind = findPersonalData(build.statement)
            if kind is not None:
                log.entries.append({"event": "refused", "kind": kind})
                self.finish(lease, build, "personal_data_suspected", log)
                return
            admitted = self.crossCheck(lease, build, task, log)
            if admitted is None:
                self.finish(lease, build, "independent_tests_failed", log)
                return
            self.publish(lease, build, admitted, log)
        except LeaseLost:
            logger.warning(json.dumps({"event": "solver.leaseLost", "buildId": build.buildId}))
        except BuildTimedOut:
            self.finish(lease, build, "generation_timeout", log)
        except AuthorFailed:
            self.finish(lease, build, "generation_failed", log)
        except TransportError as error:
            log.entries.append({"event": "transport", "code": error.code})
            if error.code not in PAUSED_CODES:
                self.finish(lease, build, "generation_failed", log)
                return
            pauseS = float(error.retryAfterS) if error.retryAfterS is not None else DEFAULT_PAUSE_S
            notBefore = self.clock.now() + timedelta(seconds=pauseS)
            if notBefore >= build.deadline:
                self.finish(lease, build, "generation_timeout", log)
                return
            self.store.pause(lease, notBefore, log.entries, log.modelCalls)
            logger.info(json.dumps({"event": "solver.paused", "buildId": build.buildId, "code": error.code}))

    def crossCheck(self, lease: Lease, build: BuildView, task: AuthorTask, log: AuthorLog) -> Admitted | None:
        def keepAlive() -> None:
            if not self.store.extend(lease, self.clock.now(), self.leaseS):
                raise LeaseLost()

        write = self.author.write
        direct = write(build.buildId, task, "direct", build.deadline, log, keepAlive)
        independent = write(build.buildId, task, "independent", build.deadline, log, keepAlive)
        admitted = admit(direct, independent, None)
        if admitted is not None:
            return admitted
        log.entries.append({"event": "disagreement", "approaches": ["direct", "independent"]})
        tiebreak = write(build.buildId, task, "tiebreak", build.deadline, log, keepAlive)
        return admit(direct, independent, tiebreak)

    def finish(self, lease: Lease, build: BuildView, reasonCode: str, log: AuthorLog) -> None:
        self.store.fail(lease, reasonCode, log.entries, log.modelCalls)
        logger.info(json.dumps({"event": "solver.failed", "buildId": build.buildId, "code": reasonCode}))

    def publish(self, lease: Lease, build: BuildView, admitted: Admitted, log: AuthorLog) -> None:
        familyId = familyIdOf(build.taskKey)
        versionNo = self.store.nextVersionNo(familyId)
        for _ in range(MAX_VERSION_TRIES):
            solverVersion = f"{familyId}.v{versionNo}"
            try:
                artifact = self.writeArtifacts(solverVersion, familyId, build, admitted)
            except ArtifactConflict:
                versionNo += 1
                continue
            version = NewVersion(
                taskKey=build.taskKey,
                source="generated",
                schemaId=SCHEMA_ID,
                value=admitted.winner.value,
                codeSha256=artifact.code.sha256,
                testsSha256=artifact.tests.sha256,
            )
            log.entries.append({"event": "published", "solverVersion": solverVersion})
            if not self.store.publish(
                lease, version, solverVersion, versionNo, metadataKey(solverVersion), artifact.publishedAt,
                log.entries, log.modelCalls,
            ):
                raise LeaseLost()
            logger.info(json.dumps({"event": "solver.published", "buildId": build.buildId, "version": solverVersion}))
            return
        raise RuntimeError(f"no free solver version number for {familyId}")

    def writeArtifacts(
        self, solverVersion: str, familyId: str, build: BuildView, admitted: Admitted
    ) -> SolverArtifact:
        prefix = versionPrefix(solverVersion)
        code = self.put(prefix + "solver.py", admitted.winner.code.encode("utf-8"), "text/x-python; charset=utf-8")
        checks: list[dict[str, JsonValue]] = []
        for candidate in admitted.agreeing[1:] + admitted.others:
            stored = self.put(
                prefix + f"{candidate.approach}.py", candidate.code.encode("utf-8"), "text/x-python; charset=utf-8"
            )
            checks.append(
                {
                    "approach": candidate.approach,
                    "codeKey": stored.key,
                    "codeSha256": stored.sha256,
                    "value": candidate.value,
                    "agrees": candidate in admitted.agreeing,
                }
            )
        crossCheck = {
            "kind": "crossCheck.v1",
            "task": {"statement": build.statement, "constraints": build.constraints},
            "expected": admitted.winner.value,
            "solver": {"approach": admitted.winner.approach, "codeSha256": code.sha256},
            "checks": checks,
        }
        tests = self.put(prefix + "tests.json", jsonBytes(crossCheck), "application/json")
        artifact = SolverArtifact(
            schemaVersion="solverArtifact.v1",
            solverVersion=solverVersion,
            source="generated",
            scope=SolverScope(familyId=familyId, schemaId=SCHEMA_ID, constraints=scopeOf(build.constraints)),
            code=code,
            tests=tests,
            publishedAt=self.clock.now(),
            generation=GenerationTrace(
                modelId=admitted.winner.modelId,
                authorInputSha256=sha256(json.dumps(crossCheck["task"], ensure_ascii=False, sort_keys=True)),
            ),
        )
        self.put(metadataKey(solverVersion), jsonBytes(artifact.model_dump(mode="json")), "application/json")
        return artifact

    def put(self, key: str, body: bytes, contentType: str) -> StoredObject:
        self.artifacts.put(key, body, contentType)
        return StoredObject(key=key, sha256=hashlib.sha256(body).hexdigest(), sizeBytes=len(body))

    def serve(self, idleS: float = 2.0) -> None:
        while True:
            if not self.runOnce():
                self.clock.sleep(idleS)


def scopeOf(constraints: dict[str, JsonValue]) -> dict[str, list[JsonValue]]:
    return {name: [value] for name, value in constraints.items()}


def jsonBytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


SANDBOXES = ("directory", "local")


def sandboxFromEnv(environ: Mapping[str, str], transportName: str) -> Sandbox:
    """SOLVER_SANDBOX=directory (the solver-sandbox container) or local; nothing is chosen implicitly.

    local runs the code next to the builder, where GIGACHAT_AUTH_KEY is in reach: refused with gigachat.
    """
    from pathlib import Path

    from references.gateway.transports.base import ConfigError
    from references.solvers.sandbox import DirectorySandbox, LocalSandbox

    kind = environ.get("SOLVER_SANDBOX", "").strip()
    if kind == "directory":
        return DirectorySandbox(Path(environ.get("SOLVER_SANDBOX_DIR", "/jobs")))
    if kind == "local":
        if transportName != "fixture":
            raise ConfigError("SOLVER_SANDBOX=local works only with GATEWAY_TRANSPORT=fixture: the key would be in reach")
        return LocalSandbox()
    raise ConfigError(f"SOLVER_SANDBOX must be one of {', '.join(SANDBOXES)}, got {kind!r}")


def main() -> None:
    import os

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from references.gateway.limiter import LimitedTransport, ProviderLimiter, RetryPolicy
    from references.gateway.settings import GatewaySettings, buildTransport, parsePositive
    from references.solvers.artifacts import S3ArtifactStore
    from shared.dotenv import loadDotEnv

    loadDotEnv()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = GatewaySettings.fromEnv()
    clock = SystemClock()
    sessionFactory = sessionmaker(create_engine(settings.databaseUrl), expire_on_commit=False)
    transport = buildTransport(settings, clock=clock)
    limiter = ProviderLimiter(sessionFactory, clock, transport.name, settings.providerLimit, settings.leaseS)
    policy = RetryPolicy(maxAttempts=settings.maxAttempts, maxWaitS=settings.maxWaitS)
    attempts = int(parsePositive(os.environ, "SOLVER_AUTHOR_ATTEMPTS", 3, whole=True))
    author = SolverAuthor(
        LimitedTransport(transport, limiter, clock, policy), sandboxFromEnv(os.environ, transport.name),
        attempts=attempts, clock=clock,
    )
    builder = SolverBuilder(SolverStore(sessionFactory), author, S3ArtifactStore.fromEnv(), clock=clock)
    logger.info(json.dumps({"event": "solver.builderStarted", "transport": transport.name}))
    builder.serve()


if __name__ == "__main__":
    main()
