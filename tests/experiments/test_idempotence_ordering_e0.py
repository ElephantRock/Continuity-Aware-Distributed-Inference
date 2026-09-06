import pytest

from experiments.correctness import (
    CorrectnessMetric,
    MetricOpportunityScope,
    OutcomeClass,
)
from experiments.idempotence_ordering import (
    S5_E0_COHORT_ID,
    S5_E0_SCENARIOS,
    S5_E0_SCENARIO_IDS,
    S5_E0_SCHEMA,
    _run_s5_e0_trial,
    run_s5_e0_paired,
    run_s5_e0_trial,
)
from simulator import PolicyID


_FAULTED = tuple(
    item.scenario_id for item in S5_E0_SCENARIOS if item.fault_id is not None
)


@pytest.fixture(scope="module")
def evaluation():
    return run_s5_e0_paired()


def _trial(evaluation, scenario_id, policy_id):
    return next(
        item
        for item in evaluation.trials
        if item.scenario.scenario_id == scenario_id and item.policy_id is policy_id
    )


def _summary(evaluation, policy_id):
    return next(
        item
        for item in evaluation.summary.policy_summaries
        if item.policy_id is policy_id
    )


def _rate(summary, metric):
    return next(rate for rate in summary.rates if rate.metric is metric)


def _outcome_count(summary, outcome):
    return dict(summary.outcome_counts)[outcome]


def test_s5_e0_manifest_is_canonical_bounded_and_predeclares_seven_faults():
    assert len(S5_E0_SCENARIOS) == 8
    assert S5_E0_SCENARIO_IDS == tuple(item.scenario_id for item in S5_E0_SCENARIOS)
    assert len(set(S5_E0_SCENARIO_IDS)) == 8
    assert len(_FAULTED) == 7
    assert sum(item.creates_dfr_opportunity for item in S5_E0_SCENARIOS) == 2
    assert S5_E0_SCENARIO_IDS == (
        "S5-D1-SAME-OUTPUT-FINALIZE-TWICE",
        "S5-D2-CONFLICTING-OUTPUT-AFTER-COMPLETION",
        "S5-D3-DUPLICATE-SEMANTIC-EVENT-ID",
        "S5-D4-CONFLICTING-DUPLICATE-EVENT-ID",
        "S5-D5-DUPLICATE-MIGRATION-COMMIT",
        "S5-D6-TERMINAL-CONTINUATION-REPLAY",
        "S5-D7-INVALID-STATE-REPLAY",
        "S5-D8-FIRST-VALID-FINALIZATION-CONTROL",
    )


