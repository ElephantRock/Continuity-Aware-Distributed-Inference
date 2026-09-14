from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from experiments.c7_protocol import C7_STOCHASTIC_SEEDS, EfficiencyEligibility, ExperimentSeries
from experiments.c75_g2_execute import (
    C75B_H4_NOT_SUPPORTED,
    C75B_H4_SUPPORTED,
    C75B_H7_NOT_SUPPORTED,
    C75B_H7_SUPPORTED,
    C75B_HARDWARE_STRATA,
    C75PairedComparison,
    C75ProgramRow,
    C75TimingCellEvidence,
    connected_support_components_p1,
    h7_gradient_runs,
    paired_ratio_difference,
    paired_timing_benefit,
    p7_negative_control_pass,
    recomputation_components,
    verify_non_timing_hardware_invariance,
)
from experiments.c75_g2_protocol import (
    C75_H4_PRIMARY_COMPARATORS,
    C75_H5_DECISION,
    C75_H6_DECISION,
    C75_MIN_CONNECTED_SUPPORT_CELLS,
    C75_P1_REUSE_FRACTIONS,
    C75_P1_SESSION_DEPTHS,
    C75_P4_FANOUT_WIDTHS,
    C75_P4_SHARED_FRACTIONS,
    C75_WORKER_COUNTS,
    C75G2Decision,
    adjudicate_g2,
)
from simulator.policies import PolicyID


C75B_ADJUDICATOR_SCHEMA = "cadi.c7.5b.g2-adjudicator.v1"
C75B_SEMANTIC_INVALID = "SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING"
C75B_COMPARABLE = "COMPARABLE"


def p1_cell_id(cell: tuple[int, float, int]) -> str:
    depth, fraction, workers = cell
    if depth not in C75_P1_SESSION_DEPTHS:
        raise ValueError("P1 depth escaped frozen axis")
    if fraction not in C75_P1_REUSE_FRACTIONS:
        raise ValueError("P1 reuse fraction escaped frozen axis")
    if workers not in C75_WORKER_COUNTS:
        raise ValueError("P1 worker count escaped frozen axis")
    return f"P1:d{depth}:r{fraction:g}:w{workers}"


def p4_cell_id(cell: tuple[int, float]) -> str:
    width, fraction = cell
    if width not in C75_P4_FANOUT_WIDTHS:
        raise ValueError("P4 fan-out width escaped frozen axis")
    if fraction not in C75_P4_SHARED_FRACTIONS:
        raise ValueError("P4 shared-prefix fraction escaped frozen axis")
    return f"P4:f{width}:r{fraction:g}"


def p7_cell_id(worker_count: int) -> str:
    if worker_count not in C75_WORKER_COUNTS:
        raise ValueError("P7 worker count escaped frozen axis")
    return f"P7:w{worker_count}"


def _expected_p1_cells() -> tuple[tuple[int, float, int], ...]:
    return tuple(
        (depth, fraction, workers)
        for workers in C75_WORKER_COUNTS
        for depth in C75_P1_SESSION_DEPTHS
        for fraction in C75_P1_REUSE_FRACTIONS
    )


def _expected_p4_cells() -> tuple[tuple[int, float], ...]:
    return tuple(
        (width, fraction)
        for width in C75_P4_FANOUT_WIDTHS
        for fraction in C75_P4_SHARED_FRACTIONS
    )


def validate_global_seed_source_mapping(rows: Sequence[C75ProgramRow]) -> dict[int, str]:
    if not rows:
        raise ValueError("full result rows must be non-empty")
    by_seed: dict[int, set[str]] = {seed: set() for seed in C7_STOCHASTIC_SEEDS}
    for row in rows:
        if not isinstance(row, C75ProgramRow):
            raise TypeError("rows must contain C75ProgramRow values")
        if row.seed not in by_seed:
            raise ValueError("row seed escaped frozen 0..63 schedule")
        by_seed[row.seed].add(row.source_record_id)
    if any(len(values) != 1 for values in by_seed.values()):
        raise ValueError("one frozen seed must map to exactly one source record across the full sweep")
    mapping = {seed: next(iter(by_seed[seed])) for seed in C7_STOCHASTIC_SEEDS}
    if len(set(mapping.values())) != len(C7_STOCHASTIC_SEEDS):
        raise ValueError("the frozen 64-seed source cohort must contain 64 unique source records")
    return mapping


