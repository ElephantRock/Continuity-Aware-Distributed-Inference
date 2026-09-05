import pytest

from continuity.entities import ReconcileOutcome
from experiments.correctness import CorrectnessMetric, OutcomeClass
from experiments.evidence_safety import (
    S4_E0_COHORT_ID,
    S4_E0_SCENARIOS,
    S4_E0_SCENARIO_SPECS,
    S4_E0_SCHEMA,
    _run_s4_e0_trial,
    run_s4_e0_paired,
    run_s4_e0_trial,
)
from simulator import PolicyID


_FAULTED = tuple(
    spec.scenario_id for spec in S4_E0_SCENARIO_SPECS if spec.fault_id is not None
)


@pytest.fixture(scope="module")
def evaluation():
    return run_s4_e0_paired()


def _trial(evaluation, scenario_id, policy_id):
    return next(
        trial
        for trial in evaluation.trials
        if trial.scenario_id == scenario_id and trial.policy_id is policy_id
    )


def _summary(evaluation, policy_id):
    return next(
        summary
        for summary in evaluation.summary.policy_summaries
        if summary.policy_id is policy_id
    )


def _rate(summary, metric):
    return next(rate for rate in summary.rates if rate.metric is metric)


def _outcome_count(summary, outcome):
    return dict(summary.outcome_counts)[outcome]


def test_s4_e0_manifest_is_canonical_and_bounded():
    assert len(S4_E0_SCENARIO_SPECS) == 10
    assert S4_E0_SCENARIOS == tuple(item.scenario_id for item in S4_E0_SCENARIO_SPECS)
    assert len(set(S4_E0_SCENARIOS)) == 10
    assert len(_FAULTED) == 9
    assert sum(spec.semantic_commit_allowed for spec in S4_E0_SCENARIO_SPECS) == 1


