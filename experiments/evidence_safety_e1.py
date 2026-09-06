from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import multiprocessing as mp
import os
import time
from typing import Any, Iterable, Mapping

from continuity.core import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ReconcileOutcome,
    RequestStatus,
)
from continuity.errors import ContinuityError
from simulator import PolicyID, PolicyObservation, project_observation
from simulator.policies import InformationField

from .correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    CorrectnessSummary,
    ExplicitNonSuccess,
    MetricOpportunityScope,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)
from .evidence_safety import (
    S4_E0_ATTEMPT_ID,
    S4_E0_OUTPUT_ID,
    S4_E0_REQUEST_ID,
    EvidenceFinalizePresentation,
    _scaffold_core,
)


S4_E1_SCHEMA = "cadi.c4.5c.evidence-safety-e1.v1"
S4_E1_COHORT_ID = "C4.5c:S4:EV1"
S4_E1_START_METHOD = "spawn"
S4_E1_WORKER_IDS = ("w1", "w2", "w3")
S4_E1_FIXED_WORK_ROUNDS = 5_000
S4_E1_MIN_CPU_SECONDS = 0.004
S4_E1_IPC_TIMEOUT_SECONDS = 10.0
S4_E1_CRASH_EXIT_CODE = 19


class EvidenceE1Mode(str, Enum):
    CONTRADICTORY_WORKERS = "CONTRADICTORY_WORKERS"
    EXPLICIT_AMBIGUOUS_WORKER = "EXPLICIT_AMBIGUOUS_WORKER"
    EXPIRED_MEASURED_OBSERVATION = "EXPIRED_MEASURED_OBSERVATION"
    WORKER_CRASH_NO_EVIDENCE = "WORKER_CRASH_NO_EVIDENCE"
    WRONG_SCOPE_WORKER = "WRONG_SCOPE_WORKER"
    AGREEING_WORKERS_CONTROL = "AGREEING_WORKERS_CONTROL"
    SINGLE_VALID_WORKER_CONTROL = "SINGLE_VALID_WORKER_CONTROL"


@dataclass(frozen=True, slots=True)
class EvidenceE1Scenario:
    scenario_id: str
    mode: EvidenceE1Mode
    oracle_reconciliation: ReconcileOutcome
    semantic_commit_allowed: bool
    fault_class: str | None
    acr_event_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise ValueError("scenario_id must be a non-empty string")
        if not isinstance(self.mode, EvidenceE1Mode):
            raise TypeError("mode must be EvidenceE1Mode")
        if not isinstance(self.oracle_reconciliation, ReconcileOutcome):
            raise TypeError("oracle_reconciliation must be ReconcileOutcome")
        if not isinstance(self.semantic_commit_allowed, bool):
            raise TypeError("semantic_commit_allowed must be bool")
        if self.fault_class is not None and (
            not isinstance(self.fault_class, str) or not self.fault_class
        ):
            raise ValueError("fault_class must be a non-empty string or None")
        if self.acr_event_id is not None and (
            not isinstance(self.acr_event_id, str) or not self.acr_event_id
        ):
            raise ValueError("acr_event_id must be a non-empty string or None")
        if (self.fault_class is None) != (self.acr_event_id is None):
            raise ValueError("faulted EV1 scenarios require exactly one ACR EventID")
        if self.semantic_commit_allowed and self.fault_class is not None:
            raise ValueError("faulted EV1 scenarios cannot allow semantic commit")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S4:EV1:{self.scenario_id}"