def validate_cell_pairing(
    rows: Sequence[C75ProgramRow],
    *,
    series: ExperimentSeries,
    cell_id: str,
    required_policies: Sequence[PolicyID] = tuple(PolicyID),
) -> None:
    policies = tuple(required_policies)
    if not policies or len(set(policies)) != len(policies):
        raise ValueError("required_policies must be a non-empty unique sequence")
    selected = tuple(rows)
    if any(row.series is not series or row.cell_id != cell_id for row in selected):
        raise ValueError("cell pairing received a row from another series/cell")
    expected = len(C75B_HARDWARE_STRATA) * len(C7_STOCHASTIC_SEEDS) * len(policies)
    if len(selected) != expected:
        raise ValueError("cell pairing must contain exactly one row per hardware/seed/policy")
    keyed: dict[tuple[str, int, PolicyID], C75ProgramRow] = {}
    for row in selected:
        if row.hardware_id not in C75B_HARDWARE_STRATA:
            raise ValueError("row hardware escaped frozen strata")
        if row.seed not in C7_STOCHASTIC_SEEDS:
            raise ValueError("row seed escaped frozen schedule")
        if row.policy_id not in policies:
            raise ValueError("cell pairing contains an unexpected policy")
        key = (row.hardware_id, row.seed, row.policy_id)
        if key in keyed:
            raise ValueError("duplicate hardware/seed/policy row")
        keyed[key] = row
    for hardware_id in C75B_HARDWARE_STRATA:
        for seed in C7_STOCHASTIC_SEEDS:
            for policy_id in policies:
                if (hardware_id, seed, policy_id) not in keyed:
                    raise ValueError("missing hardware/seed/policy row")
            paired_fingerprints = {
                keyed[(hardware_id, seed, policy_id)].paired_result_fingerprint
                for policy_id in policies
            }
            if len(paired_fingerprints) != 1:
                raise ValueError("policy rows for one hardware/seed must originate from one paired result")
    for seed in C7_STOCHASTIC_SEEDS:
        identities = {
            (
                keyed[(hardware_id, seed, policy_id)].source_record_id,
                keyed[(hardware_id, seed, policy_id)].program_case_fingerprint,
                keyed[(hardware_id, seed, policy_id)].total_input_tokens,
            )
            for hardware_id in C75B_HARDWARE_STRATA
            for policy_id in policies
        }
        if len(identities) != 1:
            raise ValueError("paired rows do not bind to one source/Program case across policy and hardware")


def _policy_rows(
    rows: Sequence[C75ProgramRow], hardware_id: str, policy_id: PolicyID
) -> tuple[C75ProgramRow, ...]:
    selected = tuple(
        sorted(
            (
                row
                for row in rows
                if row.hardware_id == hardware_id and row.policy_id is policy_id
            ),
            key=lambda row: row.seed,
        )
    )
    if tuple(row.seed for row in selected) != C7_STOCHASTIC_SEEDS:
        raise ValueError("policy comparison rows must cover exactly seeds 0..63")
    return selected


def _ranking_eligible(rows: Iterable[C75ProgramRow]) -> bool:
    return all(
        row.semantic_violation_count == 0
        and row.efficiency_eligibility is EfficiencyEligibility.ELIGIBLE
        for row in rows
    )


