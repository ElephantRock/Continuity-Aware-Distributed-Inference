from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from continuity.core import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    BindingStatus,
    ContinuationLifecycle,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionContext,
    ReconcileOutcome,
    ReplicaStatus,
    SemanticEvent,
)
from continuity.errors import InsufficientEvidence, InvalidTransition, SemanticViolation
from continuity.invariants import InvariantOracle
from experiments.c83_replay_protocol import C83A_TRACE_SPECS
from simulator.engine import DiscreteEventSimulator
from simulator.events import EventKind, SimEvent
from simulator.faults import FaultInjector
from simulator.semantic_adapter import AdapterOutcome, ContinuityAdapter


@dataclass(frozen=True, slots=True)
class ReplayObservation:
    checkpoint_id: str
    raw_outcome: str
    projection: dict[str, Any]


def _exact(
    core: ContinuityCore,
    evidence_id: str,
    scope: set[tuple[str, str]],
    *,
    authority: EvidenceAuthority = EvidenceAuthority.EXACT_OBSERVATION,
    status: EvidenceStatus = EvidenceStatus.VALID,
    observed_at: float = 10.0,
) -> Evidence:
    evidence = Evidence(
        evidence_id,
        "ok",
        "c8.3-replay",
        authority,
        status,
        observed_at,
        frozenset(scope),
    )
    core.record_evidence(evidence)
    return evidence


def _simple_request(core: ContinuityCore) -> None:
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request("r", "c")


def _finalize_attempt(
    core: ContinuityCore,
    attempt_id: str,
    output_id: str,
    evidence_id: str,
) -> None:
    core.complete_attempt(attempt_id)
    _exact(core, evidence_id, {("attempt", attempt_id)})
    core.create_output(output_id, attempt_id, True, [evidence_id])
    core.finalize_request("r", output_id, now=10.0)


def _x1_projection(core: ContinuityCore, *, stale: bool) -> dict[str, Any]:
    request = core.requests["r"]
    result: dict[str, Any] = {
        "request.committed_attempt_id": request.committed_attempt_id,
        "request.authoritative_output_id": request.authoritative_output_id,
    }
    if stale:
        result["stale_attempt.authority_status"] = core.attempts["a1"].authority_status.value
    return result


def _x2_projection(core: ContinuityCore, finalization_count: int) -> dict[str, Any]:
    return {
        "request.authoritative_output_id": core.requests["r"].authoritative_output_id,
        "attempt.authority_status": core.attempts["a1"].authority_status.value,
        "finalization_count": finalization_count,
    }


def _x3_projection(core: ContinuityCore, *, compatible: bool, consumed: bool) -> dict[str, Any]:
    return {
        "state.origin_continuation_id": core.states["x1"].origin_continuation_id,
        "target.continuation_id": "c2",
        "state_compatible": compatible,
        "state_consumed": consumed,
    }


def _x4_projection(core: ContinuityCore) -> dict[str, Any]:
    return {
        "binding.current_id": core.current_binding_by_subject["subject"],
        "binding.current_epoch": core.current_epoch_by_subject["subject"],
        "old_binding.status": core.bindings["b1"].status.value,
    }


def _x5_projection(core: ContinuityCore, outcome: ReconcileOutcome) -> dict[str, Any]:
    return {
        "binding.current_id": core.current_binding_by_subject["subject"],
        "candidate_binding.status": core.bindings["b2"].status.value,
        "reconcile.outcome": outcome.value,
    }


def _x6_projection(core: ContinuityCore, can_consume: bool) -> dict[str, Any]:
    return {
        "state.origin_continuation_id": core.states["x"].origin_continuation_id,
        "replica.status": core.replicas["rp"].status.value,
        "continuation.lifecycle": core.continuations["c1"].lifecycle.value,
        "can_consume": can_consume,
    }


def _x7_projection(core: ContinuityCore, destination_materialization: str) -> dict[str, Any]:
    return {
        "binding.current_id": core.current_binding_by_subject["state:x"],
        "candidate_binding.status": core.bindings["b2"].status.value,
        "binding.current_epoch": core.current_epoch_by_subject["state:x"],
        "destination_materialization": destination_materialization,
    }


