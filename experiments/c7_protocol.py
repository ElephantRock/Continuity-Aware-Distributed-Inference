from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping

from simulator.policies import POLICY_CONTRACT_SCHEMA, PolicyID


C7_PROTOCOL_SCHEMA = "cadi.c7.1.efficiency-protocol.v1"
C7_MANIFEST_SCHEMA = "cadi.c7.1.experiment-manifest.v1"
C7_METRIC_SCHEMA = "cadi.c7.1.metric-definition.v1"
C7_BASE_COMMIT = "a0f192c7ff5943186593bdb178758c2ab1c131b5"

C5_MOONCAKE_SOURCE_REVISION = "3cca71daccf2a7afb8fe3f0295358f70e3a69fdb"
C5_MOONCAKE_SOURCE_SHA256 = (
    "b8cbb061a85206d729d91cdc2981f43c9e0d99209dce588d3af5f7934408b9df"
)

C6_REPRESENTATION_ID = (
    "cadi.c6.cost-representation.v4."
    "source-ordered-polynomial+source-linear-algebra+decode-step-affine"
)
C6_EVIDENCE_CLASS = "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"
C6_SCIENTIFIC_FINGERPRINT = (
    "cc1b62c4e38a1612720f601375c425c0bd7d67b4eff11b101bfdf87b79a7a61a"
)
C6_ARTIFACT_SHA256 = (
    "93990386135ecbf6fc38e579841eace3431a068b10ee59209fe6ad7084bd8889"
)
C6_MAX_MODEL_LENGTH = 4096
C6_TRANSFER_BYTES_PER_PREDICTOR_TOKEN = 8192
C6_TRANSFER_PREDICTOR_TOKEN_MAX = 4096
C6_MAX_VALIDATED_TRANSFER_BYTES = (
    C6_TRANSFER_BYTES_PER_PREDICTOR_TOKEN * C6_TRANSFER_PREDICTOR_TOKEN_MAX
)
C6_STATE_BYTES_PER_TOKEN = 524288
C6_MAX_UNSPLIT_TRANSFER_STATE_TOKENS = (
    C6_MAX_VALIDATED_TRANSFER_BYTES // C6_STATE_BYTES_PER_TOKEN
)
C7_SUPPORTED_HARDWARE_IDS = ("a100-80gb", "h100-80gb")
C7_P5_STATE_TOKENS = (1, 4, 16, 64)

C7_STOCHASTIC_SEEDS = tuple(range(64))
C7_CONVERGENCE_PREFIXES = (8, 16, 32, 64)
C7_BOOTSTRAP_RESAMPLES = 10_000
C7_BOOTSTRAP_SEED = 20260911
C7_PROPORTION_CI_HALF_WIDTH = 0.01
C7_CONTINUOUS_RELATIVE_CI_HALF_WIDTH = 0.05


class ParameterSource(str, Enum):
    P_SRC1 = "P-SRC1"
    P_SRC2 = "P-SRC2"
    P_SRC3 = "P-SRC3"
    P_SRC4 = "P-SRC4"


class WorkloadClass(str, Enum):
    REAL_TRACE = "REAL-TRACE"
    TRACE_AUGMENTED = "TRACE-AUGMENTED"
    SYNTHETIC_STRESS = "SYNTHETIC-STRESS"


class ExperimentSeries(str, Enum):
    P1_DEEP_REUSE = "P1_DEEP_REUSE"
    P2_TOOL_GAP_RETENTION = "P2_TOOL_GAP_RETENTION"
    P3_BRANCH_CACHE_PRESSURE = "P3_BRANCH_CACHE_PRESSURE"
    P4_FANOUT_SHARED_PREFIX = "P4_FANOUT_SHARED_PREFIX"
    P5_MIGRATION_VS_RECOMPUTE = "P5_MIGRATION_VS_RECOMPUTE"
    P7_STATELESS_OVERHEAD = "P7_STATELESS_OVERHEAD"


