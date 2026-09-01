from __future__ import annotations

from experiments.attempt_fencing import (
    S1_E0_SCENARIOS,
    _classify_stale_authority_acceptance,
    run_s1_e0_paired,
    run_s1_e0_trial,
)
from experiments.correctness import CorrectnessMetric
from simulator import PolicyID


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


def test_s1_e0_paired_covers_ftr1_ftr3_for_all_b0_b4():
    evaluation = run_s1_e0_paired()

    assert tuple(
        (trial.scenario_id, trial.policy_id)
        for trial in evaluation.trials
    ) == tuple(
        (scenario_id, policy_id)
        for scenario_id in S1_E0_SCENARIOS
        for policy_id in PolicyID
    )


def test_s1_e0_authoritative_metrics_are_zero_for_all_competent_baselines():
    evaluation = run_s1_e0_paired()

    for policy_id in PolicyID:
        saar = _rate(
            evaluation,
            policy_id,
            CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,
        )
        dfr = _rate(
            evaluation,
            policy_id,
            CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
        )
        sser = _rate(
            evaluation,
            policy_id,
            CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE,
        )

        assert (saar.numerator, saar.denominator, saar.rate) == (0, 2, 0.0)
        assert (dfr.numerator, dfr.denominator, dfr.rate) == (0, 3, 0.0)
        assert (sser.numerator, sser.denominator, sser.rate) == (0, 3, 0.0)


def test_b4_stale_work_admission_fencing_is_not_counted_as_saar_advantage():
    evaluation = run_s1_e0_paired()

    for scenario_id in ("FTR1", "FTR3"):
        for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
            trial = _trial(evaluation, scenario_id, policy_id)
            assert len(trial.stale_admission_decisions) == 1
            decision = trial.stale_admission_decisions[0]
            assert decision.worker_id == "w1"
            assert decision.ranked_worker_ids == ("w1",)

        b4 = _trial(evaluation, scenario_id, PolicyID.B4)
        assert len(b4.stale_admission_decisions) == 1
        decision = b4.stale_admission_decisions[0]
        assert decision.worker_id is None
        assert decision.ranked_worker_ids == ()
        assert decision.reason == "ATTEMPT_FENCED"

    for policy_id in PolicyID:
        saar = _rate(
            evaluation,
            policy_id,
            CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,
        )
        assert saar.numerator == 0


def test_late_superseded_attempt_ground_truth_validates_required_injector_conditions():
    for scenario_id in ("FTR1", "FTR3"):
        trial = run_s1_e0_trial(PolicyID.B4, scenario_id)
        evidence = trial.evaluation.observed_evidence
        supersession = evidence["supersession_checks"]
        late = evidence["late_completion_checks"]
        preconditions = evidence["stale_authority_preconditions"]
        presentations = evidence["stale_authority_presentations"]

        assert len(supersession) == 1
        assert supersession[0]["superseded_execution_status"] == "RUNNING"
        assert supersession[0]["superseded_authority_status"] == "SUPERSEDED"
        assert supersession[0]["retry_authority_status"] == "CURRENT"
        assert supersession[0]["request_current_attempt_id"] == "a2"

        assert len(late) == 1
        assert late[0]["attempt_id"] == "a1"
        assert late[0]["attempt_authority"] == "SUPERSEDED"
        assert late[0]["attempt_execution_status"] == "SUCCEEDED"
        assert late[0]["stale_at_delivery"] is True
        assert late[0]["time"] > late[0]["superseded_at"]

        assert len(preconditions) == 1
        assert preconditions[0]["attempt_id"] == "a1"
        assert preconditions[0]["attempt_authority_before"] == "SUPERSEDED"
        assert preconditions[0]["attempt_execution_before"] == "SUCCEEDED"
        assert preconditions[0]["request_committed_attempt_id_before"] == "a2"

        assert len(presentations) == 1
        assert presentations[0]["attempt_id"] == "a1"
        assert presentations[0]["attempt_authority_after"] == "SUPERSEDED"
        assert presentations[0]["attempt_execution_after"] == "SUCCEEDED"
        assert presentations[0]["accepted_authoritatively"] is False
        assert presentations[0]["request_committed_attempt_id_after"] == "a2"


