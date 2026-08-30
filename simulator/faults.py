from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import random
from typing import Any, Mapping, Optional

from .engine import DiscreteEventSimulator
from .events import EventKind, SimEvent, freeze_payload
from .resources import ReplicaRuntimeStatus, ResourceModel, WorkerStatus


class FaultClass(str, Enum):
    DELIVERY_DELAY = "DELIVERY_DELAY"
    DELIVERY_DROP = "DELIVERY_DROP"
    DELIVERY_DUPLICATE = "DELIVERY_DUPLICATE"
    DELIVERY_REORDER = "DELIVERY_REORDER"
    WORKER_FAILURE = "WORKER_FAILURE"
    REPLICA_LOSS = "REPLICA_LOSS"
    REPLICA_EVICTION = "REPLICA_EVICTION"
    ATTEMPT_TIMEOUT = "ATTEMPT_TIMEOUT"
    LATE_ATTEMPT_RESULT = "LATE_ATTEMPT_RESULT"
    STALE_ATTEMPT_OBSERVATION = "STALE_ATTEMPT_OBSERVATION"


@dataclass(frozen=True, slots=True)
class FaultRecord:
    id: str
    fault_class: FaultClass
    target: str
    injection_time: float
    duration: float
    parameters: tuple[tuple[str, Any], ...]
    ground_truth_effect: str
    expected_invariant_pressure: tuple[str, ...]
    expected_safe_outcomes: tuple[str, ...]
    produced_event_ids: tuple[str, ...] = ()
    cancelled_event_ids: tuple[str, ...] = ()
    seed: Optional[int] = None
    draw: Optional[float] = None


@dataclass(frozen=True, slots=True)
class ProbabilisticFaultDecision:
    seed: int
    ordinal: int
    target: str
    draw: float
    selected_fault_class: Optional[FaultClass]
    fault_id: Optional[str]


_PRESSURE: dict[FaultClass, tuple[str, ...]] = {
    FaultClass.DELIVERY_DELAY: ("Evidence freshness", "late/stale event fencing"),
    FaultClass.DELIVERY_DROP: ("missing Evidence", "fail-closed reconciliation"),
    FaultClass.DELIVERY_DUPLICATE: ("Event/Evidence idempotence",),
    FaultClass.DELIVERY_REORDER: ("causal ordering", "late/stale event fencing"),
    FaultClass.WORKER_FAILURE: ("physical availability", "retry/recovery path"),
    FaultClass.REPLICA_LOSS: ("physical StateReplica availability", "State reuse safety"),
    FaultClass.REPLICA_EVICTION: ("physical StateReplica availability", "cold-resume/reuse path"),
    FaultClass.ATTEMPT_TIMEOUT: ("Attempt authority supersession", "late-result fencing"),
    FaultClass.LATE_ATTEMPT_RESULT: ("superseded-event fencing", "finalization authority"),
    FaultClass.STALE_ATTEMPT_OBSERVATION: ("Evidence scope/authority", "terminal-request fencing"),
}

_SAFE: dict[FaultClass, tuple[str, ...]] = {
    FaultClass.DELIVERY_DELAY: ("WAIT", "RETRY", "IGNORE_STALE", "MATCHED_AFTER_DELAY"),
    FaultClass.DELIVERY_DROP: ("WAIT", "RETRY", "RECOMPUTE", "FAIL_CLOSED"),
    FaultClass.DELIVERY_DUPLICATE: ("IDEMPOTENT", "IGNORE_DUPLICATE"),
    FaultClass.DELIVERY_REORDER: ("IGNORE_STALE", "WAIT", "RETRY", "MATCHED"),
    FaultClass.WORKER_FAILURE: ("FAIL_PHYSICAL_WORK", "RETRY", "RECOMPUTE", "MIGRATE"),
    FaultClass.REPLICA_LOSS: ("RECOMPUTE", "MIGRATE", "REJECT_REUSE"),
    FaultClass.REPLICA_EVICTION: ("RECOMPUTE", "RESTORE", "COLD_RESUME"),
    FaultClass.ATTEMPT_TIMEOUT: ("RETRY", "IGNORE_STALE", "WAIT"),
    FaultClass.LATE_ATTEMPT_RESULT: ("RECORD_NON_AUTHORITATIVE", "IGNORE_STALE"),
    FaultClass.STALE_ATTEMPT_OBSERVATION: ("REJECT", "IGNORE_STALE", "FAIL_CLOSED"),
}


