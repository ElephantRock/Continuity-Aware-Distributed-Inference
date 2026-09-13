from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.c7_protocol import (
    C6_ARTIFACT_SHA256,
    C6_SCIENTIFIC_FINGERPRINT,
    C7_PROTOCOL_FINGERPRINT,
)
from experiments.c74_migration_engine import (
    C74B_BASE_COMMIT,
    C74B_COMPARATIVE_RESULT_INSPECTION,
    build_crossover_manifest,
    build_failover_manifests,
    engine_identity,
    evaluate_crossover_cell,
    run_failover_cell,
)
from experiments.c74_migration_protocol import (
    C74_C44_BINDING_SAFETY_MERGE,
    C74_EFFICIENCY_NOT_STRENGTHENED,
    C74_EFFICIENCY_STRENGTHENED,
    C74_PARTIAL_UNRANKED_LABEL,
    C74_PROTOCOL_FINGERPRINT,
    C74_RANKABLE_SCENARIOS,
    C74_UNRANKED_SCENARIOS,
    C74EfficiencyDecision,
    C74ScenarioID,
    C74SupportMode,
    p5_cells,
    p5_cells_adjacent,
)
from simulator.policies import PolicyID


C74C_RESULT_SCHEMA = "cadi.c7.4c.migration-efficiency-result.v1"
C74C_BASE_COMMIT = "9227e36883af785aef1dc7d253404cee0e8675d1"
C74C_FROZEN_C74B_ENGINE_FINGERPRINT = (
    "2083e1a227330ea1a0ac57caf166cf8777865cbe6e3c6d259796cfd56fe02c32"
)
C74C_COMPARATIVE_RESULT_INSPECTION = "EXECUTED_FROZEN_C7.4C"
C74C_HARDWARE_ORDER = ("a100-80gb", "h100-80gb")
C74C_POLICY_ORDER = tuple(PolicyID)
C74C_EXPECTED_TRACK_A_ROWS = 40
C74C_EXPECTED_TRACK_B_ROWS = 1200
C74C_EXPECTED_RANKABLE_TRACK_B_ROWS = 1000
C74C_EXPECTED_UNRANKED_TRACK_B_ROWS = 200
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


if C74B_BASE_COMMIT != "de4046fc4bcbf343d372cf8a151cdbb96fc6b45b":
    raise RuntimeError("C7.4c C7.4b parent-base drift")
if C74B_COMPARATIVE_RESULT_INSPECTION != "NONE":
    raise RuntimeError("C7.4b mechanics boundary drift")
if engine_identity()["engine_fingerprint"] != C74C_FROZEN_C74B_ENGINE_FINGERPRINT:
    raise RuntimeError("C7.4c requires the exact reviewed C7.4b engine fingerprint")
if C74_PROTOCOL_FINGERPRINT != "21601257767442204f1acfae4c6e2e42959e37701c9236a2d2b560a2dda486b6":
    raise RuntimeError("C7.4c C7.4a protocol drift")
if C7_PROTOCOL_FINGERPRINT != "706e0d5fff362a1eda8c906b957c914251c6e7949bac6be3c2c143ae21625474":
    raise RuntimeError("C7.4c C7.1 protocol drift")
if C6_SCIENTIFIC_FINGERPRINT != "cc1b62c4e38a1612720f601375c425c0bd7d67b4eff11b101bfdf87b79a7a61a":
    raise RuntimeError("C7.4c C6 scientific fingerprint drift")
if C6_ARTIFACT_SHA256 != "93990386135ecbf6fc38e579841eace3431a068b10ee59209fe6ad7084bd8889":
    raise RuntimeError("C7.4c C6 artifact drift")
if C74_C44_BINDING_SAFETY_MERGE != "0d93e9147a7e184b33588e49b63e7a8c2b39beca":
    raise RuntimeError("C7.4c C4.4 correctness dependency drift")
if len(p5_cells()) != 20:
    raise RuntimeError("C7.4c requires the exact frozen 20-cell P5 grid")
