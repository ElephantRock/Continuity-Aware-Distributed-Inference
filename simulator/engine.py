from __future__ import annotations

import heapq
import math
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from .events import EventKind, SimEvent, freeze_payload

EventHandler = Callable[["DiscreteEventSimulator", SimEvent], None]


class DiscreteEventSimulator:
    """Single-threaded deterministic event kernel for C2.

    C1 remains the semantic authority. This class owns only simulation time,
    event ordering, reproducible randomness, and event delivery.
    """

    def __init__(self, *, seed: int = 0) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError("seed must be an integer")
        self.seed = seed
        self.now = 0.0
        self._rng = random.Random(seed)
        self._queue: list[SimEvent] = []
        self._handlers: dict[EventKind, list[EventHandler]] = defaultdict(list)
        self._cancelled: set[str] = set()
        self._pending_ids: set[str] = set()
        self._known_ids: set[str] = set()
        self._trace: list[SimEvent] = []
        self._next_sequence = 0

    @property
    def trace(self) -> tuple[SimEvent, ...]:
        return tuple(self._trace)

    @property
    def pending_events(self) -> tuple[SimEvent, ...]:
        return tuple(sorted(event for event in self._queue if event.event_id not in self._cancelled))

    def register_handler(self, kind: EventKind, handler: EventHandler) -> None:
        if not isinstance(kind, EventKind):
            raise TypeError("kind must be EventKind")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handlers[kind].append(handler)

    def schedule(
        self,
        kind: EventKind,
        *,
        at: float | None = None,
        delay: float | None = None,
        event_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> SimEvent:
        if not isinstance(kind, EventKind):
            raise TypeError("kind must be EventKind")
        if at is not None and delay is not None:
            raise ValueError("specify at or delay, not both")
        if at is None:
            delay_value = 0.0 if delay is None else self._finite_nonnegative(delay, "delay")
            event_time = self.now + delay_value
        else:
            event_time = self._finite_nonnegative(at, "at")
            if event_time < self.now:
                raise ValueError("cannot schedule an event in the simulated past")

        sequence = self._next_sequence
        self._next_sequence += 1
        actual_id = event_id if event_id is not None else f"event-{sequence:08d}"
        if not isinstance(actual_id, str) or not actual_id:
            raise ValueError("event_id must be a non-empty string")
        if actual_id in self._known_ids:
            raise ValueError(f"duplicate simulator event_id: {actual_id}")

        event = SimEvent(
            time=event_time,
            sequence=sequence,
            event_id=actual_id,
            kind=kind,
            payload=freeze_payload(payload),
        )
        self._known_ids.add(actual_id)
        self._pending_ids.add(actual_id)
        heapq.heappush(self._queue, event)
        return event

    def cancel(self, event_id: str) -> bool:
        if event_id not in self._pending_ids or event_id in self._cancelled:
            return False
        self._cancelled.add(event_id)
        return True

    def run_next(self) -> SimEvent | None:
        executed = self.run(max_events=1)
        return executed[0] if executed else None

    def run(self, *, until: float | None = None, max_events: int | None = None) -> tuple[SimEvent, ...]:
        horizon = None
        if until is not None:
            horizon = self._finite_nonnegative(until, "until")
            if horizon < self.now:
                raise ValueError("until cannot move simulated time backward")
        if max_events is not None:
            if not isinstance(max_events, int) or isinstance(max_events, bool) or max_events <= 0:
                raise ValueError("max_events must be a positive integer")

        start = len(self._trace)
        processed = 0
        hit_limit = False

        while self._queue:
            event = self._queue[0]
            if horizon is not None and event.time > horizon:
                break
            if max_events is not None and processed >= max_events:
                hit_limit = True
                break

            event = heapq.heappop(self._queue)
            self._pending_ids.discard(event.event_id)
            if event.event_id in self._cancelled:
                self._cancelled.remove(event.event_id)
                continue

            if event.time < self.now:
                raise RuntimeError("event queue violated monotonic simulated time")
            self.now = float(event.time)
            self._trace.append(event)
            processed += 1

            for handler in tuple(self._handlers.get(event.kind, ())):
                handler(self, event)

        if horizon is not None and not hit_limit and self.now < horizon:
            self.now = horizon

        return tuple(self._trace[start:])

    def random_unit(self) -> float:
        return self._rng.random()

    def random_uniform(self, low: float, high: float) -> float:
        low_value = self._finite(low, "low")
        high_value = self._finite(high, "high")
        if high_value < low_value:
            raise ValueError("high must be >= low")
        return self._rng.uniform(low_value, high_value)

    def random_choice(self, values: Sequence[Any]) -> Any:
        if not values:
            raise ValueError("random_choice requires a non-empty sequence")
        return values[self._rng.randrange(len(values))]

    @staticmethod
    def _finite(value: float, name: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"{name} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{name} must be finite")
        return numeric

    @classmethod
    def _finite_nonnegative(cls, value: float, name: str) -> float:
        numeric = cls._finite(value, name)
        if numeric < 0:
            raise ValueError(f"{name} must be non-negative")
        return numeric
