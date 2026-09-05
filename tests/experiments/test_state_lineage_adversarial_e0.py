from experiments.correctness import CorrectnessMetric, OutcomeClass
from experiments.state_lineage import ApplicationEffect, StateConsumptionEvent
from experiments.state_lineage_adversarial import (
    S2_ADVERSARIAL_CASE_IDS,
    S2_ADVERSARIAL_MANIFESTS,
    S2_ADVERSARIAL_SCHEMA,
    StateLineagePressureFamily,
    _build_runtime,
    run_s2_adversarial_case,
    run_s2_adversarial_paired,
    run_s2_adversarial_trial,
)
from simulator import PolicyID


_INCOMPATIBLE_CASES = tuple(
    manifest.case_id
    for manifest in S2_ADVERSARIAL_MANIFESTS
    if not manifest.expected_exact_compatible
)
_CONTROL_CASES = tuple(
    manifest.case_id
    for manifest in S2_ADVERSARIAL_MANIFESTS
    if manifest.expected_exact_compatible
)
_WRONG_BRANCH_CASES = tuple(
    manifest.case_id
    for manifest in S2_ADVERSARIAL_MANIFESTS
    if manifest.wbrr_event_id is not None
)


def _trial(evaluation, case_id, policy_id):
    return next(
        trial
        for trial in evaluation.trials
        if trial.manifest.case_id == case_id and trial.policy_id is policy_id
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


def test_adversarial_manifest_is_canonical_and_covers_all_pressure_families():
    assert len(S2_ADVERSARIAL_MANIFESTS) == 16
    assert S2_ADVERSARIAL_CASE_IDS == tuple(
        manifest.case_id for manifest in S2_ADVERSARIAL_MANIFESTS
    )
    assert len(set(S2_ADVERSARIAL_CASE_IDS)) == len(S2_ADVERSARIAL_CASE_IDS)
    assert {manifest.pressure_family for manifest in S2_ADVERSARIAL_MANIFESTS} == set(
        StateLineagePressureFamily
    )
    assert all(
        manifest.to_dict()["schema"] == S2_ADVERSARIAL_SCHEMA
        for manifest in S2_ADVERSARIAL_MANIFESTS
    )


def test_adversarial_paired_uses_case_then_b0_b4_order():
    evaluation = run_s2_adversarial_paired()

    assert len(evaluation.trials) == len(S2_ADVERSARIAL_MANIFESTS) * len(tuple(PolicyID))
    assert tuple(
        (trial.manifest.case_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (manifest.case_id, policy_id)
        for manifest in S2_ADVERSARIAL_MANIFESTS
        for policy_id in PolicyID
    )


def test_independent_oracle_agrees_with_direct_c1_exact_context_for_every_case():
    evaluation = run_s2_adversarial_paired()

    for trial in evaluation.trials:
        assert trial.independent_oracle_compatible == trial.c1_exact_context_compatible
        assert (
            trial.c1_exact_context_compatible
            == trial.manifest.expected_exact_compatible
        )


def test_phase_pressure_is_covered_without_changing_frozen_c3_phase_surface():
    evaluation = run_s2_adversarial_paired()

    later = _trial(evaluation, "S2C-PHASE-LATER-CONTROL", PolicyID.B4)
    assert later.c1_exact_context_compatible is True
    assert later.b4_effective_compatible is False
    assert later.safe_conservative_b4 is True
    assert later.consumption_event is None
    assert later.placement_decision.reason == "INCOMPATIBLE_STATE_RECOMPUTE"
    assert later.terminal_event.used_recompute
    assert later.terminal_event.semantically_correct
    assert later.evaluation.fault_id is None
    assert later.evaluation.metric_opportunities == ()
    assert later.evaluation.policy_decision["frozen_c3_phase_id_visible"] is False
    assert later.evaluation.policy_decision["c1_exact_context_phase_id"] == "p2"

    assert evaluation.safe_conservative_b4_case_ids == (
        "S2C-PHASE-LATER-CONTROL",
    )

    for case_id in (
        "S2C-PHASE-SAME",
        "S2C-PHASE-EARLIER",
        "S2C-PHASE-NO-CONTEXT",
    ):
        trial = _trial(evaluation, case_id, PolicyID.B4)
        assert trial.c1_exact_context_compatible is False
        assert trial.b4_effective_compatible is False
        assert trial.consumption_event is None
        assert trial.placement_decision.reason == "INCOMPATIBLE_STATE_RECOMPUTE"


def test_phase_origin_incompatible_candidates_are_consumed_by_locality_baselines():
    evaluation = run_s2_adversarial_paired()

    for case_id in (
        "S2C-PHASE-SAME",
        "S2C-PHASE-EARLIER",
        "S2C-PHASE-NO-CONTEXT",
    ):
        for policy_id in (PolicyID.B1, PolicyID.B2, PolicyID.B3):
            trial = _trial(evaluation, case_id, policy_id)
            assert trial.consumption_event is not None
            assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in (
                trial.evaluation.metric_violations
            )


def test_derived_dependency_pressure_recurses_beyond_top_level_state():
    evaluation = run_s2_adversarial_paired()

    valid = _trial(evaluation, "S2D-DERIVED-VALID-CONTROL", PolicyID.B4)
    assert valid.c1_exact_context_compatible
    assert valid.b4_effective_compatible
    assert valid.consumption_event is not None
    assert valid.placement_decision.reason == "COMPATIBLE_STATE_LOCALITY_THEN_LOAD"

    invalid_dependency = _trial(
        evaluation, "S2D-DERIVED-INVALID-DEPENDENCY", PolicyID.B4
    )
    truth = invalid_dependency.evaluation.ground_truth
    assert truth["candidate_state_validity"] == "VALID"
    assert truth["candidate_dependencies"][0]["validity"] == "INVALID"
    assert not invalid_dependency.c1_exact_context_compatible
    assert invalid_dependency.consumption_event is None

    superseded = _trial(
        evaluation, "S2D-DERIVED-SUPERSEDED-DEPENDENCY", PolicyID.B4
    )
    dep = superseded.evaluation.ground_truth["candidate_dependencies"][0]
    assert dep["producer_attempt_id"] == "a1"
    assert dep["producer_attempt_authority"] == "SUPERSEDED"
    assert not superseded.c1_exact_context_compatible
    assert superseded.consumption_event is None

    mixed = _trial(evaluation, "S2D-DERIVED-MIXED-DEPENDENCY", PolicyID.B4)
    dependencies = mixed.evaluation.ground_truth["candidate_dependencies"]
    assert len(dependencies) == 2
    assert any(item["producer_attempt_authority"] == "SUPERSEDED" for item in dependencies)
    assert not mixed.c1_exact_context_compatible
    assert mixed.consumption_event is None


def test_three_generation_producer_pressure_fences_first_attempt_state():
    trial = run_s2_adversarial_case(PolicyID.B4, "S2B-THREE-GEN-SUPERSEDED")
    truth = trial.evaluation.ground_truth

    assert truth["candidate_producer_attempt_id"] == "a1"
    assert truth["candidate_producer_attempt_authority"] == "SUPERSEDED"
    assert trial.c1_exact_context_compatible is False
    assert trial.b4_effective_compatible is False
    assert trial.consumption_event is None
    assert trial.terminal_event.used_recompute


def test_positive_controls_prove_adversarial_corpus_does_not_disable_reuse():
    evaluation = run_s2_adversarial_paired()
    expected_b4_reuse = {
        "S2A-DEEP-ANCESTOR-CONTROL",
        "S2B-COMMITTED-PRODUCER-CONTROL",
        "S2B-REQUEST-ORIGIN-CONTROL",
        "S2D-DERIVED-VALID-CONTROL",
        "S2E-VALID-CONTROL",
    }

    assert set(_CONTROL_CASES) == expected_b4_reuse | {"S2C-PHASE-LATER-CONTROL"}
    for case_id in expected_b4_reuse:
        trial = _trial(evaluation, case_id, PolicyID.B4)
        assert trial.c1_exact_context_compatible
        assert trial.b4_effective_compatible
        assert trial.consumption_event is not None
        assert not trial.terminal_event.used_recompute
        assert trial.terminal_event.semantically_correct
        assert trial.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY


def test_top_level_invalid_state_remains_physically_plausible_but_b4_rejects():
    evaluation = run_s2_adversarial_paired()
    b4 = _trial(evaluation, "S2E-TOP-LEVEL-INVALID", PolicyID.B4)
    truth = b4.evaluation.ground_truth

    assert truth["candidate_state_validity"] == "INVALID"
    assert truth["candidate_replica_status"] == "VALID"
    assert truth["candidate_physical_location"] == "w1"
    assert not b4.c1_exact_context_compatible
    assert b4.consumption_event is None

    for policy_id in (PolicyID.B1, PolicyID.B2, PolicyID.B3):
        trial = _trial(evaluation, "S2E-TOP-LEVEL-INVALID", policy_id)
        assert trial.consumption_event is not None
        assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in (
            trial.evaluation.metric_violations
        )


def test_wrong_branch_opportunities_are_fixed_and_identical_across_policies():
    evaluation = run_s2_adversarial_paired()

    assert _WRONG_BRANCH_CASES == (
        "S2A-DEEP-SIBLING",
        "S2A-DEEP-ABANDONED",
    )
    for case_id in _WRONG_BRANCH_CASES:
        records = [
            _trial(evaluation, case_id, policy_id).evaluation
            for policy_id in PolicyID
        ]
        signatures = {record.exogenous_opportunity_signature for record in records}
        assert len(signatures) == 1
        signature = next(iter(signatures))
        assert len(signature) == 1
        assert signature[0][0] == CorrectnessMetric.WRONG_BRANCH_REUSE_RATE.value


def test_application_effects_keep_gate_violation_independent_from_terminal_outcome():
    evaluation = run_s2_adversarial_paired()

    wrong = _trial(evaluation, "S2A-DEEP-SIBLING", PolicyID.B3)
    detected = _trial(evaluation, "S2A-DEEP-ABANDONED", PolicyID.B3)
    coincidentally_correct = _trial(
        evaluation, "S2B-THREE-GEN-SUPERSEDED", PolicyID.B3
    )

    assert wrong.terminal_event.application_effect is ApplicationEffect.WRONG_UNDETECTED
    assert wrong.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
    assert detected.terminal_event.application_effect is ApplicationEffect.DETECT_AND_RECOMPUTE
    assert detected.evaluation.outcome_class is OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY
    assert coincidentally_correct.terminal_event.application_effect is ApplicationEffect.CORRECT_RESULT
    assert coincidentally_correct.evaluation.outcome_class is OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY

    for trial in (wrong, detected, coincidentally_correct):
        assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in (
            trial.evaluation.metric_violations
        )


def test_adversarial_summary_records_expected_gate_and_sser_result():
    evaluation = run_s2_adversarial_paired()
    expected_wbrr = {
        PolicyID.B0: (0, 2),
        PolicyID.B1: (2, 2),
        PolicyID.B2: (2, 2),
        PolicyID.B3: (2, 2),
        PolicyID.B4: (0, 2),
    }
    expected_wscr = {
        PolicyID.B0: (0, 0),
        PolicyID.B1: (10, 10),
        PolicyID.B2: (10, 10),
        PolicyID.B3: (10, 10),
        PolicyID.B4: (0, 0),
    }
    expected_sser = {
        PolicyID.B0: (0, 10),
        PolicyID.B1: (4, 10),
        PolicyID.B2: (4, 10),
        PolicyID.B3: (4, 10),
        PolicyID.B4: (0, 10),
    }

    assert len(_INCOMPATIBLE_CASES) == 10
    for policy_id in PolicyID:
        summary = _policy_summary(evaluation, policy_id)
        assert summary.operation_count == 16
        assert summary.faulted_operation_count == 10
        wbrr = _rate(summary, CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
        wscr = _rate(summary, CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
        sser = _rate(summary, CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE)
        assert (wbrr.numerator, wbrr.denominator) == expected_wbrr[policy_id]
        assert (wscr.numerator, wscr.denominator) == expected_wscr[policy_id]
        assert (sser.numerator, sser.denominator) == expected_sser[policy_id]

        if policy_id in {PolicyID.B1, PolicyID.B2, PolicyID.B3}:
            assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 4
            assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 3
            assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 3
        else:
            assert _outcome_count(summary, OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION) == 0
            assert _outcome_count(summary, OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY) == 10
            assert _outcome_count(summary, OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY) == 0


def test_measurement_detects_injected_b4_incompatible_consumption_without_false_zero():
    manifest = next(
        item
        for item in S2_ADVERSARIAL_MANIFESTS
        if item.case_id == "S2A-DEEP-SIBLING"
    )
    runtime = _build_runtime(manifest)
    assert runtime.candidate_replica_id is not None
    event = StateConsumptionEvent(
        event_id="S2B:injected:wrong-consumption",
        directive_id=runtime.consumption_directive.directive_id,
        state_id=runtime.candidate_state_id,
        replica_id=runtime.candidate_replica_id,
        worker_id="w1",
    )

    trial = run_s2_adversarial_trial(
        PolicyID.B4,
        manifest,
        injected_consumption_event=event,
    )

    assert trial.consumption_event == event
    assert not trial.independent_oracle_compatible
    assert CorrectnessMetric.WRONG_BRANCH_REUSE_RATE in trial.evaluation.metric_violations
    assert CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE in trial.evaluation.metric_violations
    assert trial.evaluation.outcome_class is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
