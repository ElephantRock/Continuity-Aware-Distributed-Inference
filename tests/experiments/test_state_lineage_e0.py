from experiments.correctness import CorrectnessMetric, OutcomeClass
from experiments.state_lineage import (
    S2_E0_SCENARIOS,
    ApplicationEffect,
    StateConsumptionEvent,
    _build_runtime,
    _execute_consumption_directive,
    _execute_terminal_outcome,
    _run_s2_e0_trial,
    run_s2_e0_paired,
    run_s2_e0_trial,
)
from simulator import PlacementDecision, PolicyID


_WRONG_BRANCH_SCENARIOS = (
    "FTR4",
    "FTR14",
    "S2-SIMILAR-DIFFERENT",
)
_INCOMPATIBLE_CONSUMPTION_SCENARIOS = (
    "FTR4",
    "FTR5",
    "FTR14",
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
    for scenario_id in _INCOMPATIBLE_CONSUMPTION_SCENARIOS:
        assert not _trial(
            evaluation, scenario_id, PolicyID.B4
        ).independent_oracle_compatible
    assert _trial(
        evaluation, "S2-VALID-ANCESTOR", PolicyID.B4
    ).independent_oracle_compatible
    assert _trial(evaluation, "FTR6", PolicyID.B4).independent_oracle_compatible


def test_ftr5_is_canonical_superseded_producer_state():
    evaluation = run_s2_e0_paired()
    records = [
        _trial(evaluation, "FTR5", policy_id).evaluation
        for policy_id in PolicyID
    ]

    assert len({record.ground_truth_json for record in records}) == 1
    truth = records[0].ground_truth
    assert truth["candidate_producer_attempt_id"] == "a1"
    assert truth["candidate_producer_attempt_authority"] == "SUPERSEDED"
    assert truth["candidate_origin_request_committed_attempt_id"] == "a2"
    assert truth["candidate_replica_status"] == "VALID"
    assert truth["candidate_physical_location"] == "w1"
    assert truth["independent_oracle_compatible"] is False


def test_wrong_branch_candidates_are_physically_plausible_but_causally_hidden():
    evaluation = run_s2_e0_paired()

    for scenario_id in _WRONG_BRANCH_SCENARIOS:
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
        assert truth["candidate_replica_status"] == "VALID"
        assert truth["candidate_physical_location"] == "w1"
        assert truth["independent_oracle_compatible"] is False


def test_consumption_execution_rule_is_policy_neutral_and_reason_blind():
    runtime = _build_runtime("FTR4")
    b0_decision = PlacementDecision(
        PolicyID.B0, "w1", ("w1", "w2"), "LOAD_REASON"
    )
    b4_decision = PlacementDecision(
        PolicyID.B4, "w1", ("w1", "w2"), "COMPATIBILITY_REASON"
    )

    b0_event = _execute_consumption_directive(runtime, b0_decision)
    b4_event = _execute_consumption_directive(runtime, b4_decision)

    assert b0_event is not None
    assert b0_event == b4_event
    assert b0_event.worker_id == "w1"
    assert b0_event.state_id == runtime.candidate_state_id


def test_consumption_requires_actual_valid_local_replica():
    ftr4 = _build_runtime("FTR4")
    nonlocal_decision = PlacementDecision(
        PolicyID.B3, "w2", ("w2", "w1"), "EXACT_STATE_LOCALITY_THEN_LOAD"
    )
    assert _execute_consumption_directive(ftr4, nonlocal_decision) is None

    ftr6 = _build_runtime("FTR6")
    lost_local_decision = PlacementDecision(
        PolicyID.B2, "w1", ("w1", "w2"), "SESSION_AFFINITY_THEN_CACHE_LOAD"
    )
    assert _execute_consumption_directive(ftr6, lost_local_decision) is None


def test_reported_consumption_is_explicit_execution_event_not_policy_decision():
    evaluation = run_s2_e0_paired()

    for scenario_id in _INCOMPATIBLE_CONSUMPTION_SCENARIOS:
        assert _trial(evaluation, scenario_id, PolicyID.B0).consumption_event is None
        for policy_id in (PolicyID.B1, PolicyID.B2, PolicyID.B3):
            trial = _trial(evaluation, scenario_id, policy_id)
            assert trial.consumption_event is not None
            assert trial.state_consumption_event_id == trial.consumption_event.event_id
            assert trial.evaluation.observed_evidence["consumption_event"] == (
                trial.consumption_event.to_dict()
            )
            assert "reuse_action" not in trial.evaluation.policy_decision
            assert (
                trial.evaluation.policy_decision[
                    "state_consumption_is_execution_event_not_policy_decision"
                ]
                is True
            )

        b4 = _trial(evaluation, scenario_id, PolicyID.B4)
        assert b4.consumption_event is None
        assert b4.state_consumption_event_id is None
        assert b4.placement_decision.reason == "INCOMPATIBLE_STATE_RECOMPUTE"
        assert (
            b4.evaluation.outcome_class
            is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY
        )


def test_application_profiles_are_policy_invariant_and_not_compatibility_derived():
    evaluation = run_s2_e0_paired()

    expected_effects = {
        "FTR4": ApplicationEffect.WRONG_UNDETECTED.value,
        "FTR5": ApplicationEffect.DETECT_AND_RECOMPUTE.value,
        "FTR14": ApplicationEffect.WRONG_UNDETECTED.value,
        "S2-SIMILAR-DIFFERENT": ApplicationEffect.CORRECT_RESULT.value,
    }
    for scenario_id, expected_effect in expected_effects.items():
        records = [
            _trial(evaluation, scenario_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        assert len({record.ground_truth_json for record in records}) == 1
        truth = records[0].ground_truth
        assert truth["independent_oracle_compatible"] is False
        assert truth["application_profile"]["effect"] == expected_effect

    # Four equally incompatible candidates intentionally exercise three distinct
    # application outcomes. Compatibility therefore cannot determine O-class.
    assert len(set(expected_effects.values())) == 3


def test_terminal_execution_is_explicit_result_bearing_and_authoritatively_committed():
    evaluation = run_s2_e0_paired()

    for trial in evaluation.trials:
        terminal = trial.terminal_event
        assert terminal.reported_success
        assert terminal.authoritative_commit
        assert terminal.request_id == "r"
        assert terminal.attempt_id == "a"
        assert terminal.result_evidence_id
        assert terminal.output_id
        assert trial.evaluation.observed_evidence["terminal_event"] == terminal.to_dict()
        assert trial.evaluation.policy_decision["terminal_oracle_is_not_policy_visible"] is True
        assert (
            trial.evaluation.policy_decision["application_profile_is_not_policy_visible"]
            is True
        )
        assert trial.evaluation.semantic_result.authoritative_commit is True
        assert trial.evaluation.semantic_result.reported_success is True


def test_result_token_is_carried_by_evidence_referenced_from_authoritative_output():
    runtime = _build_runtime("FTR4")
    assert runtime.candidate_replica_id is not None
    event = StateConsumptionEvent(
        event_id="S2:test:consume-ftr4",
        directive_id=runtime.consumption_directive.directive_id,
        state_id=runtime.candidate_state_id,
        replica_id=runtime.candidate_replica_id,
        worker_id="w1",
    )

    terminal = _execute_terminal_outcome(runtime, event)
    output = runtime.core.outputs[terminal.output_id]
    evidence = runtime.core.evidence[terminal.result_evidence_id]

    assert terminal.result_evidence_id in output.evidence_ids
    assert ("result_token", terminal.actual_result_token) in evidence.scope
    assert runtime.core.requests[terminal.request_id].authoritative_output_id == output.id


def test_ftr4_incompatible_consumption_is_independently_wrong_o4():
    trial = run_s2_e0_trial(PolicyID.B3, "FTR4")

    assert trial.consumption_event is not None
    assert CorrectnessMetric.WRONG_BRANCH_REUSE_RATE in trial.evaluation.metric_violations
    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in trial.evaluation.metric_violations
    assert trial.terminal_event.application_effect is ApplicationEffect.WRONG_UNDETECTED
    assert not trial.terminal_event.detected_bad_state
    assert not trial.terminal_event.used_recompute
    assert trial.terminal_event.authoritative_commit
    assert not trial.terminal_event.semantically_correct
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_ftr5_wscr_violation_can_be_detected_and_recomputed_to_o2():
    trial = run_s2_e0_trial(PolicyID.B3, "FTR5")

    assert trial.consumption_event is not None
    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in trial.evaluation.metric_violations
    assert trial.terminal_event.application_effect is ApplicationEffect.DETECT_AND_RECOMPUTE
    assert trial.terminal_event.detected_bad_state
    assert trial.terminal_event.used_recompute
    assert trial.terminal_event.semantically_correct
    assert trial.terminal_event.authoritative_commit
    assert trial.evaluation.outcome_class is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY


def test_wrong_branch_and_state_consumption_can_still_commit_correct_o1():
    trial = run_s2_e0_trial(PolicyID.B3, "S2-SIMILAR-DIFFERENT")

    assert trial.consumption_event is not None
    assert CorrectnessMetric.WRONG_BRANCH_REUSE_RATE in trial.evaluation.metric_violations
    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in trial.evaluation.metric_violations
    assert trial.terminal_event.application_effect is ApplicationEffect.CORRECT_RESULT
    assert not trial.terminal_event.detected_bad_state
    assert not trial.terminal_event.used_recompute
    assert trial.terminal_event.semantically_correct
    assert trial.terminal_event.authoritative_commit
    assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_b4_incompatible_state_recomputes_then_commits_correct_terminal_result():
    trial = run_s2_e0_trial(PolicyID.B4, "FTR4")

    assert trial.consumption_event is None
    assert trial.terminal_event.used_recompute
    assert trial.terminal_event.candidate_result_token is None
    assert trial.terminal_event.authoritative_commit
    assert trial.terminal_event.semantically_correct
    assert trial.terminal_event.actual_result_token == trial.terminal_event.expected_result_token
    assert trial.evaluation.outcome_class is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY


def test_b2_session_affinity_does_not_itself_count_as_consumption():
    ftr6_b2 = run_s2_e0_trial(PolicyID.B2, "FTR6")

    assert ftr6_b2.placement_decision.worker_id == "w1"
    assert ftr6_b2.consumption_event is None
    assert ftr6_b2.state_consumption_event_id is None
    assert ftr6_b2.terminal_event.used_recompute
    assert (
        ftr6_b2.evaluation.outcome_class
        is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY
    )


def test_valid_ancestor_control_proves_b4_does_not_disable_reuse():
    evaluation = run_s2_e0_paired()

    assert _trial(
        evaluation, "S2-VALID-ANCESTOR", PolicyID.B0
    ).consumption_event is None
    for policy_id in (PolicyID.B1, PolicyID.B2, PolicyID.B3, PolicyID.B4):
        trial = _trial(evaluation, "S2-VALID-ANCESTOR", policy_id)
        assert trial.consumption_event is not None
        assert trial.independent_oracle_compatible
        assert trial.terminal_event.application_effect is ApplicationEffect.CORRECT_RESULT
        assert trial.terminal_event.semantically_correct
        assert not trial.terminal_event.used_recompute
        assert (
            trial.evaluation.outcome_class
            is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY
        )
    assert (
        _trial(
            evaluation, "S2-VALID-ANCESTOR", PolicyID.B4
        ).placement_decision.reason
        == "COMPATIBLE_STATE_LOCALITY_THEN_LOAD"
    )


def test_valid_ancestor_control_is_not_in_faulted_or_gate_denominators():
    evaluation = run_s2_e0_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "S2-VALID-ANCESTOR", policy_id)
        assert trial.evaluation.fault_id is None
        assert trial.evaluation.metric_opportunities == ()
        summary = _policy_summary(evaluation, policy_id)
        assert summary.operation_count == 6
        assert summary.faulted_operation_count == 5


def test_ftr6_lost_valid_state_recomputes_without_consumption():
    evaluation = run_s2_e0_paired()

    for policy_id in PolicyID:
        trial = _trial(evaluation, "FTR6", policy_id)
        assert trial.independent_oracle_compatible
        assert trial.consumption_event is None
        assert trial.terminal_event.used_recompute
        assert trial.terminal_event.semantically_correct
        assert (
            trial.evaluation.outcome_class
            is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY
        )
        assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE not in (
            trial.evaluation.metric_opportunities
        )


def test_wbrr_exogenous_opportunities_are_identical_across_policies():
    evaluation = run_s2_e0_paired()

    for scenario_id in _WRONG_BRANCH_SCENARIOS:
        records = [
            _trial(evaluation, scenario_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        signatures = {record.exogenous_opportunity_signature for record in records}
        assert len(signatures) == 1
        signature = next(iter(signatures))
        assert len(signature) == 1
        assert signature[0][0] == CorrectnessMetric.WRONG_BRANCH_REUSE_RATE.value


def test_ftr5_is_wscr_pressure_not_wrong_branch_pressure():
    evaluation = run_s2_e0_paired()

    for policy_id in PolicyID:
        record = _trial(evaluation, "FTR5", policy_id).evaluation
        assert CorrectnessMetric.WRONG_BRANCH_REUSE_RATE not in record.metric_opportunities


def test_s2_e0_summary_records_gate_metrics_independently_from_sser():
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
        PolicyID.B1: (4, 4),
        PolicyID.B2: (4, 4),
        PolicyID.B3: (4, 4),
        PolicyID.B4: (0, 0),
    }
    expected_sser = {
        PolicyID.B0: (0, 5),
        PolicyID.B1: (2, 5),
        PolicyID.B2: (2, 5),
        PolicyID.B3: (2, 5),
        PolicyID.B4: (0, 5),
    }

    for policy_id in PolicyID:
        summary = _policy_summary(evaluation, policy_id)
        wbrr = _rate(summary, CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
        wscr = _rate(summary, CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert (wbrr.numerator, wbrr.denominator) == expected_wbrr[policy_id]
        assert (wscr.numerator, wscr.denominator) == expected_wscr[policy_id]
        assert (sser.numerator, sser.denominator) == expected_sser[policy_id]

        if policy_id in {PolicyID.B1, PolicyID.B2, PolicyID.B3}:
            assert _outcome_count(
                summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
            ) == 2
            assert _outcome_count(
                summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY
            ) == 2
            assert _outcome_count(
                summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY
            ) == 1
        else:
            assert _outcome_count(
                summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
            ) == 0
            assert _outcome_count(
                summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY
            ) == 5
            assert _outcome_count(
                summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY
            ) == 0


def test_measurement_detects_injected_b4_consumption_event_without_false_zero():
    runtime = _build_runtime("FTR4")
    assert runtime.candidate_replica_id is not None
    event = StateConsumptionEvent(
        event_id="S2:injected:wrong-consumption",
        directive_id=runtime.consumption_directive.directive_id,
        state_id=runtime.candidate_state_id,
        replica_id=runtime.candidate_replica_id,
        worker_id="w1",
    )

    trial = _run_s2_e0_trial(
        PolicyID.B4,
        "FTR4",
        injected_consumption_event=event,
    )

    assert trial.consumption_event == event
    assert trial.independent_oracle_compatible is False
    assert CorrectnessMetric.WRONG_BRANCH_REUSE_RATE in trial.evaluation.metric_violations
    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in trial.evaluation.metric_violations
    assert trial.terminal_event.application_effect is ApplicationEffect.WRONG_UNDETECTED
    assert trial.terminal_event.authoritative_commit
    assert not trial.terminal_event.semantically_correct
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION


def test_b4_is_only_policy_marked_as_receiving_c1_lineage_guard():
    evaluation = run_s2_e0_paired()

    for trial in evaluation.trials:
        assert trial.evaluation.policy_decision[
            "c1_lineage_guard_policy_visible"
        ] is (trial.policy_id is PolicyID.B4)
