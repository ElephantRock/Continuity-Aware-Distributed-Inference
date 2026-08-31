from __future__ import annotations

import json

import pytest

from experiments.correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
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
        metric_opportunities=(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,),
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


def test_gate_violation_requires_silent_semantic_violation_outcome():
    with pytest.raises(ValueError, match="O4 silent semantic violation"):
        CorrectnessEvaluationRecord.create(
            cohort_id="cohort",
            trial_id="unsafe-label",
            operation_id="op",
            policy_id=PolicyID.B4,
            scenario_id="FTR1",
            validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
            evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
            ground_truth={"current_attempt": "a2"},
            observed_evidence={"attempt": "a1"},
            policy_decision={"trace": [{"action": "ACCEPT"}]},
            semantic_result=SemanticResult(True, True, True),
            metric_opportunities=(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,),
            metric_violations=(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,),
            fault_id="fault",
            fault_class="LATE_SUPERSEDED_ATTEMPT",
        )
