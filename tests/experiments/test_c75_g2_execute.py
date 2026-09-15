from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from experiments.c7_protocol import EfficiencyEligibility, ExperimentSeries
from experiments.c72_routing_reuse import evaluate_paired_program
from experiments.c75_g2_execute import (
    C75AlwaysValidAuthority,
    C75PairedComparison,
    C75ProgramRow,
    C75TimingCellEvidence,
    RatioComponents,
    build_manifests_for_case,
    build_p1_program_case,
    build_p4_program_case,
    build_p7_program_case,
    connected_support_components_p1,
    connected_support_components_p4,
    h7_gradient_runs,
    paired_ratio_difference,
    paired_timing_benefit,
    p7_negative_control_pass,
    parameters_for_case,
    preflight_payload,
    summarize_paired_result,
    verify_non_timing_hardware_invariance,
)
from experiments.c75_g2_protocol import (
    C75_AUGMENTATION_SCHEMA,
    C75_C72_ADMISSIBLE_DATASET_FINGERPRINT,
    C75_PROTOCOL_FINGERPRINT,
    C75_SOURCE_SELECTION_FINGERPRINT,
    augmentation_fingerprint,
)
from experiments.trace_workload import NormalizedTraceRecord
from simulator.continuity_policy import build_baseline_policies
from simulator.inference_cost_runtime import load_c64f_runtime_profiles
from simulator.policies import PolicyID


ROOT = Path(__file__).resolve().parents[2]
C64F_ARTIFACT = ROOT / "artifacts" / "c6.4f" / "exhaustive-source-equivalence.json"


def _record(record_id: str = "r0", input_tokens: int = 1024, output_tokens: int = 32) -> NormalizedTraceRecord:
    return NormalizedTraceRecord(
        record_id,
        f"session:{record_id}",
        0,
        0.0,
        input_tokens,
        output_tokens,
        f"group:{record_id}",
        input_tokens // 2,
    )


@pytest.fixture(scope="module")
def profiles():
    return load_c64f_runtime_profiles(C64F_ARTIFACT)


def _evaluate(case, record, profile, *, seed=0):
    authority = C75AlwaysValidAuthority()
    manifests = build_manifests_for_case(
        case,
        record,
        seed=seed,
        hardware_id=profile.hardware_id,
        execution_git_sha="f" * 40,
    )
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=manifests,
        case=case,
        oracle=C75AlwaysValidAuthority(),
        profile=profile,
    )
    return manifests, result


def test_preflight_payload_cannot_contain_comparative_result() -> None:
    payload = preflight_payload()
    assert payload["comparative_execution"] == "NOT_RUN"
    assert payload["c75_protocol_fingerprint"] == C75_PROTOCOL_FINGERPRINT
    assert payload["source_selection_fingerprint"] == C75_SOURCE_SELECTION_FINGERPRINT
    assert payload["augmentation_schema"] == C75_AUGMENTATION_SCHEMA
    assert payload["p1_cell_count"] == 75
    assert payload["p4_cell_count"] == 16
    assert payload["p7_control_cell_count"] == 3
    assert "policy_results" not in payload
    assert "gate_g2" not in payload


def test_p1_builder_binds_frozen_axes_and_allows_legitimate_b1_b4_confluence(profiles) -> None:
    record = _record()
    case = build_p1_program_case(
        record,
        seed=0,
        session_depth=4,
        reusable_prefix_fraction=0.5,
        worker_count=4,
    )
    assert case.series is ExperimentSeries.P1_DEEP_REUSE
    assert len(case.operations) == 4
    assert case.operations[0].eligible_reuse_tokens == 0
    assert all(op.eligible_reuse_tokens == 512 for op in case.operations[1:])
    assert parameters_for_case(case) == (
        ("reusable_prefix_fraction", 0.5),
        ("session_depth", 4),
        ("worker_count", 4),
    )

    manifests, result = _evaluate(case, record, profiles["a100-80gb"])
    by_policy = {item.policy_id: item for item in result.policy_results}
    assert all(item.semantic_violation_count == 0 for item in result.policy_results)
    assert all(item.efficiency_eligibility is EfficiencyEligibility.ELIGIBLE for item in result.policy_results)
    assert by_policy[PolicyID.B1].consumed_reuse_tokens == by_policy[PolicyID.B4].consumed_reuse_tokens
    assert by_policy[PolicyID.B1].recomputation_ratio == by_policy[PolicyID.B4].recomputation_ratio
    assert by_policy[PolicyID.B3].consumed_reuse_tokens == by_policy[PolicyID.B4].consumed_reuse_tokens

    b4_manifest = manifests[PolicyID.B4]
    assert b4_manifest.source_dataset_fingerprint == C75_C72_ADMISSIBLE_DATASET_FINGERPRINT
    expected_aug = augmentation_fingerprint(
        seed=0,
        series=case.series,
        source_record_id=record.record_id,
        program_case_fingerprint=case.fingerprint,
        parameters=parameters_for_case(case),
    )
    assert b4_manifest.augmentation_fingerprint == expected_aug


