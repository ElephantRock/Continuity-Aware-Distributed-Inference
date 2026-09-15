from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping


C8_PROTOCOL_SCHEMA = "cadi.c8.1.real-cpu-prototype-protocol.v1"
C8_RUN_MANIFEST_SCHEMA = "cadi.c8.1.run-manifest.v1"
C8_MEASUREMENT_RECORD_SCHEMA = "cadi.c8.1.measurement-record.v1"
C8_RESULT_SCHEMA = "cadi.c8.1.result.v1"
C8_BASE_COMMIT = "0680d09efcf43a9b01552f9804906d66c3fc56f2"
C8_HEADLINE_MEASUREMENT_INSPECTION = "NONE"
C8_MEASURED_EVIDENCE_CLASS = "EV1_MEASURED_CPU"
C8_IMPORTED_INFERENCE_EVIDENCE_CLASS = "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"
C8_PROTOCOL_FINGERPRINT = "616cc4daa4c167152875522767a8cc968da25c63d6acc099c265bc3554641cb6"

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

C8_RUN_MANIFEST_FIELDS = (
    "schema",
    "git_commit",
    "running_git_commit",
    "experiment_id",
    "scenario_id",
    "authority_pid",
    "worker_pids",
    "fault_harness_pid",
    "transport_id",
    "os_name",
    "os_version",
    "python_version",
    "cpu_model",
    "worker_count",
    "active_sessions",
    "continuation_count",
    "state_count",
    "replica_count",
    "concurrency",
    "evidence_count",
    "fault_configuration",
    "seed",
    "warmup_operations",
    "samples_per_repetition",
    "repetitions",
    "timing_mode",
    "correctness_guards_enabled",
    "serialization_on_measured_path",
    "clock_used_for_semantic_authority",
    "authoritative_mutation_roles",
    "evidence_class",
    "imported_inference_evidence_class",
    "measured_modeled_timing_conflated",
    "cross_layer_preconditions_match",
    "host_load",
    "software_versions",
    "start_time_utc",
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
    "INCOMPLETE_RUN_PROVENANCE",
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


def _positive_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
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
        "run_manifest_schema": C8_RUN_MANIFEST_SCHEMA,
        "measurement_record_schema": C8_MEASUREMENT_RECORD_SCHEMA,
        "result_schema": C8_RESULT_SCHEMA,
        "run_manifest_fields": list(C8_RUN_MANIFEST_FIELDS),
        "process_topology": {
            "authority_process_count": 1,
            "minimum_worker_process_count": 1,
            "fault_harness_process_count": 1,
            "all_declared_role_pids_must_be_distinct": True,
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
    running_git_commit: str
    experiment_id: str
    scenario_id: str
    authority_pid: int
    worker_pids: tuple[int, ...]
    fault_harness_pid: int
    os_name: str
    os_version: str
    python_version: str
    cpu_model: str
    worker_count: int
    active_sessions: int
    continuation_count: int
    state_count: int
    replica_count: int
    concurrency: int
    evidence_count: int
    fault_configuration: Mapping[str, Any]
    seed: int | None
    host_load: Mapping[str, float]
    software_versions: Mapping[str, str]
    start_time_utc: str
    transport_id: str = C8_TRANSPORT_ID
    warmup_operations: int = C8_WARMUP_OPERATIONS
    samples_per_repetition: int = C8_SAMPLES_PER_REPETITION
    repetitions: int = C8_REPETITIONS
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

    def __post_init__(self) -> None:
        _git_sha(self.git_commit)
        _git_sha(self.running_git_commit, "running_git_commit")
        for value, name in (
            (self.experiment_id, "experiment_id"),
            (self.scenario_id, "scenario_id"),
            (self.os_name, "os_name"),
            (self.os_version, "os_version"),
            (self.python_version, "python_version"),
            (self.cpu_model, "cpu_model"),
            (self.start_time_utc, "start_time_utc"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        _positive_int(self.authority_pid, "authority_pid")
        if not isinstance(self.worker_pids, tuple) or not self.worker_pids:
            raise ValueError("worker_pids must be a non-empty tuple")
        for pid in self.worker_pids:
            _positive_int(pid, "worker_pid")
        _positive_int(self.fault_harness_pid, "fault_harness_pid")
        _positive_int(self.worker_count, "worker_count")
        _positive_int(self.active_sessions, "active_sessions")
        _positive_int(self.continuation_count, "continuation_count")
        _nonnegative_int(self.state_count, "state_count")
        _positive_int(self.replica_count, "replica_count")
        _positive_int(self.concurrency, "concurrency")
        _positive_int(self.evidence_count, "evidence_count")
        if self.seed is not None and (not isinstance(self.seed, int) or isinstance(self.seed, bool)):
            raise TypeError("seed must be an integer or None")
        if not isinstance(self.fault_configuration, Mapping):
            raise TypeError("fault_configuration must be a mapping")
        if not isinstance(self.host_load, Mapping) or not self.host_load:
            raise ValueError("host_load must be a non-empty mapping")
        if not isinstance(self.software_versions, Mapping) or not self.software_versions:
            raise ValueError("software_versions must be a non-empty mapping")
        if not all(isinstance(role, C8ProcessRole) for role in self.authoritative_mutation_roles):
            raise TypeError("authoritative_mutation_roles must contain C8ProcessRole values")
        if self.transport_id != C8_TRANSPORT_ID:
            raise ValueError("transport escaped frozen C8.1 reference transport")
        if not isinstance(self.timing_mode, C8TimingMode):
            raise TypeError("timing_mode must be C8TimingMode")
        if self.evidence_class != C8_MEASURED_EVIDENCE_CLASS:
            raise ValueError("C8 control-plane measurements must be EV1 measured CPU evidence")
        if self.imported_inference_evidence_class != C8_IMPORTED_INFERENCE_EVIDENCE_CLASS:
            raise ValueError("imported inference timing must retain its P-SRC2 evidence label")

    @property
    def process_count(self) -> int:
        return len({self.authority_pid, *self.worker_pids, self.fault_harness_pid})

    def invalid_conditions(self) -> tuple[str, ...]:
        invalid: list[str] = []
        if self.git_commit != self.running_git_commit:
            invalid.append("RUNNING_CHECKOUT_MISMATCH")
        all_pids = (self.authority_pid, *self.worker_pids, self.fault_harness_pid)
        if len(all_pids) != len(set(all_pids)) or self.process_count < 3:
            invalid.append("NO_REAL_PROCESS_BOUNDARY")
        if not self.serialization_on_measured_path:
            invalid.append("SERIALIZATION_BYPASSED")
        if self.authoritative_mutation_roles != (C8ProcessRole.CONTROL_PLANE_AUTHORITY,):
            invalid.append("WORKER_AUTHORITATIVE_MUTATION")
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
        scale_values = {
            "worker_count": self.worker_count,
            "active_sessions": self.active_sessions,
            "continuation_count": self.continuation_count,
            "state_count": self.state_count,
            "replica_count": self.replica_count,
            "concurrency": self.concurrency,
            "evidence_count": self.evidence_count,
        }
        if self.worker_count != len(self.worker_pids) or any(
            value not in C8_SCALE_AXES[name] for name, value in scale_values.items()
        ):
            invalid.append("INCOMPLETE_RUN_PROVENANCE")
        if (
            self.warmup_operations != C8_WARMUP_OPERATIONS
            or self.samples_per_repetition != C8_SAMPLES_PER_REPETITION
            or self.repetitions != C8_REPETITIONS
            or not self.host_load
            or not self.software_versions
        ):
            invalid.append("INCOMPLETE_RUN_PROVENANCE")
        return tuple(dict.fromkeys(invalid))

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
