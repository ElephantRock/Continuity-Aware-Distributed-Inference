from experiments.binding_safety_adversarial import (
    S3_ADVERSARIAL_CASE_IDS,
    S3_ADVERSARIAL_MANIFESTS,
    BindingEvidenceMode,
    BindingPressureFamily,
    _run_adversarial_trial,
    run_s3_adversarial_case,
    run_s3_adversarial_paired,
    run_s3_adversarial_trial,
)
from experiments.correctness import CorrectnessMetric, OutcomeClass
from simulator import MigrationDisposition, PolicyID


_FAULTED = tuple(item for item in S3_ADVERSARIAL_MANIFESTS if item.fault_id is not None)
_CONTROLS = tuple(item for item in S3_ADVERSARIAL_MANIFESTS if item.fault_id is None)


def _trial(evaluation, case_id, policy_id):
    return next(
        item
        for item in evaluation.trials
        if item.case_id == case_id and item.policy_id is policy_id
    )


def _policy_summary(evaluation, policy_id):
    return next(
        item for item in evaluation.summary.policy_summaries if item.policy_id is policy_id
    )


def _rate(summary, metric):
    return next(item for item in summary.rates if item.metric is metric)


def _outcome_count(summary, outcome):
    return dict(summary.outcome_counts)[outcome]


def test_corpus_has_16_cases_across_all_five_pressure_families():
    assert len(S3_ADVERSARIAL_MANIFESTS) == 16
    assert len(S3_ADVERSARIAL_CASE_IDS) == 16
    assert len(set(S3_ADVERSARIAL_CASE_IDS)) == 16
    assert {item.pressure_family for item in S3_ADVERSARIAL_MANIFESTS} == set(
        BindingPressureFamily
    )
    assert len(_FAULTED) == 14
    assert len(_CONTROLS) == 2


def test_paired_order_is_case_then_b0_b4():
    evaluation = run_s3_adversarial_paired()

    assert len(evaluation.trials) == 16 * len(tuple(PolicyID))
    assert tuple((item.case_id, item.policy_id) for item in evaluation.trials) == tuple(
        (manifest.case_id, policy_id)
        for manifest in S3_ADVERSARIAL_MANIFESTS
        for policy_id in PolicyID
    )


