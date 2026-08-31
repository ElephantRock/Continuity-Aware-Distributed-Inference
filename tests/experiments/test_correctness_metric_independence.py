from __future__ import annotations

from experiments.correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    OutcomeClass,
    RecoveryAction,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)
from simulator.policies import PolicyID


def _rate(summary, metric: CorrectnessMetric):
    policy_summary = summary.policy_summaries[0]
    return next(rate for rate in policy_summary.rates if rate.metric is metric)


def test_gate_metric_violation_is_independent_from_o4_outcome_class():
    record = CorrectnessEvaluationRecord.create(
        cohort_id="cohort",
        trial_id="recovered-wrong-state",
        operation_id="consume-state",
        policy_id=PolicyID.B4,
        scenario_id="S2-state-lineage",
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth={"state_compatible": False},
        observed_evidence={"state_id": "wrong-sibling"},
        policy_decision={"trace": [{"action": "CONSUME"}, {"action": "RECOMPUTE"}]},
        semantic_result=SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=True,
            recovery_actions=(RecoveryAction.RECOMPUTE,),
        ),
        metric_opportunities=(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE,),
        metric_violations=(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE,),
        fault_id="fault-wrong-state",
        fault_class="WRONG_SIBLING_STATE_PRESENTED",
    )

    assert record.outcome_class is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY

    summary = summarize_correctness((record,))
    wscr = _rate(summary, CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
    sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
    recovery = _rate(summary, CorrectnessMetric.RECOVERY_RATE)

    assert (wscr.numerator, wscr.denominator, wscr.rate) == (1, 1, 1.0)
    assert (sser.numerator, sser.denominator, sser.rate) == (0, 1, 0.0)
    assert (recovery.numerator, recovery.denominator, recovery.rate) == (1, 1, 1.0)


def test_o4_can_exist_without_attributing_a_specific_gate_metric():
    record = CorrectnessEvaluationRecord.create(
        cohort_id="cohort",
        trial_id="generic-o4",
        operation_id="finalize",
        policy_id=PolicyID.B4,
        scenario_id="generic-safety",
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth={"expected": "correct"},
        observed_evidence={"reported": "wrong"},
        policy_decision={"trace": [{"action": "COMMIT"}]},
        semantic_result=SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        ),
        fault_id="fault-generic",
        fault_class="GENERIC_SEMANTIC_CORRUPTION",
    )

    summary = summarize_correctness((record,))
    sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)

    assert record.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
    assert (sser.numerator, sser.denominator, sser.rate) == (1, 1, 1.0)
    for metric in (
        CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,
        CorrectnessMetric.WRONG_BRANCH_REUSE_RATE,
        CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE,
        CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,
        CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,
        CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
    ):
        rate = _rate(summary, metric)
        assert (rate.numerator, rate.denominator, rate.rate) == (0, 0, None)
