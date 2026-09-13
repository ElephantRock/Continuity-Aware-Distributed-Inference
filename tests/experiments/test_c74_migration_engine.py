from __future__ import annotations

import math

import pytest

from experiments.c74_migration_engine import (
    C74B_COMPARATIVE_RESULT_INSPECTION,
    C74B_ELIGIBLE,
    C74B_RECONCILIATION_MISMATCH,
    C74BCrossoverPoint,
    _build_scenario_runtime,
    _policy_observation,
    _select_action,
    build_crossover_manifest,
    build_failover_manifests,
    engine_identity,
    evaluate_crossover_cell,
    run_failover_cell,
)
from experiments.c74_migration_protocol import (
    C74_PARTIAL_UNRANKED_LABEL,
    C74_PROTOCOL_FINGERPRINT,
    C74_SEMANTIC_INVALID_LABEL,
    C74CrossoverClass,
    C74RecoveryAction,
    C74ScenarioID,
)
from simulator.policies import PolicyID, project_observation


EXECUTION_SHA = "a" * 40
TRANSFER_FIXTURE = ("a100-80gb", 1, 4096)


def _manifests(
    scenario_id: C74ScenarioID,
    policy_id: PolicyID,
    *,
    hardware_id: str = TRANSFER_FIXTURE[0],
    state_tokens: int = TRANSFER_FIXTURE[1],
    recompute_tokens: int = TRANSFER_FIXTURE[2],
    observed_reconciliation: str | None = None,
):
    return build_failover_manifests(
        execution_git_commit=EXECUTION_SHA,
        scenario_id=scenario_id,
        policy_id=policy_id,
        hardware_id=hardware_id,
        state_tokens=state_tokens,
        recompute_tokens=recompute_tokens,
        observed_reconciliation=observed_reconciliation,
    )


def _synthetic_cost(
    *,
    transfer_seconds: float,
    recompute_seconds: float,
    crossover: C74CrossoverClass,
) -> C74BCrossoverPoint:
    return C74BCrossoverPoint(
        manifest_fingerprint="0" * 64,
        hardware_id="a100-80gb",
        state_tokens=1,
        state_bytes=524_288,
        recompute_tokens=64,
        transfer_seconds=transfer_seconds,
        recompute_seconds=recompute_seconds,
        crossover=crossover,
    )


def test_engine_identity_is_pre_result_and_parent_bound() -> None:
    identity = engine_identity()
    assert identity["base_commit"] == "de4046fc4bcbf343d372cf8a151cdbb96fc6b45b"
    assert identity["c74a_protocol_fingerprint"] == C74_PROTOCOL_FINGERPRINT
    assert identity["comparative_result_inspection"] == C74B_COMPARATIVE_RESULT_INSPECTION == "NONE"
    assert identity["full_comparative_executor_present"] is False
    assert identity["common_c1_commit_authority"] is True
    assert identity["transfer_cost_rule"] == "transfer_seconds <= recompute_seconds"
    assert identity["transfer_cost_tie_action"] == "TRANSFER"
    assert engine_identity() == identity


def test_exact_c6_single_cell_lookup_is_deterministic_and_finite() -> None:
    manifest = build_crossover_manifest(
        execution_git_commit=EXECUTION_SHA,
        hardware_id="a100-80gb",
        state_tokens=1,
        recompute_tokens=4096,
    )
    first = evaluate_crossover_cell(manifest)
    second = evaluate_crossover_cell(manifest)
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.state_bytes == 524_288
    assert math.isfinite(first.transfer_seconds) and first.transfer_seconds >= 0
    assert math.isfinite(first.recompute_seconds) and first.recompute_seconds >= 0
    assert first.crossover in set(C74CrossoverClass)


def test_mechanics_branch_coverage_does_not_inspect_full_p5_outcome() -> None:
    accepted_single_cell = evaluate_crossover_cell(
        build_crossover_manifest(
            execution_git_commit=EXECUTION_SHA,
            hardware_id=TRANSFER_FIXTURE[0],
            state_tokens=TRANSFER_FIXTURE[1],
            recompute_tokens=TRANSFER_FIXTURE[2],
        )
    )
    assert accepted_single_cell.transfer_seconds < accepted_single_cell.recompute_seconds
    assert accepted_single_cell.crossover is C74CrossoverClass.TRANSFER_FASTER

    synthetic_recompute = _synthetic_cost(
        transfer_seconds=2.0,
        recompute_seconds=1.0,
        crossover=C74CrossoverClass.RECOMPUTE_FASTER,
    )
    assert synthetic_recompute.crossover is C74CrossoverClass.RECOMPUTE_FASTER