@dataclass(frozen=True, slots=True)
class C75H4CellEvidence:
    cell: tuple[int, float, int]
    comparator: PolicyID
    status: str
    comparisons: tuple[tuple[str, C75PairedComparison], ...]
    supported: bool

    def __post_init__(self) -> None:
        p1_cell_id(self.cell)
        if self.comparator not in C75_H4_PRIMARY_COMPARATORS:
            raise ValueError("H4 comparator escaped frozen primary comparator set")
        if self.status not in {C75B_COMPARABLE, C75B_SEMANTIC_INVALID}:
            raise ValueError("invalid H4 cell status")
        if self.status == C75B_COMPARABLE:
            if tuple(name for name, _ in self.comparisons) != C75B_HARDWARE_STRATA:
                raise ValueError("comparable H4 evidence must contain both hardware strata")
        elif self.comparisons:
            raise ValueError("semantic-invalid H4 evidence cannot contain ranked comparisons")
        if self.supported and self.status != C75B_COMPARABLE:
            raise ValueError("semantic-invalid H4 cell cannot support H4")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": list(self.cell),
            "comparator": self.comparator.value,
            "status": self.status,
            "comparisons": {name: comparison.to_dict() for name, comparison in self.comparisons},
            "supported": self.supported,
        }


def evaluate_h4_cell(
    cell: tuple[int, float, int], rows: Sequence[C75ProgramRow], comparator: PolicyID
) -> C75H4CellEvidence:
    cell_id = p1_cell_id(cell)
    validate_cell_pairing(
        rows,
        series=ExperimentSeries.P1_DEEP_REUSE,
        cell_id=cell_id,
        required_policies=tuple(PolicyID),
    )
    ranked_rows = tuple(
        row for row in rows if row.policy_id in {comparator, PolicyID.B4}
    )
    if not _ranking_eligible(ranked_rows):
        return C75H4CellEvidence(cell, comparator, C75B_SEMANTIC_INVALID, (), False)
    verify_non_timing_hardware_invariance(rows, (comparator, PolicyID.B4))
    comparisons: list[tuple[str, C75PairedComparison]] = []
    for hardware_id in C75B_HARDWARE_STRATA:
        b4 = _policy_rows(rows, hardware_id, PolicyID.B4)
        baseline = _policy_rows(rows, hardware_id, comparator)
        comparison = paired_ratio_difference(
            tuple(recomputation_components(row) for row in b4),
            tuple(recomputation_components(row) for row in baseline),
        )
        comparisons.append((hardware_id, comparison))
    if comparisons[0][1] != comparisons[1][1]:
        raise ValueError("non-timing H4 comparison drifted across hardware strata")
    return C75H4CellEvidence(
        cell,
        comparator,
        C75B_COMPARABLE,
        tuple(comparisons),
        all(comparison.favorable for _, comparison in comparisons),
    )


@dataclass(frozen=True, slots=True)
class C75H4Adjudication:
    decision: str
    qualifying_regions: tuple[tuple[PolicyID, tuple[tuple[tuple[int, float, int], ...], ...]], ...]
    cells: tuple[C75H4CellEvidence, ...]

    @property
    def supported(self) -> bool:
        return self.decision == C75B_H4_SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "qualifying_regions": {
                policy.value: [[list(cell) for cell in component] for component in components]
                for policy, components in self.qualifying_regions
            },
            "cells": [cell.to_dict() for cell in self.cells],
        }


def adjudicate_h4(cells: Sequence[C75H4CellEvidence]) -> C75H4Adjudication:
    evidence = tuple(cells)
    expected_cells = set(_expected_p1_cells())
    expected_keys = {
        (cell, comparator)
        for cell in expected_cells
        for comparator in C75_H4_PRIMARY_COMPARATORS
    }
    by_key = {(item.cell, item.comparator): item for item in evidence}
    if len(by_key) != len(evidence) or set(by_key) != expected_keys:
        raise ValueError("H4 adjudication requires the complete frozen 75-cell map for every primary comparator")
    regions: list[tuple[PolicyID, tuple[tuple[tuple[int, float, int], ...], ...]]] = []
    any_support = False
    for comparator in C75_H4_PRIMARY_COMPARATORS:
        support_cells = {
            cell
            for cell in expected_cells
            if by_key[(cell, comparator)].supported
        }
        components = connected_support_components_p1(support_cells)
        qualifying = tuple(
            component
            for component in components
            if len(component) >= C75_MIN_CONNECTED_SUPPORT_CELLS
        )
        if qualifying:
            any_support = True
        regions.append((comparator, qualifying))
    return C75H4Adjudication(
        C75B_H4_SUPPORTED if any_support else C75B_H4_NOT_SUPPORTED,
        tuple(regions),
        tuple(sorted(evidence, key=lambda item: (item.comparator.value, item.cell))),
    )


