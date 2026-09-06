import os

import pytest

from experiments.correctness import (
    CorrectnessMetric,
    OutcomeClass,
    ResultEvidenceProvenance,
    ValidationEvidenceLevel,
)
from experiments.evidence_safety_e1 import (
    S4_E1_COHORT_ID,
    S4_E1_CRASH_EXIT_CODE,
    S4_E1_MIN_CPU_SECONDS,
    S4_E1_SCENARIOS,
    S4_E1_SCENARIO_IDS,
    S4_E1_SCHEMA,
    S4_E1_START_METHOD,
    S4_E1_WORKER_IDS,
    _SPEC_BY_ID,
    _run_case,
    run_s4_e1_paired,
    run_s4_e1_trial,
)
from continuity.entities import ReconcileOutcome
from simulator import PolicyID


_FAULTED = tuple(
    scenario.scenario_id for scenario in S4_E1_SCENARIOS if scenario.fault_id is not None
)


@pytest.fixture(scope="module")
def evaluation():
    return run_s4_e1_paired()


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


def test_s4_e1_manifest_is_canonical_bounded_and_predeclares_five_faults():
    assert S4_E1_START_METHOD == "spawn"
    assert S4_E1_WORKER_IDS == ("w1", "w2", "w3")
    assert len(S4_E1_SCENARIOS) == 7
    assert S4_E1_SCENARIO_IDS == tuple(item.scenario_id for item in S4_E1_SCENARIOS)
    assert len(set(S4_E1_SCENARIO_IDS)) == 7
    assert len(_FAULTED) == 5
    assert sum(item.semantic_commit_allowed for item in S4_E1_SCENARIOS) == 2
    assert all(
        (item.fault_id is None) == (item.acr_event_id is None)
        for item in S4_E1_SCENARIOS
    )