def test_nonfinite_and_out_of_domain_cost_inputs_fail_closed() -> None:
    with pytest.raises(ValueError, match="frozen C7.1 P5 axis"):
        build_crossover_manifest(
            execution_git_commit=EXECUTION_SHA,
            hardware_id="a100-80gb",
            state_tokens=2,
            recompute_tokens=64,
        )
    with pytest.raises(ValueError, match="finite and non-negative"):
        _synthetic_cost(
            transfer_seconds=math.nan,
            recompute_seconds=1.0,
            crossover=C74CrossoverClass.TIE,
        )
    with pytest.raises(ValueError, match="finite and non-negative"):
        _synthetic_cost(
            transfer_seconds=1.0,
            recompute_seconds=math.inf,
            crossover=C74CrossoverClass.TRANSFER_FASTER,
        )


def test_b0_b1_b2_remain_conservative_recompute() -> None:
    for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2):
        base, manifest = _manifests(C74ScenarioID.MATCHED_PLANNED_MIGRATION, policy_id)
        result = run_failover_cell(base, manifest)
        assert result.action is C74RecoveryAction.RECOMPUTE
        assert result.commit_attempted is False
        assert result.commit_succeeded is False
        assert result.state_transfer_volume_bytes == 0
        assert result.recomputation_numerator_tokens == result.recompute_tokens
        assert result.efficiency_status == C74B_ELIGIBLE
        assert result.semantic_violation_count == 0


def test_b3_and_b4_use_same_transfer_cost_rule_for_valid_matched_migration() -> None:
    for policy_id in (PolicyID.B3, PolicyID.B4):
        base, manifest = _manifests(C74ScenarioID.MATCHED_PLANNED_MIGRATION, policy_id)
        result = run_failover_cell(base, manifest)
        assert result.action is C74RecoveryAction.TRANSFER
        assert result.commit_attempted is True
        assert result.commit_succeeded is True
        assert result.commit_error_type is None
        assert result.fallback_recompute is False
        assert result.recompute_seconds_charged == 0.0
        assert result.state_transfer_volume_bytes == result.state_bytes
        assert result.recomputation_numerator_tokens == 0
        assert result.semantic_violation_count == 0
        assert result.efficiency_status == C74B_ELIGIBLE


def test_source_failure_valid_remote_can_commit_from_predeclared_surviving_source() -> None:
    base, manifest = _manifests(C74ScenarioID.SOURCE_FAILURE_VALID_REMOTE, PolicyID.B4)
    result = run_failover_cell(base, manifest)
    assert result.action is C74RecoveryAction.TRANSFER
    assert result.commit_succeeded is True
    assert result.state_transfer_volume_bytes == result.state_bytes
    assert result.semantic_violation_count == 0


def test_stale_binding_residual_makes_b3_pay_then_fallback_while_b4_avoids_service() -> None:
    b3_base, b3_manifest = _manifests(C74ScenarioID.STALE_BINDING_RESIDUAL, PolicyID.B3)
    b4_base, b4_manifest = _manifests(C74ScenarioID.STALE_BINDING_RESIDUAL, PolicyID.B4)
    b3 = run_failover_cell(b3_base, b3_manifest)
    b4 = run_failover_cell(b4_base, b4_manifest)

    assert b3.action is C74RecoveryAction.TRANSFER
    assert b3.commit_attempted is True
    assert b3.commit_succeeded is False
    assert b3.commit_error_type == "SemanticViolation"
    assert b3.fallback_recompute is True
    assert b3.transfer_seconds_charged > 0
    assert b3.recompute_seconds_charged > 0
    assert b3.redundant_transfer_bytes == b3.state_bytes
    assert b3.semantic_violation_count == 0

    assert b4.action is C74RecoveryAction.RECOMPUTE
    assert b4.commit_attempted is False
    assert b4.state_transfer_volume_bytes == 0
    assert b4.redundant_transfer_bytes == 0
    assert b4.semantic_violation_count == 0
    assert b3.recovery_time_seconds > b4.recovery_time_seconds


def test_concurrent_candidate_loser_has_same_common_c1_fence() -> None:
    b3_base, b3_manifest = _manifests(C74ScenarioID.CONCURRENT_CANDIDATE_LOSER, PolicyID.B3)
    b4_base, b4_manifest = _manifests(C74ScenarioID.CONCURRENT_CANDIDATE_LOSER, PolicyID.B4)
    b3 = run_failover_cell(b3_base, b3_manifest)
    b4 = run_failover_cell(b4_base, b4_manifest)
    assert b3.action is C74RecoveryAction.TRANSFER
    assert b3.commit_succeeded is False
    assert b3.commit_error_type == "SemanticViolation"
    assert b3.redundant_transfer_bytes == b3.state_bytes
    assert b4.action is C74RecoveryAction.RECOMPUTE
    assert b4.state_transfer_volume_bytes == 0
    assert b3.semantic_violation_count == b4.semantic_violation_count == 0