def test_s4_e0_paired_order_and_ground_truth_are_policy_invariant(evaluation):
    assert tuple(
        (trial.scenario_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (spec.scenario_id, policy_id)
        for spec in S4_E0_SCENARIO_SPECS
        for policy_id in PolicyID
    )

    for scenario_id in S4_E0_SCENARIOS:
        records = [
            _trial(evaluation, scenario_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        assert all(record.cohort_id == S4_E0_COHORT_ID for record in records)
        assert all(record.ground_truth["schema"] == S4_E0_SCHEMA for record in records)
        assert len({record.ground_truth_json for record in records}) == 1
        if scenario_id in _FAULTED:
            assert len({record.exogenous_opportunity_signature for record in records}) == 1
            signature = records[0].exogenous_opportunity_signature
            assert len(signature) == 1
            assert signature[0][0] == CorrectnessMetric.AMBIGUOUS_COMMIT_RATE.value


def test_ambiguous_only_reconciles_ambiguous_and_does_not_finalize(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "S4-AMBIGUOUS-ONLY", policy_id)
        assert trial.presentation.reconciliation is ReconcileOutcome.AMBIGUOUS
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.presentation.authoritative_commit is False
        assert trial.presentation.diverged_from_oracle is False
        assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS
        assert CorrectnessMetric.AMBIGUOUS_COMMIT_RATE not in trial.evaluation.metric_violations


def test_closed_c1_currently_commits_mixed_valid_plus_ambiguous_same_scope(evaluation):
    """Capture the C4.5a discovery before any C1 repair is attempted."""
    for policy_id in PolicyID:
        trial = _trial(
            evaluation,
            "S4-VALID-PLUS-AMBIGUOUS-SAME-SCOPE",
            policy_id,
        )
        assert trial.presentation.reconciliation is ReconcileOutcome.AMBIGUOUS
        assert trial.presentation.commit_outcome == "APPLIED"
        assert trial.presentation.error_type is None
        assert trial.presentation.authoritative_commit is True
        assert trial.presentation.diverged_from_oracle is True
        assert CorrectnessMetric.AMBIGUOUS_COMMIT_RATE in trial.evaluation.metric_violations
        assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_non_ambiguous_insufficient_evidence_cases_wait_and_do_not_finalize(evaluation):
    scenario_ids = (
        "S4-STALE-ONLY",
        "S4-UNKNOWN-ONLY",
        "S4-FAILED-ONLY",
        "S4-ESTIMATED-ONLY",
        "S4-DERIVED-ONLY",
        "S4-WRONG-SCOPE",
        "S4-EXPIRED-VALID-UNTIL",
    )
    for scenario_id in scenario_ids:
        for policy_id in PolicyID:
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.presentation.reconciliation is ReconcileOutcome.WAIT
            assert trial.presentation.commit_outcome == "REJECTED"
            assert trial.presentation.error_type == "InsufficientEvidence"
            assert trial.presentation.authoritative_commit is False
            assert trial.presentation.diverged_from_oracle is False
            assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS


def test_derived_only_output_does_not_smuggle_support_authority_into_finalize(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "S4-DERIVED-ONLY", policy_id)
        assert len(trial.evidence_ids) == 1
        assert trial.evidence_ids[0].endswith(":derived")
        objects = trial.evaluation.observed_evidence["evidence_objects"]
        by_id = {item["id"]: item for item in objects}
        derived = by_id[trial.evidence_ids[0]]
        assert derived["authority"] == "DERIVED"
        assert len(derived["derived_from"]) == 1
        support = by_id[derived["derived_from"][0]]
        assert support["authority"] == "EXACT_OBSERVATION"
        assert support["id"] not in trial.evidence_ids


def test_valid_exact_control_finalizes_normally(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "S4-VALID-EXACT-CONTROL", policy_id)
        assert trial.presentation.reconciliation is ReconcileOutcome.MATCHED
        assert trial.presentation.commit_outcome == "APPLIED"
        assert trial.presentation.error_type is None
        assert trial.presentation.authoritative_commit is True
        assert trial.presentation.diverged_from_oracle is False
        assert trial.evaluation.metric_opportunities == ()
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_only_b4_receives_frozen_evidence_and_reconciliation_information(evaluation):
    relevant = {
        "evidence_authority",
        "evidence_status",
        "evidence_freshness",
        "reconciliation",
    }
    for scenario_id in S4_E0_SCENARIOS:
        b4 = _trial(evaluation, scenario_id, PolicyID.B4)
        assert set(b4.policy_visible_evidence["evidence_fields"]) == relevant
        for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.policy_visible_evidence["evidence_fields"] == {}


def test_current_closed_stack_summary_exposes_one_real_acr_o4_per_policy(evaluation):
    for policy_id in PolicyID:
        summary = _summary(evaluation, policy_id)
        acr = _rate(summary, CorrectnessMetric.AMBIGUOUS_COMMIT_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert summary.operation_count == 10
        assert summary.faulted_operation_count == 9
        assert (acr.numerator, acr.denominator) == (1, 9)
        assert (sser.numerator, sser.denominator) == (1, 9)
        assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 1
        assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O3_EXPLICIT_NON_SUCCESS) == 8
        assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 1


def test_anti_false_zero_injection_converts_rejected_ambiguous_case_to_acr_o4():
    trial = _run_s4_e0_trial(
        PolicyID.B4,
        "S4-AMBIGUOUS-ONLY",
        inject_divergence=True,
    )
    assert trial.presentation.commit_outcome == "REJECTED"
    assert trial.presentation.error_type == "InsufficientEvidence"
    assert trial.presentation.authoritative_commit is True
    assert trial.presentation.diverged_from_oracle is True
    assert CorrectnessMetric.AMBIGUOUS_COMMIT_RATE in trial.evaluation.metric_violations
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_public_api_rejects_unknown_scenarios_and_non_policy_ids():
    with pytest.raises(ValueError):
        run_s4_e0_trial(PolicyID.B4, "unknown")

    with pytest.raises(TypeError):
        run_s4_e0_trial("B4", "S4-AMBIGUOUS-ONLY")  # type: ignore[arg-type]
