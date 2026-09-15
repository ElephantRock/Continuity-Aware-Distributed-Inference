from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
import struct
import subprocess
import time

import pytest

from experiments.c8_protocol import (
    C8_HEADLINE_MEASUREMENT_INSPECTION,
    C8_MAX_FRAME_BYTES,
    C8_PROTOCOL_FINGERPRINT,
    C8ProcessRole,
    protocol_fingerprint,
)
from prototype import c8_harness, c8_worker
from prototype.c8_events import read_events
from prototype.c8_faults import DeliveryAction, DeliveryScheduler, QueueCapacityError
from prototype.c8_runtime import (
    C8SubstrateRuntime,
    DirtyCheckoutError,
    resolve_clean_checkout,
    wait_for_event,
)
from prototype.c8_transport import (
    CanonicalEncodingError,
    EnvelopeValidationError,
    FrameSizeError,
    TruncatedFrameError,
    canonical_json_bytes,
    decode_canonical_json,
    decode_frame_bytes,
    encode_frame,
    make_envelope,
)


def _envelope(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "message_id": "test:1",
        "message_kind": "WORK",
        "sender_role": C8ProcessRole.CONTROL_PLANE_AUTHORITY,
        "receiver_role": C8ProcessRole.WORKER,
        "subject_type": "SYNTHETIC_WORK",
        "subject_id": "work:1",
        "payload_schema": "test.payload.v1",
        "payload": {"operation": "SHA256", "input": "x"},
        "send_monotonic_ns": 0,
    }
    values.update(overrides)
    return make_envelope(**values)  # type: ignore[arg-type]


def _wait_for_count(path: Path, action: str, count: int, timeout_s: float = 8.0) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        matches = [event for event in read_events(path) if event.get("action") == action]
        if len(matches) >= count:
            return matches
        time.sleep(0.01)
    raise TimeoutError(f"did not observe {count} occurrences of {action}")


def _import_roots(module: object) -> set[str]:
    source = inspect.getsource(module)
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_c81_protocol_identity_remains_frozen_and_no_headline_measurement() -> None:
    assert C8_PROTOCOL_FINGERPRINT == (
        "616cc4daa4c167152875522767a8cc968da25c63d6acc099c265bc3554641cb6"
    )
    assert protocol_fingerprint() == C8_PROTOCOL_FINGERPRINT
    assert C8_HEADLINE_MEASUREMENT_INSPECTION == "NONE"


def test_transport_round_trip_is_exact_canonical_frame() -> None:
    envelope = _envelope()
    frame = encode_frame(envelope)
    assert decode_frame_bytes(frame) == envelope
    payload_length = struct.unpack(">I", frame[:4])[0]
    assert payload_length == len(frame) - 4
    assert frame[4:] == canonical_json_bytes(envelope)


def test_transport_rejects_noncanonical_truncated_oversized_and_extra_fields() -> None:
    envelope = _envelope()
    frame = encode_frame(envelope)
    with pytest.raises(TruncatedFrameError):
        decode_frame_bytes(frame[:-1])
    with pytest.raises(FrameSizeError):
        decode_frame_bytes(struct.pack(">I", C8_MAX_FRAME_BYTES + 1))

    pretty = json.dumps(envelope, sort_keys=True, indent=2).encode("utf-8")
    with pytest.raises(CanonicalEncodingError):
        decode_canonical_json(pretty)

    extra = dict(envelope)
    extra["hidden_authority"] = True
    with pytest.raises(EnvelopeValidationError, match="field mismatch"):
        encode_frame(extra)


def test_transport_rejects_nonfinite_payload_and_bad_role_or_kind() -> None:
    with pytest.raises(CanonicalEncodingError):
        _envelope(payload={"value": float("nan")})
    with pytest.raises(EnvelopeValidationError, match="unsupported message kind"):
        _envelope(message_kind="AUTHORIZE")
    with pytest.raises(EnvelopeValidationError, match="unsupported sender/receiver role"):
        _envelope(sender_role="ORCHESTRATOR")


def test_fault_scheduler_is_deterministic_for_all_frozen_actions() -> None:
    script = (
        DeliveryAction.DELIVER,
        DeliveryAction.DUPLICATE,
        DeliveryAction.DROP,
        DeliveryAction.DELAY,
        DeliveryAction.REORDER,
        DeliveryAction.REORDER,
        DeliveryAction.LATE_DELIVER,
    )
    scheduler = DeliveryScheduler(script, capacity=4)
    assert scheduler.submit(b"a") == (b"a",)
    assert scheduler.submit(b"b") == (b"b", b"b")
    assert scheduler.submit(b"c") == ()
    assert scheduler.dropped == 1
    assert scheduler.submit(b"d") == ()
    assert scheduler.submit(b"e") == ()
    assert scheduler.submit(b"f") == (b"f", b"e")
    assert scheduler.submit(b"g") == ()
    assert scheduler.flush_delayed() == (b"d",)
    assert scheduler.flush_late() == (b"g",)
    assert scheduler.pending_count == 0

    replay = DeliveryScheduler(script, capacity=4)
    outputs = [replay.submit(value) for value in (b"a", b"b", b"c", b"d", b"e", b"f", b"g")]
    assert outputs == [
        (b"a",),
        (b"b", b"b"),
        (),
        (),
        (),
        (b"f", b"e"),
        (),
    ]


