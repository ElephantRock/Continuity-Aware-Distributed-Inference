from __future__ import annotations

import os
from pathlib import Path
import selectors
import socket
from typing import Iterable, Mapping

from .c8_events import EventLogger
from .c8_faults import DeliveryAction, DeliveryScheduler, QueueCapacityError
from .c8_transport import (
    TruncatedFrameError,
    connect_loopback,
    encode_frame,
    make_envelope,
    recv_envelope,
    send_envelope,
)
from .c8_wire_contract import C8WireRole


HARNESS_REGISTER_SCHEMA = "cadi.c8.2.harness-register.v1"
FAILURE_SCHEMA = "cadi.c8.2.process-failure.v1"


def _scheduler_map(
    rules: Mapping[str, Iterable[str]] | None,
    *,
    capacity: int,
) -> dict[str, DeliveryScheduler]:
    return {
        kind: DeliveryScheduler(tuple(DeliveryAction(action) for action in actions), capacity=capacity)
        for kind, actions in (rules or {}).items()
    }


def harness_process_main(
    authority_host: str,
    authority_port: int,
    worker_host: str,
    worker_port: int,
    event_log_path: str | Path,
    upstream_faults: Mapping[str, tuple[str, ...]] | None = None,
    downstream_faults: Mapping[str, tuple[str, ...]] | None = None,
    queue_capacity: int = 64,
) -> None:
    """Run the external transport/fault process between workers and authority."""

    if authority_host not in {"127.0.0.1", "localhost"}:
        raise ValueError("authority endpoint must be loopback")
    if worker_host not in {"127.0.0.1", "localhost"}:
        raise ValueError("worker endpoint must be loopback")
    if not isinstance(worker_port, int) or isinstance(worker_port, bool) or worker_port < 0 or worker_port > 65535:
        raise ValueError("worker port must be an integer in [0, 65535]")

    pid = os.getpid()
    logger = EventLogger(event_log_path, C8WireRole.FAULT_TRANSPORT_HARNESS)
    logger.emit(action="PROCESS_STARTED", result="OK", details={"pid_source": "os.getpid"})

    upstream_schedulers = _scheduler_map(upstream_faults, capacity=queue_capacity)
    downstream_schedulers = _scheduler_map(downstream_faults, capacity=queue_capacity)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((worker_host, worker_port))
    listener.listen(8)
    listener.setblocking(False)
    bound_host, bound_port = listener.getsockname()
    logger.emit(
        action="WORKER_LISTENING",
        result="OK",
        details={
            "host": bound_host,
            "port": int(bound_port),
            "port_source": "kernel_bind",
            "queue_capacity": queue_capacity,
        },
    )

    upstream = connect_loopback(authority_host, authority_port)
    upstream.setblocking(False)
    register = make_envelope(
        message_id=f"harness:{pid}:1",
        message_kind="REGISTER",
        sender_role=C8WireRole.FAULT_TRANSPORT_HARNESS,
        receiver_role=C8WireRole.CONTROL_PLANE_AUTHORITY,
        subject_type="PROCESS",
        subject_id="fault-transport-harness",
        payload_schema=HARNESS_REGISTER_SCHEMA,
        payload={
            "pid": pid,
            "listen_host": bound_host,
            "listen_port": int(bound_port),
        },
    )
    upstream.setblocking(True)
    send_envelope(upstream, register)
    upstream.setblocking(False)
    logger.emit(action="MESSAGE_SENT", result="REGISTER", message=register)

    selector = selectors.DefaultSelector()
    selector.register(listener, selectors.EVENT_READ, "listener")
    selector.register(upstream, selectors.EVENT_READ, "authority")

    worker: socket.socket | None = None
    worker_pid: int | None = None
    worker_id: str | None = None
    worker_shutdown_expected = False
    failure_counter = 1

    def _raw_send(sock: socket.socket, frame: bytes, *, direction: str, kind: str) -> None:
        sock.sendall(frame)
        logger.emit(
            action="FRAME_FORWARDED",
            result=direction,
            details={"message_kind": kind, "frame_bytes": len(frame)},
        )

    def _queue_or_send(
        envelope: dict[str, object],
        destination: socket.socket,
        schedulers: dict[str, DeliveryScheduler],
        *,
        direction: str,
    ) -> None:
        kind = str(envelope["message_kind"])
        frame = encode_frame(envelope)
        scheduler = schedulers.get(kind)
        try:
            frames = scheduler.submit(frame) if scheduler is not None else (frame,)
        except QueueCapacityError:
            logger.emit(
                action="QUEUE_CAPACITY_FAILURE",
                result="EXPLICIT_DROP",
                message=envelope,
                details={"direction": direction, "queue_capacity": queue_capacity},
            )
            return
        for ready in frames:
            _raw_send(destination, ready, direction=direction, kind=kind)
        if scheduler is not None and not frames:
            logger.emit(
                action="FRAME_HELD_OR_DROPPED",
                result="SCRIPTED",
                message=envelope,
                details={
                    "direction": direction,
                    "pending_count": scheduler.pending_count,
                    "dropped": scheduler.dropped,
                },
            )

    def _flush_delayed(
        schedulers: dict[str, DeliveryScheduler],
        destination: socket.socket,
        direction: str,
    ) -> None:
        for kind, scheduler in schedulers.items():
            for frame in scheduler.flush_delayed():
                _raw_send(destination, frame, direction=direction, kind=kind)

    def _flush_late_upstream() -> None:
        for kind, scheduler in upstream_schedulers.items():
            for frame in scheduler.flush_late():
                _raw_send(upstream, frame, direction="WORKER_TO_AUTHORITY_LATE", kind=kind)

    try:
        while True:
            ready = selector.select(timeout=0.05)
            if not ready:
                _flush_delayed(upstream_schedulers, upstream, "WORKER_TO_AUTHORITY_DELAYED")
                if worker is not None:
                    _flush_delayed(downstream_schedulers, worker, "AUTHORITY_TO_WORKER_DELAYED")
                continue

            for key, _mask in ready:
                if key.data == "listener":
                    new_worker, peer = listener.accept()
                    new_worker.setblocking(False)
                    if worker is not None:
                        try:
                            selector.unregister(worker)
                        except Exception:
                            pass
                        worker.close()
                    worker = new_worker
                    worker_pid = None
                    worker_id = None
                    worker_shutdown_expected = False
                    selector.register(worker, selectors.EVENT_READ, "worker")
                    logger.emit(action="WORKER_SOCKET_ACCEPTED", result="OK", details={"peer": list(peer)})
                    continue

                if key.data == "authority":
                    try:
                        upstream.setblocking(True)
                        message = recv_envelope(upstream)
                    except TruncatedFrameError:
                        logger.emit(action="AUTHORITY_DISCONNECTED", result="FAIL")
                        return
                    finally:
                        upstream.setblocking(False)
                    logger.emit(action="MESSAGE_RECEIVED", result="FROM_AUTHORITY", message=message)
                    receiver = message["receiver_role"]
                    if receiver == C8WireRole.FAULT_TRANSPORT_HARNESS.value:
                        logger.emit(action="HARNESS_CONTROL_CONSUMED", result=message["message_kind"], message=message)
                        continue
                    if receiver != C8WireRole.WORKER.value:
                        logger.emit(action="MESSAGE_REJECTED", result="INVALID_DOWNSTREAM_RECEIVER", message=message)
                        continue
                    if worker is None:
                        logger.emit(action="ROUTE_FAILURE", result="NO_WORKER_SOCKET", message=message)
                        continue
                    if message["message_kind"] == "SHUTDOWN":
                        worker_shutdown_expected = True
                    worker.setblocking(True)
                    try:
                        _queue_or_send(
                            message,
                            worker,
                            downstream_schedulers,
                            direction="AUTHORITY_TO_WORKER",
                        )
                    finally:
                        worker.setblocking(False)
                    continue

                if key.data == "worker":
                    assert worker is not None
                    try:
                        worker.setblocking(True)
                        message = recv_envelope(worker)
                    except TruncatedFrameError:
                        try:
                            selector.unregister(worker)
                        except Exception:
                            pass
                        worker.close()
                        worker = None
                        if worker_shutdown_expected:
                            logger.emit(
                                action="WORKER_DISCONNECTED",
                                result="EXPECTED_AFTER_SHUTDOWN",
                                details={"worker_pid": worker_pid, "worker_id": worker_id},
                            )
                        elif worker_pid is not None:
                            failure_counter += 1
                            failure = make_envelope(
                                message_id=f"harness:{pid}:failure:{failure_counter}",
                                message_kind="PROCESS_FAILURE",
                                sender_role=C8WireRole.FAULT_TRANSPORT_HARNESS,
                                receiver_role=C8WireRole.CONTROL_PLANE_AUTHORITY,
                                subject_type="PROCESS",
                                subject_id=worker_id or f"worker-pid-{worker_pid}",
                                payload_schema=FAILURE_SCHEMA,
                                payload={
                                    "pid": worker_pid,
                                    "worker_id": worker_id,
                                    "reason": "WORKER_SOCKET_CLOSED",
                                },
                            )
                            upstream.setblocking(True)
                            try:
                                send_envelope(upstream, failure)
                            finally:
                                upstream.setblocking(False)
                            logger.emit(action="MESSAGE_SENT", result="PROCESS_FAILURE", message=failure)
                        worker_pid = None
                        worker_id = None
                        worker_shutdown_expected = False
                        continue
                    finally:
                        if worker is not None:
                            worker.setblocking(False)

                    logger.emit(action="MESSAGE_RECEIVED", result="FROM_WORKER", message=message)
                    if message["sender_role"] != C8WireRole.WORKER.value:
                        logger.emit(action="MESSAGE_REJECTED", result="INVALID_UPSTREAM_SENDER", message=message)
                        continue
                    if message["message_kind"] == "REGISTER":
                        payload = message["payload"]
                        candidate_pid = payload.get("pid")
                        candidate_id = payload.get("worker_id")
                        if isinstance(candidate_pid, int) and isinstance(candidate_id, str):
                            worker_pid = candidate_pid
                            worker_id = candidate_id
                            upstream.setblocking(True)
                            try:
                                _flush_late_upstream()
                            finally:
                                upstream.setblocking(False)
                    upstream.setblocking(True)
                    try:
                        _queue_or_send(
                            message,
                            upstream,
                            upstream_schedulers,
                            direction="WORKER_TO_AUTHORITY",
                        )
                    finally:
                        upstream.setblocking(False)
    finally:
        selector.close()
        if worker is not None:
            worker.close()
        upstream.close()
        listener.close()
