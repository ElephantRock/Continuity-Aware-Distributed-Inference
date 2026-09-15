from __future__ import annotations

from dataclasses import replace

import pytest

from experiments.c7_protocol import EfficiencyEligibility, ExperimentSeries
from experiments.c75_g2_adjudicate import (
    C75B_COMPARABLE,
    C75B_SEMANTIC_INVALID,
    C75H4Adjudication,
    C75H4CellEvidence,
    C75H7Adjudication,
    C75H7CellEvidence,
    adjudicate_h4,
    adjudicate_h7,
    adjudicator_identity,
    gate_g2_from_decisions,
    p1_cell_id,
    p4_cell_id,
    p7_cell_id,
    validate_cell_pairing,
    validate_global_seed_source_mapping,
)
from experiments.c75_g2_execute import (
    C75B_H4_NOT_SUPPORTED,
    C75B_H4_SUPPORTED,
    C75B_H7_NOT_SUPPORTED,
    C75B_H7_SUPPORTED,
    C75B_HARDWARE_STRATA,
    C75PairedComparison,
    C75ProgramRow,
)
from experiments.c75_g2_finalize import expected_program_row_count
from experiments.c75_g2_protocol import (
    C75_H4_PRIMARY_COMPARATORS,
    C75_P1_REUSE_FRACTIONS,
    C75_P1_SESSION_DEPTHS,
    C75_P4_FANOUT_WIDTHS,
    C75_P4_SHARED_FRACTIONS,
    C75_WORKER_COUNTS,
    C75G2Decision,
)
from simulator.policies import PolicyID


def _row(
    *,
    series: ExperimentSeries,
    cell_id: str,
    hardware: str,
    seed: int,
    policy: PolicyID,
    source_record_id: str | None = None,
    case_fingerprint: str | None = None,
    pct: float = 1.0,
    fct: float | None = None,
) -> C75ProgramRow:
    source = source_record_id or f"source:{seed:02d}"
    case_fp = case_fingerprint or f"{seed + 1:064x}"
    return C75ProgramRow(
        hardware_id=hardware,
        series=series,
        cell_id=cell_id,
        seed=seed,
        source_record_id=source,
        program_case_fingerprint=case_fp,
        paired_result_fingerprint="b" * 64,
        policy_id=policy,
        manifest_fingerprint="c" * 64,
        total_input_tokens=1024,
        eligible_reuse_opportunities=0,
        consumed_reuse_opportunities=0,
        eligible_reuse_tokens=0,
        consumed_reuse_tokens=0,
        semantic_violation_count=0,
        efficiency_eligibility=EfficiencyEligibility.ELIGIBLE,
        recomputation_ratio=0.0,
        state_reuse_ratio=0.0,
        state_reuse_token_ratio=0.0,
        cold_continuation_rate=0.0,
        program_completion_time_seconds=pct,
        fanout_completion_time_seconds=fct,
    )


def _p1_cells():
    return tuple(
        (depth, fraction, workers)
        for workers in C75_WORKER_COUNTS
        for depth in C75_P1_SESSION_DEPTHS
        for fraction in C75_P1_REUSE_FRACTIONS
    )


def _p4_cells():
    return tuple(
        (width, fraction)
        for width in C75_P4_FANOUT_WIDTHS
        for fraction in C75_P4_SHARED_FRACTIONS
    )


def test_global_seed_source_mapping_is_one_to_one_and_cross_surface_stable() -> None:
    rows = tuple(
        _row(
            series=ExperimentSeries.P1_DEEP_REUSE,
            cell_id=p1_cell_id((2, 0.0, 2)),
            hardware="a100-80gb",
            seed=seed,
            policy=PolicyID.B0,
        )
        for seed in range(64)
    )
    mapping = validate_global_seed_source_mapping(rows)
    assert tuple(mapping) == tuple(range(64))
    assert len(set(mapping.values())) == 64

    duplicate = list(rows)
    duplicate[1] = replace(duplicate[1], source_record_id=duplicate[0].source_record_id)
    with pytest.raises(ValueError, match="64 unique"):
        validate_global_seed_source_mapping(tuple(duplicate))

    drift = rows + (
        replace(
            rows[0],
            series=ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
            cell_id=p4_cell_id((2, 0.25)),
            source_record_id="different-source",
        ),
    )
    with pytest.raises(ValueError, match="one frozen seed"):
        validate_global_seed_source_mapping(drift)


