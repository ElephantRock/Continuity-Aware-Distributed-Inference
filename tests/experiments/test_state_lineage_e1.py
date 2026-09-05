import os

import pytest

from experiments.correctness import (
    CorrectnessMetric,
    OutcomeClass,
    ResultEvidenceProvenance,
    ValidationEvidenceLevel,
)
from experiments.state_lineage_e1 import (
    S2_E1_COHORT_ID,
    S2_E1_MIN_CPU_SECONDS,
    S2_E1_SCENARIOS,
    S2_E1_SCENARIO_IDS,
    S2_E1_SCHEMA,
    S2_E1_START_METHOD,
    S2_E1_WORKER_IDS,
    run_s2_e1_paired,
    run_s2_e1_trial,
)
from simulator import PolicyID


@pytest.fixture(scope="module")
def evaluation():
    return run_s2_e1_paired()


def _trial(evaluation, scenario_id, policy_id):
    return next(
        trial
        for trial in evaluation.trials
        if trial.scenario_id == scenario_id and trial.policy_id is policy_id
    )


def _policy_summary(evaluation, policy_id):
    return next(
        summary
        for summary in evaluation.summary.policy_summaries
        if summary.policy_id is policy_id
    )


def _rate(summary, metric):
    return next(rate for rate in summary.rates if rate.metric is metric)


def _outcome_count(summary, outcome):
    return dict(summary.outcome_counts)[outcome]


def test_e1_manifest_is_canonical_and_bounded():
    assert S2_E1_START_METHOD == "spawn"
    assert S2_E1_WORKER_IDS == ("w1", "w2")
    assert len(S2_E1_SCENARIOS) == 7
    assert S2_E1_SCENARIO_IDS == tuple(item.scenario_id for item in S2_E1_SCENARIOS)
    assert len(set(S2_E1_SCENARIO_IDS)) == 7
    assert sum(item.fault_id is not None for item in S2_E1_SCENARIOS) == 6
    assert sum(item.wbrr_event_id is not None for item in S2_E1_SCENARIOS) == 3
    assert sum(item.expected_compatible for item in S2_E1_SCENARIOS) == 2


