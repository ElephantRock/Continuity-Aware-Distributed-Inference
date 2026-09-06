import pytest

from continuity.entities import (
    BindingStatus,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionContext,
    ReconcileOutcome,
)
from continuity.errors import InsufficientEvidence, SemanticViolation
from experiments.correctness import CorrectnessMetric, OutcomeClass
from experiments.evidence_safety import _scaffold_core
from experiments.evidence_safety_adversarial import (
    S4_ADVERSARIAL_CASE_IDS,
    S4_ADVERSARIAL_COHORT_ID,
    S4_ADVERSARIAL_MANIFESTS,
    S4_ADVERSARIAL_SCHEMA,
    _record,
    _run_s4_adversarial_trial,
    run_s4_adversarial_paired,
    run_s4_adversarial_trial,
)
from simulator import PolicyID


_FAULTED = tuple(
    manifest.case_id
    for manifest in S4_ADVERSARIAL_MANIFESTS
    if manifest.fault_id is not None
)


@pytest.fixture(scope="module")
def evaluation():
    return run_s4_adversarial_paired()


def _trial(evaluation, case_id, policy_id):
    return next(
        trial
        for trial in evaluation.trials
        if trial.manifest.case_id == case_id and trial.policy_id is policy_id
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


def test_manifest_is_canonical_bounded_and_predeclares_six_faults():
    assert len(S4_ADVERSARIAL_MANIFESTS) == 11
    assert S4_ADVERSARIAL_CASE_IDS == tuple(
        item.case_id for item in S4_ADVERSARIAL_MANIFESTS
    )
    assert len(set(S4_ADVERSARIAL_CASE_IDS)) == 11
    assert len(_FAULTED) == 6
    assert sum(item.semantic_commit_allowed for item in S4_ADVERSARIAL_MANIFESTS) == 5


def test_paired_order_ground_truth_and_exogenous_denominators_are_policy_invariant(
    evaluation,
):
    assert tuple(
        (trial.manifest.case_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (manifest.case_id, policy_id)
        for manifest in S4_ADVERSARIAL_MANIFESTS
        for policy_id in PolicyID
    )

    for case_id in S4_ADVERSARIAL_CASE_IDS:
        records = [
            _trial(evaluation, case_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        assert all(record.cohort_id == S4_ADVERSARIAL_COHORT_ID for record in records)
        assert all(record.ground_truth["schema"] == S4_ADVERSARIAL_SCHEMA for record in records)
        assert len({record.ground_truth_json for record in records}) == 1
        if case_id in _FAULTED:
            assert len({record.exogenous_opportunity_signature for record in records}) == 1
            signature = records[0].exogenous_opportunity_signature
            assert len(signature) == 1
            assert signature[0][0] == CorrectnessMetric.AMBIGUOUS_COMMIT_RATE.value


def test_repaired_contradictory_valid_exact_same_scope_fails_closed(evaluation):
    case_id = "A1-CONTRADICTORY-VALID-EXACT-SAME-SCOPE"
    for policy_id in PolicyID:
        trial = _trial(evaluation, case_id, policy_id)
        assert trial.manifest.oracle_reconciliation is ReconcileOutcome.AMBIGUOUS
        assert trial.observed_reconciliation is ReconcileOutcome.AMBIGUOUS
        assert trial.reconciliation_diverged_from_oracle is False
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.presentation.authoritative_commit is False
        assert trial.presentation.diverged_from_oracle is False
        assert CorrectnessMetric.AMBIGUOUS_COMMIT_RATE not in trial.evaluation.metric_violations
        assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS

        objects = {
            item["claim_value"]: item
            for item in trial.evaluation.observed_evidence["evidence_objects"]
            if item["id"] in trial.evidence_ids
        }
        assert set(objects) == {"SUCCEEDED", "FAILED"}
        assert {
            item["claim_key"] for item in objects.values()
        } == {"attempt:a1:terminal-outcome"}
        assert {
            item["claim"] for item in objects.values()
        } == {
            "attempt-terminal-outcome=SUCCEEDED",
            "attempt-terminal-outcome=FAILED",
        }


def test_identical_structured_exact_claims_remain_positive_control(evaluation):
    case_id = "A2-IDENTICAL-VALID-EXACT-MULTI-CONTROL"
    for policy_id in PolicyID:
        trial = _trial(evaluation, case_id, policy_id)
        assert trial.observed_reconciliation is ReconcileOutcome.MATCHED
        assert trial.reconciliation_diverged_from_oracle is False
        assert trial.presentation.commit_outcome == "APPLIED"
        assert trial.presentation.authoritative_commit is True
        objects = [
            item
            for item in trial.evaluation.observed_evidence["evidence_objects"]
            if item["id"] in trial.evidence_ids
        ]
        assert len(objects) == 2
        assert {item["claim_key"] for item in objects} == {
            "attempt:a1:terminal-outcome"
        }
        assert {item["claim_value"] for item in objects} == {"SUCCEEDED"}


def test_set_level_ambiguous_item_remains_fail_closed_even_when_wrong_scope(evaluation):
    case_id = "A3-VALID-PLUS-AMBIGUOUS-WRONG-SCOPE"
    for policy_id in PolicyID:
        trial = _trial(evaluation, case_id, policy_id)
        assert trial.manifest.oracle_reconciliation is ReconcileOutcome.AMBIGUOUS
        assert trial.observed_reconciliation is ReconcileOutcome.AMBIGUOUS
        assert trial.reconciliation_diverged_from_oracle is False
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.presentation.authoritative_commit is False
        assert trial.presentation.diverged_from_oracle is False
        assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS


def test_freshness_boundaries_and_old_observation_contract(evaluation):
    for policy_id in PolicyID:
        equal_now = _trial(
            evaluation, "B1-VALID-UNTIL-EQUAL-NOW-CONTROL", policy_id
        )
        assert equal_now.observed_reconciliation is ReconcileOutcome.MATCHED
        assert equal_now.presentation.commit_outcome == "APPLIED"
        assert equal_now.presentation.authoritative_commit is True

        expired = _trial(
            evaluation, "B2-VALID-UNTIL-EPSILON-EXPIRED", policy_id
        )
        assert expired.observed_reconciliation is ReconcileOutcome.WAIT
        assert expired.presentation.commit_outcome == "REJECTED"
        assert expired.presentation.authoritative_commit is False

        old = _trial(
            evaluation, "B3-OLD-OBSERVATION-NO-EXPIRY-CONTROL", policy_id
        )
        assert old.observed_reconciliation is ReconcileOutcome.MATCHED
        assert old.presentation.commit_outcome == "APPLIED"
        assert old.presentation.authoritative_commit is True


def test_authority_derivation_and_scope_controls(evaluation):
    for policy_id in PolicyID:
        authoritative = _trial(
            evaluation, "C1-AUTHORITATIVE-VALID-CONTROL", policy_id
        )
        assert authoritative.presentation.commit_outcome == "APPLIED"
        assert authoritative.presentation.authoritative_commit is True

        derived = _trial(
            evaluation, "C2-DERIVED-ONLY-WITH-EXACT-SUPPORT", policy_id
        )
        assert derived.observed_reconciliation is ReconcileOutcome.WAIT
        assert derived.presentation.commit_outcome == "REJECTED"
        assert derived.presentation.authoritative_commit is False
        assert len(derived.evidence_ids) == 1
        assert derived.evidence_ids[0].endswith(":derived")

        mixed_scope = _trial(
            evaluation, "D1-GOOD-PLUS-WRONG-SCOPE-VALID-CONTROL", policy_id
        )
        assert mixed_scope.observed_reconciliation is ReconcileOutcome.MATCHED
        assert mixed_scope.presentation.commit_outcome == "APPLIED"

        wrong_scope = _trial(evaluation, "D2-WRONG-SCOPE-ONLY", policy_id)
        assert wrong_scope.observed_reconciliation is ReconcileOutcome.WAIT
        assert wrong_scope.presentation.commit_outcome == "REJECTED"


def test_empty_evidence_set_waits_and_does_not_finalize(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-EMPTY-EVIDENCE-SET", policy_id)
        assert trial.evidence_ids == ()
        assert trial.observed_reconciliation is ReconcileOutcome.WAIT
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.presentation.authoritative_commit is False
        assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS


def test_only_b4_receives_frozen_evidence_and_reconciliation_information(evaluation):
    relevant = {
        "evidence_authority",
        "evidence_status",
        "evidence_freshness",
        "reconciliation",
    }
    for case_id in S4_ADVERSARIAL_CASE_IDS:
        b4 = _trial(evaluation, case_id, PolicyID.B4)
        assert set(b4.policy_visible_evidence["evidence_fields"]) == relevant
        for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
            trial = _trial(evaluation, case_id, policy_id)
            assert trial.policy_visible_evidence["evidence_fields"] == {}


def test_repaired_adversarial_summary_has_zero_acr_and_zero_sser_per_policy(
    evaluation,
):
    for policy_id in PolicyID:
        summary = _summary(evaluation, policy_id)
        acr = _rate(summary, CorrectnessMetric.AMBIGUOUS_COMMIT_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert summary.operation_count == 11
        assert summary.faulted_operation_count == 6
        assert (acr.numerator, acr.denominator) == (0, 6)
        assert (sser.numerator, sser.denominator) == (0, 6)
        assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O3_EXPLICIT_NON_SUCCESS) == 6
        assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0


def test_anti_false_zero_injection_still_creates_acr_o4_after_real_rejection():
    trial = _run_s4_adversarial_trial(
        PolicyID.B4,
        "E1-EMPTY-EVIDENCE-SET",
        inject_divergence=True,
    )
    assert trial.presentation.commit_outcome == "REJECTED"
    assert trial.presentation.error_type == "InsufficientEvidence"
    assert trial.presentation.authoritative_commit is True
    assert trial.presentation.diverged_from_oracle is True
    assert CorrectnessMetric.AMBIGUOUS_COMMIT_RATE in trial.evaluation.metric_violations
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_derived_valid_cannot_be_recorded_from_non_valid_support():
    core = _scaffold_core()
    support = _record(
        core,
        evidence_id="guard:stale-support",
        claim="support",
        status=EvidenceStatus.STALE,
    )
    with pytest.raises(SemanticViolation):
        _record(
            core,
            evidence_id="guard:derived",
            claim="derived",
            authority=EvidenceAuthority.DERIVED,
            derived_from=frozenset({support.id}),
            derivation_rule="invalid derivation over stale support",
        )


def test_non_derived_evidence_cannot_carry_derivation_provenance():
    core = _scaffold_core()
    support = _record(core, evidence_id="guard:support", claim="support")
    with pytest.raises(SemanticViolation):
        _record(
            core,
            evidence_id="guard:escalated",
            claim="escalated",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            derived_from=frozenset({support.id}),
            derivation_rule="forbidden authority escalation",
        )


def test_conflicting_duplicate_evidence_identity_is_rejected():
    core = _scaffold_core()
    _record(core, evidence_id="guard:duplicate", claim="first")
    with pytest.raises(SemanticViolation):
        _record(core, evidence_id="guard:duplicate", claim="second")


def test_output_cannot_reference_unknown_evidence():
    core = _scaffold_core()
    with pytest.raises(SemanticViolation):
        core.create_output("guard:output", "a1", True, ("guard:missing",))


def test_state_consumption_does_not_union_partial_scopes_across_evidence_items():
    core = _scaffold_core()
    core.create_state("x", origin_type="continuation", origin_id="c")
    core.add_replica("rho", "x", "w1")
    ctx = ExecutionContext("p", "s", "c", "r1", "a1")
    state_evidence = _record(
        core,
        evidence_id="guard:state-only",
        claim="state valid",
        scope=frozenset({("state", "x")}),
    )
    replica_evidence = _record(
        core,
        evidence_id="guard:replica-only",
        claim="replica valid",
        scope=frozenset({("replica", "rho")}),
    )
    assert (
        core.can_consume_state(
            "x",
            "rho",
            ctx,
            (state_evidence.id, replica_evidence.id),
            now=2.0,
        )
        is False
    )


def test_migration_commit_does_not_union_partial_scopes_across_evidence_items():
    core = _scaffold_core()
    core.activate_initial_binding("b1", "subject", "w1")
    b2 = core.propose_binding("b2", "subject", "w2")
    core.begin_migration("b2")
    binding_evidence = _record(
        core,
        evidence_id="guard:binding-only",
        claim="binding observation",
        scope=frozenset({("binding", "b2")}),
    )
    epoch_evidence = _record(
        core,
        evidence_id="guard:epoch-only",
        claim="epoch observation",
        scope=frozenset({("epoch", str(b2.epoch))}),
    )
    with pytest.raises(InsufficientEvidence):
        core.commit_migration(
            "b2",
            (binding_evidence.id, epoch_evidence.id),
            now=2.0,
        )
    assert core.bindings["b1"].status is BindingStatus.ACTIVE
    assert core.bindings["b2"].status is BindingStatus.MIGRATING


def test_public_api_rejects_unknown_cases_and_non_policy_ids():
    with pytest.raises(ValueError):
        run_s4_adversarial_trial(PolicyID.B4, "unknown")

    with pytest.raises(TypeError):
        run_s4_adversarial_trial("B4", S4_ADVERSARIAL_CASE_IDS[0])  # type: ignore[arg-type]
