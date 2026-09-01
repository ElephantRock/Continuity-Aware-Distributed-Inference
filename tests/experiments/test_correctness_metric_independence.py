from __future__ import annotations

import pytest

from experiments.correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    MetricOpportunityScope,
    OutcomeClass,
    RecoveryAction,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)
from simulator.policies import PolicyID


def _rate(summary, metric: CorrectnessMetric, policy_id: PolicyID = PolicyID.B4):
    policy_summary = next(
        item for item in summary.policy_summaries if item.policy_id is policy_id
    )
    return next(rate for rate in policy_summary.rates if rate.metric is metric)


def _record_with_metric_events(
    *,
    opportunities: tuple[CorrectnessMetric, ...],
    violations: tuple[CorrectnessMetric, ...],
) -> CorrectnessEvaluationRecord:
    opportunity_ids = tuple(f"stale-result:a{index + 1}" for index in range(len(opportunities)))
    violation_ids = opportunity_ids[: len(violations)]
    return CorrectnessEvaluationRecord.create(
        cohort_id="cohort",
        trial_id="event-cardinality",
        operation_id="operation",
        policy_id=PolicyID.B4,
        scenario_id="S1-attempt-fencing",
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth={"current_attempt": "a2"},
        observed_evidence={"late_attempt": "a1"},
        policy_decision={"trace": [{"action": "EVALUATE"}]},
        semantic_result=SemanticResult(True, True, True),
        metric_opportunities=opportunities,
        metric_opportunity_event_ids=opportunity_ids,
        metric_opportunity_scopes=tuple(
            MetricOpportunityScope.EXOGENOUS_PAIRED for _ in opportunities
        ),
        metric_violations=violations,
        metric_violation_event_ids=violation_ids,
        fault_id="fault",
        fault_class="LATE_SUPERSEDED_ATTEMPT",
    )


def test_gate_metric_violation_is_independent_from_o4_outcome_class():
    metric = CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE
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
        metric_opportunities=(metric,),
        metric_opportunity_event_ids=("state-consumption:1",),
        metric_opportunity_scopes=(MetricOpportunityScope.POLICY_DERIVED,),
        metric_violations=(metric,),
        metric_violation_event_ids=("state-consumption:1",),
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


def test_gate_event_multiset_preserves_multiple_events_within_one_operation():
    metric = CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE
    record = _record_with_metric_events(
        opportunities=(metric, metric, metric),
        violations=(metric, metric),
    )

    assert record.metric_opportunities == (metric, metric, metric)
    assert record.metric_violations == (metric, metric)
    assert record.metric_opportunity_event_ids == (
        "stale-result:a1",
        "stale-result:a2",
        "stale-result:a3",
    )
    assert record.metric_violation_event_ids == (
        "stale-result:a1",
        "stale-result:a2",
    )

    rate = _rate(summarize_correctness((record,)), metric)
    assert (rate.numerator, rate.denominator, rate.rate) == (2, 3, 2 / 3)


def test_gate_violation_must_reference_an_existing_opportunity_event():
    metric = CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE
    with pytest.raises(ValueError, match="matching metric opportunity event"):
        CorrectnessEvaluationRecord.create(
            cohort_id="cohort",
            trial_id="invalid-event",
            operation_id="operation",
            policy_id=PolicyID.B4,
            scenario_id="S1-attempt-fencing",
            validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
            evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
            ground_truth={"current_attempt": "a2"},
            observed_evidence={"late_attempt": "a1"},
            policy_decision={"trace": [{"action": "EVALUATE"}]},
            semantic_result=SemanticResult(True, True, True),
            metric_opportunities=(metric,),
            metric_opportunity_event_ids=("stale-result:a1",),
            metric_opportunity_scopes=(MetricOpportunityScope.EXOGENOUS_PAIRED,),
            metric_violations=(metric,),
            metric_violation_event_ids=("stale-result:not-presented",),
            fault_id="fault",
            fault_class="LATE_SUPERSEDED_ATTEMPT",
        )


def test_behavior_dependent_gate_opportunities_may_differ_across_paired_policies():
    metric = CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE
    common = dict(
        cohort_id="cohort",
        trial_id="paired-behavior",
        operation_id="operation",
        scenario_id="S2-state-lineage",
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth={"presented_state_compatible": False},
        fault_id="fault",
        fault_class="WRONG_SIBLING_STATE_PRESENTED",
    )
    b0 = CorrectnessEvaluationRecord.create(
        policy_id=PolicyID.B0,
        observed_evidence={"state": "wrong-sibling"},
        policy_decision={"trace": [{"action": "CONSUME"}]},
        semantic_result=SemanticResult(True, True, False),
        metric_opportunities=(metric,),
        metric_opportunity_event_ids=("b0:state-consumption:1",),
        metric_opportunity_scopes=(MetricOpportunityScope.POLICY_DERIVED,),
        metric_violations=(metric,),
        metric_violation_event_ids=("b0:state-consumption:1",),
        **common,
    )
    b4 = CorrectnessEvaluationRecord.create(
        policy_id=PolicyID.B4,
        observed_evidence={"state": "wrong-sibling"},
        policy_decision={"trace": [{"action": "REJECT"}, {"action": "RECOMPUTE"}]},
        semantic_result=SemanticResult(
            True,
            True,
            True,
            recovery_actions=(RecoveryAction.RECOMPUTE,),
        ),
        metric_opportunities=(),
        metric_opportunity_event_ids=(),
        metric_opportunity_scopes=(),
        metric_violations=(),
        metric_violation_event_ids=(),
        **common,
    )

    summary = summarize_correctness((b0, b4))
    b0_rate = _rate(summary, metric, PolicyID.B0)
    b4_rate = _rate(summary, metric, PolicyID.B4)

    assert (b0_rate.numerator, b0_rate.denominator, b0_rate.rate) == (1, 1, 1.0)
    assert (b4_rate.numerator, b4_rate.denominator, b4_rate.rate) == (0, 0, None)