def test_e1_paired_order_and_evidence_class(evaluation):
    assert tuple(
        (trial.scenario_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (scenario.scenario_id, policy_id)
        for scenario in S2_E1_SCENARIOS
        for policy_id in PolicyID
    )

    for trial in evaluation.trials:
        record = trial.evaluation
        assert record.cohort_id == S2_E1_COHORT_ID
        assert record.validation_level is ValidationEvidenceLevel.EV1_MEASURED_CPU_DISTRIBUTED
        assert record.evidence_provenance is ResultEvidenceProvenance.MEASURED
        assert record.ground_truth["schema"] == S2_E1_SCHEMA
        assert record.ground_truth["ipc_transport"] == "multiprocessing.Pipe"
        assert record.ground_truth["start_method"] == "spawn"


def test_e1_uses_two_real_worker_processes_per_scenario(evaluation):
    coordinator_pid = os.getpid()
    for scenario in S2_E1_SCENARIOS:
        scenario_trials = [
            trial for trial in evaluation.trials if trial.scenario_id == scenario.scenario_id
        ]
        process_sets = {trial.worker_process_ids for trial in scenario_trials}
        assert len(process_sets) == 1
        worker_pids = next(iter(process_sets))
        assert len(worker_pids) == 2
        assert len(set(worker_pids)) == 2
        assert coordinator_pid not in worker_pids
        assert all(pid > 0 for pid in worker_pids)
        assert all(
            int(trial.worker_execution["worker_pid"]) in worker_pids
            for trial in scenario_trials
        )


def test_e1_every_worker_execution_performs_real_cpu_work(evaluation):
    for trial in evaluation.trials:
        execution = trial.worker_execution
        assert float(execution["process_cpu_seconds"]) >= S2_E1_MIN_CPU_SECONDS
        assert float(execution["wall_execution_seconds"]) > 0.0
        assert float(execution["completed_at"]) >= float(execution["started_at"])
        assert isinstance(execution["cpu_digest"], str)
        assert len(execution["cpu_digest"]) == 64


def test_e1_independent_oracle_agrees_with_c1_for_every_policy_case(evaluation):
    expected_by_scenario = {
        scenario.scenario_id: scenario.expected_compatible for scenario in S2_E1_SCENARIOS
    }
    for trial in evaluation.trials:
        assert trial.independent_oracle_compatible == trial.c1_compatible
        assert trial.c1_compatible == expected_by_scenario[trial.scenario_id]


def test_e1_wrong_state_metrics_require_measured_worker_consumption(evaluation):
    incompatible = {
        "E1-S2-A-WRONG-SIBLING",
        "E1-S2-B-SUPERSEDED-PRODUCER",
        "E1-S2-C-ABANDONED-RESIDUAL",
        "E1-S2-D-SIMILAR-DIFFERENT",
        "E1-S2-E-DERIVED-INVALID-DEPENDENCY",
    }
    for scenario_id in incompatible:
        for policy_id in (PolicyID.B1, PolicyID.B2, PolicyID.B3):
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.consumption_event is not None
            assert trial.worker_execution["consumption_event_id"] == trial.consumption_event.event_id
            assert trial.worker_execution["consumed_state_id"] == trial.candidate_state_id
            assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in (
                trial.evaluation.metric_violations
            )

        for policy_id in (PolicyID.B0, PolicyID.B4):
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.consumption_event is None
            assert trial.worker_execution["consumed_state_id"] is None
            assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE not in (
                trial.evaluation.metric_violations
            )


def test_e1_application_outcomes_remain_independent_from_gate_violation(evaluation):
    wrong = _trial(evaluation, "E1-S2-A-WRONG-SIBLING", PolicyID.B3)
    detected = _trial(evaluation, "E1-S2-B-SUPERSEDED-PRODUCER", PolicyID.B3)
    coincidentally_correct = _trial(
        evaluation, "E1-S2-D-SIMILAR-DIFFERENT", PolicyID.B3
    )

    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in wrong.evaluation.metric_violations
    assert wrong.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION

    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in detected.evaluation.metric_violations
    assert detected.terminal_event.detected_bad_state
    assert detected.terminal_event.used_recompute
    assert detected.evaluation.outcome_class is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY

    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in (
        coincidentally_correct.evaluation.metric_violations
    )
    assert coincidentally_correct.terminal_event.semantically_correct
    assert not coincidentally_correct.terminal_event.used_recompute
    assert coincidentally_correct.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_e1_b4_still_reuses_real_valid_ancestor_state(evaluation):
    trial = _trial(evaluation, "E1-S2-F-VALID-ANCESTOR-CONTROL", PolicyID.B4)

    assert trial.independent_oracle_compatible
    assert trial.placement_decision.worker_id == "w1"
    assert trial.consumption_event is not None
    assert trial.consumption_event.worker_id == "w1"
    assert trial.worker_execution["states_before"].get(trial.candidate_state_id)
    assert not trial.terminal_event.used_recompute
    assert trial.evaluation.fault_id is None
    assert trial.evaluation.metric_violations == ()
    assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_e1_post_observation_eviction_is_measured_as_recompute_not_fake_consumption(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S2-G-POST-OBSERVATION-EVICTION", policy_id)
        assert trial.independent_oracle_compatible
        assert trial.evaluation.ground_truth["policy_visible_locations"] == ["w1"]
        assert trial.evaluation.ground_truth["evict_candidate_after_observation"] is True
        physical_events = trial.evaluation.observed_evidence["physical_state_events"]
        evictions = [item for item in physical_events if item["kind"] == "EVICTED"]
        assert len(evictions) == 1
        assert evictions[0]["state_id"] == trial.candidate_state_id
        assert trial.worker_execution["consumed_state_id"] is None
        assert trial.consumption_event is None
        assert trial.terminal_event.used_recompute
        assert trial.terminal_event.semantically_correct
        assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE not in (
            trial.evaluation.metric_opportunities
        )
        assert trial.evaluation.outcome_class is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY


def test_e1_wrong_branch_opportunities_are_exogenous_and_identical(evaluation):
    branch_cases = {
        scenario.scenario_id
        for scenario in S2_E1_SCENARIOS
        if scenario.wbrr_event_id is not None
    }
    assert branch_cases == {
        "E1-S2-A-WRONG-SIBLING",
        "E1-S2-C-ABANDONED-RESIDUAL",
        "E1-S2-D-SIMILAR-DIFFERENT",
    }
    for scenario_id in branch_cases:
        records = [
            _trial(evaluation, scenario_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        signatures = {record.exogenous_opportunity_signature for record in records}
        assert len(signatures) == 1
        signature = next(iter(signatures))
        assert len(signature) == 1
        assert signature[0][0] == CorrectnessMetric.WRONG_BRANCH_REUSE_RATE.value


def test_e1_summary_records_measured_comparator_result(evaluation):
    expected_wbrr = {
        PolicyID.B0: (0, 3),
        PolicyID.B1: (3, 3),
        PolicyID.B2: (3, 3),
        PolicyID.B3: (3, 3),
        PolicyID.B4: (0, 3),
    }
    expected_wscr = {
        PolicyID.B0: (0, 0),
        PolicyID.B1: (5, 5),
        PolicyID.B2: (5, 5),
        PolicyID.B3: (5, 5),
        PolicyID.B4: (0, 0),
    }
    expected_sser = {
        PolicyID.B0: (0, 6),
        PolicyID.B1: (3, 6),
        PolicyID.B2: (3, 6),
        PolicyID.B3: (3, 6),
        PolicyID.B4: (0, 6),
    }

    for policy_id in PolicyID:
        summary = _policy_summary(evaluation, policy_id)
        assert summary.operation_count == 7
        assert summary.faulted_operation_count == 6
        wbrr = _rate(summary, CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
        wscr = _rate(summary, CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert (wbrr.numerator, wbrr.denominator) == expected_wbrr[policy_id]
        assert (wscr.numerator, wscr.denominator) == expected_wscr[policy_id]
        assert (sser.numerator, sser.denominator) == expected_sser[policy_id]

        if policy_id in {PolicyID.B1, PolicyID.B2, PolicyID.B3}:
            assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 3
            assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 2
            assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 1
        else:
            assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0
            assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 6
            assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 0


def test_e1_real_worker_injected_b4_wrong_consumption_is_not_false_zero():
    trial = run_s2_e1_trial(
        PolicyID.B4,
        "E1-S2-A-WRONG-SIBLING",
        execution_worker_override="w1",
    )

    assert trial.placement_decision.worker_id == "w2"
    assert trial.execution_worker_id == "w1"
    assert trial.execution_worker_override == "w1"
    assert trial.consumption_event is not None
    assert int(trial.worker_execution["worker_pid"]) in trial.worker_process_ids
    assert CorrectnessMetric.WRONG_BRANCH_REUSE_RATE in trial.evaluation.metric_violations
    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in trial.evaluation.metric_violations
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