def _assert_projection(trace_id: str, observation: ReplayObservation) -> ReplayObservation:
    spec = next(item for item in C83A_TRACE_SPECS if item.trace_id == trace_id)
    checkpoint = next(
        item for item in spec.checkpoints if item.checkpoint_id == observation.checkpoint_id
    )
    expected = set(checkpoint.semantic_projection_fields)
    actual = set(observation.projection)
    if actual != expected:
        raise AssertionError(
            f"projection mismatch for {trace_id}/{observation.checkpoint_id}: "
            f"expected={sorted(expected)}, actual={sorted(actual)}"
        )
    InvariantOracle
    return observation


def _run_c1_x1() -> tuple[ReplayObservation, ...]:
    core = ContinuityCore()
    _simple_request(core)
    core.start_attempt("a1", "r")
    core.start_attempt("a2", "r")
    _finalize_attempt(core, "a2", "o2", "e2")
    current = ReplayObservation(
        "current-attempt-finalize",
        "CURRENT_ATTEMPT_FINALIZED"
        if core.requests["r"].committed_attempt_id == "a2"
        else "SEMANTIC_CHECKPOINT_FAILED",
        _x1_projection(core, stale=False),
    )
    core.complete_attempt("a1")
    _exact(core, "e1", {("attempt", "a1")})
    core.create_output("o1", "a1", True, ["e1"])
    stale_rejected = False
    try:
        core.finalize_request("r", "o1", now=10.0)
    except (InvalidTransition, SemanticViolation):
        stale_rejected = True
    stale_committed = core.requests["r"].committed_attempt_id == "a1"
    stale_raw = (
        "STALE_ATTEMPT_COMMITTED"
        if stale_committed
        else "INVALID_TRANSITION_STALE_ATTEMPT"
        if stale_rejected
        else "SEMANTIC_CHECKPOINT_FAILED"
    )
    stale = ReplayObservation(
        "stale-attempt-presentation",
        stale_raw,
        _x1_projection(core, stale=True),
    )
    InvariantOracle(core).assert_all()
    return current, stale


def _run_c1_x2() -> tuple[ReplayObservation, ...]:
    core = ContinuityCore()
    _simple_request(core)
    core.start_attempt("a1", "r")
    _finalize_attempt(core, "a1", "o1", "e1")
    finalization_count = 1
    first = ReplayObservation(
        "first-finalization",
        "FIRST_FINALIZATION"
        if core.requests["r"].authoritative_output_id == "o1"
        else "SEMANTIC_CHECKPOINT_FAILED",
        _x2_projection(core, finalization_count),
    )
    before = core.requests["r"]
    second = core.finalize_request("r", "o1", now=10.0)
    duplicate_changed = second != before or core.requests["r"].authoritative_output_id != "o1"
    if duplicate_changed:
        finalization_count += 1
    duplicate = ReplayObservation(
        "duplicate-presentation",
        "DUPLICATE_FINALIZATION" if duplicate_changed else "IDEMPOTENT_FINALIZE",
        _x2_projection(core, finalization_count),
    )
    InvariantOracle(core).assert_all()
    return first, duplicate


def _setup_sibling_state() -> tuple[ContinuityCore, ExecutionContext]:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c0", "s")
    core.create_continuation("c1", "s", ["c0"])
    core.create_continuation("c2", "s", ["c0"])
    core.create_state("x1", origin_type="continuation", origin_id="c1")
    core.create_request("r2", "c2")
    core.start_attempt("a2", "r2")
    return core, ExecutionContext("p", "s", "c2", "r2", "a2")


def _run_c1_x3() -> tuple[ReplayObservation, ...]:
    core, ctx = _setup_sibling_state()
    compatible = core.state_compatible("x1", ctx)
    consumed = compatible
    raw = "WRONG_SIBLING_STATE_CONSUMED" if consumed else "STATE_INCOMPATIBLE"
    InvariantOracle(core).assert_all()
    return (
        ReplayObservation(
            "incompatible-state-consume",
            raw,
            _x3_projection(core, compatible=compatible, consumed=consumed),
        ),
    )


def _setup_binding_replacement() -> ContinuityCore:
    core = ContinuityCore()
    core.activate_initial_binding("b1", "subject", "w1")
    candidate = core.propose_binding("b2", "subject", "w2")
    core.begin_migration("b2")
    _exact(core, "e2", {("binding", "b2"), ("epoch", str(candidate.epoch))})
    return core