class MetricID(str, Enum):
    RECOMPUTATION_RATIO = "RECOMPUTATION_RATIO"
    STATE_REUSE_RATIO = "STATE_REUSE_RATIO"
    STATE_REUSE_TOKEN_RATIO = "STATE_REUSE_TOKEN_RATIO"
    USEFUL_STATE_RESIDENCY = "USEFUL_STATE_RESIDENCY"
    WASTED_STATE_RESIDENCY = "WASTED_STATE_RESIDENCY"
    COLD_CONTINUATION_RATE = "COLD_CONTINUATION_RATE"
    STATE_TRANSFER_VOLUME_BYTES = "STATE_TRANSFER_VOLUME_BYTES"
    PROGRAM_COMPLETION_TIME = "PROGRAM_COMPLETION_TIME"
    TOOL_RETURN_TTFT = "TOOL_RETURN_TTFT"
    FANOUT_COMPLETION_TIME = "FANOUT_COMPLETION_TIME"
    RECOVERY_TIME = "RECOVERY_TIME"


class EfficiencyEligibility(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING = (
        "SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING"
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _nonnegative_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _nonnegative_float(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def source_request_c6_admissible(input_tokens: int, output_tokens: int) -> bool:
    """Return whether an unmodified source row lies inside the frozen C6 domain."""
    _nonnegative_int(input_tokens, "input_tokens")
    _nonnegative_int(output_tokens, "output_tokens")
    return (
        input_tokens >= 1
        and output_tokens >= 1
        and input_tokens + output_tokens <= C6_MAX_MODEL_LENGTH
    )


def synthetic_request_c6_admissible(input_tokens: int, output_tokens: int) -> bool:
    """Return whether a synthetic control is executable by the frozen C6 runtime."""
    _nonnegative_int(input_tokens, "input_tokens")
    _nonnegative_int(output_tokens, "output_tokens")
    if input_tokens == 0 and output_tokens == 0:
        return True
    if output_tokens == 0:
        return 1 <= input_tokens <= C6_MAX_MODEL_LENGTH
    return input_tokens >= 1 and input_tokens + output_tokens <= C6_MAX_MODEL_LENGTH


def validated_transfer_state_admissible(state_tokens: int) -> bool:
    """P-SRC2 C7.1 transfer points are exactly the predeclared P5 State sizes."""
    _nonnegative_int(state_tokens, "state_tokens")
    return state_tokens in C7_P5_STATE_TOKENS


def validated_transfer_bytes_for_state_tokens(state_tokens: int) -> int:
    if not validated_transfer_state_admissible(state_tokens):
        raise ValueError("state_tokens is not a predeclared P-SRC2 transfer point")
    result = state_tokens * C6_STATE_BYTES_PER_TOKEN
    if result > C6_MAX_VALIDATED_TRANSFER_BYTES:
        raise AssertionError("predeclared transfer point escaped the C6 finite domain")
    if result % C6_TRANSFER_BYTES_PER_PREDICTOR_TOKEN != 0:
        raise AssertionError("predeclared transfer point is not on the C6 transfer lattice")
    return result


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: MetricID
    numerator: str
    denominator: str | None
    unit: str
    independent_unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.metric_id, MetricID):
            raise TypeError("metric_id must be MetricID")
        _nonempty(self.numerator, "numerator")
        if self.denominator is not None:
            _nonempty(self.denominator, "denominator")
        _nonempty(self.unit, "unit")
        _nonempty(self.independent_unit, "independent_unit")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C7_METRIC_SCHEMA,
            "metric_id": self.metric_id.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "unit": self.unit,
            "independent_unit": self.independent_unit,
        }


METRIC_DEFINITIONS: Mapping[MetricID, MetricDefinition] = {
    MetricID.RECOMPUTATION_RATIO: MetricDefinition(
        MetricID.RECOMPUTATION_RATIO,
        "eligible reusable prefix tokens not consumed and therefore recomputed",
        "sum of source/control input tokens across the same inference opportunities",
        "fraction",
        "Program",
    ),
    MetricID.STATE_REUSE_RATIO: MetricDefinition(
        MetricID.STATE_REUSE_RATIO,
        "eligible State reuse opportunities actually consumed",
        "eligible State reuse opportunities",
        "fraction",
        "Program",
    ),
    MetricID.STATE_REUSE_TOKEN_RATIO: MetricDefinition(
        MetricID.STATE_REUSE_TOKEN_RATIO,
        "eligible reusable prefix tokens actually consumed",
        "eligible reusable prefix tokens",
        "fraction",
        "Program",
    ),
    MetricID.USEFUL_STATE_RESIDENCY: MetricDefinition(
        MetricID.USEFUL_STATE_RESIDENCY,
        "resident byte-seconds from State residency intervals reused before interval end",
        "total resident byte-seconds across classified State residency intervals",
        "fraction",
        "Program",
    ),
    MetricID.WASTED_STATE_RESIDENCY: MetricDefinition(
        MetricID.WASTED_STATE_RESIDENCY,
        "resident byte-seconds from State residency intervals not reused before interval end",
        "total resident byte-seconds across classified State residency intervals",
        "fraction",
        "Program",
    ),
    MetricID.COLD_CONTINUATION_RATE: MetricDefinition(
        MetricID.COLD_CONTINUATION_RATE,
        "reuse-eligible continuation executions consuming zero reusable prefix tokens",
        "reuse-eligible continuation executions",
        "fraction",
        "Program",
    ),
    MetricID.STATE_TRANSFER_VOLUME_BYTES: MetricDefinition(
        MetricID.STATE_TRANSFER_VOLUME_BYTES,
        "bytes actually transferred for State movement",
        None,
        "bytes",
        "Program",
    ),
    MetricID.PROGRAM_COMPLETION_TIME: MetricDefinition(
        MetricID.PROGRAM_COMPLETION_TIME,
        "Program terminal time minus Program start time",
        None,
        "seconds",
        "Program",
    ),
    MetricID.TOOL_RETURN_TTFT: MetricDefinition(
        MetricID.TOOL_RETURN_TTFT,
        "first generated-token completion time minus tool-return/resume eligibility time",
        None,
        "seconds",
        "Program",
    ),
    MetricID.FANOUT_COMPLETION_TIME: MetricDefinition(
        MetricID.FANOUT_COMPLETION_TIME,
        "completion time of the last required fan-out branch minus fan-out start time",
        None,
        "seconds",
        "Program",
    ),
    MetricID.RECOVERY_TIME: MetricDefinition(
        MetricID.RECOVERY_TIME,
        "first authoritative useful post-fault progress time minus injected fault time",
        None,
        "seconds",
        "Program",
    ),
}


def recomputation_ratio(
    *, total_input_tokens: int, eligible_reuse_tokens: int, consumed_reuse_tokens: int
) -> float:
    total = _nonnegative_int(total_input_tokens, "total_input_tokens")
    eligible = _nonnegative_int(eligible_reuse_tokens, "eligible_reuse_tokens")
    consumed = _nonnegative_int(consumed_reuse_tokens, "consumed_reuse_tokens")
    if consumed > eligible:
        raise ValueError("consumed_reuse_tokens cannot exceed eligible_reuse_tokens")
    if eligible > total:
        raise ValueError("eligible_reuse_tokens cannot exceed total_input_tokens")
    return 0.0 if total == 0 else (eligible - consumed) / total


def state_reuse_ratio(*, eligible_opportunities: int, consumed_opportunities: int) -> float:
    eligible = _nonnegative_int(eligible_opportunities, "eligible_opportunities")
    consumed = _nonnegative_int(consumed_opportunities, "consumed_opportunities")
    if consumed > eligible:
        raise ValueError("consumed_opportunities cannot exceed eligible_opportunities")
    return 0.0 if eligible == 0 else consumed / eligible


def state_reuse_token_ratio(*, eligible_tokens: int, consumed_tokens: int) -> float:
    eligible = _nonnegative_int(eligible_tokens, "eligible_tokens")
    consumed = _nonnegative_int(consumed_tokens, "consumed_tokens")
    if consumed > eligible:
        raise ValueError("consumed_tokens cannot exceed eligible_tokens")
    return 0.0 if eligible == 0 else consumed / eligible


def cold_continuation_rate(
    *, eligible_continuations: int, fully_reconstructed_continuations: int
) -> float:
    eligible = _nonnegative_int(eligible_continuations, "eligible_continuations")
    cold = _nonnegative_int(
        fully_reconstructed_continuations, "fully_reconstructed_continuations"
    )
    if cold > eligible:
        raise ValueError("fully reconstructed continuations cannot exceed eligible continuations")
    return 0.0 if eligible == 0 else cold / eligible


def residency_ratios(
    *, useful_byte_seconds: float, wasted_byte_seconds: float
) -> tuple[float, float]:
    useful = _nonnegative_float(useful_byte_seconds, "useful_byte_seconds")
    wasted = _nonnegative_float(wasted_byte_seconds, "wasted_byte_seconds")
    total = useful + wasted
    return (0.0, 0.0) if total == 0.0 else (useful / total, wasted / total)


def elapsed_metric(*, start: float, end: float, name: str) -> float:
    start_value = _nonnegative_float(start, f"{name}.start")
    end_value = _nonnegative_float(end, f"{name}.end")
    if end_value < start_value:
        raise ValueError(f"{name} end cannot precede start")
    return end_value - start_value


def efficiency_eligibility(*, covered_semantic_violations: int) -> EfficiencyEligibility:
    violations = _nonnegative_int(covered_semantic_violations, "covered_semantic_violations")
    return (
        EfficiencyEligibility.SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING
        if violations
        else EfficiencyEligibility.ELIGIBLE
    )


@dataclass(frozen=True, slots=True)
class AxisDefinition:
    name: str
    values: tuple[int | float, ...]
    source: ParameterSource
    reference_value: int | float

    def __post_init__(self) -> None:
        _nonempty(self.name, "axis name")
        if not isinstance(self.values, tuple) or not self.values:
            raise ValueError("axis values must be a non-empty tuple")
        if len(set(self.values)) != len(self.values):
            raise ValueError("axis values must be unique")
        for value in self.values:
            _nonnegative_float(value, f"{self.name} value")
        if self.reference_value not in self.values:
            raise ValueError("reference_value must be one of axis values")
        if not isinstance(self.source, ParameterSource):
            raise TypeError("source must be ParameterSource")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "values": list(self.values),
            "source": self.source.value,
            "reference_value": self.reference_value,
        }


