from __future__ import annotations

from experiments import attempt_fencing_sequences_base as sequence_base
from experiments.attempt_fencing_sequences import (
    S1_SEQUENCE_MANIFESTS,
    AttemptFencingSequenceManifest,
    SequenceAction,
    SequenceActionKind,
    _classify_sequence_stale_acceptance,
    _validated_manifest,
    run_s1_sequence_paired,
    run_s1_sequence_trial,
)
from experiments.correctness import CorrectnessMetric
from simulator import PolicyID


def _rate(evaluation, policy_id: PolicyID, metric: CorrectnessMetric):
    policy = next(
        item for item in evaluation.summary.policy_summaries if item.policy_id is policy_id
    )
    return next(item for item in policy.rates if item.metric is metric)


def _trial(evaluation, case_id: str, policy_id: PolicyID):
    return next(
        item
        for item in evaluation.trials
        if item.manifest.case_id == case_id and item.policy_id is policy_id
    )


def test_sequence_corpus_has_bounded_canonical_pressure_coverage():
    assert len(S1_SEQUENCE_MANIFESTS) == 17
    assert len({item.case_id for item in S1_SEQUENCE_MANIFESTS}) == 17
    assert len({item.fingerprint for item in S1_SEQUENCE_MANIFESTS}) == 17

    families = {
        label.split(".", 1)[0]
        for manifest in S1_SEQUENCE_MANIFESTS
        for label in manifest.pressure_labels
        if "." in label
    }
    assert families == {"A", "B", "C", "D", "E"}


def test_sequence_manifests_fix_exogenous_stale_presentations_before_policy_run():
    for manifest in S1_SEQUENCE_MANIFESTS:
        action_by_id = {item.event_id: item for item in manifest.actions}
        assert manifest.stale_presentation_event_ids
        for event_id in manifest.stale_presentation_event_ids:
            action = action_by_id[event_id]
            assert action.kind in {
                SequenceActionKind.OBSERVE,
                SequenceActionKind.OBSERVE_DUPLICATE,
            }
            assert action.payload_dict["attempt_id"] != manifest.expected_committed_attempt_id


def test_sequence_paired_uses_identical_case_then_b0_b4_ordering():
    evaluation = run_s1_sequence_paired()

    assert tuple(
        (trial.manifest.case_id, trial.policy_id) for trial in evaluation.trials
    ) == tuple(
        (manifest.case_id, policy_id)
        for manifest in S1_SEQUENCE_MANIFESTS
        for policy_id in PolicyID
    )


def test_sequence_authoritative_metrics_are_zero_for_all_competent_baselines():
    evaluation = run_s1_sequence_paired()

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
        explicit = _rate(
            evaluation,
            policy_id,
            CorrectnessMetric.EXPLICIT_NON_SUCCESS_RATE,
        )
        recovery = _rate(
            evaluation,
            policy_id,
            CorrectnessMetric.RECOVERY_RATE,
        )

        assert (saar.numerator, saar.denominator, saar.rate) == (0, 22, 0.0)
        assert (dfr.numerator, dfr.denominator, dfr.rate) == (0, 17, 0.0)
        assert (sser.numerator, sser.denominator, sser.rate) == (0, 17, 0.0)
        assert (explicit.numerator, explicit.denominator, explicit.rate) == (0, 17, 0.0)
        assert (recovery.numerator, recovery.denominator, recovery.rate) == (17, 17, 1.0)


def test_every_sequence_saar_opportunity_reaches_finalization_and_has_pre_post_snapshots():
    for manifest in S1_SEQUENCE_MANIFESTS:
        trial = run_s1_sequence_trial(PolicyID.B4, manifest)
        evidence = trial.evaluation.observed_evidence
        preconditions = evidence["stale_authority_preconditions"]
        presentations = evidence["stale_authority_presentations"]
        records = evidence["semantic_action_records"]

        assert tuple(item["event_id"] for item in preconditions) == (
            manifest.stale_presentation_event_ids
        )
        assert tuple(item["event_id"] for item in presentations) == (
            manifest.stale_presentation_event_ids
        )
        assert all(item["attempt_authority_before"] == "SUPERSEDED" for item in preconditions)
        assert all(item["attempt_execution_before"] == "SUCCEEDED" for item in preconditions)
        assert all(item["accepted_authoritatively"] is False for item in presentations)

        finalization_ids = {
            item["event_id"]
            for item in records
            if item["operation"] == "finalize_request"
        }
        assert set(manifest.stale_presentation_event_ids) <= finalization_ids


