from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import multiprocessing as mp
import os
import time
from typing import Any, Mapping

from continuity.core import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    RequestStatus,
    SemanticEvent,
)
from continuity.errors import ContinuityError
from simulator import PolicyID

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
from .idempotence_ordering import _base_core, _inject_second_finalization


S5_E1_SCHEMA = "cadi.c4.6c.idempotence-ordering-e1.v1"
S5_E1_COHORT_ID = "C4.6c:S5:EV1"
S5_E1_START_METHOD = "spawn"
S5_E1_WORKER_IDS = ("w1", "w2", "w3")
S5_E1_MIN_CPU_SECONDS = 0.003
S5_E1_FIXED_WORK_ROUNDS = 1_000
S5_E1_IPC_TIMEOUT_SECONDS = 10.0
S5_E1_REQUEST_ID = "r1"


class S5E1Mode(str, Enum):
    DUPLICATE_FINAL_RESULT = "DUPLICATE_FINAL_RESULT"
    CONFLICTING_LATE_RESULT = "CONFLICTING_LATE_RESULT"
    REORDERED_OLD_A1_AFTER_A2 = "REORDERED_OLD_A1_AFTER_A2"
    DUPLICATE_EVENT_ID = "DUPLICATE_EVENT_ID"
    SINGLE_FINAL_RESULT_CONTROL = "SINGLE_FINAL_RESULT_CONTROL"


@dataclass(frozen=True, slots=True)
class S5E1DeliveryDirective:
    physical_delivery_id: str
    semantic_event_id: str
    worker_id: str
    action: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.physical_delivery_id, "physical_delivery_id"),
            (self.semantic_event_id, "semantic_event_id"),
            (self.worker_id, "worker_id"),
            (self.action, "action"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if self.worker_id not in S5_E1_WORKER_IDS:
            raise ValueError("worker_id must be a canonical S5 EV1 worker")


@dataclass(frozen=True, slots=True)
class S5E1Scenario:
    scenario_id: str
    mode: S5E1Mode
    directives: tuple[S5E1DeliveryDirective, ...]
    fault_class: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise ValueError("scenario_id must be a non-empty string")
        if not isinstance(self.mode, S5E1Mode):
            raise TypeError("mode must be S5E1Mode")
        if not self.directives:
            raise ValueError("scenario requires at least one delivery directive")
        if len({item.physical_delivery_id for item in self.directives}) != len(self.directives):
            raise ValueError("physical delivery identities must be unique within a scenario")
        if self.fault_class is not None and (
            not isinstance(self.fault_class, str) or not self.fault_class
        ):
            raise ValueError("fault_class must be a non-empty string or None")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S5:EV1:{self.scenario_id}"


S5_E1_SCENARIOS = (
    S5E1Scenario(
        "E1-S5-A-DUPLICATE-FINAL-RESULT",
        S5E1Mode.DUPLICATE_FINAL_RESULT,
        (
            S5E1DeliveryDirective(
                "S5:EV1:A:delivery:1", "S5:EV1:A:result", "w1", "FINALIZE_O1"
            ),
            S5E1DeliveryDirective(
                "S5:EV1:A:delivery:2", "S5:EV1:A:result", "w1", "FINALIZE_O1"
            ),
        ),
        "same measured terminal result delivered twice through IPC",
    ),
    S5E1Scenario(
        "E1-S5-B-CONFLICTING-LATE-RESULT",
        S5E1Mode.CONFLICTING_LATE_RESULT,
        (
            S5E1DeliveryDirective(
                "S5:EV1:B:delivery:o1", "S5:EV1:B:o1", "w1", "FINALIZE_O1"
            ),
            S5E1DeliveryDirective(
                "S5:EV1:B:delivery:o2", "S5:EV1:B:o2", "w2", "FINALIZE_O2"
            ),
        ),
        "conflicting measured late output delivered after authoritative completion",
    ),
    S5E1Scenario(
        "E1-S5-C-REORDERED-OLD-A1-AFTER-A2",
        S5E1Mode.REORDERED_OLD_A1_AFTER_A2,
        (
            S5E1DeliveryDirective(
                "S5:EV1:C:delivery:new", "S5:EV1:C:a2", "w2", "FINALIZE_A2_O2"
            ),
            S5E1DeliveryDirective(
                "S5:EV1:C:delivery:old", "S5:EV1:C:a1", "w1", "FINALIZE_A1_O1"
            ),
        ),
        "superseded A1 result delivered after current A2 result",
    ),
    S5E1Scenario(
        "E1-S5-D-DUPLICATE-EVENT-ID",
        S5E1Mode.DUPLICATE_EVENT_ID,
        (
            S5E1DeliveryDirective(
                "S5:EV1:D:delivery:1", "S5:EV1:D:event-1", "w1", "RECORD_PRIMARY_EVENT"
            ),
            S5E1DeliveryDirective(
                "S5:EV1:D:delivery:u", "S5:EV1:D:event-u", "w3", "RECORD_UNRELATED_EVENT"
            ),
            S5E1DeliveryDirective(
                "S5:EV1:D:delivery:2", "S5:EV1:D:event-1", "w1", "RECORD_PRIMARY_EVENT"
            ),
        ),
        "same measured semantic EventID delivered twice around unrelated delivery",
    ),
    S5E1Scenario(
        "E1-S5-E-SINGLE-FINAL-RESULT-CONTROL",
        S5E1Mode.SINGLE_FINAL_RESULT_CONTROL,
        (
            S5E1DeliveryDirective(
                "S5:EV1:E:delivery:1", "S5:EV1:E:result", "w1", "FINALIZE_O1"
            ),
        ),
        None,
    ),
)
S5_E1_SCENARIO_IDS = tuple(item.scenario_id for item in S5_E1_SCENARIOS)
_SCENARIO_BY_ID: Mapping[str, S5E1Scenario] = {
    item.scenario_id: item for item in S5_E1_SCENARIOS
}


@dataclass(slots=True)
class _WorkerHandle:
    worker_id: str
    process: Any
    connection: Any


@dataclass(frozen=True, slots=True)
class MeasuredS5E1Scenario:
    scenario: S5E1Scenario
    physical_deliveries: tuple[Mapping[str, Any], ...]
    worker_process_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.worker_process_ids) != len(S5_E1_WORKER_IDS):
            raise ValueError("measured S5 EV1 scenario requires all worker PIDs")
        if len(set(self.worker_process_ids)) != len(self.worker_process_ids):
            raise ValueError("worker process IDs must be distinct")
        if len(self.physical_deliveries) != len(self.scenario.directives):
            raise ValueError("physical delivery count must match predeclared directives")


