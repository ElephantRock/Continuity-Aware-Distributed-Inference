from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Mapping


class EventKind(str, Enum):
    REQUEST_CREATED = "REQUEST_CREATED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    ATTEMPT_TIMEOUT = "ATTEMPT_TIMEOUT"
    ATTEMPT_COMPLETED = "ATTEMPT_COMPLETED"
    ATTEMPT_FAILED = "ATTEMPT_FAILED"
    RETRY_STARTED = "RETRY_STARTED"
    LATE_RESULT = "LATE_RESULT"

    STATE_CREATED = "STATE_CREATED"
    STATE_MATERIALIZATION_STARTED = "STATE_MATERIALIZATION_STARTED"
    STATE_MATERIALIZED = "STATE_MATERIALIZED"
    STATE_TRANSFER_STARTED = "STATE_TRANSFER_STARTED"
    STATE_TRANSFER_COMPLETED = "STATE_TRANSFER_COMPLETED"
    STATE_MOVED = "STATE_MOVED"
    STATE_EVICTED = "STATE_EVICTED"
    STATE_LOST = "STATE_LOST"

    MIGRATION_STARTED = "MIGRATION_STARTED"
    MIGRATION_COMMITTED = "MIGRATION_COMMITTED"
    MIGRATION_FAILED = "MIGRATION_FAILED"

    WORKER_FAILED = "WORKER_FAILED"
    WORKER_RECOVERED = "WORKER_RECOVERED"

    OBSERVATION_CREATED = "OBSERVATION_CREATED"
    OBSERVATION_DELAYED = "OBSERVATION_DELAYED"
    OBSERVATION_DROPPED = "OBSERVATION_DROPPED"
    OBSERVATION_DUPLICATED = "OBSERVATION_DUPLICATED"

    TOOL_WAIT_STARTED = "TOOL_WAIT_STARTED"
    TOOL_RETURNED = "TOOL_RETURNED"
    CONTINUATION_FORKED = "CONTINUATION_FORKED"
    CONTINUATION_JOINED = "CONTINUATION_JOINED"
    CONTINUATION_ABANDONED = "CONTINUATION_ABANDONED"
    CONTINUATION_TERMINATED = "CONTINUATION_TERMINATED"


def _freeze_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("event payload floats must be finite")
        return value
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, Mapping):
        items = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("event payload mapping keys must be strings")
            items.append((key, _freeze_value(item)))
        return tuple(sorted(items))
    raise TypeError(f"unsupported event payload value: {type(value).__name__}")


def freeze_payload(payload: Mapping[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    if payload is None:
        return ()
    frozen = _freeze_value(payload)
    if not isinstance(frozen, tuple):
        raise TypeError("event payload must be a mapping")
    return frozen


@dataclass(frozen=True, order=True, slots=True)
class SimEvent:
    """Immutable simulator event ordered only by logical time and insertion sequence."""

    time: float
    sequence: int
    event_id: str = field(compare=False)
    kind: EventKind = field(compare=False)
    payload: tuple[tuple[str, Any], ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.time, (int, float)) or isinstance(self.time, bool):
            raise TypeError("event time must be numeric")
        if not math.isfinite(float(self.time)) or self.time < 0:
            raise ValueError("event time must be finite and non-negative")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("event sequence must be a non-negative integer")
        if not isinstance(self.event_id, str) or not self.event_id:
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.kind, EventKind):
            raise TypeError("kind must be EventKind")
        if not isinstance(self.payload, tuple):
            raise TypeError("payload must be frozen before SimEvent construction")