def test_saar_classifier_permits_post_finalization_committed_stale_attempt():
    accepted = _classify_stale_authority_acceptance(
        attempt_id="a1",
        attempt_authority_before="SUPERSEDED",
        attempt_execution_before="SUCCEEDED",
        committed_attempt_id_after="a1",
        attempt_authority_after="COMMITTED",
    )

    assert accepted is True


def test_saar_classifier_rejects_nonstale_precondition_instead_of_relabeling_it():
    try:
        _classify_stale_authority_acceptance(
            attempt_id="a1",
            attempt_authority_before="CURRENT",
            attempt_execution_before="SUCCEEDED",
            committed_attempt_id_after="a1",
            attempt_authority_after="COMMITTED",
        )
    except AssertionError as exc:
        assert "SUPERSEDED" in str(exc)
    else:
        raise AssertionError("nonstale authority presentation must be rejected")


def test_saar_opportunities_are_actual_stale_authority_presentations():
    for scenario_id, expected_source in (
        ("FTR1", "C4_SUPPLEMENTAL_PRESENTATION"),
        ("FTR3", "CANONICAL_SCENARIO"),
    ):
        trial = run_s1_e0_trial(PolicyID.B4, scenario_id)
        ground_truth = trial.evaluation.ground_truth
        presentations = ground_truth["stale_authority_presentations"]
        physical_late_ids = {
            item["event_id"] for item in ground_truth["physical_late_result_events"]
        }
        saar_ids = tuple(
            event_id
            for metric, event_id in zip(
                trial.evaluation.metric_opportunities,
                trial.evaluation.metric_opportunity_event_ids,
                strict=True,
            )
            if metric is CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE
        )
        finalization_ids = {
            item["event_id"]
            for item in trial.evaluation.observed_evidence["finalization_records"]
        }

        assert len(presentations) == 1
        assert presentations[0]["source"] == expected_source
        assert saar_ids == (presentations[0]["event_id"],)
        assert saar_ids[0] not in physical_late_ids
        assert saar_ids[0] in finalization_ids


def test_ftr1_supplemental_authority_presentation_does_not_mutate_c2_catalogue():
    trial = run_s1_e0_trial(PolicyID.B4, "FTR1")
    ground_truth = trial.evaluation.ground_truth
    presentation = ground_truth["stale_authority_presentations"][0]

    assert presentation["event_id"] == "c4.2a:FTR1:stale-authority-presentation:a1"
    assert presentation["source"] == "C4_SUPPLEMENTAL_PRESENTATION"
    assert presentation["event_id"] not in {
        item["event_id"] for item in ground_truth["physical_late_result_events"]
    }


def test_duplicate_result_has_one_semantic_finalization_and_zero_dfr():
    trial = run_s1_e0_trial(PolicyID.B4, "FTR2")
    records = trial.evaluation.observed_evidence["finalization_records"]

    assert trial.finalization_applied_count == 1
    assert [item["outcome"] for item in records] == ["APPLIED", "IDEMPOTENT"]
    assert trial.authoritative_outcome.committed_attempt_id == "a1"

    evaluation = run_s1_e0_paired()
    dfr = _rate(
        evaluation,
        PolicyID.B4,
        CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
    )
    assert (dfr.numerator, dfr.denominator, dfr.rate) == (0, 3, 0.0)


def test_paired_s1_records_share_ground_truth_and_exogenous_saar_event_identity():
    evaluation = run_s1_e0_paired()

    for scenario_id in S1_E0_SCENARIOS:
        trials = tuple(
            _trial(evaluation, scenario_id, policy_id) for policy_id in PolicyID
        )
        assert len({trial.evaluation.ground_truth_json for trial in trials}) == 1
        assert len(
            {
                tuple(
                    event_id
                    for metric, event_id in zip(
                        trial.evaluation.metric_opportunities,
                        trial.evaluation.metric_opportunity_event_ids,
                        strict=True,
                    )
                    if metric is CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE
                )
                for trial in trials
            }
        ) == 1


def test_s1_e0_trial_is_deterministic_for_same_policy_and_scenario():
    first = run_s1_e0_trial(PolicyID.B4, "FTR1")
    second = run_s1_e0_trial(PolicyID.B4, "FTR1")

    assert first.evaluation.fingerprint == second.evaluation.fingerprint
    assert first.authoritative_outcome == second.authoritative_outcome
    assert first.stale_admission_decisions == second.stale_admission_decisions