@dataclass(frozen=True, slots=True)
class _ReplayPresentation:
    outcomes: tuple[str, ...]
    snapshot: Mapping[str, Any]
    finalization_effects: tuple[str, ...]
    completed_request_id: str | None


@dataclass(frozen=True, slots=True)
class S5E1Trial:
    policy_id: PolicyID
    scenario: S5E1Scenario
    evaluation: CorrectnessEvaluationRecord
    physical_deliveries: tuple[Mapping[str, Any], ...]
    worker_process_ids: tuple[int, ...]
    authoritative_snapshot: Mapping[str, Any]
    finalization_effects: tuple[str, ...]
    application_outcomes: tuple[str, ...]
    completed_request_id: str | None
    injected_duplicate_finalization: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy must match trial policy")
        if self.evaluation.scenario_id != self.scenario.scenario_id:
            raise ValueError("evaluation scenario must match trial scenario")


@dataclass(frozen=True, slots=True)
class S5E1Evaluation:
    trials: tuple[S5E1Trial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (scenario.scenario_id, policy_id)
            for scenario in S5_E1_SCENARIOS
            for policy_id in PolicyID
        )
        actual = tuple((trial.scenario.scenario_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("S5 EV1 trials must use canonical scenario then B0-B4 ordering")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _cpu_digest(seed: str) -> tuple[str, float, float]:
    started_wall = time.monotonic()
    started_cpu = time.process_time()
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    for index in range(S5_E1_FIXED_WORK_ROUNDS):
        digest = hashlib.sha256(digest + index.to_bytes(8, "little")).digest()
    index = S5_E1_FIXED_WORK_ROUNDS
    while time.process_time() - started_cpu < S5_E1_MIN_CPU_SECONDS:
        digest = hashlib.sha256(digest + index.to_bytes(8, "little")).digest()
        index += 1
    return digest.hex(), time.process_time() - started_cpu, time.monotonic() - started_wall


def _worker_main(worker_id: str, connection: Any) -> None:
    semantic_cache: dict[str, tuple[str, float]] = {}
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
            if op != "EMIT":
                raise RuntimeError(f"unknown worker command {op!r}")

            delivery_id = command.get("physical_delivery_id")
            semantic_event_id = command.get("semantic_event_id")
            action = command.get("action")
            for value, name in (
                (delivery_id, "physical_delivery_id"),
                (semantic_event_id, "semantic_event_id"),
                (action, "action"),
            ):
                if not isinstance(value, str) or not value:
                    raise RuntimeError(f"EMIT requires {name}")

            fingerprint = str(action)
            cached = semantic_cache.get(str(semantic_event_id))
            if cached is None:
                semantic_observed_at = time.time()
                semantic_cache[str(semantic_event_id)] = (fingerprint, semantic_observed_at)
            else:
                if cached[0] != fingerprint:
                    raise RuntimeError("same semantic EventID cannot change semantic action")
                semantic_observed_at = cached[1]

            started_at = time.time()
            digest, cpu_seconds, wall_seconds = _cpu_digest(
                f"S5-EV1:{worker_id}:{semantic_event_id}:{delivery_id}:{action}"
            )
            completed_at = time.time()
            connection.send(
                {
                    "kind": "OBSERVATION",
                    "physical_delivery_id": delivery_id,
                    "semantic_event_id": semantic_event_id,
                    "action": action,
                    "worker_id": worker_id,
                    "worker_pid": os.getpid(),
                    "semantic_observed_at": semantic_observed_at,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "delivered_at": time.time(),
                    "process_cpu_seconds": cpu_seconds,
                    "wall_execution_seconds": wall_seconds,
                    "cpu_digest": digest,
                }
            )
    except EOFError:
        return
    finally:
        try:
            connection.close()
        except OSError:
            pass


def _recv(handle: _WorkerHandle, expected_kind: str) -> dict[str, Any]:
    if not handle.connection.poll(S5_E1_IPC_TIMEOUT_SECONDS):
        raise TimeoutError(f"timed out waiting for {expected_kind} from {handle.worker_id}")
    message = handle.connection.recv()
    if not isinstance(message, dict) or message.get("kind") != expected_kind:
        raise AssertionError(f"unexpected worker response from {handle.worker_id}")
    if message.get("worker_id") != handle.worker_id:
        raise AssertionError("worker identity mismatch")
    if int(message.get("worker_pid", -1)) != int(handle.process.pid):
        raise AssertionError("worker PID mismatch")
    return message


def _spawn_workers() -> dict[str, _WorkerHandle]:
    ctx = mp.get_context(S5_E1_START_METHOD)
    handles: dict[str, _WorkerHandle] = {}
    for worker_id in S5_E1_WORKER_IDS:
        parent, child = ctx.Pipe(duplex=True)
        process = ctx.Process(
            target=_worker_main,
            args=(worker_id, child),
            name=f"cadi-s5-e1-{worker_id}",
        )
        process.start()
        child.close()
        handle = _WorkerHandle(worker_id, process, parent)
        _recv(handle, "READY")
        handles[worker_id] = handle
    return handles


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


def _measure_scenarios(
    scenarios: tuple[S5E1Scenario, ...],
) -> Mapping[str, MeasuredS5E1Scenario]:
    handles = _spawn_workers()
    worker_pids = tuple(int(handles[item].process.pid) for item in S5_E1_WORKER_IDS)
    measured: dict[str, MeasuredS5E1Scenario] = {}
    try:
        for scenario in scenarios:
            deliveries: list[Mapping[str, Any]] = []
            for sequence, directive in enumerate(scenario.directives, start=1):
                handle = handles[directive.worker_id]
                handle.connection.send(
                    {
                        "op": "EMIT",
                        "physical_delivery_id": directive.physical_delivery_id,
                        "semantic_event_id": directive.semantic_event_id,
                        "action": directive.action,
                    }
                )
                observation = _recv(handle, "OBSERVATION")
                if observation["physical_delivery_id"] != directive.physical_delivery_id:
                    raise AssertionError("physical delivery identity mismatch")
                if observation["semantic_event_id"] != directive.semantic_event_id:
                    raise AssertionError("semantic EventID mismatch")
                if observation["action"] != directive.action:
                    raise AssertionError("semantic action mismatch")
                if float(observation["process_cpu_seconds"]) < S5_E1_MIN_CPU_SECONDS:
                    raise AssertionError("worker observation did not satisfy CPU floor")
                deliveries.append({**observation, "coordinator_sequence": sequence})
            measured[scenario.scenario_id] = MeasuredS5E1Scenario(
                scenario=scenario,
                physical_deliveries=tuple(deliveries),
                worker_process_ids=worker_pids,
            )
    finally:
        _stop_workers(handles)
    return measured


def _request_core(*, second_output: bool = False) -> ContinuityCore:
    core = _base_core()
    core.create_request(S5_E1_REQUEST_ID, "c")
    core.start_attempt("a1", S5_E1_REQUEST_ID)
    core.complete_attempt("a1", succeeded=True)
    return core


def _attempt_generation_core() -> ContinuityCore:
    core = _base_core()
    core.create_request(S5_E1_REQUEST_ID, "c")
    core.start_attempt("a1", S5_E1_REQUEST_ID)
    core.complete_attempt("a1", succeeded=True)
    core.start_attempt("a2", S5_E1_REQUEST_ID)
    core.complete_attempt("a2", succeeded=True)
    return core


def _ensure_measured_output(
    core: ContinuityCore,
    delivery: Mapping[str, Any],
    *,
    attempt_id: str,
    output_id: str,
) -> None:
    evidence_id = f"evidence:{delivery['semantic_event_id']}"
    evidence = Evidence(
        id=evidence_id,
        claim=f"measured terminal outcome for {attempt_id} succeeded",
        source=f"S5 EV1 worker {delivery['worker_id']} semantic observation {delivery['semantic_event_id']}",
        authority=EvidenceAuthority.EXACT_OBSERVATION,
        status=EvidenceStatus.VALID,
        observed_at=float(delivery["semantic_observed_at"]),
        scope=frozenset({("attempt", attempt_id)}),
        claim_key=f"attempt:{attempt_id}:terminal-outcome",
        claim_value="SUCCEEDED",
    )
    core.record_evidence(evidence)
    if output_id not in core.outputs:
        core.create_output(output_id, attempt_id, True, (evidence_id,))


def _capture_finalize(
    core: ContinuityCore,
    output_id: str,
    *,
    now: float,
    effects: list[str],
) -> str:
    request = core.requests[S5_E1_REQUEST_ID]
    before_status = request.status
    before_output = request.authoritative_output_id
    try:
        core.finalize_request(S5_E1_REQUEST_ID, output_id, now=now)
        outcome = "APPLIED_OR_IDEMPOTENT"
    except ContinuityError as exc:
        outcome = f"REJECTED:{type(exc).__name__}"
    request = core.requests[S5_E1_REQUEST_ID]
    if (
        request.status is RequestStatus.COMPLETED
        and request.authoritative_output_id is not None
        and (
            before_status is not RequestStatus.COMPLETED
            or before_output != request.authoritative_output_id
        )
    ):
        effects.append(str(request.authoritative_output_id))
    return outcome


def _request_projection(core: ContinuityCore, attempts: tuple[str, ...]) -> dict[str, Any]:
    request = core.requests[S5_E1_REQUEST_ID]
    return {
        "request_status": request.status.name,
        "committed_attempt_id": request.committed_attempt_id,
        "authoritative_output_id": request.authoritative_output_id,
        "attempt_authority": {
            attempt_id: core.attempts[attempt_id].authority_status.name
            for attempt_id in attempts
        },
    }


def _event_projection(core: ContinuityCore) -> dict[str, Any]:
    return {
        "events": [
            {
                "id": event.id,
                "kind": event.kind,
                "subject_type": event.subject_type,
                "subject_id": event.subject_id,
                "payload": [list(item) for item in sorted(event.payload)],
            }
            for event in sorted(core.events.values(), key=lambda item: item.id)
        ]
    }


def _independent_target(scenario: S5E1Scenario) -> dict[str, Any]:
    if scenario.mode in {
        S5E1Mode.DUPLICATE_FINAL_RESULT,
        S5E1Mode.CONFLICTING_LATE_RESULT,
        S5E1Mode.SINGLE_FINAL_RESULT_CONTROL,
    }:
        return {
            "request_status": "COMPLETED",
            "committed_attempt_id": "a1",
            "authoritative_output_id": "o1",
            "attempt_authority": {"a1": "COMMITTED"},
        }
    if scenario.mode is S5E1Mode.REORDERED_OLD_A1_AFTER_A2:
        return {
            "request_status": "COMPLETED",
            "committed_attempt_id": "a2",
            "authoritative_output_id": "o2",
            "attempt_authority": {"a1": "SUPERSEDED", "a2": "COMMITTED"},
        }
    if scenario.mode is S5E1Mode.DUPLICATE_EVENT_ID:
        return {
            "events": [
                {
                    "id": "S5:EV1:D:event-1",
                    "kind": "OBSERVATION",
                    "subject_type": "attempt",
                    "subject_id": "a1",
                    "payload": [["result", "SUCCEEDED"]],
                },
                {
                    "id": "S5:EV1:D:event-u",
                    "kind": "OBSERVATION",
                    "subject_type": "continuation",
                    "subject_id": "c",
                    "payload": [["note", "UNRELATED"]],
                },
            ]
        }
    raise AssertionError("unknown S5 EV1 mode")


def _replay_measured(
    measured: MeasuredS5E1Scenario,
    *,
    inject_duplicate_finalization: bool = False,
) -> _ReplayPresentation:
    scenario = measured.scenario
    effects: list[str] = []
    outcomes: list[str] = []

    if scenario.mode in {
        S5E1Mode.DUPLICATE_FINAL_RESULT,
        S5E1Mode.CONFLICTING_LATE_RESULT,
        S5E1Mode.SINGLE_FINAL_RESULT_CONTROL,
    }:
        core = _request_core(second_output=scenario.mode is S5E1Mode.CONFLICTING_LATE_RESULT)
    elif scenario.mode is S5E1Mode.REORDERED_OLD_A1_AFTER_A2:
        core = _attempt_generation_core()
    else:
        core = _base_core()

    for delivery in measured.physical_deliveries:
        action = str(delivery["action"])
        if action == "FINALIZE_O1":
            _ensure_measured_output(core, delivery, attempt_id="a1", output_id="o1")
            outcomes.append(
                f"FINALIZE_O1:{_capture_finalize(core, 'o1', now=float(delivery['delivered_at']), effects=effects)}"
            )
        elif action == "FINALIZE_O2":
            _ensure_measured_output(core, delivery, attempt_id="a1", output_id="o2")
            outcomes.append(
                f"FINALIZE_O2:{_capture_finalize(core, 'o2', now=float(delivery['delivered_at']), effects=effects)}"
            )
        elif action == "FINALIZE_A2_O2":
            _ensure_measured_output(core, delivery, attempt_id="a2", output_id="o2")
            outcomes.append(
                f"FINALIZE_A2_O2:{_capture_finalize(core, 'o2', now=float(delivery['delivered_at']), effects=effects)}"
            )
        elif action == "FINALIZE_A1_O1":
            _ensure_measured_output(core, delivery, attempt_id="a1", output_id="o1")
            outcomes.append(
                f"FINALIZE_A1_O1:{_capture_finalize(core, 'o1', now=float(delivery['delivered_at']), effects=effects)}"
            )
        elif action == "RECORD_PRIMARY_EVENT":
            event = SemanticEvent(
                id=str(delivery["semantic_event_id"]),
                kind="OBSERVATION",
                subject_type="attempt",
                subject_id="a1",
                payload=frozenset({("result", "SUCCEEDED")}),
            )
            existed = event.id in core.events
            core.record_event(event)
            outcomes.append("RECORD_PRIMARY_EVENT:IDEMPOTENT" if existed else "RECORD_PRIMARY_EVENT:APPLIED")
        elif action == "RECORD_UNRELATED_EVENT":
            event = SemanticEvent(
                id=str(delivery["semantic_event_id"]),
                kind="OBSERVATION",
                subject_type="continuation",
                subject_id="c",
                payload=frozenset({("note", "UNRELATED")}),
            )
            existed = event.id in core.events
            core.record_event(event)
            outcomes.append("RECORD_UNRELATED_EVENT:IDEMPOTENT" if existed else "RECORD_UNRELATED_EVENT:APPLIED")
        else:  # pragma: no cover
            raise AssertionError(f"unknown measured S5 action {action!r}")

    if inject_duplicate_finalization:
        if scenario.mode is not S5E1Mode.DUPLICATE_FINAL_RESULT:
            raise ValueError("anti-false-zero injection is defined only for scenario A")
        _inject_second_finalization(core, effects)
        outcomes.append("INJECT:SECOND_FINALIZATION")

    if scenario.mode in {
        S5E1Mode.DUPLICATE_FINAL_RESULT,
        S5E1Mode.CONFLICTING_LATE_RESULT,
        S5E1Mode.SINGLE_FINAL_RESULT_CONTROL,
    }:
        snapshot = _request_projection(core, ("a1",))
    elif scenario.mode is S5E1Mode.REORDERED_OLD_A1_AFTER_A2:
        snapshot = _request_projection(core, ("a1", "a2"))
    else:
        snapshot = _event_projection(core)

    completed_request_id: str | None = None
    if scenario.mode is not S5E1Mode.DUPLICATE_EVENT_ID:
        request = core.requests[S5_E1_REQUEST_ID]
        if (
            request.status is RequestStatus.COMPLETED
            and request.committed_attempt_id is not None
            and request.authoritative_output_id is not None
        ):
            completed_request_id = S5_E1_REQUEST_ID

    return _ReplayPresentation(
        outcomes=tuple(outcomes),
        snapshot=snapshot,
        finalization_effects=tuple(effects),
        completed_request_id=completed_request_id,
    )


def _run_replay_trial(
    policy_id: PolicyID,
    measured: MeasuredS5E1Scenario,
    *,
    inject_duplicate_finalization: bool = False,
) -> S5E1Trial:
    scenario = measured.scenario
    target = _independent_target(scenario)
    replay = _replay_measured(
        measured, inject_duplicate_finalization=inject_duplicate_finalization
    )
    violations: list[str] = []
    if replay.snapshot != target:
        violations.append("SNAPSHOT_MISMATCH")
    duplicate_finalization = len(replay.finalization_effects) > 1
    if duplicate_finalization:
        violations.append("DUPLICATE_FINALIZATION")

    if scenario.mode is not S5E1Mode.DUPLICATE_EVENT_ID and replay.completed_request_id is None:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.FAIL,
        )
    elif violations:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )
    else:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=True,
        )

    opportunities: tuple[CorrectnessMetric, ...] = ()
    opportunity_ids: tuple[str, ...] = ()
    opportunity_scopes: tuple[MetricOpportunityScope, ...] = ()
    metric_violations: tuple[CorrectnessMetric, ...] = ()
    violation_ids: tuple[str, ...] = ()
    dfr_event_id: str | None = None
    if scenario.fault_id is not None and replay.completed_request_id is not None:
        dfr_event_id = (
            f"S5:EV1:{scenario.scenario_id}:completed-request:{replay.completed_request_id}"
        )
        opportunities = (CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,)
        opportunity_ids = (dfr_event_id,)
        opportunity_scopes = (MetricOpportunityScope.POLICY_DERIVED,)
        if duplicate_finalization:
            metric_violations = (CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,)
            violation_ids = (dfr_event_id,)

    physical = [dict(item) for item in measured.physical_deliveries]
    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S5_E1_COHORT_ID,
        trial_id=scenario.scenario_id,
        operation_id=f"s5-e1:{scenario.scenario_id}",
        policy_id=policy_id,
        scenario_id=scenario.scenario_id,
        validation_level=ValidationEvidenceLevel.EV1_MEASURED_CPU_DISTRIBUTED,
        evidence_provenance=ResultEvidenceProvenance.MEASURED,
        ground_truth={
            "schema": S5_E1_SCHEMA,
            "mode": scenario.mode.value,
            "independent_target_snapshot": target,
            "predeclared_physical_delivery_ids": [
                item.physical_delivery_id for item in scenario.directives
            ],
            "predeclared_semantic_event_ids": [
                item.semantic_event_id for item in scenario.directives
            ],
            "semantic_authority": "C1_COMMON_TO_B0_B4",
            "dfr_denominator_scope": "POLICY_DERIVED_COMPLETED_LOGICAL_REQUEST",
        },
        observed_evidence={
            "physical_deliveries": physical,
            "worker_process_ids": list(measured.worker_process_ids),
            "application_outcomes": list(replay.outcomes),
            "authoritative_snapshot": dict(replay.snapshot),
            "snapshot_matches_independent_target": replay.snapshot == target,
            "finalization_effects": list(replay.finalization_effects),
            "semantic_finalization_count": len(replay.finalization_effects),
            "completed_request_id": replay.completed_request_id,
            "dfr_opportunity_observed": dfr_event_id is not None,
            "invariant_violations": violations,
            "injected_duplicate_finalization": inject_duplicate_finalization,
        },
        policy_decision={
            "semantic_authority": "C1_COMMON_TO_B0_B4",
            "policy_specific_s5_information_used": False,
            "physical_schedule_measured_before_policy_replay": True,
            "workers_cannot_mutate_c1": True,
        },
        semantic_result=semantic_result,
        metric_opportunities=opportunities,
        metric_opportunity_event_ids=opportunity_ids,
        metric_opportunity_scopes=opportunity_scopes,
        metric_violations=metric_violations,
        metric_violation_event_ids=violation_ids,
        fault_id=scenario.fault_id,
        fault_class=scenario.fault_class,
    )
    return S5E1Trial(
        policy_id=policy_id,
        scenario=scenario,
        evaluation=evaluation,
        physical_deliveries=measured.physical_deliveries,
        worker_process_ids=measured.worker_process_ids,
        authoritative_snapshot=replay.snapshot,
        finalization_effects=replay.finalization_effects,
        application_outcomes=replay.outcomes,
        completed_request_id=replay.completed_request_id,
        injected_duplicate_finalization=inject_duplicate_finalization,
    )


def run_s5_e1_trial(policy_id: PolicyID, scenario_id: str) -> S5E1Trial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    scenario = _SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise ValueError(f"scenario_id must be one of {S5_E1_SCENARIO_IDS!r}")
    measured = _measure_scenarios((scenario,))[scenario_id]
    return _run_replay_trial(policy_id, measured)


def run_s5_e1_paired() -> S5E1Evaluation:
    measured = _measure_scenarios(S5_E1_SCENARIOS)
    trials = tuple(
        _run_replay_trial(policy_id, measured[scenario.scenario_id])
        for scenario in S5_E1_SCENARIOS
        for policy_id in PolicyID
    )
    return S5E1Evaluation(
        trials=trials,
        summary=summarize_correctness(tuple(item.evaluation for item in trials)),
    )


def run_s5_e1_anti_false_zero() -> S5E1Trial:
    scenario = _SCENARIO_BY_ID["E1-S5-A-DUPLICATE-FINAL-RESULT"]
    measured = _measure_scenarios((scenario,))[scenario.scenario_id]
    return _run_replay_trial(
        PolicyID.B4,
        measured,
        inject_duplicate_finalization=True,
    )
