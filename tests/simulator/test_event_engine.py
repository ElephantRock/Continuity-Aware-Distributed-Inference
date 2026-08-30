import math

import pytest

from simulator import DiscreteEventSimulator, EventKind


REQUIRED_C2_EVENT_KINDS = {
    "REQUEST_CREATED",
    "ATTEMPT_STARTED",
    "ATTEMPT_TIMEOUT",
    "ATTEMPT_COMPLETED",
    "ATTEMPT_FAILED",
    "RETRY_STARTED",
    "LATE_RESULT",
    "STATE_CREATED",
    "STATE_MATERIALIZATION_STARTED",
    "STATE_MATERIALIZED",
    "STATE_TRANSFER_STARTED",
    "STATE_TRANSFER_COMPLETED",
    "STATE_MOVED",
    "STATE_EVICTED",
    "STATE_LOST",
    "MIGRATION_STARTED",
    "MIGRATION_COMMITTED",
    "MIGRATION_FAILED",
    "WORKER_FAILED",
    "WORKER_RECOVERED",
    "OBSERVATION_CREATED",
    "OBSERVATION_DELAYED",
    "OBSERVATION_DROPPED",
    "OBSERVATION_DUPLICATED",
    "TOOL_WAIT_STARTED",
    "TOOL_RETURNED",
    "CONTINUATION_FORKED",
    "CONTINUATION_JOINED",
    "CONTINUATION_ABANDONED",
    "CONTINUATION_TERMINATED",
}


def test_required_c2_event_surface_is_explicit():
    assert {kind.value for kind in EventKind} == REQUIRED_C2_EVENT_KINDS


def test_same_time_events_execute_in_insertion_order():
    sim = DiscreteEventSimulator(seed=1)
    first = sim.schedule(EventKind.REQUEST_CREATED, at=3, event_id="first")
    second = sim.schedule(EventKind.ATTEMPT_STARTED, at=3, event_id="second")
    third = sim.schedule(EventKind.STATE_CREATED, at=3, event_id="third")

    assert sim.run() == (first, second, third)
    assert sim.now == 3.0


def test_handler_can_schedule_same_time_event_after_current_event():
    sim = DiscreteEventSimulator()

    def on_request(runtime, _event):
        runtime.schedule(EventKind.ATTEMPT_STARTED, delay=0, event_id="attempt")

    sim.register_handler(EventKind.REQUEST_CREATED, on_request)
    request = sim.schedule(EventKind.REQUEST_CREATED, at=2, event_id="request")
    executed = sim.run()

    assert [event.event_id for event in executed] == ["request", "attempt"]
    assert executed[0] == request
    assert executed[1].time == 2.0
    assert executed[1].sequence > request.sequence


def test_cancelled_event_is_not_delivered_or_traced():
    sim = DiscreteEventSimulator()
    cancelled = sim.schedule(EventKind.WORKER_FAILED, at=1, event_id="failure")
    surviving = sim.schedule(EventKind.WORKER_RECOVERED, at=2, event_id="recovery")

    assert sim.cancel(cancelled.event_id)
    assert not sim.cancel(cancelled.event_id)
    assert sim.pending_events == (surviving,)
    assert sim.run() == (surviving,)
    assert sim.trace == (surviving,)


def test_run_until_stops_before_future_event_and_advances_horizon():
    sim = DiscreteEventSimulator()
    early = sim.schedule(EventKind.TOOL_WAIT_STARTED, at=1)
    late = sim.schedule(EventKind.TOOL_RETURNED, at=5)

    assert sim.run(until=2) == (early,)
    assert sim.now == 2.0
    assert sim.pending_events == (late,)
    assert sim.run() == (late,)
    assert sim.now == 5.0


def test_max_events_stops_without_artificial_clock_advance():
    sim = DiscreteEventSimulator()
    first = sim.schedule(EventKind.REQUEST_CREATED, at=1)
    second = sim.schedule(EventKind.REQUEST_CREATED, at=2)

    assert sim.run(until=10, max_events=1) == (first,)
    assert sim.now == 1.0
    assert sim.pending_events == (second,)


def test_same_seed_reproduces_random_draws_and_scheduled_trace():
    def build(seed):
        sim = DiscreteEventSimulator(seed=seed)
        for index in range(5):
            sim.schedule(
                EventKind.OBSERVATION_DELAYED,
                at=sim.random_uniform(0.0, 10.0),
                event_id=f"e{index}",
                payload={"choice": sim.random_choice(["a", "b", "c"])},
            )
        sim.run()
        return sim

    left = build(42)
    right = build(42)

    assert left.trace == right.trace
    assert left.now == right.now


def test_event_ids_are_globally_unique_within_a_simulation():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.REQUEST_CREATED, event_id="same")
    with pytest.raises(ValueError, match="duplicate simulator event_id"):
        sim.schedule(EventKind.REQUEST_CREATED, event_id="same")


def test_scheduler_rejects_time_regression_and_nonfinite_values():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.REQUEST_CREATED, at=2)
    sim.run()

    with pytest.raises(ValueError, match="simulated past"):
        sim.schedule(EventKind.REQUEST_CREATED, at=1)
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite"):
            sim.schedule(EventKind.REQUEST_CREATED, at=value)


def test_payload_is_frozen_and_rejects_nonfinite_numbers():
    sim = DiscreteEventSimulator()
    event = sim.schedule(
        EventKind.STATE_CREATED,
        payload={"state": "x", "nested": {"sizes": [1, 2, 3]}},
    )
    assert event.payload == (("nested", (("sizes", (1, 2, 3)),)), ("state", "x"))

    with pytest.raises(ValueError, match="payload floats must be finite"):
        sim.schedule(EventKind.STATE_CREATED, payload={"bad": math.nan})