@dataclass(frozen=True, slots=True)
class SurfaceDefinition:
    series: ExperimentSeries
    x_axis: str
    y_axis: str

    def __post_init__(self) -> None:
        if not isinstance(self.series, ExperimentSeries):
            raise TypeError("series must be ExperimentSeries")
        _nonempty(self.x_axis, "x_axis")
        _nonempty(self.y_axis, "y_axis")
        if self.x_axis == self.y_axis:
            raise ValueError("surface axes must differ")

    def to_dict(self) -> dict[str, str]:
        return {"series": self.series.value, "x_axis": self.x_axis, "y_axis": self.y_axis}


AXES: Mapping[str, AxisDefinition] = {
    "session_depth": AxisDefinition("session_depth", (2, 4, 8, 16, 32), ParameterSource.P_SRC4, 8),
    "reusable_prefix_fraction": AxisDefinition(
        "reusable_prefix_fraction", (0.0, 0.25, 0.5, 0.75, 1.0), ParameterSource.P_SRC4, 0.5
    ),
    "cache_capacity_ratio": AxisDefinition(
        "cache_capacity_ratio", (0.25, 0.5, 1.0, 2.0), ParameterSource.P_SRC4, 1.0
    ),
    "worker_count": AxisDefinition("worker_count", (2, 4, 8), ParameterSource.P_SRC4, 4),
    "arrival_intensity": AxisDefinition(
        "arrival_intensity", (0.5, 1.0, 2.0), ParameterSource.P_SRC4, 1.0
    ),
    "tool_gap_seconds": AxisDefinition(
        "tool_gap_seconds", (0.25, 1.0, 5.0, 30.0, 120.0), ParameterSource.P_SRC4, 5.0
    ),
    "tool_return_probability": AxisDefinition(
        "tool_return_probability", (0.25, 0.5, 0.75, 1.0), ParameterSource.P_SRC4, 1.0
    ),
    "branch_width": AxisDefinition("branch_width", (2, 4, 8, 16), ParameterSource.P_SRC4, 4),
    "speculative_fraction": AxisDefinition(
        "speculative_fraction", (0.0, 0.25, 0.5, 0.75), ParameterSource.P_SRC4, 0.25
    ),
    "fanout_width": AxisDefinition("fanout_width", (2, 4, 8, 16), ParameterSource.P_SRC4, 4),
    "shared_prefix_fraction": AxisDefinition(
        "shared_prefix_fraction", (0.25, 0.5, 0.75, 1.0), ParameterSource.P_SRC4, 0.5
    ),
    "state_tokens": AxisDefinition("state_tokens", C7_P5_STATE_TOKENS, ParameterSource.P_SRC4, 16),
    "recompute_tokens": AxisDefinition(
        "recompute_tokens", (64, 256, 1024, 2048, 4096), ParameterSource.P_SRC4, 1024
    ),
}


