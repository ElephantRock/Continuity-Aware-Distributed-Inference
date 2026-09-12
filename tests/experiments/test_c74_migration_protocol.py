from __future__ import annotations

import json

import pytest

from experiments.c7_protocol import (
    C6_ARTIFACT_SHA256,
    C6_SCIENTIFIC_FINGERPRINT,
    C7_PROTOCOL_FINGERPRINT,
    ParameterSource,
)
from experiments.c74_migration_protocol import (
    C74_BASE_COMMIT,
    C74_CANONICAL_DETERMINISTIC_SEED,
    C74_C44_BINDING_SAFETY_MERGE,
    C74_COMPARATIVE_RESULT_INSPECTION,
    C74_EVENT_ORDER,
    C74_FROZEN_C71_FINGERPRINT,
    C74_FROZEN_C6_ARTIFACT_SHA256,
    C74_FROZEN_C6_SCIENTIFIC_FINGERPRINT,
    C74_PARTIAL_UNRANKED_LABEL,
    C74_POLICY_ACTION_RULES,
    C74_PROTOCOL_FINGERPRINT,
    C74_PROTOCOL_SCHEMA,
    C74_RANKABLE_SCENARIOS,
    C74_TIMING_EVIDENCE,
    C74_UNRANKED_SCENARIOS,
    C74CrossoverClass,
    C74CrossoverManifest,
    C74FailoverManifest,
    C74MigrationEfficiencyProtocol,
    C74ScenarioID,
    C74ScenarioRanking,
    crossover_class,
    p5_cells,
    p5_cells_adjacent,
    p5_point,
    p5_state_bytes,
    protocol_identity,
    recomputation_ratio_components,
    scenario_ranking,
)
from simulator.policies import PolicyID


ZERO_SHA256 = "0" * 64
ONE_SHA256 = "1" * 64
TWO_SHA256 = "2" * 64
THREE_SHA256 = "3" * 64
EXECUTION_SHA = "a" * 40


def test_parent_identities_and_pre_result_boundary_are_exact() -> None:
    assert C74_BASE_COMMIT == "32d77b6082583724ddace6e47856aa4db47f2957"
    assert C74_C44_BINDING_SAFETY_MERGE == "0d93e9147a7e184b33588e49b63e7a8c2b39beca"
    assert (
        C74_FROZEN_C71_FINGERPRINT
        == C7_PROTOCOL_FINGERPRINT
        == "706e0d5fff362a1eda8c906b957c914251c6e7949bac6be3c2c143ae21625474"
    )
    assert C74_FROZEN_C6_SCIENTIFIC_FINGERPRINT == C6_SCIENTIFIC_FINGERPRINT
    assert C74_FROZEN_C6_ARTIFACT_SHA256 == C6_ARTIFACT_SHA256
    assert C74_COMPARATIVE_RESULT_INSPECTION == "NONE"
    assert C74_TIMING_EVIDENCE == "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"


def test_p5_grid_is_exact_and_state_bytes_are_source_domain_points() -> None:
    cells = p5_cells()
    assert len(cells) == 20
    assert cells[0] == (1, 64)
    assert cells[-1] == (64, 4096)
    assert {state for state, _ in cells} == {1, 4, 16, 64}
    assert {recompute for _, recompute in cells} == {64, 256, 1024, 2048, 4096}
    assert p5_state_bytes(1) == 524_288
    assert p5_state_bytes(4) == 2_097_152
    assert p5_state_bytes(16) == 8_388_608
    assert p5_state_bytes(64) == 33_554_432
    with pytest.raises(ValueError, match="frozen C7.1 P5 axis"):
        p5_state_bytes(2)
    with pytest.raises(ValueError, match="frozen C7.1 P5 axis"):
        p5_point(1, 128)


def test_p5_adjacency_is_exact_one_axis_neighbor() -> None:
    assert p5_cells_adjacent((1, 64), (4, 64))
    assert p5_cells_adjacent((1, 64), (1, 256))
    assert not p5_cells_adjacent((1, 64), (4, 256))
    assert not p5_cells_adjacent((1, 64), (1, 64))
    assert not p5_cells_adjacent((1, 64), (16, 64))


