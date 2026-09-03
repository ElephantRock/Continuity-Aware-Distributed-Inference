from experiments.correctness import CorrectnessMetric, OutcomeClass
from experiments.state_lineage import (
    S2_E0_SCENARIOS,
    _run_s2_e0_trial,
    run_s2_e0_paired,
    run_s2_e0_trial,
)
from simulator import PolicyID


_WRONG_LINEAGE_SCENARIOS = (
    "FTR4",
    "S2-ABANDONED-RESIDUE",
    "S2-SIMILAR-DIFFERENT",
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


def _trial(evaluation, scenario_id, policy_id):
    return next(
        trial
        for trial in evaluation.trials
        if trial.scenario_id == scenario_id and trial.policy_id is policy_id
    )


def test_s2_e0_paired_uses_canonical_scenario_then_b0_b4_order():
    evaluation = run_s2_e0_paired()

    assert len(evaluation.trials) == len(S2_E0_SCENARIOS) * len(tuple(PolicyID))
    assert tuple(
        (trial.scenario_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (scenario_id, policy_id)
        for scenario_id in S2_E0_SCENARIOS
        for policy_id in PolicyID
    )


def test_s2_e0_independent_oracle_and_c1_compatibility_agree():
    evaluation = run_s2_e0_paired()

    assert all(
        trial.independent_oracle_compatible == trial.c1_compatible
        for trial in evaluation.trials
    )
    for scenario_id in _WRONG_LINEAGE_SCENARIOS:
        assert not _trial(
            evaluation, scenario_id, PolicyID.B4
        ).independent_oracle_compatible
    assert _trial(
        evaluation, "S2-VALID-ANCESTOR", PolicyID.B4
    ).independent_oracle_compatible
    assert _trial(evaluation, "FTR5", PolicyID.B4).independent_oracle_compatible


def test_wrong_lineage_candidates_are_physically_plausible_but_causally_hidden():
    evaluation = run_s2_e0_paired()

    for scenario_id in _WRONG_LINEAGE_SCENARIOS:
        records = [
            _trial(evaluation, scenario_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        assert len({record.ground_truth_json for record in records}) == 1
        truth = records[0].ground_truth
        assert truth["candidate_key"] == "prefix:shared"
        assert truth["candidate_semantic_type"] == "PREFIX"
        assert truth["candidate_representation"] == "KV"
        assert truth["candidate_state_validity"] == "VALID"
        assert truth["candidate_physical_locations"] == ["w1"]
        assert truth["independent_oracle_compatible"] is False


def test_b1_b3_reuse_wrong_lineage_while_b4_recomputes():
    evaluation = run_s2_e0_paired()

    for scenario_id in _WRONG_LINEAGE_SCENARIOS:
        assert not _trial(evaluation, scenario_id, PolicyID.B0).candidate_reused
        for policy_id in (PolicyID.B1, PolicyID.B2, PolicyID.B3):
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.candidate_reused
            assert trial.state_consumption_event_id is not None
            assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
        b4 = _trial(evaluation, scenario_id, PolicyID.B4)
        assert not b4.candidate_reused
        assert b4.state_consumption_event_id is None
        assert b4.placement_decision.reason == "INCOMPATIBLE_STATE_RECOMPUTE"
        assert b4.evaluation.outcome_class is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY


def test_placement_worker_choice_is_not_itself_counted_as_state_reuse():
    ftr5_b2 = run_s2_e0_trial(PolicyID.B2, "FTR5")

    # Session affinity may select w1 even though the valid State replica is lost.
    # The S2 harness must not turn that worker choice into a State consumption.
    assert ftr5_b2.placement_decision.worker_id == "w1"
    assert ftr5_b2.candidate_reused is False
    assert ftr5_b2.state_consumption_event_id is None


def test_valid_ancestor_control_proves_safety_is_not_disable_all_reuse():
    evaluation = run_s2_e0_paired()

    assert not _trial(
        evaluation, "S2-VALID-ANCESTOR", PolicyID.B0
    ).candidate_reused
    for policy_id in (PolicyID.B1, PolicyID.B2, PolicyID.B3, PolicyID.B4):
        trial = _trial(evaluation, "S2-VALID-ANCESTOR", policy_id)
        assert trial.candidate_reused
        assert trial.independent_oracle_compatible
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY
    assert (
        _trial(
            evaluation, "S2-VALID-ANCESTOR", PolicyID.B4
        ).placement_decision.reason
        == "COMPATIBLE_STATE_LOCALITY_THEN_LOAD"
    )


def test_valid_ancestor_control_is_not_in_faulted_outcome_or_gate_denominators():
    evaluation = run_s2_e0_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "S2-VALID-ANCESTOR", policy_id)
        assert trial.evaluation.fault_id is None
        assert trial.evaluation.metric_opportunities == ()
        summary = _policy_summary(evaluation, policy_id)
        assert summary.operation_count == 5
        assert summary.faulted_operation_count == 4


def test_ftr5_lost_valid_state_recomputes_without_state_consumption():
    evaluation = run_s2_e0_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "FTR5", policy_id)
        assert trial.independent_oracle_compatible
        assert not trial.candidate_reused
        assert trial.state_consumption_event_id is None
        assert trial.evaluation.outcome_class is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY
        assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE not in (
            trial.evaluation.metric_opportunities
        )


def test_wbrr_exogenous_opportunities_are_identical_across_policies():
    evaluation = run_s2_e0_paired()

    for scenario_id in _WRONG_LINEAGE_SCENARIOS:
        records = [
            _trial(evaluation, scenario_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        signatures = {record.exogenous_opportunity_signature for record in records}
        assert len(signatures) == 1
        signature = next(iter(signatures))
        assert len(signature) == 1
        assert signature[0][0] == CorrectnessMetric.WRONG_BRANCH_REUSE_RATE.value


def test_s2_e0_summary_records_expected_wbrr_wscr_and_sser():
    evaluation = run_s2_e0_paired()

    expected_wbrr = {
        PolicyID.B0: (0, 3),
        PolicyID.B1: (3, 3),
        PolicyID.B2: (3, 3),
        PolicyID.B3: (3, 3),
        PolicyID.B4: (0, 3),
    }
    expected_wscr = {
        PolicyID.B0: (0, 0),
        PolicyID.B1: (3, 3),
        PolicyID.B2: (3, 3),
        PolicyID.B3: (3, 3),
        PolicyID.B4: (0, 0),
    }
    expected_sser = {
        PolicyID.B0: (0, 4),
        PolicyID.B1: (3, 4),
        PolicyID.B2: (3, 4),
        PolicyID.B3: (3, 4),
        PolicyID.B4: (0, 4),
    }

    for policy_id in PolicyID:
        summary = _policy_summary(evaluation, policy_id)
        wbrr = _rate(summary, CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
        wscr = _rate(summary, CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert (wbrr.numerator, wbrr.denominator) == expected_wbrr[policy_id]
        assert (wscr.numerator, wscr.denominator) == expected_wscr[policy_id]
        assert (sser.numerator, sser.denominator) == expected_sser[policy_id]


def test_faulted_wrong_state_consumption_is_o4_not_merely_gate_violation():
    trial = run_s2_e0_trial(PolicyID.B3, "FTR4")

    assert CorrectnessMetric.WRONG_BRANCH_REUSE_RATE in trial.evaluation.metric_violations
    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in trial.evaluation.metric_violations
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_measurement_detects_injected_b4_wrong_state_consumption_without_false_zero():
    trial = _run_s2_e0_trial(PolicyID.B4, "FTR4", forced_reuse=True)

    assert trial.candidate_reused
    assert trial.independent_oracle_compatible is False
    assert CorrectnessMetric.WRONG_BRANCH_REUSE_RATE in trial.evaluation.metric_violations
    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in trial.evaluation.metric_violations
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_b4_is_only_policy_marked_as_receiving_c1_lineage_guard():
    evaluation = run_s2_e0_paired()

    for trial in evaluation.trials:
        assert trial.evaluation.policy_decision["c1_lineage_guard_policy_visible"] is (
            trial.policy_id is PolicyID.B4
        )
