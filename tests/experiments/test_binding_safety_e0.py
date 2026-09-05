from experiments.binding_safety import (
    S3_E0_SCENARIOS,
    _run_s3_e0_trial,
    run_s3_e0_paired,
    run_s3_e0_trial,
)
from experiments.correctness import CorrectnessMetric, OutcomeClass
from simulator import MigrationDisposition, PolicyID


_FAULTED = (
    "S3-PARTIAL-MIGRATION",
    "S3-STALE-EPOCH-CANDIDATE",
    "FTR8-LATE-OLD-OWNER",
    "S3-CONCURRENT-MIGRATION",
)


def _trial(evaluation, scenario_id, policy_id):
    return next(
        item
        for item in evaluation.trials
        if item.scenario_id == scenario_id and item.policy_id is policy_id
    )


def _policy_summary(evaluation, policy_id):
    return next(
        item for item in evaluation.summary.policy_summaries if item.policy_id is policy_id
    )


def _rate(summary, metric):
    return next(item for item in summary.rates if item.metric is metric)


def _outcome_count(summary, outcome):
    return dict(summary.outcome_counts)[outcome]


def test_s3_e0_paired_uses_canonical_scenario_then_policy_order():
    evaluation = run_s3_e0_paired()

    assert len(evaluation.trials) == len(S3_E0_SCENARIOS) * len(tuple(PolicyID))
    assert tuple((item.scenario_id, item.policy_id) for item in evaluation.trials) == tuple(
        (scenario_id, policy_id)
        for scenario_id in S3_E0_SCENARIOS
        for policy_id in PolicyID
    )


def test_ground_truth_and_exogenous_sbdr_events_are_policy_invariant():
    evaluation = run_s3_e0_paired()

    for scenario_id in _FAULTED:
        records = [
            _trial(evaluation, scenario_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        assert len({record.ground_truth_json for record in records}) == 1
        assert len({record.exogenous_opportunity_signature for record in records}) == 1
        signature = records[0].exogenous_opportunity_signature
        assert len(signature) == 1
        assert signature[0][0] == CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE.value


def test_partial_migration_never_becomes_authoritative_without_commit_evidence():
    evaluation = run_s3_e0_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "S3-PARTIAL-MIGRATION", policy_id)
        assert trial.expected_binding_id == "b1"
        assert trial.expected_epoch == 1
        assert trial.final_binding_id == "b1"
        assert trial.final_epoch == 1
        assert trial.presentation is not None
        assert trial.presentation.binding_id == "b2"
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert not trial.presentation.diverged_from_oracle
        assert trial.presentation.invariant_error_type is None
        assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS
        assert CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE not in (
            trial.evaluation.metric_violations
        )


def test_stale_candidate_is_fenced_by_common_c1_base_epoch_guard():
    evaluation = run_s3_e0_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "S3-STALE-EPOCH-CANDIDATE", policy_id)
        assert trial.expected_binding_id == "b2"
        assert trial.expected_epoch == 2
        assert trial.final_binding_id == "b2"
        assert trial.final_epoch == 2
        assert trial.presentation is not None
        assert trial.presentation.binding_id == "b3"
        assert trial.presentation.binding_epoch == 3
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "SemanticViolation"
        assert not trial.presentation.diverged_from_oracle
        assert trial.presentation.invariant_error_type is None
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_ftr8_late_old_owner_cannot_restore_previous_epoch():
    evaluation = run_s3_e0_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "FTR8-LATE-OLD-OWNER", policy_id)
        assert trial.expected_binding_id == "b2"
        assert trial.expected_epoch == 2
        assert trial.final_binding_id == "b2"
        assert trial.final_epoch == 2
        assert trial.presentation is not None
        assert trial.presentation.binding_id == "b1"
        assert trial.presentation.binding_epoch == 1
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "SemanticViolation"
        assert not trial.presentation.diverged_from_oracle
        assert trial.presentation.invariant_error_type is None


def test_concurrent_migration_has_one_winner_and_loser_cannot_commit_afterward():
    evaluation = run_s3_e0_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "S3-CONCURRENT-MIGRATION", policy_id)
        assert trial.expected_binding_id == "b2"
        assert trial.expected_epoch == 2
        assert trial.final_binding_id == "b2"
        assert trial.final_epoch == 2
        assert trial.presentation is not None
        assert trial.presentation.binding_id == "b3"
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "SemanticViolation"
        assert not trial.presentation.diverged_from_oracle
        assert trial.presentation.invariant_error_type is None


