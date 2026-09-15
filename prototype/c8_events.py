from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from .c8_wire_contract import C8WireRole


EVENT_SCHEMA = "cadi.c8.2.process-event.v1"


class EventLogger:
    """Per-process canonical JSONL log used for C8.3 cross-layer reconciliation."""

    def __init__(self, path: str | Path, role: C8WireRole) -> None:
        self.path = Path(path)
        self.role = role
        self.pid = os.getpid()
        self._index = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Each process owns its own log path, so truncation is deterministic and safe.
        self.path.write_text("", encoding="utf-8")

    def emit(
        self,
        *,
        action: str,
        result: str,
        message: Mapping[str, Any] | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(action, str) or not action:
            raise ValueError("event action must be non-empty")
        if not isinstance(result, str) or not result:
            raise ValueError("event result must be non-empty")
        self._index += 1
        event: dict[str, Any] = {
            "schema": EVENT_SCHEMA,
            "event_index": self._index,
            "process_role": self.role.value,
            "pid": self.pid,
            "message_id": None,
            "message_kind": None,
            "subject_type": None,
            "subject_id": None,
            "action": action,
            "result": result,
            "monotonic_ns": time.monotonic_ns(),
            "details": dict(details or {}),
        }
        if message is not None:
            event["message_id"] = message.get("message_id")
            event["message_kind"] = message.get("message_kind")
            event["subject_type"] = message.get("subject_type")
            event["subject_id"] = message.get("subject_id")
        line = json.dumps(
            event,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event


def read_events(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        event = json.loads(line)
        if event.get("schema") != EVENT_SCHEMA:
            raise ValueError("unexpected event schema")
        events.append(event)
    return events
