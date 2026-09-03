from __future__ import annotations

from dataclasses import replace
import os
import time

import pytest

from continuity import ContinuityCore
from continuity.entities import AttemptAuthority, ExecutionStatus, RequestStatus
from experiments.attempt_fencing_e1 import (
    S1_E1_MIN_CPU_SECONDS,
    S1_E1_RETRY_TIMEOUT_SECONDS,
    S1_E1_SCENARIOS,
    S1_E1_SCENARIO_IDS,
    E1ScenarioMode,
    _apply_presentation,
    _classify_e1_stale_acceptance,
    _scaffold_core,
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
    policy = next(item for item in evaluation.summary.policy_summaries if item.policy_id is policy_id)
    return next(item for item in policy.rates if item.metric is metric)


def _trial(evaluation, scenario_id: str, policy_id: PolicyID):
    return next(
        trial
        for trial in evaluation.trials
        if trial.scenario_id == scenario_id and trial.policy_id is policy_id
    )


def _presentation_message(*, event_id: str, attempt_id: str, output_suffix: str = ""):
    now = time.time()
    return {
        "event_id": event_id,
        "attempt_id": attempt_id,
        "observed_at": now,
        "delivered_at": now,
        "evidence_id": f"test:evidence:{attempt_id}{output_suffix}",
        "output_id": f"test:output:{attempt_id}{output_suffix}",
        "worker_pid": os.getpid(),
        "duplicate": False,
    }


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
        assert trial.evaluation.ground_truth["cpu_work"]["minimum_process_cpu_seconds"] == S1_E1_MIN_CPU_SECONDS


def test_every_retry_is_triggered_by_a_measured_wall_clock_timeout(paired_evaluation):
    for trial in paired_evaluation.trials:
        observed = trial.evaluation.observed_evidence
        attempt_ids = trial.evaluation.ground_truth["attempt_ids"]
        timeouts = observed["retry_timeout_checks"]
        assert len(timeouts) == len(attempt_ids) - 1
        for index, timeout in enumerate(timeouts, start=1):
            assert timeout["superseded_attempt_id"] == attempt_ids[index - 1]
            assert timeout["retry_attempt_id"] == attempt_ids[index]
            assert timeout["timeout_seconds"] == S1_E1_RETRY_TIMEOUT_SECONDS
            assert timeout["elapsed_seconds"] >= S1_E1_RETRY_TIMEOUT_SECONDS
            assert timeout["fired_at"] >= timeout["started_at"]


def test_retry_races_have_cpu_in_flight_when_timeout_fires(paired_evaluation):
    for trial in paired_evaluation.trials:
        spec = next(item for item in S1_E1_SCENARIOS if item.scenario_id == trial.scenario_id)
        if spec.mode is not E1ScenarioMode.RETRY_RACE:
            continue
        observed = trial.evaluation.observed_evidence
        starts = {
            batch["attempt_ids"][0]: batch["compute_started"][0]
            for batch in observed["compute_batches"]
        }
        completions = {
            item["attempt_id"]: item for item in observed["physical_completion_checks"]
        }
        for timeout in observed["retry_timeout_checks"]:
            superseded = timeout["superseded_attempt_id"]
            assert starts[superseded]["at"] <= timeout["started_at"]
            assert completions[superseded]["observed_at"] >= timeout["fired_at"]
            assert completions[superseded]["compute_cpu_seconds"] >= S1_E1_MIN_CPU_SECONDS


def test_s1_e1_all_competent_baselines_have_zero_saar_dfr_sser(paired_evaluation):
    stale_denominator = sum(len(spec.stale_presentation_event_ids) for spec in S1_E1_SCENARIOS)
    assert stale_denominator == 8

    for policy_id in PolicyID:
        saar = _rate(paired_evaluation, policy_id, CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
        dfr = _rate(paired_evaluation, policy_id, CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
        sser = _rate(paired_evaluation, policy_id, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert (saar.numerator, saar.denominator, saar.rate) == (0, 8, 0.0)
        assert (dfr.numerator, dfr.denominator, dfr.rate) == (0, 6, 0.0)
        assert (sser.numerator, sser.denominator, sser.rate) == (0, 6, 0.0)

        policy_summary = next(item for item in paired_evaluation.summary.policy_summaries if item.policy_id is policy_id)
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
            assert presentations[event_id]["invariant_error_type"] is None
            assert finalizations[event_id]["outcome"] == "REJECTED"


def test_real_e1_presentations_have_no_invariant_diagnostics(paired_evaluation):
    for trial in paired_evaluation.trials:
        for presentation in trial.evaluation.observed_evidence["terminal_presentations"]:
            assert presentation["invariant_error_type"] is None
            assert presentation["invariant_error_message"] is None


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
    trial = _trial(paired_evaluation, "E1-D-pretimeout-success-delayed-observation", PolicyID.B4)
    observed = trial.evaluation.observed_evidence
    a1_completion = next(item for item in observed["physical_completion_checks"] if item["attempt_id"] == "a1")
    stale_pre = next(
        item
        for item in observed["terminal_presentation_preconditions"]
        if item["event_id"] == "c4.2c:D:stale-a1"
    )
    assert a1_completion["attempt_authority_after"] == "CURRENT"
    assert a1_completion["attempt_execution_after"] == "SUCCEEDED"
    assert a1_completion["stale_at_delivery"] is False
    timeout = observed["retry_timeout_checks"][0]
    assert timeout["started_at"] >= a1_completion["delivered_at"]
    assert timeout["elapsed_seconds"] >= S1_E1_RETRY_TIMEOUT_SECONDS
    assert stale_pre["attempt_authority_before"] == "SUPERSEDED"
    assert stale_pre["attempt_execution_before"] == "SUCCEEDED"


def test_concurrent_terminal_race_uses_same_group_ipc_delivery(paired_evaluation):
    trial = _trial(paired_evaluation, "E1-E-concurrent-terminal-race", PolicyID.B4)
    observed = trial.evaluation.observed_evidence
    starts = {
        batch["attempt_ids"][0]: batch["compute_started"][0]["at"]
        for batch in observed["compute_batches"]
    }
    assert starts["a1"] < starts["a2"]

    group_ids = {"c4.2c:E:stale-a1", "c4.2c:E:fresh-a2"}
    delivery_order = [item for item in observed["presentation_delivery_order"] if item in group_ids]
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
    assert trial.stale_result_event_ids == ("c4.2c:F:stale-a1", "c4.2c:F:stale-a2")

    observed = trial.evaluation.observed_evidence
    starts = {
        batch["attempt_ids"][0]: batch["compute_started"][0]["at"]
        for batch in observed["compute_batches"]
    }
    for timeout in observed["retry_timeout_checks"]:
        assert starts[timeout["superseded_attempt_id"]] <= timeout["started_at"]


def test_b4_stale_physical_work_fencing_remains_diagnostic_only(paired_evaluation):
    for scenario_id in S1_E1_SCENARIO_IDS:
        trials = {policy_id: _trial(paired_evaluation, scenario_id, policy_id) for policy_id in PolicyID}
        for decision in trials[PolicyID.B4].stale_admission_decisions:
            assert decision.worker_id is None
            assert decision.ranked_worker_ids == ()
            assert decision.reason == "ATTEMPT_FENCED"

        for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
            for decision in trials[policy_id].stale_admission_decisions:
                assert decision.worker_id == "w1"
                assert decision.ranked_worker_ids == ("w1",)

        assert _rate(paired_evaluation, PolicyID.B4, CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE).numerator == 0


def test_e1_stale_classifier_keeps_repeated_stale_acceptance_measurable_after_bad_commit():
    assert _classify_e1_stale_acceptance(
        attempt_id="a1",
        attempt_authority_before="COMMITTED",
        attempt_execution_before="SUCCEEDED",
        committed_attempt_id_before="a1",
        committed_attempt_id_after="a1",
        attempt_authority_after="COMMITTED",
        authoritative_output_id_after="test:output:a1",
        presented_output_id="test:output:a1",
    ) is True


def test_bad_stale_commit_is_classified_before_invariant_failure(monkeypatch):
    core = _scaffold_core()
    core.start_attempt("a1", "r")
    core.set_attempt_execution("a1", ExecutionStatus.RUNNING)
    core.complete_attempt("a1", succeeded=True)
    core.start_attempt("a2", "r")
    core.set_attempt_execution("a2", ExecutionStatus.RUNNING)

    def defective_finalize(self: ContinuityCore, request_id: str, output_id: str, now=None):
        del now
        output = self.outputs[output_id]
        stale_id = output.attempt_id
        request = self.requests[request_id]
        self.requests[request_id] = replace(
            request,
            status=RequestStatus.COMPLETED,
            committed_attempt_id=stale_id,
            authoritative_output_id=output_id,
        )
        self.attempts[stale_id] = replace(
            self.attempts[stale_id], authority_status=AttemptAuthority.COMMITTED
        )
        # Deliberately leave a2 CURRENT: this is structurally invalid but must be measurable.

    monkeypatch.setattr(ContinuityCore, "finalize_request", defective_finalize)
    _, post = _apply_presentation(
        core,
        _presentation_message(event_id="test:stale", attempt_id="a1"),
        stale=True,
    )
    assert post["accepted_authoritatively"] is True
    assert post["finalization_outcome"] == "APPLIED"
    assert post["invariant_error_type"] is not None


def test_dangling_authoritative_reference_is_recorded_after_stale_acceptance(monkeypatch):
    core = _scaffold_core()
    core.start_attempt("a1", "r")
    core.set_attempt_execution("a1", ExecutionStatus.RUNNING)
    core.complete_attempt("a1", succeeded=True)
    core.start_attempt("a2", "r")
    core.set_attempt_execution("a2", ExecutionStatus.RUNNING)

    def defective_finalize(self: ContinuityCore, request_id: str, output_id: str, now=None):
        del now, output_id
        request = self.requests[request_id]
        self.attempts["a1"] = replace(
            self.attempts["a1"], authority_status=AttemptAuthority.COMMITTED
        )
        self.attempts["a2"] = replace(
            self.attempts["a2"], authority_status=AttemptAuthority.SUPERSEDED
        )
        self.requests[request_id] = replace(
            request,
            status=RequestStatus.COMPLETED,
            current_attempt_id=None,
            committed_attempt_id="a1",
            authoritative_output_id="missing-output",
        )

    monkeypatch.setattr(ContinuityCore, "finalize_request", defective_finalize)
    _, post = _apply_presentation(
        core,
        _presentation_message(event_id="test:dangling", attempt_id="a1"),
        stale=True,
    )
    assert post["accepted_authoritatively"] is True
    assert post["finalization_outcome"] == "APPLIED"
    assert post["request_committed_attempt_id_after"] == "a1"
    assert post["request_authoritative_output_id_after"] == "missing-output"
    assert post["invariant_error_type"] == "KeyError"


@pytest.mark.parametrize("authority_channel", ("output", "attempt"))
def test_stale_acceptance_detects_partial_authority_corruption(monkeypatch, authority_channel):
    core = _scaffold_core()
    core.start_attempt("a1", "r")
    core.set_attempt_execution("a1", ExecutionStatus.RUNNING)
    core.complete_attempt("a1", succeeded=True)
    core.start_attempt("a2", "r")
    core.set_attempt_execution("a2", ExecutionStatus.RUNNING)
    message = _presentation_message(event_id=f"test:partial:{authority_channel}", attempt_id="a1")

    def defective_finalize(self: ContinuityCore, request_id: str, output_id: str, now=None):
        del now
        request = self.requests[request_id]
        if authority_channel == "output":
            self.requests[request_id] = replace(
                request,
                status=RequestStatus.COMPLETED,
                committed_attempt_id="a2",
                authoritative_output_id=output_id,
            )
        else:
            self.attempts["a1"] = replace(
                self.attempts["a1"], authority_status=AttemptAuthority.COMMITTED
            )
            self.requests[request_id] = replace(
                request,
                status=RequestStatus.COMPLETED,
                committed_attempt_id="a2",
            )

    monkeypatch.setattr(ContinuityCore, "finalize_request", defective_finalize)
    _, post = _apply_presentation(core, message, stale=True)
    assert post["accepted_authoritatively"] is True
    assert post["finalization_outcome"] == "APPLIED"
    assert post["request_committed_attempt_id_after"] == "a2"
    assert post["invariant_error_type"] is not None


def test_completed_to_completed_semantic_mutation_counts_as_second_applied_finalization(monkeypatch):
    core = _scaffold_core()
    core.start_attempt("a1", "r")
    core.set_attempt_execution("a1", ExecutionStatus.RUNNING)
    core.complete_attempt("a1", succeeded=True)

    _, first = _apply_presentation(
        core,
        _presentation_message(event_id="test:first", attempt_id="a1", output_suffix=":1"),
        stale=False,
    )
    assert first["finalization_outcome"] == "APPLIED"

    def defective_refinalize(self: ContinuityCore, request_id: str, output_id: str, now=None):
        del now
        request = self.requests[request_id]
        self.requests[request_id] = replace(request, authoritative_output_id=output_id)

    monkeypatch.setattr(ContinuityCore, "finalize_request", defective_refinalize)
    _, second = _apply_presentation(
        core,
        _presentation_message(event_id="test:second", attempt_id="a1", output_suffix=":2"),
        stale=False,
    )
    assert second["finalization_outcome"] == "APPLIED"
    assert sum(item["finalization_outcome"] == "APPLIED" for item in (first, second)) == 2
