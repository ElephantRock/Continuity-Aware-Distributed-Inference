from __future__ import annotations

import math

import pytest

from experiments.c8_protocol import (
    C8_BASE_COMMIT,
    C8_HEADLINE_MEASUREMENT_INSPECTION,
    C8_IMPORTED_INFERENCE_EVIDENCE_CLASS,
    C8_INVALID_RESULT_CONDITIONS,
    C8_MAX_FRAME_BYTES,
    C8_MEASURED_EVIDENCE_CLASS,
    C8_MEASUREMENT_CLOCK,
    C8_MEASUREMENT_RECORD_SCHEMA,
    C8_MESSAGE_ENVELOPE_FIELDS,
    C8_METRICS,
    C8_OVERHEAD_COMPONENTS,
    C8_PROTOCOL_FINGERPRINT,
    C8_PROTOCOL_SCHEMA,
    C8_RESULT_SCHEMA,
    C8_RUN_MANIFEST_FIELDS,
    C8_RUN_MANIFEST_SCHEMA,
    C8_SCALE_AXES,
    C8_TRACE_SPECS,
    C8_TRANSPORT_ADDRESS_FAMILY,
    C8_TRANSPORT_FRAMING,
    C8_TRANSPORT_ID,
    C8_WARMUP_OPERATIONS,
    C8Measurement,
    C8ProcessRole,
    C8RunManifest,
    C8TimingMode,
    protocol_fingerprint,
    protocol_payload,
    validate_measurement_set,
    validate_protocol_identity,
)


VALID_GIT_SHA = "1" * 40
OTHER_GIT_SHA = "2" * 40


def _manifest(**overrides: object) -> C8RunManifest:
    values: dict[str, object] = {
        "git_commit": VALID_GIT_SHA,
        "running_git_commit": VALID_GIT_SHA,
        "experiment_id": "C8.4-DECISION-LATENCY",
        "scenario_id": "baseline",
        "authority_pid": 1001,
        "worker_pids": (1002,),
        "fault_harness_pid": 1003,
        "os_name": "Linux",
        "os_version": "test",
        "python_version": "3.12.14",
        "cpu_model": "test-cpu",
        "worker_count": 1,
        "active_sessions": 1,
        "continuation_count": 1,
        "state_count": 0,
        "replica_count": 1,
        "concurrency": 1,
        "evidence_count": 1,
        "fault_configuration": {},
        "seed": 0,
        "host_load": {"load1": 0.0},
        "software_versions": {"python": "3.12.14"},
        "start_time_utc": "2026-09-16T00:00:00Z",
    }
    values.update(overrides)
    return C8RunManifest(**values)  # type: ignore[arg-type]


def test_frozen_protocol_identity_is_literal_and_stable() -> None:
    assert C8_PROTOCOL_SCHEMA == "cadi.c8.1.real-cpu-prototype-protocol.v1"
    assert C8_BASE_COMMIT == "0680d09efcf43a9b01552f9804906d66c3fc56f2"
    assert C8_HEADLINE_MEASUREMENT_INSPECTION == "NONE"
    assert C8_PROTOCOL_FINGERPRINT == (
        "616cc4daa4c167152875522767a8cc968da25c63d6acc099c265bc3554641cb6"
    )
    assert protocol_fingerprint() == C8_PROTOCOL_FINGERPRINT
    validate_protocol_identity()


def test_machine_schema_identity_is_fingerprinted() -> None:
    payload = protocol_payload()
    assert C8_RUN_MANIFEST_SCHEMA == "cadi.c8.1.run-manifest.v1"
    assert C8_MEASUREMENT_RECORD_SCHEMA == "cadi.c8.1.measurement-record.v1"
    assert C8_RESULT_SCHEMA == "cadi.c8.1.result.v1"
    assert payload["run_manifest_schema"] == C8_RUN_MANIFEST_SCHEMA
    assert payload["measurement_record_schema"] == C8_MEASUREMENT_RECORD_SCHEMA
    assert payload["result_schema"] == C8_RESULT_SCHEMA
    assert payload["run_manifest_fields"] == list(C8_RUN_MANIFEST_FIELDS)
    assert "running_git_commit" in C8_RUN_MANIFEST_FIELDS
    assert {"authority_pid", "worker_pids", "fault_harness_pid"}.issubset(
        C8_RUN_MANIFEST_FIELDS
    )


