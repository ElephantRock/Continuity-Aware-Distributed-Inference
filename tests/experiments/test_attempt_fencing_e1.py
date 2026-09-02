from __future__ import annotations

import os

import pytest

from experiments.attempt_fencing_e1 import (
    S1_E1_SCENARIOS,
    S1_E1_SCENARIO_IDS,
    _classify_e1_stale_acceptance,
    run_s1_e1_paired,
)
from experiments.correctness import (
    CorrectnessMetric,
    OutcomeClass,
    ResultEvidenceProvenance,
    ValidationEvidenceLevel,
)
from simulator import PolicyID


@pytest.fixture(scope="module")
def paired_evaluation():
    return run_s1_e1_paired()


def _rate(evaluation, policy_id: PolicyID, metric: CorrectnessMetric):
    policy = next(
        item for item in evaluation.summary.policy_summaries if item.policy_id is policy_id
    )
    return next(item for item in policy.rates if item.metric is metric)


def _trial(evaluation, scenario_id: str, policy_id: PolicyID):
    return next(
        trial
        for trial in evaluation.trials
        if trial.scenario_id == scenario_id and trial.policy_id is policy_id
    )


def test_s1_e1_paired_uses_canonical_scenario_then_b0_b4_order(paired_evaluation):
    assert tuple((item.scenario_id, item.policy_id) for item in paired_evaluation.trials) == tuple(
        (spec.scenario_id, policy_id)
        for spec in S1_E1_SCENARIOS
        for policy_id in PolicyID
    )


def test_s1_e1_is_measured_ev1_and_uses_real_distinct_worker_processes(paired_evaluation):
    for trial in paired_evaluation.trials:
        assert trial.evaluation.validation_level is ValidationEvidenceLevel.EV1_MEASURED_CPU_DISTRIBUTED
        assert trial.evaluation.evidence_provenance is ResultEvidenceProvenance.MEASURED
        observed = trial.evaluation.observed_evidence
        worker_pids = observed["worker_process_ids"]
        expected_workers = len(trial.evaluation.ground_truth["attempt_ids"])
        assert len(worker_pids) == expected_workers
        assert len(set(worker_pids)) == expected_workers
        assert all(pid != observed["coordinator_pid"] for pid in worker_pids)
        assert observed["coordinator_pid"] == os.getpid()
        assert trial.evaluation.ground_truth["start_method"] == "spawn"
        assert trial.evaluation.ground_truth["ipc_transport"] == "multiprocessing.Pipe+Queue"


def test_s1_e1_all_competent_baselines_have_zero_saar_dfr_sser(paired_evaluation):
    stale_denominator = sum(
        len(spec.stale_presentation_event_ids) for spec in S1_E1_SCENARIOS
    )
    assert stale_denominator == 8

    for policy_id in PolicyID:
        saar = _rate(
            paired_evaluation,
            policy_id,
            CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,
        )
        dfr = _rate(
            paired_evaluation,
            policy_id,
            CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
        )
        sser = _rate(
            paired_evaluation,
            policy_id,
            CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE,
        )
        assert (saar.numerator, saar.denominator, saar.rate) == (0, 8, 0.0)
        assert (dfr.numerator, dfr.denominator, dfr.rate) == (0, 6, 0.0)
        assert (sser.numerator, sser.denominator, sser.rate) == (0, 6, 0.0)

        policy_summary = next(
            item for item in paired_evaluation.summary.policy_summaries if item.policy_id is policy_id
        )
        outcome_counts = dict(policy_summary.outcome_counts)
        assert outcome_counts[OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY] == 6
        assert outcome_counts[OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION] == 0


def test_s1_e1_paired_ground_truth_and_exogenous_saar_ids_are_policy_independent(paired_evaluation):
    for scenario_id in S1_E1_SCENARIO_IDS:
        trials = tuple(_trial(paired_evaluation, scenario_id, policy_id) for policy_id in PolicyID)
        assert len({trial.evaluation.ground_truth_json for trial in trials}) == 1
        assert len({trial.evaluation.exogenous_opportunity_signature for trial in trials}) == 1


def test_every_declared_saar_event_reaches_terminal_finalization_and_is_stale(paired_evaluation):
    for trial in paired_evaluation.trials:
        stale_ids = set(trial.stale_result_event_ids)
        observed = trial.evaluation.observed_evidence
        preconditions = {
            item["event_id"]: item
            for item in observed["terminal_presentation_preconditions"]
            if item["event_id"] in stale_ids
        }
        finalizations = {
            item["event_id"]: item
            for item in observed["finalization_records"]
            if item["event_id"] in stale_ids
        }
        presentations = {
            item["event_id"]: item
            for item in observed["terminal_presentations"]
            if item["event_id"] in stale_ids
        }
        assert set(preconditions) == stale_ids
        assert set(finalizations) == stale_ids
        assert set(presentations) == stale_ids
        for event_id in stale_ids:
            assert preconditions[event_id]["attempt_execution_before"] == "SUCCEEDED"
            assert preconditions[event_id]["attempt_authority_before"] == "SUPERSEDED"
            assert presentations[event_id]["accepted_authoritatively"] is False
            assert finalizations[event_id]["outcome"] == "REJECTED"