def _run_c1_x4() -> tuple[ReplayObservation, ...]:
    core = _setup_binding_replacement()
    core.commit_migration("b2", ["e2"], now=10.0)
    replacement = ReplayObservation(
        "replacement-binding-commit",
        "NEW_BINDING_COMMITTED"
        if core.current_binding_by_subject["subject"] == "b2"
        else "SEMANTIC_CHECKPOINT_FAILED",
        _x4_projection(core),
    )
    epoch = core.bindings["b1"].epoch
    core.record_event(
        SemanticEvent(
            "late-old-owner",
            "BINDING_ACTIVE_OBSERVED",
            "binding",
            "b1",
            frozenset({("epoch", str(epoch)), ("location", "w1")}),
        )
    )
    stale_restored = core.current_binding_by_subject["subject"] == "b1"
    stale = ReplayObservation(
        "stale-binding-presentation",
        "STALE_BINDING_RESTORED"
        if stale_restored
        else "STALE_EVENT_RECORDED_NO_AUTHORITY_CHANGE",
        _x4_projection(core),
    )
    InvariantOracle(core).assert_all()
    return replacement, stale


def _setup_ambiguous_binding() -> ContinuityCore:
    core = ContinuityCore()
    core.activate_initial_binding("b1", "subject", "w1")
    candidate = core.propose_binding("b2", "subject", "w2")
    core.begin_migration("b2")
    _exact(
        core,
        "ambiguous",
        {("binding", "b2"), ("epoch", str(candidate.epoch))},
        authority=EvidenceAuthority.AUTHORITATIVE,
        status=EvidenceStatus.AMBIGUOUS,
    )
    return core


def _run_c1_x5() -> tuple[ReplayObservation, ...]:
    core = _setup_ambiguous_binding()
    candidate = core.bindings["b2"]
    outcome = core.reconcile(
        "commit_migration",
        ["ambiguous"],
        now=10.0,
        required_scope={("binding", "b2"), ("epoch", str(candidate.epoch))},
    )
    committed = core.current_binding_by_subject["subject"] == "b2"
    raw = (
        "AMBIGUOUS_OWNERSHIP_COMMITTED"
        if committed
        else "RECONCILE_AMBIGUOUS"
        if outcome is ReconcileOutcome.AMBIGUOUS
        else "SEMANTIC_CHECKPOINT_FAILED"
    )
    InvariantOracle(core).assert_all()
    return (ReplayObservation("ambiguous-reconciliation", raw, _x5_projection(core, outcome)),)


def _setup_waiting_state() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c1", "s")
    core.create_state("x", origin_type="continuation", origin_id="c1")
    core.add_replica("rp", "x", "w1")
    core.set_continuation_lifecycle("c1", ContinuationLifecycle.WAITING)
    return core


def _finish_waiting_state(core: ContinuityCore) -> ReplayObservation:
    core.resume_after_wait("c1", "c2")
    core.create_request("r2", "c2")
    core.start_attempt("a2", "r2")
    ctx = ExecutionContext("p", "s", "c2", "r2", "a2")
    can_consume = core.can_consume_state("x", "rp", ctx, [], now=10.0)
    raw = "LOST_REPLICA_CONSUMED" if can_consume else "CAN_CONSUME_FALSE"
    return ReplayObservation("resume-after-eviction", raw, _x6_projection(core, can_consume))


def _run_c1_x6() -> tuple[ReplayObservation, ...]:
    core = _setup_waiting_state()
    core.set_replica_status("rp", ReplicaStatus.LOST)
    result = _finish_waiting_state(core)
    InvariantOracle(core).assert_all()
    return (result,)


def _setup_partial_migration() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_state("x", origin_type="continuation", origin_id="c")
    core.add_replica("src", "x", "w1")
    core.add_replica("dst", "x", "w2", status=ReplicaStatus.MATERIALIZING)
    core.activate_initial_binding("b1", "state:x", "w1")
    core.propose_binding("b2", "state:x", "w2")
    core.begin_migration("b2")
    return core


def _finish_partial_migration(core: ContinuityCore, materialization: str) -> ReplayObservation:
    insufficient = False
    try:
        core.commit_migration("b2", [], now=10.0)
    except InsufficientEvidence:
        insufficient = True
    committed = core.current_binding_by_subject["state:x"] == "b2"
    raw = (
        "PARTIAL_DESTINATION_COMMITTED"
        if committed
        else "INSUFFICIENT_EVIDENCE"
        if insufficient
        else "SEMANTIC_CHECKPOINT_FAILED"
    )
    return ReplayObservation(
        "partial-migration-commit",
        raw,
        _x7_projection(core, materialization),
    )


