from __future__ import annotations

from experiments.c83_replay_protocol import C83A_TRACE_SPECS, C83Layer
from experiments.c83_replay_results import (
    _replay_failure_row,
    _row,
    _scientific_row,
    semantic_state_fingerprint,
)
from experiments.c83_replay_semantics import ReplayObservation


EXECUTION_SHA = "1" * 40


def test_row_reorders_projection_by_frozen_contract_before_hashing() -> None:
    spec = C83A_TRACE_SPECS[0]
    observation = ReplayObservation(
        "current-attempt-finalize",
        "CURRENT_ATTEMPT_FINALIZED",
        {
            "request.authoritative_output_id": "o2",
            "request.committed_attempt_id": "a2",
        },
    )
    row = _row(
        spec=spec,
        execution_git_commit=EXECUTION_SHA,
        layer=C83Layer.C1,
        observation=observation,
    )
    assert row["semantic_state_fingerprint"] == semantic_state_fingerprint(
        {
            "request.committed_attempt_id": "a2",
            "request.authoritative_output_id": "o2",
        }
    )


def test_replay_failure_row_preserves_denominator_without_semantic_violation() -> None:
    spec = C83A_TRACE_SPECS[0]
    checkpoint = spec.checkpoints[1]
    topology = {
        "authority_pid": 101,
        "fault_harness_pid": 102,
        "worker_pids": [103, 104],
        "authority_port": 31001,
        "worker_port": 31002,
        "transport_id": "LOOPBACK_TCP_LENGTH_PREFIXED_CANONICAL_JSON_V1",
        "real_process_boundary": True,
    }
    row = _replay_failure_row(
        spec=spec,
        checkpoint=checkpoint,
        execution_git_commit=EXECUTION_SHA,
        topology=topology,
    )
    assert row["raw_outcome"] == "REPLAY_EXECUTION_FAILURE"
    assert row["normalized_outcome"] == "FAIL"
    assert row["opportunities"] == 1
    assert row["violations"] == 0
    assert row["explicit_non_success"] is True
    assert row["replay_execution_failure"] is True


def test_scientific_row_normalizes_volatile_real_process_identifiers() -> None:
    base = {
        "trace_id": "C8-X2-DUPLICATE-COMPLETION",
        "trial_id": "c83b:C8-X2-DUPLICATE-COMPLETION:trial-0",
        "layer": "C8",
        "checkpoint_id": "first-finalization",
        "raw_outcome": "FIRST_FINALIZATION",
        "normalized_outcome": "COMMITTED",
        "opportunities": 0,
        "violations": 0,
        "explicit_non_success": False,
        "replay_execution_failure": False,
        "semantic_state_fingerprint": "a" * 64,
        "fault_script_fingerprint": "b" * 64,
    }
    left = {
        **base,
        "topology_provenance": {
            "authority_pid": 101,
            "fault_harness_pid": 102,
            "worker_pids": [103],
            "authority_port": 31001,
            "worker_port": 31002,
            "transport_id": "LOOPBACK_TCP_LENGTH_PREFIXED_CANONICAL_JSON_V1",
            "real_process_boundary": True,
        },
    }
    right = {
        **base,
        "topology_provenance": {
            "authority_pid": 501,
            "fault_harness_pid": 502,
            "worker_pids": [503],
            "authority_port": 42001,
            "worker_port": 42002,
            "transport_id": "LOOPBACK_TCP_LENGTH_PREFIXED_CANONICAL_JSON_V1",
            "real_process_boundary": True,
        },
    }
    assert _scientific_row(left) == _scientific_row(right)


def test_scientific_row_detects_topology_shape_change() -> None:
    base = {
        "trace_id": "C8-X1-LATE-SUPERSEDED-ATTEMPT",
        "trial_id": "c83b:C8-X1-LATE-SUPERSEDED-ATTEMPT:trial-0",
        "layer": "C8",
        "checkpoint_id": "current-attempt-finalize",
        "raw_outcome": "CURRENT_ATTEMPT_COMMITTED",
        "normalized_outcome": "COMMITTED",
        "opportunities": 0,
        "violations": 0,
        "explicit_non_success": False,
        "replay_execution_failure": False,
        "semantic_state_fingerprint": "c" * 64,
        "fault_script_fingerprint": "d" * 64,
    }
    two_generations = {
        **base,
        "topology_provenance": {
            "authority_pid": 1,
            "fault_harness_pid": 2,
            "worker_pids": [3, 4],
            "authority_port": 30001,
            "worker_port": 30002,
            "transport_id": "LOOPBACK_TCP_LENGTH_PREFIXED_CANONICAL_JSON_V1",
            "real_process_boundary": True,
        },
    }
    one_generation = {
        **base,
        "topology_provenance": {
            "authority_pid": 1,
            "fault_harness_pid": 2,
            "worker_pids": [3],
            "authority_port": 30001,
            "worker_port": 30002,
            "transport_id": "LOOPBACK_TCP_LENGTH_PREFIXED_CANONICAL_JSON_V1",
            "real_process_boundary": True,
        },
    }
    assert _scientific_row(two_generations) != _scientific_row(one_generation)
