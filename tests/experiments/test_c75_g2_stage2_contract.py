from __future__ import annotations

from pathlib import Path

import pytest

from experiments.c72_routing_reuse import evaluate_paired_program
from experiments.c75_g2_execute import (
    C75AlwaysValidAuthority,
    build_manifests_for_case,
    build_p1_program_case,
)
from experiments.c75_g2_stage2_contract import (
    source_record_for_seed,
    stage2_contract_identity,
    summarize_verified_paired_result,
    validate_result_rows_against_frozen_mapping,
    validate_seed_record_binding,
)
from experiments.trace_workload import NormalizedTraceRecord
from simulator.continuity_policy import build_baseline_policies
from simulator.inference_cost_runtime import load_c64f_runtime_profiles
from simulator.policies import PolicyID


ROOT = Path(__file__).resolve().parents[2]
C64F_ARTIFACT = ROOT / "artifacts" / "c6.4f" / "exhaustive-source-equivalence.json"


def _record(seed: int) -> NormalizedTraceRecord:
    input_tokens = 256 + 4 * seed
    return NormalizedTraceRecord(
        f"record:{seed:02d}",
        f"session:{seed:02d}",
        seed,
        float(seed),
        input_tokens,
        16,
        f"group:{seed:02d}",
        input_tokens // 2,
    )


def _selected() -> tuple[NormalizedTraceRecord, ...]:
    return tuple(_record(seed) for seed in range(64))


def test_same_index_seed_record_binding_is_mandatory() -> None:
    selected = _selected()
    assert source_record_for_seed(selected, 7) == selected[7]
    validate_seed_record_binding(selected, seed=7, record=selected[7])
    with pytest.raises(ValueError, match="same-index"):
        validate_seed_record_binding(selected, seed=7, record=selected[8])


def test_result_rows_must_preserve_exact_frozen_seed_mapping() -> None:
    selected = _selected()
    profiles = load_c64f_runtime_profiles(C64F_ARTIFACT)
    seed = 3
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
    paired = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=manifests,
        case=case,
        oracle=authority,
        profile=profiles["a100-80gb"],
    )
    rows = summarize_verified_paired_result(
        case=case,
        paired=paired,
        manifests=manifests,
        hardware_id="a100-80gb",
        seed=seed,
        selected_source_records=selected,
        source_record=record,
        cell_id="P1:d2:r0.5:w2",
    )
    validate_result_rows_against_frozen_mapping(rows, selected)
    bad = list(rows)
    bad[0] = bad[0].__class__(
        **{
            **{name: getattr(bad[0], name) for name in bad[0].__dataclass_fields__},
            "source_record_id": selected[seed + 1].record_id,
        }
    )
    with pytest.raises(ValueError, match="seed-to-source-record"):
        validate_result_rows_against_frozen_mapping(tuple(bad), selected)


def test_verified_summary_rejects_hardware_relabeling() -> None:
    selected = _selected()
    profiles = load_c64f_runtime_profiles(C64F_ARTIFACT)
    seed = 4
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
    paired = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=manifests,
        case=case,
        oracle=authority,
        profile=profiles["a100-80gb"],
    )
    with pytest.raises(ValueError, match="manifest hardware"):
        summarize_verified_paired_result(
            case=case,
            paired=paired,
            manifests=manifests,
            hardware_id="h100-80gb",
            seed=seed,
            selected_source_records=selected,
            source_record=record,
            cell_id="P1:d2:r0.5:w2",
        )


def test_verified_summary_rejects_manifest_fingerprint_substitution() -> None:
    selected = _selected()
    profiles = load_c64f_runtime_profiles(C64F_ARTIFACT)
    seed = 5
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
    paired = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=manifests,
        case=case,
        oracle=authority,
        profile=profiles["a100-80gb"],
    )
    foreign_manifests = build_manifests_for_case(
        case,
        record,
        seed=seed,
        hardware_id="a100-80gb",
        execution_git_sha="e" * 40,
    )
    assert manifests[PolicyID.B4].fingerprint != foreign_manifests[PolicyID.B4].fingerprint
    with pytest.raises(ValueError, match="manifest fingerprint"):
        summarize_verified_paired_result(
            case=case,
            paired=paired,
            manifests=foreign_manifests,
            hardware_id="a100-80gb",
            seed=seed,
            selected_source_records=selected,
            source_record=record,
            cell_id="P1:d2:r0.5:w2",
        )


def test_stage2_contract_identity_remains_pre_result() -> None:
    identity = stage2_contract_identity()
    assert identity["comparative_execution"] == "NOT_RUN"
    assert identity["seed_record_rule"] == "seed N must use selected_source_records[N] exactly"
    assert "manifest.hardware_id" in identity["hardware_binding_rule"]
