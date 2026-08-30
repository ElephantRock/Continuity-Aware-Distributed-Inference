from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Callable, Iterable, Optional

from .core import ContinuityCore
from .entities import (
    Attempt, AttemptAuthority, Binding, BindingStatus, Continuation, ContinuationLifecycle,
    Evidence, EvidenceAuthority, EvidenceStatus, ExecutionStatus, LogicalRequest, Output,
    Phase, PhaseStatus, PhaseType, Program, ProgramStatus, ReconcileOutcome, ReplicaStatus,
    RequestStatus, ReusableState, SemanticEvent, Session, StateLifecycle, StateReplica,
    StateValidity,
)

SNAPSHOT_SCHEMA = "cadi.core.snapshot.v1"
EVENT_TRACE_SCHEMA = "cadi.semantic-event.v1"

_DATACLASS_TYPES = {
    cls.__name__: cls
    for cls in (
        Program, Session, Continuation, LogicalRequest, Attempt, Phase, ReusableState,
        StateReplica, Binding, Evidence, Output, SemanticEvent,
    )
}

_ENUM_TYPES = {
    cls.__name__: cls
    for cls in (
        ProgramStatus, ContinuationLifecycle, RequestStatus, ExecutionStatus,
        AttemptAuthority, PhaseType, PhaseStatus, StateLifecycle, StateValidity,
        ReplicaStatus, BindingStatus, EvidenceAuthority, EvidenceStatus, ReconcileOutcome,
    )
}

_CORE_FIELDS = (
    "programs",
    "sessions",
    "continuations",
    "requests",
    "attempts",
    "phases",
    "states",
    "replicas",
    "bindings",
    "evidence",
    "outputs",
    "events",
    "event_order",
    "current_binding_by_subject",
    "current_epoch_by_subject",
    "last_allocated_epoch_by_subject",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _reject_non_finite_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON numeric constant is not allowed: {value}")


def _strict_json_loads(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_non_finite_constant)


def _encode(value: Any) -> Any:
    if is_dataclass(value):
        return {
            "$type": type(value).__name__,
            "fields": {f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "name": value.name}
    if isinstance(value, frozenset):
        items = [_encode(item) for item in value]
        items.sort(key=_canonical_json)
        return {"$frozenset": items}
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if isinstance(value, list):
        return {"$list": [_encode(item) for item in value]}
    if isinstance(value, dict):
        entries = [[_encode(k), _encode(v)] for k, v in value.items()]
        entries.sort(key=lambda item: _canonical_json(item[0]))
        return {"$dict": entries}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported snapshot value: {type(value)!r}")


def _decode(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "$enum" in value:
        enum_type = _ENUM_TYPES[value["$enum"]]
        return enum_type[value["name"]]
    if "$frozenset" in value:
        return frozenset(_decode(item) for item in value["$frozenset"])
    if "$tuple" in value:
        return tuple(_decode(item) for item in value["$tuple"])
    if "$list" in value:
        return [_decode(item) for item in value["$list"]]
    if "$dict" in value:
        return {_decode(k): _decode(v) for k, v in value["$dict"]}
    if "$type" in value:
        cls = _DATACLASS_TYPES[value["$type"]]
        kwargs = {name: _decode(item) for name, item in value["fields"].items()}
        return cls(**kwargs)
    return {k: _decode(v) for k, v in value.items()}


def snapshot_core(core: ContinuityCore) -> str:
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "state": {name: _encode(getattr(core, name)) for name in _CORE_FIELDS},
    }
    return _canonical_json(payload)


def restore_core(
    snapshot: str,
    semantic_validity: Optional[Callable[[ReusableState, Any], bool]] = None,
) -> ContinuityCore:
    payload = _strict_json_loads(snapshot)
    if payload.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("unsupported Continuity snapshot schema")
    core = ContinuityCore(semantic_validity=semantic_validity)
    state = payload["state"]
    missing = set(_CORE_FIELDS) - set(state)
    if missing:
        raise ValueError(f"snapshot missing core fields: {sorted(missing)}")
    for name in _CORE_FIELDS:
        setattr(core, name, _decode(state[name]))
    from .invariants import InvariantOracle
    try:
        InvariantOracle(core).assert_all()
    except (AssertionError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("snapshot violates Continuity invariants") from exc
    return core


def snapshot_fingerprint(core: ContinuityCore) -> str:
    return hashlib.sha256(snapshot_core(core).encode("utf-8")).hexdigest()


def event_to_record(event: SemanticEvent) -> dict[str, Any]:
    return {"schema": EVENT_TRACE_SCHEMA, "event": _encode(event)}


def event_from_record(record: dict[str, Any]) -> SemanticEvent:
    if record.get("schema") != EVENT_TRACE_SCHEMA:
        raise ValueError("unsupported semantic-event schema")
    event = _decode(record["event"])
    if not isinstance(event, SemanticEvent):
        raise ValueError("trace record does not contain a SemanticEvent")
    return event


def events_to_jsonl(events: Iterable[SemanticEvent]) -> str:
    lines = [_canonical_json(event_to_record(event)) for event in events]
    return "\n".join(lines) + ("\n" if lines else "")


def events_from_jsonl(text: str) -> list[SemanticEvent]:
    result: list[SemanticEvent] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        result.append(event_from_record(_strict_json_loads(line)))
    return result


def export_event_log(core: ContinuityCore) -> str:
    return events_to_jsonl(core.events[event_id] for event_id in core.event_order)


def replay_event_log(core: ContinuityCore, text: str) -> ContinuityCore:
    for event in events_from_jsonl(text):
        core.record_event(event)
    return core