def test_reference_transport_is_real_loopback_and_strict_serialization() -> None:
    payload = protocol_payload()
    assert C8_TRANSPORT_ID == "LOOPBACK_TCP_LENGTH_PREFIXED_CANONICAL_JSON_V1"
    assert C8_TRANSPORT_ADDRESS_FAMILY == "AF_INET_LOOPBACK"
    assert C8_TRANSPORT_FRAMING == "UINT32_BE_LENGTH_PLUS_CANONICAL_JSON_UTF8"
    assert C8_MAX_FRAME_BYTES == 1_048_576
    assert payload["transport"]["serialization_required"] is True
    assert payload["transport"]["fault_harness_external_to_authority"] is True
    assert payload["process_topology"] == {
        "authority_process_count": 1,
        "minimum_worker_process_count": 1,
        "fault_harness_process_count": 1,
        "all_declared_role_pids_must_be_distinct": True,
    }


def test_clock_is_measurement_only_and_never_authority() -> None:
    payload = protocol_payload()
    assert C8_MEASUREMENT_CLOCK == "time.monotonic_ns"
    assert payload["clock"]["synchronized_wall_clock_required_for_safety"] is False
    assert payload["clock"]["timestamps_authorize_semantics"] is False


def test_single_authority_roles_are_frozen() -> None:
    payload = protocol_payload()
    assert [role.value for role in C8ProcessRole] == [
        "CONTROL_PLANE_AUTHORITY",
        "WORKER",
        "FAULT_TRANSPORT_HARNESS",
    ]
    assert payload["authority"] == {
        "authoritative_role": "CONTROL_PLANE_AUTHORITY",
        "worker_may_authoritatively_mutate": False,
        "single_logical_authority": True,
        "consensus_in_scope": False,
        "byzantine_in_scope": False,
    }


def test_cross_layer_trace_contract_has_exact_required_families() -> None:
    assert tuple(trace.trace_id for trace in C8_TRACE_SPECS) == (
        "C8-X1-LATE-SUPERSEDED-ATTEMPT",
        "C8-X2-DUPLICATE-COMPLETION",
        "C8-X3-WRONG-SIBLING-STATE",
        "C8-X4-STALE-BINDING",
        "C8-X5-AMBIGUOUS-OWNERSHIP",
        "C8-X6-STATE-EVICTION-TOOL-WAIT",
        "C8-X7-PARTIAL-MIGRATION",
    )
    assert len({trace.trace_id for trace in C8_TRACE_SPECS}) == 7
    assert all(trace.c1_reference and trace.c2_reference for trace in C8_TRACE_SPECS)
    assert all(trace.c8_injection and trace.expected_semantic_outcome for trace in C8_TRACE_SPECS)
    assert C8_TRACE_SPECS[5].prior_e1_reference.startswith("NONE_C4_DIRECT")


def test_evidence_classes_cannot_be_silently_upgraded() -> None:
    payload = protocol_payload()
    assert C8_MEASURED_EVIDENCE_CLASS == "EV1_MEASURED_CPU"
    assert C8_IMPORTED_INFERENCE_EVIDENCE_CLASS == "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"
    assert payload["evidence"] == {
        "measured_control_plane": C8_MEASURED_EVIDENCE_CLASS,
        "imported_inference_timing": C8_IMPORTED_INFERENCE_EVIDENCE_CLASS,
    }


def test_gate_g3_has_no_post_hoc_universal_percentage_threshold() -> None:
    gate = protocol_payload()["gate_g3"]
    assert gate["universal_percentage_threshold"] is None
    assert gate["adjudication_after_measurement_only"] is True
    assert "dominant serving bottleneck" in gate["question"]


def test_metrics_overhead_and_scale_axes_are_complete_and_bounded() -> None:
    assert tuple(metric.metric_id for metric in C8_METRICS) == (
        "decision_latency_ns",
        "reconciliation_latency_ns",
        "control_action_latency_ns",
        "process_cpu_time_ns",
        "peak_rss_bytes",
        "event_throughput_events_per_s",
        "serialization_bytes",
        "serialization_latency_ns",
        "transport_latency_ns",
        "failure_recovery_latency_ns",
    )
    assert C8_OVERHEAD_COMPONENTS == (
        "identity_creation",
        "graph_lookup",
        "ancestry_compatibility_check",
        "evidence_evaluation",
        "binding_validation",
        "reconciliation",
        "metadata_serialization",
        "ipc_transport",
    )
    assert C8_SCALE_AXES["worker_count"] == (1, 2, 4, 8)
    assert C8_SCALE_AXES["concurrency"] == (1, 4, 16, 64)
    assert all(values == tuple(sorted(set(values))) for values in C8_SCALE_AXES.values())


