import os

import pytest

from experiments.correctness import (
    CorrectnessMetric,
    MetricOpportunityScope,
    OutcomeClass,
    ResultEvidenceProvenance,
    ValidationEvidenceLevel,
)
from experiments.idempotence_ordering_e1 import (
    S5_E1_COHORT_ID,
    S5_E1_MIN_CPU_SECONDS,
    S5_E1_SCENARIOS,
    S5_E1_SCENARIO_IDS,
    S5_E1_SCHEMA,
    S5_E1_START_METHOD,
    S5_E1_WORKER_IDS,
    run_s5_e1_anti_false_zero,
    run_s5_e1_paired,
    run_s5_e1_trial,
)
from simulator import PolicyID


_FAULTED = tuple(
    item.scenario_id for item in S5_E1_SCENARIOS if item.fault_id is not None
)
_REQUEST_FAULTED = (
    "E1-S5-A-DUPLICATE-FINAL-RESULT",
    "E1-S5-B-CONFLICTING-LATE-RESULT",
    "E1-S5-C-REORDERED-OLD-A1-AFTER-A2",
)


@pytest.fixture(scope="module")
def evaluation():
    return run_s5_e1_paired()


def _trial(evaluation, scenario_id, policy_id):
    return next(
        item
        for item in evaluation.trials
        if item.scenario.scenario_id == scenario_id and item.policy_id is policy_id
    )


def _summary(evaluation, policy_id):
    return next(
        item
        for item in evaluation.summary.policy_summaries
        if item.policy_id is policy_id
    )


def _rate(summary, metric):
    return next(rate for rate in summary.rates if rate.metric is metric)


def _outcome_count(summary, outcome):
    return dict(summary.outcome_counts)[outcome]


def test_s5_e1_manifest_is_canonical_bounded_and_predeclared():
    assert S5_E1_START_METHOD == "spawn"
    assert S5_E1_WORKER_IDS == ("w1", "w2", "w3")
    assert len(S5_E1_SCENARIOS) == 5
    assert len(_FAULTED) == 4
    assert S5_E1_SCENARIO_IDS == tuple(item.scenario_id for item in S5_E1_SCENARIOS)
    assert S5_E1_SCENARIO_IDS == (
        "E1-S5-A-DUPLICATE-FINAL-RESULT",
        "E1-S5-B-CONFLICTING-LATE-RESULT",
        "E1-S5-C-REORDERED-OLD-A1-AFTER-A2",
        "E1-S5-D-DUPLICATE-EVENT-ID",
        "E1-S5-E-SINGLE-FINAL-RESULT-CONTROL",
    )
    physical_ids = [
        directive.physical_delivery_id
        for scenario in S5_E1_SCENARIOS
        for directive in scenario.directives
    ]
    assert len(physical_ids) == len(set(physical_ids))