@dataclass(frozen=True, slots=True)
class C75H7CellEvidence:
    series: ExperimentSeries
    cell: tuple[int | float, ...]
    status: str
    timing: tuple[C75TimingCellEvidence, ...]

    def __post_init__(self) -> None:
        if self.series is ExperimentSeries.P1_DEEP_REUSE:
            if len(self.cell) != 3:
                raise ValueError("P1 H7 cell must contain depth/reuse/workers")
            p1_cell_id((int(self.cell[0]), float(self.cell[1]), int(self.cell[2])))
        elif self.series is ExperimentSeries.P4_FANOUT_SHARED_PREFIX:
            if len(self.cell) != 2:
                raise ValueError("P4 H7 cell must contain width/shared-prefix")
            p4_cell_id((int(self.cell[0]), float(self.cell[1])))
        else:
            raise ValueError("H7 cell series must be P1 or P4")
        if self.status not in {C75B_COMPARABLE, C75B_SEMANTIC_INVALID}:
            raise ValueError("invalid H7 cell status")
        if self.status == C75B_COMPARABLE:
            if tuple(item.hardware_id for item in self.timing) != C75B_HARDWARE_STRATA:
                raise ValueError("comparable H7 evidence must contain both hardware strata")
        elif self.timing:
            raise ValueError("semantic-invalid H7 evidence cannot contain ranked timing")

    def to_dict(self) -> dict[str, Any]:
        return {
            "series": self.series.value,
            "cell": list(self.cell),
            "status": self.status,
            "timing": [
                {
                    "hardware_id": item.hardware_id,
                    "comparison": item.comparison.to_dict(),
                    "median_program_benefit": item.median_program_benefit,
                }
                for item in self.timing
            ],
        }


def evaluate_h7_cell(
    series: ExperimentSeries,
    cell: tuple[int | float, ...],
    rows: Sequence[C75ProgramRow],
) -> C75H7CellEvidence:
    if series is ExperimentSeries.P1_DEEP_REUSE:
        typed_cell = (int(cell[0]), float(cell[1]), int(cell[2]))
        cell_id = p1_cell_id(typed_cell)
    elif series is ExperimentSeries.P4_FANOUT_SHARED_PREFIX:
        typed_cell = (int(cell[0]), float(cell[1]))
        cell_id = p4_cell_id(typed_cell)
    else:
        raise ValueError("H7 evaluation supports only P1/P4")
    validate_cell_pairing(rows, series=series, cell_id=cell_id, required_policies=tuple(PolicyID))
    ranked_rows = tuple(row for row in rows if row.policy_id in {PolicyID.B0, PolicyID.B4})
    if not _ranking_eligible(ranked_rows):
        return C75H7CellEvidence(series, tuple(cell), C75B_SEMANTIC_INVALID, ())
    timing: list[C75TimingCellEvidence] = []
    for hardware_id in C75B_HARDWARE_STRATA:
        b4 = _policy_rows(rows, hardware_id, PolicyID.B4)
        baseline = _policy_rows(rows, hardware_id, PolicyID.B0)
        if series is ExperimentSeries.P1_DEEP_REUSE:
            b4_times = tuple(row.program_completion_time_seconds for row in b4)
            baseline_times = tuple(row.program_completion_time_seconds for row in baseline)
        else:
            b4_times = tuple(row.fanout_completion_time_seconds for row in b4)
            baseline_times = tuple(row.fanout_completion_time_seconds for row in baseline)
        if any(value is None for value in b4_times + baseline_times):
            raise ValueError("eligible H7 row is missing its frozen timing metric")
        comparison, median_benefit = paired_timing_benefit(
            tuple(float(value) for value in b4_times if value is not None),
            tuple(float(value) for value in baseline_times if value is not None),
        )
        timing.append(
            C75TimingCellEvidence(
                series=series,
                cell=tuple(cell),
                hardware_id=hardware_id,
                comparison=comparison,
                median_program_benefit=median_benefit,
            )
        )
    return C75H7CellEvidence(series, tuple(cell), C75B_COMPARABLE, tuple(timing))


