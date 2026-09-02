from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import multiprocessing as mp
import os
import queue
import time
from typing import Any, Mapping

from continuity import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionStatus,
    Output,
    RequestStatus,
)
from continuity.errors import ContinuityError, SemanticViolation
from continuity.invariants import InvariantOracle
from simulator import (
    AuthoritativeOutcome,
    CoreContinuityAuthority,
    PlacementDecision,
    PolicyID,
    PolicyObservation,
    WorkerObservation,
    authoritative_outcome,
    build_baseline_policies,
    decide_placement,
)

from .correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    CorrectnessSummary,
    ExplicitNonSuccess,
    MetricOpportunityScope,
    RecoveryAction,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)


S1_E1_SCHEMA = "cadi.s1-attempt-fencing-e1.v1"
S1_E1_COHORT_ID = "C4.2c:S1:E1"
S1_E1_START_METHOD = "spawn"
S1_E1_WORK_ROUNDS = 50_000
S1_E1_IPC_TIMEOUT_SECONDS = 10.0


class E1ScenarioMode(str, Enum):
    RETRY_RACE = "RETRY_RACE"
    PRETIMEOUT_SUCCESS_DELAYED_OBSERVATION = "PRETIMEOUT_SUCCESS_DELAYED_OBSERVATION"


@dataclass(frozen=True, slots=True)
class E1PresentationDirective:
    event_id: str
    attempt_id: str
    group: int
    stale: bool
    duplicate: bool = False

    def __post_init__(self) -> None:
        for value, name in ((self.event_id, "event_id"), (self.attempt_id, "attempt_id")):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.group, int) or isinstance(self.group, bool) or self.group < 0:
            raise ValueError("group must be a non-negative integer")
        if not isinstance(self.stale, bool) or not isinstance(self.duplicate, bool):
            raise TypeError("stale and duplicate must be bool")


@dataclass(frozen=True, slots=True)
class E1ScenarioSpec:
    scenario_id: str
    mode: E1ScenarioMode
    attempt_ids: tuple[str, ...]
    expected_committed_attempt_id: str
    presentations: tuple[E1PresentationDirective, ...]
    fault_class: str

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise ValueError("scenario_id must be a non-empty string")
        if not isinstance(self.mode, E1ScenarioMode):
            raise TypeError("mode must be E1ScenarioMode")
        if not isinstance(self.attempt_ids, tuple) or len(self.attempt_ids) < 2:
            raise ValueError("E1 retry-race scenarios require at least two AttemptIDs")
        if not all(isinstance(item, str) and item for item in self.attempt_ids):
            raise ValueError("attempt_ids must contain non-empty strings")
        if len(self.attempt_ids) != len(set(self.attempt_ids)):
            raise ValueError("attempt_ids must be unique")
        if self.expected_committed_attempt_id != self.attempt_ids[-1]:
            raise ValueError("expected committed Attempt must be the final generation")
        if not isinstance(self.presentations, tuple) or not self.presentations:
            raise ValueError("presentations must be a non-empty tuple")
        if len({item.event_id for item in self.presentations}) != len(self.presentations):
            raise ValueError("presentation EventIDs must be unique")
        if any(item.attempt_id not in self.attempt_ids for item in self.presentations):
            raise ValueError("presentations must reference declared AttemptIDs")
        groups = tuple(item.group for item in self.presentations)
        if groups != tuple(sorted(groups)):
            raise ValueError("presentations must be declared in nondecreasing group order")
        if not any(
            item.attempt_id == self.expected_committed_attempt_id and not item.stale
            for item in self.presentations
        ):
            raise ValueError("scenario must present the expected current Attempt")
        if any(
            item.stale and item.attempt_id == self.expected_committed_attempt_id
            for item in self.presentations
        ):
            raise ValueError("the expected committed Attempt cannot be oracle-stale")
        if not any(item.stale for item in self.presentations):
            raise ValueError("E1 S1 scenario must contain at least one SAAR opportunity")
        if not isinstance(self.fault_class, str) or not self.fault_class:
            raise ValueError("fault_class must be a non-empty string")

    @property
    def stale_presentation_event_ids(self) -> tuple[str, ...]:
        return tuple(item.event_id for item in self.presentations if item.stale)

    @property
    def presentation_groups(self) -> tuple[tuple[E1PresentationDirective, ...], ...]:
        result: list[tuple[E1PresentationDirective, ...]] = []
        for group in sorted({item.group for item in self.presentations}):
            result.append(tuple(item for item in self.presentations if item.group == group))
        return tuple(result)


def _event(scenario: str, label: str) -> str:
    return f"c4.2c:{scenario}:{label}"


