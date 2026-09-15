from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

from .c8_events import EventLogger
from .c8_transport import connect_loopback, make_envelope, recv_envelope, send_envelope
from .c8_wire_contract import C8WireRole


REGISTER_SCHEMA = "cadi.c8.2.worker-register.v1"
WORK_SCHEMA = "cadi.c8.2.synthetic-work.v1"
COMPLETE_SCHEMA = "cadi.c8.2.synthetic-completion.v1"
CONTROL_SCHEMA = "cadi.c8.2.control.v1"


def _message_id(worker_id: str, pid: int, counter: int) -> str:
    return f"worker:{worker_id}:{pid}:{counter}"


def worker_process_main(
    harness_host: str,
    harness_port: int,
    worker_id: str,
    event_log_path: str | Path,
    work_delay_s: float = 0.0,
) -> None:
    """Run one physical worker without importing Continuity semantic authority."""

    if not isinstance(worker_id, str) or not worker_id:
        raise ValueError("worker_id must be non-empty")
    if work_delay_s < 0.0:
        raise ValueError("work_delay_s must be non-negative")

    pid = os.getpid()
    logger = EventLogger(event_log_path, C8WireRole.WORKER)
    logger.emit(
        action="PROCESS_STARTED",
        result="OK",
        details={"worker_id": worker_id, "pid_source": "os.getpid"},
    )
    sock = connect_loopback(harness_host, harness_port)
    counter = 1
    continuity_modules = sorted(
        name for name in sys.modules if name == "continuity" or name.startswith("continuity.")
    )
    register = make_envelope(
        message_id=_message_id(worker_id, pid, counter),
        message_kind="REGISTER",
        sender_role=C8WireRole.WORKER,
        receiver_role=C8WireRole.CONTROL_PLANE_AUTHORITY,
        subject_type="PROCESS",
        subject_id=worker_id,
        payload_schema=REGISTER_SCHEMA,
        payload={
            "pid": pid,
            "worker_id": worker_id,
            "continuity_modules_loaded": continuity_modules,
        },
    )
    send_envelope(sock, register)
    logger.emit(action="MESSAGE_SENT", result="REGISTER", message=register)

    try:
        while True:
            message = recv_envelope(sock)
            logger.emit(action="MESSAGE_RECEIVED", result=message["message_kind"], message=message)
            # send_monotonic_ns is deliberately not consulted by dispatch/authorization.
            kind = message["message_kind"]
            if kind == "REGISTERED":
                continue
            if kind == "WORK":
                payload = message["payload"]
                operation = payload.get("operation")
                input_value = payload.get("input")
                if work_delay_s:
                    time.sleep(work_delay_s)
                if operation == "SHA256" and isinstance(input_value, str):
                    status = "OK"
                    output = hashlib.sha256(input_value.encode("utf-8")).hexdigest()
                else:
                    status = "ERROR"
                    output = None
                counter += 1
                complete = make_envelope(
                    message_id=_message_id(worker_id, pid, counter),
                    message_kind="COMPLETE",
                    sender_role=C8WireRole.WORKER,
                    receiver_role=C8WireRole.CONTROL_PLANE_AUTHORITY,
                    subject_type=message["subject_type"],
                    subject_id=message["subject_id"],
                    payload_schema=COMPLETE_SCHEMA,
                    payload={
                        "pid": pid,
                        "worker_id": worker_id,
                        "status": status,
                        "output": output,
                        "work_message_id": message["message_id"],
                    },
                )
                send_envelope(sock, complete)
                logger.emit(action="MESSAGE_SENT", result="COMPLETE", message=complete)
                continue
            if kind == "SHUTDOWN":
                counter += 1
                ack = make_envelope(
                    message_id=_message_id(worker_id, pid, counter),
                    message_kind="SHUTDOWN_ACK",
                    sender_role=C8WireRole.WORKER,
                    receiver_role=C8WireRole.CONTROL_PLANE_AUTHORITY,
                    subject_type="PROCESS",
                    subject_id=worker_id,
                    payload_schema=CONTROL_SCHEMA,
                    payload={"pid": pid, "worker_id": worker_id},
                )
                send_envelope(sock, ack)
                logger.emit(action="MESSAGE_SENT", result="SHUTDOWN_ACK", message=ack)
                logger.emit(action="PROCESS_EXITING", result="GRACEFUL")
                return
            logger.emit(
                action="MESSAGE_REJECTED",
                result="UNEXPECTED_KIND",
                message=message,
            )
    finally:
        sock.close()
