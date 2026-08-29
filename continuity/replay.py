from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable

from .core import ContinuityCore
from .serialization import _canonical_json, _decode, _encode

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


@dataclass(frozen=True)
class SemanticOperation:
    """One deterministic state-changing ContinuityCore invocation."""

    id: str
    action: str
    arguments: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def build(cls, id: str, action: str, **arguments: Any) -> "SemanticOperation":
        _require_explicit_replay_time(action, arguments)
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
    if record.get("schema") != OPERATION_TRACE_SCHEMA:
        raise ValueError("unsupported semantic-operation schema")
    operation_id = record.get("operation_id")
    action = record.get("action")
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("semantic operation requires a non-empty operation_id")
    if not isinstance(action, str) or action not in _ALLOWED_ACTIONS:
        raise ValueError(f"unsupported semantic operation action: {action!r}")
    arguments = _decode(record.get("arguments", {"$dict": []}))
    if not isinstance(arguments, dict) or not all(isinstance(k, str) for k in arguments):
        raise ValueError("semantic operation arguments must decode to a string-keyed mapping")
    _require_explicit_replay_time(action, arguments)
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
        operation = operation_from_record(json.loads(line))
        if operation.id in seen:
            raise ValueError(f"duplicate semantic operation identity: {operation.id}")
        seen.add(operation.id)
        result.append(operation)
    return result


def apply_operation(core: ContinuityCore, operation: SemanticOperation) -> Any:
    if operation.action not in _ALLOWED_ACTIONS:
        raise ValueError(f"unsupported semantic operation action: {operation.action!r}")
    arguments = operation.kwargs()
    _require_explicit_replay_time(operation.action, arguments)
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
