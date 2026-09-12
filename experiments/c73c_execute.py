from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from experiments.c7_protocol import (
    C7_BOOTSTRAP_RESAMPLES,
    C7_BOOTSTRAP_SEED,
    C7_CONVERGENCE_PREFIXES,
    C7_PROTOCOL_FINGERPRINT,
    C7_STOCHASTIC_SEEDS,
    ExperimentSeries,
)
from experiments.c73_retention_engine import (
    C73B_ENGINE_SCHEMA,
    RetentionPolicyResult,
    run_retention_program,
)
from experiments.c73_retention_protocol import (
    C73_PRIMARY_TTL_SECONDS,
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    CapacityOutcome,
    RetentionPolicyID,
)
from experiments.c73c_protocol import (
    C73C_H5_NOT_SUPPORTED,
    C73C_INFEASIBLE_LABEL,
    C73C_INSUFFICIENT_DENOMINATOR_LABEL,
    C73C_PRIMARY_SURFACES,
    C73C_PROTOCOL_FINGERPRINT,
    C73CPairedEvaluationProtocol,
    C73CPrimaryCell,
    C73CSurfaceID,
    RatioComponents,
    aggregate_ratio,
    bootstrap_resample_indices,
    build_base_manifest,
    build_primary_program_case,
    metric_components,
    percentile_95_interval,
    primary_cells_adjacent,
    primary_surface_cells,
    retention_manifest_variants,
    tool_return_ttft_projection,
)
from simulator.inference_cost_runtime import load_c64f_runtime_profiles


C73C2_RESULT_SCHEMA = "cadi.c7.3c2.paired-retention-result.v1"
C73C2_BASE_COMMIT = "e8eb46b482c89c9bd32d820c1ef5cbecbff15d66"
C73C2_FROZEN_PROTOCOL_FINGERPRINT = (
    "694f3c4e24ecca61b5c265e26f8417eafbcd4c932af3f9e60813fbb76d1b6800"
)
C73C2_CANONICAL_RESIDENCY_HARDWARE = "a100-80gb"
C73C2_HARDWARE_STRATA = ("a100-80gb", "h100-80gb")
C73C2_PRIMARY_BASELINES = (
    "LRU",
    "FIXED_TTL(5s)",
    "SESSION_PINNING",
)
C73C2_RATIO_METRICS = ("USR", "WSR", "RR", "CCR")
C73C2_SUPPORT_METRICS = ("USR", "RR", "P2_TOOL_RETURN_TTFT", "CCR")
C73C2_SUPPORTED = "SUPPORTED_WITHIN_DECLARED_PHASE_SPACE"
C73C2_COMPARABLE = "COMPARABLE"
C73C2_TTFT_DESCRIPTIVE_ONLY = "DESCRIPTIVE_ONLY_INSUFFICIENT_RETURNING_PROGRAMS"
C73C2_SEMANTIC_INVALID = "SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING"
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

if C73C_PROTOCOL_FINGERPRINT != C73C2_FROZEN_PROTOCOL_FINGERPRINT:
    raise RuntimeError("C7.3c2 frozen protocol fingerprint drift")
if C7_STOCHASTIC_SEEDS != tuple(range(64)):
    raise RuntimeError("C7.3c2 requires exactly seeds 0..63")
if C7_CONVERGENCE_PREFIXES != (8, 16, 32, 64):
    raise RuntimeError("C7.3c2 convergence-prefix drift")
if C7_BOOTSTRAP_RESAMPLES != 10_000 or C7_BOOTSTRAP_SEED != 20260911:
    raise RuntimeError("C7.3c2 bootstrap contract drift")