S4_E1_SCENARIOS = (
    EvidenceE1Scenario(
        "E1-S4-A-CONTRADICTORY-WORKERS",
        EvidenceE1Mode.CONTRADICTORY_WORKERS,
        ReconcileOutcome.AMBIGUOUS,
        False,
        "independent VALID exact workers assert mutually exclusive terminal outcomes",
        "S4:EV1:A:contradictory-workers-finalization",
    ),
    EvidenceE1Scenario(
        "E1-S4-B-EXPLICIT-AMBIGUOUS-WORKER",
        EvidenceE1Mode.EXPLICIT_AMBIGUOUS_WORKER,
        ReconcileOutcome.AMBIGUOUS,
        False,
        "real worker reports explicit AMBIGUOUS terminal-outcome Evidence",
        "S4:EV1:B:ambiguous-worker-finalization",
    ),
    EvidenceE1Scenario(
        "E1-S4-C-EXPIRED-MEASURED-OBSERVATION",
        EvidenceE1Mode.EXPIRED_MEASURED_OBSERVATION,
        ReconcileOutcome.WAIT,
        False,
        "measured VALID exact observation is expired at semantic commit",
        "S4:EV1:C:expired-worker-finalization",
    ),
    EvidenceE1Scenario(
        "E1-S4-D-WORKER-CRASH-NO-EVIDENCE",
        EvidenceE1Mode.WORKER_CRASH_NO_EVIDENCE,
        ReconcileOutcome.WAIT,
        False,
        "worker exits after measured CPU work without delivering usable Evidence",
        "S4:EV1:D:worker-crash-finalization",
    ),
    EvidenceE1Scenario(
        "E1-S4-E-WRONG-SCOPE-WORKER",
        EvidenceE1Mode.WRONG_SCOPE_WORKER,
        ReconcileOutcome.WAIT,
        False,
        "real worker exact Evidence is scoped to another Attempt",
        "S4:EV1:E:wrong-scope-worker-finalization",
    ),
    EvidenceE1Scenario(
        "E1-S4-F-AGREEING-WORKERS-CONTROL",
        EvidenceE1Mode.AGREEING_WORKERS_CONTROL,
        ReconcileOutcome.MATCHED,
        True,
        None,
        None,
    ),
    EvidenceE1Scenario(
        "E1-S4-G-SINGLE-VALID-WORKER-CONTROL",
        EvidenceE1Mode.SINGLE_VALID_WORKER_CONTROL,
        ReconcileOutcome.MATCHED,
        True,
        None,
        None,
    ),
)
S4_E1_SCENARIO_IDS = tuple(item.scenario_id for item in S4_E1_SCENARIOS)
_SPEC_BY_ID: Mapping[str, EvidenceE1Scenario] = {
    item.scenario_id: item for item in S4_E1_SCENARIOS
}


@dataclass(slots=True)
class _WorkerHandle:
    worker_id: str
    process: Any
    connection: Any


@dataclass(frozen=True, slots=True)
class _MeasuredScenario:
    scenario_id: str
    physical_events: tuple[Mapping[str, Any], ...]
    observations: tuple[Mapping[str, Any], ...]
    worker_process_ids: tuple[int, ...]
    commit_now: float

    def __post_init__(self) -> None:
        if self.scenario_id not in S4_E1_SCENARIO_IDS:
            raise ValueError("measured scenario must be canonical")
        if len(self.worker_process_ids) != 3 or len(set(self.worker_process_ids)) != 3:
            raise ValueError("S4 EV1 requires three distinct worker process IDs")
        if not all(isinstance(item, int) and item > 0 for item in self.worker_process_ids):
            raise ValueError("worker_process_ids must be positive integers")
        if not isinstance(self.commit_now, float) or self.commit_now <= 0.0:
            raise ValueError("commit_now must be a positive float")


@dataclass(frozen=True, slots=True)
class EvidenceE1Trial:
    policy_id: PolicyID
    scenario_id: str
    evaluation: CorrectnessEvaluationRecord
    presentation: EvidenceFinalizePresentation
    evidence_ids: tuple[str, ...]
    observed_reconciliation: ReconcileOutcome
    reconciliation_diverged_from_oracle: bool
    physical_events: tuple[Mapping[str, Any], ...]
    worker_process_ids: tuple[int, ...]
    policy_visible_evidence: Mapping[str, Any]
    injected_divergence: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.scenario_id not in S4_E1_SCENARIO_IDS:
            raise ValueError("scenario_id must be canonical S4 EV1 scenario")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy must match trial policy")
        if self.evaluation.scenario_id != self.scenario_id:
            raise ValueError("evaluation scenario must match trial scenario")
        if not isinstance(self.presentation, EvidenceFinalizePresentation):
            raise TypeError("presentation must be EvidenceFinalizePresentation")
        if not isinstance(self.observed_reconciliation, ReconcileOutcome):
            raise TypeError("observed_reconciliation must be ReconcileOutcome")
        spec = _SPEC_BY_ID[self.scenario_id]
        expected_divergence = self.observed_reconciliation is not spec.oracle_reconciliation
        if self.reconciliation_diverged_from_oracle != expected_divergence:
            raise ValueError("reconciliation divergence flag is inconsistent")
        if len(self.worker_process_ids) != 3 or len(set(self.worker_process_ids)) != 3:
            raise ValueError("trial must retain three distinct worker PIDs")
        if not isinstance(self.injected_divergence, bool):
            raise TypeError("injected_divergence must be bool")


