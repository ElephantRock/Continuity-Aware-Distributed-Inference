from __future__ import annotations

import math

import pytest

from continuity import ContinuityCore
from continuity.entities import AttemptAuthority, ExecutionStatus
from simulator import ContinuityAdapter, DiscreteEventSimulator, EventKind, ResourceModel
from simulator.fault_linkage import (
    CrossLayerFaultInjector,
    FaultOutcomeClass,
    FaultOutcomeLinker,
)
from simulator.faults import FaultClass
from simulator.resources import ReplicaRuntimeStatus, WorkerStatus


def _scaffold_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    return core


def _running_a1():
    sim = DiscreteEventSimulator(seed=31)
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    sim.run(until=1)
    assert core.requests["r"].current_attempt_id == "a1"
    return sim, core, adapter


def _superseded_a1():
    sim, core, adapter = _running_a1()
    faults = CrossLayerFaultInjector(sim)
    faults.inject_attempt_timeout(adapter, "r", "a1", "a2", at=2, fault_id="timeout")
    sim.run(until=2)
    assert core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED
    assert core.requests["r"].current_attempt_id == "a2"
    return sim, core, adapter, faults


def test_attempt_timeout_fault_links_to_retry_semantic_outcome():
    sim, core, adapter = _running_a1()
    faults = CrossLayerFaultInjector(sim)
    record = faults.inject_attempt_timeout(
        adapter, "r", "a1", "a2", at=2, fault_id="timeout"
    )
    pending = FaultOutcomeLinker(faults, adapter=adapter).observe("timeout")
    assert pending.outcome_class is FaultOutcomeClass.PENDING

    sim.run()
    outcome = FaultOutcomeLinker(faults, adapter=adapter).observe("timeout")

    assert record.fault_class is FaultClass.ATTEMPT_TIMEOUT
    assert outcome.outcome_class is FaultOutcomeClass.SEMANTIC_APPLIED
    assert outcome.invariant_violations == ()
    assert outcome.request_id == "r"
    assert outcome.authoritative_outcome is not None
    assert outcome.authoritative_outcome.current_attempt_id == "a2"
    assert core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED
    assert any(action.operation == "schedule_retry" for action in outcome.semantic_actions)


def test_timeout_fault_rejects_noncurrent_attempt_without_consuming_fault_id():
    sim, core, adapter, faults = _superseded_a1()
    with pytest.raises(ValueError, match="current Attempt"):
        faults.inject_attempt_timeout(adapter, "r", "a1", "a3", fault_id="later")

    # The failed cross-layer injection must not reserve the FaultID.
    adapter.schedule_attempt_completion("a2", at=3)
    sim.run(until=3)
    record = faults.inject_late_attempt_result(adapter, "a1", at=4, fault_id="later")
    assert record.id == "later"


def test_late_result_fault_records_succeeded_plus_superseded_without_authority_regain():
    sim, core, adapter, faults = _superseded_a1()
    faults.inject_late_attempt_result(adapter, "a1", at=3, fault_id="late")
    sim.run()

    outcome = FaultOutcomeLinker(faults, adapter=adapter).observe("late")
    assert outcome.outcome_class is FaultOutcomeClass.SEMANTIC_APPLIED
    assert outcome.invariant_violations == ()
    assert core.attempts["a1"].execution_status is ExecutionStatus.SUCCEEDED
    assert core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED
    assert outcome.authoritative_outcome is not None
    assert outcome.authoritative_outcome.current_attempt_id == "a2"


def test_late_result_requires_superseded_attempt_at_injection_time():
    sim, _, adapter = _running_a1()
    faults = CrossLayerFaultInjector(sim)
    with pytest.raises(ValueError, match="SUPERSEDED"):
        faults.inject_late_attempt_result(adapter, "a1", fault_id="late")
    assert faults.records == ()


def test_stale_superseded_terminal_observation_is_linked_to_explicit_rejection():
    sim, core, adapter, faults = _superseded_a1()
    faults.inject_late_attempt_result(adapter, "a1", at=3, fault_id="late")
    sim.run(until=3)
    assert core.attempts["a1"].execution_status is ExecutionStatus.SUCCEEDED

    faults.inject_stale_attempt_observation(
        adapter,
        "r",
        "a1",
        "e-stale",
        "o-stale",
        at=4,
        observed_at=3,
        fault_id="stale-observation",
    )
    sim.run()

    outcome = FaultOutcomeLinker(faults, adapter=adapter).observe("stale-observation")
    assert outcome.outcome_class is FaultOutcomeClass.SEMANTIC_REJECTED
    assert outcome.semantic_error is not None
    assert outcome.invariant_violations == ()
    assert outcome.authoritative_outcome is not None
    assert outcome.authoritative_outcome.current_attempt_id == "a2"
    assert outcome.authoritative_outcome.committed_attempt_id is None
    assert core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED


