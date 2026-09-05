from __future__ import annotations

from dataclasses import dataclass
import hashlib
import multiprocessing as mp
import os
import time
from typing import Any, Mapping

from continuity.entities import (
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    RequestStatus,
)
from simulator import (
    CoreContinuityAuthority,
    PlacementDecision,
    PolicyID,
    build_baseline_policies,
    decide_placement,
)

from .correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    CorrectnessSummary,
    MetricOpportunityScope,
    RecoveryAction,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)
from .state_lineage import (
    ApplicationEffect,
    StateConsumptionEvent,
    TerminalExecutionEvent,
    _ScenarioRuntime,
    _build_runtime as _build_s2_e0_runtime,
    _placement_to_dict,
)
from .state_lineage_adversarial import (
    S2_ADVERSARIAL_MANIFESTS,
    _build_runtime as _build_s2_adversarial_runtime,
    _independent_compatible,
)


S2_E1_SCHEMA = "cadi.c4.3c.state-lineage-e1.v1"
S2_E1_COHORT_ID = "C4.3c:S2:EV1"
S2_E1_START_METHOD = "spawn"
S2_E1_FIXED_WORK_ROUNDS = 5_000
S2_E1_MIN_CPU_SECONDS = 0.003
S2_E1_IPC_TIMEOUT_SECONDS = 10.0
S2_E1_WORKER_IDS = ("w1", "w2")


