import os

import pytest

from experiments.binding_safety_e1 import (
    S3_E1_COHORT_ID,
    S3_E1_CRASH_EXIT_CODE,
    S3_E1_MIN_CPU_SECONDS,
    S3_E1_SCENARIOS,
    S3_E1_SCENARIO_IDS,
    S3_E1_SCHEMA,
    S3_E1_START_METHOD,
    S3_E1_WORKER_IDS,
    _SPEC_BY_ID,
    _run_case,
    run_s3_e1_paired,
    run_s3_e1_trial,
)
from experiments.correctness import (
    CorrectnessMetric,
    OutcomeClass,
    ResultEvidenceProvenance,
    ValidationEvidenceLevel,
)
from simulator import MigrationDisposition, PolicyID


_FAULTED = tuple(
    scenario.scenario_id for scenario in S3_E1_SCENARIOS if scenario.fault_id is not None
)


@pytest.fixture(scope="module")
def evaluation():
    return run_s3_e1_paired()


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


def test_s3_e1_manifest_is_canonical_and_bounded():
    assert S3_E1_START_METHOD == "spawn"
    assert S3_E1_WORKER_IDS == ("w1", "w2", "w3")
    assert len(S3_E1_SCENARIOS) == 7
    assert S3_E1_SCENARIO_IDS == tuple(item.scenario_id for item in S3_E1_SCENARIOS)
    assert len(set(S3_E1_SCENARIO_IDS)) == 7
    assert len(_FAULTED) == 6
    assert sum(item.explicit_wait for item in S3_E1_SCENARIOS) == 2
    assert sum(item.fault_id is None for item in S3_E1_SCENARIOS) == 1