def test_paired_order_and_measured_evidence_class(evaluation):
    assert tuple(
        (trial.scenario.scenario_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (scenario.scenario_id, policy_id)
        for scenario in S5_E1_SCENARIOS
        for policy_id in PolicyID
    )
    for trial in evaluation.trials:
        record = trial.evaluation
        assert record.cohort_id == S5_E1_COHORT_ID
        assert record.validation_level is ValidationEvidenceLevel.EV1_MEASURED_CPU_DISTRIBUTED
        assert record.evidence_provenance is ResultEvidenceProvenance.MEASURED
        assert record.ground_truth["schema"] == S5_E1_SCHEMA
        assert record.policy_decision["physical_schedule_measured_before_policy_replay"] is True
        assert record.policy_decision["workers_cannot_mutate_c1"] is True
        assert record.policy_decision["semantic_authority"] == "C1_COMMON_TO_B0_B4"


def test_real_worker_process_provenance_and_cpu_floor(evaluation):
    coordinator_pid = os.getpid()
    used_worker_ids = set()
    for scenario in S5_E1_SCENARIOS:
        trial = _trial(evaluation, scenario.scenario_id, PolicyID.B4)
        assert len(trial.worker_process_ids) == 3
        assert len(set(trial.worker_process_ids)) == 3
        assert coordinator_pid not in trial.worker_process_ids
        assert all(pid > 0 for pid in trial.worker_process_ids)
        assert len(trial.physical_deliveries) == len(scenario.directives)
        for sequence, (delivery, directive) in enumerate(
            zip(trial.physical_deliveries, scenario.directives, strict=True), start=1
        ):
            used_worker_ids.add(delivery["worker_id"])
            assert delivery["kind"] == "OBSERVATION"
            assert delivery["physical_delivery_id"] == directive.physical_delivery_id
            assert delivery["semantic_event_id"] == directive.semantic_event_id
            assert delivery["action"] == directive.action
            assert delivery["worker_id"] == directive.worker_id
            assert int(delivery["worker_pid"]) in trial.worker_process_ids
            assert delivery["coordinator_sequence"] == sequence
            assert float(delivery["process_cpu_seconds"]) >= S5_E1_MIN_CPU_SECONDS
            assert float(delivery["wall_execution_seconds"]) > 0.0
            assert float(delivery["completed_at"]) >= float(delivery["started_at"])
            assert float(delivery["delivered_at"]) >= float(delivery["completed_at"])
            assert isinstance(delivery["cpu_digest"], str)
            assert len(delivery["cpu_digest"]) == 64
    assert used_worker_ids == set(S5_E1_WORKER_IDS)


def test_one_measured_physical_schedule_is_replayed_unchanged_across_policies(evaluation):
    for scenario in S5_E1_SCENARIOS:
        trials = [_trial(evaluation, scenario.scenario_id, policy_id) for policy_id in PolicyID]
        first = trials[0]
        for trial in trials[1:]:
            assert trial.worker_process_ids == first.worker_process_ids
            assert trial.physical_deliveries == first.physical_deliveries
        records = [trial.evaluation for trial in trials]
        assert len({record.ground_truth_json for record in records}) == 1
        physical_json = [record.observed_evidence["physical_deliveries"] for record in records]
        assert all(item == physical_json[0] for item in physical_json[1:])


def test_duplicate_final_result_has_one_logical_completion(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S5-A-DUPLICATE-FINAL-RESULT", policy_id)
        assert trial.finalization_effects == ("o1",)
        assert trial.completed_request_id == "r1"
        assert trial.authoritative_snapshot == {
            "request_status": "COMPLETED",
            "committed_attempt_id": "a1",
            "authoritative_output_id": "o1",
            "attempt_authority": {"a1": "COMMITTED"},
        }
        assert trial.evaluation.metric_opportunities == (
            CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
        )
        assert trial.evaluation.metric_opportunity_scopes == (
            MetricOpportunityScope.POLICY_DERIVED,
        )
        assert trial.evaluation.metric_violations == ()
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_conflicting_late_measured_result_cannot_replace_completed_output(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S5-B-CONFLICTING-LATE-RESULT", policy_id)
        assert trial.finalization_effects == ("o1",)
        assert trial.authoritative_snapshot["authoritative_output_id"] == "o1"
        assert trial.completed_request_id == "r1"
        assert "FINALIZE_O2:REJECTED:InvalidTransition" in trial.application_outcomes[1]
        assert trial.evaluation.metric_violations == ()


def test_late_old_a1_measured_result_cannot_regain_authority_after_a2(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S5-C-REORDERED-OLD-A1-AFTER-A2", policy_id)
        assert trial.finalization_effects == ("o2",)
        assert trial.completed_request_id == "r1"
        assert trial.authoritative_snapshot == {
            "request_status": "COMPLETED",
            "committed_attempt_id": "a2",
            "authoritative_output_id": "o2",
            "attempt_authority": {"a1": "SUPERSEDED", "a2": "COMMITTED"},
        }
        assert trial.application_outcomes[0].startswith("FINALIZE_A2_O2:APPLIED")
        assert "FINALIZE_A1_O1:REJECTED:" in trial.application_outcomes[1]
        assert trial.evaluation.metric_violations == ()


def test_duplicate_measured_event_id_has_one_semantic_primary_event(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S5-D-DUPLICATE-EVENT-ID", policy_id)
        assert trial.completed_request_id is None
        assert trial.evaluation.metric_opportunities == ()
        events = trial.authoritative_snapshot["events"]
        assert [item["id"] for item in events] == [
            "S5:EV1:D:event-1",
            "S5:EV1:D:event-u",
        ]
        primary_outcomes = [
            item for item in trial.application_outcomes if item.startswith("RECORD_PRIMARY_EVENT:")
        ]
        assert primary_outcomes == [
            "RECORD_PRIMARY_EVENT:APPLIED",
            "RECORD_PRIMARY_EVENT:IDEMPOTENT",
        ]
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_positive_control_proves_first_measured_finalization_remains_functional(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S5-E-SINGLE-FINAL-RESULT-CONTROL", policy_id)
        assert trial.scenario.fault_id is None
        assert trial.finalization_effects == ("o1",)
        assert trial.completed_request_id == "r1"
        assert trial.authoritative_snapshot["request_status"] == "COMPLETED"
        assert trial.authoritative_snapshot["authoritative_output_id"] == "o1"
        assert trial.evaluation.metric_opportunities == ()
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_policy_derived_dfr_denominator_exists_only_for_actual_faulted_completions(evaluation):
    for policy_id in PolicyID:
        for scenario in S5_E1_SCENARIOS:
            trial = _trial(evaluation, scenario.scenario_id, policy_id)
            if scenario.scenario_id in _REQUEST_FAULTED:
                assert trial.completed_request_id == "r1"
                assert trial.evaluation.metric_opportunities == (
                    CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
                )
                assert trial.evaluation.metric_opportunity_scopes == (
                    MetricOpportunityScope.POLICY_DERIVED,
                )
                assert trial.evaluation.metric_opportunity_event_ids[0].endswith(
                    ":completed-request:r1"
                )
            else:
                assert trial.evaluation.metric_opportunities == ()
                assert trial.evaluation.metric_opportunity_event_ids == ()


def test_s5_e1_summary_is_measured_semantic_null_comparator(evaluation):
    for policy_id in PolicyID:
        summary = _summary(evaluation, policy_id)
        dfr = _rate(summary, CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert summary.operation_count == 5
        assert summary.faulted_operation_count == 4
        assert (dfr.numerator, dfr.denominator) == (0, 3)
        assert (sser.numerator, sser.denominator) == (0, 4)
        assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 4
        assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O3_EXPLICIT_NON_SUCCESS) == 0
        assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0


def test_anti_false_zero_measured_schedule_detects_forced_second_finalization():
    trial = run_s5_e1_anti_false_zero()
    assert trial.injected_duplicate_finalization is True
    assert trial.finalization_effects == ("o1", "injected-second-output")
    assert trial.evaluation.observed_evidence["snapshot_matches_independent_target"] is False
    assert "SNAPSHOT_MISMATCH" in trial.evaluation.observed_evidence["invariant_violations"]
    assert "DUPLICATE_FINALIZATION" in trial.evaluation.observed_evidence["invariant_violations"]
    assert trial.evaluation.metric_violations == (
        CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
    )
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_public_trial_api_rejects_unknown_scenario_and_non_policy_id():
    with pytest.raises(ValueError):
        run_s5_e1_trial(PolicyID.B4, "unknown")

    with pytest.raises(TypeError):
        run_s5_e1_trial("B4", S5_E1_SCENARIO_IDS[0])  # type: ignore[arg-type]