def test_stale_observation_injection_requires_succeeded_superseded_attempt():
    sim, _, adapter, faults = _superseded_a1()
    with pytest.raises(ValueError, match="SUCCEEDED"):
        faults.inject_stale_attempt_observation(
            adapter, "r", "a1", "e", "o", fault_id="stale"
        )
    assert "stale" not in {record.id for record in faults.records}


def test_replica_eviction_links_physical_effect_without_semantic_claim():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    resources.materialize_replica("rep", "state", "w1", size_bytes=8, duration=0)
    sim.run()
    faults = CrossLayerFaultInjector(sim, resources)

    record = faults.evict_replica("rep", fault_id="evict")
    sim.run()
    outcome = FaultOutcomeLinker(faults, resources=resources).observe("evict")

    assert record.fault_class is FaultClass.REPLICA_EVICTION
    assert resources.replicas["rep"].status is ReplicaRuntimeStatus.EVICTED
    assert outcome.outcome_class is FaultOutcomeClass.PHYSICAL_EFFECT
    assert outcome.physical_summary == (("replica_status", "EVICTED"),)
    assert outcome.authoritative_outcome is None
    faults.assert_ground_truth()


def test_worker_failure_from_base_injector_links_physical_effect():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    faults = CrossLayerFaultInjector(sim, resources)
    faults.fail_worker("w1", fault_id="down")
    sim.run()

    outcome = FaultOutcomeLinker(faults).observe("down")
    assert resources.workers["w1"].status is WorkerStatus.DOWN
    assert outcome.outcome_class is FaultOutcomeClass.PHYSICAL_EFFECT
    assert outcome.physical_summary == (("worker_status", "DOWN"),)


def test_dropped_delivery_links_to_explicit_suppression():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=2, event_id="completion")
    faults = CrossLayerFaultInjector(sim)
    faults.drop_delivery("completion", fault_id="drop")
    sim.run()

    outcome = FaultOutcomeLinker(faults).observe("drop")
    assert outcome.outcome_class is FaultOutcomeClass.DELIVERY_SUPPRESSED
    assert outcome.semantic_actions == ()
    assert outcome.invariant_violations == ()


def test_outcome_linker_preserves_external_policy_and_recovery_annotations():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    faults = CrossLayerFaultInjector(sim)
    faults.drop_delivery("e", fault_id="f")
    sim.run()

    outcome = FaultOutcomeLinker(faults).observe(
        "f", policy="test-policy", recovery_action="RETRY", recovery_latency=2.5
    )
    assert outcome.policy == "test-policy"
    assert outcome.recovery_action == "RETRY"
    assert outcome.recovery_latency == 2.5


def test_outcome_linker_rejects_invalid_recovery_latency():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    faults = CrossLayerFaultInjector(sim)
    faults.drop_delivery("e", fault_id="f")
    linker = FaultOutcomeLinker(faults)

    with pytest.raises(ValueError, match="non-negative"):
        linker.observe("f", recovery_latency=-1)
    with pytest.raises(TypeError, match="numeric"):
        linker.observe("f", recovery_latency="slow")


def test_outcome_linker_requires_same_simulator_for_adapter_and_resources():
    sim = DiscreteEventSimulator()
    faults = CrossLayerFaultInjector(sim)
    other = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(other, core)
    resources = ResourceModel(other)

    with pytest.raises(ValueError, match="injector simulator"):
        FaultOutcomeLinker(faults, adapter=adapter)
    with pytest.raises(ValueError, match="injector simulator"):
        FaultOutcomeLinker(faults, resources=resources)


def test_unknown_fault_id_is_explicit():
    sim = DiscreteEventSimulator()
    faults = CrossLayerFaultInjector(sim)
    with pytest.raises(KeyError, match="unknown fault_id"):
        FaultOutcomeLinker(faults).observe("missing")


def test_cross_layer_injector_requires_same_adapter_simulator():
    sim = DiscreteEventSimulator()
    faults = CrossLayerFaultInjector(sim)
    other = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(other, core)
    with pytest.raises(ValueError, match="same simulator"):
        faults.inject_attempt_timeout(adapter, "r", "a1", "a2")


def test_recovery_latency_must_not_be_nonfinite():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    faults = CrossLayerFaultInjector(sim)
    faults.drop_delivery("e", fault_id="f")
    linker = FaultOutcomeLinker(faults)
    # This test intentionally states the trust-boundary requirement; implementation
    # review must ensure non-finite metadata is rejected before C2.4.2 closes.
    for value in (math.inf, -math.inf, math.nan):
        with pytest.raises(ValueError):
            linker.observe("f", recovery_latency=value)
