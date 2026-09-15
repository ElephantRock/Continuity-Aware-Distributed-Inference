from __future__ import annotations

import hashlib
import os
from pathlib import Path
import socket
from typing import Any

from continuity.core import ContinuityCore

from .c8_events import EventLogger
from .c8_transport import (
    TruncatedFrameError,
    make_envelope,
    recv_envelope,
    send_envelope,
)
from .c8_wire_contract import C8WireRole


REGISTER_SCHEMA = "cadi.c8.2.authority-register.v1"
WORK_SCHEMA = "cadi.c8.2.synthetic-work.v1"
CONTROL_SCHEMA = "cadi.c8.2.control.v1"


def _message_id(pid: int, counter: int) -> str:
    return f"authority:{pid}:{counter}"


def _send(
    conn: socket.socket,
    *,
    pid: int,
    counter: int,
    kind: str,
    receiver: C8WireRole,
    subject_type: str,
    subject_id: str,
    payload_schema: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    message = make_envelope(
        message_id=_message_id(pid, counter),
        message_kind=kind,
        sender_role=C8WireRole.CONTROL_PLANE_AUTHORITY,
        receiver_role=receiver,
        subject_type=subject_type,
        subject_id=subject_id,
        payload_schema=payload_schema,
        payload=payload,
    )
    send_envelope(conn, message)
    return message


def authority_process_main(
    host: str,
    port: int,
    event_log_path: str | Path,
    *,
    shutdown_worker_after_completion: bool = True,
) -> None:
    """Run the sole authoritative process for the C8.2 substrate."""

    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("C8.2 authority must bind to loopback")
    if not isinstance(port, int) or isinstance(port, bool) or port < 0 or port > 65535:
        raise ValueError("authority port must be an integer in [0, 65535]")
    pid = os.getpid()
    logger = EventLogger(event_log_path, C8WireRole.CONTROL_PLANE_AUTHORITY)
    core = ContinuityCore()
    logger.emit(
        action="PROCESS_STARTED",
        result="OK",
        details={
            "pid_source": "os.getpid",
            "continuity_core_owned": True,
            "core_type": type(core).__name__,
        },
    )

    counter = 0
    harness_pid: int | None = None
    current_worker_pid: int | None = None
    current_worker_id: str | None = None
    worker_generation = 0

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(1)
    bound_host, bound_port = listener.getsockname()
    logger.emit(
        action="LISTENING",
        result="OK",
        details={"host": bound_host, "port": int(bound_port), "port_source": "kernel_bind"},
    )

    conn: socket.socket | None = None
    try:
        conn, peer = listener.accept()
        logger.emit(action="HARNESS_SOCKET_ACCEPTED", result="OK", details={"peer": list(peer)})
        while True:
            try:
                message = recv_envelope(conn)
            except TruncatedFrameError:
                logger.emit(action="TRANSPORT_CLOSED", result="HARNESS_DISCONNECTED")
                return
            logger.emit(action="MESSAGE_RECEIVED", result=message["message_kind"], message=message)
            if message["receiver_role"] != C8WireRole.CONTROL_PLANE_AUTHORITY.value:
                logger.emit(action="MESSAGE_REJECTED", result="WRONG_RECEIVER", message=message)
                continue

            kind = message["message_kind"]
            sender = message["sender_role"]
            payload = message["payload"]

            if kind == "REGISTER" and sender == C8WireRole.FAULT_TRANSPORT_HARNESS.value:
                reported_pid = payload.get("pid")
                if not isinstance(reported_pid, int) or reported_pid <= 0:
                    logger.emit(action="MESSAGE_REJECTED", result="INVALID_HARNESS_PID", message=message)
                    continue
                harness_pid = reported_pid
                counter += 1
                response = _send(
                    conn,
                    pid=pid,
                    counter=counter,
                    kind="REGISTERED",
                    receiver=C8WireRole.FAULT_TRANSPORT_HARNESS,
                    subject_type="PROCESS",
                    subject_id=message["subject_id"],
                    payload_schema=REGISTER_SCHEMA,
                    payload={"authority_pid": pid, "harness_pid": harness_pid},
                )
                logger.emit(action="HARNESS_REGISTERED", result="OK", message=message, details={"pid": harness_pid})
                logger.emit(action="MESSAGE_SENT", result="REGISTERED", message=response)
                continue

            if kind == "REGISTER" and sender == C8WireRole.WORKER.value:
                if harness_pid is None:
                    logger.emit(action="MESSAGE_REJECTED", result="HARNESS_NOT_REGISTERED", message=message)
                    continue
                reported_pid = payload.get("pid")
                worker_id = payload.get("worker_id")
                loaded = payload.get("continuity_modules_loaded")
                if (
                    not isinstance(reported_pid, int)
                    or reported_pid <= 0
                    or not isinstance(worker_id, str)
                    or not worker_id
                    or loaded != []
                ):
                    logger.emit(action="MESSAGE_REJECTED", result="INVALID_WORKER_REGISTRATION", message=message)
                    continue
                current_worker_pid = reported_pid
                current_worker_id = worker_id
                worker_generation += 1
                counter += 1
                registered = _send(
                    conn,
                    pid=pid,
                    counter=counter,
                    kind="REGISTERED",
                    receiver=C8WireRole.WORKER,
                    subject_type="PROCESS",
                    subject_id=worker_id,
                    payload_schema=REGISTER_SCHEMA,
                    payload={
                        "authority_pid": pid,
                        "harness_pid": harness_pid,
                        "worker_pid": reported_pid,
                        "worker_generation": worker_generation,
                    },
                )
                logger.emit(
                    action="WORKER_REGISTERED",
                    result="OK",
                    message=message,
                    details={
                        "worker_id": worker_id,
                        "worker_pid": reported_pid,
                        "worker_generation": worker_generation,
                    },
                )
                logger.emit(action="MESSAGE_SENT", result="REGISTERED", message=registered)

                counter += 1
                work_id = f"work:{worker_id}:{worker_generation}"
                work_input = f"cadi:{worker_id}:{worker_generation}"
                work = _send(
                    conn,
                    pid=pid,
                    counter=counter,
                    kind="WORK",
                    receiver=C8WireRole.WORKER,
                    subject_type="SYNTHETIC_WORK",
                    subject_id=work_id,
                    payload_schema=WORK_SCHEMA,
                    payload={
                        "operation": "SHA256",
                        "input": work_input,
                        "expected_output": hashlib.sha256(work_input.encode("utf-8")).hexdigest(),
                        "worker_generation": worker_generation,
                    },
                )
                logger.emit(action="MESSAGE_SENT", result="WORK", message=work)
                continue

            if kind == "COMPLETE" and sender == C8WireRole.WORKER.value:
                reported_pid = payload.get("pid")
                expected_current = reported_pid == current_worker_pid
                logger.emit(
                    action="PHYSICAL_COMPLETION_OBSERVED",
                    result="CURRENT_WORKER" if expected_current else "NONCURRENT_WORKER",
                    message=message,
                    details={
                        "reported_pid": reported_pid,
                        "current_worker_pid": current_worker_pid,
                        "timestamp_used_for_authority": False,
                    },
                )
                if shutdown_worker_after_completion and expected_current and current_worker_id:
                    counter += 1
                    shutdown = _send(
                        conn,
                        pid=pid,
                        counter=counter,
                        kind="SHUTDOWN",
                        receiver=C8WireRole.WORKER,
                        subject_type="PROCESS",
                        subject_id=current_worker_id,
                        payload_schema=CONTROL_SCHEMA,
                        payload={"reason": "C8.2_CONFORMANCE_COMPLETE"},
                    )
                    logger.emit(action="MESSAGE_SENT", result="SHUTDOWN", message=shutdown)
                continue

            if kind == "PROCESS_FAILURE" and sender == C8WireRole.FAULT_TRANSPORT_HARNESS.value:
                failed_pid = payload.get("pid")
                logger.emit(
                    action="PROCESS_FAILURE_OBSERVED",
                    result="EXPLICIT_NON_SEMANTIC_OBSERVATION",
                    message=message,
                    details={"failed_pid": failed_pid, "reason": payload.get("reason")},
                )
                if failed_pid == current_worker_pid:
                    current_worker_pid = None
                    current_worker_id = None
                continue

            if kind == "SHUTDOWN_ACK" and sender == C8WireRole.WORKER.value:
                logger.emit(action="WORKER_SHUTDOWN_ACK", result="OK", message=message)
                if payload.get("pid") == current_worker_pid:
                    current_worker_pid = None
                    current_worker_id = None
                continue

            logger.emit(action="MESSAGE_REJECTED", result="UNEXPECTED_KIND_OR_SENDER", message=message)
    finally:
        if conn is not None:
            conn.close()
        listener.close()