def _run_c1_x7() -> tuple[ReplayObservation, ...]:
    core = _setup_partial_migration()
    result = _finish_partial_migration(core, "PARTIAL")
    InvariantOracle(core).assert_all()
    return (result,)


_C1_RUNNERS: dict[str, Callable[[], tuple[ReplayObservation, ...]]] = {
    "C8-X1-LATE-SUPERSEDED-ATTEMPT": _run_c1_x1,
    "C8-X2-DUPLICATE-COMPLETION": _run_c1_x2,
    "C8-X3-WRONG-SIBLING-STATE": _run_c1_x3,
    "C8-X4-STALE-BINDING": _run_c1_x4,
    "C8-X5-AMBIGUOUS-OWNERSHIP": _run_c1_x5,
    "C8-X6-STATE-EVICTION-TOOL-WAIT": _run_c1_x6,
    "C8-X7-PARTIAL-MIGRATION": _run_c1_x7,
}


def run_c1_trace(trace_id: str) -> tuple[ReplayObservation, ...]:
    try:
        result = _C1_RUNNERS[trace_id]()
    except KeyError as exc:
        raise ValueError(f"unknown C8.3 trace: {trace_id}") from exc
    return tuple(_assert_projection(trace_id, item) for item in result)


def _run_c2_x1() -> tuple[ReplayObservation, ...]:
    sim = DiscreteEventSimulator(seed=0)
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0.0, event_id="x1-request")
    adapter.schedule_attempt_start("r", "a1", at=1.0, event_id="x1-a1-start")
    adapter.schedule_attempt_start("r", "a2", at=2.0, event_id="x1-a2-start")
    adapter.schedule_attempt_completion("a1", at=2.5, late=True, event_id="x1-a1-late")
    adapter.schedule_attempt_completion("a2", at=3.0, event_id="x1-a2-complete")
    adapter.schedule_observation(
        "r", "a2", "e2", "o2", at=4.0, observed_at=3.0, event_id="x1-a2-observe"
    )
    adapter.schedule_observation(
        "r", "a1", "e1", "o1", at=6.0, observed_at=5.0, event_id="x1-a1-observe"
    )
    faults = FaultInjector(sim, seed=0)
    faults.reorder_after("x1-a1-late", "x1-a2-observe", gap=1.0, fault_id="x1-reorder")
    sim.run()
    current_ok = (
        core.requests["r"].committed_attempt_id == "a2"
        and core.requests["r"].authoritative_output_id == "o2"
    )
    stale_finalize = [
        record
        for record in adapter.records
        if record.event_id == "x1-a1-observe" and record.operation == "finalize_request"
    ]
    stale_rejected = bool(stale_finalize) and stale_finalize[-1].outcome is AdapterOutcome.REJECTED
    stale_committed = core.requests["r"].committed_attempt_id == "a1"
    InvariantOracle(core).assert_all()
    return (
        ReplayObservation(
            "current-attempt-finalize",
            "CURRENT_ATTEMPT_COMMITTED" if current_ok else "SEMANTIC_CHECKPOINT_FAILED",
            _x1_projection(core, stale=False),
        ),
        ReplayObservation(
            "stale-attempt-presentation",
            "STALE_ATTEMPT_COMMITTED"
            if stale_committed
            else "IGNORE_STALE"
            if stale_rejected
            else "SEMANTIC_CHECKPOINT_FAILED",
            _x1_projection(core, stale=True),
        ),
    )


def _run_c2_x2() -> tuple[ReplayObservation, ...]:
    sim = DiscreteEventSimulator(seed=0)
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0.0, event_id="x2-request")
    adapter.schedule_attempt_start("r", "a1", at=1.0, event_id="x2-a1-start")
    adapter.schedule_attempt_completion("a1", at=2.0, event_id="x2-a1-complete")
    adapter.schedule_observation(
        "r", "a1", "e1", "o1", at=3.0, observed_at=2.0, event_id="x2-observe"
    )
    faults = FaultInjector(sim, seed=0)
    faults.duplicate_delivery("x2-observe", fault_id="x2-duplicate")
    sim.run()
    finalizations = [record for record in adapter.records if record.operation == "finalize_request"]
    applied = sum(record.outcome is AdapterOutcome.APPLIED for record in finalizations)
    idempotent = sum(record.outcome is AdapterOutcome.IDEMPOTENT for record in finalizations)
    duplicate_violation = applied > 1 or core.requests["r"].authoritative_output_id != "o1"
    finalization_count = applied
    InvariantOracle(core).assert_all()
    return (
        ReplayObservation(
            "first-finalization",
            "FIRST_FINALIZATION" if applied >= 1 else "SEMANTIC_CHECKPOINT_FAILED",
            _x2_projection(core, finalization_count),
        ),
        ReplayObservation(
            "duplicate-presentation",
            "DUPLICATE_FINALIZATION"
            if duplicate_violation
            else "IGNORE_DUPLICATE"
            if idempotent >= 1
            else "SEMANTIC_CHECKPOINT_FAILED",
            _x2_projection(core, finalization_count),
        ),
    )


