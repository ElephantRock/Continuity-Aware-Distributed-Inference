from __future__ import annotations

from experiments.attempt_fencing import (
    S1_E0_SCENARIOS,
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
        stale = evidence["stale_result_presentations"]

        assert len(supersession) == 1
        assert supersession[0]["superseded_execution_status"] == "RUNNING"
        assert supersession[0]["superseded_authority_status"] == "SUPERSEDED"
        assert supersession[0]["retry_authority_status"] == "CURRENT"
        assert supersession[0]["request_current_attempt_id"] == "a2"

        assert len(stale) == 1
        assert stale[0]["attempt_id"] == "a1"
        assert stale[0]["attempt_authority"] == "SUPERSEDED"
        assert stale[0]["attempt_execution_status"] == "SUCCEEDED"
        assert stale[0]["stale_at_delivery"] is True
        assert stale[0]["time"] > stale[0]["superseded_at"]


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