def test_scenario_rankability_preserves_partial_materialization_boundary() -> None:
    assert len(C74_RANKABLE_SCENARIOS) == 5
    assert C74_UNRANKED_SCENARIOS == (
        C74ScenarioID.PARTIAL_MATERIALIZATION_NO_COMMIT,
    )
    for scenario in C74_RANKABLE_SCENARIOS:
        assert scenario_ranking(scenario) is C74ScenarioRanking.RANKABLE
    assert (
        scenario_ranking(C74ScenarioID.PARTIAL_MATERIALIZATION_NO_COMMIT).value
        == C74_PARTIAL_UNRANKED_LABEL
    )


def test_event_order_freezes_failure_before_common_commit_and_fallback() -> None:
    assert C74_EVENT_ORDER == (
        "FAULT_OR_MIGRATION_TRIGGER",
        "COMMON_PHYSICAL_STATE_OBSERVATION",
        "POLICY_VIEW_PROJECTION",
        "ACTION_SELECTION",
        "TRANSFER_OR_RECOMPUTE_SERVICE",
        "DESTINATION_FAILURE_IF_PREDECLARED",
        "COMMON_C1_MIGRATION_COMMIT_ATTEMPT_IF_TRANSFERRED",
        "FALLBACK_RECOMPUTE_IF_TRANSFER_CANNOT_AUTHORITATIVELY_COMMIT",
        "FIRST_AUTHORITATIVE_USEFUL_PROGRESS",
        "FINAL_METRIC_ACCOUNTING",
    )


def test_policy_rules_preserve_information_boundary_and_common_cost_rule() -> None:
    assert set(C74_POLICY_ACTION_RULES) == set(PolicyID)
    for policy in (PolicyID.B0, PolicyID.B1, PolicyID.B2):
        rule = C74_POLICY_ACTION_RULES[policy]
        assert rule["may_select_transfer"] is False
        assert rule["binding_generation_visible"] is False
        assert rule["reconciliation_visible"] is False
    b3 = C74_POLICY_ACTION_RULES[PolicyID.B3]
    assert b3["may_select_transfer"] is True
    assert b3["binding_generation_visible"] is False
    assert b3["reconciliation_visible"] is False
    b4 = C74_POLICY_ACTION_RULES[PolicyID.B4]
    assert b4["may_select_transfer"] is True
    assert b4["binding_generation_visible"] is True
    assert b4["reconciliation_visible"] is True


def test_crossover_classification_is_strict_and_not_an_h6_decision() -> None:
    assert crossover_class(transfer_seconds=1.0, recompute_seconds=2.0) is C74CrossoverClass.TRANSFER_FASTER
    assert crossover_class(transfer_seconds=2.0, recompute_seconds=1.0) is C74CrossoverClass.RECOMPUTE_FASTER
    assert crossover_class(transfer_seconds=1.0, recompute_seconds=1.0) is C74CrossoverClass.TIE
    with pytest.raises(ValueError):
        crossover_class(transfer_seconds=-1.0, recompute_seconds=1.0)


def test_recomputation_ratio_components_preserve_frozen_token_definition() -> None:
    assert recomputation_ratio_components(
        recompute_tokens=1024, transfer_authoritative_success=True
    ) == (0, 1024)
    assert recomputation_ratio_components(
        recompute_tokens=1024, transfer_authoritative_success=False
    ) == (1024, 1024)
    with pytest.raises(ValueError, match="frozen C7.1 P5 axis"):
        recomputation_ratio_components(
            recompute_tokens=1000, transfer_authoritative_success=False
        )


def test_protocol_payload_carries_no_comparative_outcome() -> None:
    protocol = C74MigrationEfficiencyProtocol()
    payload = protocol.to_dict()
    assert payload["schema"] == C74_PROTOCOL_SCHEMA
    assert payload["comparative_result_inspection"] == "NONE"
    assert payload["tracks"]["P5_CROSSOVER"]["policy_free"] is True
    assert payload["tracks"]["P5_CROSSOVER"]["can_adjudicate_h6"] is False
    assert payload["tracks"]["FAILOVER_EFFICIENCY"]["canonical_manifest_seed"] == 0
    assert payload["tracks"]["FAILOVER_EFFICIENCY"]["artificial_repeated_seeds"] is False
    assert payload["tracks"]["FAILOVER_EFFICIENCY"]["bootstrap_applied"] is False
    assert payload["closed_correctness_dependency"]["correctness_component_reopened"] is False
    text = json.dumps(payload, sort_keys=True)
    assert "policy_results" not in text
    assert "h6_decision_observed" not in text


