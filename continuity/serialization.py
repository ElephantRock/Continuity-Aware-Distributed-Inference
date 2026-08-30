from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
import math
import types
from typing import Any, Callable, Iterable, Optional, Union, get_args, get_origin, get_type_hints

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

_ENTITY_COLLECTION_TYPES = {
    "programs": Program,
    "sessions": Session,
    "continuations": Continuation,
    "requests": LogicalRequest,
    "attempts": Attempt,
    "phases": Phase,
    "states": ReusableState,
    "replicas": StateReplica,
    "bindings": Binding,
    "evidence": Evidence,
    "outputs": Output,
    "events": SemanticEvent,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _reject_non_finite_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON numeric constant is not allowed: {value}")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON numeric value is not allowed: {value}")
    return parsed


def _strict_json_loads(text: str) -> Any:
    return json.loads(
        text,
        parse_constant=_reject_non_finite_constant,
        parse_float=_parse_finite_float,
    )


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


def _matches_annotation(value: Any, annotation: Any) -> bool:
    if annotation is Any:
        return True
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        return any(_matches_annotation(value, arg) for arg in args)
    if origin is frozenset:
        return (
            isinstance(value, frozenset)
            and len(args) == 1
            and all(_matches_annotation(item, args[0]) for item in value)
        )
    if origin is tuple:
        if not isinstance(value, tuple):
            return False
        if len(args) == 2 and args[1] is Ellipsis:
            return all(_matches_annotation(item, args[0]) for item in value)
        return len(value) == len(args) and all(
            _matches_annotation(item, arg) for item, arg in zip(value, args)
        )
    if annotation is bool:
        return type(value) is bool
    if annotation is int:
        return type(value) is int
    if annotation is float:
        if type(value) not in (int, float) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(float(value))
        except (OverflowError, ValueError):
            return False
    if annotation is str:
        return type(value) is str
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return isinstance(value, annotation)
    if isinstance(annotation, type):
        return isinstance(value, annotation)
    return False


def _validate_entity(entity: Any, expected_type: type, field_name: str) -> None:
    if not isinstance(entity, expected_type):
        raise ValueError(
            f"snapshot {field_name} contains {type(entity).__name__}, "
            f"expected {expected_type.__name__}"
        )
    hints = get_type_hints(expected_type)
    for field in fields(expected_type):
        value = getattr(entity, field.name)
        if not _matches_annotation(value, hints[field.name]):
            raise ValueError(
                f"snapshot {field_name}.{entity.id}.{field.name} has invalid type"
            )


def _validate_decoded_state(state: dict[str, Any]) -> None:
    seen_ids: set[str] = set()
    for name, expected_type in _ENTITY_COLLECTION_TYPES.items():
        collection = state[name]
        if not isinstance(collection, dict):
            raise ValueError(f"snapshot {name} must decode to a dict")
        for key, entity in collection.items():
            if type(key) is not str:
                raise ValueError(f"snapshot {name} keys must be strings")
            _validate_entity(entity, expected_type, name)
            if entity.id != key:
                raise ValueError(f"snapshot {name} key does not match entity ID")
            if key in seen_ids:
                raise ValueError("snapshot reuses a logical ID across entity collections")
            seen_ids.add(key)

    event_order = state["event_order"]
    if not isinstance(event_order, list) or any(
        type(item) is not str for item in event_order
    ):
        raise ValueError("snapshot event_order must decode to list[str]")

    current_bindings = state["current_binding_by_subject"]
    if not isinstance(current_bindings, dict) or any(
        type(key) is not str or type(value) is not str
        for key, value in current_bindings.items()
    ):
        raise ValueError(
            "snapshot current_binding_by_subject must decode to dict[str, str]"
        )

    for name in ("current_epoch_by_subject", "last_allocated_epoch_by_subject"):
        mapping = state[name]
        if not isinstance(mapping, dict) or any(
            type(key) is not str or type(value) is not int
            for key, value in mapping.items()
        ):
            raise ValueError(f"snapshot {name} must decode to dict[str, int]")


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
    if not isinstance(payload, dict) or payload.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError("unsupported Continuity snapshot schema")
    raw_state = payload.get("state")
    if not isinstance(raw_state, dict):
        raise ValueError("snapshot state must be an object")
    missing = set(_CORE_FIELDS) - set(raw_state)
    extra = set(raw_state) - set(_CORE_FIELDS)
    if missing:
        raise ValueError(f"snapshot missing core fields: {sorted(missing)}")
    if extra:
        raise ValueError(f"snapshot has unknown core fields: {sorted(extra)}")

    try:
        state = {name: _decode(raw_state[name]) for name in _CORE_FIELDS}
        _validate_decoded_state(state)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("snapshot violates Continuity schema") from exc

    core = ContinuityCore(semantic_validity=semantic_validity)
    for name in _CORE_FIELDS:
        setattr(core, name, state[name])

    from .invariants import InvariantOracle

    try:
        InvariantOracle(core).assert_all()
    except (AssertionError, KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("snapshot violates Continuity invariants") from exc
    return core


def snapshot_fingerprint(core: ContinuityCore) -> str:
    return hashlib.sha256(snapshot_core(core).encode("utf-8")).hexdigest()


def event_to_record(event: SemanticEvent) -> dict[str, Any]:
    _validate_entity(event, SemanticEvent, "event")
    return {"schema": EVENT_TRACE_SCHEMA, "event": _encode(event)}


def event_from_record(record: dict[str, Any]) -> SemanticEvent:
    if not isinstance(record, dict) or record.get("schema") != EVENT_TRACE_SCHEMA:
        raise ValueError("unsupported semantic-event schema")
    event = _decode(record["event"])
    _validate_entity(event, SemanticEvent, "event")
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
