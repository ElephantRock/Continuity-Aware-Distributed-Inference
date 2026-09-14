from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Sequence

from experiments.c7_protocol import (
    AXES,
    C6_ARTIFACT_SHA256,
    C6_EVIDENCE_CLASS,
    C6_SCIENTIFIC_FINGERPRINT,
    C7_BOOTSTRAP_RESAMPLES,
    C7_BOOTSTRAP_SEED,
    C7_PROTOCOL_FINGERPRINT,
    C7_STOCHASTIC_SEEDS,
    C7_SUPPORTED_HARDWARE_IDS,
    ExperimentSeries,
    MetricID,
    WorkloadClass,
)
from experiments.c72_routing_reuse import (
    C72_ADMISSIBLE_NORMALIZATION_VERSION,
    C72_BASE_COMMIT,
    C72_PAIRED_SCHEMA,
    C72_RESULT_SCHEMA,
)
from experiments.trace_workload import NormalizedTraceRecord
from simulator.policies import PolicyID


C75_PROTOCOL_SCHEMA = "cadi.c7.5a.g2-integration-protocol.v1"
C75_BASE_COMMIT = "8ecb61c67ca73de4855d34a01ae35e54b2677cb3"
C75_COMPARATIVE_RESULT_INSPECTION = "NONE"

C75_C72_MERGE = "47f1574c79f84c2e32e432a5da0f0522a6436937"
C75_C72_ADMISSIBLE_DATASET_FINGERPRINT = (
    "05a37c9cb3d1649aacd82de1b773161feafa6304b33d90fa3cff33eb60ae7846"
)
C75_C72_SOURCE_DATASET_FINGERPRINT = (
    "22ead90d97ae218f229f94378f8f019499ede0e4050e6cbf8092b455ae047718"
)

C75_C73_PROTOCOL_FINGERPRINT = (
    "694f3c4e24ecca61b5c265e26f8417eafbcd4c932af3f9e60813fbb76d1b6800"
)
C75_C73_EXECUTION_SHA = "8d241311bd2f457c0d22404f467d3e1b7fc757ae"
C75_C73_ARTIFACT_SHA256 = (
    "f725c4b8f0c56e24a6b8304d608bacf4b6c3c00b899a012898383cbfd9f45fa5"
)
C75_H5_DECISION = "SUPPORTED_WITHIN_DECLARED_PHASE_SPACE"

C75_C74_PROTOCOL_FINGERPRINT = (
    "21601257767442204f1acfae4c6e2e42959e37701c9236a2d2b560a2dda486b6"
)
C75_C74_ENGINE_FINGERPRINT = (
    "2083e1a227330ea1a0ac57caf166cf8777865cbe6e3c6d259796cfd56fe02c32"
)
C75_C74_SCIENTIFIC_FINGERPRINT = (
    "a8d435456b1d784592eda96e3352190456fb9aeb34dda6c12c5a0edb58b54944"
)
C75_C74_ARTIFACT_SHA256 = (
    "3550516d9d2355849f5d6a731d7577f906856ec94a60d8947ad81e8382e25e18"
)
C75_H6_DECISION = "EFFICIENCY_STRENGTHENED_WITHIN_DECLARED_PHASE_SPACE"

C75_SOURCE_SELECTION_SCHEMA = "cadi.c7.5a.seed-source-selection.v1"
C75_RESOURCE_REALIZATION_SCHEMA = "cadi.c7.5a.resource-realization.v1"
C75_SOURCE_SELECTION_SALT = "cadi.c7.5a.seed-record.v1"
C75_RESOURCE_SALT = "cadi.c7.5a.resource-fact.v1"
C75_QUARTER_STEP_DIVISOR = 4
C75_WORKER_CAPACITY = 1
C75_QUEUE_LEVELS = 4

C75_P1_SESSION_DEPTHS = tuple(int(x) for x in AXES["session_depth"].values)
C75_P1_REUSE_FRACTIONS = tuple(float(x) for x in AXES["reusable_prefix_fraction"].values)
C75_WORKER_COUNTS = tuple(int(x) for x in AXES["worker_count"].values)
C75_P4_FANOUT_WIDTHS = tuple(int(x) for x in AXES["fanout_width"].values)
C75_P4_SHARED_FRACTIONS = tuple(float(x) for x in AXES["shared_prefix_fraction"].values)
C75_P4_WORKER_COUNT = int(AXES["worker_count"].reference_value)
C75_P7_ARRIVAL_INTENSITY = float(AXES["arrival_intensity"].reference_value)