SURFACES: tuple[SurfaceDefinition, ...] = (
    SurfaceDefinition(ExperimentSeries.P1_DEEP_REUSE, "session_depth", "reusable_prefix_fraction"),
    SurfaceDefinition(
        ExperimentSeries.P1_DEEP_REUSE, "reusable_prefix_fraction", "cache_capacity_ratio"
    ),
    SurfaceDefinition(
        ExperimentSeries.P2_TOOL_GAP_RETENTION, "tool_gap_seconds", "cache_capacity_ratio"
    ),
    SurfaceDefinition(
        ExperimentSeries.P2_TOOL_GAP_RETENTION, "tool_gap_seconds", "tool_return_probability"
    ),
    SurfaceDefinition(
        ExperimentSeries.P3_BRANCH_CACHE_PRESSURE, "branch_width", "cache_capacity_ratio"
    ),
    SurfaceDefinition(
        ExperimentSeries.P4_FANOUT_SHARED_PREFIX, "fanout_width", "shared_prefix_fraction"
    ),
    SurfaceDefinition(
        ExperimentSeries.P5_MIGRATION_VS_RECOMPUTE, "state_tokens", "recompute_tokens"
    ),
    SurfaceDefinition(
        ExperimentSeries.P7_STATELESS_OVERHEAD, "worker_count", "arrival_intensity"
    ),
)


