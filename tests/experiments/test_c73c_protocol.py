from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from continuity.entities import ContinuationLifecycle, StateLifecycle
from experiments.c7_protocol import (
    AXES,
    C7_PROTOCOL_FINGERPRINT,
    ExperimentSeries,
    ParameterSource,
)
from experiments.c73_retention_engine import RetentionEventKind, RetentionPolicyResult
from experiments.c73_retention_protocol import (
    C73_DEFAULT_STATE_BYTES,
    C73_PRIMARY_TTL_SECONDS,
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    C73_TTL_SENSITIVITY_SECONDS,
    CapacityOutcome,
    RetentionPolicyID,
)
from experiments.c73c_protocol import (
    C73C_BASE_COMMIT,
    C73C_FROZEN_C71_FINGERPRINT,
    C73C_FROZEN_C73A_FINGERPRINT,
    C73C_H5_NOT_SUPPORTED,
    C73C_INFEASIBLE_LABEL,
    C73C_PRIMARY_SURFACES,
    C73C_PROTOCOL_FINGERPRINT,
    C73C_REUSE_CONTEXT_TOKENS,
    C73CPairedEvaluationProtocol,
    C73CPrimaryCell,
    C73CSurfaceID,
    RatioComponents,
    aggregate_ratio,
    bootstrap_resample_indices,
    build_base_manifest,
    build_primary_program_case,
    metric_components,
    p2_pressure_priority,
    p2_tool_return_uniform,
    p3_admission_priority,
    p3_speculative_uniform,
    percentile_95_interval,
    primary_cells_adjacent,
    primary_surface_cells,
    reference_working_set_bytes,
    retention_manifest_variants,
)
from simulator.policies import PolicyID


def _p2_gap_cache(gap: float = 5.0, ratio: float = 1.0) -> C73CPrimaryCell:
    return C73CPrimaryCell(C73CSurfaceID.P2_GAP_CACHE, gap, ratio)


def _p2_gap_return(gap: float = 5.0, probability: float = 1.0) -> C73CPrimaryCell:
    return C73CPrimaryCell(C73CSurfaceID.P2_GAP_RETURN, gap, probability)


def _p3_width_cache(width: int = 4, ratio: float = 1.0) -> C73CPrimaryCell:
    return C73CPrimaryCell(C73CSurfaceID.P3_WIDTH_CACHE, width, ratio)


def test_parent_identities_and_pre_result_boundary() -> None:
    protocol = C73CPairedEvaluationProtocol()
    payload = protocol.to_dict()
    assert C73C_BASE_COMMIT == "451cafddb21d667f6abb73f48477560c850cda20"
    assert C73C_FROZEN_C71_FINGERPRINT == C7_PROTOCOL_FINGERPRINT
    assert C73C_FROZEN_C73A_FINGERPRINT == C73_RETENTION_PROTOCOL_FINGERPRINT
    assert payload["comparative_result_inspection"] == "NONE"
    assert "policy_results" not in payload
    assert "p2_results" not in payload
    assert "p3_results" not in payload
    assert len(C73C_PROTOCOL_FINGERPRINT) == 64


def test_primary_surfaces_are_compact_and_hold_reference_axes() -> None:
    assert [surface.surface_id for surface in C73C_PRIMARY_SURFACES] == [
        C73CSurfaceID.P2_GAP_CACHE,
        C73CSurfaceID.P2_GAP_RETURN,
        C73CSurfaceID.P3_WIDTH_CACHE,
    ]
    p2a = C73C_PRIMARY_SURFACES[0]
    p2b = C73C_PRIMARY_SURFACES[1]
    p3 = C73C_PRIMARY_SURFACES[2]
    assert dict(p2a.fixed_axes) == {
        "tool_return_probability": AXES["tool_return_probability"].reference_value
    }
    assert dict(p2b.fixed_axes) == {
        "cache_capacity_ratio": AXES["cache_capacity_ratio"].reference_value
    }
    assert dict(p3.fixed_axes) == {
        "speculative_fraction": AXES["speculative_fraction"].reference_value
    }
    assert len(primary_surface_cells(C73CSurfaceID.P2_GAP_CACHE)) == 20
    assert len(primary_surface_cells(C73CSurfaceID.P2_GAP_RETURN)) == 20
    assert len(primary_surface_cells(C73CSurfaceID.P3_WIDTH_CACHE)) == 16