def _run_c2_x3() -> tuple[ReplayObservation, ...]:
    sim = DiscreteEventSimulator(seed=0)
    core, ctx = _setup_sibling_state()
    result: list[ReplayObservation] = []

    def inspect(_sim: DiscreteEventSimulator, _event: SimEvent) -> None:
        compatible = core.state_compatible("x1", ctx)
        consumed = compatible
        result.append(
            ReplayObservation(
                "incompatible-state-consume",
                "WRONG_SIBLING_STATE_CONSUMED" if consumed else "REJECT_REUSE",
                _x3_projection(core, compatible=compatible, consumed=consumed),
            )
        )

    sim.register_handler(EventKind.STATE_CREATED, inspect)
    sim.schedule(EventKind.STATE_CREATED, at=1.0, event_id="x3-consume")
    sim.run()
    InvariantOracle(core).assert_all()
    return tuple(result)


def _run_c2_x4() -> tuple[ReplayObservation, ...]:
    sim = DiscreteEventSimulator(seed=0)
    core = _setup_binding_replacement()
    result: list[ReplayObservation] = []

    def commit(_sim: DiscreteEventSimulator, _event: SimEvent) -> None:
        core.commit_migration("b2", ["e2"], now=10.0)
        result.append(
            ReplayObservation(
                "replacement-binding-commit",
                "NEW_BINDING_COMMITTED"
                if core.current_binding_by_subject["subject"] == "b2"
                else "SEMANTIC_CHECKPOINT_FAILED",
                _x4_projection(core),
            )
        )

    def stale(_sim: DiscreteEventSimulator, _event: SimEvent) -> None:
        old = core.bindings["b1"]
        core.record_event(
            SemanticEvent(
                "c2-late-old-owner",
                "BINDING_ACTIVE_OBSERVED",
                "binding",
                "b1",
                frozenset({("epoch", str(old.epoch)), ("location", "w1")}),
            )
        )
        restored = core.current_binding_by_subject["subject"] == "b1"
        result.append(
            ReplayObservation(
                "stale-binding-presentation",
                "STALE_BINDING_RESTORED" if restored else "IGNORE_STALE",
                _x4_projection(core),
            )
        )

    sim.register_handler(EventKind.MIGRATION_COMMITTED, commit)
    sim.register_handler(EventKind.OBSERVATION_CREATED, stale)
    sim.schedule(EventKind.OBSERVATION_CREATED, at=1.0, event_id="x4-old-observation")
    sim.schedule(EventKind.MIGRATION_COMMITTED, at=2.0, event_id="x4-new-binding")
    faults = FaultInjector(sim, seed=0)
    faults.reorder_after(
        "x4-old-observation", "x4-new-binding", gap=1.0, fault_id="x4-reorder"
    )
    sim.run()
    InvariantOracle(core).assert_all()
    return tuple(result)


def _run_c2_x5() -> tuple[ReplayObservation, ...]:
    sim = DiscreteEventSimulator(seed=0)
    core = _setup_ambiguous_binding()
    result: list[ReplayObservation] = []

    def reconcile(_sim: DiscreteEventSimulator, _event: SimEvent) -> None:
        candidate = core.bindings["b2"]
        outcome = core.reconcile(
            "commit_migration",
            ["ambiguous"],
            now=10.0,
            required_scope={("binding", "b2"), ("epoch", str(candidate.epoch))},
        )
        committed = core.current_binding_by_subject["subject"] == "b2"
        raw = (
            "AMBIGUOUS_OWNERSHIP_COMMITTED"
            if committed
            else "AMBIGUOUS"
            if outcome is ReconcileOutcome.AMBIGUOUS
            else "SEMANTIC_CHECKPOINT_FAILED"
        )
        result.append(
            ReplayObservation("ambiguous-reconciliation", raw, _x5_projection(core, outcome))
        )

    sim.register_handler(EventKind.OBSERVATION_CREATED, reconcile)
    sim.schedule(EventKind.OBSERVATION_CREATED, at=1.0, event_id="x5-ambiguous")
    sim.run()
    InvariantOracle(core).assert_all()
    return tuple(result)