def test_b4_partial_migration_waits_but_stale_epoch_safety_still_resides_in_c1():
    evaluation = run_s3_e0_paired()

    partial = _trial(evaluation, "S3-PARTIAL-MIGRATION", PolicyID.B4)
    assert len(partial.policy_migration_decisions) == 1
    assert partial.policy_migration_decisions[0].disposition is MigrationDisposition.WAIT
    assert partial.policy_migration_decisions[0].reason == "RECONCILIATION_REQUIRED"

    stale = _trial(evaluation, "S3-STALE-EPOCH-CANDIDATE", PolicyID.B4)
    assert len(stale.policy_migration_decisions) == 1
    assert stale.policy_migration_decisions[0].disposition is MigrationDisposition.ALLOW_COMMIT
    assert stale.policy_migration_decisions[0].reason == "RECONCILED_BINDING_COMMIT_ELIGIBLE"
    assert stale.presentation is not None
    assert stale.presentation.commit_outcome == "REJECTED"
    assert stale.presentation.error_type == "SemanticViolation"

    late = _trial(evaluation, "FTR8-LATE-OLD-OWNER", PolicyID.B4)
    assert late.policy_migration_decisions[0].disposition is MigrationDisposition.ALLOW_COMMIT
    assert late.presentation is not None
    assert late.presentation.commit_outcome == "REJECTED"


def test_b4_concurrent_candidates_can_both_be_admitted_but_c1_serializes_commit():
    trial = run_s3_e0_trial(PolicyID.B4, "S3-CONCURRENT-MIGRATION")

    assert len(trial.policy_migration_decisions) == 2
    assert all(
        item.disposition is MigrationDisposition.ALLOW_COMMIT
        for item in trial.policy_migration_decisions
    )
    assert trial.presentation is not None
    assert trial.presentation.binding_id == "b3"
    assert trial.presentation.commit_outcome == "REJECTED"
    assert trial.final_binding_id == "b2"
    assert trial.final_epoch == 2


def test_b0_b3_do_not_receive_an_invented_binding_migration_policy_surface():
    evaluation = run_s3_e0_paired()

    for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
        for scenario_id in S3_E0_SCENARIOS:
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.policy_migration_decisions == ()
            assert (
                trial.evaluation.policy_decision["binding_information_contract"]
                == "NO_BINDING_AWARE_MIGRATION_POLICY_SURFACE"
            )
            assert trial.evaluation.policy_decision[
                "c1_commit_is_authoritative_not_policy_decision"
            ] is True


def test_success_control_proves_migration_is_not_globally_disabled():
    evaluation = run_s3_e0_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "S3-SUCCESS-CONTROL", policy_id)
        assert trial.expected_binding_id == "b2"
        assert trial.expected_epoch == 2
        assert trial.final_binding_id == "b2"
        assert trial.final_epoch == 2
        assert trial.presentation is None
        assert trial.evaluation.fault_id is None
        assert trial.evaluation.metric_opportunities == ()
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY

    b4 = _trial(evaluation, "S3-SUCCESS-CONTROL", PolicyID.B4)
    assert len(b4.policy_migration_decisions) == 1
    assert b4.policy_migration_decisions[0].disposition is MigrationDisposition.ALLOW_COMMIT


def test_summary_records_semantic_null_comparator_for_authoritative_binding_safety():
    evaluation = run_s3_e0_paired()

    for policy_id in PolicyID:
        summary = _policy_summary(evaluation, policy_id)
        sbdr = _rate(summary, CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert summary.operation_count == 5
        assert summary.faulted_operation_count == 4
        assert (sbdr.numerator, sbdr.denominator) == (0, 4)
        assert (sser.numerator, sser.denominator) == (0, 4)
        assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 3
        assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O3_EXPLICIT_NON_SUCCESS) == 1
        assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0


def test_anti_false_zero_injected_binding_divergence_is_measured_as_sbdr_and_o4():
    trial = _run_s3_e0_trial(
        PolicyID.B4,
        "FTR8-LATE-OLD-OWNER",
        inject_divergence=True,
    )

    assert trial.presentation is not None
    assert trial.presentation.commit_outcome == "REJECTED"
    assert trial.presentation.error_type == "SemanticViolation"
    assert trial.presentation.diverged_from_oracle
    assert trial.final_binding_id == "b1"
    assert trial.final_epoch == 1
    assert CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE in (
        trial.evaluation.metric_violations
    )
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
    assert trial.evaluation.semantic_result.reported_success
    assert trial.evaluation.semantic_result.authoritative_commit
    assert not trial.evaluation.semantic_result.semantically_correct


def test_public_trial_api_rejects_unknown_scenarios_and_preserves_policy_type_checks():
    try:
        run_s3_e0_trial(PolicyID.B4, "unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown scenario must fail")

    try:
        run_s3_e0_trial("B4", "S3-SUCCESS-CONTROL")  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("non-PolicyID must fail")