def test_paired_order_ground_truth_and_common_authority_are_policy_invariant(evaluation):
    assert tuple(
        (trial.scenario.scenario_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (scenario.scenario_id, policy_id)
        for scenario in S5_E0_SCENARIOS
        for policy_id in PolicyID
    )
    for scenario_id in S5_E0_SCENARIO_IDS:
        trials = [_trial(evaluation, scenario_id, policy_id) for policy_id in PolicyID]
        records = [trial.evaluation for trial in trials]
        assert all(record.cohort_id == S5_E0_COHORT_ID for record in records)
        assert all(record.ground_truth["schema"] == S5_E0_SCHEMA for record in records)
        assert len({record.ground_truth_json for record in records}) == 1
        assert all(
            record.policy_decision["semantic_authority"] == "C1_COMMON_TO_B0_B4"
            for record in records
        )
        assert all(
            record.policy_decision["policy_specific_s5_information_used"] is False
            for record in records
        )


def test_same_output_duplicate_finalization_has_one_semantic_effect(evaluation):
    scenario_id = "S5-D1-SAME-OUTPUT-FINALIZE-TWICE"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.finalization_effects == ("o1",)
        assert trial.invariant_violations == ()
        assert trial.semantic_snapshot["request_status"] == "COMPLETED"
        assert trial.semantic_snapshot["authoritative_output_id"] == "o1"
        assert trial.semantic_snapshot["attempt_authority_status"] == "COMMITTED"
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY
        assert trial.evaluation.metric_opportunities == (
            CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
        )
        assert trial.evaluation.metric_opportunity_scopes == (
            MetricOpportunityScope.POLICY_DERIVED,
        )
        assert trial.evaluation.metric_violations == ()


def test_conflicting_output_after_completion_is_rejected_and_output_immutable(evaluation):
    scenario_id = "S5-D2-CONFLICTING-OUTPUT-AFTER-COMPLETION"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.finalization_effects == ("o1",)
        assert trial.invariant_violations == ()
        assert trial.semantic_snapshot["authoritative_output_id"] == "o1"
        assert trial.application_outcomes[0] == "APPLIED_OR_IDEMPOTENT"
        assert trial.application_outcomes[1].startswith("REJECTED:InvalidTransition")
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY
        assert trial.evaluation.metric_opportunities == (
            CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
        )
        assert trial.evaluation.metric_violations == ()


def test_duplicate_semantic_event_is_idempotent(evaluation):
    scenario_id = "S5-D3-DUPLICATE-SEMANTIC-EVENT-ID"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.semantic_snapshot["event_count"] == 1
        assert trial.semantic_snapshot["event_order"] == ["event-1"]
        assert trial.invariant_violations == ()
        assert trial.evaluation.metric_opportunities == ()


def test_conflicting_duplicate_event_identity_is_rejected_without_rewrite(evaluation):
    scenario_id = "S5-D4-CONFLICTING-DUPLICATE-EVENT-ID"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.semantic_snapshot["event_count"] == 1
        assert trial.semantic_snapshot["event_order"] == ["event-1"]
        assert trial.semantic_snapshot["event_payload"] == [("result", "SUCCEEDED")]
        assert trial.application_outcomes[1].startswith("REJECTED:SemanticViolation")
        assert trial.invariant_violations == ()
        assert trial.evaluation.metric_opportunities == ()


def test_duplicate_migration_commit_cannot_advance_authority_twice(evaluation):
    scenario_id = "S5-D5-DUPLICATE-MIGRATION-COMMIT"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.semantic_snapshot == {
            "current_binding": "b2",
            "current_epoch": 2,
            "b1_status": "SUPERSEDED",
            "b2_status": "ACTIVE",
        }
        assert trial.application_outcomes[1].startswith("REJECTED:SemanticViolation")
        assert trial.invariant_violations == ()
        assert trial.evaluation.metric_opportunities == ()


def test_terminal_continuation_replay_and_resurrection_guard(evaluation):
    scenario_id = "S5-D6-TERMINAL-CONTINUATION-REPLAY"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.semantic_snapshot == {"continuation_lifecycle": "TERMINAL"}
        assert trial.application_outcomes[:2] == ("APPLIED", "IDEMPOTENT")
        assert trial.application_outcomes[2].startswith("REJECTED:InvalidTransition")
        assert trial.invariant_violations == ()


def test_invalid_state_replay_and_resurrection_guard(evaluation):
    scenario_id = "S5-D7-INVALID-STATE-REPLAY"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.semantic_snapshot == {"state_validity": "INVALID"}
        assert trial.application_outcomes[:2] == ("APPLIED", "IDEMPOTENT")
        assert trial.application_outcomes[2].startswith("REJECTED:InvalidTransition")
        assert trial.invariant_violations == ()


def test_first_valid_finalization_positive_control_remains_functional(evaluation):
    scenario_id = "S5-D8-FIRST-VALID-FINALIZATION-CONTROL"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.finalization_effects == ("o1",)
        assert trial.semantic_snapshot["request_status"] == "COMPLETED"
        assert trial.semantic_snapshot["authoritative_output_id"] == "o1"
        assert trial.semantic_snapshot["attempt_authority_status"] == "COMMITTED"
        assert trial.invariant_violations == ()
        assert trial.evaluation.fault_id is None
        assert trial.evaluation.metric_opportunities == ()
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_policy_derived_dfr_denominators_are_only_completed_faulted_requests(evaluation):
    for policy_id in PolicyID:
        for scenario in S5_E0_SCENARIOS:
            trial = _trial(evaluation, scenario.scenario_id, policy_id)
            dfr_events = [
                event_id
                for metric, event_id in zip(
                    trial.evaluation.metric_opportunities,
                    trial.evaluation.metric_opportunity_event_ids,
                    strict=True,
                )
                if metric is CorrectnessMetric.DUPLICATE_FINALIZATION_RATE
            ]
            if scenario.creates_dfr_opportunity:
                assert len(dfr_events) == 1
                assert trial.evaluation.metric_opportunity_scopes == (
                    MetricOpportunityScope.POLICY_DERIVED,
                )
            else:
                assert dfr_events == []


def test_s5_e0_summary_is_zero_dfr_zero_sser_common_mechanism(evaluation):
    for policy_id in PolicyID:
        summary = _summary(evaluation, policy_id)
        dfr = _rate(summary, CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert summary.operation_count == 8
        assert summary.faulted_operation_count == 7
        assert (dfr.numerator, dfr.denominator) == (0, 2)
        assert (sser.numerator, sser.denominator) == (0, 7)
        assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 7
        assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O3_EXPLICIT_NON_SUCCESS) == 0
        assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0


def test_anti_false_zero_injection_creates_dfr_and_o4_after_real_safe_replay():
    trial = _run_s5_e0_trial(
        PolicyID.B4,
        "S5-D1-SAME-OUTPUT-FINALIZE-TWICE",
        inject_duplicate_finalization=True,
    )
    assert trial.injected_duplicate_finalization is True
    assert trial.finalization_effects == ("o1", "injected-second-output")
    assert "IDEMPOTENCE_VIOLATION" in trial.invariant_violations
    assert trial.evaluation.metric_opportunities == (
        CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
    )
    assert trial.evaluation.metric_violations == (
        CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
    )
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_public_api_rejects_unknown_scenarios_and_non_policy_ids():
    with pytest.raises(ValueError):
        run_s5_e0_trial(PolicyID.B4, "unknown")

    with pytest.raises(TypeError):
        run_s5_e0_trial("B4", S5_E0_SCENARIO_IDS[0])  # type: ignore[arg-type]
