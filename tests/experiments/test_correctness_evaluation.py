from __future__ import annotations

import json

import pytest

from experiments.correctness import (
    CORRECTNESS_EVALUATION_SCHEMA,
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    ExplicitNonSuccess,
    OutcomeClass,
    RecoveryAction,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)
from simulator.policies import PolicyID


def _record(
    *,
    trial_id: str = "trial",
    policy_id: PolicyID = PolicyID.B4,
    semantic_result: SemanticResult | None = None,
    opportunities: tuple[CorrectnessMetric, ...] = (),
    violations: tuple[CorrectnessMetric, ...] = (),
    faulted: bool = True,
    validation_level: ValidationEvidenceLevel = ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
    evidence_provenance: ResultEvidenceProvenance = ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
) -> CorrectnessEvaluationRecord:
    return CorrectnessEvaluationRecord.create(
        trial_id=trial_id,
        policy_id=policy_id,
        scenario_id="FTR1" if faulted else "W1",
        validation_level=validation_level,
        evidence_provenance=evidence_provenance,
        ground_truth={
            "active_attempt_id": "a2",
            "state_compatibility": {"x1": False},
            "binding_epoch": 2,
        },
        observed_evidence={"attempt_id": "a1", "binding_epoch": 1},
        policy_decision={"action": "REJECT"},
        semantic_result=semantic_result
        or SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=True,
        ),
        metric_opportunities=opportunities,
        metric_violations=violations,
        fault_id="fault-1" if faulted else None,
        fault_class="LATE_SUPERSEDED_ATTEMPT" if faulted else None,
    )


def _rate(summary, policy_id: PolicyID, metric: CorrectnessMetric):
    policy = next(item for item in summary.policy_summaries if item.policy_id is policy_id)
    return next(item for item in policy.rates if item.metric is metric)