@dataclass(frozen=True, slots=True)
class StateLineageE1Scenario:
    scenario_id: str
    source_case_id: str
    source_kind: str
    fault_class: str | None
    wbrr_event_id: str | None
    expected_compatible: bool
    evict_candidate_after_observation: bool = False

    def __post_init__(self) -> None:
        for value, name in (
            (self.scenario_id, "scenario_id"),
            (self.source_case_id, "source_case_id"),
            (self.source_kind, "source_kind"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if self.source_kind not in {"C4.3a", "C4.3b"}:
            raise ValueError("source_kind must be C4.3a or C4.3b")
        if self.fault_class is not None and (
            not isinstance(self.fault_class, str) or not self.fault_class
        ):
            raise ValueError("fault_class must be a non-empty string or None")
        if self.wbrr_event_id is not None and (
            not isinstance(self.wbrr_event_id, str) or not self.wbrr_event_id
        ):
            raise ValueError("wbrr_event_id must be a non-empty string or None")
        if not isinstance(self.expected_compatible, bool):
            raise TypeError("expected_compatible must be bool")
        if not isinstance(self.evict_candidate_after_observation, bool):
            raise TypeError("evict_candidate_after_observation must be bool")
        if self.wbrr_event_id is not None and self.fault_class is None:
            raise ValueError("WBRR opportunity must belong to a faulted scenario")
        if self.evict_candidate_after_observation and not self.expected_compatible:
            raise ValueError("physical-eviction control must start from compatible State")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S2:EV1:{self.scenario_id}"


S2_E1_SCENARIOS = (
    StateLineageE1Scenario(
        "E1-S2-A-WRONG-SIBLING",
        "FTR4",
        "C4.3a",
        "wrong sibling State under real worker execution",
        "S2:EV1:A:wrong-branch-reuse-opportunity",
        False,
    ),
    StateLineageE1Scenario(
        "E1-S2-B-SUPERSEDED-PRODUCER",
        "FTR5",
        "C4.3a",
        "superseded-producer State under real worker execution",
        None,
        False,
    ),
    StateLineageE1Scenario(
        "E1-S2-C-ABANDONED-RESIDUAL",
        "FTR14",
        "C4.3a",
        "abandoned-branch residual State under real worker execution",
        "S2:EV1:C:wrong-branch-reuse-opportunity",
        False,
    ),
    StateLineageE1Scenario(
        "E1-S2-D-SIMILAR-DIFFERENT",
        "S2-SIMILAR-DIFFERENT",
        "C4.3a",
        "similar-but-different State under real worker execution",
        "S2:EV1:D:wrong-branch-reuse-opportunity",
        False,
    ),
    StateLineageE1Scenario(
        "E1-S2-E-DERIVED-INVALID-DEPENDENCY",
        "S2D-DERIVED-INVALID-DEPENDENCY",
        "C4.3b",
        "derived State with invalid dependency under real worker execution",
        None,
        False,
    ),
    StateLineageE1Scenario(
        "E1-S2-F-VALID-ANCESTOR-CONTROL",
        "S2-VALID-ANCESTOR",
        "C4.3a",
        None,
        None,
        True,
    ),
    StateLineageE1Scenario(
        "E1-S2-G-POST-OBSERVATION-EVICTION",
        "S2-VALID-ANCESTOR",
        "C4.3a",
        "compatible State physically evicted after scheduler observation",
        None,
        True,
        evict_candidate_after_observation=True,
    ),
)
S2_E1_SCENARIO_IDS = tuple(item.scenario_id for item in S2_E1_SCENARIOS)
_SPEC_BY_ID: Mapping[str, StateLineageE1Scenario] = {
    item.scenario_id: item for item in S2_E1_SCENARIOS
}
_ADVERSARIAL_MANIFEST_BY_ID = {
    item.case_id: item for item in S2_ADVERSARIAL_MANIFESTS
}


@dataclass(frozen=True, slots=True)
class StateLineageE1Trial:
    policy_id: PolicyID
    scenario_id: str
    evaluation: CorrectnessEvaluationRecord
    placement_decision: PlacementDecision
    execution_worker_id: str
    candidate_state_id: str
    consumption_event: StateConsumptionEvent | None
    terminal_event: TerminalExecutionEvent
    independent_oracle_compatible: bool
    c1_compatible: bool
    worker_process_ids: tuple[int, ...]
    worker_execution: Mapping[str, Any]
    execution_worker_override: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.scenario_id not in S2_E1_SCENARIO_IDS:
            raise ValueError("scenario_id must be canonical C4.3c EV1 scenario")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy_id must match trial policy_id")
        if self.evaluation.scenario_id != self.scenario_id:
            raise ValueError("evaluation scenario_id must match trial scenario_id")
        if self.placement_decision.policy_id is not self.policy_id:
            raise ValueError("placement decision must match trial policy")
        if self.execution_worker_id not in S2_E1_WORKER_IDS:
            raise ValueError("execution_worker_id must be canonical worker")
        if self.independent_oracle_compatible != self.c1_compatible:
            raise ValueError("C1 compatibility must agree with independent EV1 oracle")
        if len(self.worker_process_ids) != 2 or len(set(self.worker_process_ids)) != 2:
            raise ValueError("EV1 trial requires two distinct real worker process IDs")
        if not all(isinstance(item, int) and item > 0 for item in self.worker_process_ids):
            raise ValueError("worker_process_ids must be positive integers")
        consumed = None if self.consumption_event is None else self.consumption_event.state_id
        if self.terminal_event.consumed_state_id != consumed:
            raise ValueError("terminal event must reference measured worker consumption")
        if self.execution_worker_override is not None and (
            self.execution_worker_override not in S2_E1_WORKER_IDS
        ):
            raise ValueError("execution_worker_override must be canonical worker or None")


@dataclass(frozen=True, slots=True)
class StateLineageE1Evaluation:
    trials: tuple[StateLineageE1Trial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (scenario.scenario_id, policy_id)
            for scenario in S2_E1_SCENARIOS
            for policy_id in PolicyID
        )
        actual = tuple((trial.scenario_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("S2 EV1 trials must use canonical scenario then B0-B4 ordering")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


@dataclass(slots=True)
class _WorkerHandle:
    worker_id: str
    process: Any
    connection: Any


def _cpu_digest(seed: str) -> tuple[str, float, float]:
    started_wall = time.monotonic()
    started_cpu = time.process_time()
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    for index in range(S2_E1_FIXED_WORK_ROUNDS):
        digest = hashlib.sha256(digest + index.to_bytes(8, "little")).digest()
    index = S2_E1_FIXED_WORK_ROUNDS
    while time.process_time() - started_cpu < S2_E1_MIN_CPU_SECONDS:
        digest = hashlib.sha256(digest + index.to_bytes(8, "little")).digest()
        index += 1
    return (
        digest.hex(),
        time.process_time() - started_cpu,
        time.monotonic() - started_wall,
    )


def _worker_main(worker_id: str, connection: Any) -> None:
    physical_states: dict[str, str] = {}
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
            if op == "RESET":
                physical_states.clear()
                connection.send(
                    {
                        "kind": "RESET",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "states": {},
                        "at": time.time(),
                    }
                )
                continue
            if op == "INSTALL":
                state_id = command.get("state_id")
                replica_id = command.get("replica_id")
                if not isinstance(state_id, str) or not state_id:
                    raise RuntimeError("INSTALL requires state_id")
                if not isinstance(replica_id, str) or not replica_id:
                    raise RuntimeError("INSTALL requires replica_id")
                physical_states[state_id] = replica_id
                connection.send(
                    {
                        "kind": "INSTALLED",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "state_id": state_id,
                        "replica_id": replica_id,
                        "states": dict(sorted(physical_states.items())),
                        "at": time.time(),
                    }
                )
                continue
            if op == "EVICT":
                state_id = command.get("state_id")
                if not isinstance(state_id, str) or not state_id:
                    raise RuntimeError("EVICT requires state_id")
                removed_replica_id = physical_states.pop(state_id, None)
                connection.send(
                    {
                        "kind": "EVICTED",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "state_id": state_id,
                        "removed_replica_id": removed_replica_id,
                        "states": dict(sorted(physical_states.items())),
                        "at": time.time(),
                    }
                )
                continue
            if op == "EXECUTE":
                event_id = command.get("event_id")
                directive_id = command.get("directive_id")
                candidate_state_id = command.get("candidate_state_id")
                application_effect = command.get("application_effect")
                expected_result_token = command.get("expected_result_token")
                scenario_id = command.get("scenario_id")
                request_id = command.get("request_id")
                if not all(
                    isinstance(value, str) and value
                    for value in (
                        event_id,
                        directive_id,
                        candidate_state_id,
                        application_effect,
                        expected_result_token,
                        scenario_id,
                        request_id,
                    )
                ):
                    raise RuntimeError("EXECUTE requires complete string identity")

                states_before = dict(sorted(physical_states.items()))
                replica_id = physical_states.get(candidate_state_id)
                consumed_state_id = candidate_state_id if replica_id is not None else None
                consumption_event_id = (
                    None
                    if consumed_state_id is None
                    else f"{event_id}:state-consumed:{candidate_state_id}"
                )
                started_at = time.time()
                digest, cpu_seconds, wall_seconds = _cpu_digest(
                    f"{scenario_id}:{worker_id}:{request_id}:{event_id}"
                )

                candidate_result_token: str | None
                detected_bad_state: bool
                used_recompute: bool
                if consumed_state_id is None:
                    candidate_result_token = None
                    detected_bad_state = False
                    used_recompute = True
                    actual_result_token = expected_result_token
                else:
                    if application_effect == ApplicationEffect.CORRECT_RESULT.value:
                        candidate_result_token = expected_result_token
                        detected_bad_state = False
                        used_recompute = False
                        actual_result_token = candidate_result_token
                    elif application_effect == ApplicationEffect.WRONG_UNDETECTED.value:
                        candidate_result_token = (
                            f"candidate-wrong:{scenario_id}:"
                            f"{candidate_state_id}:{request_id}"
                        )
                        detected_bad_state = False
                        used_recompute = False
                        actual_result_token = candidate_result_token
                    elif application_effect == ApplicationEffect.DETECT_AND_RECOMPUTE.value:
                        candidate_result_token = (
                            f"candidate-detected:{scenario_id}:"
                            f"{candidate_state_id}:{request_id}"
                        )
                        detected_bad_state = True
                        used_recompute = True
                        actual_result_token = expected_result_token
                    else:
                        raise RuntimeError("unknown application_effect")

                connection.send(
                    {
                        "kind": "EXECUTION_RESULT",
                        "event_id": event_id,
                        "scenario_id": scenario_id,
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "started_at": started_at,
                        "completed_at": time.time(),
                        "process_cpu_seconds": cpu_seconds,
                        "wall_execution_seconds": wall_seconds,
                        "cpu_digest": digest,
                        "states_before": states_before,
                        "candidate_state_id": candidate_state_id,
                        "consumed_state_id": consumed_state_id,
                        "consumed_replica_id": replica_id,
                        "consumption_event_id": consumption_event_id,
                        "directive_id": directive_id,
                        "application_effect": application_effect,
                        "expected_result_token": expected_result_token,
                        "candidate_result_token": candidate_result_token,
                        "actual_result_token": actual_result_token,
                        "detected_bad_state": detected_bad_state,
                        "used_recompute": used_recompute,
                    }
                )
                continue
            raise RuntimeError(f"unknown worker command: {command!r}")
    except EOFError:
        return
    finally:
        connection.close()


def _recv(handle: _WorkerHandle, *, expected_kind: str) -> dict[str, Any]:
    if not handle.connection.poll(S2_E1_IPC_TIMEOUT_SECONDS):
        raise TimeoutError(f"timed out waiting for {expected_kind} from {handle.worker_id}")
    message = handle.connection.recv()
    if not isinstance(message, dict):
        raise AssertionError("worker response must be a mapping")
    if message.get("kind") != expected_kind:
        raise AssertionError(
            f"expected worker response {expected_kind}, got {message.get('kind')!r}"
        )
    if message.get("worker_id") != handle.worker_id:
        raise AssertionError("worker response identity mismatch")
    if int(message.get("worker_pid", -1)) != int(handle.process.pid):
        raise AssertionError("worker response PID mismatch")
    return message


def _command(
    handle: _WorkerHandle,
    command: Mapping[str, Any],
    *,
    expected_kind: str,
) -> dict[str, Any]:
    handle.connection.send(dict(command))
    return _recv(handle, expected_kind=expected_kind)


def _spawn_workers() -> tuple[dict[str, _WorkerHandle], tuple[dict[str, Any], ...]]:
    ctx = mp.get_context(S2_E1_START_METHOD)
    handles: dict[str, _WorkerHandle] = {}
    ready_events: list[dict[str, Any]] = []
    for worker_id in S2_E1_WORKER_IDS:
        parent, child = ctx.Pipe(duplex=True)
        process = ctx.Process(
            target=_worker_main,
            args=(worker_id, child),
            name=f"cadi-s2-e1-{worker_id}",
        )
        process.start()
        child.close()
        handle = _WorkerHandle(worker_id, process, parent)
        handles[worker_id] = handle
    for worker_id in S2_E1_WORKER_IDS:
        ready_events.append(_recv(handles[worker_id], expected_kind="READY"))
    return handles, tuple(ready_events)


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
        handle.connection.close()


def _build_runtime(spec: StateLineageE1Scenario) -> _ScenarioRuntime:
    if spec.source_kind == "C4.3a":
        runtime = _build_s2_e0_runtime(spec.source_case_id)
    else:
        manifest = _ADVERSARIAL_MANIFEST_BY_ID.get(spec.source_case_id)
        if manifest is None:
            raise AssertionError(f"missing C4.3b manifest {spec.source_case_id}")
        runtime = _build_s2_adversarial_runtime(manifest)
    exact = _independent_compatible(
        runtime.core,
        runtime.candidate_state_id,
        runtime.consumer_context,
    )
    c1 = runtime.core.state_compatible(
        runtime.candidate_state_id,
        runtime.consumer_context,
    )
    if exact != c1:
        raise AssertionError("C1 compatibility diverges from independent EV1 oracle")
    if exact != spec.expected_compatible:
        raise AssertionError("EV1 scenario compatibility expectation is incorrect")
    return runtime


def _expected_result_token(runtime: _ScenarioRuntime) -> str:
    return (
        f"correct:{runtime.consumer_context.continuation_id}:"
        f"{runtime.consumer_context.request_id}"
    )


def _reset_and_install(
    handles: Mapping[str, _WorkerHandle],
    runtime: _ScenarioRuntime,
) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for worker_id in S2_E1_WORKER_IDS:
        events.append(
            _command(
                handles[worker_id],
                {"op": "RESET"},
                expected_kind="RESET",
            )
        )
    if runtime.candidate_replica_id is None:
        raise AssertionError("EV1 candidate requires a physical replica identity")
    events.append(
        _command(
            handles["w1"],
            {
                "op": "INSTALL",
                "state_id": runtime.candidate_state_id,
                "replica_id": runtime.candidate_replica_id,
            },
            expected_kind="INSTALLED",
        )
    )
    if runtime.compatible_alternative_state_id is not None:
        events.append(
            _command(
                handles["w2"],
                {
                    "op": "INSTALL",
                    "state_id": runtime.compatible_alternative_state_id,
                    "replica_id": f"physical:{runtime.compatible_alternative_state_id}@w2",
                },
                expected_kind="INSTALLED",
            )
        )
    return tuple(events)


def _finalize_measured_result(
    runtime: _ScenarioRuntime,
    spec: StateLineageE1Scenario,
    policy_id: PolicyID,
    execution: Mapping[str, Any],
    consumption_event: StateConsumptionEvent | None,
) -> TerminalExecutionEvent:
    core = runtime.core
    ctx = runtime.consumer_context
    core.complete_attempt(ctx.attempt_id)
    evidence_id = f"S2:EV1:{spec.scenario_id}:{policy_id.value}:result-evidence"
    actual = str(execution["actual_result_token"])
    core.record_evidence(
        Evidence(
            id=evidence_id,
            claim="measured application terminal result",
            source="C4.3c real worker process",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=float(execution["completed_at"]),
            scope=frozenset(
                {
                    ("attempt", ctx.attempt_id),
                    ("result_token", actual),
                    ("worker_pid", str(execution["worker_pid"])),
                }
            ),
        )
    )
    output_id = f"S2:EV1:{spec.scenario_id}:{policy_id.value}:terminal-output"
    core.create_output(
        output_id,
        ctx.attempt_id,
        terminal=True,
        evidence_ids=(evidence_id,),
    )
    core.finalize_request(ctx.request_id, output_id, now=float(execution["completed_at"]))
    request = core.requests[ctx.request_id]
    authoritative_commit = (
        request.status is RequestStatus.COMPLETED
        and request.committed_attempt_id == ctx.attempt_id
        and request.authoritative_output_id == output_id
        and evidence_id in core.outputs[output_id].evidence_ids
        and ("result_token", actual) in core.evidence[evidence_id].scope
    )
    if not authoritative_commit:
        raise AssertionError("EV1 result-bearing Output did not commit authoritatively")

    consumed_state_id = (
        None if consumption_event is None else consumption_event.state_id
    )
    candidate_result = execution.get("candidate_result_token")
    return TerminalExecutionEvent(
        event_id=f"S2:EV1:{spec.scenario_id}:{policy_id.value}:terminal",
        request_id=ctx.request_id,
        attempt_id=ctx.attempt_id,
        result_evidence_id=evidence_id,
        output_id=output_id,
        application_profile_id=runtime.application_profile.profile_id,
        application_effect=runtime.application_profile.effect,
        expected_result_token=str(execution["expected_result_token"]),
        candidate_result_token=(
            None if candidate_result is None else str(candidate_result)
        ),
        actual_result_token=actual,
        consumed_state_id=consumed_state_id,
        detected_bad_state=bool(execution["detected_bad_state"]),
        used_recompute=bool(execution["used_recompute"]),
        reported_success=True,
        authoritative_commit=True,
        semantically_correct=actual == str(execution["expected_result_token"]),
    )


def _ground_truth(
    runtime: _ScenarioRuntime,
    spec: StateLineageE1Scenario,
    compatible: bool,
) -> dict[str, Any]:
    state = runtime.core.states[runtime.candidate_state_id]
    producer_attempt = (
        None
        if state.producer_attempt_id is None
        else runtime.core.attempts[state.producer_attempt_id]
    )
    dependencies = tuple(
        {
            "state_id": dependency_id,
            "validity": runtime.core.states[dependency_id].validity.name,
            "producer_attempt_id": runtime.core.states[dependency_id].producer_attempt_id,
            "producer_attempt_authority": (
                None
                if runtime.core.states[dependency_id].producer_attempt_id is None
                else runtime.core.attempts[
                    runtime.core.states[dependency_id].producer_attempt_id
                ].authority_status.name
            ),
        }
        for dependency_id in sorted(state.derived_from)
    )
    return {
        "schema": S2_E1_SCHEMA,
        "scenario_id": spec.scenario_id,
        "source_case_id": spec.source_case_id,
        "fault_class": spec.fault_class,
        "candidate_state_id": runtime.candidate_state_id,
        "candidate_replica_id": runtime.candidate_replica_id,
        "candidate_state_validity": state.validity.name,
        "candidate_origin_continuation_id": state.origin_continuation_id,
        "candidate_producer_attempt_id": state.producer_attempt_id,
        "candidate_producer_attempt_authority": (
            None if producer_attempt is None else producer_attempt.authority_status.name
        ),
        "candidate_dependencies": dependencies,
        "consumer_context": {
            "program_id": runtime.consumer_context.program_id,
            "session_id": runtime.consumer_context.session_id,
            "continuation_id": runtime.consumer_context.continuation_id,
            "request_id": runtime.consumer_context.request_id,
            "attempt_id": runtime.consumer_context.attempt_id,
            "phase_id": runtime.consumer_context.phase_id,
        },
        "independent_oracle_compatible": compatible,
        "c1_compatible": compatible,
        "policy_visible_locations": list(runtime.observation.state_locations),
        "physical_initial_candidate_location": "w1",
        "evict_candidate_after_observation": spec.evict_candidate_after_observation,
        "application_profile": runtime.application_profile.to_dict(),
        "worker_ids": list(S2_E1_WORKER_IDS),
        "start_method": S2_E1_START_METHOD,
        "ipc_transport": "multiprocessing.Pipe",
        "cpu_work": {
            "algorithm": "SHA-256",
            "minimum_fixed_rounds": S2_E1_FIXED_WORK_ROUNDS,
            "minimum_process_cpu_seconds": S2_E1_MIN_CPU_SECONDS,
        },
        "wrong_branch_reuse_opportunity_event_id": spec.wbrr_event_id,
    }


def _execute_policy_trial(
    handles: Mapping[str, _WorkerHandle],
    ready_events: tuple[dict[str, Any], ...],
    spec: StateLineageE1Scenario,
    policy_id: PolicyID,
    *,
    execution_worker_override: str | None = None,
) -> StateLineageE1Trial:
    runtime = _build_runtime(spec)
    compatible = _independent_compatible(
        runtime.core,
        runtime.candidate_state_id,
        runtime.consumer_context,
    )
    c1_compatible = runtime.core.state_compatible(
        runtime.candidate_state_id,
        runtime.consumer_context,
    )
    if compatible != c1_compatible:
        raise AssertionError("C1 compatibility diverges from independent EV1 oracle")

    physical_events = list(_reset_and_install(handles, runtime))
    authority = CoreContinuityAuthority(runtime.core)
    policy = build_baseline_policies(authority)[policy_id]
    decision = decide_placement(policy, runtime.observation)
    if decision.worker_id is None:
        raise AssertionError("EV1 scenario requires an executable worker placement")

    if spec.evict_candidate_after_observation:
        physical_events.append(
            _command(
                handles["w1"],
                {"op": "EVICT", "state_id": runtime.candidate_state_id},
                expected_kind="EVICTED",
            )
        )

    execution_worker_id = (
        decision.worker_id
        if execution_worker_override is None
        else execution_worker_override
    )
    if execution_worker_id not in handles:
        raise ValueError("execution worker override must name a real worker")

    execution_event_id = (
        f"S2:EV1:{spec.scenario_id}:{policy_id.value}:worker-execution"
    )
    execution = _command(
        handles[execution_worker_id],
        {
            "op": "EXECUTE",
            "event_id": execution_event_id,
            "scenario_id": spec.scenario_id,
            "directive_id": runtime.consumption_directive.directive_id,
            "candidate_state_id": runtime.candidate_state_id,
            "application_effect": runtime.application_profile.effect.value,
            "expected_result_token": _expected_result_token(runtime),
            "request_id": runtime.consumer_context.request_id,
        },
        expected_kind="EXECUTION_RESULT",
    )
    if float(execution["process_cpu_seconds"]) < S2_E1_MIN_CPU_SECONDS:
        raise AssertionError("worker execution did not satisfy process-CPU floor")

    consumption_event: StateConsumptionEvent | None = None
    if execution["consumed_state_id"] is not None:
        if execution["consumed_state_id"] != runtime.candidate_state_id:
            raise AssertionError("worker consumed a State other than declared candidate")
        if execution["consumed_replica_id"] is None:
            raise AssertionError("measured State consumption requires physical replica")
        consumption_event = StateConsumptionEvent(
            event_id=str(execution["consumption_event_id"]),
            directive_id=str(execution["directive_id"]),
            state_id=str(execution["consumed_state_id"]),
            replica_id=str(execution["consumed_replica_id"]),
            worker_id=str(execution["worker_id"]),
        )

    incompatible_consumption = consumption_event is not None and not compatible
    wrong_branch_reuse = spec.wbrr_event_id is not None and incompatible_consumption

    opportunities: list[CorrectnessMetric] = []
    opportunity_event_ids: list[str] = []
    opportunity_scopes: list[MetricOpportunityScope] = []
    violations: list[CorrectnessMetric] = []
    violation_event_ids: list[str] = []

    if spec.wbrr_event_id is not None:
        opportunities.append(CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
        opportunity_event_ids.append(spec.wbrr_event_id)
        opportunity_scopes.append(MetricOpportunityScope.EXOGENOUS_PAIRED)
        if wrong_branch_reuse:
            violations.append(CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
            violation_event_ids.append(spec.wbrr_event_id)

    if consumption_event is not None and spec.fault_id is not None:
        opportunities.append(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
        opportunity_event_ids.append(consumption_event.event_id)
        opportunity_scopes.append(MetricOpportunityScope.POLICY_DERIVED)
        if incompatible_consumption:
            violations.append(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
            violation_event_ids.append(consumption_event.event_id)

    terminal_event = _finalize_measured_result(
        runtime,
        spec,
        policy_id,
        execution,
        consumption_event,
    )
    semantic_result = SemanticResult(
        reported_success=terminal_event.reported_success,
        authoritative_commit=terminal_event.authoritative_commit,
        semantically_correct=terminal_event.semantically_correct,
        recovery_actions=(
            (RecoveryAction.RECOMPUTE,) if terminal_event.used_recompute else ()
        ),
    )

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S2_E1_COHORT_ID,
        trial_id=spec.scenario_id,
        operation_id=runtime.consumer_context.request_id,
        policy_id=policy_id,
        scenario_id=spec.scenario_id,
        validation_level=ValidationEvidenceLevel.EV1_MEASURED_CPU_DISTRIBUTED,
        evidence_provenance=ResultEvidenceProvenance.MEASURED,
        ground_truth=_ground_truth(runtime, spec, compatible),
        observed_evidence={
            "coordinator_pid": os.getpid(),
            "worker_ready_events": list(ready_events),
            "physical_state_events": physical_events,
            "worker_execution": dict(execution),
            "measured_consumption_event": (
                None if consumption_event is None else consumption_event.to_dict()
            ),
            "terminal_event": terminal_event.to_dict(),
        },
        policy_decision={
            "placement": _placement_to_dict(decision),
            "execution_worker_id": execution_worker_id,
            "execution_worker_override": execution_worker_override,
            "worker_rule": "CONSUME_DECLARED_CANDIDATE_ONLY_IF_PHYSICALLY_LOCAL",
            "lineage_oracle_is_not_worker_visible": True,
            "application_profile_is_not_policy_visible": True,
        },
        semantic_result=semantic_result,
        metric_opportunities=tuple(opportunities),
        metric_opportunity_event_ids=tuple(opportunity_event_ids),
        metric_opportunity_scopes=tuple(opportunity_scopes),
        metric_violations=tuple(violations),
        metric_violation_event_ids=tuple(violation_event_ids),
        fault_id=spec.fault_id,
        fault_class=spec.fault_class,
    )

    return StateLineageE1Trial(
        policy_id=policy_id,
        scenario_id=spec.scenario_id,
        evaluation=evaluation,
        placement_decision=decision,
        execution_worker_id=execution_worker_id,
        candidate_state_id=runtime.candidate_state_id,
        consumption_event=consumption_event,
        terminal_event=terminal_event,
        independent_oracle_compatible=compatible,
        c1_compatible=c1_compatible,
        worker_process_ids=tuple(
            sorted(int(handle.process.pid) for handle in handles.values())
        ),
        worker_execution=dict(execution),
        execution_worker_override=execution_worker_override,
    )


def _run_case(
    spec: StateLineageE1Scenario,
    policy_ids: tuple[PolicyID, ...],
    *,
    execution_worker_override: str | None = None,
) -> tuple[StateLineageE1Trial, ...]:
    handles, ready_events = _spawn_workers()
    try:
        return tuple(
            _execute_policy_trial(
                handles,
                ready_events,
                spec,
                policy_id,
                execution_worker_override=execution_worker_override,
            )
            for policy_id in policy_ids
        )
    finally:
        _stop_workers(handles)


def run_s2_e1_trial(
    policy_id: PolicyID,
    scenario_id: str,
    *,
    execution_worker_override: str | None = None,
) -> StateLineageE1Trial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    spec = _SPEC_BY_ID.get(scenario_id)
    if spec is None:
        raise ValueError(f"scenario_id must be one of {S2_E1_SCENARIO_IDS!r}")
    return _run_case(
        spec,
        (policy_id,),
        execution_worker_override=execution_worker_override,
    )[0]


def run_s2_e1_paired() -> StateLineageE1Evaluation:
    trials: list[StateLineageE1Trial] = []
    for spec in S2_E1_SCENARIOS:
        trials.extend(_run_case(spec, tuple(PolicyID)))
    frozen = tuple(trials)
    return StateLineageE1Evaluation(
        trials=frozen,
        summary=summarize_correctness(tuple(item.evaluation for item in frozen)),
    )
