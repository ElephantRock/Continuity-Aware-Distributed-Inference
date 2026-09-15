from __future__ import annotations

from collections import deque
from enum import Enum
from typing import Deque, Iterable


class DeliveryAction(str, Enum):
    DELIVER = "DELIVER"
    DELAY = "DELAY"
    DUPLICATE = "DUPLICATE"
    REORDER = "REORDER"
    DROP = "DROP"
    LATE_DELIVER = "LATE_DELIVER"


class QueueCapacityError(RuntimeError):
    pass


class DeliveryScheduler:
    """Deterministic frame scheduler used only by the external transport harness.

    The scheduler never inspects or changes semantic payload fields. It operates on
    already-encoded canonical frames and is therefore unable to confer authority.
    """

    def __init__(self, script: Iterable[DeliveryAction | str] = (), *, capacity: int = 64) -> None:
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self.capacity = capacity
        self._script = tuple(
            action if isinstance(action, DeliveryAction) else DeliveryAction(action)
            for action in script
        )
        self._cursor = 0
        self._delayed: Deque[bytes] = deque()
        self._late: Deque[bytes] = deque()
        self._reorder_held: bytes | None = None
        self.dropped = 0

    @property
    def pending_count(self) -> int:
        return len(self._delayed) + len(self._late) + (1 if self._reorder_held is not None else 0)

    @property
    def script_consumed(self) -> int:
        return self._cursor

    def _reserve(self) -> None:
        if self.pending_count >= self.capacity:
            raise QueueCapacityError("bounded delivery queue is full")

    def _next_action(self) -> DeliveryAction:
        if self._cursor >= len(self._script):
            return DeliveryAction.DELIVER
        action = self._script[self._cursor]
        self._cursor += 1
        return action

    def submit(self, frame: bytes) -> tuple[bytes, ...]:
        if not isinstance(frame, bytes) or not frame:
            raise ValueError("scheduled frame must be non-empty bytes")
        action = self._next_action()
        if action is DeliveryAction.DELIVER:
            return (frame,)
        if action is DeliveryAction.DUPLICATE:
            return (frame, frame)
        if action is DeliveryAction.DROP:
            self.dropped += 1
            return ()
        if action is DeliveryAction.DELAY:
            self._reserve()
            self._delayed.append(frame)
            return ()
        if action is DeliveryAction.LATE_DELIVER:
            self._reserve()
            self._late.append(frame)
            return ()
        if action is DeliveryAction.REORDER:
            if self._reorder_held is None:
                self._reserve()
                self._reorder_held = frame
                return ()
            held = self._reorder_held
            self._reorder_held = None
            return (frame, held)
        raise AssertionError(f"unreachable action: {action}")

    def flush_delayed(self) -> tuple[bytes, ...]:
        values = tuple(self._delayed)
        self._delayed.clear()
        return values

    def flush_late(self) -> tuple[bytes, ...]:
        values = tuple(self._late)
        self._late.clear()
        return values

    def flush_reorder(self) -> tuple[bytes, ...]:
        if self._reorder_held is None:
            return ()
        value = self._reorder_held
        self._reorder_held = None
        return (value,)