def test_destination_failure_charges_completed_transfer_before_safe_recompute() -> None:
    base, manifest = _manifests(
        C74ScenarioID.DESTINATION_FAILURE_AFTER_TRANSFER_BEFORE_COMMIT,
        PolicyID.B4,
    )
    result = run_failover_cell(base, manifest)
    assert result.action is C74RecoveryAction.TRANSFER
    assert result.commit_attempted is True
    assert result.commit_succeeded is False
    assert result.commit_error_type == "InsufficientEvidence"
    assert result.fallback_recompute is True
    assert result.transfer_seconds_charged > 0
    assert result.recompute_seconds_charged > 0
    assert result.recovery_time_seconds == (
        result.transfer_seconds_charged + result.recompute_seconds_charged
    )
    assert result.state_transfer_volume_bytes == result.state_bytes
    assert result.redundant_transfer_bytes == result.state_bytes
    assert result.semantic_violation_count == 0


def test_recompute_faster_mechanics_apply_equally_to_b3_and_b4_without_result_scan() -> None:
    synthetic_recompute = _synthetic_cost(
        transfer_seconds=2.0,
        recompute_seconds=1.0,
        crossover=C74CrossoverClass.RECOMPUTE_FASTER,
    )
    for policy_id in (PolicyID.B3, PolicyID.B4):
        runtime = _build_scenario_runtime(C74ScenarioID.MATCHED_PLANNED_MIGRATION)
        observation = _policy_observation(runtime, observed_reconciliation="MATCHED")
        view = project_observation(observation, policy_id)
        action, reason = _select_action(runtime, policy_id, view, synthetic_recompute)
        assert action is C74RecoveryAction.RECOMPUTE
        assert "RECOMPUTE_FASTER" in reason


def test_exact_cost_tie_selects_transfer_for_b3_and_b4_when_otherwise_eligible() -> None:
    synthetic_tie = _synthetic_cost(
        transfer_seconds=1.0,
        recompute_seconds=1.0,
        crossover=C74CrossoverClass.TIE,
    )
    for policy_id in (PolicyID.B3, PolicyID.B4):
        runtime = _build_scenario_runtime(C74ScenarioID.MATCHED_PLANNED_MIGRATION)
        observation = _policy_observation(runtime, observed_reconciliation="MATCHED")
        view = project_observation(observation, policy_id)
        action, _ = _select_action(runtime, policy_id, view, synthetic_tie)
        assert action is C74RecoveryAction.TRANSFER


def test_partial_materialization_remains_unranked_and_does_not_invent_bytes() -> None:
    for policy_id in PolicyID:
        base, manifest = _manifests(
            C74ScenarioID.PARTIAL_MATERIALIZATION_NO_COMMIT,
            policy_id,
        )
        result = run_failover_cell(base, manifest)
        assert result.action is C74RecoveryAction.RECOMPUTE
        assert result.state_transfer_volume_bytes == 0
        assert result.commit_attempted is False
        assert result.efficiency_status == C74_PARTIAL_UNRANKED_LABEL
        assert result.efficiency_eligible is False


def test_unexpected_ambiguity_is_retained_and_excluded_from_ranking() -> None:
    base, manifest = _manifests(
        C74ScenarioID.MATCHED_PLANNED_MIGRATION,
        PolicyID.B3,
        observed_reconciliation="AMBIGUOUS",
    )
    result = run_failover_cell(
        base,
        manifest,
        observed_reconciliation="AMBIGUOUS",
    )
    assert result.observed_reconciliation == "AMBIGUOUS"
    assert result.expected_reconciliation == "MATCHED"
    assert result.action is C74RecoveryAction.TRANSFER
    assert result.commit_succeeded is False
    assert result.fallback_recompute is True
    assert result.efficiency_status == C74B_RECONCILIATION_MISMATCH
    assert result.efficiency_eligible is False


def test_manifest_policy_view_binding_fails_closed_on_unrecorded_reconciliation_change() -> None:
    base, manifest = _manifests(
        C74ScenarioID.MATCHED_PLANNED_MIGRATION,
        PolicyID.B4,
    )
    with pytest.raises(ValueError, match="policy-view fingerprint drift"):
        run_failover_cell(
            base,
            manifest,
            observed_reconciliation="AMBIGUOUS",
        )


def test_anti_false_zero_authority_corruption_becomes_semantically_invalid() -> None:
    base, manifest = _manifests(C74ScenarioID.STALE_BINDING_RESIDUAL, PolicyID.B4)
    result = run_failover_cell(
        base,
        manifest,
        inject_authority_corruption=True,
    )
    assert result.semantic_violation_count == 1
    assert result.efficiency_status == C74_SEMANTIC_INVALID_LABEL
    assert result.efficiency_eligible is False


def test_failover_result_and_fingerprints_are_byte_deterministic() -> None:
    base1, manifest1 = _manifests(C74ScenarioID.MATCHED_PLANNED_MIGRATION, PolicyID.B4)
    base2, manifest2 = _manifests(C74ScenarioID.MATCHED_PLANNED_MIGRATION, PolicyID.B4)
    first = run_failover_cell(base1, manifest1)
    second = run_failover_cell(base2, manifest2)
    assert base1.fingerprint == base2.fingerprint
    assert manifest1.fingerprint == manifest2.fingerprint
    assert first.to_dict() == second.to_dict()
    assert first.fingerprint == second.fingerprint
