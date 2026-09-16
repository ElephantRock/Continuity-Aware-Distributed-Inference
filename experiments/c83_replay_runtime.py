from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import selectors
import socket
import time
from typing import Any, Callable, Iterable, Mapping

from continuity.core import ContinuityCore
from continuity.entities import AttemptAuthority, ReconcileOutcome, ReplicaStatus, SemanticEvent
from continuity.errors import InsufficientEvidence, InvalidTransition, SemanticViolation
from continuity.invariants import InvariantOracle
from experiments.c83_replay_protocol import C83A_TRACE_SPECS
from experiments.c83_replay_semantics import (
    ReplayObservation,
    _exact,
    _finish_partial_migration,
    _finish_waiting_state,
    _setup_ambiguous_binding,
    _setup_binding_replacement,
    _setup_partial_migration,
    _setup_sibling_state,
    _setup_waiting_state,
    _x1_projection,
    _x2_projection,
    _x3_projection,
    _x4_projection,
    _x5_projection,
)
from prototype.c8_events import EventLogger, read_events
from prototype.c8_faults import DeliveryAction, DeliveryScheduler, QueueCapacityError
from prototype.c8_transport import (
    TruncatedFrameError,
    connect_loopback,
    encode_frame,
    make_envelope,
    recv_envelope,
    send_envelope,
)
from prototype.c8_wire_contract import C8WireRole
from prototype.c8_worker import worker_process_main


REGISTER_SCHEMA = "cadi.c8.2.authority-register.v1"
WORK_SCHEMA = "cadi.c8.2.synthetic-work.v1"
HARNESS_REGISTER_SCHEMA = "cadi.c8.2.harness-register.v1"
FAILURE_SCHEMA = "cadi.c8.2.process-failure.v1"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _append_jsonl(path: str | Path, value: Mapping[str, Any]) -> None:
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(_canonical_json(dict(value)) + "\n")
        handle.flush()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    values: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("C8.3 replay JSONL entry must be an object")
            values.append(value)
    return values


def _scheduler_map(
    rules: Mapping[str, Iterable[str]] | None,
    *,
    capacity: int,
) -> dict[str, DeliveryScheduler]:
    return {
        kind: DeliveryScheduler(
            tuple(DeliveryAction(action) for action in actions),
            capacity=capacity,
        )
        for kind, actions in (rules or {}).items()
    }


