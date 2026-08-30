from __future__ import annotations

import pytest

from simulator import DiscreteEventSimulator, EventKind, ResourceModel
from simulator.faults import FaultClass, FaultInjector
from simulator.resources import ReplicaRuntimeStatus, WorkerStatus


def test_delay_delivery_cancels_original_and_reschedules_equivalent_event():
    sim = DiscreteEventSimulator()
    seen = []
    sim.register_handler(EventKind.ATTEMPT_COMPLETED, lambda _sim, event: seen.append((event.event_id, event.time)))
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=2, event_id="original", payload={"attempt_id": "a1"})
    faults = FaultInjector(sim)

    record = faults.delay_delivery("original", 3, fault_id="f-delay")
    sim.run()

    assert record.fault_class is FaultClass.DELIVERY_DELAY
    assert record.cancelled_event_ids == ("original",)
    assert seen == [("fault:f-delay:delayed:original", 5.0)]
    assert "original" not in {event.event_id for event in sim.trace}
    faults.assert_ground_truth()


def test_drop_delivery_prevents_execution():
    sim = DiscreteEventSimulator()
    seen = []
    sim.register_handler(EventKind.ATTEMPT_TIMEOUT, lambda _sim, event: seen.append(event.event_id))
    sim.schedule(EventKind.ATTEMPT_TIMEOUT, at=1, event_id="timeout")
    faults = FaultInjector(sim)

    record = faults.drop_delivery("timeout", fault_id="f-drop")
    sim.run()

    assert record.ground_truth_effect == "pending delivery cancelled and not replaced"
    assert seen == []
    faults.assert_ground_truth()


def test_duplicate_observation_uses_explicit_duplicate_event_kind():
    sim = DiscreteEventSimulator()
    seen = []
    sim.register_handler(EventKind.OBSERVATION_CREATED, lambda _sim, event: seen.append(event.kind))
    sim.register_handler(EventKind.OBSERVATION_DUPLICATED, lambda _sim, event: seen.append(event.kind))
    sim.schedule(
        EventKind.OBSERVATION_CREATED,
        at=2,
        event_id="obs",
        payload={"attempt_id": "a2", "evidence_id": "e2"},
    )
    faults = FaultInjector(sim)

    record = faults.duplicate_delivery("obs", delay=1, fault_id="f-dup")
    sim.run()

    assert seen == [EventKind.OBSERVATION_CREATED, EventKind.OBSERVATION_DUPLICATED]
    assert record.produced_event_ids == ("fault:f-dup:duplicate:obs",)
    faults.assert_ground_truth()


def test_duplicate_non_observation_preserves_event_kind():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="done")
    faults = FaultInjector(sim)

    faults.duplicate_delivery("done", fault_id="dup")

    duplicate = next(event for event in sim.pending_events if event.event_id == "fault:dup:duplicate:done")
    assert duplicate.kind is EventKind.ATTEMPT_COMPLETED


def test_reorder_after_moves_target_after_anchor_in_execution_order():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="target")
    sim.schedule(EventKind.ATTEMPT_TIMEOUT, at=2, event_id="anchor")
    faults = FaultInjector(sim)

    record = faults.reorder_after("target", "anchor", fault_id="f-order")
    sim.run()

    ids = [event.event_id for event in sim.trace]
    assert ids == ["anchor", "fault:f-order:reordered:target"]
    assert record.duration == 1.0
    faults.assert_ground_truth()


def test_same_time_reorder_uses_insertion_sequence_to_follow_anchor():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=3, event_id="target")
    sim.schedule(EventKind.ATTEMPT_TIMEOUT, at=3, event_id="anchor")
    faults = FaultInjector(sim)

    faults.reorder_after("target", "anchor", fault_id="f-order")
    sim.run()

    assert [event.event_id for event in sim.trace] == ["anchor", "fault:f-order:reordered:target"]
    faults.assert_ground_truth()


def test_fault_ids_are_unique_and_duplicate_fault_id_does_not_mutate_target():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e1")
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=2, event_id="e2")
    faults = FaultInjector(sim)
    faults.drop_delivery("e1", fault_id="same")

    with pytest.raises(ValueError, match="duplicate fault_id"):
        faults.drop_delivery("e2", fault_id="same")

    assert "e2" in {event.event_id for event in sim.pending_events}


def test_fault_requires_pending_delivery():
    sim = DiscreteEventSimulator()
    faults = FaultInjector(sim)
    with pytest.raises(ValueError, match="not pending"):
        faults.drop_delivery("missing")


def test_probabilistic_generator_records_no_fault_decision():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    faults = FaultInjector(sim, seed=7)

    assert faults.probabilistic_delivery_fault("e", {}) is None
    assert len(faults.decisions) == 1
    decision = faults.decisions[0]
    assert decision.seed == 7
    assert decision.selected_fault_class is None
    assert decision.fault_id is None
    assert "e" in {event.event_id for event in sim.pending_events}


def test_probabilistic_delay_is_same_seed_reproducible():
    def build():
        sim = DiscreteEventSimulator()
        sim.schedule(EventKind.ATTEMPT_COMPLETED, at=2, event_id="e")
        injector = FaultInjector(sim, seed=123)
        record = injector.probabilistic_delivery_fault(
            "e", {FaultClass.DELIVERY_DELAY: 1.0}, max_delay=5.0
        )
        return injector, record

    first, first_record = build()
    second, second_record = build()

    assert first.decisions == second.decisions
    assert first_record == second_record
    assert first_record is not None
    assert first_record.seed == 123
    assert 0 <= first_record.duration <= 5