def test_duplicate_delivery_preserves_semantic_identity_and_observation_time(paired_evaluation):
    trial = _trial(paired_evaluation, "E1-C-duplicate-result-delivery", PolicyID.B4)
    observed = trial.evaluation.observed_evidence
    pre = {item["event_id"]: item for item in observed["terminal_presentation_preconditions"]}
    final = {item["event_id"]: item for item in observed["terminal_presentations"]}

    fresh = pre["c4.2c:C:fresh-a2"]
    fresh_dup = pre["c4.2c:C:fresh-a2-duplicate"]
    stale = pre["c4.2c:C:stale-a1"]
    stale_dup = pre["c4.2c:C:stale-a1-duplicate"]

    for original, duplicate in ((fresh, fresh_dup), (stale, stale_dup)):
        assert duplicate["duplicate"] is True
        assert original["attempt_id"] == duplicate["attempt_id"]
        assert original["evidence_id"] == duplicate["evidence_id"]
        assert original["output_id"] == duplicate["output_id"]
        assert original["observed_at"] == duplicate["observed_at"]
        assert original["event_id"] != duplicate["event_id"]
        assert original["delivered_at"] <= duplicate["delivered_at"]

    assert final["c4.2c:C:fresh-a2"]["finalization_outcome"] == "APPLIED"
    assert final["c4.2c:C:fresh-a2-duplicate"]["finalization_outcome"] == "IDEMPOTENT"
    assert final["c4.2c:C:stale-a1"]["finalization_outcome"] == "REJECTED"
    assert final["c4.2c:C:stale-a1-duplicate"]["finalization_outcome"] == "REJECTED"
    assert trial.finalization_applied_count == 1


def test_pretimeout_physical_success_becomes_stale_only_after_retry_supersession(paired_evaluation):
    trial = _trial(
        paired_evaluation,
        "E1-D-pretimeout-success-delayed-observation",
        PolicyID.B4,
    )
    observed = trial.evaluation.observed_evidence
    a1_completion = next(
        item for item in observed["physical_completion_checks"] if item["attempt_id"] == "a1"
    )
    stale_pre = next(
        item
        for item in observed["terminal_presentation_preconditions"]
        if item["event_id"] == "c4.2c:D:stale-a1"
    )
    assert a1_completion["attempt_authority_after"] == "CURRENT"
    assert a1_completion["attempt_execution_after"] == "SUCCEEDED"
    assert a1_completion["stale_at_delivery"] is False
    assert stale_pre["attempt_authority_before"] == "SUPERSEDED"
    assert stale_pre["attempt_execution_before"] == "SUCCEEDED"


def test_concurrent_race_arms_distinct_processes_before_terminal_delivery(paired_evaluation):
    trial = _trial(paired_evaluation, "E1-E-concurrent-terminal-race", PolicyID.B4)
    observed = trial.evaluation.observed_evidence
    batch = observed["compute_batches"][0]
    assert batch["attempt_ids"] == ["a1", "a2"]
    ready = batch["compute_ready"]
    assert {item["attempt_id"] for item in ready} == {"a1", "a2"}
    assert len({item["worker_pid"] for item in ready}) == 2

    group_ids = {"c4.2c:E:stale-a1", "c4.2c:E:fresh-a2"}
    delivery_order = [
        item for item in observed["presentation_delivery_order"] if item in group_ids
    ]
    assert set(delivery_order) == group_ids
    assert trial.authoritative_outcome.committed_attempt_id == "a2"


def test_three_generation_retry_race_preserves_attempt_generations_and_current_commit(paired_evaluation):
    trial = _trial(paired_evaluation, "E1-F-three-generation-retry-race", PolicyID.B4)
    assert tuple((item.id, item.generation) for item in trial.authoritative_outcome.attempts) == (
        ("a1", 1),
        ("a2", 2),
        ("a3", 3),
    )
    assert trial.authoritative_outcome.committed_attempt_id == "a3"
    assert trial.stale_result_event_ids == (
        "c4.2c:F:stale-a1",
        "c4.2c:F:stale-a2",
    )


def test_b4_stale_physical_work_fencing_remains_diagnostic_only(paired_evaluation):
    for scenario_id in S1_E1_SCENARIO_IDS:
        trials = {policy_id: _trial(paired_evaluation, scenario_id, policy_id) for policy_id in PolicyID}
        b4_decisions = trials[PolicyID.B4].stale_admission_decisions
        for decision in b4_decisions:
            assert decision.worker_id is None
            assert decision.ranked_worker_ids == ()
            assert decision.reason == "ATTEMPT_FENCED"

        for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
            for decision in trials[policy_id].stale_admission_decisions:
                assert decision.worker_id == "w1"
                assert decision.ranked_worker_ids == ("w1",)

        assert _rate(
            paired_evaluation,
            PolicyID.B4,
            CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,
        ).numerator == 0


def test_e1_stale_classifier_keeps_repeated_stale_acceptance_measurable_after_bad_commit():
    assert _classify_e1_stale_acceptance(
        attempt_id="a1",
        attempt_authority_before="COMMITTED",
        attempt_execution_before="SUCCEEDED",
        committed_attempt_id_before="a1",
        committed_attempt_id_after="a1",
        attempt_authority_after="COMMITTED",
    ) is True