@dataclass(frozen=True, slots=True)
class EvidenceE1Evaluation:
    trials: tuple[EvidenceE1Trial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (scenario.scenario_id, policy_id)
            for scenario in S4_E1_SCENARIOS
            for policy_id in PolicyID
        )
        actual = tuple((trial.scenario_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("S4 EV1 trials must use canonical scenario then B0-B4 order")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _cpu_digest(
    seed: str, minimum_cpu_seconds: float = S4_E1_MIN_CPU_SECONDS
) -> tuple[str, float, float]:
    started_wall = time.monotonic()
    started_cpu = time.process_time()
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    for index in range(S4_E1_FIXED_WORK_ROUNDS):
        digest = hashlib.sha256(digest + index.to_bytes(8, "little")).digest()
    index = S4_E1_FIXED_WORK_ROUNDS
    while time.process_time() - started_cpu < minimum_cpu_seconds:
        digest = hashlib.sha256(digest + index.to_bytes(8, "little")).digest()
        index += 1
    return (
        digest.hex(),
        time.process_time() - started_cpu,
        time.monotonic() - started_wall,
    )


def _worker_main(worker_id: str, connection: Any) -> None:
    connection.send(
        {
            "kind": "READY",
            "worker_id": worker_id,
            "worker_pid": os.getpid(),
            "at": time.time(),
        }
    )
    try:
        while True:
            command = connection.recv()
            if not isinstance(command, dict):
                raise RuntimeError("worker command must be a mapping")
            op = command.get("op")
            if op == "STOP":
                return
            if op == "OBSERVE":
                event_id = command.get("event_id")
                semantic_key = command.get("semantic_key")
                semantic_value = command.get("semantic_value")
                evidence_status = command.get("evidence_status")
                scope_attempt_id = command.get("scope_attempt_id")
                if not isinstance(event_id, str) or not event_id:
                    raise RuntimeError("OBSERVE requires event_id")
                if not isinstance(semantic_key, str) or not semantic_key:
                    raise RuntimeError("OBSERVE requires semantic_key")
                if not isinstance(semantic_value, str) or not semantic_value:
                    raise RuntimeError("OBSERVE requires semantic_value")
                if evidence_status not in {"VALID", "AMBIGUOUS"}:
                    raise RuntimeError("OBSERVE evidence_status must be VALID or AMBIGUOUS")
                if not isinstance(scope_attempt_id, str) or not scope_attempt_id:
                    raise RuntimeError("OBSERVE requires scope_attempt_id")
                started_at = time.time()
                digest, cpu_seconds, wall_seconds = _cpu_digest(
                    f"S4-EV1:OBSERVE:{worker_id}:{event_id}:{semantic_key}:{semantic_value}"
                )
                observed_at = time.time()
                connection.send(
                    {
                        "kind": "OBSERVATION",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "event_id": event_id,
                        "semantic_key": semantic_key,
                        "semantic_value": semantic_value,
                        "evidence_status": evidence_status,
                        "scope_attempt_id": scope_attempt_id,
                        "started_at": started_at,
                        "observed_at": observed_at,
                        "completed_at": observed_at,
                        "process_cpu_seconds": cpu_seconds,
                        "wall_execution_seconds": wall_seconds,
                        "cpu_digest": digest,
                    }
                )
                continue
            if op == "CRASH_AFTER_CPU":
                event_id = command.get("event_id")
                if not isinstance(event_id, str) or not event_id:
                    raise RuntimeError("CRASH_AFTER_CPU requires event_id")
                started_at = time.time()
                digest, cpu_seconds, wall_seconds = _cpu_digest(
                    f"S4-EV1:CRASH:{worker_id}:{event_id}"
                )
                completed_at = time.time()
                connection.send(
                    {
                        "kind": "CRASHING",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "event_id": event_id,
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "process_cpu_seconds": cpu_seconds,
                        "wall_execution_seconds": wall_seconds,
                        "cpu_digest": digest,
                    }
                )
                connection.close()
                os._exit(S4_E1_CRASH_EXIT_CODE)
            raise RuntimeError(f"unknown worker command: {command!r}")
    except EOFError:
        return
    finally:
        try:
            connection.close()
        except OSError:
            pass


def _recv(handle: _WorkerHandle, *, expected_kind: str) -> dict[str, Any]:
    if not handle.connection.poll(S4_E1_IPC_TIMEOUT_SECONDS):
        raise TimeoutError(f"timed out waiting for {expected_kind} from {handle.worker_id}")
    message = handle.connection.recv()
    if not isinstance(message, dict):
        raise AssertionError("worker response must be a mapping")
    if message.get("kind") != expected_kind:
        raise AssertionError(
            f"expected {expected_kind} from {handle.worker_id}, got {message.get('kind')!r}"
        )
    if message.get("worker_id") != handle.worker_id:
        raise AssertionError("worker identity mismatch")
    if int(message.get("worker_pid", -1)) != int(handle.process.pid):
        raise AssertionError("worker PID mismatch")
    message["delivered_at"] = time.time()
    return message


def _spawn_one(ctx: Any, worker_id: str) -> tuple[_WorkerHandle, dict[str, Any]]:
    parent, child = ctx.Pipe(duplex=True)
    process = ctx.Process(
        target=_worker_main,
        args=(worker_id, child),
        name=f"cadi-s4-e1-{worker_id}",
    )
    process.start()
    child.close()
    handle = _WorkerHandle(worker_id, process, parent)
    ready = _recv(handle, expected_kind="READY")
    return handle, ready


def _spawn_workers() -> tuple[Any, dict[str, _WorkerHandle], tuple[dict[str, Any], ...]]:
    ctx = mp.get_context(S4_E1_START_METHOD)
    handles: dict[str, _WorkerHandle] = {}
    ready_events: list[dict[str, Any]] = []
    for worker_id in S4_E1_WORKER_IDS:
        handle, ready = _spawn_one(ctx, worker_id)
        handles[worker_id] = handle
        ready_events.append(ready)
    return ctx, handles, tuple(ready_events)


def _ensure_workers(
    ctx: Any, handles: dict[str, _WorkerHandle]
) -> tuple[dict[str, Any], ...]:
    ready_events: list[dict[str, Any]] = []
    for worker_id in S4_E1_WORKER_IDS:
        handle = handles.get(worker_id)
        if handle is not None and handle.process.is_alive():
            continue
        if handle is not None:
            try:
                handle.connection.close()
            except OSError:
                pass
        replacement, ready = _spawn_one(ctx, worker_id)
        handles[worker_id] = replacement
        ready_events.append(ready)
    return tuple(ready_events)


def _stop_workers(handles: Mapping[str, _WorkerHandle]) -> None:
    for handle in handles.values():
        try:
            if handle.process.is_alive():
                handle.connection.send({"op": "STOP"})
        except (BrokenPipeError, EOFError, OSError):
            pass
    for handle in handles.values():
        handle.process.join(timeout=2.0)
        if handle.process.is_alive():
            handle.process.terminate()
            handle.process.join(timeout=2.0)
        try:
            handle.connection.close()
        except OSError:
            pass


def _observe(
    handle: _WorkerHandle,
    *,
    event_id: str,
    semantic_value: str,
    evidence_status: str = "VALID",
    scope_attempt_id: str = S4_E0_ATTEMPT_ID,
) -> dict[str, Any]:
    semantic_key = f"attempt:{scope_attempt_id}:terminal-outcome"
    handle.connection.send(
        {
            "op": "OBSERVE",
            "event_id": event_id,
            "semantic_key": semantic_key,
            "semantic_value": semantic_value,
            "evidence_status": evidence_status,
            "scope_attempt_id": scope_attempt_id,
        }
    )
    result = _recv(handle, expected_kind="OBSERVATION")
    if float(result["process_cpu_seconds"]) < S4_E1_MIN_CPU_SECONDS:
        raise AssertionError("worker observation did not satisfy process-CPU floor")
    return result


def _crash_after_cpu(
    handle: _WorkerHandle, *, event_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    handle.connection.send({"op": "CRASH_AFTER_CPU", "event_id": event_id})
    crash = _recv(handle, expected_kind="CRASHING")
    if float(crash["process_cpu_seconds"]) < S4_E1_MIN_CPU_SECONDS:
        raise AssertionError("worker crash work did not satisfy process-CPU floor")
    handle.process.join(timeout=2.0)
    if handle.process.is_alive():
        raise AssertionError("controlled worker crash did not exit")
    if handle.process.exitcode != S4_E1_CRASH_EXIT_CODE:
        raise AssertionError("controlled worker crash returned unexpected exit code")
    exit_event = {
        "kind": "PROCESS_EXIT",
        "worker_id": handle.worker_id,
        "worker_pid": int(handle.process.pid),
        "exit_code": int(handle.process.exitcode),
        "at": time.time(),
    }
    return crash, exit_event


def _measure_scenario(
    handles: Mapping[str, _WorkerHandle],
    scenario: EvidenceE1Scenario,
    *,
    prefix_events: Iterable[Mapping[str, Any]] = (),
) -> _MeasuredScenario:
    physical_events: list[Mapping[str, Any]] = list(prefix_events)
    observations: list[Mapping[str, Any]] = []
    event_prefix = f"S4:EV1:{scenario.scenario_id}"
    mode = scenario.mode

    if mode is EvidenceE1Mode.CONTRADICTORY_WORKERS:
        observations.extend(
            (
                _observe(
                    handles["w1"],
                    event_id=f"{event_prefix}:w1-success",
                    semantic_value="SUCCEEDED",
                ),
                _observe(
                    handles["w2"],
                    event_id=f"{event_prefix}:w2-failure",
                    semantic_value="FAILED",
                ),
            )
        )
    elif mode is EvidenceE1Mode.EXPLICIT_AMBIGUOUS_WORKER:
        observations.append(
            _observe(
                handles["w1"],
                event_id=f"{event_prefix}:w1-ambiguous",
                semantic_value="UNKNOWN",
                evidence_status="AMBIGUOUS",
            )
        )
    elif mode is EvidenceE1Mode.EXPIRED_MEASURED_OBSERVATION:
        observations.append(
            _observe(
                handles["w1"],
                event_id=f"{event_prefix}:w1-expiring",
                semantic_value="SUCCEEDED",
            )
        )
    elif mode is EvidenceE1Mode.WORKER_CRASH_NO_EVIDENCE:
        crash, exit_event = _crash_after_cpu(
            handles["w2"], event_id=f"{event_prefix}:w2-crash"
        )
        physical_events.extend((crash, exit_event))
    elif mode is EvidenceE1Mode.WRONG_SCOPE_WORKER:
        observations.append(
            _observe(
                handles["w1"],
                event_id=f"{event_prefix}:w1-wrong-scope",
                semantic_value="SUCCEEDED",
                scope_attempt_id="other-attempt",
            )
        )
    elif mode is EvidenceE1Mode.AGREEING_WORKERS_CONTROL:
        observations.extend(
            (
                _observe(
                    handles["w1"],
                    event_id=f"{event_prefix}:w1-success",
                    semantic_value="SUCCEEDED",
                ),
                _observe(
                    handles["w2"],
                    event_id=f"{event_prefix}:w2-success",
                    semantic_value="SUCCEEDED",
                ),
            )
        )
    elif mode is EvidenceE1Mode.SINGLE_VALID_WORKER_CONTROL:
        observations.append(
            _observe(
                handles["w3"],
                event_id=f"{event_prefix}:w3-success",
                semantic_value="SUCCEEDED",
            )
        )
    else:
        raise AssertionError("unhandled S4 EV1 scenario")

    physical_events.extend(observations)
    process_ids = tuple(int(handles[item].process.pid) for item in S4_E1_WORKER_IDS)
    commit_now = max(
        [time.time()]
        + [
            float(item.get("delivered_at", item.get("completed_at", 0.0)))
            for item in physical_events
        ]
    )
    return _MeasuredScenario(
        scenario_id=scenario.scenario_id,
        physical_events=tuple(physical_events),
        observations=tuple(observations),
        worker_process_ids=process_ids,
        commit_now=float(commit_now),
    )


def _record_measured_evidence(
    core: ContinuityCore,
    scenario: EvidenceE1Scenario,
    measured: _MeasuredScenario,
) -> tuple[str, ...]:
    evidence_ids: list[str] = []
    for index, event in enumerate(measured.observations, start=1):
        status = EvidenceStatus[str(event["evidence_status"])]
        valid_until: float | None = None
        if scenario.mode is EvidenceE1Mode.EXPIRED_MEASURED_OBSERVATION:
            valid_until = float(event["observed_at"]) - 1e-6
        evidence = Evidence(
            id=f"S4:EV1:{scenario.scenario_id}:evidence:{index}",
            claim=f"measured terminal outcome={event['semantic_value']}",
            source="C4.5c real worker process",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=status,
            observed_at=float(event["observed_at"]),
            scope=frozenset(
                {
                    ("attempt", str(event["scope_attempt_id"])),
                    ("worker", str(event["worker_id"])),
                    ("worker_pid", str(event["worker_pid"])),
                }
            ),
            valid_until=valid_until,
            claim_key=str(event["semantic_key"]),
            claim_value=str(event["semantic_value"]),
        )
        core.record_evidence(evidence)
        evidence_ids.append(evidence.id)
    return tuple(evidence_ids)


def _evidence_dict(evidence: Evidence) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "claim": evidence.claim,
        "source": evidence.source,
        "authority": evidence.authority.name,
        "status": evidence.status.name,
        "observed_at": evidence.observed_at,
        "scope": [list(item) for item in sorted(evidence.scope)],
        "valid_until": evidence.valid_until,
        "confidence": evidence.confidence,
        "derived_from": sorted(evidence.derived_from),
        "derivation_rule": evidence.derivation_rule,
        "claim_key": evidence.claim_key,
        "claim_value": evidence.claim_value,
    }


def _policy_visible_projection(
    core: ContinuityCore,
    policy_id: PolicyID,
    evidence_ids: tuple[str, ...],
    reconciliation: ReconcileOutcome,
    *,
    now: float,
) -> dict[str, Any]:
    evidence = tuple(core.evidence[item] for item in evidence_ids)
    authority = max((item.authority for item in evidence), default=None)
    if any(item.status is EvidenceStatus.AMBIGUOUS for item in evidence):
        status = EvidenceStatus.AMBIGUOUS
    else:
        status = evidence[0].status if evidence else None
    latest_observed = max((item.observed_at for item in evidence), default=now)
    observation = PolicyObservation(
        request_id=S4_E0_REQUEST_ID,
        workers=(),
        attempt_id=S4_E0_ATTEMPT_ID,
        attempt_authority=AttemptAuthority.CURRENT.name,
        evidence_authority=None if authority is None else authority.name,
        evidence_status=None if status is None else status.name,
        evidence_freshness=max(0.0, now - latest_observed),
        reconciliation=reconciliation.name,
    )
    view = project_observation(observation, policy_id)
    relevant = (
        InformationField.EVIDENCE_AUTHORITY,
        InformationField.EVIDENCE_STATUS,
        InformationField.EVIDENCE_FRESHNESS,
        InformationField.RECONCILIATION,
    )
    return {
        "available_fields": sorted(field.value for field in view.available_fields),
        "evidence_fields": {
            field.value: view.value(field)
            for field in relevant
            if field in view.available_fields
        },
    }


def _attempt_finalize_measured(
    core: ContinuityCore,
    scenario: EvidenceE1Scenario,
    evidence_ids: tuple[str, ...],
    reconciliation: ReconcileOutcome,
    *,
    now: float,
    inject_divergence: bool,
) -> EvidenceFinalizePresentation:
    core.create_output(S4_E0_OUTPUT_ID, S4_E0_ATTEMPT_ID, True, evidence_ids)
    before_request = core.requests[S4_E0_REQUEST_ID]
    before_attempt = core.attempts[S4_E0_ATTEMPT_ID]
    error: ContinuityError | None = None
    try:
        core.finalize_request(S4_E0_REQUEST_ID, S4_E0_OUTPUT_ID, now=now)
    except ContinuityError as exc:
        error = exc

    request = core.requests[S4_E0_REQUEST_ID]
    attempt = core.attempts[S4_E0_ATTEMPT_ID]
    committed_after_c1 = (
        request.status is RequestStatus.COMPLETED
        and request.authoritative_output_id == S4_E0_OUTPUT_ID
        and attempt.authority_status is AttemptAuthority.COMMITTED
    )

    if inject_divergence and not scenario.semantic_commit_allowed and not committed_after_c1:
        core.attempts[S4_E0_ATTEMPT_ID] = replace(
            attempt, authority_status=AttemptAuthority.COMMITTED
        )
        core.requests[S4_E0_REQUEST_ID] = replace(
            request,
            status=RequestStatus.COMPLETED,
            current_attempt_id=None,
            committed_attempt_id=S4_E0_ATTEMPT_ID,
            authoritative_output_id=S4_E0_OUTPUT_ID,
        )
        request = core.requests[S4_E0_REQUEST_ID]
        attempt = core.attempts[S4_E0_ATTEMPT_ID]

    authoritative_commit = (
        request.status is RequestStatus.COMPLETED
        and request.authoritative_output_id == S4_E0_OUTPUT_ID
        and attempt.authority_status is AttemptAuthority.COMMITTED
    )
    diverged = authoritative_commit != scenario.semantic_commit_allowed
    commit_outcome = (
        "APPLIED"
        if committed_after_c1
        else ("REJECTED" if error is not None else "IDEMPOTENT")
    )
    return EvidenceFinalizePresentation(
        before_request_status=before_request.status.name,
        after_request_status=request.status.name,
        before_attempt_authority=before_attempt.authority_status.name,
        after_attempt_authority=attempt.authority_status.name,
        reconciliation=reconciliation,
        commit_outcome=commit_outcome,
        error_type=None if error is None else type(error).__name__,
        oracle_commit_allowed=scenario.semantic_commit_allowed,
        authoritative_commit=authoritative_commit,
        diverged_from_oracle=diverged,
    )


def _explicit_non_success(reconciliation: ReconcileOutcome) -> ExplicitNonSuccess:
    if reconciliation is ReconcileOutcome.AMBIGUOUS:
        return ExplicitNonSuccess.AMBIGUOUS
    return ExplicitNonSuccess.WAIT


def _evaluate_measured(
    scenario: EvidenceE1Scenario,
    measured: _MeasuredScenario,
    policy_id: PolicyID,
    *,
    inject_divergence: bool = False,
) -> EvidenceE1Trial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    if inject_divergence and scenario.fault_id is None:
        raise ValueError("anti-false-zero injection requires a faulted scenario")

    core = _scaffold_core()
    evidence_ids = _record_measured_evidence(core, scenario, measured)
    observed = core.reconcile(
        "finalize",
        evidence_ids,
        now=measured.commit_now,
        required_scope={("attempt", S4_E0_ATTEMPT_ID)},
    )
    policy_visible = _policy_visible_projection(
        core,
        policy_id,
        evidence_ids,
        observed,
        now=measured.commit_now,
    )
    presentation = _attempt_finalize_measured(
        core,
        scenario,
        evidence_ids,
        observed,
        now=measured.commit_now,
        inject_divergence=inject_divergence,
    )

    if presentation.diverged_from_oracle:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )
    elif scenario.semantic_commit_allowed:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=True,
        )
    else:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=_explicit_non_success(observed),
        )

    opportunities: tuple[CorrectnessMetric, ...] = ()
    opportunity_event_ids: tuple[str, ...] = ()
    opportunity_scopes: tuple[MetricOpportunityScope, ...] = ()
    violations: tuple[CorrectnessMetric, ...] = ()
    violation_event_ids: tuple[str, ...] = ()
    if scenario.acr_event_id is not None:
        opportunities = (CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,)
        opportunity_event_ids = (scenario.acr_event_id,)
        opportunity_scopes = (MetricOpportunityScope.EXOGENOUS_PAIRED,)
        if presentation.diverged_from_oracle:
            violations = (CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,)
            violation_event_ids = (scenario.acr_event_id,)

    ground_truth = {
        "schema": S4_E1_SCHEMA,
        "scenario_id": scenario.scenario_id,
        "mode": scenario.mode.value,
        "semantic_commit_allowed": scenario.semantic_commit_allowed,
        "oracle_reconciliation": scenario.oracle_reconciliation.name,
        "acr_event_id": scenario.acr_event_id,
        "oracle_rule": (
            "AMBIGUOUS_OR_INSUFFICIENT_MEASURED_EVIDENCE_MUST_NOT_"
            "AUTHORITATIVELY_FINALIZE"
        ),
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "start_method": S4_E1_START_METHOD,
        "ipc_transport": "multiprocessing.Pipe",
    }
    observed_evidence = {
        "physical_events": [dict(item) for item in measured.physical_events],
        "worker_process_ids": list(measured.worker_process_ids),
        "evidence_objects": [
            _evidence_dict(core.evidence[item]) for item in sorted(core.evidence)
        ],
        "output_evidence_ids": list(evidence_ids),
        "commit_now": measured.commit_now,
        "observed_reconciliation": observed.name,
        "oracle_reconciliation": scenario.oracle_reconciliation.name,
        "reconciliation_diverged_from_oracle": (
            observed is not scenario.oracle_reconciliation
        ),
        "finalization_presentation": presentation.to_dict(),
        "request_after": {
            "status": core.requests[S4_E0_REQUEST_ID].status.name,
            "current_attempt_id": core.requests[S4_E0_REQUEST_ID].current_attempt_id,
            "committed_attempt_id": core.requests[S4_E0_REQUEST_ID].committed_attempt_id,
            "authoritative_output_id": core.requests[
                S4_E0_REQUEST_ID
            ].authoritative_output_id,
        },
        "attempt_after": {
            "execution_status": core.attempts[S4_E0_ATTEMPT_ID].execution_status.name,
            "authority_status": core.attempts[S4_E0_ATTEMPT_ID].authority_status.name,
        },
        "injected_divergence": inject_divergence,
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "policy_visible_evidence": dict(policy_visible),
        "workers_cannot_mutate_c1": True,
        "c1_commit_is_authoritative_not_policy_decision": True,
        "oracle_commit_allowed_is_not_policy_visible": True,
        "oracle_reconciliation_is_not_policy_visible": True,
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S4_E1_COHORT_ID,
        trial_id=scenario.scenario_id,
        operation_id=f"finalize:{S4_E0_REQUEST_ID}",
        policy_id=policy_id,
        scenario_id=scenario.scenario_id,
        validation_level=ValidationEvidenceLevel.EV1_MEASURED_CPU_DISTRIBUTED,
        evidence_provenance=ResultEvidenceProvenance.MEASURED,
        ground_truth=ground_truth,
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=semantic_result,
        metric_opportunities=opportunities,
        metric_opportunity_event_ids=opportunity_event_ids,
        metric_opportunity_scopes=opportunity_scopes,
        metric_violations=violations,
        metric_violation_event_ids=violation_event_ids,
        fault_id=scenario.fault_id,
        fault_class=scenario.fault_class,
    )

    return EvidenceE1Trial(
        policy_id=policy_id,
        scenario_id=scenario.scenario_id,
        evaluation=evaluation,
        presentation=presentation,
        evidence_ids=evidence_ids,
        observed_reconciliation=observed,
        reconciliation_diverged_from_oracle=(
            observed is not scenario.oracle_reconciliation
        ),
        physical_events=measured.physical_events,
        worker_process_ids=measured.worker_process_ids,
        policy_visible_evidence=policy_visible,
        injected_divergence=inject_divergence,
    )