def test_cell_pairing_fails_closed_on_cross_hardware_program_identity_drift() -> None:
    cell = (2, 0.5, 2)
    cell_id = p1_cell_id(cell)
    rows = []
    for hardware in C75B_HARDWARE_STRATA:
        for seed in range(64):
            for policy in PolicyID:
                rows.append(
                    _row(
                        series=ExperimentSeries.P1_DEEP_REUSE,
                        cell_id=cell_id,
                        hardware=hardware,
                        seed=seed,
                        policy=policy,
                    )
                )
    validate_cell_pairing(
        tuple(rows),
        series=ExperimentSeries.P1_DEEP_REUSE,
        cell_id=cell_id,
    )
    index = next(
        i
        for i, row in enumerate(rows)
        if row.hardware_id == "h100-80gb" and row.seed == 0 and row.policy_id is PolicyID.B4
    )
    rows[index] = replace(rows[index], program_case_fingerprint="d" * 64)
    with pytest.raises(ValueError, match="one source/Program case"):
        validate_cell_pairing(
            tuple(rows),
            series=ExperimentSeries.P1_DEEP_REUSE,
            cell_id=cell_id,
        )


def _comparison(supported: bool) -> C75PairedComparison:
    if supported:
        return C75PairedComparison(-0.1, (-0.2, -0.05), True)
    return C75PairedComparison(0.0, (0.0, 0.0), False)


def test_h4_finalizer_requires_complete_maps_and_accepts_one_relevant_comparator_region() -> None:
    support_cells = {(2, 0.5, 2), (4, 0.5, 2)}
    evidence = []
    for comparator in C75_H4_PRIMARY_COMPARATORS:
        for cell in _p1_cells():
            supported = comparator is PolicyID.B0 and cell in support_cells
            comparison = _comparison(supported)
            evidence.append(
                C75H4CellEvidence(
                    cell=cell,
                    comparator=comparator,
                    status=C75B_COMPARABLE,
                    comparisons=tuple((hardware, comparison) for hardware in C75B_HARDWARE_STRATA),
                    supported=supported,
                )
            )
    result = adjudicate_h4(tuple(evidence))
    assert result.decision == C75B_H4_SUPPORTED
    assert result.supported is True
    b0_regions = dict(result.qualifying_regions)[PolicyID.B0]
    assert any(len(component) >= 2 for component in b0_regions)
    assert dict(result.qualifying_regions)[PolicyID.B1] == ()
    assert dict(result.qualifying_regions)[PolicyID.B2] == ()

    with pytest.raises(ValueError, match="complete frozen 75-cell map"):
        adjudicate_h4(tuple(evidence[:-1]))


def test_h7_finalizer_requires_complete_p1_p4_maps_and_p7_control() -> None:
    cells = tuple(
        C75H7CellEvidence(
            ExperimentSeries.P1_DEEP_REUSE,
            tuple(cell),
            C75B_SEMANTIC_INVALID,
            (),
        )
        for cell in _p1_cells()
    ) + tuple(
        C75H7CellEvidence(
            ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
            tuple(cell),
            C75B_SEMANTIC_INVALID,
            (),
        )
        for cell in _p4_cells()
    )
    p7_rows = []
    for workers in C75_WORKER_COUNTS:
        cell_id = p7_cell_id(workers)
        for hardware in C75B_HARDWARE_STRATA:
            for seed in range(64):
                for policy in PolicyID:
                    p7_rows.append(
                        _row(
                            series=ExperimentSeries.P7_STATELESS_OVERHEAD,
                            cell_id=cell_id,
                            hardware=hardware,
                            seed=seed,
                            policy=policy,
                        )
                    )
    result = adjudicate_h7(cells, tuple(p7_rows))
    assert result.p7_pass is True
    assert result.decision == C75B_H7_NOT_SUPPORTED
    assert result.supported is False

    with pytest.raises(ValueError, match="complete frozen P1/P4 cell map"):
        adjudicate_h7(cells[:-1], tuple(p7_rows))


def test_gate_reduction_is_frozen_before_results() -> None:
    h4 = C75H4Adjudication(C75B_H4_NOT_SUPPORTED, (), ())
    h7_supported = C75H7Adjudication(C75B_H7_SUPPORTED, True, ({"run": "fixture"},), ())
    h7_not_supported = C75H7Adjudication(C75B_H7_NOT_SUPPORTED, True, (), ())
    assert gate_g2_from_decisions(h4, h7_supported) is C75G2Decision.A
    assert gate_g2_from_decisions(h4, h7_not_supported) is C75G2Decision.B

    h4_positive = C75H4Adjudication(C75B_H4_SUPPORTED, (), ())
    assert gate_g2_from_decisions(h4_positive, h7_not_supported) is C75G2Decision.B


def test_finalizer_row_count_and_adjudicator_identity_are_frozen() -> None:
    assert expected_program_row_count() == 60_160
    identity = adjudicator_identity()
    assert identity["h4_complete_cell_count"] == 75
    assert identity["h7_complete_cell_count"] == 91
    assert identity["p7_control_cell_count"] == 3
    assert identity["comparative_execution"] == "NOT_RUN"
    assert "G2=A iff H7 is supported" in identity["gate_reduction"]