def test_semantic_result_classifies_all_four_failure_outcomes():
    transparent = SemanticResult(True, True, True)
    degraded = SemanticResult(
        True,
        True,
        True,
        recovery_actions=(RecoveryAction.RETRY, RecoveryAction.RECOMPUTE),
    )
    explicit = SemanticResult(False, False, None, ExplicitNonSuccess.WAIT)
    silent = SemanticResult(True, True, False)

    assert transparent.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY
    assert degraded.outcome_class is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY
    assert explicit.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS
    assert silent.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_explicit_non_success_is_not_counted_as_silent_semantic_error():
    record = _record(
        semantic_result=SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.AMBIGUOUS,
        ),
        opportunities=(CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,),
    )
    summary = summarize_correctness((record,))

    sser = _rate(summary, PolicyID.B4, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
    explicit = _rate(summary, PolicyID.B4, CorrectnessMetric.EXPLICIT_NON_SUCCESS_RATE)
    ambiguous_commit = _rate(
        summary, PolicyID.B4, CorrectnessMetric.AMBIGUOUS_COMMIT_RATE
    )

    assert (sser.numerator, sser.denominator, sser.rate) == (0, 1, 0.0)
    assert (explicit.numerator, explicit.denominator, explicit.rate) == (1, 1, 1.0)
    assert (ambiguous_commit.numerator, ambiguous_commit.denominator, ambiguous_commit.rate) == (
        0,
        1,
        0.0,
    )


def test_gate_metric_uses_explicit_opportunity_denominator():
    safe = _record(
        trial_id="safe",
        opportunities=(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,),
    )
    violation = _record(
        trial_id="violation",
        semantic_result=SemanticResult(True, True, False),
        opportunities=(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,),
        violations=(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,),
    )
    unrelated = _record(
        trial_id="unrelated",
        opportunities=(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE,),
    )
    summary = summarize_correctness((safe, violation, unrelated))

    saar = _rate(summary, PolicyID.B4, CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
    assert (saar.numerator, saar.denominator, saar.rate) == (1, 2, 0.5)


def test_zero_opportunity_denominator_is_explicit_none_not_zero():
    summary = summarize_correctness((_record(),))
    rate = _rate(summary, PolicyID.B4, CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)

    assert rate.numerator == 0
    assert rate.denominator == 0
    assert rate.rate is None


def test_control_trial_does_not_inflate_faulted_outcome_denominators():
    control = _record(trial_id="control", faulted=False)
    faulted = _record(
        trial_id="faulted",
        semantic_result=SemanticResult(False, False, None, ExplicitNonSuccess.REJECT),
    )
    summary = summarize_correctness((control, faulted))
    policy = summary.policy_summaries[0]

    assert policy.trial_count == 2
    assert policy.faulted_trial_count == 1
    assert _rate(
        summary, PolicyID.B4, CorrectnessMetric.EXPLICIT_NON_SUCCESS_RATE
    ).denominator == 1
    assert _rate(
        summary, PolicyID.B4, CorrectnessMetric.RECOVERY_RATE
    ).denominator == 1


def test_validation_level_and_result_provenance_are_orthogonal_and_complete():
    assert {item.value for item in ValidationEvidenceLevel} == {
        "EV0", "EV1", "EV2", "EV3", "EV4"
    }
    assert {item.value for item in ResultEvidenceProvenance} == {
        "MEASURED",
        "SIMULATED",
        "TRACE_DERIVED",
        "SYNTHETICALLY_GENERATED",
        "ANALYTICALLY_DERIVED",
        "ESTIMATED",
    }

    record = _record(
        validation_level=ValidationEvidenceLevel.EV3_CALIBRATED_SIMULATION,
        evidence_provenance=ResultEvidenceProvenance.ESTIMATED,
    )
    payload = record.to_dict()
    assert payload["validation_level"] == "EV3"
    assert payload["evidence_provenance"] == "ESTIMATED"


def test_canonical_mapping_snapshot_is_detached_and_fingerprint_order_independent():
    ground_truth = {"z": [3, 2, 1], "a": {"epoch": 4}}
    first = CorrectnessEvaluationRecord.create(
        trial_id="trial",
        policy_id=PolicyID.B4,
        scenario_id="FTR9",
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth=ground_truth,
        observed_evidence={"b": 2, "a": 1},
        policy_decision={"decision": "WAIT"},
        semantic_result=SemanticResult(False, False, None, ExplicitNonSuccess.WAIT),
        fault_id="f",
        fault_class="STALE_BINDING",
    )
    ground_truth["a"]["epoch"] = 99

    second = CorrectnessEvaluationRecord.create(
        trial_id="trial",
        policy_id=PolicyID.B4,
        scenario_id="FTR9",
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth={"a": {"epoch": 4}, "z": [3, 2, 1]},
        observed_evidence={"a": 1, "b": 2},
        policy_decision={"decision": "WAIT"},
        semantic_result=SemanticResult(False, False, None, ExplicitNonSuccess.WAIT),
        fault_id="f",
        fault_class="STALE_BINDING",
    )

    assert first.ground_truth["a"]["epoch"] == 4
    assert first.to_json() == second.to_json()
    assert first.fingerprint == second.fingerprint


def test_evaluation_record_round_trip_preserves_all_required_views_and_evidence_dimensions():
    record = _record(
        opportunities=(CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,),
    )
    restored = CorrectnessEvaluationRecord.from_json(record.to_json())

    assert restored == record
    assert restored.ground_truth == record.ground_truth
    assert restored.observed_evidence == record.observed_evidence
    assert restored.policy_decision == record.policy_decision
    assert restored.semantic_result == record.semantic_result
    assert restored.validation_level is record.validation_level
    assert restored.evidence_provenance is record.evidence_provenance
    assert restored.to_dict()["schema"] == CORRECTNESS_EVALUATION_SCHEMA


def test_round_trip_rejects_tampered_outcome_class():
    record = _record()
    payload = json.loads(record.to_json())
    payload["outcome_class"] = "O4"

    with pytest.raises(ValueError, match="outcome_class"):
        CorrectnessEvaluationRecord.from_dict(payload)


def test_metric_violation_must_have_matching_opportunity():
    with pytest.raises(ValueError, match="subset"):
        _record(
            violations=(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE,),
        )


def test_gate_metric_opportunity_requires_faulted_trial():
    with pytest.raises(ValueError, match="faulted trial"):
        _record(
            faulted=False,
            opportunities=(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,),
        )


def test_nonfinite_ground_truth_is_rejected_before_serialization():
    with pytest.raises(ValueError, match="non-finite"):
        CorrectnessEvaluationRecord.create(
            trial_id="trial",
            policy_id=PolicyID.B4,
            scenario_id="FTR1",
            validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
            evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
            ground_truth={"value": float("nan")},
            observed_evidence={},
            policy_decision={},
            semantic_result=SemanticResult(True, True, True),
            fault_id="f",
            fault_class="FAULT",
        )


def test_duplicate_policy_trial_identity_cannot_be_double_counted():
    first = _record()
    duplicate = _record()

    with pytest.raises(ValueError, match="duplicate"):
        summarize_correctness((first, duplicate))


def test_summary_policy_order_is_canonical_independent_of_input_order():
    b4 = _record(trial_id="b4", policy_id=PolicyID.B4)
    b1 = _record(trial_id="b1", policy_id=PolicyID.B1)
    b0 = _record(trial_id="b0", policy_id=PolicyID.B0)

    summary = summarize_correctness((b4, b1, b0))

    assert tuple(item.policy_id for item in summary.policy_summaries) == (
        PolicyID.B0,
        PolicyID.B1,
        PolicyID.B4,
    )
    assert summary.fingerprint == summarize_correctness((b0, b4, b1)).fingerprint