S1_E1_SCENARIOS: tuple[E1ScenarioSpec, ...] = (
    E1ScenarioSpec(
        scenario_id="E1-A-stale-before-fresh",
        mode=E1ScenarioMode.RETRY_RACE,
        attempt_ids=("a1", "a2"),
        expected_committed_attempt_id="a2",
        presentations=(
            E1PresentationDirective(_event("A", "stale-a1"), "a1", 0, True),
            E1PresentationDirective(_event("A", "fresh-a2"), "a2", 1, False),
        ),
        fault_class="late stale result before current result",
    ),
    E1ScenarioSpec(
        scenario_id="E1-B-fresh-before-stale",
        mode=E1ScenarioMode.RETRY_RACE,
        attempt_ids=("a1", "a2"),
        expected_committed_attempt_id="a2",
        presentations=(
            E1PresentationDirective(_event("B", "fresh-a2"), "a2", 0, False),
            E1PresentationDirective(_event("B", "stale-a1"), "a1", 1, True),
        ),
        fault_class="current result before delayed stale result",
    ),
    E1ScenarioSpec(
        scenario_id="E1-C-duplicate-result-delivery",
        mode=E1ScenarioMode.RETRY_RACE,
        attempt_ids=("a1", "a2"),
        expected_committed_attempt_id="a2",
        presentations=(
            E1PresentationDirective(_event("C", "fresh-a2"), "a2", 0, False),
            E1PresentationDirective(_event("C", "fresh-a2-duplicate"), "a2", 1, False, True),
            E1PresentationDirective(_event("C", "stale-a1"), "a1", 2, True),
            E1PresentationDirective(_event("C", "stale-a1-duplicate"), "a1", 3, True, True),
        ),
        fault_class="duplicate current and stale result delivery",
    ),
    E1ScenarioSpec(
        scenario_id="E1-D-pretimeout-success-delayed-observation",
        mode=E1ScenarioMode.PRETIMEOUT_SUCCESS_DELAYED_OBSERVATION,
        attempt_ids=("a1", "a2"),
        expected_committed_attempt_id="a2",
        presentations=(
            E1PresentationDirective(_event("D", "stale-a1"), "a1", 0, True),
            E1PresentationDirective(_event("D", "fresh-a2"), "a2", 1, False),
        ),
        fault_class="physical success before timeout with delayed terminal observation",
    ),
    E1ScenarioSpec(
        scenario_id="E1-E-concurrent-terminal-race",
        mode=E1ScenarioMode.RETRY_RACE,
        attempt_ids=("a1", "a2"),
        expected_committed_attempt_id="a2",
        presentations=(
            E1PresentationDirective(_event("E", "stale-a1"), "a1", 0, True),
            E1PresentationDirective(_event("E", "fresh-a2"), "a2", 0, False),
        ),
        fault_class="concurrent stale and current terminal result race",
    ),
    E1ScenarioSpec(
        scenario_id="E1-F-three-generation-retry-race",
        mode=E1ScenarioMode.RETRY_RACE,
        attempt_ids=("a1", "a2", "a3"),
        expected_committed_attempt_id="a3",
        presentations=(
            E1PresentationDirective(_event("F", "stale-a1"), "a1", 0, True),
            E1PresentationDirective(_event("F", "stale-a2"), "a2", 0, True),
            E1PresentationDirective(_event("F", "fresh-a3"), "a3", 0, False),
        ),
        fault_class="bounded three-generation retry race",
    ),
)

S1_E1_SCENARIO_IDS = tuple(item.scenario_id for item in S1_E1_SCENARIOS)
_SPEC_BY_ID: Mapping[str, E1ScenarioSpec] = {item.scenario_id: item for item in S1_E1_SCENARIOS}


@dataclass(frozen=True, slots=True)
class AttemptFencingE1Trial:
    policy_id: PolicyID
    scenario_id: str
    evaluation: CorrectnessEvaluationRecord
    authoritative_outcome: AuthoritativeOutcome
    stale_result_event_ids: tuple[str, ...]
    finalization_applied_count: int
    stale_admission_decisions: tuple[PlacementDecision, ...]
    worker_process_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.scenario_id not in S1_E1_SCENARIO_IDS:
            raise ValueError("scenario_id must be a canonical S1 E1 scenario")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy_id must match trial policy_id")
        if self.evaluation.scenario_id != self.scenario_id:
            raise ValueError("evaluation scenario_id must match trial scenario_id")
        if not isinstance(self.authoritative_outcome, AuthoritativeOutcome):
            raise TypeError("authoritative_outcome must be AuthoritativeOutcome")
        if not isinstance(self.finalization_applied_count, int) or self.finalization_applied_count < 0:
            raise ValueError("finalization_applied_count must be non-negative")
        if not all(isinstance(item, PlacementDecision) for item in self.stale_admission_decisions):
            raise TypeError("stale_admission_decisions must contain PlacementDecision values")
        if not self.worker_process_ids or not all(
            isinstance(item, int) and item > 0 for item in self.worker_process_ids
        ):
            raise ValueError("worker_process_ids must contain positive process IDs")


