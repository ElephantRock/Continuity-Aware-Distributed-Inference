from continuity import ContinuityCore
from simulator import (
    DiscreteEventSimulator,
    EventKind,
    ReplicaRuntimeStatus,
    ResourceModel,
    TaskStatus,
    TransferStatus,
    WorkerStatus,
)


def _resources(seed=0):
    sim = DiscreteEventSimulator(seed=seed)
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    resources.add_worker("w2")
    return sim, resources


def test_worker_queue_is_fifo_and_service_time_is_deterministic():
    sim, resources = _resources()
    resources.enqueue_task("w1", "t1", duration=2)
    resources.enqueue_task("w1", "t2", duration=1)

    sim.run()

    assert resources.tasks["t1"].status is TaskStatus.COMPLETED
    assert resources.tasks["t1"].started_at == 0
    assert resources.tasks["t1"].completed_at == 2
    assert resources.tasks["t2"].status is TaskStatus.COMPLETED
    assert resources.tasks["t2"].started_at == 2
    assert resources.tasks["t2"].completed_at == 3
    assert resources.worker_queues["w1"] == []
    assert resources.worker_active["w1"] == []


def test_worker_capacity_allows_deterministic_concurrency():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w", capacity=2)
    resources.enqueue_task("w", "t1", duration=2)
    resources.enqueue_task("w", "t2", duration=1)
    resources.enqueue_task("w", "t3", duration=1)

    sim.run()

    assert resources.tasks["t1"].started_at == 0
    assert resources.tasks["t2"].started_at == 0
    assert resources.tasks["t3"].started_at == 1
    assert resources.tasks["t1"].completed_at == 2
    assert resources.tasks["t3"].completed_at == 2


def test_worker_failure_fails_active_work_and_recovery_resumes_queue():
    sim, resources = _resources()
    resources.enqueue_task("w1", "active", duration=5)
    resources.enqueue_task("w1", "queued", duration=1)
    sim.run(max_events=2)

    assert resources.tasks["active"].status is TaskStatus.RUNNING
    assert resources.tasks["queued"].status is TaskStatus.QUEUED

    resources.fail_worker("w1")
    sim.run_next()

    assert resources.workers["w1"].status is WorkerStatus.DOWN
    assert resources.tasks["active"].status is TaskStatus.FAILED
    assert resources.tasks["queued"].status is TaskStatus.QUEUED

    resources.recover_worker("w1")
    sim.run()

    assert resources.workers["w1"].status is WorkerStatus.UP
    assert resources.tasks["queued"].status is TaskStatus.COMPLETED
    assert resources.tasks["queued"].started_at == 0
    assert resources.tasks["queued"].completed_at == 1
    assert all(event.event_id != "task-complete:active" for event in sim.trace)


def test_directed_network_link_duration_is_latency_plus_serialization():
    _, resources = _resources()
    link = resources.add_link(
        "l12",
        "w1",
        "w2",
        latency=1.5,
        bandwidth_bytes_per_time=100,
    )
    assert link.transfer_duration(250) == 4.0


def test_replica_materialization_has_explicit_completion_time():
    sim, resources = _resources()
    resources.materialize_replica("rp", "state", "w1", size_bytes=100, duration=3)

    sim.run(until=2)
    assert resources.replicas["rp"].status is ReplicaRuntimeStatus.MATERIALIZING

    sim.run()
    assert resources.replicas["rp"].status is ReplicaRuntimeStatus.AVAILABLE
    assert sim.now == 3
    assert [event.kind for event in sim.trace].count(EventKind.STATE_MATERIALIZED) == 1


def test_worker_failure_during_materialization_loses_runtime_replica():
    sim, resources = _resources()
    resources.materialize_replica("rp", "state", "w1", size_bytes=100, duration=5)
    sim.run_next()
    resources.fail_worker("w1")
    sim.run()

    assert resources.replicas["rp"].status is ReplicaRuntimeStatus.LOST
    assert all(event.event_id != "materialize-complete:rp" for event in sim.trace)
    assert any(event.kind is EventKind.STATE_LOST for event in sim.trace)


def test_state_transfer_moves_runtime_replica_after_link_delay():
    sim, resources = _resources()
    resources.add_link("l12", "w1", "w2", latency=1, bandwidth_bytes_per_time=100)
    resources.materialize_replica("rp", "state", "w1", size_bytes=200, duration=0)
    sim.run()

    resources.start_transfer("tx", "rp", "l12")
    sim.run()

    assert resources.transfers["tx"].status is TransferStatus.COMPLETED
    assert resources.transfers["tx"].started_at == 0
    assert resources.transfers["tx"].completed_at == 3
    assert resources.replicas["rp"].location_id == "w2"
    assert resources.replicas["rp"].status is ReplicaRuntimeStatus.AVAILABLE
    assert any(event.kind is EventKind.STATE_MOVED for event in sim.trace)


def test_destination_failure_aborts_transfer_but_preserves_source_replica():
    sim, resources = _resources()
    resources.add_link("l12", "w1", "w2", latency=1, bandwidth_bytes_per_time=100)
    resources.materialize_replica("rp", "state", "w1", size_bytes=200, duration=0)
    sim.run()
    resources.start_transfer("tx", "rp", "l12")
    sim.run_next()

    resources.fail_worker("w2")
    sim.run()

    assert resources.transfers["tx"].status is TransferStatus.FAILED
    assert resources.replicas["rp"].status is ReplicaRuntimeStatus.AVAILABLE
    assert resources.replicas["rp"].location_id == "w1"
    assert any(event.kind is EventKind.STATE_TRANSFER_FAILED for event in sim.trace)


def test_source_failure_aborts_transfer_and_loses_source_replica():
    sim, resources = _resources()
    resources.add_link("l12", "w1", "w2", latency=1, bandwidth_bytes_per_time=100)
    resources.materialize_replica("rp", "state", "w1", size_bytes=200, duration=0)
    sim.run()
    resources.start_transfer("tx", "rp", "l12")
    sim.run_next()

    resources.fail_worker("w1")
    sim.run()

    assert resources.transfers["tx"].status is TransferStatus.FAILED
    assert resources.replicas["rp"].status is ReplicaRuntimeStatus.LOST


def test_eviction_changes_only_physical_runtime_shadow_not_c1_semantics():
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_state("state", origin_type="continuation", origin_id="c")
    core.add_replica("rp", "state", "w1")
    semantic_state_before = core.states["state"]
    semantic_replica_before = core.replicas["rp"]

    sim, resources = _resources()
    resources.materialize_replica("rp", "state", "w1", size_bytes=10, duration=0)
    sim.run()
    resources.evict_replica("rp")
    sim.run()

    assert resources.replicas["rp"].status is ReplicaRuntimeStatus.EVICTED
    assert core.states["state"] == semantic_state_before
    assert core.replicas["rp"] == semantic_replica_before


def test_same_seed_and_resource_scenario_produce_same_trace_and_state():
    def run(seed):
        sim, resources = _resources(seed)
        resources.add_link("l12", "w1", "w2", latency=0.5, bandwidth_bytes_per_time=100)
        resources.enqueue_task("w1", "task", duration=sim.random_uniform(1, 2))
        resources.materialize_replica(
            "rp",
            "state",
            "w1",
            size_bytes=100,
            duration=sim.random_uniform(0, 1),
        )
        sim.run()
        resources.start_transfer("tx", "rp", "l12")
        sim.run()
        return sim.trace, resources.tasks, resources.replicas, resources.transfers

    assert run(17) == run(17)
