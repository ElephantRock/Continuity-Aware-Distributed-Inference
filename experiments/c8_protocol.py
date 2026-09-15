from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping


C8_PROTOCOL_SCHEMA = "cadi.c8.1.real-cpu-prototype-protocol.v1"
C8_BASE_COMMIT = "0680d09efcf43a9b01552f9804906d66c3fc56f2"
C8_HEADLINE_MEASUREMENT_INSPECTION = "NONE"
C8_MEASURED_EVIDENCE_CLASS = "EV1_MEASURED_CPU"
C8_IMPORTED_INFERENCE_EVIDENCE_CLASS = "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"
C8_PROTOCOL_FINGERPRINT = "e29f069a9ff42ca993c01db22a3f96441ea5f9a6c6d6693041c68af43597ff34"

C8_TRANSPORT_ID = "LOOPBACK_TCP_LENGTH_PREFIXED_CANONICAL_JSON_V1"
C8_TRANSPORT_ADDRESS_FAMILY = "AF_INET_LOOPBACK"
C8_TRANSPORT_FRAMING = "UINT32_BE_LENGTH_PLUS_CANONICAL_JSON_UTF8"
C8_MAX_FRAME_BYTES = 1_048_576
C8_MEASUREMENT_CLOCK = "time.monotonic_ns"

C8_WARMUP_OPERATIONS = 200
C8_SAMPLES_PER_REPETITION = 1_000
C8_REPETITIONS = 7
C8_BOOTSTRAP_RESAMPLES = 10_000
C8_BOOTSTRAP_SEED = 8_012_026


class C8ProcessRole(str, Enum):
    CONTROL_PLANE_AUTHORITY = "CONTROL_PLANE_AUTHORITY"
    WORKER = "WORKER"
    FAULT_TRANSPORT_HARNESS = "FAULT_TRANSPORT_HARNESS"


class C8TimingMode(str, Enum):
    PRODUCTION_GUARDS_ONLY = "PRODUCTION_GUARDS_ONLY"
    PRODUCTION_GUARDS_PLUS_DEBUG = "PRODUCTION_GUARDS_PLUS_DEBUG"


@dataclass(frozen=True, slots=True)
class C8TraceSpec:
    trace_id: str
    c1_reference: str
    c2_reference: str
    prior_e1_reference: str
    c8_injection: str
    expected_semantic_outcome: str
    allowed_non_success: tuple[str, ...]
    forbidden_metric: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.trace_id, "trace_id"),
            (self.c1_reference, "c1_reference"),
            (self.c2_reference, "c2_reference"),
            (self.prior_e1_reference, "prior_e1_reference"),
            (self.c8_injection, "c8_injection"),
            (self.expected_semantic_outcome, "expected_semantic_outcome"),
            (self.forbidden_metric, "forbidden_metric"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.allowed_non_success, tuple) or not self.allowed_non_success:
            raise ValueError("allowed_non_success must be a non-empty tuple")
        if not all(isinstance(item, str) and item for item in self.allowed_non_success):
            raise ValueError("allowed_non_success must contain non-empty strings")
        if len(self.allowed_non_success) != len(set(self.allowed_non_success)):
            raise ValueError("allowed_non_success must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "c1_reference": self.c1_reference,
            "c2_reference": self.c2_reference,
            "prior_e1_reference": self.prior_e1_reference,
            "c8_injection": self.c8_injection,
            "expected_semantic_outcome": self.expected_semantic_outcome,
            "allowed_non_success": list(self.allowed_non_success),
            "forbidden_metric": self.forbidden_metric,
        }


@dataclass(frozen=True, slots=True)
class C8MetricSpec:
    metric_id: str
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, str) or not self.metric_id:
            raise ValueError("metric_id must be non-empty")
        if not isinstance(self.unit, str) or not self.unit:
            raise ValueError("unit must be non-empty")

    def to_list(self) -> list[str]:
        return [self.metric_id, self.unit]


