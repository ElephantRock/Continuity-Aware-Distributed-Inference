from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import multiprocessing as mp
import os
import time
from typing import Any, Mapping

from continuity.entities import Evidence, EvidenceAuthority, EvidenceStatus
from continuity.invariants import InvariantOracle
from simulator import MigrationDecision, PolicyID

from .binding_safety import (
    S3_E0_SUBJECT_ID,
    BindingPresentationResult,
    _attempt_binding_commit,
    _authority_snapshot,
    _b4_migration_decision,
    _scaffold_core,
)
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


S3_E1_SCHEMA = "cadi.c4.4c.binding-safety-e1.v1"
S3_E1_COHORT_ID = "C4.4c:S3:EV1"
S3_E1_START_METHOD = "spawn"
S3_E1_WORKER_IDS = ("w1", "w2", "w3")
S3_E1_FIXED_WORK_ROUNDS = 5_000
S3_E1_MIN_CPU_SECONDS = 0.004
S3_E1_IPC_TIMEOUT_SECONDS = 10.0
S3_E1_CRASH_EXIT_CODE = 17


class BindingE1Mode(str, Enum):
    PARTIAL_MATERIALIZATION = "PARTIAL_MATERIALIZATION"
    DESTINATION_CRASH = "DESTINATION_CRASH"
    LATE_OLD_OWNER = "LATE_OLD_OWNER"
    CONCURRENT_CANDIDATES = "CONCURRENT_CANDIDATES"
    DELAYED_STALE_LOSER = "DELAYED_STALE_LOSER"
    MULTI_EPOCH_LATE_OWNER = "MULTI_EPOCH_LATE_OWNER"
    SUCCESS_CONTROL = "SUCCESS_CONTROL"


