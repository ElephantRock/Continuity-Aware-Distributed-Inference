from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from experiments.c7_protocol import C7_STOCHASTIC_SEEDS, ExperimentSeries
from experiments.c75_g2_adjudicate import (
    C75H4Adjudication,
    C75H7Adjudication,
    adjudicate_h4,
    adjudicate_h7,
    evaluate_h4_cell,
    evaluate_h7_cell,
    gate_g2_from_decisions,
    p1_cell_id,
    p4_cell_id,
    p7_cell_id,
    validate_global_seed_source_mapping,
)
from experiments.c75_g2_execute import C75B_HARDWARE_STRATA, C75ProgramRow
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


C75B_FINALIZER_SCHEMA = "cadi.c7.5b.g2-finalizer.v1"


def _p1_cells() -> tuple[tuple[int, float, int], ...]:
    return tuple(
        (depth, fraction, workers)
        for workers in C75_WORKER_COUNTS
        for depth in C75_P1_SESSION_DEPTHS
        for fraction in C75_P1_REUSE_FRACTIONS
    )


def _p4_cells() -> tuple[tuple[int, float], ...]:
    return tuple(
        (width, fraction)
        for width in C75_P4_FANOUT_WIDTHS
        for fraction in C75_P4_SHARED_FRACTIONS
    )


def _expected_cell_ids() -> set[tuple[ExperimentSeries, str]]:
    result = {
        (ExperimentSeries.P1_DEEP_REUSE, p1_cell_id(cell)) for cell in _p1_cells()
    }
    result |= {
        (ExperimentSeries.P4_FANOUT_SHARED_PREFIX, p4_cell_id(cell)) for cell in _p4_cells()
    }
    result |= {
        (ExperimentSeries.P7_STATELESS_OVERHEAD, p7_cell_id(workers))
        for workers in C75_WORKER_COUNTS
    }
    return result


def expected_program_row_count() -> int:
    cells = len(_p1_cells()) + len(_p4_cells()) + len(C75_WORKER_COUNTS)
    return cells * len(C75B_HARDWARE_STRATA) * len(C7_STOCHASTIC_SEEDS) * len(tuple(PolicyID))


@dataclass(frozen=True, slots=True)
class C75FinalAdjudication:
    seed_source_mapping: tuple[tuple[int, str], ...]
    h4: C75H4Adjudication
    h7: C75H7Adjudication
    gate_g2: C75G2Decision

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C75B_FINALIZER_SCHEMA,
            "seed_source_mapping": {str(seed): record_id for seed, record_id in self.seed_source_mapping},
            "h4": self.h4.to_dict(),
            "h7": self.h7.to_dict(),
            "gate_g2": self.gate_g2.value,
        }


def finalize_from_rows(rows: Sequence[C75ProgramRow]) -> C75FinalAdjudication:
    result_rows = tuple(rows)
    if len(result_rows) != expected_program_row_count():
        raise ValueError("C7.5b finalization requires the complete frozen P1/P4/P7 row set")
    observed_cell_ids = {(row.series, row.cell_id) for row in result_rows}
    if observed_cell_ids != _expected_cell_ids():
        raise ValueError("C7.5b result contains a missing or unexpected series/cell")
    seed_source_mapping = validate_global_seed_source_mapping(result_rows)

    h4_cells = []
    h7_cells = []
    for cell in _p1_cells():
        cell_rows = tuple(row for row in result_rows if row.cell_id == p1_cell_id(cell))
        for comparator in C75_H4_PRIMARY_COMPARATORS:
            h4_cells.append(evaluate_h4_cell(cell, cell_rows, comparator))
        h7_cells.append(
            evaluate_h7_cell(ExperimentSeries.P1_DEEP_REUSE, tuple(cell), cell_rows)
        )
    for cell in _p4_cells():
        cell_rows = tuple(row for row in result_rows if row.cell_id == p4_cell_id(cell))
        h7_cells.append(
            evaluate_h7_cell(ExperimentSeries.P4_FANOUT_SHARED_PREFIX, tuple(cell), cell_rows)
        )

    p7_rows = tuple(
        row for row in result_rows if row.series is ExperimentSeries.P7_STATELESS_OVERHEAD
    )
    h4 = adjudicate_h4(tuple(h4_cells))
    h7 = adjudicate_h7(tuple(h7_cells), p7_rows)
    gate = gate_g2_from_decisions(h4, h7)
    return C75FinalAdjudication(
        tuple((seed, seed_source_mapping[seed]) for seed in C7_STOCHASTIC_SEEDS),
        h4,
        h7,
        gate,
    )