@dataclass(frozen=True, slots=True)
class EfficiencyProtocol:
    base_commit: str = C7_BASE_COMMIT

    def __post_init__(self) -> None:
        if self.base_commit != C7_BASE_COMMIT:
            raise ValueError("C7.1 protocol base commit is frozen")
        for surface in SURFACES:
            if surface.x_axis not in AXES or surface.y_axis not in AXES:
                raise ValueError("surface references an undeclared axis")
        if AXES["state_tokens"].values != C7_P5_STATE_TOKENS:
            raise ValueError("P5 State axis must exactly match the frozen transfer points")
        if any(axis.source is not ParameterSource.P_SRC4 for axis in AXES.values()):
            raise ValueError(
                "C7.1 sweep axes are synthetic P-SRC4 choices; P-SRC2 classifies cost evaluation"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C7_PROTOCOL_SCHEMA,
            "base_commit": self.base_commit,
            "policy_contract_schema": POLICY_CONTRACT_SCHEMA,
            "policies": [policy.value for policy in PolicyID],
            "source": {
                "id": "mooncake-fast25-conversation",
                "revision": C5_MOONCAKE_SOURCE_REVISION,
                "sha256": C5_MOONCAKE_SOURCE_SHA256,
                "admissibility": (
                    "input_tokens>=1 && output_tokens>=1 && input_tokens+output_tokens<=4096"
                ),
                "clip_truncate_or_extrapolate": False,
                "filter_is_population_claim": False,
            },
            "cost_model": {
                "representation_id": C6_REPRESENTATION_ID,
                "evidence_class": C6_EVIDENCE_CLASS,
                "scientific_fingerprint": C6_SCIENTIFIC_FINGERPRINT,
                "artifact_sha256": C6_ARTIFACT_SHA256,
                "supported_hardware_ids": list(C7_SUPPORTED_HARDWARE_IDS),
                "max_model_length": C6_MAX_MODEL_LENGTH,
                "transfer_bytes_per_predictor_token": C6_TRANSFER_BYTES_PER_PREDICTOR_TOKEN,
                "max_validated_transfer_bytes": C6_MAX_VALIDATED_TRANSFER_BYTES,
                "state_bytes_per_token": C6_STATE_BYTES_PER_TOKEN,
                "p5_state_tokens": list(C7_P5_STATE_TOKENS),
                "max_unsplit_transfer_state_tokens": C6_MAX_UNSPLIT_TRANSFER_STATE_TOKENS,
                "p_src2_scope": "cost evaluation only; sweep-point choice remains P-SRC4",
            },
            "metrics": [METRIC_DEFINITIONS[metric].to_dict() for metric in MetricID],
            "axes": [AXES[name].to_dict() for name in sorted(AXES)],
            "surfaces": [surface.to_dict() for surface in SURFACES],
            "statistics": {
                "common_random_numbers": True,
                "stochastic_seeds": list(C7_STOCHASTIC_SEEDS),
                "convergence_prefixes": list(C7_CONVERGENCE_PREFIXES),
                "proportion_ci_half_width": C7_PROPORTION_CI_HALF_WIDTH,
                "continuous_relative_ci_half_width": C7_CONTINUOUS_RELATIVE_CI_HALF_WIDTH,
                "continuous_zero_baseline_rule": (
                    "do not declare early convergence from a relative target; consume through seed 63 "
                    "unless the paired interval is exactly zero"
                ),
                "bootstrap_resamples": C7_BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": C7_BOOTSTRAP_SEED,
                "bootstrap_interval": "percentile-95",
                "cluster_unit": "Program; Session only when Program is not independent unit",
                "max_runs_if_not_converged": 64,
                "deterministic_source_runs_replicated": False,
            },
            "semantic_validity": {
                "covered_violation_excludes_efficiency_ranking": True,
                "invalid_label": (
                    EfficiencyEligibility.SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING.value
                ),
            },
            "invalid_result_conditions": [
                "source row clipped, truncated, or extrapolated to enter the C6 domain",
                "positive decode outside input_tokens>=1 and input_tokens+output_tokens<=4096",
                "hardware identity outside the accepted C6 runtime family",
                "P-SRC2 transfer State size outside {1,4,16,64} State tokens",
                "compared policies do not share workload seed/segment/resource/fault schedule",
                "policy receives information outside cadi.policy-information-contract.v2",
                "covered semantic violation counted as an efficiency win",
                "synthetic semantic parameter relabeled as empirical source evidence",
                "observed comparative result used to revise this protocol in place",
            ],
            "result_revision_rule": (
                "Observed C7 comparative results may not change this protocol in place; "
                "a required revision creates a new protocol version and preserves prior results."
            ),
        }

    @property
    def fingerprint(self) -> str:
        return _sha256_json(self.to_dict())


