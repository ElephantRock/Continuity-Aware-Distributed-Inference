from __future__ import annotations

import json

import pytest

from experiments.correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    MetricOpportunityScope,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)
from simulator.policies import PolicyID


def _record(
    *,
    trial_id: str,
    validation_level: ValidationEvidenceLevel = ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
    evidence_provenance: ResultEvidenceProvenance = ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
) -> CorrectnessEvaluationRecord:
    metric = CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE
    return CorrectnessEvaluationRecord.create(
        cohort_id="cohort",
        trial_id=trial_id,
        operation_id=f"op-{trial_id}",
        policy_id=PolicyID.B4,
        scenario_id="FTR1",
        validation_level=validation_level,
        evidence_provenance=evidence_provenance,
        ground_truth={"current_attempt": "a2"},
        observed_evidence={"attempt": "a1"},
        policy_decision={"trace": [{"action": "REJECT"}]},
        semantic_result=SemanticResult(True, True, True),
        metric_opportunities=(metric,),
        metric_opportunity_event_ids=(f"stale-result:{trial_id}",),
        metric_opportunity_scopes=(MetricOpportunityScope.EXOGENOUS_PAIRED,),
        fault_id=f"fault-{trial_id}",
        fault_class="LATE_SUPERSEDED_ATTEMPT",
    )


def test_summary_rejects_mixed_validation_levels():
    ev0 = _record(trial_id="ev0")
    ev3 = _record(
        trial_id="ev3",
        validation_level=ValidationEvidenceLevel.EV3_CALIBRATED_SIMULATION,
    )

    with pytest.raises(ValueError, match="one evidence stratum"):
        summarize_correctness((ev0, ev3))


def test_summary_rejects_mixed_result_provenance():
    synthetic = _record(trial_id="synthetic")
    estimated = _record(
        trial_id="estimated",
        evidence_provenance=ResultEvidenceProvenance.ESTIMATED,
    )

    with pytest.raises(ValueError, match="one evidence stratum"):
        summarize_correctness((synthetic, estimated))


def test_summary_serializes_evidence_stratum_and_fingerprint_depends_on_it():
    synthetic = summarize_correctness((_record(trial_id="same"),))
    estimated = summarize_correctness(
        (
            _record(
                trial_id="same",
                evidence_provenance=ResultEvidenceProvenance.ESTIMATED,
            ),
        )
    )

    assert synthetic.to_dict()["validation_level"] == "EV0"
    assert synthetic.to_dict()["evidence_provenance"] == "SYNTHETICALLY_GENERATED"
    assert estimated.to_dict()["evidence_provenance"] == "ESTIMATED"
    assert synthetic.fingerprint != estimated.fingerprint


def test_deserializer_rejects_missing_metric_violations_field():
    payload = json.loads(_record(trial_id="missing").to_json())
    del payload["metric_violations"]

    with pytest.raises(ValueError, match="missing=.*metric_violations"):
        CorrectnessEvaluationRecord.from_dict(payload)


def test_deserializer_rejects_misspelled_safety_field_instead_of_defaulting_safe():
    payload = json.loads(_record(trial_id="misspelled").to_json())
    payload["metric_violation"] = payload.pop("metric_violations")

    with pytest.raises(ValueError, match="unexpected=.*metric_violation"):
        CorrectnessEvaluationRecord.from_dict(payload)


def test_deserializer_rejects_duplicate_top_level_json_members_before_last_value_wins():
    record = _record(trial_id="duplicate-top")
    payload = record.to_json()
    needle = '"metric_violations":[]'
    tampered = payload.replace(
        needle,
        '"metric_violations":["Stale Attempt Acceptance Rate"],' + needle,
        1,
    )

    with pytest.raises(ValueError, match="duplicate JSON member: metric_violations"):
        CorrectnessEvaluationRecord.from_json(tampered)


def test_deserializer_rejects_duplicate_nested_json_members():
    record = _record(trial_id="duplicate-nested")
    payload = record.to_json()
    needle = '"ground_truth":{"current_attempt":"a2"}'
    tampered = payload.replace(
        needle,
        '"ground_truth":{"current_attempt":"a1","current_attempt":"a2"}',
        1,
    )

    with pytest.raises(ValueError, match="duplicate JSON member: current_attempt"):
        CorrectnessEvaluationRecord.from_json(tampered)