def test_sequence_ground_truth_and_exogenous_saar_identity_are_paired_across_policies():
    evaluation = run_s1_sequence_paired()

    for manifest in S1_SEQUENCE_MANIFESTS:
        trials = tuple(_trial(evaluation, manifest.case_id, policy_id) for policy_id in PolicyID)
        assert len({item.evaluation.ground_truth_json for item in trials}) == 1
        saar_ids = {
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
        assert saar_ids == {manifest.stale_presentation_event_ids}


def test_duplicate_delivery_cases_remain_semantically_idempotent_not_dfr_failures():
    for case_id in (
        "B-duplicate-timeout",
        "B-duplicate-retry-start",
        "C-duplicate-late-success",
        "D-duplicate-stale-presentation",
        "E-three-generations-stale-after-commit-duplicates",
    ):
        trial = run_s1_sequence_trial(PolicyID.B4, next(
            item for item in S1_SEQUENCE_MANIFESTS if item.case_id == case_id
        ))
        assert trial.finalization_applied_count == 1
        assert trial.authoritative_outcome.committed_attempt_id == trial.manifest.expected_committed_attempt_id


def test_duplicate_observations_preserve_original_identity_and_observation_time():
    for case_id in (
        "D-duplicate-stale-presentation",
        "E-three-generations-stale-after-commit-duplicates",
    ):
        manifest = next(item for item in S1_SEQUENCE_MANIFESTS if item.case_id == case_id)
        originals = [
            item for item in manifest.actions if item.kind is SequenceActionKind.OBSERVE
        ]
        duplicates = [
            item
            for item in manifest.actions
            if item.kind is SequenceActionKind.OBSERVE_DUPLICATE
        ]
        assert duplicates
        for duplicate in duplicates:
            payload = duplicate.payload_dict
            original = next(
                item
                for item in originals
                if all(
                    item.payload_dict[key] == payload[key]
                    for key in ("request_id", "attempt_id", "evidence_id", "output_id")
                )
            )
            assert float(payload["observed_at"]) == float(
                original.payload_dict.get("observed_at", original.at)
            )
            assert original.at < duplicate.at


def test_public_trial_and_paired_entry_points_normalize_generated_style_manifests():
    raw = next(
        item
        for item in sequence_base.S1_SEQUENCE_MANIFESTS
        if item.case_id == "D-duplicate-stale-presentation"
    )
    expected = next(
        item
        for item in S1_SEQUENCE_MANIFESTS
        if item.case_id == raw.case_id
    )

    direct = run_s1_sequence_trial(PolicyID.B4, raw)
    paired = run_s1_sequence_paired((raw,))

    assert direct.manifest == expected
    assert paired.manifests == (expected,)
    assert tuple(trial.manifest for trial in paired.trials) == tuple(
        expected for _ in PolicyID
    )
    assert all(
        trial.evaluation.ground_truth_json == direct.evaluation.ground_truth_json
        for trial in paired.trials
        if trial.policy_id is PolicyID.B4
    )


def test_normalization_preserves_an_original_explicit_observed_at_for_duplicate_delivery():
    raw = next(
        item
        for item in sequence_base.S1_SEQUENCE_MANIFESTS
        if item.case_id == "D-duplicate-stale-presentation"
    )
    duplicate = next(
        item for item in raw.actions if item.kind is SequenceActionKind.OBSERVE_DUPLICATE
    )
    duplicate_identity = tuple(
        duplicate.payload_dict[key]
        for key in ("request_id", "attempt_id", "evidence_id", "output_id")
    )
    explicit_observed_at = duplicate.at - 1.25
    actions = []
    for action in raw.actions:
        payload = action.payload_dict
        identity = tuple(
            payload.get(key)
            for key in ("request_id", "attempt_id", "evidence_id", "output_id")
        )
        if action.kind is SequenceActionKind.OBSERVE and identity == duplicate_identity:
            payload["observed_at"] = repr(explicit_observed_at)
        actions.append(
            SequenceAction(
                kind=action.kind,
                at=action.at,
                event_id=action.event_id,
                payload=tuple(payload.items()),
            )
        )
    custom = AttemptFencingSequenceManifest(
        case_id=raw.case_id,
        seed=raw.seed,
        pressure_labels=raw.pressure_labels,
        actions=tuple(actions),
        stale_presentation_event_ids=raw.stale_presentation_event_ids,
        expected_committed_attempt_id=raw.expected_committed_attempt_id,
        schema=raw.schema,
    )

    normalized = _validated_manifest(custom)
    normalized_duplicate = next(
        item
        for item in normalized.actions
        if item.kind is SequenceActionKind.OBSERVE_DUPLICATE
    )
    normalized_original = next(
        item
        for item in normalized.actions
        if item.kind is SequenceActionKind.OBSERVE
        and tuple(
            item.payload_dict[key]
            for key in ("request_id", "attempt_id", "evidence_id", "output_id")
        ) == duplicate_identity
    )

    assert float(normalized_original.payload_dict["observed_at"]) == explicit_observed_at
    assert float(normalized_duplicate.payload_dict["observed_at"]) == explicit_observed_at
    trial = run_s1_sequence_trial(PolicyID.B4, custom)
    finalization_ids = {
        item["event_id"]
        for item in trial.evaluation.observed_evidence["semantic_action_records"]
        if item["operation"] == "finalize_request"
    }
    assert set(trial.manifest.stale_presentation_event_ids) <= finalization_ids


def test_repeated_stale_acceptance_can_be_counted_after_prior_bad_commit():
    accepted = _classify_sequence_stale_acceptance(
        attempt_id="a1",
        attempt_authority_before="COMMITTED",
        attempt_execution_before="SUCCEEDED",
        committed_attempt_id_before="a1",
        committed_attempt_id_after="a1",
        attempt_authority_after="COMMITTED",
    )

    assert accepted is True


def test_three_generation_cases_commit_a3_and_retain_all_stale_presentations():
    for case_id, expected_stale_count in (
        ("E-three-generations-stale-before-commit", 2),
        ("E-three-generations-stale-after-commit-duplicates", 4),
    ):
        manifest = next(item for item in S1_SEQUENCE_MANIFESTS if item.case_id == case_id)
        trial = run_s1_sequence_trial(PolicyID.B4, manifest)
        assert trial.authoritative_outcome.committed_attempt_id == "a3"
        assert len(manifest.stale_presentation_event_ids) == expected_stale_count
        assert len(trial.authoritative_outcome.attempts) == 3


def test_b4_late_work_fencing_remains_diagnostic_only():
    evaluation = run_s1_sequence_paired()

    for manifest in S1_SEQUENCE_MANIFESTS:
        b4 = _trial(evaluation, manifest.case_id, PolicyID.B4)
        for decision in b4.stale_admission_decisions:
            assert decision.worker_id is None
            assert decision.ranked_worker_ids == ()
            assert decision.reason == "ATTEMPT_FENCED"

        for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
            trial = _trial(evaluation, manifest.case_id, policy_id)
            assert len(trial.stale_admission_decisions) == len(b4.stale_admission_decisions)
            for decision in trial.stale_admission_decisions:
                assert decision.worker_id == "w1"
                assert decision.ranked_worker_ids == ("w1",)


def test_sequence_trial_and_manifest_fingerprints_are_deterministic():
    manifest = S1_SEQUENCE_MANIFESTS[0]
    reconstructed = AttemptFencingSequenceManifest(
        case_id=manifest.case_id,
        seed=manifest.seed,
        pressure_labels=manifest.pressure_labels,
        actions=manifest.actions,
        stale_presentation_event_ids=manifest.stale_presentation_event_ids,
        expected_committed_attempt_id=manifest.expected_committed_attempt_id,
    )
    first = run_s1_sequence_trial(PolicyID.B4, manifest)
    second = run_s1_sequence_trial(PolicyID.B4, reconstructed)

    assert reconstructed.fingerprint == manifest.fingerprint
    assert first.evaluation.fingerprint == second.evaluation.fingerprint
    assert first.authoritative_outcome == second.authoritative_outcome