if C73_PRIMARY_TTL_SECONDS != 5.0:
    raise RuntimeError("C7.3c2 ordinary fixed-TTL baseline drift")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fp(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _execution_sha(value: str) -> str:
    if not isinstance(value, str) or _SHA40_RE.fullmatch(value) is None:
        raise ValueError("execution_git_sha must be a lowercase 40-hex Git commit")
    return value


def policy_key(policy_id: RetentionPolicyID, ttl_seconds: float | None = None) -> str:
    if not isinstance(policy_id, RetentionPolicyID):
        raise TypeError("policy_id must be RetentionPolicyID")
    if policy_id is RetentionPolicyID.FIXED_TTL:
        if ttl_seconds is None:
            raise ValueError("FIXED_TTL requires ttl_seconds")
        return f"FIXED_TTL({float(ttl_seconds):g}s)"
    if ttl_seconds is not None:
        raise ValueError("ttl_seconds is valid only for FIXED_TTL")
    return policy_id.value


@dataclass(frozen=True, slots=True)
class LogicalCell:
    cell_id: str
    series: ExperimentSeries
    parameters: tuple[tuple[str, int | float], ...]
    representative: C73CPrimaryCell
    memberships: tuple[C73CPrimaryCell, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "series": self.series.value,
            "parameters": dict(self.parameters),
            "surface_memberships": [
                {
                    "surface_id": cell.surface_id.value,
                    "x_value": cell.x_value,
                    "y_value": cell.y_value,
                }
                for cell in self.memberships
            ],
        }


def logical_primary_cells() -> tuple[LogicalCell, ...]:
    grouped: dict[
        tuple[str, tuple[tuple[str, int | float], ...]],
        list[C73CPrimaryCell],
    ] = {}
    order: list[tuple[str, tuple[tuple[str, int | float], ...]]] = []
    for surface in C73C_PRIMARY_SURFACES:
        for cell in primary_surface_cells(surface.surface_id):
            key = cell.logical_cell_key
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(cell)

    result: list[LogicalCell] = []
    for key in order:
        members = tuple(grouped[key])
        representative = members[0]
        payload = [key[0], list(key[1])]
        result.append(
            LogicalCell(
                cell_id=f"{key[0]}:{_fp(payload)[:16]}",
                series=representative.series,
                parameters=tuple(sorted(representative.parameters.items())),
                representative=representative,
                memberships=members,
            )
        )
    return tuple(result)


def _result_invariance_tuple(result: RetentionPolicyResult) -> tuple[Any, ...]:
    return (
        result.policy_id.value,
        result.capacity_outcome.value,
        result.eligible_reuse_opportunities,
        result.consumed_reuse_opportunities,
        result.semantic_rejection_count,
        result.useful_byte_seconds,
        result.wasted_byte_seconds,
        result.total_classified_byte_seconds,
        result.useful_residency_fraction,
        result.wasted_residency_fraction,
        result.max_resident_bytes,
        result.capacity_bytes,
    )


def _ratio_components_dict(
    result: RetentionPolicyResult,
) -> dict[str, RatioComponents] | None:
    """Return efficiency components only when semantic ranking is admissible."""
    if result.semantic_rejection_count:
        return None
    components = metric_components(result)
    return {
        "USR": components.useful_state_residency,
        "WSR": components.wasted_state_residency,
        "RR": components.recomputation_ratio,
        "CCR": components.cold_continuation_rate,
    }


def _ratio_value(items: Sequence[RatioComponents]) -> float | None:
    return aggregate_ratio(items).value


@lru_cache(maxsize=None)
def _bootstrap_indices_cached(sample_size: int, resample_index: int) -> tuple[int, ...]:
    """Cache only the already-frozen SHA-256 bootstrap schedule."""
    return bootstrap_resample_indices(sample_size, resample_index)


@lru_cache(maxsize=None)
def _paired_ratio_comparison_cached(
    b4: tuple[RatioComponents, ...],
    baseline: tuple[RatioComponents, ...],
) -> tuple[str, float | None, tuple[float, float] | None]:
    if len(b4) != len(baseline) or not b4:
        raise ValueError("paired ratio comparison requires equal non-empty Program sequences")
    b4_total = aggregate_ratio(b4)
    base_total = aggregate_ratio(baseline)
    if b4_total.value is None or base_total.value is None:
        return C73C_INSUFFICIENT_DENOMINATOR_LABEL, None, None

    diffs: list[float] = []
    n = len(b4)
    for resample_index in range(C7_BOOTSTRAP_RESAMPLES):
        indices = _bootstrap_indices_cached(n, resample_index)
        rb4 = aggregate_ratio(tuple(b4[index] for index in indices))
        rbase = aggregate_ratio(tuple(baseline[index] for index in indices))
        if rb4.value is None or rbase.value is None:
            return (
                C73C_INSUFFICIENT_DENOMINATOR_LABEL,
                b4_total.value - base_total.value,
                None,
            )
        diffs.append(rb4.value - rbase.value)

    return (
        C73C2_COMPARABLE,
        b4_total.value - base_total.value,
        percentile_95_interval(diffs),
    )


def paired_ratio_comparison(
    b4: Sequence[RatioComponents],
    baseline: Sequence[RatioComponents],
) -> dict[str, Any]:
    status, point, interval = _paired_ratio_comparison_cached(tuple(b4), tuple(baseline))
    return {
        "status": status,
        "point_difference": point,
        "ci95": None if interval is None else list(interval),
        "favorable": False,
    }


@lru_cache(maxsize=None)
def _paired_mean_comparison_cached(
    b4: tuple[float, ...],
    baseline: tuple[float, ...],
    minimum_sample_size: int,
) -> tuple[str, int, float | None, tuple[float, float] | None, bool]:
    if len(b4) != len(baseline):
        raise ValueError("paired mean comparison requires equal Program sequences")
    if len(b4) < minimum_sample_size:
        point = None if not b4 else sum(b4) / len(b4) - sum(baseline) / len(baseline)
        return C73C2_TTFT_DESCRIPTIVE_ONLY, len(b4), point, None, False

    point = sum(b4) / len(b4) - sum(baseline) / len(baseline)
    diffs: list[float] = []
    n = len(b4)
    for resample_index in range(C7_BOOTSTRAP_RESAMPLES):
        indices = _bootstrap_indices_cached(n, resample_index)
        diffs.append(
            sum(b4[index] for index in indices) / n
            - sum(baseline[index] for index in indices) / n
        )
    lo, hi = percentile_95_interval(diffs)
    return C73C2_COMPARABLE, n, point, (lo, hi), hi < 0.0


def paired_mean_comparison(
    b4: Sequence[float],
    baseline: Sequence[float],
    *,
    minimum_sample_size: int,
) -> dict[str, Any]:
    status, sample_size, point, interval, favorable = _paired_mean_comparison_cached(
        tuple(b4), tuple(baseline), minimum_sample_size
    )
    return {
        "status": status,
        "sample_size": sample_size,
        "point_difference": point,
        "ci95": None if interval is None else list(interval),
        "favorable": favorable,
    }


def _apply_favorable_direction(metric: str, comparison: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(comparison)
    ci = result.get("ci95")
    if result.get("status") != C73C2_COMPARABLE or ci is None:
        result["favorable"] = False
        return result
    lo, hi = ci
    result["favorable"] = lo > 0.0 if metric == "USR" else hi < 0.0
    return result


def wsr_from_usr_comparison(usr: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the exact WSR comparison from WSR == 1 - USR."""
    result = {
        "status": usr["status"],
        "point_difference": None,
        "ci95": None,
        "favorable": False,
        "derived_from": "EXACT_USR_COMPLEMENT",
    }
    if usr["point_difference"] is not None:
        result["point_difference"] = -float(usr["point_difference"])
    if usr["ci95"] is not None:
        lo, hi = usr["ci95"]
        result["ci95"] = [-hi, -lo]
        if result["status"] == C73C2_COMPARABLE:
            result["favorable"] = result["ci95"][1] < 0.0
    return result


def ccr_from_rr_comparison(rr: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the exact RR comparison under the frozen 1024-token identity."""
    result = dict(rr)
    if rr.get("ci95") is not None:
        result["ci95"] = list(rr["ci95"])
    result["derived_from"] = "EXACT_RR_FIXED_1024_TOKEN_IDENTITY"
    return result


def _prefix_ratio_diagnostics(
    per_seed: Sequence[Mapping[str, RatioComponents]],
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    for prefix in C7_CONVERGENCE_PREFIXES:
        subset = per_seed[:prefix]
        diagnostics[str(prefix)] = {
            metric: _ratio_value(tuple(record[metric] for record in subset))
            for metric in C73C2_RATIO_METRICS
        }
    return diagnostics


def _cell_seed_execution(
    logical_cell: LogicalCell,
    *,
    seed: int,
    execution_git_sha: str,
    profiles: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    case = build_primary_program_case(logical_cell.representative, seed=seed)
    hardware_runs: dict[str, dict[str, Any]] = {}

    for hardware_id in C73C2_HARDWARE_STRATA:
        base = build_base_manifest(
            logical_cell.representative,
            seed=seed,
            hardware_id=hardware_id,
            execution_git_commit=execution_git_sha,
        )
        policy_runs: dict[str, Any] = {}
        for manifest in retention_manifest_variants(case=case, base_manifest=base):
            key = policy_key(manifest.retention_policy_id, manifest.ttl_seconds)
            if key in policy_runs:
                raise AssertionError("duplicate retention policy variant")
            result = run_retention_program(
                case=case,
                base_manifest=base,
                retention_manifest=manifest,
            )
            policy_runs[key] = {"manifest": manifest, "result": result}
        hardware_runs[hardware_id] = {"base": base, "policy_runs": policy_runs}

    canonical = hardware_runs[C73C2_CANONICAL_RESIDENCY_HARDWARE]
    alternate_hardware = next(
        hardware_id
        for hardware_id in C73C2_HARDWARE_STRATA
        if hardware_id != C73C2_CANONICAL_RESIDENCY_HARDWARE
    )
    alternate = hardware_runs[alternate_hardware]
    if set(canonical["policy_runs"]) != set(alternate["policy_runs"]):
        raise AssertionError("retention policy set differs across hardware strata")

    policy_payloads: dict[str, Any] = {}
    internal: dict[str, Any] = {}
    eligible_counts: set[int] = set()
    for key in sorted(canonical["policy_runs"]):
        canonical_run = canonical["policy_runs"][key]
        alternate_run = alternate["policy_runs"][key]
        result = canonical_run["result"]
        alt_result = alternate_run["result"]
        if _result_invariance_tuple(result) != _result_invariance_tuple(alt_result):
            raise AssertionError(
                "non-timing retention outcome differs across accepted hardware strata"
            )
        components = _ratio_components_dict(result)
        eligible_counts.add(result.eligible_reuse_opportunities)

        ttft_by_hardware: dict[str, float] = {}
        if (
            logical_cell.series is ExperimentSeries.P2_TOOL_GAP_RETENTION
            and result.eligible_reuse_opportunities > 0
        ):
            for hardware_id in C73C2_HARDWARE_STRATA:
                hw_result = hardware_runs[hardware_id]["policy_runs"][key]["result"]
                ttft_by_hardware[hardware_id] = tool_return_ttft_projection(
                    profile=profiles[hardware_id],
                    consumed_reuse=bool(hw_result.consumed_reuse_opportunities),
                )

        policy_payloads[key] = {
            "retention_manifest_fingerprints": {
                hardware_id: hardware_runs[hardware_id]["policy_runs"][key][
                    "manifest"
                ].fingerprint
                for hardware_id in C73C2_HARDWARE_STRATA
            },
            "retention_policy_result_fingerprints": {
                hardware_id: hardware_runs[hardware_id]["policy_runs"][key][
                    "result"
                ].fingerprint
                for hardware_id in C73C2_HARDWARE_STRATA
            },
            "capacity_outcome": result.capacity_outcome.value,
            "semantic_rejection_count": result.semantic_rejection_count,
            "efficiency_ranking_eligibility": (
                "ELIGIBLE" if components is not None else C73C2_SEMANTIC_INVALID
            ),
            "eligible_reuse_opportunities": result.eligible_reuse_opportunities,
            "consumed_reuse_opportunities": result.consumed_reuse_opportunities,
            "metric_components": (
                None
                if components is None
                else {
                    metric: components[metric].to_dict()
                    for metric in C73C2_RATIO_METRICS
                }
            ),
            "tool_return_ttft_seconds": ttft_by_hardware,
        }
        internal[key] = {
            "result": result,
            "components": components,
            "ttft": ttft_by_hardware,
        }

    if len(eligible_counts) != 1:
        raise AssertionError("eligible reuse opportunity denominator must be policy-independent")

    seed_record = {
        "seed": seed,
        "program_case_fingerprint": case.fingerprint,
        "base_c7_manifest_fingerprints": {
            hardware_id: hardware_runs[hardware_id]["base"].fingerprint
            for hardware_id in C73C2_HARDWARE_STRATA
        },
        "hardware_invariance_pass": True,
        "policy_records": policy_payloads,
    }
    return seed_record, internal


def _invalid_metric_comparisons(status: str) -> dict[str, Any]:
    return {
        metric: {
            "status": status,
            "point_difference": None,
            "ci95": None,
            "favorable": False,
        }
        for metric in C73C2_RATIO_METRICS
    }


def _cell_summary(
    logical_cell: LogicalCell,
    *,
    execution_git_sha: str,
    profiles: Mapping[str, Any],
) -> dict[str, Any]:
    seed_records: list[dict[str, Any]] = []
    internal_by_seed: list[dict[str, Any]] = []
    for seed in C7_STOCHASTIC_SEEDS:
        public, internal = _cell_seed_execution(
            logical_cell,
            seed=seed,
            execution_git_sha=execution_git_sha,
            profiles=profiles,
        )
        seed_records.append(public)
        internal_by_seed.append(internal)

    policy_keys = tuple(sorted(internal_by_seed[0]))
    if not all(set(record) == set(policy_keys) for record in internal_by_seed):
        raise AssertionError("policy set drift across seeds")
    if "LIFECYCLE_B4" not in policy_keys:
        raise AssertionError("LIFECYCLE_B4 result missing")

    policy_metrics: dict[str, Any] = {}
    prefix_diagnostics: dict[str, Any] = {}
    for key in policy_keys:
        results = [record[key]["result"] for record in internal_by_seed]
        components_by_seed = [record[key]["components"] for record in internal_by_seed]
        semantically_valid = all(components is not None for components in components_by_seed)
        if semantically_valid:
            typed_components = [
                components for components in components_by_seed if components is not None
            ]
            ratio_values = {
                metric: _ratio_value(
                    tuple(components[metric] for components in typed_components)
                )
                for metric in C73C2_RATIO_METRICS
            }
            prefix_diagnostics[key] = _prefix_ratio_diagnostics(typed_components)
        else:
            ratio_values = {metric: None for metric in C73C2_RATIO_METRICS}
            prefix_diagnostics[key] = {
                "status": C73C2_SEMANTIC_INVALID,
                "prefixes": None,
            }
        policy_metrics[key] = {
            "capacity_infeasible_rate": sum(
                result.capacity_outcome is CapacityOutcome.CAPACITY_INFEASIBLE
                for result in results
            )
            / len(results),
            "semantic_rejection_count": sum(
                result.semantic_rejection_count for result in results
            ),
            "efficiency_ranking_eligibility": (
                "ELIGIBLE" if semantically_valid else C73C2_SEMANTIC_INVALID
            ),
            **ratio_values,
        }

    comparisons: dict[str, Any] = {}
    b4_records = [record["LIFECYCLE_B4"] for record in internal_by_seed]
    for baseline in policy_keys:
        if baseline == "LIFECYCLE_B4":
            continue
        baseline_records = [record[baseline] for record in internal_by_seed]
        all_capacity_eligible = all(
            b4["result"].capacity_outcome is CapacityOutcome.ELIGIBLE
            and base["result"].capacity_outcome is CapacityOutcome.ELIGIBLE
            for b4, base in zip(b4_records, baseline_records)
        )
        no_semantic_violation = all(
            b4["result"].semantic_rejection_count == 0
            and base["result"].semantic_rejection_count == 0
            for b4, base in zip(b4_records, baseline_records)
        )

        if not all_capacity_eligible:
            metric_comparisons = _invalid_metric_comparisons(C73C_INFEASIBLE_LABEL)
        elif not no_semantic_violation:
            metric_comparisons = _invalid_metric_comparisons(C73C2_SEMANTIC_INVALID)
        else:
            b4_components = [record["components"] for record in b4_records]
            base_components = [record["components"] for record in baseline_records]
            if any(item is None for item in b4_components + base_components):
                raise AssertionError("semantic eligibility and metric components disagree")
            b4_typed = [item for item in b4_components if item is not None]
            base_typed = [item for item in base_components if item is not None]

            usr = _apply_favorable_direction(
                "USR",
                paired_ratio_comparison(
                    tuple(record["USR"] for record in b4_typed),
                    tuple(record["USR"] for record in base_typed),
                ),
            )
            rr = _apply_favorable_direction(
                "RR",
                paired_ratio_comparison(
                    tuple(record["RR"] for record in b4_typed),
                    tuple(record["RR"] for record in base_typed),
                ),
            )
            metric_comparisons = {
                "USR": usr,
                "WSR": wsr_from_usr_comparison(usr),
                "RR": rr,
                "CCR": ccr_from_rr_comparison(rr),
            }

        comparison_payload: dict[str, Any] = {
            "ratio_metrics": metric_comparisons,
            "all_64_capacity_eligible_for_pair": all_capacity_eligible,
            "no_semantic_violation": no_semantic_violation,
        }

        if logical_cell.series is ExperimentSeries.P2_TOOL_GAP_RETENTION:
            returning_indices = tuple(
                index
                for index, record in enumerate(b4_records)
                if record["result"].eligible_reuse_opportunities > 0
            )
            if any(
                (record["result"].eligible_reuse_opportunities > 0)
                != (index in returning_indices)
                for index, record in enumerate(baseline_records)
            ):
                raise AssertionError("P2 returning Program subset is policy-dependent")
            ttft_comparisons: dict[str, Any] = {}
            for hardware_id in C73C2_HARDWARE_STRATA:
                if not all_capacity_eligible or not no_semantic_violation:
                    ttft_comparisons[hardware_id] = {
                        "status": (
                            C73C_INFEASIBLE_LABEL
                            if not all_capacity_eligible
                            else C73C2_SEMANTIC_INVALID
                        ),
                        "sample_size": len(returning_indices),
                        "point_difference": None,
                        "ci95": None,
                        "favorable": False,
                    }
                    continue
                b4_ttft = tuple(
                    b4_records[index]["ttft"][hardware_id]
                    for index in returning_indices
                )
                base_ttft = tuple(
                    baseline_records[index]["ttft"][hardware_id]
                    for index in returning_indices
                )
                ttft_comparisons[hardware_id] = paired_mean_comparison(
                    b4_ttft,
                    base_ttft,
                    minimum_sample_size=C7_CONVERGENCE_PREFIXES[0],
                )
            comparison_payload["P2_TOOL_RETURN_TTFT"] = {
                "returning_program_count": len(returning_indices),
                "hardware": ttft_comparisons,
            }

        comparisons[baseline] = comparison_payload

    h5_support: dict[str, bool] = {}
    for metric in ("USR", "RR", "CCR"):
        h5_support[metric] = all(
            comparisons[baseline]["ratio_metrics"][metric]["status"] == C73C2_COMPARABLE
            and comparisons[baseline]["ratio_metrics"][metric]["favorable"]
            for baseline in C73C2_PRIMARY_BASELINES
        )

    if logical_cell.series is ExperimentSeries.P2_TOOL_GAP_RETENTION:
        h5_support["P2_TOOL_RETURN_TTFT"] = all(
            comparisons[baseline]["P2_TOOL_RETURN_TTFT"]["hardware"][hardware_id][
                "status"
            ]
            == C73C2_COMPARABLE
            and comparisons[baseline]["P2_TOOL_RETURN_TTFT"]["hardware"][hardware_id][
                "favorable"
            ]
            for baseline in C73C2_PRIMARY_BASELINES
            for hardware_id in C73C2_HARDWARE_STRATA
        )
    else:
        h5_support["P2_TOOL_RETURN_TTFT"] = False

    return {
        **logical_cell.to_dict(),
        "seed_records": seed_records,
        "policy_metrics_seed64": policy_metrics,
        "convergence_prefix_ratio_diagnostics": prefix_diagnostics,
        "comparisons_vs_lifecycle_b4": comparisons,
        "h5_support_cell": h5_support,
        "hardware_invariance_pass": True,
    }


def support_components_for_surface(
    surface_id: C73CSurfaceID,
    metric: str,
    summaries_by_cell_id: Mapping[str, Mapping[str, Any]],
    logical_cells: Sequence[LogicalCell],
) -> list[list[dict[str, Any]]]:
    member_to_logical: dict[tuple[Any, ...], str] = {}
    logical_by_id = {cell.cell_id: cell for cell in logical_cells}
    for logical in logical_cells:
        for member in logical.memberships:
            member_to_logical[(member.surface_id, member.x_value, member.y_value)] = (
                logical.cell_id
            )

    surface_cells = tuple(primary_surface_cells(surface_id))
    supporting = {
        (cell.surface_id, cell.x_value, cell.y_value)
        for cell in surface_cells
        if summaries_by_cell_id[
            member_to_logical[(cell.surface_id, cell.x_value, cell.y_value)]
        ]["h5_support_cell"][metric]
    }
    visited: set[tuple[Any, ...]] = set()
    components: list[list[dict[str, Any]]] = []

    for cell in surface_cells:
        key = (cell.surface_id, cell.x_value, cell.y_value)
        if key not in supporting or key in visited:
            continue
        queue = deque([cell])
        visited.add(key)
        component: list[C73CPrimaryCell] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for candidate in surface_cells:
                candidate_key = (
                    candidate.surface_id,
                    candidate.x_value,
                    candidate.y_value,
                )
                if candidate_key in visited or candidate_key not in supporting:
                    continue
                if primary_cells_adjacent(current, candidate):
                    visited.add(candidate_key)
                    queue.append(candidate)
        if len(component) >= 2:
            components.append(
                [
                    {
                        "cell_id": member_to_logical[
                            (member.surface_id, member.x_value, member.y_value)
                        ],
                        "surface_id": member.surface_id.value,
                        "x_value": member.x_value,
                        "y_value": member.y_value,
                        "parameters": dict(
                            logical_by_id[
                                member_to_logical[
                                    (member.surface_id, member.x_value, member.y_value)
                                ]
                            ].parameters
                        ),
                    }
                    for member in component
                ]
            )
    return components


def execute_c73c2(*, execution_git_sha: str) -> dict[str, Any]:
    execution_git_sha = _execution_sha(execution_git_sha)
    protocol = C73CPairedEvaluationProtocol()
    if protocol.fingerprint != C73C2_FROZEN_PROTOCOL_FINGERPRINT:
        raise RuntimeError("loaded C7.3c protocol is not the frozen execution target")

    profiles = load_c64f_runtime_profiles()
    if tuple(sorted(profiles)) != tuple(sorted(C73C2_HARDWARE_STRATA)):
        raise RuntimeError("accepted C6 hardware family drift")

    logical_cells = logical_primary_cells()
    summaries = tuple(
        _cell_summary(
            logical_cell,
            execution_git_sha=execution_git_sha,
            profiles=profiles,
        )
        for logical_cell in logical_cells
    )
    summaries_by_id = {summary["cell_id"]: summary for summary in summaries}

    supporting_regions: list[dict[str, Any]] = []
    for surface in C73C_PRIMARY_SURFACES:
        for metric in C73C2_SUPPORT_METRICS:
            if (
                metric == "P2_TOOL_RETURN_TTFT"
                and surface.series is not ExperimentSeries.P2_TOOL_GAP_RETENTION
            ):
                continue
            components = support_components_for_surface(
                surface.surface_id,
                metric,
                summaries_by_id,
                logical_cells,
            )
            for component in components:
                supporting_regions.append(
                    {
                        "surface_id": surface.surface_id.value,
                        "metric": metric,
                        "cells": component,
                    }
                )

    h5_decision = C73C2_SUPPORTED if supporting_regions else C73C_H5_NOT_SUPPORTED
    return {
        "schema": C73C2_RESULT_SCHEMA,
        "base_commit": C73C2_BASE_COMMIT,
        "execution_git_sha": execution_git_sha,
        "frozen_identities": {
            "c7_1_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
            "c7_3a_retention_protocol_fingerprint": C73_RETENTION_PROTOCOL_FINGERPRINT,
            "c7_3b_engine_schema": C73B_ENGINE_SCHEMA,
            "c7_3b_merge_sha": "451cafddb21d667f6abb73f48477560c850cda20",
            "c7_3c_protocol_fingerprint": C73C2_FROZEN_PROTOCOL_FINGERPRINT,
        },
        "evidence_boundary": {
            "workload_structure": "P-SRC4",
            "timing_cost_evidence": "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2",
            "direct_hardware_measurement_claim": False,
            "synthetic_prevalence_claim": False,
        },
        "execution_contract": {
            "unique_logical_cell_count": len(logical_cells),
            "surface_membership_count": sum(len(cell.memberships) for cell in logical_cells),
            "seed_count": len(C7_STOCHASTIC_SEEDS),
            "seeds": list(C7_STOCHASTIC_SEEDS),
            "hardware_strata": list(C73C2_HARDWARE_STRATA),
            "canonical_residency_hardware": C73C2_CANONICAL_RESIDENCY_HARDWARE,
            "primary_baselines": list(C73C2_PRIMARY_BASELINES),
            "bootstrap_resamples": C7_BOOTSTRAP_RESAMPLES,
            "bootstrap_seed": C7_BOOTSTRAP_SEED,
            "convergence_prefixes": list(C7_CONVERGENCE_PREFIXES),
            "adaptive_early_stop": False,
            "bootstrap_index_schedule_cached_without_statistical_change": True,
            "wsr_bootstrap_derived_from_exact_usr_complement": True,
            "ccr_bootstrap_derived_from_exact_rr_fixed_token_identity": True,
            "rr_ccr_independent_corroboration_claim": False,
        },
        "cells": list(summaries),
        "h5": {
            "decision": h5_decision,
            "supporting_regions": supporting_regions,
            "support_metric_set": list(C73C2_SUPPORT_METRICS),
            "meaningful_range_minimum_adjacent_cells": 2,
        },
    }


def build_artifact(*, execution_git_sha: str) -> dict[str, Any]:
    result = execute_c73c2(execution_git_sha=execution_git_sha)
    return {"result": result, "result_fingerprint": _fp(result)}


def _write_artifact(payload: Mapping[str, Any], output: str | Path | None) -> None:
    text = _json(payload) + "\n"
    if output is None:
        print(text, end="")
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Execute frozen C7.3c2 paired retention evaluation"
    )
    parser.add_argument("--execution-git-sha", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    payload = build_artifact(execution_git_sha=args.execution_git_sha)
    _write_artifact(payload, args.output)


if __name__ == "__main__":
    main()
