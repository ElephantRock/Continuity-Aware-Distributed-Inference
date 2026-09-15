from __future__ import annotations

import ast
import inspect
from pathlib import Path
import socket
import struct
import time

import pytest

from experiments.c8_protocol import (
    C8_MAX_FRAME_BYTES as FROZEN_MAX_FRAME_BYTES,
    C8_MESSAGE_ENVELOPE_FIELDS as FROZEN_MESSAGE_ENVELOPE_FIELDS,
    C8_PROTOCOL_FINGERPRINT,
    C8ProcessRole,
)
from prototype import c8_events, c8_harness, c8_transport, c8_worker
from prototype.c8_events import read_events
from prototype.c8_runtime import C8SubstrateRuntime
from prototype.c8_transport import FrameTimeoutError, recv_envelope
from prototype.c8_wire_contract import (
    C81_PROTOCOL_FINGERPRINT,
    C8_MAX_FRAME_BYTES,
    C8_MESSAGE_ENVELOPE_FIELDS,
    C8WireRole,
)


def _import_roots(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_wire_contract_snapshot_matches_frozen_c81_protocol() -> None:
    assert C81_PROTOCOL_FINGERPRINT == C8_PROTOCOL_FINGERPRINT
    assert C8_MAX_FRAME_BYTES == FROZEN_MAX_FRAME_BYTES
    assert C8_MESSAGE_ENVELOPE_FIELDS == FROZEN_MESSAGE_ENVELOPE_FIELDS
    assert tuple(role.value for role in C8WireRole) == tuple(role.value for role in C8ProcessRole)


def test_non_authority_runtime_modules_do_not_import_experiments_or_continuity() -> None:
    for module in (c8_worker, c8_harness, c8_events, c8_transport):
        roots = _import_roots(module)
        assert "continuity" not in roots
        assert "experiments" not in roots


def test_processes_self_report_kernel_assigned_loopback_ports(tmp_path: Path) -> None:
    runtime = C8SubstrateRuntime(tmp_path)
    try:
        runtime.start(worker_id="worker-port-proof")
        assert 1 <= runtime.authority_port <= 65535
        assert 1 <= runtime.worker_port <= 65535
        assert runtime.authority_port != runtime.worker_port

        authority_listen = next(
            event for event in read_events(runtime.authority_log) if event["action"] == "LISTENING"
        )
        harness_listen = next(
            event for event in read_events(runtime.harness_log) if event["action"] == "WORKER_LISTENING"
        )
        assert authority_listen["details"]["port_source"] == "kernel_bind"
        assert harness_listen["details"]["port_source"] == "kernel_bind"
        assert authority_listen["details"]["port"] == runtime.authority_port
        assert harness_listen["details"]["port"] == runtime.worker_port
    finally:
        runtime.stop()


def test_stalled_partial_frame_fails_closed_on_bounded_deadline() -> None:
    sender, receiver = socket.socketpair()
    try:
        sender.sendall(struct.pack(">I", 10) + b"{}")
        started = time.monotonic()
        with pytest.raises(FrameTimeoutError, match="deadline exceeded"):
            recv_envelope(receiver, timeout_s=0.05)
        assert time.monotonic() - started < 0.5
    finally:
        sender.close()
        receiver.close()