C75_H4_PRIMARY_COMPARATORS = (PolicyID.B0, PolicyID.B1, PolicyID.B2)
C75_H4_DIAGNOSTIC_COMPARATOR = PolicyID.B3
C75_H7_REFERENCE = PolicyID.B0
C75_H7_MIN_ORDERED_LEVELS = 3
C75_MIN_CONNECTED_SUPPORT_CELLS = 2


class C75Representability(str, Enum):
    RANKABLE = "RANKABLE"
    NEGATIVE_CONTROL_ONLY = "NEGATIVE_CONTROL_ONLY"
    NOT_REALIZED_BY_C72 = "NOT_REALIZED_BY_C72"


class C75G2Decision(str, Enum):
    A = "G2_A_STRONG_EFFICIENCY_SUPPORT"
    B = "G2_B_MEANINGFUL_FRONTIER_OR_STATEFULNESS_THRESHOLD"
    C = "G2_C_NO_USEFUL_SYSTEMS_LEVERAGE"


@dataclass(frozen=True, slots=True)
class C75Surface:
    surface_id: str
    series: ExperimentSeries
    x_axis: str
    y_axis: str | None
    representability: C75Representability
    strata_axis: str | None = None
    fixed_parameters: tuple[tuple[str, int | float], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.surface_id, str) or not self.surface_id:
            raise ValueError("surface_id must be non-empty")
        if not isinstance(self.series, ExperimentSeries):
            raise TypeError("series must be ExperimentSeries")
        if self.x_axis not in AXES:
            raise ValueError("x_axis must be a frozen C7.1 axis")
        if self.y_axis is not None and self.y_axis not in AXES:
            raise ValueError("y_axis must be a frozen C7.1 axis or None")
        if self.strata_axis is not None and self.strata_axis not in AXES:
            raise ValueError("strata_axis must be a frozen C7.1 axis or None")
        names = tuple(name for name, _ in self.fixed_parameters)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("fixed_parameters must have unique canonical names")
        for name, value in self.fixed_parameters:
            axis = AXES.get(name)
            if axis is None or value not in axis.values:
                raise ValueError("fixed parameter escaped frozen C7.1 axis")

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "series": self.series.value,
            "x_axis": self.x_axis,
            "y_axis": self.y_axis,
            "strata_axis": self.strata_axis,
            "representability": self.representability.value,
            "fixed_parameters": {name: value for name, value in self.fixed_parameters},
        }


