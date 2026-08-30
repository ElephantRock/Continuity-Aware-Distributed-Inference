from __future__ import annotations

from collections.abc import Iterable as IterableABC
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import inspect
import json
import math
import types
from typing import Any, Iterable, Union, get_args, get_origin, get_type_hints

from .core import ContinuityCore
from .serialization import _canonical_json, _decode, _encode, _strict_json_loads

OPERATION_TRACE_SCHEMA = "cadi.semantic-operation.v1"

_ALLOWED_ACTIONS = frozenset({
    "create_program",
    "set_program_status",
    "create_session",
    "create_continuation",
    "set_continuation_lifecycle",
    "resume_after_wait",
    "create_request",
    "start_attempt",
    "set_attempt_execution",
    "complete_attempt",
    "create_phase",
    "set_phase_status",
    "complete_phase",
    "create_output",
    "finalize_request",
    "create_state",
    "set_state_validity",
    "add_replica",
    "set_replica_status",
    "propose_binding",
    "activate_initial_binding",
    "begin_migration",
    "commit_migration",
    "record_evidence",
    "record_event",
})

_TIME_SENSITIVE_ACTIONS = frozenset({"finalize_request", "commit_migration"})


def _require_explicit_replay_time(action: str, arguments: dict[str, Any]) -> None:
    """Prevent replay semantics from depending on the host wall clock."""
    if action in _TIME_SENSITIVE_ACTIONS and (
        "now" not in arguments or arguments["now"] is None
    ):
        raise ValueError(f"semantic replay action {action!r} requires explicit 'now'")