def test_p2_surface_intersection_collapses_to_one_logical_cell_and_manifest() -> None:
    a = _p2_gap_cache(5.0, 1.0)
    b = _p2_gap_return(5.0, 1.0)
    assert a.logical_cell_key == b.logical_cell_key
    case_a = build_primary_program_case(a, seed=7)
    case_b = build_primary_program_case(b, seed=7)
    assert case_a.fingerprint == case_b.fingerprint
    manifest_a = build_base_manifest(
        a,
        seed=7,
        hardware_id="a100-80gb",
        execution_git_commit=C73C_BASE_COMMIT,
    )
    manifest_b = build_base_manifest(
        b,
        seed=7,
        hardware_id="a100-80gb",
        execution_git_commit=C73C_BASE_COMMIT,
    )
    assert manifest_a.fingerprint == manifest_b.fingerprint


def test_primary_cell_rejects_non_axis_value() -> None:
    with pytest.raises(ValueError, match="frozen C7.1 axis"):
        C73CPrimaryCell(C73CSurfaceID.P2_GAP_CACHE, 2.0, 1.0)


def test_p2_common_random_numbers_are_axis_independent_and_nested() -> None:
    for seed in range(64):
        u = p2_tool_return_uniform(seed)
        assert 0.0 <= u < 1.0
        outcomes = [u < p for p in (0.25, 0.5, 0.75, 1.0)]
        assert outcomes == sorted(outcomes)
        priorities = {
            state_id: p2_pressure_priority(seed, state_id)
            for state_id in (
                "pressure-active",
                "pressure-speculative-0",
                "pressure-speculative-1",
            )
        }
        assert len(set(priorities.values())) == 3

    a = build_primary_program_case(_p2_gap_cache(0.25, 1.0), seed=11)
    b = build_primary_program_case(_p2_gap_cache(120.0, 1.0), seed=11)
    order_a = [
        event.state_id
        for event in sorted(a.events, key=lambda event: (event.time_seconds, event.phase, event.ordinal))
        if event.kind is RetentionEventKind.ADMIT and event.state_id != "target"
    ]
    order_b = [
        event.state_id
        for event in sorted(b.events, key=lambda event: (event.time_seconds, event.phase, event.ordinal))
        if event.kind is RetentionEventKind.ADMIT and event.state_id != "target"
    ]
    assert order_a == order_b


def test_p2_realization_has_frozen_state_pressure_and_reference_working_set() -> None:
    case = build_primary_program_case(_p2_gap_cache(5.0, 0.5), seed=3)
    assert len(case.states) == 4
    assert reference_working_set_bytes(case) == 4 * C73_DEFAULT_STATE_BYTES
    target = next(state for state in case.states if state.state_id == "target")
    assert target.initial_lifecycle is StateLifecycle.ACTIVE
    waiting = [
        event
        for event in case.events
        if event.kind is RetentionEventKind.DEPENDENTS
        and event.state_id == "target"
        and event.dependent_lifecycles == (ContinuationLifecycle.WAITING,)
    ]
    assert len(waiting) == 1
    assert all(state.size_bytes == C73_DEFAULT_STATE_BYTES for state in case.states)


def test_p3_common_random_numbers_nested_across_fraction_and_width() -> None:
    for seed in range(64):
        for branch_index in range(16):
            u = p3_speculative_uniform(seed, branch_index)
            assert 0.0 <= u < 1.0
            outcomes = [u < p for p in (0.0, 0.25, 0.5, 0.75)]
            assert outcomes == sorted(outcomes)
            assert 0.0 <= p3_admission_priority(seed, branch_index) < 1.0

    seed = 17
    priorities4 = [p3_admission_priority(seed, i) for i in range(4)]
    priorities16 = [p3_admission_priority(seed, i) for i in range(16)]
    assert priorities4 == priorities16[:4]


def test_p3_realization_uses_waiting_vs_speculative_at_pressure_then_active_on_reuse() -> None:
    case = build_primary_program_case(_p3_width_cache(8, 1.0), seed=9)
    assert len(case.states) == 8
    assert reference_working_set_bytes(case) == 8 * C73_DEFAULT_STATE_BYTES
    assert {
        state.initial_lifecycle for state in case.states
    } <= {StateLifecycle.WAITING, StateLifecycle.SPECULATIVE}
    reuse_state_ids = {
        event.state_id
        for event in case.events
        if event.kind is RetentionEventKind.REUSE
    }
    for state in case.states:
        if state.initial_lifecycle is StateLifecycle.WAITING:
            assert state.state_id in reuse_state_ids
            assert any(
                event.kind is RetentionEventKind.DEPENDENTS
                and event.state_id == state.state_id
                and event.dependent_lifecycles == (ContinuationLifecycle.ACTIVE,)
                for event in case.events
            )
        else:
            assert state.state_id not in reuse_state_ids