def test_p1_zero_reuse_does_not_fabricate_state_locality(profiles) -> None:
    record = _record()
    case = build_p1_program_case(
        record,
        seed=1,
        session_depth=4,
        reusable_prefix_fraction=0.0,
        worker_count=2,
    )
    for operation in case.operations:
        assert operation.eligible_reuse_tokens == 0
        assert operation.observation.state_candidate_key is None
        assert operation.observation.exact_state_id is None
        assert operation.observation.state_locations == ()
    _, result = _evaluate(case, record, profiles["a100-80gb"], seed=1)
    by_policy = {item.policy_id: item for item in result.policy_results}
    assert by_policy[PolicyID.B0].recomputation_ratio == 0.0
    assert by_policy[PolicyID.B4].recomputation_ratio == 0.0


def test_p4_builder_realizes_one_shared_state_location_without_semantic_regression(profiles) -> None:
    record = _record("r4")
    case = build_p4_program_case(
        record,
        seed=2,
        fanout_width=4,
        shared_prefix_fraction=0.75,
    )
    assert case.series is ExperimentSeries.P4_FANOUT_SHARED_PREFIX
    assert len(case.operations) == 4
    state_ids = {op.observation.exact_state_id for op in case.operations}
    locations = {op.observation.state_locations for op in case.operations}
    assert len(state_ids) == 1
    assert len(locations) == 1
    assert all(op.eligible_reuse_tokens == 768 for op in case.operations)
    manifests, result = _evaluate(case, record, profiles["a100-80gb"], seed=2)
    assert all(item.semantic_violation_count == 0 for item in result.policy_results)
    assert manifests[PolicyID.B4].parameters == (
        ("fanout_width", 4),
        ("shared_prefix_fraction", 0.75),
        ("worker_count", 4),
    )


def test_p7_builder_is_strictly_stateless_and_all_policies_converge(profiles) -> None:
    record = _record("r7")
    for worker_count in (2, 4, 8):
        case = build_p7_program_case(record, seed=3, worker_count=worker_count)
        observation = case.operations[0].observation
        assert observation.session_preferred_location is None
        assert observation.state_candidate_key is None
        assert observation.exact_state_id is None
        assert observation.state_locations == ()
        _, result = _evaluate(case, record, profiles["a100-80gb"], seed=3)
        assert {item.recomputation_ratio for item in result.policy_results} == {0.0}
        assert {item.state_reuse_ratio for item in result.policy_results} == {0.0}
        assert len({item.program_completion_time_seconds for item in result.policy_results}) == 1


def test_summarized_program_rows_preserve_components_and_fingerprints(profiles) -> None:
    record = _record("row")
    case = build_p1_program_case(
        record,
        seed=4,
        session_depth=2,
        reusable_prefix_fraction=0.25,
        worker_count=2,
    )
    _, result = _evaluate(case, record, profiles["a100-80gb"], seed=4)
    rows = summarize_paired_result(
        case=case,
        paired=result,
        hardware_id="a100-80gb",
        seed=4,
        source_record_id=record.record_id,
        cell_id="P1:d2:r0.25:w2",
    )
    assert tuple(row.policy_id for row in rows) == tuple(PolicyID)
    assert all(row.program_case_fingerprint == case.fingerprint for row in rows)
    assert all(row.paired_result_fingerprint == result.fingerprint for row in rows)
    assert all(row.total_input_tokens == 2048 for row in rows)


def test_ratio_of_sums_bootstrap_support_and_tie() -> None:
    b4 = tuple(RatioComponents(1, 10) for _ in range(8))
    baseline = tuple(RatioComponents(2, 10) for _ in range(8))
    supported = paired_ratio_difference(b4, baseline)
    assert supported.point == pytest.approx(-0.1)
    assert supported.ci95[0] == pytest.approx(-0.1)
    assert supported.ci95[1] == pytest.approx(-0.1)
    assert supported.favorable is True

    tied = paired_ratio_difference(b4, b4)
    assert tied.point == 0.0
    assert tied.ci95 == (0.0, 0.0)
    assert tied.favorable is False


def test_paired_timing_bootstrap_uses_ratio_of_summed_program_times() -> None:
    baseline = (10.0, 20.0, 30.0, 40.0)
    b4 = (9.0, 18.0, 27.0, 36.0)
    comparison, median_benefit = paired_timing_benefit(b4, baseline)
    assert comparison.point == pytest.approx(0.1)
    assert comparison.ci95[0] == pytest.approx(0.1)
    assert comparison.ci95[1] == pytest.approx(0.1)
    assert comparison.favorable is True
    assert median_benefit == pytest.approx(0.1)


def test_connected_components_respect_surface_adjacency() -> None:
    p1 = connected_support_components_p1(
        {
            (2, 0.25, 2),
            (4, 0.25, 2),
            (8, 0.25, 2),
            (2, 0.25, 4),
        }
    )
    assert sorted(map(len, p1)) == [1, 3]
    p4 = connected_support_components_p4({(2, 0.25), (4, 0.25), (16, 1.0)})
    assert sorted(map(len, p4)) == [1, 2]