def test_probabilistic_configuration_rejects_invalid_probabilities():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    faults = FaultInjector(sim)

    with pytest.raises(ValueError, match="sum to at most 1"):
        faults.probabilistic_delivery_fault(
            "e",
            {FaultClass.DELIVERY_DROP: 0.7, FaultClass.DELIVERY_DUPLICATE: 0.7},
        )
    with pytest.raises(ValueError, match="unsupported"):
        faults.probabilistic_delivery_fault("e", {FaultClass.WORKER_FAILURE: 0.5})
    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        faults.probabilistic_delivery_fault("e", {FaultClass.DELIVERY_DROP: -0.1})


def test_worker_failure_delegates_to_resource_model_and_ground_truth_oracle():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    faults = FaultInjector(sim, resources)

    record = faults.fail_worker("w1", fault_id="worker-down")
    sim.run()

    assert record.fault_class is FaultClass.WORKER_FAILURE
    assert resources.workers["w1"].status is WorkerStatus.DOWN
    faults.assert_ground_truth()


def test_replica_loss_delegates_to_resource_model_and_ground_truth_oracle():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    resources.materialize_replica("rep1", "state1", "w1", size_bytes=10, duration=0)
    sim.run()
    assert resources.replicas["rep1"].status is ReplicaRuntimeStatus.AVAILABLE
    faults = FaultInjector(sim, resources)

    record = faults.lose_replica("rep1", fault_id="replica-lost")
    sim.run()

    assert record.fault_class is FaultClass.REPLICA_LOSS
    assert resources.replicas["rep1"].status is ReplicaRuntimeStatus.LOST
    faults.assert_ground_truth()


def test_resource_faults_require_matching_resource_model():
    sim = DiscreteEventSimulator()
    faults = FaultInjector(sim)
    with pytest.raises(ValueError, match="requires ResourceModel"):
        faults.fail_worker("w1")

    other = DiscreteEventSimulator()
    resources = ResourceModel(other)
    with pytest.raises(ValueError, match="same simulator"):
        FaultInjector(sim, resources)


def test_ground_truth_can_be_checked_before_fault_events_execute():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=4, event_id="e")
    faults = FaultInjector(sim)
    faults.delay_delivery("e", 2, fault_id="f")

    faults.assert_ground_truth()
    assert [event.event_id for event in sim.pending_events] == ["fault:f:delayed:e"]


def test_rejected_transform_does_not_reserve_explicit_or_automatic_fault_id():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    sim.schedule(EventKind.ATTEMPT_FAILED, at=2, event_id="fault:f:duplicate:e")
    faults = FaultInjector(sim)
    with pytest.raises(ValueError, match="duplicate simulator event_id"):
        faults.duplicate_delivery("e", fault_id="f")
    assert faults.drop_delivery("e", fault_id="f").id == "f"

    sim2 = DiscreteEventSimulator()
    sim2.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    sim2.schedule(
        EventKind.ATTEMPT_FAILED, at=2, event_id="fault:fault-00000000:duplicate:e"
    )
    faults2 = FaultInjector(sim2)
    with pytest.raises(ValueError, match="duplicate simulator event_id"):
        faults2.duplicate_delivery("e")
    assert faults2.drop_delivery("e").id == "fault-00000000"


def test_rejected_probabilistic_transform_restores_rng_state_and_fault_identity():
    def setup(with_collision):
        sim = DiscreteEventSimulator()
        sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
        sim.schedule(EventKind.ATTEMPT_COMPLETED, at=3, event_id="e2")
        if with_collision:
            sim.schedule(
                EventKind.ATTEMPT_FAILED,
                at=2,
                event_id="fault:fault-00000000:duplicate:e",
            )
        return sim, FaultInjector(sim, seed=91)

    _, failed = setup(True)
    with pytest.raises(ValueError, match="duplicate simulator event_id"):
        failed.probabilistic_delivery_fault(
            "e", {FaultClass.DELIVERY_DUPLICATE: 1.0}, max_delay=3
        )
    assert failed.decisions == ()
    assert failed.records == ()

    _, clean = setup(False)
    clean_record = clean.probabilistic_delivery_fault(
        "e2", {FaultClass.DELIVERY_DUPLICATE: 1.0}, max_delay=3
    )
    failed_record = failed.probabilistic_delivery_fault(
        "e2", {FaultClass.DELIVERY_DUPLICATE: 1.0}, max_delay=3
    )
    assert failed_record is not None and clean_record is not None
    assert failed.decisions == clean.decisions
    assert failed_record.id == clean_record.id == "fault-00000000"
    assert failed_record.duration == clean_record.duration


def test_composed_delay_then_drop_preserves_ground_truth_chain():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    faults = FaultInjector(sim)
    delayed = faults.delay_delivery("e", 2, fault_id="delay")
    faults.drop_delivery(delayed.produced_event_ids[0], fault_id="drop")
    sim.run()
    assert sim.trace == ()
    faults.assert_ground_truth()


def test_worker_failure_ground_truth_allows_later_recovery():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    faults = FaultInjector(sim, resources)
    faults.fail_worker("w1", fault_id="down")
    resources.recover_worker("w1")
    sim.run()
    assert resources.workers["w1"].status is WorkerStatus.UP
    faults.assert_ground_truth()
