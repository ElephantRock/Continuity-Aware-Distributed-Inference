from __future__ import annotations

from enum import Enum


C81_PROTOCOL_FINGERPRINT = "616cc4daa4c167152875522767a8cc968da25c63d6acc099c265bc3554641cb6"
C8_MAX_FRAME_BYTES = 1_048_576
C8_MESSAGE_ENVELOPE_FIELDS = (
    "schema",
    "message_id",
    "message_kind",
    "sender_role",
    "receiver_role",
    "subject_type",
    "subject_id",
    "payload_schema",
    "payload",
    "send_monotonic_ns",
)


class C8WireRole(str, Enum):
    CONTROL_PLANE_AUTHORITY = "CONTROL_PLANE_AUTHORITY"
    WORKER = "WORKER"
    FAULT_TRANSPORT_HARNESS = "FAULT_TRANSPORT_HARNESS"