def _matches_operation_annotation(value: Any, annotation: Any) -> bool:
    """Runtime type check for untrusted semantic-operation arguments."""
    if annotation is Any:
        return True

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in (Union, types.UnionType):
        return any(_matches_operation_annotation(value, arg) for arg in args)

    if origin is IterableABC:
        if isinstance(value, (str, bytes, dict)) or not isinstance(value, IterableABC):
            return False
        return len(args) == 1 and all(
            _matches_operation_annotation(item, args[0]) for item in value
        )

    if origin is list:
        return isinstance(value, list) and len(args) == 1 and all(
            _matches_operation_annotation(item, args[0]) for item in value
        )

    if origin is set:
        return isinstance(value, set) and len(args) == 1 and all(
            _matches_operation_annotation(item, args[0]) for item in value
        )

    if origin is frozenset:
        return isinstance(value, frozenset) and len(args) == 1 and all(
            _matches_operation_annotation(item, args[0]) for item in value
        )

    if origin is tuple:
        if not isinstance(value, tuple):
            return False
        if len(args) == 2 and args[1] is Ellipsis:
            return all(_matches_operation_annotation(item, args[0]) for item in value)
        return len(value) == len(args) and all(
            _matches_operation_annotation(item, arg) for item, arg in zip(value, args)
        )

    if origin is dict:
        return isinstance(value, dict) and len(args) == 2 and all(
            _matches_operation_annotation(k, args[0])
            and _matches_operation_annotation(v, args[1])
            for k, v in value.items()
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

    if isinstance(annotation, type) and is_dataclass(annotation):
        if type(value) is not annotation:
            return False
        hints = get_type_hints(annotation)
        return all(
            field.name in hints
            and _matches_operation_annotation(getattr(value, field.name), hints[field.name])
            for field in fields(annotation)
        )

    if isinstance(annotation, type):
        return isinstance(value, annotation)

    return False


def _validate_operation_arguments(action: str, arguments: dict[str, Any]) -> None:
    """Validate shape and runtime types before constructing or dispatching an operation."""
    if action not in _ALLOWED_ACTIONS:
        raise ValueError(f"unsupported semantic operation action: {action!r}")

    method = getattr(ContinuityCore, action)
    signature = inspect.signature(method)
    try:
        bound = signature.bind(None, **arguments)
    except TypeError as exc:
        raise ValueError(f"invalid arguments for semantic operation {action!r}") from exc

    hints = get_type_hints(method)
    for name, value in bound.arguments.items():
        if name == "self":
            continue
        annotation = hints.get(name, Any)
        if not _matches_operation_annotation(value, annotation):
            raise ValueError(
                f"semantic operation {action!r} argument {name!r} has invalid type"
            )


def _decode_operation_arguments(record: dict[str, Any]) -> dict[str, Any]:
    try:
        arguments = _decode(record.get("arguments", {"$dict": []}))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("semantic operation arguments could not be decoded") from exc
    if not isinstance(arguments, dict) or not all(type(k) is str for k in arguments):
        raise ValueError("semantic operation arguments must decode to a string-keyed mapping")
    return arguments


@dataclass(frozen=True)
class SemanticOperation:
    """One deterministic state-changing ContinuityCore invocation."""

    id: str
    action: str
    arguments: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def build(cls, id: str, action: str, **arguments: Any) -> "SemanticOperation":
        if type(id) is not str or not id:
            raise ValueError("semantic operation requires a non-empty operation_id")
        _require_explicit_replay_time(action, arguments)
        _validate_operation_arguments(action, arguments)
        return cls(id=id, action=action, arguments=tuple(sorted(arguments.items())))

    def kwargs(self) -> dict[str, Any]:
        return dict(self.arguments)


def operation_to_record(operation: SemanticOperation) -> dict[str, Any]:
    return {
        "schema": OPERATION_TRACE_SCHEMA,
        "operation_id": operation.id,
        "action": operation.action,
        "arguments": _encode(operation.kwargs()),
    }


def operation_from_record(record: dict[str, Any]) -> SemanticOperation:
    if not isinstance(record, dict) or record.get("schema") != OPERATION_TRACE_SCHEMA:
        raise ValueError("unsupported semantic-operation schema")
    operation_id = record.get("operation_id")
    action = record.get("action")
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("semantic operation requires a non-empty operation_id")
    if not isinstance(action, str) or action not in _ALLOWED_ACTIONS:
        raise ValueError(f"unsupported semantic operation action: {action!r}")
    arguments = _decode_operation_arguments(record)
    _require_explicit_replay_time(action, arguments)
    _validate_operation_arguments(action, arguments)
    return SemanticOperation.build(operation_id, action, **arguments)


def operations_to_jsonl(operations: Iterable[SemanticOperation]) -> str:
    lines = [_canonical_json(operation_to_record(operation)) for operation in operations]
    return "\n".join(lines) + ("\n" if lines else "")


def operations_from_jsonl(text: str) -> list[SemanticOperation]:
    result: list[SemanticOperation] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        operation = operation_from_record(_strict_json_loads(line))
        if operation.id in seen:
            raise ValueError(f"duplicate semantic operation identity: {operation.id}")
        seen.add(operation.id)
        result.append(operation)
    return result


def apply_operation(core: ContinuityCore, operation: SemanticOperation) -> Any:
    if type(operation.id) is not str or not operation.id:
        raise ValueError("semantic operation requires a non-empty operation_id")
    if operation.action not in _ALLOWED_ACTIONS:
        raise ValueError(f"unsupported semantic operation action: {operation.action!r}")
    try:
        arguments = operation.kwargs()
    except (TypeError, ValueError) as exc:
        raise ValueError("semantic operation arguments are malformed") from exc
    if not all(type(k) is str for k in arguments):
        raise ValueError("semantic operation arguments must be string-keyed")
    _require_explicit_replay_time(operation.action, arguments)
    _validate_operation_arguments(operation.action, arguments)
    method = getattr(core, operation.action)
    return method(**arguments)


def replay_operations(core: ContinuityCore, operations: Iterable[SemanticOperation]) -> ContinuityCore:
    seen: set[str] = set()
    for operation in operations:
        if operation.id in seen:
            raise ValueError(f"duplicate semantic operation identity: {operation.id}")
        seen.add(operation.id)
        apply_operation(core, operation)
    return core


def replay_operation_jsonl(core: ContinuityCore, text: str) -> ContinuityCore:
    return replay_operations(core, operations_from_jsonl(text))