class FaultInjector:
    """Policy-neutral C2 fault injector with explicit ground-truth records.

    Faults alter pending simulator delivery or delegate to the physical ResourceModel.
    They never mutate ContinuityCore semantic state and never choose a routing/recovery
    policy. Probabilistic generation uses an injector-local RNG so fault schedules are
    reproducible and independent from other simulator random draws.
    """

    def __init__(
        self,
        simulator: DiscreteEventSimulator,
        resources: ResourceModel | None = None,
        *,
        seed: int = 0,
    ) -> None:
        if not isinstance(simulator, DiscreteEventSimulator):
            raise TypeError("simulator must be DiscreteEventSimulator")
        if resources is not None and not isinstance(resources, ResourceModel):
            raise TypeError("resources must be ResourceModel or None")
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError("seed must be an integer")
        if resources is not None and resources.simulator is not simulator:
            raise ValueError("resources must reference the same simulator")
        self.simulator = simulator
        self.resources = resources
        self.seed = seed
        self._rng = random.Random(seed)
        self._records: list[FaultRecord] = []
        self._record_ids: set[str] = set()
        self._decisions: list[ProbabilisticFaultDecision] = []
        self._next_fault = 0

    @property
    def records(self) -> tuple[FaultRecord, ...]:
        return tuple(self._records)

    @property
    def decisions(self) -> tuple[ProbabilisticFaultDecision, ...]:
        return tuple(self._decisions)

    def delay_delivery(
        self,
        event_id: str,
        delay: float,
        *,
        fault_id: str | None = None,
        draw: float | None = None,
    ) -> FaultRecord:
        event = self._pending(event_id)
        delay_value = self._finite_nonnegative(delay, "delay")
        actual_fault_id = self._reserve_fault_id(fault_id)
        replacement_id = f"fault:{actual_fault_id}:delayed:{event.event_id}"
        replacement = self.simulator.schedule(
            event.kind,
            at=event.time + delay_value,
            event_id=replacement_id,
            payload=dict(event.payload),
        )
        if not self.simulator.cancel(event.event_id):
            raise RuntimeError("pending event became unavailable during delay injection")
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.DELIVERY_DELAY,
                target=event.event_id,
                injection_time=self.simulator.now,
                duration=delay_value,
                parameters=freeze_payload(
                    {
                        "original_time": event.time,
                        "replacement_time": replacement.time,
                        "replacement_event_id": replacement.event_id,
                    }
                ),
                ground_truth_effect="original delivery cancelled; equivalent delivery rescheduled later",
                expected_invariant_pressure=_PRESSURE[FaultClass.DELIVERY_DELAY],
                expected_safe_outcomes=_SAFE[FaultClass.DELIVERY_DELAY],
                produced_event_ids=(replacement.event_id,),
                cancelled_event_ids=(event.event_id,),
                seed=self.seed if draw is not None else None,
                draw=draw,
            )
        )

    def drop_delivery(
        self,
        event_id: str,
        *,
        fault_id: str | None = None,
        draw: float | None = None,
    ) -> FaultRecord:
        event = self._pending(event_id)
        actual_fault_id = self._reserve_fault_id(fault_id)
        if not self.simulator.cancel(event.event_id):
            raise RuntimeError("pending event became unavailable during drop injection")
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.DELIVERY_DROP,
                target=event.event_id,
                injection_time=self.simulator.now,
                duration=0.0,
                parameters=freeze_payload({"original_time": event.time, "event_kind": event.kind.value}),
                ground_truth_effect="pending delivery cancelled and not replaced",
                expected_invariant_pressure=_PRESSURE[FaultClass.DELIVERY_DROP],
                expected_safe_outcomes=_SAFE[FaultClass.DELIVERY_DROP],
                cancelled_event_ids=(event.event_id,),
                seed=self.seed if draw is not None else None,
                draw=draw,
            )
        )

    def duplicate_delivery(
        self,
        event_id: str,
        *,
        delay: float = 0.0,
        fault_id: str | None = None,
        draw: float | None = None,
    ) -> FaultRecord:
        event = self._pending(event_id)
        delay_value = self._finite_nonnegative(delay, "delay")
        actual_fault_id = self._reserve_fault_id(fault_id)
        duplicate_kind = (
            EventKind.OBSERVATION_DUPLICATED
            if event.kind is EventKind.OBSERVATION_CREATED
            else event.kind
        )
        duplicate_id = f"fault:{actual_fault_id}:duplicate:{event.event_id}"
        duplicate = self.simulator.schedule(
            duplicate_kind,
            at=event.time + delay_value,
            event_id=duplicate_id,
            payload=dict(event.payload),
        )
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.DELIVERY_DUPLICATE,
                target=event.event_id,
                injection_time=self.simulator.now,
                duration=delay_value,
                parameters=freeze_payload(
                    {
                        "original_time": event.time,
                        "duplicate_time": duplicate.time,
                        "duplicate_event_id": duplicate.event_id,
                    }
                ),
                ground_truth_effect="original delivery retained and one duplicate delivery added",
                expected_invariant_pressure=_PRESSURE[FaultClass.DELIVERY_DUPLICATE],
                expected_safe_outcomes=_SAFE[FaultClass.DELIVERY_DUPLICATE],
                produced_event_ids=(duplicate.event_id,),
                seed=self.seed if draw is not None else None,
                draw=draw,
            )
        )

    def reorder_after(
        self,
        event_id: str,
        after_event_id: str,
        *,
        gap: float = 0.0,
        fault_id: str | None = None,
    ) -> FaultRecord:
        event = self._pending(event_id)
        anchor = self._pending(after_event_id)
        if event.event_id == anchor.event_id:
            raise ValueError("event_id and after_event_id must differ")
        gap_value = self._finite_nonnegative(gap, "gap")
        actual_fault_id = self._reserve_fault_id(fault_id)
        replacement_time = max(event.time, anchor.time + gap_value)
        replacement_id = f"fault:{actual_fault_id}:reordered:{event.event_id}"
        replacement = self.simulator.schedule(
            event.kind,
            at=replacement_time,
            event_id=replacement_id,
            payload=dict(event.payload),
        )
        if not self.simulator.cancel(event.event_id):
            raise RuntimeError("pending event became unavailable during reorder injection")
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.DELIVERY_REORDER,
                target=event.event_id,
                injection_time=self.simulator.now,
                duration=replacement.time - event.time,
                parameters=freeze_payload(
                    {
                        "anchor_event_id": anchor.event_id,
                        "anchor_time": anchor.time,
                        "original_time": event.time,
                        "replacement_time": replacement.time,
                        "replacement_event_id": replacement.event_id,
                    }
                ),
                ground_truth_effect="target delivery cancelled and reinserted after the anchor delivery",
                expected_invariant_pressure=_PRESSURE[FaultClass.DELIVERY_REORDER],
                expected_safe_outcomes=_SAFE[FaultClass.DELIVERY_REORDER],
                produced_event_ids=(replacement.event_id,),
                cancelled_event_ids=(event.event_id,),
            )
        )

    def fail_worker(self, worker_id: str, *, fault_id: str | None = None) -> FaultRecord:
        resources = self._require_resources()
        actual_fault_id = self._reserve_fault_id(fault_id)
        event = resources.fail_worker(worker_id)
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.WORKER_FAILURE,
                target=worker_id,
                injection_time=self.simulator.now,
                duration=0.0,
                parameters=freeze_payload({"event_id": event.event_id}),
                ground_truth_effect="WORKER_FAILED event scheduled for the target worker",
                expected_invariant_pressure=_PRESSURE[FaultClass.WORKER_FAILURE],
                expected_safe_outcomes=_SAFE[FaultClass.WORKER_FAILURE],
                produced_event_ids=(event.event_id,),
            )
        )

    def lose_replica(self, replica_id: str, *, fault_id: str | None = None) -> FaultRecord:
        resources = self._require_resources()
        actual_fault_id = self._reserve_fault_id(fault_id)
        event = resources.lose_replica(replica_id)
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.REPLICA_LOSS,
                target=replica_id,
                injection_time=self.simulator.now,
                duration=0.0,
                parameters=freeze_payload({"event_id": event.event_id}),
                ground_truth_effect="STATE_LOST event scheduled for the target physical replica",
                expected_invariant_pressure=_PRESSURE[FaultClass.REPLICA_LOSS],
                expected_safe_outcomes=_SAFE[FaultClass.REPLICA_LOSS],
                produced_event_ids=(event.event_id,),
            )
        )

    def probabilistic_delivery_fault(
        self,
        event_id: str,
        probabilities: Mapping[FaultClass, float],
        *,
        max_delay: float = 0.0,
    ) -> FaultRecord | None:
        event = self._pending(event_id)
        ordered = (
            FaultClass.DELIVERY_DROP,
            FaultClass.DELIVERY_DUPLICATE,
            FaultClass.DELIVERY_DELAY,
        )
        normalized: dict[FaultClass, float] = {}
        total = 0.0
        for fault_class in ordered:
            value = probabilities.get(fault_class, 0.0)
            probability = self._probability(value, fault_class.value)
            normalized[fault_class] = probability
            total += probability
        unsupported = set(probabilities) - set(ordered)
        if unsupported:
            names = ", ".join(sorted(item.value if isinstance(item, FaultClass) else str(item) for item in unsupported))
            raise ValueError(f"unsupported probabilistic delivery classes: {names}")
        if total > 1.0 + 1e-12:
            raise ValueError("probabilities must sum to at most 1")
        max_delay_value = self._finite_nonnegative(max_delay, "max_delay")
        rng_state = self._rng.getstate()
        try:
            draw = self._rng.random()
            selected: FaultClass | None = None
            cursor = 0.0
            for fault_class in ordered:
                cursor += normalized[fault_class]
                if draw < cursor:
                    selected = fault_class
                    break

            ordinal = len(self._decisions)
            if selected is None:
                self._decisions.append(
                    ProbabilisticFaultDecision(self.seed, ordinal, event.event_id, draw, None, None)
                )
                return None

            fault_id = self._next_fault_id()
            if selected is FaultClass.DELIVERY_DROP:
                record = self.drop_delivery(event.event_id, fault_id=fault_id, draw=draw)
            elif selected is FaultClass.DELIVERY_DUPLICATE:
                delay = self._rng.uniform(0.0, max_delay_value) if max_delay_value else 0.0
                record = self.duplicate_delivery(
                    event.event_id, delay=delay, fault_id=fault_id, draw=draw
                )
            else:
                delay = self._rng.uniform(0.0, max_delay_value) if max_delay_value else 0.0
                record = self.delay_delivery(
                    event.event_id, delay, fault_id=fault_id, draw=draw
                )
            self._decisions.append(
                ProbabilisticFaultDecision(
                    self.seed, ordinal, event.event_id, draw, selected, record.id
                )
            )
            return record
        except Exception:
            self._rng.setstate(rng_state)
            raise

    def assert_ground_truth(self) -> None:
        trace = {event.event_id: event for event in self.simulator.trace}
        pending = {event.event_id: event for event in self.simulator.pending_events}
        cancelled_by_fault = {
            event_id
            for record in self._records
            for event_id in record.cancelled_event_ids
        }
        for record in self._records:
            for event_id in record.cancelled_event_ids:
                if event_id in trace or event_id in pending:
                    raise AssertionError(f"cancelled fault target remained live or executed: {event_id}")
            for event_id in record.produced_event_ids:
                if (
                    event_id not in trace
                    and event_id not in pending
                    and event_id not in cancelled_by_fault
                ):
                    raise AssertionError(
                        f"fault-produced event missing from simulator history/state: {event_id}"
                    )
            if record.fault_class is FaultClass.DELIVERY_REORDER:
                params = dict(record.parameters)
                anchor_id = params["anchor_event_id"]
                replacement_id = params["replacement_event_id"]
                if anchor_id in trace and replacement_id in trace:
                    order = {event.event_id: index for index, event in enumerate(self.simulator.trace)}
                    if order[replacement_id] <= order[anchor_id]:
                        raise AssertionError("reordered delivery did not execute after anchor")
            elif record.fault_class is FaultClass.WORKER_FAILURE and self.resources is not None:
                event_id = record.produced_event_ids[0]
                if event_id in trace and self.resources.workers[record.target].status is not WorkerStatus.DOWN:
                    order = {event.event_id: index for index, event in enumerate(self.simulator.trace)}
                    failure_index = order[event_id]
                    recovered_after = any(
                        index > failure_index
                        and event.kind is EventKind.WORKER_RECOVERED
                        and dict(event.payload).get("worker_id") == record.target
                        for index, event in enumerate(self.simulator.trace)
                    )
                    if not recovered_after:
                        raise AssertionError("worker-failure ground truth did not produce DOWN worker")
            elif record.fault_class is FaultClass.REPLICA_LOSS and self.resources is not None:
                event_id = record.produced_event_ids[0]
                if event_id in trace and self.resources.replicas[record.target].status is not ReplicaRuntimeStatus.LOST:
                    raise AssertionError("replica-loss ground truth did not produce LOST replica")
            elif record.fault_class is FaultClass.REPLICA_EVICTION and self.resources is not None:
                event_id = record.produced_event_ids[0]
                if event_id in trace and self.resources.replicas[record.target].status is not ReplicaRuntimeStatus.EVICTED:
                    raise AssertionError("replica-eviction ground truth did not produce EVICTED replica")

    def _append(self, record: FaultRecord) -> FaultRecord:
        if record.id in self._record_ids:
            raise RuntimeError(f"fault_id became committed before record append: {record.id}")
        self._record_ids.add(record.id)
        self._records.append(record)
        while f"fault-{self._next_fault:08d}" in self._record_ids:
            self._next_fault += 1
        return record

    def _reserve_fault_id(self, fault_id: str | None) -> str:
        actual = self._next_fault_id() if fault_id is None else fault_id
        if not isinstance(actual, str) or not actual:
            raise ValueError("fault_id must be a non-empty string")
        if actual in self._record_ids:
            raise ValueError(f"duplicate fault_id: {actual}")
        return actual

    def _next_fault_id(self) -> str:
        index = self._next_fault
        while True:
            candidate = f"fault-{index:08d}"
            if candidate not in self._record_ids:
                return candidate
            index += 1

    def _pending(self, event_id: str) -> SimEvent:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be a non-empty string")
        for event in self.simulator.pending_events:
            if event.event_id == event_id:
                return event
        raise ValueError(f"event is not pending: {event_id}")

    def _require_resources(self) -> ResourceModel:
        if self.resources is None:
            raise ValueError("resource fault requires ResourceModel")
        return self.resources

    @staticmethod
    def _finite_nonnegative(value: float, name: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"{name} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"{name} must be finite and non-negative")
        return numeric

    @staticmethod
    def _probability(value: float, name: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"probability for {name} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0 or numeric > 1:
            raise ValueError(f"probability for {name} must be finite and within [0, 1]")
        return numeric
