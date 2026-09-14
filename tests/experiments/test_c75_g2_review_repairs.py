from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from experiments.c7_protocol import EfficiencyEligibility, ExperimentSeries
from experiments.c72_routing_reuse import evaluate_paired_program
from experiments.c75_g2_adjudicate import (
    C75B_SEMANTIC_INVALID,
    evaluate_h4_cell,
    p1_cell_id,
    validate_cell_pairing,
)
from experiments.c75_g2_execute import (
    C75AlwaysValidAuthority,
    C75ProgramRow,
    build_manifests_for_case,
    build_p1_program_case,
)
from experiments.c75_g2_stage2_contract import summarize_verified_paired_result
from experiments.trace_workload import NormalizedTraceRecord
from simulator.continuity_policy import build_baseline_policies
from simulator.inference_cost_runtime import load_c64f_runtime_profiles
from simulator.policies import PolicyID


ROOT = Path(__file__).resolve().parents[2]
C64F_ARTIFACT = ROOT / "artifacts" / "c6.4f" / "exhaustive-source-equivalence.json"


def _source(seed: int) -> NormalizedTraceRecord:
    tokens = 256 + 4 * seed
    return NormalizedTraceRecord(
        f"record:{seed:02d}",
        f"session:{seed:02d}",
        seed,
        float(seed),
        tokens,
        16,
        f"group:{seed:02d}",
        tokens // 2,
    )


def _selected() -> tuple[NormalizedTraceRecord, ...]:
    return tuple(_source(seed) for seed in range(64))


def _row(*, hardware: str, seed: int, policy: PolicyID, cell_id: str) -> C75ProgramRow:
    return C75ProgramRow(
        hardware_id=hardware,
        series=ExperimentSeries.P1_DEEP_REUSE,
        cell_id=cell_id,
        seed=seed,
        source_record_id=f"record:{seed:02d}",
        program_case_fingerprint=f"{seed + 1:064x}",
        paired_result_fingerprint=("a" if hardware == "a100-80gb" else "b") * 64,
        policy_id=policy,
        manifest_fingerprint=f"{policy.value[-1]}" * 64,
        total_input_tokens=1024,
        eligible_reuse_opportunities=1,
        consumed_reuse_opportunities=1,
        eligible_reuse_tokens=512,
        consumed_reuse_tokens=512,
        semantic_violation_count=0,
        efficiency_eligibility=EfficiencyEligibility.ELIGIBLE,
        recomputation_ratio=0.0,
        state_reuse_ratio=1.0,
        state_reuse_token_ratio=1.0,
        cold_continuation_rate=0.0,
        program_completion_time_seconds=1.0,
        fanout_completion_time_seconds=None,
    )


def _complete_cell_rows(cell_id: str) -> list[C75ProgramRow]:
    return [
        _row(hardware=hardware, seed=seed, policy=policy, cell_id=cell_id)
        for hardware in ("a100-80gb", "h100-80gb")
        for seed in range(64)
        for policy in PolicyID
    ]


def test_summary_rejects_caller_surface_relabel() -> None:
    selected = _selected()
    seed = 2
    record = selected[seed]
    case = build_p1_program_case(
        record,
        seed=seed,
        session_depth=2,
        reusable_prefix_fraction=0.5,
        worker_count=2,
    )
    manifests = build_manifests_for_case(
        case,
        record,
        seed=seed,
        hardware_id="a100-80gb",
        execution_git_sha="f" * 40,
    )
    authority = C75AlwaysValidAuthority()
    profiles = load_c64f_runtime_profiles(C64F_ARTIFACT)
    paired = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=manifests,
        case=case,
        oracle=authority,
        profile=profiles["a100-80gb"],
    )
    with pytest.raises(ValueError, match="realized Program surface cell"):
        summarize_verified_paired_result(
            case=case,
            paired=paired,
            manifests=manifests,
            hardware_id="a100-80gb",
            seed=seed,
            selected_source_records=selected,
            source_record=record,
            cell_id="P1:d32:r1:w8",
        )


def test_cell_pairing_rejects_policy_rows_from_different_paired_results() -> None:
    cell_id = p1_cell_id((2, 0.5, 2))
    rows = _complete_cell_rows(cell_id)
    target = next(
        index
        for index, row in enumerate(rows)
        if row.hardware_id == "a100-80gb" and row.seed == 0 and row.policy_id is PolicyID.B1
    )
    rows[target] = replace(rows[target], paired_result_fingerprint="c" * 64)
    with pytest.raises(ValueError, match="one paired result"):
        validate_cell_pairing(
            tuple(rows),
            series=ExperimentSeries.P1_DEEP_REUSE,
            cell_id=cell_id,
        )


def test_semantic_invalid_h4_cell_is_preserved_before_invariance_check() -> None:
    cell = (2, 0.5, 2)
    cell_id = p1_cell_id(cell)
    rows = _complete_cell_rows(cell_id)
    target = next(
        index
        for index, row in enumerate(rows)
        if row.hardware_id == "h100-80gb" and row.seed == 0 and row.policy_id is PolicyID.B4
    )
    rows[target] = replace(
        rows[target],
        semantic_violation_count=1,
        efficiency_eligibility=EfficiencyEligibility.SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING,
        recomputation_ratio=0.5,
        consumed_reuse_tokens=0,
    )
    result = evaluate_h4_cell(cell, tuple(rows), PolicyID.B0)
    assert result.status == C75B_SEMANTIC_INVALID
    assert result.supported is False
    assert result.comparisons == ()