C8_TRACE_SPECS = (
    C8TraceSpec(
        "C8-X1-LATE-SUPERSEDED-ATTEMPT",
        "tests/counterexamples/test_failure_model_traces.py + continuity/replay.py",
        "simulator/faults.py + simulator/fault_campaign.py",
        "experiments/attempt_fencing_e1.py",
        "delay old worker completion until a newer Attempt is current",
        "superseded Attempt cannot finalize the LogicalRequest",
        ("WAIT", "RETRY", "FAIL"),
        "Stale Attempt Acceptance Rate",
    ),
    C8TraceSpec(
        "C8-X2-DUPLICATE-COMPLETION",
        "tests/counterexamples/test_failure_model_traces.py + continuity/replay.py",
        "simulator/faults.py + simulator/fault_campaign.py",
        "experiments/idempotence_ordering_e1.py",
        "duplicate a real worker completion frame",
        "duplicate completion is idempotent and cannot finalize twice",
        ("WAIT", "RETRY", "FAIL"),
        "Duplicate Finalization Rate",
    ),
    C8TraceSpec(
        "C8-X3-WRONG-SIBLING-STATE",
        "tests/counterexamples/test_failure_model_traces.py + continuity/replay.py",
        "simulator/faults.py + simulator/semantic_adapter.py",
        "experiments/state_lineage_e1.py",
        "present attractive physical State from an incompatible sibling Continuation",
        "incompatible sibling State is rejected for consumption",
        ("RECOMPUTE", "REJECT", "FAIL"),
        "Wrong-Branch Reuse Rate",
    ),
    C8TraceSpec(
        "C8-X4-STALE-BINDING",
        "tests/counterexamples/test_failure_model_traces.py + continuity/replay.py",
        "simulator/faults.py + simulator/semantic_adapter.py",
        "experiments/binding_safety_e1.py",
        "delay an older Binding-epoch observation until after replacement",
        "stale Binding epoch cannot regain authoritative ownership",
        ("WAIT", "RETRY", "RECOMPUTE", "FAIL"),
        "Silent Binding Divergence Rate",
    ),
    C8TraceSpec(
        "C8-X5-AMBIGUOUS-OWNERSHIP",
        "tests/counterexamples/test_failure_model_traces.py + continuity/replay.py",
        "simulator/faults.py + simulator/fault_oracle.py",
        "experiments/evidence_safety_e1.py",
        "deliver conflicting insufficient ownership observations",
        "correctness-sensitive ownership commit fails closed under ambiguity",
        ("WAIT", "RETRY", "RECOMPUTE", "FAIL", "AMBIGUOUS"),
        "Ambiguous Commit Rate",
    ),
    C8TraceSpec(
        "C8-X6-STATE-EVICTION-TOOL-WAIT",
        "continuity/replay.py + tests/invariants/test_state.py",
        "simulator/faults.py + simulator/fault_campaign.py",
        "NONE_C4_DIRECT; C7 retention evidence remains separate",
        "evict the only physical State replica while the Continuation is suspended",
        "resume must recompute/fail rather than consume absent or incompatible State",
        ("RECOMPUTE", "REJECT", "FAIL"),
        "Wrong-State Consumption Rate",
    ),
    C8TraceSpec(
        "C8-X7-PARTIAL-MIGRATION",
        "continuity/replay.py + tests/invariants/test_binding_evidence.py",
        "simulator/faults.py + simulator/fault_oracle.py",
        "experiments/binding_safety_e1.py + experiments/evidence_safety_e1.py",
        "terminate/delay destination after partial State materialization before authoritative migration commit",
        "partial materialization cannot authorize ownership transfer without sufficient evidence",
        ("WAIT", "RETRY", "RECOMPUTE", "FAIL", "AMBIGUOUS"),
        "Ambiguous Commit Rate",
    ),
)

C8_METRICS = (
    C8MetricSpec("decision_latency_ns", "ns"),
    C8MetricSpec("reconciliation_latency_ns", "ns"),
    C8MetricSpec("control_action_latency_ns", "ns"),
    C8MetricSpec("process_cpu_time_ns", "ns"),
    C8MetricSpec("peak_rss_bytes", "bytes"),
    C8MetricSpec("event_throughput_events_per_s", "events/s"),
    C8MetricSpec("serialization_bytes", "bytes"),
    C8MetricSpec("serialization_latency_ns", "ns"),
    C8MetricSpec("transport_latency_ns", "ns"),
    C8MetricSpec("failure_recovery_latency_ns", "ns"),
)

C8_OVERHEAD_COMPONENTS = (
    "identity_creation",
    "graph_lookup",
    "ancestry_compatibility_check",
    "evidence_evaluation",
    "binding_validation",
    "reconciliation",
    "metadata_serialization",
    "ipc_transport",
)