FROZEN_C7_PROTOCOL = EfficiencyProtocol()
C7_PROTOCOL_FINGERPRINT = FROZEN_C7_PROTOCOL.fingerprint


@dataclass(frozen=True, slots=True)
class C7ExperimentManifest:
    experiment_id: str
    git_commit: str
    protocol_fingerprint: str
    series: ExperimentSeries
    policy_id: PolicyID
    workload_class: WorkloadClass
    hardware_id: str
    program_objective: str
    seed: int | None
    source_dataset_fingerprint: str | None
    augmentation_fingerprint: str | None
    parameters: tuple[tuple[str, int | float], ...]
    parameter_sources: tuple[tuple[str, ParameterSource], ...]

    def __post_init__(self) -> None:
        _nonempty(self.experiment_id, "experiment_id")
        _nonempty(self.git_commit, "git_commit")
        if self.protocol_fingerprint != C7_PROTOCOL_FINGERPRINT:
            raise ValueError("manifest must bind to the frozen C7.1 protocol fingerprint")
        if not isinstance(self.series, ExperimentSeries):
            raise TypeError("series must be ExperimentSeries")
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if not isinstance(self.workload_class, WorkloadClass):
            raise TypeError("workload_class must be WorkloadClass")
        _nonempty(self.hardware_id, "hardware_id")
        if self.hardware_id not in C7_SUPPORTED_HARDWARE_IDS:
            raise ValueError("hardware_id is outside the accepted C6 runtime family")
        _nonempty(self.program_objective, "program_objective")
        if self.seed is not None:
            _nonnegative_int(self.seed, "seed")
            if self.seed not in C7_STOCHASTIC_SEEDS:
                raise ValueError("stochastic seed must come from the frozen C7.1 sequence 0..63")
        if self.source_dataset_fingerprint is not None:
            _nonempty(self.source_dataset_fingerprint, "source_dataset_fingerprint")
        if self.augmentation_fingerprint is not None:
            _nonempty(self.augmentation_fingerprint, "augmentation_fingerprint")

        if self.workload_class is WorkloadClass.REAL_TRACE:
            if self.seed is not None:
                raise ValueError("deterministic REAL-TRACE manifests must not invent a stochastic seed")
            if self.source_dataset_fingerprint is None:
                raise ValueError("REAL-TRACE manifests require source_dataset_fingerprint")
            if self.augmentation_fingerprint is not None:
                raise ValueError("REAL-TRACE manifests must not carry an augmentation fingerprint")
        elif self.workload_class is WorkloadClass.TRACE_AUGMENTED:
            if self.seed is None:
                raise ValueError("TRACE-AUGMENTED manifests require a fixed seed")
            if self.source_dataset_fingerprint is None or self.augmentation_fingerprint is None:
                raise ValueError("TRACE-AUGMENTED manifests require source and augmentation fingerprints")
        else:
            if self.seed is None:
                raise ValueError("SYNTHETIC-STRESS manifests require a fixed seed")
            if self.source_dataset_fingerprint is not None or self.augmentation_fingerprint is not None:
                raise ValueError("SYNTHETIC-STRESS manifests must not claim source/augmentation fingerprints")

        if not isinstance(self.parameters, tuple):
            raise TypeError("parameters must be a tuple")
        if not isinstance(self.parameter_sources, tuple):
            raise TypeError("parameter_sources must be a tuple")
        parameter_names = tuple(name for name, _ in self.parameters)
        source_names = tuple(name for name, _ in self.parameter_sources)
        if parameter_names != tuple(sorted(parameter_names)) or len(set(parameter_names)) != len(parameter_names):
            raise ValueError("parameters must have unique canonically ordered names")
        if source_names != tuple(sorted(source_names)) or len(set(source_names)) != len(source_names):
            raise ValueError("parameter_sources must have unique canonically ordered names")
        if parameter_names != source_names:
            raise ValueError("every parameter must have exactly one source classification")

        parameter_source_map = dict(self.parameter_sources)
        for name, value in self.parameters:
            _nonempty(name, "parameter name")
            _nonnegative_float(value, f"parameter {name}")
            axis = AXES.get(name)
            if axis is not None:
                if value not in axis.values:
                    raise ValueError(f"parameter {name} is outside the frozen C7.1 axis values")
                if parameter_source_map[name] is not axis.source:
                    raise ValueError(f"parameter {name} must retain its frozen C7.1 source class")
        for name, source in self.parameter_sources:
            _nonempty(name, "parameter source name")
            if not isinstance(source, ParameterSource):
                raise TypeError("parameter source must be ParameterSource")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C7_MANIFEST_SCHEMA,
            "experiment_id": self.experiment_id,
            "git_commit": self.git_commit,
            "protocol_fingerprint": self.protocol_fingerprint,
            "series": self.series.value,
            "policy_id": self.policy_id.value,
            "policy_contract_schema": POLICY_CONTRACT_SCHEMA,
            "workload_class": self.workload_class.value,
            "hardware_id": self.hardware_id,
            "cost_representation_id": C6_REPRESENTATION_ID,
            "cost_evidence_class": C6_EVIDENCE_CLASS,
            "cost_scientific_fingerprint": C6_SCIENTIFIC_FINGERPRINT,
            "cost_artifact_sha256": C6_ARTIFACT_SHA256,
            "program_objective": self.program_objective,
            "seed": self.seed,
            "source_dataset_fingerprint": self.source_dataset_fingerprint,
            "augmentation_fingerprint": self.augmentation_fingerprint,
            "parameters": {name: value for name, value in self.parameters},
            "parameter_sources": {name: source.value for name, source in self.parameter_sources},
        }

    @property
    def fingerprint(self) -> str:
        return _sha256_json(self.to_dict())


def main() -> None:
    print(
        _canonical_json(
            {
                "protocol": FROZEN_C7_PROTOCOL.to_dict(),
                "protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
            }
        )
    )


if __name__ == "__main__":
    main()