if C74_UNRANKED_SCENARIOS != (C74ScenarioID.PARTIAL_MATERIALIZATION_NO_COMMIT,):
    raise RuntimeError("C7.4c unranked-scenario boundary drift")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fp(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _execution_sha(value: str) -> str:
    if not isinstance(value, str) or _SHA40_RE.fullmatch(value) is None:
        raise ValueError("execution_git_commit must be a lowercase 40-hex Git commit")
    return value


def _cell_key(cell: tuple[int, int]) -> tuple[int, int]:
    ordered = p5_cells()
    return (ordered.index(cell), 0)


def _sorted_cells(cells: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    order = {cell: index for index, cell in enumerate(p5_cells())}
    return tuple(sorted(set(cells), key=lambda cell: order[cell]))


def _connected_components(
    cells: Iterable[tuple[int, int]],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    remaining = set(cells)
    order = {cell: index for index, cell in enumerate(p5_cells())}
    components: list[tuple[tuple[int, int], ...]] = []
    while remaining:
        start = min(remaining, key=lambda cell: order[cell])
        remaining.remove(start)
        component = {start}
        frontier = [start]
        while frontier:
            current = frontier.pop()
            adjacent = [
                candidate
                for candidate in tuple(remaining)
                if p5_cells_adjacent(current, candidate)
            ]
            for candidate in sorted(adjacent, key=lambda cell: order[cell]):
                remaining.remove(candidate)
                component.add(candidate)
                frontier.append(candidate)
        components.append(tuple(sorted(component, key=lambda cell: order[cell])))
    return tuple(sorted(components, key=lambda item: order[item[0]]))


def _track_a_rows(execution_git_commit: str) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for hardware_id in C74C_HARDWARE_ORDER:
        for state_tokens, recompute_tokens in p5_cells():
            manifest = build_crossover_manifest(
                execution_git_commit=execution_git_commit,
                hardware_id=hardware_id,
                state_tokens=state_tokens,
                recompute_tokens=recompute_tokens,
            )
            result = evaluate_crossover_cell(manifest)
            rows.append(
                {
                    "track": "P5_CROSSOVER",
                    "manifest_fingerprint": manifest.fingerprint,
                    "result_fingerprint": result.fingerprint,
                    "result": result.to_dict(),
                }
            )
    if len(rows) != C74C_EXPECTED_TRACK_A_ROWS:
        raise RuntimeError("C7.4c Track-A row cardinality drift")
    return tuple(rows)


def _track_b_rows(
    execution_git_commit: str,
) -> tuple[tuple[dict[str, Any], ...], Mapping[tuple[str, str, int, int, str], Any]]:
    rows: list[dict[str, Any]] = []
    lookup: dict[tuple[str, str, int, int, str], Any] = {}
    scenario_order = tuple(C74_RANKABLE_SCENARIOS) + tuple(C74_UNRANKED_SCENARIOS)
    for scenario_id in scenario_order:
        for policy_id in C74C_POLICY_ORDER:
            for hardware_id in C74C_HARDWARE_ORDER:
                for state_tokens, recompute_tokens in p5_cells():
                    base_manifest, failover_manifest = build_failover_manifests(
                        execution_git_commit=execution_git_commit,
                        scenario_id=scenario_id,
                        policy_id=policy_id,
                        hardware_id=hardware_id,
                        state_tokens=state_tokens,
                        recompute_tokens=recompute_tokens,
                    )
                    result = run_failover_cell(base_manifest, failover_manifest)
                    key = (
                        scenario_id.value,
                        hardware_id,
                        state_tokens,
                        recompute_tokens,
                        policy_id.value,
                    )
                    if key in lookup:
                        raise RuntimeError("duplicate C7.4c Track-B result key")
                    lookup[key] = result
                    rows.append(
                        {
                            "track": "FAILOVER_EFFICIENCY",
                            "base_manifest_fingerprint": base_manifest.fingerprint,
                            "failover_manifest_fingerprint": failover_manifest.fingerprint,
                            "result_fingerprint": result.fingerprint,
                            "result": result.to_dict(),
                        }
                    )
    if len(rows) != C74C_EXPECTED_TRACK_B_ROWS or len(lookup) != C74C_EXPECTED_TRACK_B_ROWS:
        raise RuntimeError("C7.4c Track-B row cardinality drift")
    return tuple(rows), lookup


def _policy_results_for_cell(
    lookup: Mapping[tuple[str, str, int, int, str], Any],
    *,
    scenario_id: C74ScenarioID,
    hardware_id: str,
    state_tokens: int,
    recompute_tokens: int,
) -> Mapping[PolicyID, Any]:
    return {
        policy_id: lookup[
            (
                scenario_id.value,
                hardware_id,
                state_tokens,
                recompute_tokens,
                policy_id.value,
            )
        ]
        for policy_id in C74C_POLICY_ORDER
    }


def _hardware_cell_support(
    policy_results: Mapping[PolicyID, Any],
) -> dict[str, bool]:
    b4 = policy_results[PolicyID.B4]
    baselines = tuple(policy_results[policy_id] for policy_id in C74C_POLICY_ORDER[:-1])
    all_semantically_valid = all(
        result.semantic_violation_count == 0 and result.efficiency_eligible
        for result in policy_results.values()
    )
    no_recovery_regression = all(
        b4.recovery_time_seconds <= baseline.recovery_time_seconds
        for baseline in baselines
    )
    recovery_strict = any(
        b4.recovery_time_seconds < baseline.recovery_time_seconds
        for baseline in baselines
    )
    volume_strict_vs_b3 = (
        b4.state_transfer_volume_bytes
        < policy_results[PolicyID.B3].state_transfer_volume_bytes
    )
    return {
        "semantic_valid": all_semantically_valid,
        "no_recovery_regression": no_recovery_regression,
        "recovery_time_support": (
            all_semantically_valid and no_recovery_regression and recovery_strict
        ),
        "transfer_volume_support": (
            all_semantically_valid and no_recovery_regression and volume_strict_vs_b3
        ),
    }


def _support_analysis(
    lookup: Mapping[tuple[str, str, int, int, str], Any],
) -> dict[str, Any]:
    support_regions: list[dict[str, Any]] = []
    scenario_summaries: list[dict[str, Any]] = []

    for scenario_id in C74_RANKABLE_SCENARIOS:
        per_hardware: dict[str, dict[tuple[int, int], dict[str, bool]]] = {}
        per_hardware_results: dict[str, dict[tuple[int, int], Mapping[PolicyID, Any]]] = {}
        for hardware_id in C74C_HARDWARE_ORDER:
            per_hardware[hardware_id] = {}
            per_hardware_results[hardware_id] = {}
            for state_tokens, recompute_tokens in p5_cells():
                cell = (state_tokens, recompute_tokens)
                results = _policy_results_for_cell(
                    lookup,
                    scenario_id=scenario_id,
                    hardware_id=hardware_id,
                    state_tokens=state_tokens,
                    recompute_tokens=recompute_tokens,
                )
                per_hardware_results[hardware_id][cell] = results
                per_hardware[hardware_id][cell] = _hardware_cell_support(results)

        recovery_intersection = _sorted_cells(
            cell
            for cell in p5_cells()
            if all(
                per_hardware[hardware_id][cell]["recovery_time_support"]
                for hardware_id in C74C_HARDWARE_ORDER
            )
        )

        volume_cells: list[tuple[int, int]] = []
        for cell in p5_cells():
            support_on_both = all(
                per_hardware[hardware_id][cell]["transfer_volume_support"]
                for hardware_id in C74C_HARDWARE_ORDER
            )
            if not support_on_both:
                continue
            a100 = per_hardware_results["a100-80gb"][cell]
            h100 = per_hardware_results["h100-80gb"][cell]
            hardware_invariant_volume = (
                a100[PolicyID.B4].state_transfer_volume_bytes
                == h100[PolicyID.B4].state_transfer_volume_bytes
                and a100[PolicyID.B3].state_transfer_volume_bytes
                == h100[PolicyID.B3].state_transfer_volume_bytes
            )
            if hardware_invariant_volume:
                volume_cells.append(cell)
        volume_intersection = _sorted_cells(volume_cells)

        recovery_components = _connected_components(recovery_intersection)
        volume_components = _connected_components(volume_intersection)
        qualifying_recovery = tuple(component for component in recovery_components if len(component) >= 2)
        qualifying_volume = tuple(component for component in volume_components if len(component) >= 2)

        for mode, components in (
            (C74SupportMode.RECOVERY_TIME, qualifying_recovery),
            (C74SupportMode.TRANSFER_VOLUME, qualifying_volume),
        ):
            for component in components:
                support_regions.append(
                    {
                        "scenario_id": scenario_id.value,
                        "support_mode": mode.value,
                        "cell_count": len(component),
                        "cells": [
                            {"state_tokens": state, "recompute_tokens": recompute}
                            for state, recompute in component
                        ],
                    }
                )

        scenario_summaries.append(
            {
                "scenario_id": scenario_id.value,
                "recovery_time_intersection_cell_count": len(recovery_intersection),
                "transfer_volume_intersection_cell_count": len(volume_intersection),
                "qualifying_recovery_region_count": len(qualifying_recovery),
                "qualifying_volume_region_count": len(qualifying_volume),
            }
        )

    decision = (
        C74_EFFICIENCY_STRENGTHENED
        if support_regions
        else C74_EFFICIENCY_NOT_STRENGTHENED
    )
    if decision not in {item.value for item in C74EfficiencyDecision}:
        raise RuntimeError("C7.4c H6 decision escaped frozen classification set")
    return {
        "h6_efficiency_classification": decision,
        "support_region_count": len(support_regions),
        "support_regions": support_regions,
        "scenario_summaries": scenario_summaries,
        "minimum_connected_cells": 2,
        "track_a_can_support": False,
        "partial_materialization_can_support": False,
        "correctness_component_reopened": False,
    }


def _track_a_summary(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for hardware_id in C74C_HARDWARE_ORDER:
        counts = Counter(
            row["result"]["crossover"]
            for row in rows
            if row["result"]["hardware_id"] == hardware_id
        )
        summaries.append(
            {
                "hardware_id": hardware_id,
                "row_count": sum(counts.values()),
                "classification_counts": {
                    key: counts.get(key, 0)
                    for key in ("TRANSFER_FASTER", "RECOMPUTE_FASTER", "TIE")
                },
            }
        )
    return summaries


def _track_b_summary(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for hardware_id in C74C_HARDWARE_ORDER:
        selected = [row["result"] for row in rows if row["result"]["hardware_id"] == hardware_id]
        status_counts = Counter(row["efficiency_status"] for row in selected)
        summaries.append(
            {
                "hardware_id": hardware_id,
                "row_count": len(selected),
                "semantic_violation_row_count": sum(
                    1 for row in selected if row["semantic_violation_count"] != 0
                ),
                "efficiency_status_counts": dict(sorted(status_counts.items())),
            }
        )
    return summaries


def generate_result(execution_git_commit: str) -> dict[str, Any]:
    execution_git_commit = _execution_sha(execution_git_commit)
    track_a_rows = _track_a_rows(execution_git_commit)
    track_b_rows, lookup = _track_b_rows(execution_git_commit)

    rankable_count = sum(
        1
        for row in track_b_rows
        if row["result"]["efficiency_status"] != C74_PARTIAL_UNRANKED_LABEL
    )
    partial_count = sum(
        1
        for row in track_b_rows
        if row["result"]["efficiency_status"] == C74_PARTIAL_UNRANKED_LABEL
    )
    if rankable_count != C74C_EXPECTED_RANKABLE_TRACK_B_ROWS:
        raise RuntimeError("C7.4c rankable Track-B row cardinality drift")
    if partial_count != C74C_EXPECTED_UNRANKED_TRACK_B_ROWS:
        raise RuntimeError("C7.4c partial-materialization row cardinality drift")

    support = _support_analysis(lookup)
    payload: dict[str, Any] = {
        "schema": C74C_RESULT_SCHEMA,
        "execution_git_commit": execution_git_commit,
        "base_commit": C74C_BASE_COMMIT,
        "identities": {
            "c7_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
            "c74a_protocol_fingerprint": C74_PROTOCOL_FINGERPRINT,
            "c74b_engine_fingerprint": C74C_FROZEN_C74B_ENGINE_FINGERPRINT,
            "c6_scientific_fingerprint": C6_SCIENTIFIC_FINGERPRINT,
            "c6_artifact_sha256": C6_ARTIFACT_SHA256,
            "c44_binding_safety_merge": C74_C44_BINDING_SAFETY_MERGE,
        },
        "comparative_result_inspection": C74C_COMPARATIVE_RESULT_INSPECTION,
        "evidence": {
            "timing": "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2",
            "scenario_structure": "P-SRC4",
            "direct_hardware_measurement_claim": False,
            "bootstrap_applied": False,
            "artificial_repeated_seeds": False,
        },
        "row_counts": {
            "track_a": len(track_a_rows),
            "track_b": len(track_b_rows),
            "track_b_rankable": rankable_count,
            "track_b_partial_unranked": partial_count,
        },
        "track_a_summary": _track_a_summary(track_a_rows),
        "track_b_summary": _track_b_summary(track_b_rows),
        "h6_efficiency": support,
        "track_a_rows": list(track_a_rows),
        "track_b_rows": list(track_b_rows),
    }
    return {**payload, "scientific_fingerprint": _fp(payload)}


def write_result(path: str | Path, execution_git_commit: str) -> dict[str, Any]:
    result = generate_result(execution_git_commit)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_json(result) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute frozen C7.4c migration/failover evaluation")
    parser.add_argument("--execution-git-commit", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = generate_result(args.execution_git_commit)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_json(result) + "\n", encoding="utf-8")
    else:
        print(_json(result))


if __name__ == "__main__":
    main()