C75_SURFACES = (
    C75Surface(
        "P1_DEPTH_REUSE_BY_WORKERS",
        ExperimentSeries.P1_DEEP_REUSE,
        "session_depth",
        "reusable_prefix_fraction",
        C75Representability.RANKABLE,
        strata_axis="worker_count",
    ),
    C75Surface(
        "P1_REUSE_CACHE_UNREALIZED",
        ExperimentSeries.P1_DEEP_REUSE,
        "reusable_prefix_fraction",
        "cache_capacity_ratio",
        C75Representability.NOT_REALIZED_BY_C72,
    ),
    C75Surface(
        "P4_FANOUT_SHARED_PREFIX",
        ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
        "fanout_width",
        "shared_prefix_fraction",
        C75Representability.RANKABLE,
        fixed_parameters=(("worker_count", C75_P4_WORKER_COUNT),),
    ),
    C75Surface(
        "P7_STATELESS_WORKERS_CONTROL",
        ExperimentSeries.P7_STATELESS_OVERHEAD,
        "worker_count",
        None,
        C75Representability.NEGATIVE_CONTROL_ONLY,
        fixed_parameters=(("arrival_intensity", C75_P7_ARRIVAL_INTENSITY),),
    ),
    C75Surface(
        "P7_WORKERS_ARRIVAL_UNREALIZED",
        ExperimentSeries.P7_STATELESS_OVERHEAD,
        "worker_count",
        "arrival_intensity",
        C75Representability.NOT_REALIZED_BY_C72,
    ),
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def source_record_order_key(record_id: str) -> str:
    if not isinstance(record_id, str) or not record_id:
        raise ValueError("record_id must be non-empty")
    return hashlib.sha256(f"{C75_SOURCE_SELECTION_SALT}|{record_id}".encode("utf-8")).hexdigest()


def source_record_eligible(input_tokens: int, output_tokens: int) -> bool:
    for value, name in ((input_tokens, "input_tokens"), (output_tokens, "output_tokens")):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{name} must be int")
    return (
        input_tokens >= 1
        and output_tokens >= 1
        and input_tokens + output_tokens <= 4096
        and input_tokens % C75_QUARTER_STEP_DIVISOR == 0
    )


def select_seed_records(records: Sequence[NormalizedTraceRecord]) -> tuple[NormalizedTraceRecord, ...]:
    if not isinstance(records, Sequence):
        raise TypeError("records must be a Sequence")
    eligible: list[NormalizedTraceRecord] = []
    for record in records:
        if not isinstance(record, NormalizedTraceRecord):
            raise TypeError("records must contain NormalizedTraceRecord values")
        if record.input_tokens is None or record.output_tokens is None:
            continue
        if source_record_eligible(record.input_tokens, record.output_tokens):
            eligible.append(record)
    eligible.sort(key=lambda record: (source_record_order_key(record.record_id), record.record_id))
    required = len(C7_STOCHASTIC_SEEDS)
    if len(eligible) < required:
        raise ValueError("source-selection pool has fewer than 64 eligible records")
    selected = tuple(eligible[:required])
    if len({record.record_id for record in selected}) != required:
        raise AssertionError("seed source selection must contain unique source records")
    return selected


def reusable_tokens(input_tokens: int, fraction: float) -> int:
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool) or input_tokens <= 0:
        raise ValueError("input_tokens must be a positive integer")
    if input_tokens % C75_QUARTER_STEP_DIVISOR:
        raise ValueError("input_tokens must be divisible by four")
    if fraction not in C75_P1_REUSE_FRACTIONS:
        raise ValueError("fraction must be a frozen quarter-step reusable-prefix fraction")
    tokens = int(input_tokens * fraction)
    if tokens / input_tokens != fraction:
        raise AssertionError("quarter-step reuse must be represented exactly without rounding")
    return tokens


def _seed_fact(seed: int, label: str, modulo: int) -> int:
    if seed not in C7_STOCHASTIC_SEEDS:
        raise ValueError("seed must come from frozen C7.1 sequence 0..63")
    if not isinstance(label, str) or not label:
        raise ValueError("label must be non-empty")
    if not isinstance(modulo, int) or isinstance(modulo, bool) or modulo <= 0:
        raise ValueError("modulo must be positive integer")
    digest = hashlib.sha256(f"{C75_RESOURCE_SALT}|{label}|{seed}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % modulo


def state_worker_index(seed: int, worker_count: int, *, operation_ordinal: int = 0) -> int:
    return _seed_fact(seed, f"state-location:{operation_ordinal}", worker_count)


def session_preferred_worker_index(seed: int, worker_count: int) -> int:
    return _seed_fact(seed, "session-preferred", worker_count)


def worker_queue_depth(seed: int, worker_index: int, *, operation_ordinal: int = 0) -> int:
    if not isinstance(worker_index, int) or isinstance(worker_index, bool) or worker_index < 0:
        raise ValueError("worker_index must be non-negative integer")
    return _seed_fact(seed, f"worker-load:{operation_ordinal}:{worker_index}", C75_QUEUE_LEVELS)


def p4_state_worker_index(seed: int, worker_count: int = C75_P4_WORKER_COUNT) -> int:
    return _seed_fact(seed, "p4-shared-state-location", worker_count)


def p4_worker_queue_depth(seed: int, worker_index: int, *, branch_ordinal: int = 0) -> int:
    if not isinstance(worker_index, int) or isinstance(worker_index, bool) or worker_index < 0:
        raise ValueError("worker_index must be non-negative integer")
    return _seed_fact(seed, f"p4-worker-load:{branch_ordinal}:{worker_index}", C75_QUEUE_LEVELS)


def p1_cells() -> tuple[tuple[int, float, int], ...]:
    return tuple(
        (depth, fraction, workers)
        for workers in C75_WORKER_COUNTS
        for depth in C75_P1_SESSION_DEPTHS
        for fraction in C75_P1_REUSE_FRACTIONS
    )


def p4_cells() -> tuple[tuple[int, float], ...]:
    return tuple(
        (width, fraction)
        for width in C75_P4_FANOUT_WIDTHS
        for fraction in C75_P4_SHARED_FRACTIONS
    )


def p7_control_cells() -> tuple[int, ...]:
    return C75_WORKER_COUNTS


def p1_cells_adjacent(left: tuple[int, float, int], right: tuple[int, float, int]) -> bool:
    ld, lf, lw = left
    rd, rf, rw = right
    if lw != rw:
        return False
    depth_steps = abs(C75_P1_SESSION_DEPTHS.index(ld) - C75_P1_SESSION_DEPTHS.index(rd))
    fraction_steps = abs(C75_P1_REUSE_FRACTIONS.index(lf) - C75_P1_REUSE_FRACTIONS.index(rf))
    return depth_steps + fraction_steps == 1


def p4_cells_adjacent(left: tuple[int, float], right: tuple[int, float]) -> bool:
    lw, lf = left
    rw, rf = right
    width_steps = abs(C75_P4_FANOUT_WIDTHS.index(lw) - C75_P4_FANOUT_WIDTHS.index(rw))
    fraction_steps = abs(C75_P4_SHARED_FRACTIONS.index(lf) - C75_P4_SHARED_FRACTIONS.index(rf))
    return width_steps + fraction_steps == 1


def relative_timing_benefit(*, baseline_seconds: float, b4_seconds: float) -> float:
    for value, name in ((baseline_seconds, "baseline_seconds"), (b4_seconds, "b4_seconds")):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"{name} must be numeric")
        if not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
    if baseline_seconds == 0:
        raise ValueError("baseline_seconds must be positive for relative benefit")
    return (float(baseline_seconds) - float(b4_seconds)) / float(baseline_seconds)