@dataclass(frozen=True, slots=True)
class BindingE1Scenario:
    scenario_id: str
    mode: BindingE1Mode
    fault_class: str | None
    sbdr_event_id: str | None
    expected_binding_id: str
    expected_epoch: int
    explicit_wait: bool = False

    def __post_init__(self) -> None:
        for value, name in (
            (self.scenario_id, "scenario_id"),
            (self.expected_binding_id, "expected_binding_id"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.mode, BindingE1Mode):
            raise TypeError("mode must be BindingE1Mode")
        if self.fault_class is not None and (
            not isinstance(self.fault_class, str) or not self.fault_class
        ):
            raise ValueError("fault_class must be a non-empty string or None")
        if self.sbdr_event_id is not None and (
            not isinstance(self.sbdr_event_id, str) or not self.sbdr_event_id
        ):
            raise ValueError("sbdr_event_id must be a non-empty string or None")
        if (self.fault_class is None) != (self.sbdr_event_id is None):
            raise ValueError("faulted EV1 scenarios require exactly one SBDR EventID")
        if not isinstance(self.expected_epoch, int) or isinstance(self.expected_epoch, bool):
            raise TypeError("expected_epoch must be int")
        if self.expected_epoch < 1:
            raise ValueError("expected_epoch must be positive")
        if not isinstance(self.explicit_wait, bool):
            raise TypeError("explicit_wait must be bool")
        if self.explicit_wait and self.fault_class is None:
            raise ValueError("explicit_wait is only meaningful for faulted cases")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S3:EV1:{self.scenario_id}"


S3_E1_SCENARIOS = (
    BindingE1Scenario(
        "E1-S3-A-PARTIAL-MATERIALIZATION",
        BindingE1Mode.PARTIAL_MATERIALIZATION,
        "destination partially materialized without semantic commit Evidence",
        "S3:EV1:A:partial-commit-presentation",
        "b1",
        1,
        explicit_wait=True,
    ),
    BindingE1Scenario(
        "E1-S3-B-DESTINATION-CRASH",
        BindingE1Mode.DESTINATION_CRASH,
        "destination process exits before semantic migration commit",
        "S3:EV1:B:post-crash-commit-presentation",
        "b1",
        1,
        explicit_wait=True,
    ),
    BindingE1Scenario(
        "E1-S3-C-LATE-OLD-OWNER",
        BindingE1Mode.LATE_OLD_OWNER,
        "late old-owner presentation after W1 to W2 migration commit",
        "S3:EV1:C:late-old-owner-presentation",
        "b2",
        2,
    ),
    BindingE1Scenario(
        "E1-S3-D-CONCURRENT-CANDIDATES",
        BindingE1Mode.CONCURRENT_CANDIDATES,
        "concurrent W2/W3 migration candidates with one semantic winner",
        "S3:EV1:D:concurrent-loser-presentation",
        "b2",
        2,
    ),
    BindingE1Scenario(
        "E1-S3-E-DELAYED-STALE-LOSER",
        BindingE1Mode.DELAYED_STALE_LOSER,
        "delayed physical loser work and stale presentation after winner commit",
        "S3:EV1:E:delayed-stale-loser-presentation",
        "b2",
        2,
    ),
    BindingE1Scenario(
        "E1-S3-F-MULTI-EPOCH-LATE-OWNER",
        BindingE1Mode.MULTI_EPOCH_LATE_OWNER,
        "multi-epoch stale owner after W1 to W2 to W3 migration",
        "S3:EV1:F:multi-epoch-old-owner-presentation",
        "b3",
        3,
    ),
    BindingE1Scenario(
        "E1-S3-G-SUCCESS-CONTROL",
        BindingE1Mode.SUCCESS_CONTROL,
        None,
        None,
        "b2",
        2,
    ),
)
S3_E1_SCENARIO_IDS = tuple(item.scenario_id for item in S3_E1_SCENARIOS)
_SPEC_BY_ID: Mapping[str, BindingE1Scenario] = {
    item.scenario_id: item for item in S3_E1_SCENARIOS
}


@dataclass(slots=True)
class _WorkerHandle:
    worker_id: str
    process: Any
    connection: Any


@dataclass(frozen=True, slots=True)
class BindingE1Trial:
    policy_id: PolicyID
    scenario_id: str
    evaluation: CorrectnessEvaluationRecord
    policy_migration_decisions: tuple[MigrationDecision, ...]
    presentation: BindingPresentationResult
    physical_events: tuple[Mapping[str, Any], ...]
    worker_process_ids: tuple[int, ...]
    expected_binding_id: str
    expected_epoch: int
    final_binding_id: str
    final_epoch: int
    injected_divergence: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.scenario_id not in S3_E1_SCENARIO_IDS:
            raise ValueError("scenario_id must be canonical S3 EV1 scenario")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy must match trial policy")
        if self.evaluation.scenario_id != self.scenario_id:
            raise ValueError("evaluation scenario must match trial scenario")
        if self.policy_id is not PolicyID.B4 and self.policy_migration_decisions:
            raise ValueError("only B4 has the frozen migration-admission surface")
        if not all(isinstance(item, MigrationDecision) for item in self.policy_migration_decisions):
            raise TypeError("policy_migration_decisions must contain MigrationDecision")
        if not isinstance(self.presentation, BindingPresentationResult):
            raise TypeError("presentation must be BindingPresentationResult")
        if len(self.worker_process_ids) != 3 or len(set(self.worker_process_ids)) != 3:
            raise ValueError("S3 EV1 trial requires three distinct worker process IDs")
        if not all(isinstance(item, int) and item > 0 for item in self.worker_process_ids):
            raise ValueError("worker_process_ids must be positive integers")
        if not isinstance(self.injected_divergence, bool):
            raise TypeError("injected_divergence must be bool")


@dataclass(frozen=True, slots=True)
class BindingE1Evaluation:
    trials: tuple[BindingE1Trial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (scenario.scenario_id, policy_id)
            for scenario in S3_E1_SCENARIOS
            for policy_id in PolicyID
        )
        actual = tuple((trial.scenario_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("S3 EV1 trials must use canonical scenario then B0-B4 ordering")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _cpu_digest(seed: str, minimum_cpu_seconds: float = S3_E1_MIN_CPU_SECONDS) -> tuple[str, float, float]:
    started_wall = time.monotonic()
    started_cpu = time.process_time()
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    for index in range(S3_E1_FIXED_WORK_ROUNDS):
        digest = hashlib.sha256(digest + index.to_bytes(8, "little")).digest()
    index = S3_E1_FIXED_WORK_ROUNDS
    while time.process_time() - started_cpu < minimum_cpu_seconds:
        digest = hashlib.sha256(digest + index.to_bytes(8, "little")).digest()
        index += 1
    return (
        digest.hex(),
        time.process_time() - started_cpu,
        time.monotonic() - started_wall,
    )


def _materialize_payload(worker_id: str, pending: Mapping[str, Any]) -> dict[str, Any]:
    binding_id = str(pending["binding_id"])
    epoch = int(pending["epoch"])
    completeness = str(pending["completeness"])
    started_at = time.time()
    digest, cpu_seconds, wall_seconds = _cpu_digest(
        f"S3-EV1:MATERIALIZE:{worker_id}:{binding_id}:{epoch}:{completeness}"
    )
    return {
        "kind": "MATERIALIZED",
        "worker_id": worker_id,
        "worker_pid": os.getpid(),
        "event_id": str(pending["event_id"]),
        "binding_id": binding_id,
        "epoch": epoch,
        "completeness": completeness,
        "started_at": started_at,
        "completed_at": time.time(),
        "process_cpu_seconds": cpu_seconds,
        "wall_execution_seconds": wall_seconds,
        "cpu_digest": digest,
    }


def _worker_main(worker_id: str, connection: Any) -> None:
    physical_bindings: dict[str, dict[str, Any]] = {}
    pending: dict[str, Any] | None = None
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
                physical_bindings.clear()
                pending = None
                connection.send(
                    {
                        "kind": "RESET",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "bindings": {},
                        "at": time.time(),
                    }
                )
                continue
            if op == "ARM_MATERIALIZE":
                if pending is not None:
                    raise RuntimeError("worker already has an armed materialization")
                binding_id = command.get("binding_id")
                epoch = command.get("epoch")
                event_id = command.get("event_id")
                completeness = command.get("completeness", "READY")
                if not isinstance(binding_id, str) or not binding_id:
                    raise RuntimeError("ARM_MATERIALIZE requires binding_id")
                if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
                    raise RuntimeError("ARM_MATERIALIZE requires positive epoch")
                if not isinstance(event_id, str) or not event_id:
                    raise RuntimeError("ARM_MATERIALIZE requires event_id")
                if completeness not in {"READY", "PARTIAL"}:
                    raise RuntimeError("completeness must be READY or PARTIAL")
                pending = {
                    "binding_id": binding_id,
                    "epoch": epoch,
                    "event_id": event_id,
                    "completeness": completeness,
                }
                connection.send(
                    {
                        "kind": "MATERIALIZE_ARMED",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        **pending,
                        "at": time.time(),
                    }
                )
                continue
            if op == "GO_MATERIALIZE":
                if pending is None:
                    raise RuntimeError("GO_MATERIALIZE requires an armed materialization")
                payload = _materialize_payload(worker_id, pending)
                physical_bindings[payload["binding_id"]] = {
                    "epoch": payload["epoch"],
                    "completeness": payload["completeness"],
                }
                pending = None
                payload["bindings_after"] = dict(sorted(physical_bindings.items()))
                connection.send(payload)
                continue
            if op == "DROP":
                binding_id = command.get("binding_id")
                if not isinstance(binding_id, str) or not binding_id:
                    raise RuntimeError("DROP requires binding_id")
                removed = physical_bindings.pop(binding_id, None)
                connection.send(
                    {
                        "kind": "DROPPED",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "binding_id": binding_id,
                        "removed": removed,
                        "bindings_after": dict(sorted(physical_bindings.items())),
                        "at": time.time(),
                    }
                )
                continue
            if op == "CPU":
                event_id = command.get("event_id")
                if not isinstance(event_id, str) or not event_id:
                    raise RuntimeError("CPU requires event_id")
                minimum = float(command.get("minimum_cpu_seconds", S3_E1_MIN_CPU_SECONDS))
                started_at = time.time()
                digest, cpu_seconds, wall_seconds = _cpu_digest(
                    f"S3-EV1:CPU:{worker_id}:{event_id}", minimum
                )
                connection.send(
                    {
                        "kind": "CPU_RESULT",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "event_id": event_id,
                        "started_at": started_at,
                        "completed_at": time.time(),
                        "process_cpu_seconds": cpu_seconds,
                        "wall_execution_seconds": wall_seconds,
                        "cpu_digest": digest,
                    }
                )
                continue
            if op == "PRESENT":
                event_id = command.get("event_id")
                binding_id = command.get("binding_id")
                if not isinstance(event_id, str) or not event_id:
                    raise RuntimeError("PRESENT requires event_id")
                if not isinstance(binding_id, str) or not binding_id:
                    raise RuntimeError("PRESENT requires binding_id")
                local = physical_bindings.get(binding_id)
                if local is None:
                    raise RuntimeError("cannot PRESENT a Binding not physically materialized")
                connection.send(
                    {
                        "kind": "PRESENTATION",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "event_id": event_id,
                        "binding_id": binding_id,
                        "epoch": int(local["epoch"]),
                        "completeness": str(local["completeness"]),
                        "bindings_at_presentation": dict(sorted(physical_bindings.items())),
                        "observed_at": time.time(),
                        "delivered_at": time.time(),
                    }
                )
                continue
            if op == "CRASH":
                event_id = command.get("event_id")
                if not isinstance(event_id, str) or not event_id:
                    raise RuntimeError("CRASH requires event_id")
                connection.send(
                    {
                        "kind": "CRASHING",
                        "worker_id": worker_id,
                        "worker_pid": os.getpid(),
                        "event_id": event_id,
                        "bindings_before_exit": dict(sorted(physical_bindings.items())),
                        "at": time.time(),
                    }
                )
                connection.close()
                os._exit(S3_E1_CRASH_EXIT_CODE)
            raise RuntimeError(f"unknown worker command: {command!r}")
    except EOFError:
        return
    finally:
        try:
            connection.close()
        except OSError:
            pass


def _recv(handle: _WorkerHandle, *, expected_kind: str) -> dict[str, Any]:
    if not handle.connection.poll(S3_E1_IPC_TIMEOUT_SECONDS):
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
    return message


def _command(
    handle: _WorkerHandle,
    command: Mapping[str, Any],
    *,
    expected_kind: str,
) -> dict[str, Any]:
    handle.connection.send(dict(command))
    return _recv(handle, expected_kind=expected_kind)


def _spawn_one(ctx: Any, worker_id: str) -> tuple[_WorkerHandle, dict[str, Any]]:
    parent, child = ctx.Pipe(duplex=True)
    process = ctx.Process(
        target=_worker_main,
        args=(worker_id, child),
        name=f"cadi-s3-e1-{worker_id}",
    )
    process.start()
    child.close()
    handle = _WorkerHandle(worker_id, process, parent)
    ready = _recv(handle, expected_kind="READY")
    return handle, ready


def _spawn_workers() -> tuple[Any, dict[str, _WorkerHandle], tuple[dict[str, Any], ...]]:
    ctx = mp.get_context(S3_E1_START_METHOD)
    handles: dict[str, _WorkerHandle] = {}
    ready_events: list[dict[str, Any]] = []
    for worker_id in S3_E1_WORKER_IDS:
        handle, ready = _spawn_one(ctx, worker_id)
        handles[worker_id] = handle
        ready_events.append(ready)
    return ctx, handles, tuple(ready_events)


def _ensure_workers(ctx: Any, handles: dict[str, _WorkerHandle]) -> tuple[dict[str, Any], ...]:
    ready_events: list[dict[str, Any]] = []
    for worker_id in S3_E1_WORKER_IDS:
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


def _reset_workers(handles: Mapping[str, _WorkerHandle]) -> tuple[dict[str, Any], ...]:
    return tuple(
        _command(handle, {"op": "RESET"}, expected_kind="RESET")
        for handle in (handles[worker_id] for worker_id in S3_E1_WORKER_IDS)
    )


def _arm_materialization(
    handle: _WorkerHandle,
    *,
    binding_id: str,
    epoch: int,
    event_id: str,
    completeness: str,
) -> dict[str, Any]:
    return _command(
        handle,
        {
            "op": "ARM_MATERIALIZE",
            "binding_id": binding_id,
            "epoch": epoch,
            "event_id": event_id,
            "completeness": completeness,
        },
        expected_kind="MATERIALIZE_ARMED",
    )


def _go_materialization(handle: _WorkerHandle) -> dict[str, Any]:
    handle.connection.send({"op": "GO_MATERIALIZE"})
    result = _recv(handle, expected_kind="MATERIALIZED")
    if float(result["process_cpu_seconds"]) < S3_E1_MIN_CPU_SECONDS:
        raise AssertionError("physical materialization did not satisfy process-CPU floor")
    return result


def _materialize(
    handle: _WorkerHandle,
    *,
    binding_id: str,
    epoch: int,
    event_id: str,
    completeness: str = "READY",
) -> tuple[dict[str, Any], dict[str, Any]]:
    armed = _arm_materialization(
        handle,
        binding_id=binding_id,
        epoch=epoch,
        event_id=event_id,
        completeness=completeness,
    )
    handle.connection.send({"op": "GO_MATERIALIZE"})
    result = _recv(handle, expected_kind="MATERIALIZED")
    if float(result["process_cpu_seconds"]) < S3_E1_MIN_CPU_SECONDS:
        raise AssertionError("physical materialization did not satisfy process-CPU floor")
    return armed, result


def _materialize_concurrently(
    first: _WorkerHandle,
    second: _WorkerHandle,
    *,
    first_binding_id: str,
    first_epoch: int,
    second_binding_id: str,
    second_epoch: int,
    scenario_id: str,
) -> tuple[dict[str, Any], ...]:
    armed_first = _arm_materialization(
        first,
        binding_id=first_binding_id,
        epoch=first_epoch,
        event_id=f"S3:EV1:{scenario_id}:materialize:{first_binding_id}",
        completeness="READY",
    )
    armed_second = _arm_materialization(
        second,
        binding_id=second_binding_id,
        epoch=second_epoch,
        event_id=f"S3:EV1:{scenario_id}:materialize:{second_binding_id}",
        completeness="READY",
    )
    first.connection.send({"op": "GO_MATERIALIZE"})
    second.connection.send({"op": "GO_MATERIALIZE"})
    result_first = _recv(first, expected_kind="MATERIALIZED")
    result_second = _recv(second, expected_kind="MATERIALIZED")
    for result in (result_first, result_second):
        if float(result["process_cpu_seconds"]) < S3_E1_MIN_CPU_SECONDS:
            raise AssertionError("concurrent materialization did not satisfy process-CPU floor")
    return armed_first, armed_second, result_first, result_second


def _record_measured_commit_evidence(
    core: Any,
    *,
    binding_id: str,
    evidence_id: str,
    worker_event: Mapping[str, Any],
) -> str:
    binding = core.bindings[binding_id]
    observed_at = float(
        worker_event.get("delivered_at", worker_event.get("completed_at", worker_event.get("at")))
    )
    core.record_evidence(
        Evidence(
            id=evidence_id,
            claim="measured migration commit readiness",
            source="C4.4c real worker process",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=observed_at,
            scope=frozenset(
                {
                    ("binding", binding.id),
                    ("epoch", str(binding.epoch)),
                    ("worker", str(worker_event["worker_id"])),
                    ("worker_pid", str(worker_event["worker_pid"])),
                }
            ),
        )
    )
    return evidence_id


def _commit_measured_setup(
    core: Any,
    *,
    binding_id: str,
    worker_event: Mapping[str, Any],
    evidence_id: str,
) -> dict[str, Any]:
    evidence = _record_measured_commit_evidence(
        core,
        binding_id=binding_id,
        evidence_id=evidence_id,
        worker_event=worker_event,
    )
    before = _authority_snapshot(core)
    now = float(worker_event.get("completed_at", worker_event.get("delivered_at")))
    core.commit_migration(binding_id, (evidence,), now=now)
    InvariantOracle(core).assert_all()
    after = _authority_snapshot(core)
    if after == before:
        raise AssertionError("measured setup migration must change Binding authority")
    return {
        "kind": "MEASURED_SETUP_COMMIT",
        "binding_id": binding_id,
        "evidence_id": evidence,
        "worker_event_id": worker_event.get("event_id"),
        "before": before,
        "after": after,
    }


def _physical_initial_owner(
    handles: Mapping[str, _WorkerHandle],
    scenario_id: str,
) -> tuple[dict[str, Any], ...]:
    armed, materialized = _materialize(
        handles["w1"],
        binding_id="b1",
        epoch=1,
        event_id=f"S3:EV1:{scenario_id}:physical-initial-b1",
    )
    return armed, materialized


def _presentation_message(
    handles: Mapping[str, _WorkerHandle],
    *,
    worker_id: str,
    binding_id: str,
    event_id: str,
) -> dict[str, Any]:
    return _command(
        handles[worker_id],
        {"op": "PRESENT", "binding_id": binding_id, "event_id": event_id},
        expected_kind="PRESENTATION",
    )


def _decision(
    core: Any,
    policy_id: PolicyID,
    *,
    binding_id: str,
    reconciliation: str,
) -> MigrationDecision | None:
    return _b4_migration_decision(
        core,
        policy_id,
        binding_id=binding_id,
        reconciliation=reconciliation,
    )


def _execute_trial(
    ctx: Any,
    handles: dict[str, _WorkerHandle],
    scenario: BindingE1Scenario,
    policy_id: PolicyID,
    *,
    inject_divergence: bool = False,
) -> BindingE1Trial:
    if inject_divergence and scenario.fault_id is None:
        raise ValueError("divergence injection requires a faulted scenario")

    respawn_events = list(_ensure_workers(ctx, handles))
    physical_events: list[Mapping[str, Any]] = []
    physical_events.extend(respawn_events)
    physical_events.extend(_reset_workers(handles))
    physical_events.extend(_physical_initial_owner(handles, scenario.scenario_id))

    core = _scaffold_core()
    decisions: list[MigrationDecision] = []
    setup_commits: list[Mapping[str, Any]] = []
    final_worker_presentation: Mapping[str, Any] | None = None
    final_evidence_ids: tuple[str, ...] = ()
    final_binding_id: str

    if scenario.mode is BindingE1Mode.PARTIAL_MATERIALIZATION:
        b2 = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(b2.id)
        armed, materialized = _materialize(
            handles["w2"],
            binding_id=b2.id,
            epoch=b2.epoch,
            event_id=f"S3:EV1:{scenario.scenario_id}:partial-b2",
            completeness="PARTIAL",
        )
        physical_events.extend((armed, materialized))
        item = _decision(core, policy_id, binding_id=b2.id, reconciliation="WAIT")
        if item is not None:
            decisions.append(item)
        final_worker_presentation = _presentation_message(
            handles,
            worker_id="w2",
            binding_id=b2.id,
            event_id=scenario.sbdr_event_id or "",
        )
        physical_events.append(final_worker_presentation)
        final_binding_id = b2.id

    elif scenario.mode is BindingE1Mode.DESTINATION_CRASH:
        b2 = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(b2.id)
        armed, materialized = _materialize(
            handles["w2"],
            binding_id=b2.id,
            epoch=b2.epoch,
            event_id=f"S3:EV1:{scenario.scenario_id}:ready-b2",
        )
        physical_events.extend((armed, materialized))
        item = _decision(core, policy_id, binding_id=b2.id, reconciliation="WAIT")
        if item is not None:
            decisions.append(item)
        crash = _command(
            handles["w2"],
            {"op": "CRASH", "event_id": f"S3:EV1:{scenario.scenario_id}:crash-w2"},
            expected_kind="CRASHING",
        )
        physical_events.append(crash)
        handles["w2"].process.join(timeout=2.0)
        if handles["w2"].process.is_alive():
            raise AssertionError("destination crash process did not exit")
        if handles["w2"].process.exitcode != S3_E1_CRASH_EXIT_CODE:
            raise AssertionError("destination crash process returned unexpected exit code")
        physical_events.append(
            {
                "kind": "PROCESS_EXIT",
                "worker_id": "w2",
                "worker_pid": handles["w2"].process.pid,
                "exit_code": handles["w2"].process.exitcode,
                "at": time.time(),
            }
        )
        final_binding_id = b2.id

    elif scenario.mode is BindingE1Mode.LATE_OLD_OWNER:
        b2 = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(b2.id)
        armed, materialized = _materialize(
            handles["w2"],
            binding_id=b2.id,
            epoch=b2.epoch,
            event_id=f"S3:EV1:{scenario.scenario_id}:ready-b2",
        )
        physical_events.extend((armed, materialized))
        item = _decision(core, policy_id, binding_id=b2.id, reconciliation="MATCHED")
        if item is not None:
            decisions.append(item)
        setup_commits.append(
            _commit_measured_setup(
                core,
                binding_id=b2.id,
                worker_event=materialized,
                evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:b2-commit-evidence",
            )
        )
        final_worker_presentation = _presentation_message(
            handles,
            worker_id="w1",
            binding_id="b1",
            event_id=scenario.sbdr_event_id or "",
        )
        physical_events.append(final_worker_presentation)
        item = _decision(core, policy_id, binding_id="b1", reconciliation="MATCHED")
        if item is not None:
            decisions.append(item)
        evidence = _record_measured_commit_evidence(
            core,
            binding_id="b1",
            evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:late-b1-evidence",
            worker_event=final_worker_presentation,
        )
        final_evidence_ids = (evidence,)
        final_binding_id = "b1"

    elif scenario.mode is BindingE1Mode.CONCURRENT_CANDIDATES:
        b2 = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(b2.id)
        b3 = core.propose_binding("b3", S3_E0_SUBJECT_ID, "w3")
        core.begin_migration(b3.id)
        concurrent = _materialize_concurrently(
            handles["w2"],
            handles["w3"],
            first_binding_id=b2.id,
            first_epoch=b2.epoch,
            second_binding_id=b3.id,
            second_epoch=b3.epoch,
            scenario_id=scenario.scenario_id,
        )
        physical_events.extend(concurrent)
        b2_materialized, b3_materialized = concurrent[-2], concurrent[-1]
        for candidate in (b2, b3):
            item = _decision(core, policy_id, binding_id=candidate.id, reconciliation="MATCHED")
            if item is not None:
                decisions.append(item)
        setup_commits.append(
            _commit_measured_setup(
                core,
                binding_id=b2.id,
                worker_event=b2_materialized,
                evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:b2-winner-evidence",
            )
        )
        final_worker_presentation = _presentation_message(
            handles,
            worker_id="w3",
            binding_id=b3.id,
            event_id=scenario.sbdr_event_id or "",
        )
        physical_events.append(final_worker_presentation)
        evidence = _record_measured_commit_evidence(
            core,
            binding_id=b3.id,
            evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:b3-loser-evidence",
            worker_event=final_worker_presentation,
        )
        final_evidence_ids = (evidence,)
        final_binding_id = b3.id

    elif scenario.mode is BindingE1Mode.DELAYED_STALE_LOSER:
        b2 = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(b2.id)
        b3 = core.propose_binding("b3", S3_E0_SUBJECT_ID, "w3")
        core.begin_migration(b3.id)
        concurrent = _materialize_concurrently(
            handles["w2"],
            handles["w3"],
            first_binding_id=b2.id,
            first_epoch=b2.epoch,
            second_binding_id=b3.id,
            second_epoch=b3.epoch,
            scenario_id=scenario.scenario_id,
        )
        physical_events.extend(concurrent)
        b2_materialized = concurrent[-2]
        for candidate in (b2, b3):
            item = _decision(core, policy_id, binding_id=candidate.id, reconciliation="MATCHED")
            if item is not None:
                decisions.append(item)
        handles["w3"].connection.send(
            {
                "op": "CPU",
                "event_id": f"S3:EV1:{scenario.scenario_id}:stale-loser-physical-work",
                "minimum_cpu_seconds": S3_E1_MIN_CPU_SECONDS,
            }
        )
        setup_commits.append(
            _commit_measured_setup(
                core,
                binding_id=b2.id,
                worker_event=b2_materialized,
                evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:b2-winner-evidence",
            )
        )
        delayed_work = _recv(handles["w3"], expected_kind="CPU_RESULT")
        if float(delayed_work["process_cpu_seconds"]) < S3_E1_MIN_CPU_SECONDS:
            raise AssertionError("stale loser physical work did not satisfy CPU floor")
        physical_events.append(delayed_work)
        final_worker_presentation = _presentation_message(
            handles,
            worker_id="w3",
            binding_id=b3.id,
            event_id=scenario.sbdr_event_id or "",
        )
        physical_events.append(final_worker_presentation)
        evidence = _record_measured_commit_evidence(
            core,
            binding_id=b3.id,
            evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:delayed-b3-evidence",
            worker_event=final_worker_presentation,
        )
        final_evidence_ids = (evidence,)
        final_binding_id = b3.id

    elif scenario.mode is BindingE1Mode.MULTI_EPOCH_LATE_OWNER:
        b2 = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(b2.id)
        armed2, materialized2 = _materialize(
            handles["w2"],
            binding_id=b2.id,
            epoch=b2.epoch,
            event_id=f"S3:EV1:{scenario.scenario_id}:ready-b2",
        )
        physical_events.extend((armed2, materialized2))
        item = _decision(core, policy_id, binding_id=b2.id, reconciliation="MATCHED")
        if item is not None:
            decisions.append(item)
        setup_commits.append(
            _commit_measured_setup(
                core,
                binding_id=b2.id,
                worker_event=materialized2,
                evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:b2-commit-evidence",
            )
        )
        b3 = core.propose_binding("b3", S3_E0_SUBJECT_ID, "w3")
        core.begin_migration(b3.id)
        armed3, materialized3 = _materialize(
            handles["w3"],
            binding_id=b3.id,
            epoch=b3.epoch,
            event_id=f"S3:EV1:{scenario.scenario_id}:ready-b3",
        )
        physical_events.extend((armed3, materialized3))
        item = _decision(core, policy_id, binding_id=b3.id, reconciliation="MATCHED")
        if item is not None:
            decisions.append(item)
        setup_commits.append(
            _commit_measured_setup(
                core,
                binding_id=b3.id,
                worker_event=materialized3,
                evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:b3-commit-evidence",
            )
        )
        final_worker_presentation = _presentation_message(
            handles,
            worker_id="w1",
            binding_id="b1",
            event_id=scenario.sbdr_event_id or "",
        )
        physical_events.append(final_worker_presentation)
        item = _decision(core, policy_id, binding_id="b1", reconciliation="MATCHED")
        if item is not None:
            decisions.append(item)
        evidence = _record_measured_commit_evidence(
            core,
            binding_id="b1",
            evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:late-b1-evidence",
            worker_event=final_worker_presentation,
        )
        final_evidence_ids = (evidence,)
        final_binding_id = "b1"

    elif scenario.mode is BindingE1Mode.SUCCESS_CONTROL:
        b2 = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(b2.id)
        armed, materialized = _materialize(
            handles["w2"],
            binding_id=b2.id,
            epoch=b2.epoch,
            event_id=f"S3:EV1:{scenario.scenario_id}:ready-b2",
        )
        physical_events.extend((armed, materialized))
        item = _decision(core, policy_id, binding_id=b2.id, reconciliation="MATCHED")
        if item is not None:
            decisions.append(item)
        final_worker_presentation = _presentation_message(
            handles,
            worker_id="w2",
            binding_id=b2.id,
            event_id=f"S3:EV1:{scenario.scenario_id}:positive-control-presentation",
        )
        physical_events.append(final_worker_presentation)
        evidence = _record_measured_commit_evidence(
            core,
            binding_id=b2.id,
            evidence_id=f"S3:EV1:{scenario.scenario_id}:{policy_id.value}:b2-control-evidence",
            worker_event=final_worker_presentation,
        )
        final_evidence_ids = (evidence,)
        final_binding_id = b2.id

    else:
        raise AssertionError("unhandled S3 EV1 scenario")

    if scenario.mode is BindingE1Mode.DESTINATION_CRASH:
        now = float(next(item["at"] for item in reversed(physical_events) if item.get("kind") == "PROCESS_EXIT"))
    elif final_worker_presentation is not None:
        now = float(final_worker_presentation["delivered_at"])
    else:
        now = time.time()

    presentation = _attempt_binding_commit(
        core,
        event_id=(scenario.sbdr_event_id or f"S3:EV1:{scenario.scenario_id}:control"),
        binding_id=final_binding_id,
        evidence_ids=final_evidence_ids,
        expected_binding_id=scenario.expected_binding_id,
        expected_epoch=scenario.expected_epoch,
        now=now,
        inject_divergence=inject_divergence,
    )

    final = _authority_snapshot(core)
    actual_binding_id = str(final["current_binding_id"])
    actual_epoch = int(final["current_epoch"])
    final_matches_oracle = (
        actual_binding_id == scenario.expected_binding_id
        and actual_epoch == scenario.expected_epoch
    )
    if presentation.diverged_from_oracle != (not final_matches_oracle):
        raise AssertionError("S3 EV1 presentation divergence must match final authority divergence")

    opportunities: tuple[CorrectnessMetric, ...] = ()
    opportunity_event_ids: tuple[str, ...] = ()
    opportunity_scopes: tuple[MetricOpportunityScope, ...] = ()
    violations: tuple[CorrectnessMetric, ...] = ()
    violation_event_ids: tuple[str, ...] = ()
    if scenario.sbdr_event_id is not None:
        opportunities = (CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,)
        opportunity_event_ids = (scenario.sbdr_event_id,)
        opportunity_scopes = (MetricOpportunityScope.EXOGENOUS_PAIRED,)
        if not final_matches_oracle:
            violations = (CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,)
            violation_event_ids = (scenario.sbdr_event_id,)

    if inject_divergence:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )
    elif scenario.explicit_wait:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.WAIT,
        )
    else:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=final_matches_oracle,
        )

    worker_pids = tuple(
        int(handles[worker_id].process.pid) for worker_id in S3_E1_WORKER_IDS
    )

    ground_truth = {
        "schema": S3_E1_SCHEMA,
        "scenario_id": scenario.scenario_id,
        "mode": scenario.mode.value,
        "fault_class": scenario.fault_class,
        "sbdr_event_id": scenario.sbdr_event_id,
        "subject_id": S3_E0_SUBJECT_ID,
        "initial_binding_id": "b1",
        "initial_epoch": 1,
        "expected_final_binding_id": scenario.expected_binding_id,
        "expected_final_epoch": scenario.expected_epoch,
        "explicit_wait": scenario.explicit_wait,
        "worker_ids": list(S3_E1_WORKER_IDS),
        "start_method": S3_E1_START_METHOD,
        "ipc_transport": "multiprocessing.Pipe",
        "cpu_work": {
            "algorithm": "SHA-256",
            "minimum_fixed_rounds": S3_E1_FIXED_WORK_ROUNDS,
            "minimum_process_cpu_seconds": S3_E1_MIN_CPU_SECONDS,
        },
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "oracle_rule": "EXPECTED_BINDING_AND_EPOCH_DECLARED_BEFORE_POLICY_EXECUTION",
    }
    observed_evidence = {
        "coordinator_pid": os.getpid(),
        "worker_process_ids": list(worker_pids),
        "physical_events": [dict(item) for item in physical_events],
        "setup_commits": [dict(item) for item in setup_commits],
        "final_worker_presentation": (
            None if final_worker_presentation is None else dict(final_worker_presentation)
        ),
        "coordinator_binding_presentation": presentation.to_dict(),
        "final_authority": final,
        "injected_divergence": inject_divergence,
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "binding_information_contract": (
            "B4_BINDING_ID_EPOCH_RECONCILIATION"
            if policy_id is PolicyID.B4
            else "NO_BINDING_AWARE_MIGRATION_POLICY_SURFACE"
        ),
        "migration_decisions": [
            {
                "binding_id": item.binding_id,
                "binding_epoch": item.binding_epoch,
                "disposition": item.disposition.value,
                "reason": item.reason,
            }
            for item in decisions
        ],
        "workers_cannot_mutate_c1": True,
        "oracle_expected_authority_is_not_policy_visible": True,
        "c1_commit_is_authoritative_not_policy_decision": True,
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S3_E1_COHORT_ID,
        trial_id=scenario.scenario_id,
        operation_id=f"binding:{S3_E0_SUBJECT_ID}",
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

    return BindingE1Trial(
        policy_id=policy_id,
        scenario_id=scenario.scenario_id,
        evaluation=evaluation,
        policy_migration_decisions=tuple(decisions),
        presentation=presentation,
        physical_events=tuple(physical_events),
        worker_process_ids=worker_pids,
        expected_binding_id=scenario.expected_binding_id,
        expected_epoch=scenario.expected_epoch,
        final_binding_id=actual_binding_id,
        final_epoch=actual_epoch,
        injected_divergence=inject_divergence,
    )


def _run_case(
    scenario: BindingE1Scenario,
    policy_ids: tuple[PolicyID, ...],
    *,
    inject_divergence: bool = False,
) -> tuple[BindingE1Trial, ...]:
    ctx, handles, _ = _spawn_workers()
    try:
        return tuple(
            _execute_trial(
                ctx,
                handles,
                scenario,
                policy_id,
                inject_divergence=inject_divergence,
            )
            for policy_id in policy_ids
        )
    finally:
        _stop_workers(handles)


def run_s3_e1_trial(policy_id: PolicyID, scenario_id: str) -> BindingE1Trial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    scenario = _SPEC_BY_ID.get(scenario_id)
    if scenario is None:
        raise ValueError(f"scenario_id must be one of {S3_E1_SCENARIO_IDS!r}")
    return _run_case(scenario, (policy_id,))[0]


def run_s3_e1_paired() -> BindingE1Evaluation:
    trials: list[BindingE1Trial] = []
    for scenario in S3_E1_SCENARIOS:
        trials.extend(_run_case(scenario, tuple(PolicyID)))
    frozen = tuple(trials)
    return BindingE1Evaluation(
        trials=frozen,
        summary=summarize_correctness(tuple(item.evaluation for item in frozen)),
    )