def test_faulted_ground_truth_and_exogenous_sbdr_signature_are_policy_invariant():
    evaluation = run_s3_adversarial_paired()

    for manifest in _FAULTED:
        records = [
            _trial(evaluation, manifest.case_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        assert len({record.ground_truth_json for record in records}) == 1
        assert len({record.exogenous_opportunity_signature for record in records}) == 1
        signature = records[0].exogenous_opportunity_signature
        assert signature == (
            (
                CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE.value,
                manifest.sbdr_event_id,
            ),
        )


def test_every_faulted_case_preserves_manifest_authority_for_all_policies():
    evaluation = run_s3_adversarial_paired()

    for manifest in _FAULTED:
        for policy_id in PolicyID:
            trial = _trial(evaluation, manifest.case_id, policy_id)
            assert trial.final_binding_id == manifest.expected_binding_id
            assert trial.final_epoch == manifest.expected_epoch
            assert not trial.presentation.diverged_from_oracle
            assert CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE not in (
                trial.evaluation.metric_violations
            )
            assert trial.presentation.invariant_error_type is None


def test_multi_epoch_late_owner_cases_remain_fenced():
    evaluation = run_s3_adversarial_paired()

    expected = {
        "A-TWO-HOP-EPOCH1-LATE": ("b3", 3, "b1"),
        "A-TWO-HOP-EPOCH2-LATE": ("b3", 3, "b2"),
        "A-THREE-HOP-EPOCH1-LATE": ("b4", 4, "b1"),
    }
    for case_id, (binding_id, epoch, presented) in expected.items():
        for policy_id in PolicyID:
            trial = _trial(evaluation, case_id, policy_id)
            assert (trial.final_binding_id, trial.final_epoch) == (binding_id, epoch)
            assert trial.presentation.binding_id == presented
            assert trial.presentation.commit_outcome == "REJECTED"
            assert trial.presentation.error_type == "SemanticViolation"


def test_concurrent_race_variants_have_exactly_one_authoritative_winner():
    evaluation = run_s3_adversarial_paired()

    expected = {
        "B-THREE-CANDIDATES-LOWEST-WINS": ("b2", 2, "b4"),
        "B-HIGHER-CANDIDATE-WINS-FIRST": ("b3", 3, "b2"),
        "B-THREE-CANDIDATES-MIDDLE-WINS": ("b3", 3, "b4"),
    }
    for case_id, (winner, epoch, loser) in expected.items():
        for policy_id in PolicyID:
            trial = _trial(evaluation, case_id, policy_id)
            assert (trial.final_binding_id, trial.final_epoch) == (winner, epoch)
            assert trial.presentation.binding_id == loser
            assert trial.presentation.commit_outcome == "REJECTED"
            assert trial.presentation.error_type == "SemanticViolation"


def test_missing_and_misscoped_evidence_are_explicit_wait_not_binding_divergence():
    evaluation = run_s3_adversarial_paired()

    cases = (
        "C-MISSING-COMMIT-EVIDENCE",
        "C-WRONG-BINDING-EVIDENCE-SCOPE",
        "C-WRONG-EPOCH-EVIDENCE-SCOPE",
        "D-DESTINATION-FAIL-BEFORE-COMMIT",
    )
    for case_id in cases:
        for policy_id in PolicyID:
            trial = _trial(evaluation, case_id, policy_id)
            assert (trial.final_binding_id, trial.final_epoch) == ("b1", 1)
            assert trial.presentation.commit_outcome == "REJECTED"
            assert trial.presentation.error_type == "InsufficientEvidence"
            assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS
            assert not trial.evaluation.semantic_result.reported_success

    assert next(
        item
        for item in S3_ADVERSARIAL_MANIFESTS
        if item.case_id == "C-WRONG-BINDING-EVIDENCE-SCOPE"
    ).evidence_mode is BindingEvidenceMode.WRONG_BINDING_SCOPE
    assert next(
        item
        for item in S3_ADVERSARIAL_MANIFESTS
        if item.case_id == "C-WRONG-EPOCH-EVIDENCE-SCOPE"
    ).evidence_mode is BindingEvidenceMode.WRONG_EPOCH_SCOPE


def test_partial_candidate_is_fenced_after_alternate_commit():
    evaluation = run_s3_adversarial_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "D-PARTIAL-THEN-ALTERNATE-COMMIT", policy_id)
        assert (trial.final_binding_id, trial.final_epoch) == ("b3", 3)
        assert trial.presentation.binding_id == "b2"
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "SemanticViolation"
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY
        assert any(
            item.get("kind") == "PARTIAL_MATERIALIZATION"
            for item in trial.setup_records
        )


def test_replay_and_repeat_cases_remain_safe_after_prior_commit_or_rejection():
    evaluation = run_s3_adversarial_paired()

    expected = {
        "E-DUPLICATE-WINNER-COMMIT": ("b2", 2),
        "E-REPEATED-STALE-LOSER": ("b2", 2),
        "E-MULTIHOP-OLD-OWNER-REPLAY": ("b4", 4),
    }
    for case_id, authority in expected.items():
        for policy_id in PolicyID:
            trial = _trial(evaluation, case_id, policy_id)
            assert (trial.final_binding_id, trial.final_epoch) == authority
            assert trial.presentation.commit_outcome == "REJECTED"
            assert trial.presentation.error_type == "SemanticViolation"
            assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY

    repeated = _trial(evaluation, "E-REPEATED-STALE-LOSER", PolicyID.B4)
    assert any(
        item.get("kind") == "SETUP_REJECTED_PRESENTATION"
        for item in repeated.setup_records
    )
    multihop = _trial(evaluation, "E-MULTIHOP-OLD-OWNER-REPLAY", PolicyID.B4)
    assert any(
        item.get("kind") == "SETUP_REJECTED_PRESENTATION"
        for item in multihop.setup_records
    )


def test_positive_controls_prove_valid_migration_remains_enabled():
    evaluation = run_s3_adversarial_paired()

    expected = {
        "C-VALID-EVIDENCE-CONTROL": ("b2", 2),
        "E-SEQUENTIAL-NEXT-EPOCH-CONTROL": ("b3", 3),
    }
    for case_id, authority in expected.items():
        for policy_id in PolicyID:
            trial = _trial(evaluation, case_id, policy_id)
            assert trial.evaluation.fault_id is None
            assert trial.evaluation.metric_opportunities == ()
            assert (trial.final_binding_id, trial.final_epoch) == authority
            assert trial.presentation.commit_outcome == "APPLIED"
            assert trial.presentation.error_type is None
            assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_b4_waits_on_insufficient_reconciliation_and_allows_matched_presentations():
    evaluation = run_s3_adversarial_paired()

    for manifest in S3_ADVERSARIAL_MANIFESTS:
        trial = _trial(evaluation, manifest.case_id, PolicyID.B4)
        assert len(trial.policy_migration_decisions) == 1
        decision = trial.policy_migration_decisions[0]
        if manifest.reconciliation == "MATCHED":
            assert decision.disposition is MigrationDisposition.ALLOW_COMMIT
        else:
            assert decision.disposition is MigrationDisposition.WAIT


def test_b0_b3_do_not_receive_binding_policy_surface_but_keep_common_c1_authority():
    evaluation = run_s3_adversarial_paired()

    for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
        for manifest in S3_ADVERSARIAL_MANIFESTS:
            trial = _trial(evaluation, manifest.case_id, policy_id)
            assert trial.policy_migration_decisions == ()
            assert trial.evaluation.policy_decision[
                "binding_information_contract"
            ] == "NO_BINDING_AWARE_MIGRATION_POLICY_SURFACE"
            assert trial.evaluation.policy_decision[
                "c1_commit_is_authoritative_not_policy_decision"
            ] is True


def test_summary_records_semantic_null_comparator_over_14_faulted_cases():
    evaluation = run_s3_adversarial_paired()

    for policy_id in PolicyID:
        summary = _policy_summary(evaluation, policy_id)
        sbdr = _rate(summary, CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert summary.operation_count == 16
        assert summary.faulted_operation_count == 14
        assert (sbdr.numerator, sbdr.denominator) == (0, 14)
        assert (sser.numerator, sser.denominator) == (0, 14)
        assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 10
        assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O3_EXPLICIT_NON_SUCCESS) == 4
        assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0


def test_anti_false_zero_corruption_records_sbdr_and_o4_after_real_rejection():
    trial = _run_adversarial_trial(
        PolicyID.B4,
        "A-TWO-HOP-EPOCH1-LATE",
        inject_divergence=True,
    )

    assert trial.presentation.commit_outcome == "REJECTED"
    assert trial.presentation.error_type == "SemanticViolation"
    assert trial.presentation.diverged_from_oracle
    assert CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE in (
        trial.evaluation.metric_violations
    )
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
    assert trial.evaluation.semantic_result.reported_success
    assert trial.evaluation.semantic_result.authoritative_commit
    assert not trial.evaluation.semantic_result.semantically_correct


def test_single_case_api_replays_all_policies_in_order():
    trials = run_s3_adversarial_case("B-HIGHER-CANDIDATE-WINS-FIRST")
    assert tuple(item.policy_id for item in trials) == tuple(PolicyID)
    assert all(item.case_id == "B-HIGHER-CANDIDATE-WINS-FIRST" for item in trials)


def test_public_api_rejects_unknown_cases_and_non_policy_ids():
    try:
        run_s3_adversarial_trial(PolicyID.B4, "unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown case must fail")

    try:
        run_s3_adversarial_case("unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown case must fail")

    try:
        run_s3_adversarial_trial("B4", S3_ADVERSARIAL_CASE_IDS[0])  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("non-PolicyID must fail")