def adjudicate_g2(*, h4_supported: bool, h5_supported: bool, h6_supported: bool, h7_supported: bool) -> C75G2Decision:
    values = (h4_supported, h5_supported, h6_supported, h7_supported)
    if not all(isinstance(value, bool) for value in values):
        raise TypeError("H4-H7 support flags must be bool")
    if h7_supported and sum((h4_supported, h5_supported, h6_supported)) >= 2:
        return C75G2Decision.A
    if not any(values):
        return C75G2Decision.C
    return C75G2Decision.B


@dataclass(frozen=True, slots=True)
class C75Protocol:
    base_commit: str = C75_BASE_COMMIT

    def __post_init__(self) -> None:
        if self.base_commit != C75_BASE_COMMIT:
            raise ValueError("C7.5a base commit is frozen")
        checks = (
            (C7_PROTOCOL_FINGERPRINT, "706e0d5fff362a1eda8c906b957c914251c6e7949bac6be3c2c143ae21625474", "C7.1 protocol"),
            (C72_BASE_COMMIT, "990ef4f081b0edebb1e2ca83bddaf252eb39e549", "C7.2 base"),
            (C6_SCIENTIFIC_FINGERPRINT, "cc1b62c4e38a1612720f601375c425c0bd7d67b4eff11b101bfdf87b79a7a61a", "C6 scientific fingerprint"),
            (C6_ARTIFACT_SHA256, "93990386135ecbf6fc38e579841eace3431a068b10ee59209fe6ad7084bd8889", "C6 artifact"),
            (C6_EVIDENCE_CLASS, "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2", "C6 evidence class"),
        )
        for actual, expected, name in checks:
            if actual != expected:
                raise RuntimeError(f"{name} drift")
        if tuple(C7_STOCHASTIC_SEEDS) != tuple(range(64)):
            raise RuntimeError("C7 seed schedule drift")
        if C7_BOOTSTRAP_RESAMPLES != 10_000 or C7_BOOTSTRAP_SEED != 20260911:
            raise RuntimeError("C7 bootstrap rule drift")
        if tuple(C7_SUPPORTED_HARDWARE_IDS) != ("a100-80gb", "h100-80gb"):
            raise RuntimeError("accepted hardware strata drift")
        if AXES["worker_count"].reference_value != C75_P4_WORKER_COUNT:
            raise RuntimeError("worker-count reference drift")
        if AXES["arrival_intensity"].reference_value != C75_P7_ARRIVAL_INTENSITY:
            raise RuntimeError("arrival-intensity reference drift")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C75_PROTOCOL_SCHEMA,
            "base_commit": self.base_commit,
            "comparative_result_inspection": C75_COMPARATIVE_RESULT_INSPECTION,
            "parents": {
                "c7_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
                "c72_merge": C75_C72_MERGE,
                "c72_base_commit": C72_BASE_COMMIT,
                "c72_result_schema": C72_RESULT_SCHEMA,
                "c72_paired_schema": C72_PAIRED_SCHEMA,
                "c72_admissible_normalization": C72_ADMISSIBLE_NORMALIZATION_VERSION,
                "c72_source_dataset_fingerprint": C75_C72_SOURCE_DATASET_FINGERPRINT,
                "c72_admissible_dataset_fingerprint": C75_C72_ADMISSIBLE_DATASET_FINGERPRINT,
                "c6_scientific_fingerprint": C6_SCIENTIFIC_FINGERPRINT,
                "c6_artifact_sha256": C6_ARTIFACT_SHA256,
                "c6_evidence_class": C6_EVIDENCE_CLASS,
                "c73_protocol_fingerprint": C75_C73_PROTOCOL_FINGERPRINT,
                "c73_execution_sha": C75_C73_EXECUTION_SHA,
                "c73_artifact_sha256": C75_C73_ARTIFACT_SHA256,
                "h5_decision": C75_H5_DECISION,
                "c74_protocol_fingerprint": C75_C74_PROTOCOL_FINGERPRINT,
                "c74_engine_fingerprint": C75_C74_ENGINE_FINGERPRINT,
                "c74_scientific_fingerprint": C75_C74_SCIENTIFIC_FINGERPRINT,
                "c74_artifact_sha256": C75_C74_ARTIFACT_SHA256,
                "h6_decision": C75_H6_DECISION,
            },
            "workload_realization": {
                "workload_class": WorkloadClass.TRACE_AUGMENTED.value,
                "source_selection_schema": C75_SOURCE_SELECTION_SCHEMA,
                "source_selection_salt": C75_SOURCE_SELECTION_SALT,
                "selection_pool": "C7-admissible Mooncake rows with input_tokens divisible by 4; no source-field mutation",
                "selection_order": "ascending SHA256(source_selection_salt|record_id), then record_id",
                "seed_mapping": "seed 0..63 maps to same-index first 64 unique ordered records",
                "quarter_step_divisor": C75_QUARTER_STEP_DIVISOR,
                "resource_realization_schema": C75_RESOURCE_REALIZATION_SCHEMA,
                "resource_salt": C75_RESOURCE_SALT,
                "worker_capacity": C75_WORKER_CAPACITY,
                "queue_levels": C75_QUEUE_LEVELS,
                "independent_seed_domains": ["worker queue depth", "physical State location", "session preferred location"],
                "p1": "one selected source token pair per Program; root zero reuse; later operations use exact frozen reusable-prefix fraction",
                "p4": "one selected source token pair per Program replicated across synthetic fan-out branches; shared State has one seed-derived physical location; branch load facts are branch-specific",
                "p7": "one source-shaped stateless request with no cross-request State/session/Continuity locality hints",
            },
            "surfaces": [surface.to_dict() for surface in C75_SURFACES],
            "rankable_cell_counts": {
                "p1": len(p1_cells()),
                "p4": len(p4_cells()),
                "p7_negative_control": len(p7_control_cells()),
            },
            "representability_limits": {
                "p1_cache_capacity_ratio": C75Representability.NOT_REALIZED_BY_C72.value,
                "p1_arrival_intensity": C75Representability.NOT_REALIZED_BY_C72.value,
                "p7_arrival_intensity": C75Representability.NOT_REALIZED_BY_C72.value,
                "rule": "unrealized axes may be reported as limitations but may not enter ranking or support claims",
            },
            "h4": {
                "series": ExperimentSeries.P1_DEEP_REUSE.value,
                "primary_metric": MetricID.RECOMPUTATION_RATIO.value,
                "orientation": "B4 - baseline; negative favorable",
                "primary_comparators": [item.value for item in C75_H4_PRIMARY_COMPARATORS],
                "diagnostic_comparator": C75_H4_DIAGNOSTIC_COMPARATOR.value,
                "cell_support_is_per_comparator": True,
                "cell_support": "B4 and comparator semantically eligible; paired Program-cluster percentile-95 CI for RR(B4)-RR(baseline) entirely <0; RR identical across accepted hardware strata",
                "aggregate_estimator": "ratio-of-sums over Program token components",
                "minimum_connected_cells": C75_MIN_CONNECTED_SUPPORT_CELLS,
                "region": "orthogonal adjacency within one fixed-worker-count P1 surface for the same comparator",
                "hypothesis_support": "at least one primary comparator has a qualifying connected region; all B0/B1/B2 maps and no-benefit regions must be reported",
                "cache_aware_confluence_note": "C7.2 may make B1 and B4 coincide when the same compatible resident State locality is visible; a B1 tie is a valid no-benefit result, not grounds to weaken B1",
                "correlated_explanatory_metrics": [MetricID.STATE_REUSE_RATIO.value, MetricID.STATE_REUSE_TOKEN_RATIO.value, MetricID.COLD_CONTINUATION_RATE.value],
            },
            "h7": {
                "reference_policy": C75_H7_REFERENCE.value,
                "p1_metric": MetricID.PROGRAM_COMPLETION_TIME.value,
                "p4_metric": MetricID.FANOUT_COMPLETION_TIME.value,
                "relative_benefit": "(B0_time-B4_time)/B0_time; positive favorable",
                "bootstrap_estimator": "ratio of summed paired Program times within each resample",
                "trend_point_estimator": "median per-Program paired relative benefit",
                "eligible_statefulness_axes": {
                    "P1": ["session_depth", "reusable_prefix_fraction"],
                    "P4": ["fanout_width", "shared_prefix_fraction"],
                },
                "gradient": "axis-aligned contiguous run at fixed other-axis value (and fixed worker-count stratum for P1); >=3 ordered levels; each cell percentile-95 benefit CI entirely >0; median benefit nondecreasing with >=1 strict increase; same direction on both hardware strata",
                "minimum_ordered_levels": C75_H7_MIN_ORDERED_LEVELS,
                "hypothesis_support": "at least one qualifying P1 or P4 statefulness gradient plus passing P7 negative control",
                "multi_surface_confirmation": "qualifying gradients in both P1 and P4 are reported as stronger cross-dimension confirmation but are not required by H7",
                "interaction_rule": "non-qualifying and non-monotone axes remain reportable interactions; H7 does not require monotonicity for every variable",
            },
            "p7_negative_control": {
                "ranked_as_efficiency_support": False,
                "requirements": [
                    "B0-B4 State Reuse Ratio == 0",
                    "B0-B4 Recomputation Ratio == 0",
                    "B0-B4 modeled Program Completion Time exactly equal per control cell/hardware stratum",
                ],
                "routing_control_cost_model": "ZERO_UNEVIDENCED_COMPONENT",
                "physical_overhead_claim": False,
            },
            "statistics": {
                "seeds": list(C7_STOCHASTIC_SEEDS),
                "complete_seed_count_required": len(C7_STOCHASTIC_SEEDS),
                "convergence_prefixes_diagnostic_only": True,
                "bootstrap_resamples": C7_BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": C7_BOOTSTRAP_SEED,
                "bootstrap_interval": "percentile-95",
                "cluster_unit": "Program",
                "semantic_invalid_excluded": True,
            },
            "ablation_boundary": {
                "b0_b4_role": "information-contract decomposition; not pure single-mechanism causal ablation",
                "b4_vs_b3": "diagnostic only unless separately supported by closed targeted evidence",
                "closed_h5_h6_recomputed": False,
            },
            "gate_g2": {
                "A": "H7 supported and at least two of H4/H5/H6 supported or strengthened within their declared phase spaces",
                "C": "none of H4/H5/H6/H7 has a qualifying meaningful region",
                "B": "every other outcome with at least one qualifying meaningful region",
                "evaluation_order": [C75G2Decision.A.value, C75G2Decision.C.value, C75G2Decision.B.value],
            },
            "invalid_result_conditions": [
                "source record mutated, clipped, truncated, or rounded",
                "seed source selection missing, duplicated, or outside declared arithmetic pool",
                "policy information-contract leak",
                "semantic-invalid observation counted as support",
                "accepted C6 hardware stratum missing",
                "timing benefit denominator zero or non-finite",
                "NOT_REALIZED_BY_C72 axis used as ranked evidence",
                "P7 interpreted as physical software-control overhead",
                "closed H5/H6 recomputed under C7.5 rules",
                "observed comparative result used to revise this protocol in place",
            ],
        }

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())


FROZEN_C75_PROTOCOL = C75Protocol()
C75_PROTOCOL_FINGERPRINT = FROZEN_C75_PROTOCOL.fingerprint


def main() -> None:
    print(_canonical_json({"protocol": FROZEN_C75_PROTOCOL.to_dict(), "protocol_fingerprint": C75_PROTOCOL_FINGERPRINT}))


if __name__ == "__main__":
    main()