def test_base_manifest_freezes_underlying_routing_and_psrc4_axes() -> None:
    cell = _p3_width_cache(4, 0.5)
    manifest = build_base_manifest(
        cell,
        seed=0,
        hardware_id="h100-80gb",
        execution_git_commit=C73C_BASE_COMMIT,
    )
    assert manifest.series is ExperimentSeries.P3_BRANCH_CACHE_PRESSURE
    assert manifest.policy_id is PolicyID.B4
    assert dict(manifest.parameters) == {
        "branch_width": 4,
        "cache_capacity_ratio": 0.5,
        "speculative_fraction": AXES["speculative_fraction"].reference_value,
        "state_tokens": AXES["state_tokens"].reference_value,
    }
    assert set(dict(manifest.parameter_sources).values()) == {ParameterSource.P_SRC4}


def test_retention_manifest_variants_share_case_base_and_capacity() -> None:
    cell = _p2_gap_cache(5.0, 0.5)
    case = build_primary_program_case(cell, seed=5)
    base = build_base_manifest(
        cell,
        seed=5,
        hardware_id="a100-80gb",
        execution_git_commit=C73C_BASE_COMMIT,
    )
    manifests = retention_manifest_variants(case=case, base_manifest=base)
    assert len(manifests) == 3 + len(C73_TTL_SENSITIVITY_SECONDS)
    assert {manifest.base_c7_manifest_fingerprint for manifest in manifests} == {
        base.fingerprint
    }
    assert {manifest.program_case_fingerprint for manifest in manifests} == {
        case.fingerprint
    }
    assert len({manifest.capacity_bytes for manifest in manifests}) == 1
    ttl_values = {
        manifest.ttl_seconds
        for manifest in manifests
        if manifest.retention_policy_id is RetentionPolicyID.FIXED_TTL
    }
    assert ttl_values == set(C73_TTL_SENSITIVITY_SECONDS)
    assert C73_PRIMARY_TTL_SECONDS in ttl_values


def test_retention_manifest_rejects_case_base_axis_or_seed_mismatch() -> None:
    base_cell = _p2_gap_cache(5.0, 1.0)
    mismatched_cell = _p2_gap_cache(30.0, 1.0)
    base = build_base_manifest(
        base_cell,
        seed=5,
        hardware_id="a100-80gb",
        execution_git_commit=C73C_BASE_COMMIT,
    )
    wrong_case = build_primary_program_case(mismatched_cell, seed=5)
    with pytest.raises(ValueError, match="does not match deterministic realization"):
        retention_manifest_variants(case=wrong_case, base_manifest=base)

    right_case_wrong_seed = build_primary_program_case(base_cell, seed=6)
    with pytest.raises(ValueError, match="does not match deterministic realization"):
        retention_manifest_variants(case=right_case_wrong_seed, base_manifest=base)


def test_case_base_cross_binding_rejects_extra_axes_and_wrong_underlying_policy() -> None:
    cell = _p2_gap_cache(5.0, 1.0)
    case = build_primary_program_case(cell, seed=5)
    base = build_base_manifest(
        cell,
        seed=5,
        hardware_id="a100-80gb",
        execution_git_commit=C73C_BASE_COMMIT,
    )
    extra_params = tuple(sorted(base.parameters + (("extra_axis", 1.0),)))
    extra_sources = tuple(sorted(base.parameter_sources + (("extra_axis", ParameterSource.P_SRC4),)))
    extra = replace(base, parameters=extra_params, parameter_sources=extra_sources)
    with pytest.raises(ValueError, match="exactly the frozen P2/P3 parameter set"):
        retention_manifest_variants(case=case, base_manifest=extra)

    wrong_policy = replace(base, policy_id=PolicyID.LRU)
    with pytest.raises(ValueError, match="PolicyID.B4"):
        retention_manifest_variants(case=case, base_manifest=wrong_policy)