def test_message_envelope_carries_transport_identity_not_new_semantic_authority() -> None:
    assert C8_MESSAGE_ENVELOPE_FIELDS == (
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


def test_valid_manifest_binds_running_checkout_and_distinct_processes() -> None:
    manifest = _manifest()
    assert manifest.process_count == 3
    assert manifest.invalid_conditions() == ()
    assert manifest.rankable_for_gate_g3 is True


@pytest.mark.parametrize(
    ("overrides", "expected_condition"),
    [
        ({"running_git_commit": OTHER_GIT_SHA}, "RUNNING_CHECKOUT_MISMATCH"),
        ({"worker_pids": (1001,)}, "NO_REAL_PROCESS_BOUNDARY"),
        ({"fault_harness_pid": 1002}, "NO_REAL_PROCESS_BOUNDARY"),
        ({"serialization_on_measured_path": False}, "SERIALIZATION_BYPASSED"),
        (
            {
                "authoritative_mutation_roles": (
                    C8ProcessRole.CONTROL_PLANE_AUTHORITY,
                    C8ProcessRole.WORKER,
                )
            },
            "WORKER_AUTHORITATIVE_MUTATION",
        ),
        ({"correctness_guards_enabled": False}, "REQUIRED_CORRECTNESS_GUARD_DISABLED"),
        ({"clock_used_for_semantic_authority": True}, "CLOCK_USED_FOR_SEMANTIC_AUTHORITY"),
        ({"cross_layer_preconditions_match": False}, "CROSS_LAYER_PRECONDITION_MISMATCH"),
        (
            {"timing_mode": C8TimingMode.PRODUCTION_GUARDS_PLUS_DEBUG},
            "UNLABELED_DEBUG_TIMING_PERTURBATION",
        ),
        ({"measured_modeled_timing_conflated": True}, "MEASURED_AND_MODELED_TIMING_CONFLATED"),
        ({"worker_count": 2}, "INCOMPLETE_RUN_PROVENANCE"),
        ({"warmup_operations": C8_WARMUP_OPERATIONS - 1}, "INCOMPLETE_RUN_PROVENANCE"),
    ],
)
def test_manifest_invalid_conditions_fail_rankability(
    overrides: dict[str, object], expected_condition: str
) -> None:
    manifest = _manifest(**overrides)
    assert expected_condition in manifest.invalid_conditions()
    assert expected_condition in C8_INVALID_RESULT_CONDITIONS
    assert manifest.rankable_for_gate_g3 is False


def test_manifest_rejects_malformed_or_wrong_provenance() -> None:
    with pytest.raises(ValueError, match="40-character git SHA"):
        _manifest(git_commit="deadbeef")
    with pytest.raises(ValueError, match="worker_pids"):
        _manifest(worker_pids=())
    with pytest.raises(ValueError, match="positive integer"):
        _manifest(authority_pid=0)
    with pytest.raises(ValueError, match="transport escaped"):
        _manifest(transport_id="IN_MEMORY_QUEUE")
    with pytest.raises(ValueError, match="EV1 measured CPU"):
        _manifest(evidence_class="SIMULATED")
    with pytest.raises(ValueError, match="P-SRC2"):
        _manifest(imported_inference_evidence_class="MEASURED_GPU")
    with pytest.raises(ValueError, match="host_load"):
        _manifest(host_load={})


def test_measurements_fail_closed_on_nonfinite_negative_unknown_or_duplicate() -> None:
    good = C8Measurement("decision_latency_ns", 10.0)
    validate_measurement_set((good,))
    with pytest.raises(ValueError, match="frozen C8.1 metric"):
        C8Measurement("invented_metric", 1.0)
    for value in (math.nan, math.inf, -1.0):
        with pytest.raises(ValueError, match="finite and non-negative"):
            C8Measurement("decision_latency_ns", value)
    with pytest.raises(ValueError, match="duplicate metric IDs"):
        validate_measurement_set((good, good))