def _run_c2_x6() -> tuple[ReplayObservation, ...]:
    sim = DiscreteEventSimulator(seed=0)
    core = _setup_waiting_state()
    result: list[ReplayObservation] = []

    def evict(_sim: DiscreteEventSimulator, _event: SimEvent) -> None:
        core.set_replica_status("rp", ReplicaStatus.LOST)

    def resume(_sim: DiscreteEventSimulator, _event: SimEvent) -> None:
        observation = _finish_waiting_state(core)
        raw = (
            "LOST_REPLICA_CONSUMED"
            if observation.raw_outcome == "LOST_REPLICA_CONSUMED"
            else "REJECT_REUSE"
        )
        result.append(
            ReplayObservation(observation.checkpoint_id, raw, observation.projection)
        )

    sim.register_handler(EventKind.STATE_EVICTED, evict)
    sim.register_handler(EventKind.TOOL_RETURNED, resume)
    sim.schedule(EventKind.STATE_EVICTED, at=1.0, event_id="x6-evict")
    sim.schedule(EventKind.TOOL_RETURNED, at=2.0, event_id="x6-resume")
    sim.run()
    InvariantOracle(core).assert_all()
    return tuple(result)


def _run_c2_x7() -> tuple[ReplayObservation, ...]:
    sim = DiscreteEventSimulator(seed=0)
    core = _setup_partial_migration()
    result: list[ReplayObservation] = []
    materialization = {"state": "PARTIAL"}

    def fail_destination(_sim: DiscreteEventSimulator, _event: SimEvent) -> None:
        core.set_replica_status("dst", ReplicaStatus.LOST)

    def commit(_sim: DiscreteEventSimulator, _event: SimEvent) -> None:
        observation = _finish_partial_migration(core, materialization["state"])
        raw = (
            "PARTIAL_DESTINATION_COMMITTED"
            if observation.raw_outcome == "PARTIAL_DESTINATION_COMMITTED"
            else "WAIT"
            if observation.raw_outcome == "INSUFFICIENT_EVIDENCE"
            else "SEMANTIC_CHECKPOINT_FAILED"
        )
        result.append(
            ReplayObservation(observation.checkpoint_id, raw, observation.projection)
        )

    sim.register_handler(EventKind.WORKER_FAILED, fail_destination)
    sim.register_handler(EventKind.MIGRATION_COMMITTED, commit)
    sim.schedule(
        EventKind.STATE_MATERIALIZATION_STARTED,
        at=1.0,
        event_id="x7-materialization-started",
    )
    sim.schedule(EventKind.WORKER_FAILED, at=2.0, event_id="x7-destination-failed")
    sim.schedule(EventKind.MIGRATION_COMMITTED, at=3.0, event_id="x7-commit-attempt")
    sim.run()
    InvariantOracle(core).assert_all()
    return tuple(result)


_C2_RUNNERS: dict[str, Callable[[], tuple[ReplayObservation, ...]]] = {
    "C8-X1-LATE-SUPERSEDED-ATTEMPT": _run_c2_x1,
    "C8-X2-DUPLICATE-COMPLETION": _run_c2_x2,
    "C8-X3-WRONG-SIBLING-STATE": _run_c2_x3,
    "C8-X4-STALE-BINDING": _run_c2_x4,
    "C8-X5-AMBIGUOUS-OWNERSHIP": _run_c2_x5,
    "C8-X6-STATE-EVICTION-TOOL-WAIT": _run_c2_x6,
    "C8-X7-PARTIAL-MIGRATION": _run_c2_x7,
}


def run_c2_trace(trace_id: str) -> tuple[ReplayObservation, ...]:
    try:
        result = _C2_RUNNERS[trace_id]()
    except KeyError as exc:
        raise ValueError(f"unknown C8.3 trace: {trace_id}") from exc
    return tuple(_assert_projection(trace_id, item) for item in result)