C8_SCALE_AXES: Mapping[str, tuple[int, ...]] = {
    "worker_count": (1, 2, 4, 8),
    "active_sessions": (1, 8, 32, 128),
    "continuation_count": (1, 16, 64, 256),
    "state_count": (0, 32, 128, 512),
    "replica_count": (1, 2, 4),
    "concurrency": (1, 4, 16, 64),
    "evidence_count": (1, 4, 16, 64),
}

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

C8_INVALID_RESULT_CONDITIONS = (
    "RUNNING_CHECKOUT_MISMATCH",
    "NO_REAL_PROCESS_BOUNDARY",
    "SERIALIZATION_BYPASSED",
    "WORKER_AUTHORITATIVE_MUTATION",
    "REQUIRED_CORRECTNESS_GUARD_DISABLED",
    "CLOCK_USED_FOR_SEMANTIC_AUTHORITY",
    "CROSS_LAYER_PRECONDITION_MISMATCH",
    "UNLABELED_DEBUG_TIMING_PERTURBATION",
    "MEASURED_AND_MODELED_TIMING_CONFLATED",
    "NONFINITE_OR_INCOMPLETE_MEASUREMENT",
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _git_sha(value: str, name: str = "git_commit") -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError(f"{name} must be a 40-character git SHA")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be hexadecimal") from exc
    return value


def protocol_payload() -> dict[str, Any]:
    return {
        "schema": C8_PROTOCOL_SCHEMA,
        "base_commit": C8_BASE_COMMIT,
        "headline_measurement_inspection": C8_HEADLINE_MEASUREMENT_INSPECTION,
        "evidence": {
            "measured_control_plane": C8_MEASURED_EVIDENCE_CLASS,
            "imported_inference_timing": C8_IMPORTED_INFERENCE_EVIDENCE_CLASS,
        },
        "authority": {
            "authoritative_role": C8ProcessRole.CONTROL_PLANE_AUTHORITY.value,
            "worker_may_authoritatively_mutate": False,
            "single_logical_authority": True,
            "consensus_in_scope": False,
            "byzantine_in_scope": False,
        },
        "roles": [role.value for role in C8ProcessRole],
        "transport": {
            "id": C8_TRANSPORT_ID,
            "address_family": C8_TRANSPORT_ADDRESS_FAMILY,
            "framing": C8_TRANSPORT_FRAMING,
            "max_frame_bytes": C8_MAX_FRAME_BYTES,
            "serialization_required": True,
            "fault_harness_external_to_authority": True,
        },
        "clock": {
            "measurement_clock": C8_MEASUREMENT_CLOCK,
            "synchronized_wall_clock_required_for_safety": False,
            "timestamps_authorize_semantics": False,
        },
        "message_envelope_fields": list(C8_MESSAGE_ENVELOPE_FIELDS),
        "trace_families": [trace.to_dict() for trace in C8_TRACE_SPECS],
        "metrics": [metric.to_list() for metric in C8_METRICS],
        "overhead_components": list(C8_OVERHEAD_COMPONENTS),
        "scale_axes": {name: list(values) for name, values in C8_SCALE_AXES.items()},
        "measurement_procedure": {
            "warmup_operations": C8_WARMUP_OPERATIONS,
            "samples_per_repetition": C8_SAMPLES_PER_REPETITION,
            "repetitions": C8_REPETITIONS,
            "bootstrap_resamples": C8_BOOTSTRAP_RESAMPLES,
            "bootstrap_seed": C8_BOOTSTRAP_SEED,
            "interval": "paired repetition-level bootstrap 95% percentile interval",
            "latency_summary": ["median", "p95", "p99"],
            "non_finite_handling": "INVALID_RESULT",
            "outlier_handling": "NO_SILENT_TRIMMING",
            "debug_timing_policy": "PRODUCTION_GUARDS_PLUS_DEBUG_IS_DIAGNOSTIC_ONLY",
            "host_load_recording_required": True,
        },
        "invalid_result_conditions": list(C8_INVALID_RESULT_CONDITIONS),
        "gate_g3": {
            "universal_percentage_threshold": None,
            "question": "does Continuity become a dominant serving bottleneck over a declared intended workload region?",
            "adjudication_after_measurement_only": True,
        },
    }


def protocol_fingerprint() -> str:
    return _sha256(protocol_payload())


def validate_protocol_identity() -> None:
    if protocol_fingerprint() != C8_PROTOCOL_FINGERPRINT:
        raise RuntimeError("C8.1 protocol payload diverged from frozen fingerprint")


@dataclass(frozen=True, slots=True)
class C8RunManifest:
    git_commit: str
    process_count: int
    process_roles: tuple[C8ProcessRole, ...]
    transport_id: str = C8_TRANSPORT_ID
    serialization_on_measured_path: bool = True
    authoritative_mutation_roles: tuple[C8ProcessRole, ...] = (
        C8ProcessRole.CONTROL_PLANE_AUTHORITY,
    )
    correctness_guards_enabled: bool = True
    clock_used_for_semantic_authority: bool = False
    timing_mode: C8TimingMode = C8TimingMode.PRODUCTION_GUARDS_ONLY
    evidence_class: str = C8_MEASURED_EVIDENCE_CLASS
    imported_inference_evidence_class: str = C8_IMPORTED_INFERENCE_EVIDENCE_CLASS
    measured_modeled_timing_conflated: bool = False
    cross_layer_preconditions_match: bool = True
    host_load_recorded: bool = True

    def __post_init__(self) -> None:
        _git_sha(self.git_commit)
        if not isinstance(self.process_count, int) or isinstance(self.process_count, bool):
            raise TypeError("process_count must be an integer")
        if self.process_count < 2:
            raise ValueError("C8 requires a real multi-process boundary")
        if not isinstance(self.process_roles, tuple) or not self.process_roles:
            raise ValueError("process_roles must be a non-empty tuple")
        if not all(isinstance(role, C8ProcessRole) for role in self.process_roles):
            raise TypeError("process_roles must contain C8ProcessRole values")
        required = {C8ProcessRole.CONTROL_PLANE_AUTHORITY, C8ProcessRole.WORKER}
        if not required.issubset(set(self.process_roles)):
            raise ValueError("C8 requires control-plane authority and worker roles")
        if self.transport_id != C8_TRANSPORT_ID:
            raise ValueError("transport escaped frozen C8.1 reference transport")
        if self.authoritative_mutation_roles != (C8ProcessRole.CONTROL_PLANE_AUTHORITY,):
            raise ValueError("only the control-plane authority may mutate semantic authority")
        if not isinstance(self.timing_mode, C8TimingMode):
            raise TypeError("timing_mode must be C8TimingMode")
        if self.evidence_class != C8_MEASURED_EVIDENCE_CLASS:
            raise ValueError("C8 control-plane measurements must be EV1 measured CPU evidence")
        if self.imported_inference_evidence_class != C8_IMPORTED_INFERENCE_EVIDENCE_CLASS:
            raise ValueError("imported inference timing must retain its P-SRC2 evidence label")

    def invalid_conditions(self) -> tuple[str, ...]:
        invalid: list[str] = []
        if not self.serialization_on_measured_path:
            invalid.append("SERIALIZATION_BYPASSED")
        if not self.correctness_guards_enabled:
            invalid.append("REQUIRED_CORRECTNESS_GUARD_DISABLED")
        if self.clock_used_for_semantic_authority:
            invalid.append("CLOCK_USED_FOR_SEMANTIC_AUTHORITY")
        if not self.cross_layer_preconditions_match:
            invalid.append("CROSS_LAYER_PRECONDITION_MISMATCH")
        if self.timing_mode is C8TimingMode.PRODUCTION_GUARDS_PLUS_DEBUG:
            invalid.append("UNLABELED_DEBUG_TIMING_PERTURBATION")
        if self.measured_modeled_timing_conflated:
            invalid.append("MEASURED_AND_MODELED_TIMING_CONFLATED")
        if not self.host_load_recorded:
            invalid.append("NONFINITE_OR_INCOMPLETE_MEASUREMENT")
        return tuple(invalid)

    @property
    def rankable_for_gate_g3(self) -> bool:
        return not self.invalid_conditions()


@dataclass(frozen=True, slots=True)
class C8Measurement:
    metric_id: str
    value: float

    def __post_init__(self) -> None:
        known = {metric.metric_id for metric in C8_METRICS}
        if self.metric_id not in known:
            raise ValueError("metric_id is not a frozen C8.1 metric")
        if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise TypeError("measurement value must be numeric")
        value = float(self.value)
        if not math.isfinite(value) or value < 0.0:
            raise ValueError("measurement value must be finite and non-negative")


def validate_measurement_set(values: tuple[C8Measurement, ...]) -> None:
    if not isinstance(values, tuple) or not values:
        raise ValueError("measurement set must be a non-empty tuple")
    metric_ids = tuple(item.metric_id for item in values)
    if len(metric_ids) != len(set(metric_ids)):
        raise ValueError("measurement set contains duplicate metric IDs")


validate_protocol_identity()