def test_s3_e1_paired_order_and_evidence_class(evaluation):
    assert tuple(
        (trial.scenario_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (scenario.scenario_id, policy_id)
        for scenario in S3_E1_SCENARIOS
        for policy_id in PolicyID
    )

    for trial in evaluation.trials:
        record = trial.evaluation
        assert record.cohort_id == S3_E1_COHORT_ID
        assert record.validation_level is ValidationEvidenceLevel.EV1_MEASURED_CPU_DISTRIBUTED
        assert record.evidence_provenance is ResultEvidenceProvenance.MEASURED
        assert record.ground_truth["schema"] == S3_E1_SCHEMA
        assert record.ground_truth["start_method"] == "spawn"
        assert record.ground_truth["ipc_transport"] == "multiprocessing.Pipe"
        assert record.policy_decision["workers_cannot_mutate_c1"] is True
        assert record.policy_decision["c1_commit_is_authoritative_not_policy_decision"] is True


def test_s3_e1_uses_three_real_worker_processes_and_measured_cpu(evaluation):
    coordinator_pid = os.getpid()
    for trial in evaluation.trials:
        assert len(trial.worker_process_ids) == 3
        assert len(set(trial.worker_process_ids)) == 3
        assert coordinator_pid not in trial.worker_process_ids
        assert all(pid > 0 for pid in trial.worker_process_ids)

        measured_work = [
            event
            for event in trial.physical_events
            if event.get("kind") in {"MATERIALIZED", "CPU_RESULT"}
        ]
        assert measured_work
        for event in measured_work:
            assert int(event["worker_pid"]) in trial.worker_process_ids
            assert float(event["process_cpu_seconds"]) >= S3_E1_MIN_CPU_SECONDS
            assert float(event["wall_execution_seconds"]) > 0.0
            assert float(event["completed_at"]) >= float(event["started_at"])
            assert isinstance(event["cpu_digest"], str)
            assert len(event["cpu_digest"]) == 64


def test_s3_e1_ground_truth_and_exogenous_sbdr_events_are_policy_invariant(evaluation):
    for scenario_id in _FAULTED:
        records = [
            _trial(evaluation, scenario_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        assert len({record.ground_truth_json for record in records}) == 1
        assert len({record.exogenous_opportunity_signature for record in records}) == 1
        signature = records[0].exogenous_opportunity_signature
        assert len(signature) == 1
        assert signature[0][0] == CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE.value


def test_partial_materialization_is_physical_not_semantic_authority(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S3-A-PARTIAL-MATERIALIZATION", policy_id)
        partial = [
            event
            for event in trial.physical_events
            if event.get("kind") == "MATERIALIZED"
            and event.get("binding_id") == "b2"
        ]
        assert len(partial) == 1
        assert partial[0]["completeness"] == "PARTIAL"
        assert trial.presentation.binding_id == "b2"
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.final_binding_id == "b1"
        assert trial.final_epoch == 1
        assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS
        assert CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE not in (
            trial.evaluation.metric_violations
        )


def test_destination_process_crash_before_commit_cannot_create_authority(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S3-B-DESTINATION-CRASH", policy_id)
        exits = [event for event in trial.physical_events if event.get("kind") == "PROCESS_EXIT"]
        assert len(exits) == 1
        assert exits[0]["worker_id"] == "w2"
        assert exits[0]["exit_code"] == S3_E1_CRASH_EXIT_CODE
        assert trial.evaluation.observed_evidence["final_worker_presentation"] is None
        assert trial.presentation.binding_id == "b2"
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "InsufficientEvidence"
        assert trial.final_binding_id == "b1"
        assert trial.final_epoch == 1
        assert trial.evaluation.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS


def test_late_old_owner_reaches_real_c1_path_and_is_fenced(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S3-C-LATE-OLD-OWNER", policy_id)
        worker_event = trial.evaluation.observed_evidence["final_worker_presentation"]
        assert worker_event["worker_id"] == "w1"
        assert worker_event["binding_id"] == "b1"
        assert worker_event["epoch"] == 1
        assert trial.presentation.binding_id == "b1"
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "SemanticViolation"
        assert trial.final_binding_id == "b2"
        assert trial.final_epoch == 2
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_concurrent_candidates_have_real_physical_presence_but_one_semantic_winner(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S3-D-CONCURRENT-CANDIDATES", policy_id)
        materialized = {
            event["binding_id"]: event
            for event in trial.physical_events
            if event.get("kind") == "MATERIALIZED"
            and event.get("binding_id") in {"b2", "b3"}
        }
        assert set(materialized) == {"b2", "b3"}
        assert materialized["b2"]["worker_id"] == "w2"
        assert materialized["b3"]["worker_id"] == "w3"
        assert trial.presentation.binding_id == "b3"
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "SemanticViolation"
        assert trial.final_binding_id == "b2"
        assert trial.final_epoch == 2


def test_delayed_stale_loser_executes_real_cpu_work_then_is_fenced(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S3-E-DELAYED-STALE-LOSER", policy_id)
        delayed = [
            event
            for event in trial.physical_events
            if event.get("kind") == "CPU_RESULT"
            and event.get("worker_id") == "w3"
        ]
        assert len(delayed) == 1
        assert float(delayed[0]["process_cpu_seconds"]) >= S3_E1_MIN_CPU_SECONDS
        assert trial.presentation.binding_id == "b3"
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "SemanticViolation"
        assert trial.final_binding_id == "b2"
        assert trial.final_epoch == 2


def test_multi_epoch_old_owner_cannot_regain_authority(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S3-F-MULTI-EPOCH-LATE-OWNER", policy_id)
        assert len(trial.evaluation.observed_evidence["setup_commits"]) == 2
        assert trial.presentation.binding_id == "b1"
        assert trial.presentation.binding_epoch == 1
        assert trial.presentation.commit_outcome == "REJECTED"
        assert trial.presentation.error_type == "SemanticViolation"
        assert trial.final_binding_id == "b3"
        assert trial.final_epoch == 3


def test_success_control_proves_real_process_migration_is_not_globally_disabled(evaluation):
    for policy_id in PolicyID:
        trial = _trial(evaluation, "E1-S3-G-SUCCESS-CONTROL", policy_id)
        assert trial.presentation.binding_id == "b2"
        assert trial.presentation.commit_outcome == "APPLIED"
        assert trial.presentation.error_type is None
        assert trial.final_binding_id == "b2"
        assert trial.final_epoch == 2
        assert trial.evaluation.fault_id is None
        assert trial.evaluation.metric_opportunities == ()
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_b4_migration_admission_is_diagnostic_while_c1_remains_authoritative(evaluation):
    partial = _trial(evaluation, "E1-S3-A-PARTIAL-MATERIALIZATION", PolicyID.B4)
    crash = _trial(evaluation, "E1-S3-B-DESTINATION-CRASH", PolicyID.B4)
    assert partial.policy_migration_decisions[0].disposition is MigrationDisposition.WAIT
    assert crash.policy_migration_decisions[0].disposition is MigrationDisposition.WAIT

    late = _trial(evaluation, "E1-S3-C-LATE-OLD-OWNER", PolicyID.B4)
    assert all(
        item.disposition is MigrationDisposition.ALLOW_COMMIT
        for item in late.policy_migration_decisions
    )
    assert late.presentation.commit_outcome == "REJECTED"
    assert late.final_binding_id == "b2"

    concurrent = _trial(evaluation, "E1-S3-D-CONCURRENT-CANDIDATES", PolicyID.B4)
    assert len(concurrent.policy_migration_decisions) == 2
    assert all(
        item.disposition is MigrationDisposition.ALLOW_COMMIT
        for item in concurrent.policy_migration_decisions
    )
    assert concurrent.presentation.commit_outcome == "REJECTED"


def test_b0_b3_do_not_receive_an_invented_binding_migration_policy_surface(evaluation):
    for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
        for scenario_id in S3_E1_SCENARIO_IDS:
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.policy_migration_decisions == ()
            assert (
                trial.evaluation.policy_decision["binding_information_contract"]
                == "NO_BINDING_AWARE_MIGRATION_POLICY_SURFACE"
            )


def test_s3_e1_summary_is_measured_semantic_null_comparator(evaluation):
    for policy_id in PolicyID:
        summary = _policy_summary(evaluation, policy_id)
        sbdr = _rate(summary, CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert summary.operation_count == 7
        assert summary.faulted_operation_count == 6
        assert (sbdr.numerator, sbdr.denominator) == (0, 6)
        assert (sser.numerator, sser.denominator) == (0, 6)
        assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 4
        assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 0
        assert _outcome_count(summary, OutcomeClass.O3_EXPLICIT_NON_SUCCESS) == 2
        assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0


def test_anti_false_zero_real_worker_divergence_is_measured_as_sbdr_and_o4():
    scenario = _SPEC_BY_ID["E1-S3-C-LATE-OLD-OWNER"]
    trial = _run_case(
        scenario,
        (PolicyID.B4,),
        inject_divergence=True,
    )[0]

    assert trial.presentation.commit_outcome == "REJECTED"
    assert trial.presentation.error_type == "SemanticViolation"
    assert trial.presentation.diverged_from_oracle
    assert trial.final_binding_id == "b1"
    assert trial.final_epoch == 1
    assert CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE in (
        trial.evaluation.metric_violations
    )
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_public_trial_api_rejects_unknown_scenarios_and_non_policy_ids():
    with pytest.raises(ValueError):
        run_s3_e1_trial(PolicyID.B4, "unknown")

    with pytest.raises(TypeError):
        run_s3_e1_trial("B4", "E1-S3-G-SUCCESS-CONTROL")  # type: ignore[arg-type]