def test_fault_scheduler_queue_capacity_failure_is_explicit() -> None:
    scheduler = DeliveryScheduler((DeliveryAction.DELAY, DeliveryAction.LATE_DELIVER), capacity=1)
    assert scheduler.submit(b"held") == ()
    with pytest.raises(QueueCapacityError, match="queue is full"):
        scheduler.submit(b"overflow")


def test_worker_and_harness_source_do_not_import_continuity_authority() -> None:
    assert "continuity" not in _import_roots(c8_worker)
    assert "continuity" not in _import_roots(c8_harness)


def test_real_topology_uses_distinct_self_reported_pids_and_tcp(tmp_path: Path) -> None:
    runtime = C8SubstrateRuntime(tmp_path)
    try:
        runtime.start(worker_id="worker-a")
        completion = wait_for_event(runtime.authority_log, "PHYSICAL_COMPLETION_OBSERVED")
        assert completion["result"] == "CURRENT_WORKER"
        assert completion["details"]["timestamp_used_for_authority"] is False

        assert runtime.worker is not None
        runtime.worker.join(5.0)
        assert runtime.worker.exitcode == 0
        wait_for_event(runtime.authority_log, "WORKER_SHUTDOWN_ACK")

        topology = runtime.reported_topology()
        assert topology.all_distinct is True
        assert runtime.authority is not None and runtime.harness is not None
        assert topology.authority_pid == runtime.authority.pid
        assert topology.harness_pid == runtime.harness.pid
        assert topology.worker_pids[-1] == runtime.worker.pid

        harness_events = read_events(runtime.harness_log)
        directions = {
            event["result"]
            for event in harness_events
            if event.get("action") == "FRAME_FORWARDED"
        }
        assert "WORKER_TO_AUTHORITY" in directions
        assert "AUTHORITY_TO_WORKER" in directions

        registered = wait_for_event(runtime.authority_log, "WORKER_REGISTERED")
        assert registered["details"]["worker_pid"] == runtime.worker.pid
    finally:
        runtime.stop()


def test_real_worker_termination_and_restart_changes_pid(tmp_path: Path) -> None:
    runtime = C8SubstrateRuntime(tmp_path, shutdown_worker_after_completion=False)
    try:
        runtime.start(worker_id="worker-old", work_delay_s=5.0)
        wait_for_event(runtime.authority_log, "WORKER_REGISTERED")
        assert runtime.worker is not None
        original_pid = runtime.worker.pid
        old_pid, new_worker = runtime.restart_worker(worker_id="worker-new", work_delay_s=0.0)
        assert old_pid == original_pid
        assert new_worker.pid is not None and new_worker.pid != old_pid
        wait_for_event(runtime.authority_log, "PHYSICAL_COMPLETION_OBSERVED")
        registered = _wait_for_count(runtime.authority_log, "WORKER_REGISTERED", 2)
        assert registered[-1]["details"]["worker_pid"] == new_worker.pid
        topology = runtime.reported_topology()
        assert topology.worker_pids == (old_pid, new_worker.pid)
        assert topology.all_distinct is True
    finally:
        runtime.stop()


def test_actual_harness_duplicate_delivery_preserves_transport_identity(tmp_path: Path) -> None:
    runtime = C8SubstrateRuntime(
        tmp_path,
        upstream_faults={"COMPLETE": ("DUPLICATE",)},
        shutdown_worker_after_completion=False,
    )
    try:
        runtime.start(worker_id="worker-dup")
        completions = _wait_for_count(runtime.authority_log, "PHYSICAL_COMPLETION_OBSERVED", 2)
        assert completions[0]["message_id"] == completions[1]["message_id"]
        assert completions[0]["subject_id"] == completions[1]["subject_id"]
        assert all(event["result"] == "CURRENT_WORKER" for event in completions[:2])
    finally:
        runtime.stop()


def test_clean_checkout_provenance_rejects_tracked_and_untracked_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "c8@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "C8 Test"], cwd=repo, check=True)
    tracked = repo / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

    head = resolve_clean_checkout(repo)
    assert len(head) == 40

    untracked = repo / "untracked.py"
    untracked.write_text("print('unexpected')\n", encoding="utf-8")
    with pytest.raises(DirtyCheckoutError, match="not clean"):
        resolve_clean_checkout(repo)
    untracked.unlink()

    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(DirtyCheckoutError, match="not clean"):
        resolve_clean_checkout(repo)