def _run_case(
    scenario: EvidenceE1Scenario,
    policy_ids: tuple[PolicyID, ...],
    *,
    inject_divergence: bool = False,
) -> tuple[EvidenceE1Trial, ...]:
    ctx, handles, ready_events = _spawn_workers()
    try:
        measured = _measure_scenario(
            handles, scenario, prefix_events=ready_events
        )
        return tuple(
            _evaluate_measured(
                scenario,
                measured,
                policy_id,
                inject_divergence=inject_divergence,
            )
            for policy_id in policy_ids
        )
    finally:
        _stop_workers(handles)


def run_s4_e1_trial(policy_id: PolicyID, scenario_id: str) -> EvidenceE1Trial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    scenario = _SPEC_BY_ID.get(scenario_id)
    if scenario is None:
        raise ValueError(f"scenario_id must be one of {S4_E1_SCENARIO_IDS!r}")
    return _run_case(scenario, (policy_id,))[0]


def run_s4_e1_paired() -> EvidenceE1Evaluation:
    ctx, handles, ready_events = _spawn_workers()
    trials: list[EvidenceE1Trial] = []
    try:
        pending_ready: tuple[Mapping[str, Any], ...] = ready_events
        for scenario in S4_E1_SCENARIOS:
            respawn = _ensure_workers(ctx, handles)
            prefix_events = pending_ready + respawn
            pending_ready = ()
            measured = _measure_scenario(
                handles,
                scenario,
                prefix_events=prefix_events,
            )
            trials.extend(
                _evaluate_measured(scenario, measured, policy_id)
                for policy_id in PolicyID
            )
    finally:
        _stop_workers(handles)
    result = tuple(trials)
    return EvidenceE1Evaluation(
        trials=result,
        summary=summarize_correctness(tuple(item.evaluation for item in result)),
    )
