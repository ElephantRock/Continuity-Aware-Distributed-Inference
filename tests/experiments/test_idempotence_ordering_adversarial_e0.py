import pytest

from experiments.correctness import (
    CorrectnessMetric,
    MetricOpportunityScope,
    OutcomeClass,
)
from experiments.idempotence_ordering_adversarial import (
    S5_ADVERSARIAL_CANONICAL_ACTIONS,
    S5_ADVERSARIAL_COHORT_ID,
    S5_ADVERSARIAL_SCHEMA,
    S5_ADVERSARIAL_VARIANTS,
    S5_ADVERSARIAL_VARIANT_IDS,
    S5PermutationFamily,
    _run_s5_adversarial_trial,
    canonical_trace_snapshot,
    run_s5_adversarial_paired,
    run_s5_adversarial_trial,
)
from simulator import PolicyID


@pytest.fixture(scope="module")
def evaluation():
    return run_s5_adversarial_paired()


def _trial(evaluation, variant_id, policy_id):
    return next(
        item
        for item in evaluation.trials
        if item.variant.variant_id == variant_id and item.policy_id is policy_id
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


def test_manifest_is_predeclared_bounded_and_has_fourteen_faulted_variants():
    assert len(S5_ADVERSARIAL_VARIANTS) == 14
    assert S5_ADVERSARIAL_VARIANT_IDS == tuple(
        item.variant_id for item in S5_ADVERSARIAL_VARIANTS
    )
    assert len(set(S5_ADVERSARIAL_VARIANT_IDS)) == 14
    assert tuple(S5_ADVERSARIAL_CANONICAL_ACTIONS) == tuple(S5PermutationFamily)
    assert sum(
        item.family
        in {
            S5PermutationFamily.DUPLICATE_FINALIZE,
            S5PermutationFamily.CONFLICTING_OUTPUT,
            S5PermutationFamily.ATTEMPT_GENERATION,
        }
        for item in S5_ADVERSARIAL_VARIANTS
    ) == 9


def test_each_family_canonical_trace_matches_independent_declared_target():
    snapshots = {
        family: canonical_trace_snapshot(family) for family in S5PermutationFamily
    }
    assert snapshots[S5PermutationFamily.DUPLICATE_FINALIZE][
        "authoritative_output_id"
    ] == "o1"
    assert snapshots[S5PermutationFamily.CONFLICTING_OUTPUT][
        "authoritative_output_id"
    ] == "o1"
    assert snapshots[S5PermutationFamily.ATTEMPT_GENERATION][
        "authoritative_output_id"
    ] == "o2"
    assert len(snapshots[S5PermutationFamily.DUPLICATE_EVENT_ID]["events"]) == 2
    assert snapshots[S5PermutationFamily.STALE_BINDING_LOSER]["current_binding"] == "b2"
    assert snapshots[S5PermutationFamily.STALE_BINDING_LOSER]["current_epoch"] == 2


def test_paired_order_ground_truth_and_common_authority_are_policy_invariant(evaluation):
    assert tuple(
        (trial.variant.variant_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (variant.variant_id, policy_id)
        for variant in S5_ADVERSARIAL_VARIANTS
        for policy_id in PolicyID
    )
    for variant in S5_ADVERSARIAL_VARIANTS:
        records = [
            _trial(evaluation, variant.variant_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        assert all(record.cohort_id == S5_ADVERSARIAL_COHORT_ID for record in records)
        assert all(record.ground_truth["schema"] == S5_ADVERSARIAL_SCHEMA for record in records)
        assert len({record.ground_truth_json for record in records}) == 1
        assert all(
            record.policy_decision["semantic_authority"] == "C1_COMMON_TO_B0_B4"
            for record in records
        )
        assert all(
            record.policy_decision["policy_specific_s5_information_used"] is False
            for record in records
        )


def test_every_permutation_matches_canonical_authoritative_snapshot(evaluation):
    for trial in evaluation.trials:
        assert trial.observed_snapshot == trial.canonical_snapshot
        assert trial.invariant_violations == ()
        assert trial.evaluation.observed_evidence[
            "snapshot_matches_canonical_target"
        ] is True
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_duplicate_finalize_variants_have_one_effect_and_policy_derived_dfr(evaluation):
    for variant_id in ("P1-A", "P1-B", "P1-C"):
        for policy_id in PolicyID:
            trial = _trial(evaluation, variant_id, policy_id)
            assert trial.finalization_effects == ("o1",)
            assert trial.completed_request_id == "r1"
            assert trial.evaluation.metric_opportunities == (
                CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
            )
            assert trial.evaluation.metric_opportunity_scopes == (
                MetricOpportunityScope.POLICY_DERIVED,
            )
            assert trial.evaluation.metric_violations == ()


def test_conflicting_late_output_never_replaces_authoritative_o1(evaluation):
    for variant_id in ("P2-A", "P2-B", "P2-C"):
        for policy_id in PolicyID:
            trial = _trial(evaluation, variant_id, policy_id)
            assert trial.observed_snapshot["authoritative_output_id"] == "o1"
            assert trial.finalization_effects == ("o1",)
            assert trial.completed_request_id == "r1"
            f2 = next(item for item in trial.outcomes if item.startswith("F2:"))
            assert "REJECTED:InvalidTransition" in f2


def test_old_a1_delivery_cannot_supersede_current_or_committed_a2(evaluation):
    for variant_id in ("P3-A", "P3-B", "P3-C"):
        for policy_id in PolicyID:
            trial = _trial(evaluation, variant_id, policy_id)
            assert trial.observed_snapshot["committed_attempt_id"] == "a2"
            assert trial.observed_snapshot["authoritative_output_id"] == "o2"
            assert trial.observed_snapshot["attempt_authority"] == {
                "a1": "SUPERSEDED",
                "a2": "COMMITTED",
            }
            assert trial.finalization_effects == ("o2",)
            old = next(item for item in trial.outcomes if item.startswith("OLD:"))
            assert "REJECTED:" in old


def test_duplicate_event_id_variants_normalize_identity_not_raw_delivery_order(evaluation):
    for variant_id in ("P4-A", "P4-B", "P4-C"):
        for policy_id in PolicyID:
            trial = _trial(evaluation, variant_id, policy_id)
            assert trial.completed_request_id is None
            assert trial.evaluation.metric_opportunities == ()
            events = trial.observed_snapshot["events"]
            assert [item["id"] for item in events] == ["event-1", "event-u"]
            assert events[0]["payload"] == [["result", "SUCCEEDED"]]
            e_outcomes = [item for item in trial.outcomes if item.startswith("E:")]
            assert e_outcomes.count("E:APPLIED") == 1
            assert e_outcomes.count("E:IDEMPOTENT") == 1


def test_stale_binding_loser_cannot_advance_newer_epoch(evaluation):
    for variant_id in ("P5-A", "P5-B"):
        for policy_id in PolicyID:
            trial = _trial(evaluation, variant_id, policy_id)
            snapshot = trial.observed_snapshot
            assert snapshot["current_binding"] == "b2"
            assert snapshot["current_epoch"] == 2
            assert snapshot["bindings"]["b1"]["status"] == "SUPERSEDED"
            assert snapshot["bindings"]["b2"]["status"] == "ACTIVE"
            assert snapshot["bindings"]["b3"]["status"] == "MIGRATING"
            late = next(item for item in trial.outcomes if item.startswith("LATE:"))
            assert "REJECTED:SemanticViolation" in late
            assert trial.completed_request_id is None
            assert trial.evaluation.metric_opportunities == ()


def test_policy_derived_dfr_denominator_exists_only_for_actual_completed_requests(evaluation):
    for trial in evaluation.trials:
        if trial.completed_request_id is None:
            assert trial.evaluation.metric_opportunities == ()
            assert trial.evaluation.metric_opportunity_event_ids == ()
        else:
            assert trial.completed_request_id == "r1"
            assert trial.evaluation.metric_opportunities == (
                CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
            )
            assert trial.evaluation.metric_opportunity_scopes == (
                MetricOpportunityScope.POLICY_DERIVED,
            )
            assert trial.evaluation.metric_opportunity_event_ids[0].endswith(
                ":completed-request:r1"
            )


def test_summary_is_zero_dfr_zero_sser_semantic_null_comparator(evaluation):
    for policy_id in PolicyID:
        summary = _summary(evaluation, policy_id)
        dfr = _rate(summary, CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert summary.operation_count == 14
        assert summary.faulted_operation_count == 14
        assert (dfr.numerator, dfr.denominator) == (0, 9)
        assert (sser.numerator, sser.denominator) == (0, 14)
        assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 14
        assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O3_EXPLICIT_NON_SUCCESS) == 0
        assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0


def test_anti_false_zero_injection_produces_dfr_and_o4_after_real_safe_permutation():
    trial = _run_s5_adversarial_trial(
        PolicyID.B4,
        "P1-B",
        inject_duplicate_finalization=True,
    )
    assert trial.injected_duplicate_finalization is True
    assert trial.finalization_effects == ("o1", "injected-second-output")
    assert "SNAPSHOT_MISMATCH" in trial.invariant_violations
    assert "DUPLICATE_FINALIZATION" in trial.invariant_violations
    assert trial.evaluation.metric_violations == (
        CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
    )
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_public_api_rejects_unknown_variant_and_non_policy_id():
    with pytest.raises(ValueError):
        run_s5_adversarial_trial(PolicyID.B4, "unknown")

    with pytest.raises(TypeError):
        run_s5_adversarial_trial("B4", "P1-A")  # type: ignore[arg-type]