@dataclass(frozen=True, slots=True)
class AttemptFencingE1Evaluation:
    trials: tuple[AttemptFencingE1Trial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (spec.scenario_id, policy_id)
            for spec in S1_E1_SCENARIOS
            for policy_id in PolicyID
        )
        actual = tuple((trial.scenario_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("S1 E1 trials must use canonical scenario then B0-B4 ordering")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


@dataclass(slots=True)
class _WorkerHandle:
    attempt_id: str
    process: Any
    command: Any


class _Inbox:
    def __init__(self, result_queue: Any) -> None:
        self._queue = result_queue
        self._buffer: list[dict[str, Any]] = []

    @staticmethod
    def _matches(
        message: dict[str, Any],
        *,
        kind: str,
        attempt_id: str | None,
        event_ids: set[str] | None,
    ) -> bool:
        if message.get("kind") != kind:
            return False
        if attempt_id is not None and message.get("attempt_id") != attempt_id:
            return False
        if event_ids is not None and message.get("event_id") not in event_ids:
            return False
        return True

    def wait(
        self,
        *,
        kind: str,
        attempt_id: str | None = None,
        event_ids: set[str] | None = None,
        timeout: float = S1_E1_IPC_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for index, message in enumerate(self._buffer):
                if self._matches(
                    message, kind=kind, attempt_id=attempt_id, event_ids=event_ids
                ):
                    return self._buffer.pop(index)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"timed out waiting for {kind} attempt={attempt_id} events={event_ids}"
                )
            try:
                message = self._queue.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"timed out waiting for {kind} attempt={attempt_id} events={event_ids}"
                ) from exc
            if not isinstance(message, dict):
                raise AssertionError("worker IPC message must be a mapping")
            if self._matches(message, kind=kind, attempt_id=attempt_id, event_ids=event_ids):
                return message
            self._buffer.append(message)


def _worker_main(
    scenario_id: str,
    attempt_id: str,
    work_rounds: int,
    command: Any,
    result_queue: Any,
) -> None:
    pid = os.getpid()
    cached: dict[str, Any] | None = None
    result_queue.put(
        {
            "kind": "READY",
            "scenario_id": scenario_id,
            "attempt_id": attempt_id,
            "worker_pid": pid,
            "at": time.time(),
        }
    )
    try:
        while True:
            instruction = command.recv()
            op = instruction.get("op") if isinstance(instruction, dict) else None
            if op == "STOP":
                return
            if op == "BEGIN":
                result_queue.put(
                    {
                        "kind": "STARTED",
                        "scenario_id": scenario_id,
                        "attempt_id": attempt_id,
                        "worker_pid": pid,
                        "at": time.time(),
                    }
                )
                continue
            if op == "ARM":
                result_queue.put(
                    {
                        "kind": "COMPUTE_READY",
                        "scenario_id": scenario_id,
                        "attempt_id": attempt_id,
                        "worker_pid": pid,
                        "at": time.time(),
                    }
                )
                continue
            if op == "GO":
                start = time.time()
                digest = hashlib.sha256(f"{scenario_id}:{attempt_id}".encode("utf-8")).digest()
                for index in range(work_rounds):
                    digest = hashlib.sha256(digest + index.to_bytes(8, "little")).digest()
                observed_at = time.time()
                cached = {
                    "scenario_id": scenario_id,
                    "attempt_id": attempt_id,
                    "worker_pid": pid,
                    "compute_started_at": start,
                    "observed_at": observed_at,
                    "result_token": digest.hex(),
                    "evidence_id": f"c4.2c:{scenario_id}:evidence:{attempt_id}",
                    "output_id": f"c4.2c:{scenario_id}:output:{attempt_id}",
                }
                result_queue.put(
                    {
                        "kind": "COMPLETION",
                        "event_id": f"c4.2c:{scenario_id}:physical-completion:{attempt_id}",
                        **cached,
                        "delivered_at": time.time(),
                    }
                )
                continue
            if op == "PRESENT":
                if cached is None:
                    raise RuntimeError("cannot present before physical computation completes")
                event_id = instruction.get("event_id")
                duplicate = instruction.get("duplicate", False)
                if not isinstance(event_id, str) or not event_id:
                    raise RuntimeError("presentation requires a non-empty event_id")
                if not isinstance(duplicate, bool):
                    raise RuntimeError("duplicate must be bool")
                result_queue.put(
                    {
                        "kind": "PRESENTATION",
                        "event_id": event_id,
                        **cached,
                        "duplicate": duplicate,
                        "delivered_at": time.time(),
                    }
                )
                continue
            raise RuntimeError(f"unknown worker command: {instruction!r}")
    except EOFError:
        return
    finally:
        command.close()


def _scaffold_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request("r", "c")
    return core


def _attempt_admission_observation(
    *, request_id: str, attempt_id: str, attempt_authority: str
) -> PolicyObservation:
    return PolicyObservation(
        request_id=request_id,
        workers=(
            WorkerObservation(
                worker_id="w1",
                available=True,
                capacity=1,
                active_tasks=0,
                queued_tasks=0,
            ),
        ),
        attempt_id=attempt_id,
        attempt_authority=attempt_authority,
        session_id="s",
        continuation_id="c",
        program_id="p",
    )


def _placement_to_dict(decision: PlacementDecision) -> dict[str, Any]:
    return {
        "policy_id": decision.policy_id.value,
        "worker_id": decision.worker_id,
        "ranked_worker_ids": list(decision.ranked_worker_ids),
        "reason": decision.reason,
    }


def _classify_e1_stale_acceptance(
    *,
    attempt_id: str,
    attempt_authority_before: str,
    attempt_execution_before: str,
    committed_attempt_id_before: str | None,
    committed_attempt_id_after: str | None,
    attempt_authority_after: str,
) -> bool:
    if attempt_execution_before != ExecutionStatus.SUCCEEDED.name:
        raise AssertionError("SAAR authority presentation requires delivered physical success")

    if attempt_authority_before == AttemptAuthority.SUPERSEDED.name:
        accepted = committed_attempt_id_after == attempt_id
        if accepted and attempt_authority_after != AttemptAuthority.COMMITTED.name:
            raise AssertionError("accepted stale Attempt must become COMMITTED")
        return accepted

    if (
        attempt_authority_before == AttemptAuthority.COMMITTED.name
        and committed_attempt_id_before == attempt_id
    ):
        accepted = committed_attempt_id_after == attempt_id
        if accepted and attempt_authority_after != AttemptAuthority.COMMITTED.name:
            raise AssertionError("previously accepted stale Attempt must remain COMMITTED")
        return accepted

    raise AssertionError(
        "oracle-stale presentation must be SUPERSEDED, or already COMMITTED only because a prior stale presentation was accepted"
    )


def _ensure_terminal_evidence(core: ContinuityCore, message: Mapping[str, Any]) -> str:
    evidence_id = message["evidence_id"]
    attempt_id = message["attempt_id"]
    observed_at = float(message["observed_at"])
    expected = Evidence(
        id=evidence_id,
        claim="terminal_attempt_success",
        source="c4.2c.e1-worker",
        authority=EvidenceAuthority.EXACT_OBSERVATION,
        status=EvidenceStatus.VALID,
        observed_at=observed_at,
        scope=frozenset({("attempt", attempt_id)}),
    )
    existing = core.evidence.get(evidence_id)
    if existing is not None:
        if existing != expected:
            raise SemanticViolation("conflicting terminal Evidence identity in E1 harness")
        return "IDEMPOTENT"
    core.record_evidence(expected)
    return "APPLIED"


def _ensure_terminal_output(core: ContinuityCore, message: Mapping[str, Any]) -> str:
    output_id = message["output_id"]
    attempt_id = message["attempt_id"]
    evidence_id = message["evidence_id"]
    expected = Output(
        id=output_id,
        attempt_id=attempt_id,
        terminal=True,
        evidence_ids=frozenset({evidence_id}),
    )
    existing = core.outputs.get(output_id)
    if existing is not None:
        if existing != expected:
            raise SemanticViolation("conflicting terminal Output identity in E1 harness")
        return "IDEMPOTENT"
    core.create_output(output_id, attempt_id, True, evidence_ids=[evidence_id])
    return "APPLIED"


def _apply_presentation(
    core: ContinuityCore,
    message: Mapping[str, Any],
    *,
    stale: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempt_id = message["attempt_id"]
    attempt = core.attempts[attempt_id]
    request = core.requests["r"]
    pre = {
        "event_id": message["event_id"],
        "attempt_id": attempt_id,
        "attempt_authority_before": attempt.authority_status.name,
        "attempt_execution_before": attempt.execution_status.name,
        "request_current_attempt_id_before": request.current_attempt_id,
        "request_committed_attempt_id_before": request.committed_attempt_id,
        "observed_at": float(message["observed_at"]),
        "delivered_at": float(message["delivered_at"]),
        "evidence_id": message["evidence_id"],
        "output_id": message["output_id"],
        "worker_pid": int(message["worker_pid"]),
        "duplicate": bool(message["duplicate"]),
    }
    if stale:
        already_bad_commit = (
            attempt.authority_status is AttemptAuthority.COMMITTED
            and request.committed_attempt_id == attempt_id
        )
        if (
            attempt.authority_status is not AttemptAuthority.SUPERSEDED
            and not already_bad_commit
        ):
            raise AssertionError(
                "oracle-stale E1 presentation must be SUPERSEDED unless prior stale acceptance committed it"
            )
        if attempt.execution_status is not ExecutionStatus.SUCCEEDED:
            raise AssertionError("oracle-stale E1 presentation requires SUCCEEDED execution")

    evidence_outcome = _ensure_terminal_evidence(core, message)
    output_outcome = _ensure_terminal_output(core, message)
    before_status = core.requests["r"].status
    before_output = core.requests["r"].authoritative_output_id
    error: BaseException | None = None
    try:
        core.finalize_request("r", message["output_id"], now=time.time())
    except ContinuityError as exc:
        error = exc
    InvariantOracle(core).assert_all()
    after_request = core.requests["r"]
    after_attempt = core.attempts[attempt_id]
    if error is not None:
        finalization_outcome = "REJECTED"
    elif before_status is RequestStatus.COMPLETED and before_output == message["output_id"]:
        finalization_outcome = "IDEMPOTENT"
    elif after_request.status is RequestStatus.COMPLETED and before_status is not RequestStatus.COMPLETED:
        finalization_outcome = "APPLIED"
    else:
        finalization_outcome = "IDEMPOTENT"

    accepted = False
    if stale:
        accepted = _classify_e1_stale_acceptance(
            attempt_id=attempt_id,
            attempt_authority_before=pre["attempt_authority_before"],
            attempt_execution_before=pre["attempt_execution_before"],
            committed_attempt_id_before=pre["request_committed_attempt_id_before"],
            committed_attempt_id_after=after_request.committed_attempt_id,
            attempt_authority_after=after_attempt.authority_status.name,
        )

    post = {
        "event_id": message["event_id"],
        "attempt_id": attempt_id,
        "attempt_authority_after": after_attempt.authority_status.name,
        "attempt_execution_after": after_attempt.execution_status.name,
        "request_current_attempt_id_after": after_request.current_attempt_id,
        "request_committed_attempt_id_after": after_request.committed_attempt_id,
        "evidence_outcome": evidence_outcome,
        "output_outcome": output_outcome,
        "finalization_outcome": finalization_outcome,
        "error_type": None if error is None else type(error).__name__,
        "accepted_authoritatively": accepted,
    }
    return pre, post


def _spawn_worker(
    ctx: Any,
    result_queue: Any,
    inbox: _Inbox,
    spec: E1ScenarioSpec,
    attempt_id: str,
) -> tuple[_WorkerHandle, dict[str, Any]]:
    parent_command, child_command = ctx.Pipe(duplex=True)
    process = ctx.Process(
        target=_worker_main,
        args=(spec.scenario_id, attempt_id, S1_E1_WORK_ROUNDS, child_command, result_queue),
        name=f"cadi-e1-{spec.scenario_id}-{attempt_id}",
    )
    process.start()
    child_command.close()
    ready = inbox.wait(kind="READY", attempt_id=attempt_id)
    if ready["worker_pid"] != process.pid:
        raise AssertionError("worker READY PID must match spawned process PID")
    handle = _WorkerHandle(attempt_id=attempt_id, process=process, command=parent_command)
    return handle, ready


def _begin_worker(handle: _WorkerHandle, inbox: _Inbox) -> dict[str, Any]:
    handle.command.send({"op": "BEGIN"})
    return inbox.wait(kind="STARTED", attempt_id=handle.attempt_id)


def _compute_batch(
    handles: Mapping[str, _WorkerHandle],
    inbox: _Inbox,
    attempt_ids: tuple[str, ...],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    for attempt_id in attempt_ids:
        handles[attempt_id].command.send({"op": "ARM"})
    ready = tuple(
        inbox.wait(kind="COMPUTE_READY", attempt_id=attempt_id)
        for attempt_id in attempt_ids
    )
    for attempt_id in attempt_ids:
        handles[attempt_id].command.send({"op": "GO"})

    remaining = set(attempt_ids)
    completions: list[dict[str, Any]] = []
    while remaining:
        message = inbox.wait(kind="COMPLETION", event_ids=None)
        attempt_id = message.get("attempt_id")
        if attempt_id not in remaining:
            raise AssertionError("unexpected or duplicate physical completion")
        completions.append(message)
        remaining.remove(attempt_id)
    return ready, tuple(completions)


def _apply_completion(
    core: ContinuityCore,
    policy: Any,
    message: Mapping[str, Any],
) -> tuple[dict[str, Any], PlacementDecision | None]:
    attempt_id = message["attempt_id"]
    attempt = core.attempts[attempt_id]
    authority_before = attempt.authority_status.name
    execution_before = attempt.execution_status.name
    core.complete_attempt(attempt_id, succeeded=True)
    InvariantOracle(core).assert_all()
    attempt_after = core.attempts[attempt_id]
    request = core.requests[attempt_after.request_id]
    stale = attempt_after.authority_status is AttemptAuthority.SUPERSEDED
    decision = None
    if stale:
        decision = decide_placement(
            policy,
            _attempt_admission_observation(
                request_id=attempt_after.request_id,
                attempt_id=attempt_id,
                attempt_authority=attempt_after.authority_status.name,
            ),
        )
    check = {
        "event_id": message["event_id"],
        "attempt_id": attempt_id,
        "worker_pid": int(message["worker_pid"]),
        "compute_started_at": float(message["compute_started_at"]),
        "observed_at": float(message["observed_at"]),
        "delivered_at": float(message["delivered_at"]),
        "result_token": message["result_token"],
        "attempt_authority_before": authority_before,
        "attempt_execution_before": execution_before,
        "attempt_authority_after": attempt_after.authority_status.name,
        "attempt_execution_after": attempt_after.execution_status.name,
        "request_current_attempt_id": request.current_attempt_id,
        "stale_at_delivery": stale,
    }
    return check, decision


def _run_e1_trace(
    policy_id: PolicyID,
    spec: E1ScenarioSpec,
) -> tuple[
    ContinuityCore,
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[PlacementDecision, ...],
    tuple[int, ...],
    tuple[dict[str, Any], ...],
]:
    ctx = mp.get_context(S1_E1_START_METHOD)
    result_queue = ctx.Queue()
    inbox = _Inbox(result_queue)
    core = _scaffold_core()
    policy = build_baseline_policies(CoreContinuityAuthority(core))[policy_id]
    handles: dict[str, _WorkerHandle] = {}
    worker_lifecycle: list[dict[str, Any]] = []
    completion_checks: list[dict[str, Any]] = []
    presentation_preconditions: list[dict[str, Any]] = []
    presentation_results: list[dict[str, Any]] = []
    stale_admission_decisions: list[PlacementDecision] = []
    compute_batches: list[dict[str, Any]] = []

    try:
        first = spec.attempt_ids[0]
        core.start_attempt(first, "r")
        core.set_attempt_execution(first, ExecutionStatus.RUNNING)
        handle, ready = _spawn_worker(ctx, result_queue, inbox, spec, first)
        handles[first] = handle
        started = _begin_worker(handle, inbox)
        worker_lifecycle.extend((ready, started))

        if spec.mode is E1ScenarioMode.PRETIMEOUT_SUCCESS_DELAYED_OBSERVATION:
            batch_ready, completions = _compute_batch(handles, inbox, (first,))
            compute_batches.append(
                {
                    "attempt_ids": [first],
                    "compute_ready": list(batch_ready),
                }
            )
            if len(completions) != 1:
                raise AssertionError("pretimeout success batch must produce one completion")
            check, decision = _apply_completion(core, policy, completions[0])
            if check["attempt_authority_after"] != AttemptAuthority.CURRENT.name:
                raise AssertionError("pretimeout physical success must occur while A1 is CURRENT")
            completion_checks.append(check)
            if decision is not None:
                stale_admission_decisions.append(decision)

        for attempt_id in spec.attempt_ids[1:]:
            superseded_id = core.requests["r"].current_attempt_id
            if superseded_id is None:
                raise AssertionError("retry start requires a current Attempt")
            superseded = core.attempts[superseded_id]
            superseded_execution_before = superseded.execution_status.name
            core.start_attempt(attempt_id, "r")
            core.set_attempt_execution(attempt_id, ExecutionStatus.RUNNING)
            InvariantOracle(core).assert_all()
            if core.attempts[superseded_id].authority_status is not AttemptAuthority.SUPERSEDED:
                raise AssertionError("retry must supersede the prior Attempt")
            handle, ready = _spawn_worker(ctx, result_queue, inbox, spec, attempt_id)
            handles[attempt_id] = handle
            started = _begin_worker(handle, inbox)
            worker_lifecycle.extend((ready, started))
            worker_lifecycle.append(
                {
                    "kind": "SUPERSESSION",
                    "scenario_id": spec.scenario_id,
                    "superseded_attempt_id": superseded_id,
                    "retry_attempt_id": attempt_id,
                    "superseded_execution_before": superseded_execution_before,
                    "superseded_authority_after": core.attempts[superseded_id].authority_status.name,
                    "retry_authority_after": core.attempts[attempt_id].authority_status.name,
                    "request_current_attempt_id": core.requests["r"].current_attempt_id,
                    "at": time.time(),
                }
            )

        if spec.mode is E1ScenarioMode.PRETIMEOUT_SUCCESS_DELAYED_OBSERVATION:
            unfinished = spec.attempt_ids[1:]
        else:
            unfinished = spec.attempt_ids
        if unfinished:
            batch_ready, completions = _compute_batch(handles, inbox, unfinished)
            compute_batches.append(
                {
                    "attempt_ids": list(unfinished),
                    "compute_ready": list(batch_ready),
                }
            )
            for completion in completions:
                check, decision = _apply_completion(core, policy, completion)
                completion_checks.append(check)
                if decision is not None:
                    stale_admission_decisions.append(decision)

        directive_by_event = {item.event_id: item for item in spec.presentations}
        for group in spec.presentation_groups:
            remaining = {item.event_id for item in group}
            for item in group:
                handles[item.attempt_id].command.send(
                    {
                        "op": "PRESENT",
                        "event_id": item.event_id,
                        "duplicate": item.duplicate,
                    }
                )
            while remaining:
                message = inbox.wait(kind="PRESENTATION", event_ids=remaining)
                event_id = message["event_id"]
                directive = directive_by_event[event_id]
                pre, post = _apply_presentation(core, message, stale=directive.stale)
                presentation_preconditions.append(pre)
                presentation_results.append(post)
                remaining.remove(event_id)

        return (
            core,
            tuple(worker_lifecycle),
            tuple(completion_checks),
            tuple(presentation_preconditions),
            tuple(presentation_results),
            tuple(stale_admission_decisions),
            tuple(sorted(int(handle.process.pid) for handle in handles.values())),
            tuple(compute_batches),
        )
    finally:
        for handle in handles.values():
            try:
                if handle.process.is_alive():
                    handle.command.send({"op": "STOP"})
            except (BrokenPipeError, EOFError, OSError):
                pass
        for handle in handles.values():
            handle.process.join(timeout=2.0)
            if handle.process.is_alive():
                handle.process.terminate()
                handle.process.join(timeout=2.0)
            handle.command.close()
        result_queue.close()
        result_queue.join_thread()


def _semantic_identity_for(spec: E1ScenarioSpec, attempt_id: str) -> dict[str, str]:
    return {
        "evidence_id": f"c4.2c:{spec.scenario_id}:evidence:{attempt_id}",
        "output_id": f"c4.2c:{spec.scenario_id}:output:{attempt_id}",
    }


def run_s1_e1_trial(policy_id: PolicyID, scenario_id: str) -> AttemptFencingE1Trial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    spec = _SPEC_BY_ID.get(scenario_id)
    if spec is None:
        raise ValueError(f"scenario_id must be one of {S1_E1_SCENARIO_IDS!r}")

    (
        core,
        worker_lifecycle,
        completion_checks,
        presentation_preconditions,
        presentation_results,
        stale_admission_decisions,
        worker_process_ids,
        compute_batches,
    ) = _run_e1_trace(policy_id, spec)

    outcome = authoritative_outcome(core, "r")
    finalization_applied_count = sum(
        item["finalization_outcome"] == "APPLIED" for item in presentation_results
    )
    accepted_stale_ids = tuple(
        item["event_id"]
        for item in presentation_results
        if item["event_id"] in set(spec.stale_presentation_event_ids)
        and item["accepted_authoritatively"]
    )

    opportunities: list[CorrectnessMetric] = []
    opportunity_event_ids: list[str] = []
    opportunity_scopes: list[MetricOpportunityScope] = []
    violations: list[CorrectnessMetric] = []
    violation_event_ids: list[str] = []

    for event_id in spec.stale_presentation_event_ids:
        opportunities.append(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
        opportunity_event_ids.append(event_id)
        opportunity_scopes.append(MetricOpportunityScope.EXOGENOUS_PAIRED)
        if event_id in accepted_stale_ids:
            violations.append(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
            violation_event_ids.append(event_id)

    if core.requests["r"].status is RequestStatus.COMPLETED:
        completed_id = "completed-request:r"
        opportunities.append(CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
        opportunity_event_ids.append(completed_id)
        opportunity_scopes.append(MetricOpportunityScope.POLICY_DERIVED)
        if finalization_applied_count > 1:
            violations.append(CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
            violation_event_ids.append(completed_id)

    semantic_correct = outcome.committed_attempt_id == spec.expected_committed_attempt_id
    if core.requests["r"].status is RequestStatus.COMPLETED:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=semantic_correct,
            recovery_actions=(RecoveryAction.RETRY,),
        )
    else:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.FAIL,
            recovery_actions=(RecoveryAction.RETRY,),
        )

    presentation_ground_truth = []
    for item in spec.presentations:
        identity = _semantic_identity_for(spec, item.attempt_id)
        presentation_ground_truth.append(
            {
                "event_id": item.event_id,
                "attempt_id": item.attempt_id,
                "group": item.group,
                "stale": item.stale,
                "duplicate": item.duplicate,
                **identity,
            }
        )

    ground_truth = {
        "schema": S1_E1_SCHEMA,
        "scenario_id": spec.scenario_id,
        "mode": spec.mode.value,
        "request_id": "r",
        "attempt_ids": list(spec.attempt_ids),
        "expected_committed_attempt_id": spec.expected_committed_attempt_id,
        "stale_presentation_event_ids": list(spec.stale_presentation_event_ids),
        "presentations": presentation_ground_truth,
        "worker_process_count": len(spec.attempt_ids),
        "start_method": S1_E1_START_METHOD,
        "ipc_transport": "multiprocessing.Pipe+Queue",
        "cpu_work": {"algorithm": "SHA-256", "rounds": S1_E1_WORK_ROUNDS},
        "duplicate_semantics": "same attempt/evidence/output/observed_at; distinct delivery EventID",
        "semantic_authority": "C1_COMMON_TO_B0_B4",
    }

    finalization_records = [
        {
            "event_id": item["event_id"],
            "attempt_id": item["attempt_id"],
            "outcome": item["finalization_outcome"],
            "error_type": item["error_type"],
        }
        for item in presentation_results
    ]
    observed_evidence = {
        "coordinator_pid": os.getpid(),
        "worker_process_ids": list(worker_process_ids),
        "worker_lifecycle": list(worker_lifecycle),
        "compute_batches": list(compute_batches),
        "physical_completion_checks": list(completion_checks),
        "terminal_presentation_preconditions": list(presentation_preconditions),
        "terminal_presentations": list(presentation_results),
        "presentation_delivery_order": [item["event_id"] for item in presentation_results],
        "finalization_records": finalization_records,
        "authoritative_outcome": {
            "request_status": outcome.request_status,
            "current_attempt_id": outcome.current_attempt_id,
            "committed_attempt_id": outcome.committed_attempt_id,
            "authoritative_output_id": outcome.authoritative_output_id,
        },
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "stale_attempt_admission_probe_is_gate_metric": False,
        "stale_attempt_admission_decisions": [
            _placement_to_dict(item) for item in stale_admission_decisions
        ],
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S1_E1_COHORT_ID,
        trial_id=spec.scenario_id,
        operation_id="r",
        policy_id=policy_id,
        scenario_id=spec.scenario_id,
        validation_level=ValidationEvidenceLevel.EV1_MEASURED_CPU_DISTRIBUTED,
        evidence_provenance=ResultEvidenceProvenance.MEASURED,
        ground_truth=ground_truth,
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=semantic_result,
        metric_opportunities=tuple(opportunities),
        metric_opportunity_event_ids=tuple(opportunity_event_ids),
        metric_opportunity_scopes=tuple(opportunity_scopes),
        metric_violations=tuple(violations),
        metric_violation_event_ids=tuple(violation_event_ids),
        fault_id=f"S1:E1:{spec.scenario_id}",
        fault_class=spec.fault_class,
    )

    return AttemptFencingE1Trial(
        policy_id=policy_id,
        scenario_id=spec.scenario_id,
        evaluation=evaluation,
        authoritative_outcome=outcome,
        stale_result_event_ids=spec.stale_presentation_event_ids,
        finalization_applied_count=finalization_applied_count,
        stale_admission_decisions=stale_admission_decisions,
        worker_process_ids=worker_process_ids,
    )


def run_s1_e1_paired() -> AttemptFencingE1Evaluation:
    trials = tuple(
        run_s1_e1_trial(policy_id, spec.scenario_id)
        for spec in S1_E1_SCENARIOS
        for policy_id in PolicyID
    )
    summary = summarize_correctness(tuple(item.evaluation for item in trials))
    return AttemptFencingE1Evaluation(trials=trials, summary=summary)