def test_s4_e1_paired_order_validation_level_and_measured_provenance(evaluation):
    assert tuple(
        (trial.scenario_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (scenario.scenario_id, policy_id)
        for scenario in S4_E1_SCENARIOS
        for policy_id in PolicyID
    )

    for trial in evaluation.trials:
        record = trial.evaluation
        assert record.cohort_id == S4_E1_COHORT_ID
        assert record.validation_level is ValidationEvidenceLevel.EV1_MEASURED_CPU_DISTRIBUTED
        assert record.evidence_provenance is ResultEvidenceProvenance.MEASURED
        assert record.ground_truth["schema"] == S4_E1_SCHEMA
        assert record.ground_truth["start_method"] == "spawn"
        assert record.ground_truth["ipc_transport"] == "multiprocessing.Pipe"
        assert record.policy_decision["workers_cannot_mutate_c1"] is True
        assert record.policy_decision["c1_commit_is_authoritative_not_policy_decision"] is True


def test_s4_e1_uses_real_distinct_worker_processes_and_measured_cpu(evaluation):
    coordinator_pid = os.getpid()
    for trial in evaluation.trials:
        assert len(trial.worker_process_ids) == 3
        assert len(set(trial.worker_process_ids)) == 3
        assert coordinator_pid not in trial.worker_process_ids
        assert all(pid > 0 for pid in trial.worker_process_ids)

        measured_work = [
            event
            for event in trial.physical_events
            if event.get("kind") in {"OBSERVATION", "CRASHING"}
        ]
        assert measured_work
        for event in measured_work:
            assert int(event["worker_pid"]) in trial.worker_process_ids
            assert float(event["process_cpu_seconds"]) >= S4_E1_MIN_CPU_SECONDS
            assert float(event["wall_execution_seconds"]) > 0.0
            assert float(event["completed_at"]) >= float(event["started_at"])
            assert float(event["delivered_at"]) >= float(event["completed_at"])
            assert isinstance(event["cpu_digest"], str)
            assert len(event["cpu_digest"]) == 64


def test_s4_e1_replays_same_measured_physical_events_across_policies(evaluation):
    for scenario_id in S4_E1_SCENARIO_IDS:
        trials = [_trial(evaluation, scenario_id, policy_id) for policy_id in PolicyID]
        assert all(
            item.physical_events == trials[0].physical_events for item in trials[1:]
        )
        assert all(
            item.worker_process_ids == trials[0].worker_process_ids for item in trials[1:]
        )


def test_ground_truth_and_fixed_exogenous_acr_denominators_are_policy_invariant(evaluation):
    for scenario_id in _FAULTED:
        records = [
            _trial(evaluation, scenario_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        assert len({record.ground_truth_json for record in records}) == 1
        assert len({record.exogenous_opportunity_signature for record in records}) == 1
        signature = records[0].exogenous_opportunity_signature
        assert len(signature) == 1
        assert signature[0][0] == CorrectnessMetric.AMBIGUOUS_COMMIT_RATE.value


def test_contradictory_real_workers_reconcile_ambiguous_and_fail_closed(evaluation):
    scenario_id = "E1-S4-A-CONTRADICTORY-WORKERS"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.observed_reconciliation is ReconcileOutcome.AMBIGUOUS
        assert trial.reconciliation_diverged_from_oracle is False
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.presentation.authoritative_commit is False
        assert trial.presentation.after_request_status == "RUNNING"
        assert trial.presentation.after_attempt_authority == "CURRENT"
        assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS
        assert CorrectnessMetric.AMBIGUOUS_COMMIT_RATE not in trial.evaluation.metric_violations

        objects = [
            item
            for item in trial.evaluation.observed_evidence["evidence_objects"]
            if item["id"] in trial.evidence_ids
        ]
        assert len(objects) == 2
        assert {item["claim_key"] for item in objects} == {
            "attempt:a1:terminal-outcome"
        }
        assert {item["claim_value"] for item in objects} == {"SUCCEEDED", "FAILED"}
        assert len({int(event["worker_pid"]) for event in trial.physical_events if event.get("kind") == "OBSERVATION"}) == 2


def test_explicit_ambiguous_real_worker_fails_closed(evaluation):
    scenario_id = "E1-S4-B-EXPLICIT-AMBIGUOUS-WORKER"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.observed_reconciliation is ReconcileOutcome.AMBIGUOUS
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.presentation.authoritative_commit is False
        objects = trial.evaluation.observed_evidence["evidence_objects"]
        assert len(objects) == 1
        assert objects[0]["status"] == "AMBIGUOUS"


def test_expired_measured_observation_waits_and_does_not_commit(evaluation):
    scenario_id = "E1-S4-C-EXPIRED-MEASURED-OBSERVATION"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.observed_reconciliation is ReconcileOutcome.WAIT
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.presentation.authoritative_commit is False
        evidence = trial.evaluation.observed_evidence["evidence_objects"][0]
        assert float(evidence["valid_until"]) < float(evidence["observed_at"])
        assert float(evidence["valid_until"]) < float(
            trial.evaluation.observed_evidence["commit_now"]
        )


def test_controlled_worker_crash_produces_no_evidence_and_no_commit(evaluation):
    scenario_id = "E1-S4-D-WORKER-CRASH-NO-EVIDENCE"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        crash = [
            event for event in trial.physical_events if event.get("kind") == "CRASHING"
        ]
        exits = [
            event for event in trial.physical_events if event.get("kind") == "PROCESS_EXIT"
        ]
        assert len(crash) == 1
        assert len(exits) == 1
        assert crash[0]["worker_id"] == "w2"
        assert exits[0]["worker_id"] == "w2"
        assert exits[0]["exit_code"] == S4_E1_CRASH_EXIT_CODE
        assert trial.evidence_ids == ()
        assert trial.evaluation.observed_evidence["evidence_objects"] == []
        assert trial.observed_reconciliation is ReconcileOutcome.WAIT
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.authoritative_commit is False


def test_wrong_scope_real_worker_waits_and_does_not_commit(evaluation):
    scenario_id = "E1-S4-E-WRONG-SCOPE-WORKER"
    for policy_id in PolicyID:
        trial = _trial(evaluation, scenario_id, policy_id)
        assert trial.observed_reconciliation is ReconcileOutcome.WAIT
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.presentation.authoritative_commit is False
        evidence = trial.evaluation.observed_evidence["evidence_objects"][0]
        assert ["attempt", "other-attempt"] in evidence["scope"]
        assert evidence["claim_key"] == "attempt:other-attempt:terminal-outcome"


def test_real_worker_positive_controls_remain_functional(evaluation):
    for scenario_id in (
        "E1-S4-F-AGREEING-WORKERS-CONTROL",
        "E1-S4-G-SINGLE-VALID-WORKER-CONTROL",
    ):
        for policy_id in PolicyID:
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.observed_reconciliation is ReconcileOutcome.MATCHED
            assert trial.reconciliation_diverged_from_oracle is False
            assert trial.presentation.commit_outcome == "APPLIED"
            assert trial.presentation.error_type is None
            assert trial.presentation.authoritative_commit is True
            assert trial.presentation.after_request_status == "COMPLETED"
            assert trial.presentation.after_attempt_authority == "COMMITTED"
            assert trial.evaluation.fault_id is None
            assert trial.evaluation.metric_opportunities == ()


def test_b4_gets_frozen_evidence_fields_while_b0_b3_do_not(evaluation):
    relevant = {
        "evidence_authority",
        "evidence_status",
        "evidence_freshness",
        "reconciliation",
    }
    for scenario_id in S4_E1_SCENARIO_IDS:
        b4 = _trial(evaluation, scenario_id, PolicyID.B4)
        assert set(b4.policy_visible_evidence["evidence_fields"]) == relevant
        for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.policy_visible_evidence["evidence_fields"] == {}


def test_s4_e1_summary_is_zero_acr_zero_sser_semantic_null_comparator(evaluation):
    for policy_id in PolicyID:
        summary = _summary(evaluation, policy_id)
        acr = _rate(summary, CorrectnessMetric.AMBIGUOUS_COMMIT_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert summary.operation_count == 7
        assert summary.faulted_operation_count == 5
        assert (acr.numerator, acr.denominator) == (0, 5)
        assert (sser.numerator, sser.denominator) == (0, 5)
        assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O3_EXPLICIT_NON_SUCCESS) == 5
        assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0


def test_anti_false_zero_real_worker_rejection_can_be_injected_as_acr_o4():
    scenario = _SPEC_BY_ID["E1-S4-A-CONTRADICTORY-WORKERS"]
    trial = _run_case(
        scenario,
        (PolicyID.B4,),
        inject_divergence=True,
    )[0]
    assert trial.observed_reconciliation is ReconcileOutcome.AMBIGUOUS
    assert trial.presentation.commit_outcome == "REJECTED"
    assert trial.presentation.error_type == "InsufficientEvidence"
    assert trial.presentation.authoritative_commit is True
    assert trial.presentation.diverged_from_oracle is True
    assert CorrectnessMetric.AMBIGUOUS_COMMIT_RATE in trial.evaluation.metric_violations
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_public_trial_api_rejects_unknown_scenarios_and_non_policy_ids():
    with pytest.raises(ValueError):
        run_s4_e1_trial(PolicyID.B4, "unknown")

    with pytest.raises(TypeError):
        run_s4_e1_trial("B4", S4_E1_SCENARIO_IDS[0])  # type: ignore[arg-type]
