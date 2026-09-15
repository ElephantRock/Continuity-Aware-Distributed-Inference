from __future__ import annotations

import json
import socket
import struct
import time
from typing import Any, Mapping

from .c8_wire_contract import (
    C8_MAX_FRAME_BYTES,
    C8_MESSAGE_ENVELOPE_FIELDS,
    C8WireRole,
)


MESSAGE_SCHEMA = "cadi.c8.2.message-envelope.v1"
HEADER_BYTES = 4
HEADER = struct.Struct(">I")
FRAME_IO_TIMEOUT_S = 2.0

MESSAGE_KINDS = frozenset(
    {
        "REGISTER",
        "REGISTERED",
        "WORK",
        "COMPLETE",
        "PROCESS_FAILURE",
        "SHUTDOWN",
        "SHUTDOWN_ACK",
    }
)


class TransportError(RuntimeError):
    """Base class for fail-closed C8.2 transport failures."""


class FrameSizeError(TransportError):
    pass


class TruncatedFrameError(TransportError):
    pass


class FrameTimeoutError(TruncatedFrameError):
    """A partial/stalled frame is treated as a bounded truncation failure."""


class CanonicalEncodingError(TransportError):
    pass


class EnvelopeValidationError(TransportError):
    pass


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def canonical_json_bytes(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalEncodingError(str(exc)) from exc
    return text.encode("utf-8")


def decode_canonical_json(payload: bytes) -> Any:
    if not payload:
        raise CanonicalEncodingError("empty JSON payload")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CanonicalEncodingError("invalid canonical JSON payload") from exc
    if canonical_json_bytes(value) != payload:
        raise CanonicalEncodingError("payload is valid JSON but not canonical encoding")
    return value


def make_envelope(
    *,
    message_id: str,
    message_kind: str,
    sender_role: C8WireRole | str,
    receiver_role: C8WireRole | str,
    subject_type: str,
    subject_id: str,
    payload_schema: str,
    payload: Mapping[str, Any],
    send_monotonic_ns: int | None = None,
) -> dict[str, Any]:
    envelope = {
        "schema": MESSAGE_SCHEMA,
        "message_id": message_id,
        "message_kind": message_kind,
        "sender_role": sender_role.value if isinstance(sender_role, C8WireRole) else sender_role,
        "receiver_role": (
            receiver_role.value if isinstance(receiver_role, C8WireRole) else receiver_role
        ),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "payload_schema": payload_schema,
        "payload": dict(payload),
        "send_monotonic_ns": time.monotonic_ns() if send_monotonic_ns is None else send_monotonic_ns,
    }
    validate_envelope(envelope)
    return envelope


def validate_envelope(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EnvelopeValidationError("message envelope must be a JSON object")
    expected = set(C8_MESSAGE_ENVELOPE_FIELDS)
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise EnvelopeValidationError(f"envelope field mismatch: missing={missing}, extra={extra}")
    if value["schema"] != MESSAGE_SCHEMA:
        raise EnvelopeValidationError("unsupported message schema")
    for field in ("message_id", "subject_type", "subject_id", "payload_schema"):
        if not isinstance(value[field], str) or not value[field]:
            raise EnvelopeValidationError(f"{field} must be a non-empty string")
    kind = value["message_kind"]
    if kind not in MESSAGE_KINDS:
        raise EnvelopeValidationError("unsupported message kind")
    roles = {role.value for role in C8WireRole}
    if value["sender_role"] not in roles or value["receiver_role"] not in roles:
        raise EnvelopeValidationError("unsupported sender/receiver role")
    if not isinstance(value["payload"], dict):
        raise EnvelopeValidationError("payload must be a JSON object")
    timestamp = value["send_monotonic_ns"]
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
        raise EnvelopeValidationError("send_monotonic_ns must be a non-negative integer")
    canonical_json_bytes(value)
    return value


def encode_frame(envelope: Mapping[str, Any]) -> bytes:
    validated = validate_envelope(dict(envelope))
    payload = canonical_json_bytes(validated)
    length = len(payload)
    if length <= 0 or length > C8_MAX_FRAME_BYTES:
        raise FrameSizeError(f"frame payload length {length} is outside the frozen bound")
    return HEADER.pack(length) + payload


def decode_frame_bytes(frame: bytes) -> dict[str, Any]:
    if len(frame) < HEADER_BYTES:
        raise TruncatedFrameError("frame is missing the four-byte header")
    (length,) = HEADER.unpack(frame[:HEADER_BYTES])
    if length <= 0 or length > C8_MAX_FRAME_BYTES:
        raise FrameSizeError(f"frame payload length {length} is outside the frozen bound")
    if len(frame) != HEADER_BYTES + length:
        raise TruncatedFrameError("frame byte count does not match declared payload length")
    value = decode_canonical_json(frame[HEADER_BYTES:])
    return validate_envelope(value)


def _validated_timeout(timeout_s: float) -> float:
    if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool):
        raise TypeError("frame timeout must be numeric")
    timeout = float(timeout_s)
    if timeout <= 0.0:
        raise ValueError("frame timeout must be positive")
    return timeout


def recv_exact(sock: socket.socket, size: int, *, timeout_s: float = FRAME_IO_TIMEOUT_S) -> bytes:
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError("recv size must be a non-negative integer")
    timeout = _validated_timeout(timeout_s)
    old_timeout = sock.gettimeout()
    chunks: list[bytes] = []
    remaining = size
    try:
        sock.settimeout(timeout)
        while remaining:
            try:
                chunk = sock.recv(remaining)
            except socket.timeout as exc:
                raise FrameTimeoutError(
                    f"frame receive deadline exceeded with {remaining} bytes still required"
                ) from exc
            if not chunk:
                raise TruncatedFrameError(f"socket closed with {remaining} bytes still required")
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        sock.settimeout(old_timeout)
    return b"".join(chunks)


def recv_envelope(sock: socket.socket, *, timeout_s: float = FRAME_IO_TIMEOUT_S) -> dict[str, Any]:
    header = recv_exact(sock, HEADER_BYTES, timeout_s=timeout_s)
    (length,) = HEADER.unpack(header)
    if length <= 0 or length > C8_MAX_FRAME_BYTES:
        raise FrameSizeError(f"frame payload length {length} is outside the frozen bound")
    payload = recv_exact(sock, length, timeout_s=timeout_s)
    value = decode_canonical_json(payload)
    return validate_envelope(value)


def send_envelope(
    sock: socket.socket,
    envelope: Mapping[str, Any],
    *,
    timeout_s: float = FRAME_IO_TIMEOUT_S,
) -> None:
    timeout = _validated_timeout(timeout_s)
    old_timeout = sock.gettimeout()
    try:
        sock.settimeout(timeout)
        try:
            sock.sendall(encode_frame(envelope))
        except socket.timeout as exc:
            raise FrameTimeoutError("frame send deadline exceeded") from exc
    finally:
        sock.settimeout(old_timeout)


def connect_loopback(
    host: str,
    port: int,
    *,
    timeout_s: float = 5.0,
    retry_interval_s: float = 0.01,
) -> socket.socket:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("C8.2 reference transport is restricted to loopback")
    deadline = time.monotonic() + timeout_s
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            sock.connect((host, port))
            sock.settimeout(None)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
            time.sleep(retry_interval_s)
    raise ConnectionError(f"unable to connect to loopback endpoint {host}:{port}") from last_error