def replay_harness_process_main(
    authority_host: str,
    authority_port: int,
    worker_host: str,
    worker_port: int,
    event_log_path: str | Path,
    control_conn: Any,
    upstream_faults: Mapping[str, tuple[str, ...]] | None = None,
    downstream_faults: Mapping[str, tuple[str, ...]] | None = None,
    queue_capacity: int = 64,
) -> None:
    """External C8.3 transport harness with explicit transport-only release controls.

    This process reuses the frozen C8.2 wire format, roles, frame codec, and
    DeliveryScheduler.  The parent experiment orchestrator may release held frames,
    but it has no ContinuityCore and cannot perform semantic mutation.
    """

    if authority_host not in {"127.0.0.1", "localhost"}:
        raise ValueError("authority endpoint must be loopback")
    if worker_host not in {"127.0.0.1", "localhost"}:
        raise ValueError("worker endpoint must be loopback")
    if not isinstance(worker_port, int) or isinstance(worker_port, bool) or not (0 <= worker_port <= 65535):
        raise ValueError("worker port must be in [0, 65535]")

    pid = os.getpid()
    logger = EventLogger(event_log_path, C8WireRole.FAULT_TRANSPORT_HARNESS)
    logger.emit(
        action="PROCESS_STARTED",
        result="OK",
        details={"pid_source": "os.getpid", "c83_manual_release": True},
    )
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
    upstream.setblocking(True)
    register = make_envelope(
        message_id=f"harness:{pid}:1",
        message_kind="REGISTER",
        sender_role=C8WireRole.FAULT_TRANSPORT_HARNESS,
        receiver_role=C8WireRole.CONTROL_PLANE_AUTHORITY,
        subject_type="PROCESS",
        subject_id="fault-transport-harness",
        payload_schema=HARNESS_REGISTER_SCHEMA,
        payload={"pid": pid, "listen_host": bound_host, "listen_port": int(bound_port)},
    )
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

    def raw_send(sock: socket.socket, frame: bytes, *, direction: str, kind: str) -> None:
        sock.sendall(frame)
        logger.emit(
            action="FRAME_FORWARDED",
            result=direction,
            details={"message_kind": kind, "frame_bytes": len(frame)},
        )

    def queue_or_send(
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
            raw_send(destination, ready, direction=direction, kind=kind)
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

    def flush_upstream(mode: str) -> None:
        for kind, scheduler in upstream_schedulers.items():
            frames = (
                scheduler.flush_delayed()
                if mode == "DELAYED"
                else scheduler.flush_late()
                if mode == "LATE"
                else scheduler.flush_reorder()
            )
            for frame in frames:
                raw_send(
                    upstream,
                    frame,
                    direction=f"WORKER_TO_AUTHORITY_{mode}",
                    kind=kind,
                )
        logger.emit(action="TRANSPORT_RELEASE", result=mode)

    def flush_downstream(mode: str) -> None:
        if worker is None:
            raise RuntimeError("cannot release downstream frames without a worker")
        for kind, scheduler in downstream_schedulers.items():
            frames = (
                scheduler.flush_delayed()
                if mode == "DELAYED"
                else scheduler.flush_late()
                if mode == "LATE"
                else scheduler.flush_reorder()
            )
            for frame in frames:
                raw_send(
                    worker,
                    frame,
                    direction=f"AUTHORITY_TO_WORKER_{mode}",
                    kind=kind,
                )
        logger.emit(action="TRANSPORT_RELEASE", result=f"DOWNSTREAM_{mode}")

    def consume_control() -> bool:
        while control_conn.poll():
            command = control_conn.recv()
            if command == "STOP":
                logger.emit(action="CONTROL", result="STOP")
                return False
            if command == "FLUSH_DELAYED_UPSTREAM":
                upstream.setblocking(True)
                try:
                    flush_upstream("DELAYED")
                finally:
                    upstream.setblocking(False)
                continue
            if command == "FLUSH_LATE_UPSTREAM":
                upstream.setblocking(True)
                try:
                    flush_upstream("LATE")
                finally:
                    upstream.setblocking(False)
                continue
            if command == "FLUSH_REORDER_UPSTREAM":
                upstream.setblocking(True)
                try:
                    flush_upstream("REORDER")
                finally:
                    upstream.setblocking(False)
                continue
            if command == "FLUSH_DELAYED_DOWNSTREAM":
                if worker is not None:
                    worker.setblocking(True)
                    try:
                        flush_downstream("DELAYED")
                    finally:
                        worker.setblocking(False)
                continue
            raise ValueError(f"unknown replay harness control command: {command!r}")
        return True

    try:
        running = True
        while running:
            running = consume_control()
            if not running:
                break
            ready = selector.select(timeout=0.02)
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
                    logger.emit(
                        action="WORKER_SOCKET_ACCEPTED",
                        result="OK",
                        details={"peer": list(peer)},
                    )
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
                        logger.emit(
                            action="HARNESS_CONTROL_CONSUMED",
                            result=message["message_kind"],
                            message=message,
                        )
                        continue
                    if receiver != C8WireRole.WORKER.value:
                        logger.emit(
                            action="MESSAGE_REJECTED",
                            result="INVALID_DOWNSTREAM_RECEIVER",
                            message=message,
                        )
                        continue
                    if worker is None:
                        logger.emit(action="ROUTE_FAILURE", result="NO_WORKER_SOCKET", message=message)
                        continue
                    if message["message_kind"] == "SHUTDOWN":
                        worker_shutdown_expected = True
                    worker.setblocking(True)
                    try:
                        queue_or_send(
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
                            logger.emit(
                                action="MESSAGE_SENT",
                                result="PROCESS_FAILURE",
                                message=failure,
                            )
                        worker_pid = None
                        worker_id = None
                        worker_shutdown_expected = False
                        continue
                    finally:
                        if worker is not None:
                            worker.setblocking(False)

                    logger.emit(action="MESSAGE_RECEIVED", result="FROM_WORKER", message=message)
                    if message["sender_role"] != C8WireRole.WORKER.value:
                        logger.emit(
                            action="MESSAGE_REJECTED",
                            result="INVALID_UPSTREAM_SENDER",
                            message=message,
                        )
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
                        queue_or_send(
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
        control_conn.close()


def _authority_message_id(pid: int, counter: int) -> str:
    return f"c83-authority:{pid}:{counter}"


def _send_authority(
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
        message_id=_authority_message_id(pid, counter),
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


def _authority_setup(trace_id: str) -> ContinuityCore:
    if trace_id == "C8-X1-LATE-SUPERSEDED-ATTEMPT":
        core = ContinuityCore()
        core.create_program("p")
        core.create_session("s", "p")
        core.create_continuation("c", "s")
        core.create_request("r", "c")
        core.start_attempt("a1", "r")
        return core
    if trace_id == "C8-X2-DUPLICATE-COMPLETION":
        core = ContinuityCore()
        core.create_program("p")
        core.create_session("s", "p")
        core.create_continuation("c", "s")
        core.create_request("r", "c")
        core.start_attempt("a1", "r")
        return core
    if trace_id == "C8-X3-WRONG-SIBLING-STATE":
        return _setup_sibling_state()[0]
    if trace_id == "C8-X4-STALE-BINDING":
        return _setup_binding_replacement()
    if trace_id == "C8-X5-AMBIGUOUS-OWNERSHIP":
        return _setup_ambiguous_binding()
    if trace_id == "C8-X6-STATE-EVICTION-TOOL-WAIT":
        return _setup_waiting_state()
    if trace_id == "C8-X7-PARTIAL-MIGRATION":
        return _setup_partial_migration()
    raise ValueError(f"unknown C8.3 trace: {trace_id}")


def replay_authority_process_main(
    trace_id: str,
    host: str,
    port: int,
    event_log_path: str | Path,
    semantic_log_path: str | Path,
) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("C8.3 authority must bind to loopback")
    pid = os.getpid()
    logger = EventLogger(event_log_path, C8WireRole.CONTROL_PLANE_AUTHORITY)
    core = _authority_setup(trace_id)
    logger.emit(
        action="PROCESS_STARTED",
        result="OK",
        details={
            "pid_source": "os.getpid",
            "continuity_core_owned": True,
            "trace_id": trace_id,
        },
    )

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

    counter = 0
    harness_pid: int | None = None
    worker_generation = 0
    pid_to_generation: dict[int, int] = {}
    complete_count = 0
    finalization_count = 0
    checkpoint_ids: set[str] = set()

    def emit_checkpoint(observation: ReplayObservation) -> None:
        if observation.checkpoint_id in checkpoint_ids:
            raise RuntimeError(f"duplicate C8 checkpoint: {observation.checkpoint_id}")
        checkpoint_ids.add(observation.checkpoint_id)
        _append_jsonl(
            semantic_log_path,
            {
                "trace_id": trace_id,
                "checkpoint_id": observation.checkpoint_id,
                "raw_outcome": observation.raw_outcome,
                "projection": observation.projection,
            },
        )
        logger.emit(
            action="C83_CHECKPOINT",
            result=observation.checkpoint_id,
            details={"raw_outcome": observation.raw_outcome},
        )
        InvariantOracle(core).assert_all()

    def finalize_attempt(attempt_id: str, output_id: str, evidence_id: str) -> None:
        nonlocal finalization_count
        core.complete_attempt(attempt_id)
        if evidence_id not in core.evidence:
            _exact(core, evidence_id, {("attempt", attempt_id)})
        if output_id not in core.outputs:
            core.create_output(output_id, attempt_id, True, [evidence_id])
        before = core.requests["r"].authoritative_output_id
        core.finalize_request("r", output_id, now=10.0)
        after = core.requests["r"].authoritative_output_id
        if before is None and after is not None:
            finalization_count += 1

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
                response = _send_authority(
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
                logger.emit(action="HARNESS_REGISTERED", result="OK", message=message)
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
                worker_generation += 1
                pid_to_generation[reported_pid] = worker_generation
                counter += 1
                registered = _send_authority(
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

                if trace_id == "C8-X4-STALE-BINDING" and worker_generation == 1:
                    core.commit_migration("b2", ["e2"], now=10.0)
                    emit_checkpoint(
                        ReplayObservation(
                            "replacement-binding-commit",
                            "NEW_BINDING_COMMITTED"
                            if core.current_binding_by_subject["subject"] == "b2"
                            else "SEMANTIC_CHECKPOINT_FAILED",
                            _x4_projection(core),
                        )
                    )

                counter += 1
                work_id = f"c83:{trace_id}:work:{worker_generation}"
                work_input = f"c83:{trace_id}:{worker_generation}"
                work = _send_authority(
                    conn,
                    pid=pid,
                    counter=counter,
                    kind="WORK",
                    receiver=C8WireRole.WORKER,
                    subject_type="C83_REPLAY_WORK",
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
                if not isinstance(reported_pid, int) or reported_pid not in pid_to_generation:
                    logger.emit(action="MESSAGE_REJECTED", result="UNKNOWN_WORKER_PID", message=message)
                    continue
                generation = pid_to_generation[reported_pid]
                complete_count += 1

                if trace_id == "C8-X1-LATE-SUPERSEDED-ATTEMPT":
                    if generation == 2:
                        finalize_attempt("a2", "o2", "e2")
                        emit_checkpoint(
                            ReplayObservation(
                                "current-attempt-finalize",
                                "CURRENT_ATTEMPT_COMMITTED"
                                if core.requests["r"].committed_attempt_id == "a2"
                                else "SEMANTIC_CHECKPOINT_FAILED",
                                _x1_projection(core, stale=False),
                            )
                        )
                    elif generation == 1:
                        core.complete_attempt("a1")
                        if "e1" not in core.evidence:
                            _exact(core, "e1", {("attempt", "a1")})
                        if "o1" not in core.outputs:
                            core.create_output("o1", "a1", True, ["e1"])
                        rejected = False
                        try:
                            core.finalize_request("r", "o1", now=10.0)
                        except (InvalidTransition, SemanticViolation):
                            rejected = True
                        committed = core.requests["r"].committed_attempt_id == "a1"
                        emit_checkpoint(
                            ReplayObservation(
                                "stale-attempt-presentation",
                                "STALE_ATTEMPT_COMMITTED"
                                if committed
                                else "STALE_ATTEMPT_FENCED"
                                if rejected
                                else "SEMANTIC_CHECKPOINT_FAILED",
                                _x1_projection(core, stale=True),
                            )
                        )
                    continue

                if trace_id == "C8-X2-DUPLICATE-COMPLETION":
                    if complete_count == 1:
                        finalize_attempt("a1", "o1", "e1")
                        emit_checkpoint(
                            ReplayObservation(
                                "first-finalization",
                                "FIRST_FINALIZATION",
                                _x2_projection(core, finalization_count),
                            )
                        )
                    elif complete_count == 2:
                        before = core.requests["r"]
                        core.finalize_request("r", "o1", now=10.0)
                        changed = core.requests["r"] != before
                        if changed:
                            finalization_count += 1
                        emit_checkpoint(
                            ReplayObservation(
                                "duplicate-presentation",
                                "DUPLICATE_FINALIZATION" if changed else "IDEMPOTENT_FINALIZE",
                                _x2_projection(core, finalization_count),
                            )
                        )
                    continue

                if trace_id == "C8-X3-WRONG-SIBLING-STATE":
                    ctx = _setup_sibling_state()[1]
                    compatible = core.state_compatible("x1", ctx)
                    consumed = compatible
                    emit_checkpoint(
                        ReplayObservation(
                            "incompatible-state-consume",
                            "WRONG_SIBLING_STATE_CONSUMED" if consumed else "STATE_INCOMPATIBLE",
                            _x3_projection(core, compatible=compatible, consumed=consumed),
                        )
                    )
                    continue

                if trace_id == "C8-X4-STALE-BINDING":
                    old = core.bindings["b1"]
                    core.record_event(
                        SemanticEvent(
                            "c83-late-old-owner",
                            "BINDING_ACTIVE_OBSERVED",
                            "binding",
                            "b1",
                            frozenset({("epoch", str(old.epoch)), ("location", "w1")}),
                        )
                    )
                    restored = core.current_binding_by_subject["subject"] == "b1"
                    emit_checkpoint(
                        ReplayObservation(
                            "stale-binding-presentation",
                            "STALE_BINDING_RESTORED" if restored else "STALE_BINDING_IGNORED",
                            _x4_projection(core),
                        )
                    )
                    continue

                if trace_id == "C8-X5-AMBIGUOUS-OWNERSHIP":
                    candidate = core.bindings["b2"]
                    outcome = core.reconcile(
                        "commit_migration",
                        ["ambiguous"],
                        now=10.0,
                        required_scope={("binding", "b2"), ("epoch", str(candidate.epoch))},
                    )
                    committed = core.current_binding_by_subject["subject"] == "b2"
                    emit_checkpoint(
                        ReplayObservation(
                            "ambiguous-reconciliation",
                            "AMBIGUOUS_OWNERSHIP_COMMITTED"
                            if committed
                            else "RECONCILE_AMBIGUOUS"
                            if outcome is ReconcileOutcome.AMBIGUOUS
                            else "SEMANTIC_CHECKPOINT_FAILED",
                            _x5_projection(core, outcome),
                        )
                    )
                    continue

            if kind == "PROCESS_FAILURE" and sender == C8WireRole.FAULT_TRANSPORT_HARNESS.value:
                failed_pid = payload.get("pid")
                generation = pid_to_generation.get(failed_pid) if isinstance(failed_pid, int) else None
                logger.emit(
                    action="PROCESS_FAILURE_OBSERVED",
                    result="EXPLICIT_NON_SEMANTIC_OBSERVATION",
                    message=message,
                    details={"failed_pid": failed_pid, "worker_generation": generation},
                )
                if trace_id == "C8-X1-LATE-SUPERSEDED-ATTEMPT" and generation == 1:
                    if "a2" not in core.attempts:
                        core.start_attempt("a2", "r")
                    logger.emit(action="C83_LIFECYCLE", result="GENERATION_2_READY")
                    continue
                if trace_id == "C8-X6-STATE-EVICTION-TOOL-WAIT" and generation == 1:
                    core.set_replica_status("rp", ReplicaStatus.LOST)
                    observation = _finish_waiting_state(core)
                    emit_checkpoint(
                        ReplayObservation(
                            observation.checkpoint_id,
                            "LOST_REPLICA_CONSUMED"
                            if observation.raw_outcome == "LOST_REPLICA_CONSUMED"
                            else "REPLICA_LOST_REUSE_REJECTED",
                            observation.projection,
                        )
                    )
                    continue
                if trace_id == "C8-X7-PARTIAL-MIGRATION" and generation == 1:
                    core.set_replica_status("dst", ReplicaStatus.LOST)
                    observation = _finish_partial_migration(core, "PARTIAL")
                    emit_checkpoint(
                        ReplayObservation(
                            observation.checkpoint_id,
                            "PARTIAL_DESTINATION_COMMITTED"
                            if observation.raw_outcome == "PARTIAL_DESTINATION_COMMITTED"
                            else "INSUFFICIENT_EVIDENCE"
                            if observation.raw_outcome == "INSUFFICIENT_EVIDENCE"
                            else "SEMANTIC_CHECKPOINT_FAILED",
                            observation.projection,
                        )
                    )
                    continue
                continue

            logger.emit(action="MESSAGE_REJECTED", result="UNEXPECTED_KIND_OR_SENDER", message=message)
    finally:
        if conn is not None:
            conn.close()
        listener.close()


def _wait_until(
    path: str | Path,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for value in read_events(path):
            if predicate(value):
                return value
        time.sleep(0.01)
    raise TimeoutError(f"expected replay event not observed in {path}")


def _event_port(path: str | Path, action: str) -> int:
    event = _wait_until(path, lambda item: item.get("action") == action)
    details = event.get("details")
    if not isinstance(details, dict):
        raise RuntimeError(f"{action} event lacks details")
    port = details.get("port")
    if (
        not isinstance(port, int)
        or isinstance(port, bool)
        or not (1 <= port <= 65535)
        or details.get("port_source") != "kernel_bind"
    ):
        raise RuntimeError(f"{action} did not self-report a kernel-bound port")
    return port


class C83RealReplayRuntime:
    def __init__(self, trace_id: str, work_dir: str | Path) -> None:
        self.spec = next(item for item in C83A_TRACE_SPECS if item.trace_id == trace_id)
        self.trace_id = trace_id
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.authority_log = self.work_dir / "authority.jsonl"
        self.harness_log = self.work_dir / "harness.jsonl"
        self.semantic_log = self.work_dir / "semantic.jsonl"
        self.worker_logs: list[Path] = []
        self.host = "127.0.0.1"
        self.authority_port = 0
        self.worker_port = 0
        self._ctx = mp.get_context("spawn")
        self._parent_control, self._child_control = self._ctx.Pipe(duplex=True)
        self.authority: mp.Process | None = None
        self.harness: mp.Process | None = None
        self.workers: list[mp.Process] = []

    def start(self) -> None:
        if self.authority is not None:
            raise RuntimeError("replay runtime already started")
        self.authority = self._ctx.Process(
            target=replay_authority_process_main,
            args=(self.trace_id, self.host, 0, self.authority_log, self.semantic_log),
            name=f"c83-authority-{self.trace_id}",
        )
        self.authority.start()
        self.authority_port = _event_port(self.authority_log, "LISTENING")
        upstream_faults = {
            kind: tuple(action.value for action in actions)
            for kind, actions in self.spec.c8_fault_script
        }
        self.harness = self._ctx.Process(
            target=replay_harness_process_main,
            args=(
                self.host,
                self.authority_port,
                self.host,
                0,
                self.harness_log,
                self._child_control,
                upstream_faults,
                {},
                64,
            ),
            name=f"c83-harness-{self.trace_id}",
        )
        self.harness.start()
        self.worker_port = _event_port(self.harness_log, "WORKER_LISTENING")
        _wait_until(self.authority_log, lambda item: item.get("action") == "HARNESS_REGISTERED")

    def start_worker(self, *, work_delay_s: float = 0.0) -> mp.Process:
        generation = len(self.workers) + 1
        log_path = self.work_dir / f"worker-{generation}.jsonl"
        self.worker_logs.append(log_path)
        worker = self._ctx.Process(
            target=worker_process_main,
            args=(
                self.host,
                self.worker_port,
                f"worker-{generation}",
                log_path,
                work_delay_s,
            ),
            name=f"c83-worker-{generation}",
        )
        worker.start()
        self.workers.append(worker)
        _wait_until(log_path, lambda item: item.get("action") == "PROCESS_STARTED")
        return worker

    def terminate_worker(self, generation: int, *, timeout_s: float = 5.0) -> int:
        worker = self.workers[generation - 1]
        if worker.pid is None:
            raise RuntimeError("worker has no PID")
        pid = worker.pid
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout_s)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout_s)
        if worker.is_alive():
            raise RuntimeError("worker could not be terminated")
        return pid

    def control(self, command: str) -> None:
        self._parent_control.send(command)

    def wait_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        return _wait_until(
            self.authority_log,
            lambda item: item.get("action") == "C83_CHECKPOINT"
            and item.get("result") == checkpoint_id,
        )

    def wait_held_complete(self) -> dict[str, Any]:
        return _wait_until(
            self.harness_log,
            lambda item: item.get("action") == "FRAME_HELD_OR_DROPPED"
            and item.get("message_kind") == "COMPLETE",
        )

    def run(self) -> tuple[ReplayObservation, ...]:
        self.start()
        try:
            if self.trace_id == "C8-X1-LATE-SUPERSEDED-ATTEMPT":
                self.start_worker()
                self.wait_held_complete()
                self.terminate_worker(1)
                _wait_until(
                    self.authority_log,
                    lambda item: item.get("action") == "C83_LIFECYCLE"
                    and item.get("result") == "GENERATION_2_READY",
                )
                self.start_worker()
                self.wait_checkpoint("current-attempt-finalize")
                self.control("FLUSH_LATE_UPSTREAM")
                self.wait_checkpoint("stale-attempt-presentation")
            elif self.trace_id == "C8-X2-DUPLICATE-COMPLETION":
                self.start_worker()
                self.wait_checkpoint("first-finalization")
                self.wait_checkpoint("duplicate-presentation")
            elif self.trace_id == "C8-X3-WRONG-SIBLING-STATE":
                self.start_worker()
                self.wait_checkpoint("incompatible-state-consume")
            elif self.trace_id == "C8-X4-STALE-BINDING":
                self.start_worker()
                self.wait_checkpoint("replacement-binding-commit")
                self.wait_held_complete()
                self.control("FLUSH_DELAYED_UPSTREAM")
                self.wait_checkpoint("stale-binding-presentation")
            elif self.trace_id == "C8-X5-AMBIGUOUS-OWNERSHIP":
                self.start_worker()
                self.wait_checkpoint("ambiguous-reconciliation")
            elif self.trace_id == "C8-X6-STATE-EVICTION-TOOL-WAIT":
                self.start_worker(work_delay_s=10.0)
                _wait_until(
                    self.authority_log,
                    lambda item: item.get("action") == "WORKER_REGISTERED",
                )
                self.terminate_worker(1)
                self.wait_checkpoint("resume-after-eviction")
            elif self.trace_id == "C8-X7-PARTIAL-MIGRATION":
                self.start_worker(work_delay_s=10.0)
                _wait_until(
                    self.authority_log,
                    lambda item: item.get("action") == "WORKER_REGISTERED",
                )
                self.terminate_worker(1)
                self.wait_checkpoint("partial-migration-commit")
            else:
                raise ValueError(f"unknown C8.3 trace: {self.trace_id}")

            values = read_jsonl(self.semantic_log)
            observations = tuple(
                ReplayObservation(
                    str(item["checkpoint_id"]),
                    str(item["raw_outcome"]),
                    dict(item["projection"]),
                )
                for item in values
            )
            expected_ids = tuple(item.checkpoint_id for item in self.spec.checkpoints)
            actual_ids = tuple(item.checkpoint_id for item in observations)
            if actual_ids != expected_ids:
                raise RuntimeError(
                    f"C8 checkpoint order mismatch: expected {expected_ids}, got {actual_ids}"
                )
            return observations
        finally:
            self.stop()

    def topology(self) -> dict[str, Any]:
        authority_event = _wait_until(
            self.authority_log,
            lambda item: item.get("action") == "PROCESS_STARTED",
        )
        harness_event = _wait_until(
            self.harness_log,
            lambda item: item.get("action") == "PROCESS_STARTED",
        )
        worker_pids = [
            int(
                _wait_until(path, lambda item: item.get("action") == "PROCESS_STARTED")["pid"]
            )
            for path in self.worker_logs
        ]
        return {
            "authority_pid": int(authority_event["pid"]),
            "authority_port": self.authority_port,
            "fault_harness_pid": int(harness_event["pid"]),
            "worker_port": self.worker_port,
            "worker_pids": worker_pids,
            "transport_id": "LOOPBACK_TCP_LENGTH_PREFIXED_CANONICAL_JSON_V1",
            "real_process_boundary": True,
        }

    def stop(self, *, timeout_s: float = 3.0) -> None:
        try:
            if self.harness is not None and self.harness.is_alive():
                try:
                    self.control("STOP")
                except (BrokenPipeError, EOFError, OSError):
                    pass
        finally:
            for process in [*self.workers, self.harness, self.authority]:
                if process is None:
                    continue
                if process.is_alive():
                    process.terminate()
                process.join(timeout_s)
                if process.is_alive():
                    process.kill()
                    process.join(timeout_s)
            self._parent_control.close()
            self._child_control.close()