def test_protocol_fingerprint_is_deterministic() -> None:
    assert C74MigrationEfficiencyProtocol().fingerprint == C74_PROTOCOL_FINGERPRINT
    assert C74MigrationEfficiencyProtocol().to_dict() == C74MigrationEfficiencyProtocol().to_dict()


def test_crossover_manifest_is_policy_free_and_design_axes_are_p_src4() -> None:
    manifest = C74CrossoverManifest(
        execution_git_commit=EXECUTION_SHA,
        protocol_fingerprint=C74_PROTOCOL_FINGERPRINT,
        hardware_id="a100-80gb",
        state_tokens=16,
        recompute_tokens=1024,
    )
    payload = manifest.to_dict()
    assert payload["policy_id"] is None
    assert payload["seed"] == C74_CANONICAL_DETERMINISTIC_SEED == 0
    assert payload["state_tokens_source"] == ParameterSource.P_SRC4.value
    assert payload["recompute_tokens_source"] == ParameterSource.P_SRC4.value
    assert payload["cost_evidence"] == C74_TIMING_EVIDENCE
    assert payload["state_bytes"] == 8_388_608


def test_failover_manifest_binds_scenario_policy_and_all_common_fingerprints() -> None:
    manifest = C74FailoverManifest(
        execution_git_commit=EXECUTION_SHA,
        base_c7_manifest_fingerprint=ZERO_SHA256,
        protocol_fingerprint=C74_PROTOCOL_FINGERPRINT,
        scenario_id=C74ScenarioID.STALE_BINDING_RESIDUAL,
        policy_id=PolicyID.B4,
        hardware_id="h100-80gb",
        state_tokens=4,
        recompute_tokens=256,
        program_case_fingerprint=ONE_SHA256,
        physical_fault_fingerprint=TWO_SHA256,
        policy_view_fingerprint=THREE_SHA256,
    )
    payload = manifest.to_dict()
    assert payload["scenario_id"] == "STALE_BINDING_RESIDUAL"
    assert payload["scenario_ranking"] == "RANKABLE"
    assert payload["policy_id"] == "B4"
    assert payload["seed"] == 0
    assert manifest.fingerprint == manifest.fingerprint


def test_manifest_rejects_protocol_axis_seed_and_hash_drift() -> None:
    with pytest.raises(ValueError, match="bind frozen C7.4a"):
        C74CrossoverManifest(EXECUTION_SHA, ZERO_SHA256, "a100-80gb", 1, 64)
    with pytest.raises(ValueError, match="frozen to 0"):
        C74CrossoverManifest(
            EXECUTION_SHA,
            C74_PROTOCOL_FINGERPRINT,
            "a100-80gb",
            1,
            64,
            seed=1,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        C74FailoverManifest(
            execution_git_commit=EXECUTION_SHA,
            base_c7_manifest_fingerprint="not-a-hash",
            protocol_fingerprint=C74_PROTOCOL_FINGERPRINT,
            scenario_id=C74ScenarioID.MATCHED_PLANNED_MIGRATION,
            policy_id=PolicyID.B3,
            hardware_id="a100-80gb",
            state_tokens=1,
            recompute_tokens=64,
            program_case_fingerprint=ONE_SHA256,
            physical_fault_fingerprint=TWO_SHA256,
            policy_view_fingerprint=THREE_SHA256,
        )


def test_main_identity_is_pre_result_and_has_no_outcome(capsys) -> None:
    from experiments.c74_migration_protocol import main

    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload == protocol_identity()
    assert payload["schema"] == C74_PROTOCOL_SCHEMA
    assert payload["protocol_fingerprint"] == C74_PROTOCOL_FINGERPRINT
    assert payload["comparative_result_inspection"] == "NONE"
    assert payload["p5_cell_count"] == 20
    assert payload["rankable_scenario_count"] == 5
    assert payload["unranked_scenario_count"] == 1