def _timing_evidence(series, cell, hardware, median_value, *, favorable=True):
    ci = (0.01, 0.02) if favorable else (-0.01, 0.02)
    return C75TimingCellEvidence(
        series=series,
        cell=cell,
        hardware_id=hardware,
        comparison=C75PairedComparison(0.015 if favorable else 0.0, ci, favorable),
        median_program_benefit=median_value,
    )


def test_h7_gradient_requires_contiguous_monotone_run_on_both_hardware_strata() -> None:
    evidence = []
    for hardware in ("a100-80gb", "h100-80gb"):
        evidence.extend(
            [
                _timing_evidence(ExperimentSeries.P1_DEEP_REUSE, (2, 0.5, 4), hardware, 0.05),
                _timing_evidence(ExperimentSeries.P1_DEEP_REUSE, (4, 0.5, 4), hardware, 0.08),
                _timing_evidence(ExperimentSeries.P1_DEEP_REUSE, (8, 0.5, 4), hardware, 0.10),
            ]
        )
    runs = h7_gradient_runs(tuple(evidence))
    assert any(run["series"] == "P1_DEEP_REUSE" and run["axis"] == "session_depth" for run in runs)

    broken = tuple(
        replace(item, median_program_benefit=0.01)
        if item.hardware_id == "h100-80gb" and item.cell == (4, 0.5, 4)
        else item
        for item in evidence
    )
    assert not h7_gradient_runs(broken)


def _row(*, hardware, cell_id, seed, policy, pct, rr=0.0, reuse=0.0):
    return C75ProgramRow(
        hardware_id=hardware,
        series=ExperimentSeries.P7_STATELESS_OVERHEAD,
        cell_id=cell_id,
        seed=seed,
        source_record_id=f"r{seed}",
        program_case_fingerprint="a" * 64,
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
        recomputation_ratio=rr,
        state_reuse_ratio=reuse,
        state_reuse_token_ratio=0.0,
        cold_continuation_rate=0.0,
        program_completion_time_seconds=pct,
        fanout_completion_time_seconds=None,
    )


def test_p7_negative_control_checks_all_policies_seeds_cells_and_hardware() -> None:
    rows = []
    for hardware in ("a100-80gb", "h100-80gb"):
        for workers in (2, 4, 8):
            for seed in range(64):
                for policy in PolicyID:
                    rows.append(
                        _row(
                            hardware=hardware,
                            cell_id=f"P7:w{workers}",
                            seed=seed,
                            policy=policy,
                            pct=1.0 + workers / 100,
                        )
                    )
    assert p7_negative_control_pass(tuple(rows)) is True
    bad = list(rows)
    bad[0] = replace(bad[0], recomputation_ratio=0.01)
    assert p7_negative_control_pass(tuple(bad)) is False


def test_hardware_invariance_rejects_non_timing_drift() -> None:
    rows = []
    for hardware in ("a100-80gb", "h100-80gb"):
        for seed in range(64):
            for policy in (PolicyID.B0, PolicyID.B4):
                rows.append(
                    C75ProgramRow(
                        hardware_id=hardware,
                        series=ExperimentSeries.P1_DEEP_REUSE,
                        cell_id="P1:d2:r0.5:w2",
                        seed=seed,
                        source_record_id=f"r{seed}",
                        program_case_fingerprint="a" * 64,
                        paired_result_fingerprint="b" * 64,
                        policy_id=policy,
                        manifest_fingerprint="c" * 64,
                        total_input_tokens=2048,
                        eligible_reuse_opportunities=1,
                        consumed_reuse_opportunities=1 if policy is PolicyID.B4 else 0,
                        eligible_reuse_tokens=512,
                        consumed_reuse_tokens=512 if policy is PolicyID.B4 else 0,
                        semantic_violation_count=0,
                        efficiency_eligibility=EfficiencyEligibility.ELIGIBLE,
                        recomputation_ratio=0.0 if policy is PolicyID.B4 else 0.25,
                        state_reuse_ratio=1.0 if policy is PolicyID.B4 else 0.0,
                        state_reuse_token_ratio=1.0 if policy is PolicyID.B4 else 0.0,
                        cold_continuation_rate=0.0 if policy is PolicyID.B4 else 1.0,
                        program_completion_time_seconds=1.0 if hardware == "a100-80gb" else 0.8,
                        fanout_completion_time_seconds=None,
                    )
                )
    verify_non_timing_hardware_invariance(tuple(rows), (PolicyID.B0, PolicyID.B4))
    drifted = list(rows)
    index = next(i for i, row in enumerate(drifted) if row.hardware_id == "h100-80gb" and row.policy_id is PolicyID.B4)
    drifted[index] = replace(drifted[index], consumed_reuse_tokens=0, recomputation_ratio=0.25)
    with pytest.raises(ValueError, match="invariance"):
        verify_non_timing_hardware_invariance(tuple(drifted), (PolicyID.B0, PolicyID.B4))