@dataclass(frozen=True, slots=True)
class C75H7Adjudication:
    decision: str
    p7_pass: bool
    qualifying_runs: tuple[dict[str, Any], ...]
    cells: tuple[C75H7CellEvidence, ...]

    @property
    def supported(self) -> bool:
        return self.decision == C75B_H7_SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "p7_pass": self.p7_pass,
            "qualifying_runs": list(self.qualifying_runs),
            "cells": [cell.to_dict() for cell in self.cells],
        }


def adjudicate_h7(
    cells: Sequence[C75H7CellEvidence], p7_rows: Sequence[C75ProgramRow]
) -> C75H7Adjudication:
    evidence = tuple(cells)
    expected_keys = {
        (ExperimentSeries.P1_DEEP_REUSE, tuple(cell)) for cell in _expected_p1_cells()
    } | {
        (ExperimentSeries.P4_FANOUT_SHARED_PREFIX, tuple(cell)) for cell in _expected_p4_cells()
    }
    by_key = {(item.series, tuple(item.cell)): item for item in evidence}
    if len(by_key) != len(evidence) or set(by_key) != expected_keys:
        raise ValueError("H7 adjudication requires the complete frozen P1/P4 cell map")
    for workers in C75_WORKER_COUNTS:
        cell_rows = tuple(row for row in p7_rows if row.cell_id == p7_cell_id(workers))
        validate_cell_pairing(
            cell_rows,
            series=ExperimentSeries.P7_STATELESS_OVERHEAD,
            cell_id=p7_cell_id(workers),
            required_policies=tuple(PolicyID),
        )
    p7_pass = p7_negative_control_pass(p7_rows)
    timing = tuple(
        timing_item
        for item in evidence
        if item.status == C75B_COMPARABLE
        for timing_item in item.timing
    )
    runs = h7_gradient_runs(timing)
    supported = bool(runs) and p7_pass
    return C75H7Adjudication(
        C75B_H7_SUPPORTED if supported else C75B_H7_NOT_SUPPORTED,
        p7_pass,
        runs,
        tuple(sorted(evidence, key=lambda item: (item.series.value, item.cell))),
    )


def gate_g2_from_decisions(h4: C75H4Adjudication, h7: C75H7Adjudication) -> C75G2Decision:
    if C75_H5_DECISION != "SUPPORTED_WITHIN_DECLARED_PHASE_SPACE":
        raise RuntimeError("closed H5 decision drift")
    if C75_H6_DECISION != "EFFICIENCY_STRENGTHENED_WITHIN_DECLARED_PHASE_SPACE":
        raise RuntimeError("closed H6 decision drift")
    return adjudicate_g2(
        h4_supported=h4.supported,
        h5_supported=True,
        h6_supported=True,
        h7_supported=h7.supported,
    )


def adjudicator_identity() -> dict[str, Any]:
    return {
        "schema": C75B_ADJUDICATOR_SCHEMA,
        "h4_complete_cell_count": len(_expected_p1_cells()),
        "h4_primary_comparators": [policy.value for policy in C75_H4_PRIMARY_COMPARATORS],
        "h4_min_connected_cells": C75_MIN_CONNECTED_SUPPORT_CELLS,
        "h7_complete_cell_count": len(_expected_p1_cells()) + len(_expected_p4_cells()),
        "p7_control_cell_count": len(C75_WORKER_COUNTS),
        "cross_hardware_pairing_identity": "source_record_id+program_case_fingerprint+total_input_tokens",
        "within_hardware_pairing_identity": "one paired_result_fingerprint across all required policies for each seed",
        "semantic_invalid_h4_rule": "preserve and exclude before non-timing hardware-invariance ranking checks",
        "global_seed_source_mapping": "one source record per seed across the full sweep; 64 unique records",
        "closed_h5": C75_H5_DECISION,
        "closed_h6": C75_H6_DECISION,
        "gate_reduction": "with frozen positive H5+H6, G2=A iff H7 is supported; otherwise G2=B; H4 remains reported evidence",
        "comparative_execution": "NOT_RUN",
    }