def test_metric_components_preserve_ratio_numerators_and_zero_denominators() -> None:
    result = RetentionPolicyResult(
        "0" * 64,
        "1" * 64,
        C73_RETENTION_PROTOCOL_FINGERPRINT,
        "2" * 64,
        RetentionPolicyID.LRU,
        CapacityOutcome.ELIGIBLE,
        2,
        1,
        0,
        30.0,
        70.0,
        100.0,
        0.3,
        0.7,
        C73_DEFAULT_STATE_BYTES,
        C73_DEFAULT_STATE_BYTES,
        (),
    )
    components = metric_components(result)
    assert components.useful_state_residency == RatioComponents(30.0, 100.0)
    assert components.wasted_state_residency == RatioComponents(70.0, 100.0)
    assert components.recomputation_ratio == RatioComponents(
        C73C_REUSE_CONTEXT_TOKENS,
        2 * C73C_REUSE_CONTEXT_TOKENS,
    )
    assert components.cold_continuation_rate == RatioComponents(1.0, 2.0)
    assert components.recomputation_ratio.value == components.cold_continuation_rate.value

    combined = aggregate_ratio((RatioComponents(0.0, 0.0), RatioComponents(1.0, 2.0)))
    assert combined == RatioComponents(1.0, 2.0)
    assert RatioComponents(0.0, 0.0).value is None


def test_semantic_rejection_is_not_rankable_efficiency() -> None:
    result = RetentionPolicyResult(
        "0" * 64,
        "1" * 64,
        C73_RETENTION_PROTOCOL_FINGERPRINT,
        "2" * 64,
        RetentionPolicyID.LRU,
        CapacityOutcome.ELIGIBLE,
        1,
        0,
        1,
        0.0,
        1.0,
        1.0,
        0.0,
        1.0,
        0,
        C73_DEFAULT_STATE_BYTES,
        (),
    )
    with pytest.raises(ValueError, match="semantic rejection"):
        metric_components(result)


def test_bootstrap_schedule_and_percentile_are_deterministic() -> None:
    a = bootstrap_resample_indices(8, 0)
    b = bootstrap_resample_indices(8, 0)
    c = bootstrap_resample_indices(8, 1)
    assert a == b
    assert a != c
    assert len(a) == 8
    assert all(0 <= value < 8 for value in a)
    lo, hi = percentile_95_interval((0.0, 1.0, 2.0, 3.0))
    assert lo == pytest.approx(0.075)
    assert hi == pytest.approx(2.925)


def test_primary_surface_adjacency_is_one_grid_step() -> None:
    assert primary_cells_adjacent(
        _p2_gap_cache(1.0, 1.0),
        _p2_gap_cache(5.0, 1.0),
    )
    assert primary_cells_adjacent(
        _p2_gap_cache(5.0, 0.5),
        _p2_gap_cache(5.0, 1.0),
    )
    assert not primary_cells_adjacent(
        _p2_gap_cache(1.0, 0.5),
        _p2_gap_cache(5.0, 1.0),
    )
    assert not primary_cells_adjacent(
        _p2_gap_cache(5.0, 1.0),
        _p2_gap_return(5.0, 1.0),
    )


def test_h5_contract_keeps_infeasibility_and_null_outcome_explicit() -> None:
    payload = C73CPairedEvaluationProtocol().to_dict()
    assert payload["capacity_infeasibility"]["label"] == C73C_INFEASIBLE_LABEL
    assert payload["capacity_infeasibility"]["infeasible_is_policy_win"] is False
    assert payload["h5_rule"]["null_decision"] == C73C_H5_NOT_SUPPORTED
    assert payload["h5_rule"]["primary_baselines"] == [
        "LRU", "FIXED_TTL(5s)", "SESSION_PINNING"
    ]
    assert payload["h5_rule"]["single_hardware_ttft_can_trigger_h5"] is False
    assert "both accepted C6 hardware-profile strata" in payload["h5_rule"]["ttft_hardware_support_rule"]
    assert payload["metric_estimators"]["p2_ttft_minimum_returning_programs_for_inference"] == 8
    assert payload["paired_fairness"]["case_must_regenerate_exactly_from_base_manifest_parameters_and_seed"] is True
    assert payload["metric_estimators"][
        "rr_ccr_independent_corroboration_claim"
    ] is False


def test_c73c1_source_contains_no_retention_comparative_execution_call() -> None:
    source = Path("experiments/c73c_protocol.py").read_text()
    assert "run_retention_program(" not in source
