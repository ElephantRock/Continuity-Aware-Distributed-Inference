from __future__ import annotations

from typing import Any, Sequence

from continuity.entities import AttemptAuthority, ExecutionStatus, RequestStatus
from simulator import (
    AdapterOutcome,
    ContinuityAdapter,
    CoreContinuityAuthority,
    DiscreteEventSimulator,
    EventKind,
    PolicyID,
    authoritative_outcome,
    build_baseline_policies,
    decide_placement,
)

from . import attempt_fencing_sequences_base as _base
from .attempt_fencing_sequences_base import *  # noqa: F401,F403
from .correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    ExplicitNonSuccess,
    MetricOpportunityScope,
    RecoveryAction,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)


def _validated_manifest(
    manifest: AttemptFencingSequenceManifest,
) -> AttemptFencingSequenceManifest:
    """Normalize generated templates into executable exogenous manifests.

    A duplicate observation is the same observation delivered again, so it must
    retain the original Evidence/Output identity and original observed-at time.
    Delivery EventIDs remain distinct correctness-sensitive presentations.
    Stale denominator EventIDs are ordered by deterministic delivery order.
    """

    stale_ids = frozenset(manifest.stale_presentation_event_ids)
    first_observation_time: dict[tuple[str, str, str, str], float] = {}
    actions: list[SequenceAction] = []

    for action in manifest.actions:
        payload = action.payload_dict
        if action.kind in {
            SequenceActionKind.OBSERVE,
            SequenceActionKind.OBSERVE_DUPLICATE,
        }:
            identity = (
                payload["request_id"],
                payload["attempt_id"],
                payload["evidence_id"],
                payload["output_id"],
            )
            if action.kind is SequenceActionKind.OBSERVE_DUPLICATE:
                original_at = first_observation_time.get(identity)
                if original_at is None:
                    raise ValueError(
                        "duplicate observation must follow an original observation with identical identity"
                    )
                payload["observed_at"] = repr(original_at)
            else:
                original_observed_at = float(payload.get("observed_at", action.at))
                first_observation_time.setdefault(identity, original_observed_at)

        actions.append(
            SequenceAction(
                kind=action.kind,
                at=action.at,
                event_id=action.event_id,
                payload=tuple(payload.items()),
            )
        )

    ordered_stale_ids = tuple(
        action.event_id for action in actions if action.event_id in stale_ids
    )
    return AttemptFencingSequenceManifest(
        case_id=manifest.case_id,
        seed=manifest.seed,
        pressure_labels=manifest.pressure_labels,
        actions=tuple(actions),
        stale_presentation_event_ids=ordered_stale_ids,
        expected_committed_attempt_id=manifest.expected_committed_attempt_id,
        schema=manifest.schema,
    )


S1_SEQUENCE_MANIFESTS = tuple(
    _validated_manifest(manifest) for manifest in _base.S1_SEQUENCE_MANIFESTS
)


def _schedule_validated_action(
    adapter: ContinuityAdapter,
    action: SequenceAction,
) -> None:
    if action.kind not in {
        SequenceActionKind.OBSERVE,
        SequenceActionKind.OBSERVE_DUPLICATE,
    }:
        _base._schedule_action(adapter, action)
        return

    data = action.payload_dict
    observed_at = float(data.get("observed_at", action.at))
    adapter.schedule_observation(
        data["request_id"],
        data["attempt_id"],
        data["evidence_id"],
        data["output_id"],
        at=action.at,
        observed_at=observed_at,
        duplicated=action.kind is SequenceActionKind.OBSERVE_DUPLICATE,
        event_id=action.event_id,
    )


def _classify_sequence_stale_acceptance(
    *,
    attempt_id: str,
    attempt_authority_before: str,
    attempt_execution_before: str,
    committed_attempt_id_before: str | None,
    committed_attempt_id_after: str | None,
    attempt_authority_after: str,
) -> bool:
    """Classify one oracle-stale presentation without suppressing prior failures.

    The ordinary path reuses the repaired C4.2a classifier. If an earlier stale
    presentation has already (incorrectly) committed this Attempt, later duplicate
    presentations remain oracle-stale inputs; they must be measurable rather than
    crashing because the defective system has changed SUPERSEDED -> COMMITTED.
    """

    if attempt_execution_before != ExecutionStatus.SUCCEEDED.name:
        raise AssertionError(
            "SAAR authority presentation requires delivered physical success"
        )

    if attempt_authority_before == AttemptAuthority.SUPERSEDED.name:
        return _base._classify_stale_authority_acceptance(
            attempt_id=attempt_id,
            attempt_authority_before=attempt_authority_before,
            attempt_execution_before=attempt_execution_before,
            committed_attempt_id_after=committed_attempt_id_after,
            attempt_authority_after=attempt_authority_after,
        )

    if (
        attempt_authority_before == AttemptAuthority.COMMITTED.name
        and committed_attempt_id_before == attempt_id
    ):
        accepted = committed_attempt_id_after == attempt_id
        if accepted and attempt_authority_after != AttemptAuthority.COMMITTED.name:
            raise AssertionError(
                "previously accepted stale Attempt must remain COMMITTED while authoritative"
            )
        return accepted

    raise AssertionError(
        "oracle-stale presentation must be SUPERSEDED, or already COMMITTED only because a prior stale presentation was accepted"
    )


def _replay_validated_manifest(
    policy_id: PolicyID,
    manifest: AttemptFencingSequenceManifest,
) -> _base._ReplayResult:
    sim = DiscreteEventSimulator(seed=manifest.seed)
    core = _base._scaffold_core()
    stale_ids = frozenset(manifest.stale_presentation_event_ids)
    action_by_id = {item.event_id: item for item in manifest.actions}
    stale_preconditions: dict[str, dict[str, Any]] = {}
    stale_presentations: list[dict[str, Any]] = []
    retry_checks: list[dict[str, Any]] = []
    late_completion_checks: list[dict[str, Any]] = []
    stale_admission_decisions = []

    def on_stale_precondition(_sim: DiscreteEventSimulator, event: Any) -> None:
        if event.event_id not in stale_ids:
            return
        action = action_by_id[event.event_id]
        data = action.payload_dict
        attempt = core.attempts[data["attempt_id"]]
        request = core.requests[data["request_id"]]
        if attempt.execution_status is not ExecutionStatus.SUCCEEDED:
            raise AssertionError(
                "manifest-labeled SAAR presentation requires delivered SUCCEEDED execution"
            )
        already_bad_commit = (
            attempt.authority_status is AttemptAuthority.COMMITTED
            and request.committed_attempt_id == attempt.id
        )
        if (
            attempt.authority_status is not AttemptAuthority.SUPERSEDED
            and not already_bad_commit
        ):
            raise AssertionError(
                "manifest-labeled stale presentation must be SUPERSEDED unless a prior stale presentation already committed it"
            )
        stale_preconditions[event.event_id] = {
            "event_id": event.event_id,
            "time": sim.now,
            "request_id": data["request_id"],
            "attempt_id": data["attempt_id"],
            "attempt_authority_before": attempt.authority_status.name,
            "attempt_execution_before": attempt.execution_status.name,
            "request_current_attempt_id_before": request.current_attempt_id,
            "request_committed_attempt_id_before": request.committed_attempt_id,
            "prior_stale_acceptance_before": already_bad_commit,
        }

    sim.register_handler(EventKind.OBSERVATION_CREATED, on_stale_precondition)
    sim.register_handler(EventKind.OBSERVATION_DUPLICATED, on_stale_precondition)

    adapter = ContinuityAdapter(sim, core)
    policy = build_baseline_policies(CoreContinuityAuthority(core))[policy_id]

    def on_retry(_sim: DiscreteEventSimulator, event: Any) -> None:
        data = dict(event.payload)
        retry_id = data.get("retry_attempt_id")
        superseded_id = data.get("superseded_attempt_id")
        request_id = data.get("request_id")
        retry = core.attempts.get(retry_id) if isinstance(retry_id, str) else None
        superseded = core.attempts.get(superseded_id) if isinstance(superseded_id, str) else None
        request = core.requests.get(request_id) if isinstance(request_id, str) else None
        retry_checks.append(
            {
                "event_id": event.event_id,
                "time": sim.now,
                "request_id": request_id,
                "superseded_attempt_id": superseded_id,
                "retry_attempt_id": retry_id,
                "request_status": None if request is None else request.status.name,
                "request_current_attempt_id": None if request is None else request.current_attempt_id,
                "superseded_authority": None if superseded is None else superseded.authority_status.name,
                "superseded_execution": None if superseded is None else superseded.execution_status.name,
                "retry_authority": None if retry is None else retry.authority_status.name,
                "retry_execution": None if retry is None else retry.execution_status.name,
            }
        )

    def on_late_result(_sim: DiscreteEventSimulator, event: Any) -> None:
        data = dict(event.payload)
        attempt_id = data.get("attempt_id")
        attempt = core.attempts.get(attempt_id) if isinstance(attempt_id, str) else None
        if attempt is None:
            return
        request = core.requests[attempt.request_id]
        stale = attempt.authority_status is AttemptAuthority.SUPERSEDED
        late_completion_checks.append(
            {
                "event_id": event.event_id,
                "time": sim.now,
                "attempt_id": attempt.id,
                "attempt_authority": attempt.authority_status.name,
                "attempt_execution": attempt.execution_status.name,
                "request_current_attempt_id": request.current_attempt_id,
                "request_committed_attempt_id": request.committed_attempt_id,
                "stale_at_delivery": stale,
            }
        )
        if stale:
            stale_admission_decisions.append(
                decide_placement(
                    policy,
                    _base._attempt_admission_observation(
                        request_id=attempt.request_id,
                        attempt_id=attempt.id,
                        attempt_authority=attempt.authority_status.name,
                    ),
                )
            )

    def on_stale_presentation(_sim: DiscreteEventSimulator, event: Any) -> None:
        if event.event_id not in stale_ids:
            return
        precondition = stale_preconditions.get(event.event_id)
        if precondition is None:
            raise AssertionError("stale precondition missing before finalization")
        action = action_by_id[event.event_id]
        data = action.payload_dict
        attempt = core.attempts[data["attempt_id"]]
        request = core.requests[data["request_id"]]
        accepted = _classify_sequence_stale_acceptance(
            attempt_id=attempt.id,
            attempt_authority_before=precondition["attempt_authority_before"],
            attempt_execution_before=precondition["attempt_execution_before"],
            committed_attempt_id_before=precondition["request_committed_attempt_id_before"],
            committed_attempt_id_after=request.committed_attempt_id,
            attempt_authority_after=attempt.authority_status.name,
        )
        stale_presentations.append(
            {
                "event_id": event.event_id,
                "time": sim.now,
                "request_id": request.id,
                "attempt_id": attempt.id,
                "attempt_authority_after": attempt.authority_status.name,
                "attempt_execution_after": attempt.execution_status.name,
                "request_current_attempt_id_after": request.current_attempt_id,
                "request_committed_attempt_id_after": request.committed_attempt_id,
                "accepted_authoritatively": accepted,
            }
        )

    sim.register_handler(EventKind.RETRY_STARTED, on_retry)
    sim.register_handler(EventKind.LATE_RESULT, on_late_result)
    sim.register_handler(EventKind.OBSERVATION_CREATED, on_stale_presentation)
    sim.register_handler(EventKind.OBSERVATION_DUPLICATED, on_stale_presentation)

    for action in manifest.actions:
        _schedule_validated_action(adapter, action)
    sim.run()

    if tuple(stale_preconditions) != manifest.stale_presentation_event_ids:
        raise AssertionError(
            "every exogenous stale presentation must capture one pre-finalization stale snapshot"
        )
    observed_ids = tuple(item["event_id"] for item in stale_presentations)
    if observed_ids != manifest.stale_presentation_event_ids:
        raise AssertionError(
            "runtime stale presentations must exactly match the exogenous manifest order"
        )

    return _base._ReplayResult(
        core=core,
        adapter=adapter,
        stale_preconditions=tuple(
            stale_preconditions[event_id]
            for event_id in manifest.stale_presentation_event_ids
        ),
        stale_presentations=tuple(stale_presentations),
        retry_checks=tuple(retry_checks),
        late_completion_checks=tuple(late_completion_checks),
        stale_admission_decisions=tuple(stale_admission_decisions),
    )


def run_s1_sequence_trial(
    policy_id: PolicyID,
    manifest: AttemptFencingSequenceManifest,
) -> AttemptFencingSequenceTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    if not isinstance(manifest, AttemptFencingSequenceManifest):
        raise TypeError("manifest must be AttemptFencingSequenceManifest")

    manifest = _validated_manifest(manifest)
    replay = _replay_validated_manifest(policy_id, manifest)
    core = replay.core
    adapter = replay.adapter
    request = core.requests["r"]
    outcome = authoritative_outcome(core, "r")

    finalization_records = tuple(
        record for record in adapter.records if record.operation == "finalize_request"
    )
    stale_finalization_ids = {
        record.event_id
        for record in finalization_records
        if record.event_id in set(manifest.stale_presentation_event_ids)
    }
    if stale_finalization_ids != set(manifest.stale_presentation_event_ids):
        raise AssertionError(
            "every SAAR opportunity must reach one terminal finalization attempt"
        )

    accepted_event_ids = {
        item["event_id"]
        for item in replay.stale_presentations
        if item["accepted_authoritatively"]
    }
    finalization_applied_count = sum(
        record.outcome is AdapterOutcome.APPLIED for record in finalization_records
    )

    reported_success = (
        request.status is RequestStatus.COMPLETED
        and outcome.authoritative_output_id is not None
        and outcome.committed_attempt_id is not None
    )
    semantic_correct = (
        reported_success
        and outcome.committed_attempt_id == manifest.expected_committed_attempt_id
    )

    opportunities: list[CorrectnessMetric] = []
    opportunity_event_ids: list[str] = []
    opportunity_scopes: list[MetricOpportunityScope] = []
    violations: list[CorrectnessMetric] = []
    violation_event_ids: list[str] = []

    for event_id in manifest.stale_presentation_event_ids:
        opportunities.append(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
        opportunity_event_ids.append(event_id)
        opportunity_scopes.append(MetricOpportunityScope.EXOGENOUS_PAIRED)
        if event_id in accepted_event_ids:
            violations.append(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
            violation_event_ids.append(event_id)

    if reported_success:
        completed_request_event_id = f"{manifest.case_id}:completed-request:r"
        opportunities.append(CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
        opportunity_event_ids.append(completed_request_event_id)
        opportunity_scopes.append(MetricOpportunityScope.POLICY_DERIVED)
        if finalization_applied_count > 1:
            violations.append(CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
            violation_event_ids.append(completed_request_event_id)

    ground_truth = {
        "corpus_schema": manifest.schema,
        "case_id": manifest.case_id,
        "seed": manifest.seed,
        "manifest_fingerprint": manifest.fingerprint,
        "pressure_labels": list(manifest.pressure_labels),
        "ordered_actions": [item.to_dict() for item in manifest.actions],
        "stale_presentation_event_ids": list(manifest.stale_presentation_event_ids),
        "expected_committed_attempt_id": manifest.expected_committed_attempt_id,
    }
    observed_evidence = {
        "stale_authority_preconditions": list(replay.stale_preconditions),
        "stale_authority_presentations": list(replay.stale_presentations),
        "retry_checks": list(replay.retry_checks),
        "late_completion_checks": list(replay.late_completion_checks),
        "authoritative_outcome": {
            "request_status": outcome.request_status,
            "current_attempt_id": outcome.current_attempt_id,
            "committed_attempt_id": outcome.committed_attempt_id,
            "authoritative_output_id": outcome.authoritative_output_id,
        },
        "semantic_action_records": [
            {
                "event_id": record.event_id,
                "event_kind": record.event_kind.value,
                "operation": record.operation,
                "outcome": record.outcome.value,
                "error_type": record.error_type,
            }
            for record in adapter.records
        ],
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "stale_attempt_admission_probe_is_gate_metric": False,
        "stale_attempt_admission_decisions": [
            _base._placement_to_dict(item) for item in replay.stale_admission_decisions
        ],
    }

    if reported_success:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=semantic_correct,
            recovery_actions=(RecoveryAction.RETRY,),
        )
    else:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.FAIL,
            recovery_actions=(RecoveryAction.RETRY,),
        )

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S1_SEQUENCE_COHORT_ID,
        trial_id=manifest.case_id,
        operation_id="r",
        policy_id=policy_id,
        scenario_id=manifest.case_id,
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth=ground_truth,
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=semantic_result,
        metric_opportunities=tuple(opportunities),
        metric_opportunity_event_ids=tuple(opportunity_event_ids),
        metric_opportunity_scopes=tuple(opportunity_scopes),
        metric_violations=tuple(violations),
        metric_violation_event_ids=tuple(violation_event_ids),
        fault_id=f"S1-sequence:{manifest.case_id}",
        fault_class=";".join(manifest.pressure_labels),
    )

    return AttemptFencingSequenceTrial(
        policy_id=policy_id,
        manifest=manifest,
        evaluation=evaluation,
        authoritative_outcome=outcome,
        stale_admission_decisions=replay.stale_admission_decisions,
        finalization_applied_count=finalization_applied_count,
    )


def run_s1_sequence_paired(
    manifests: Sequence[AttemptFencingSequenceManifest] = S1_SEQUENCE_MANIFESTS,
) -> AttemptFencingSequenceEvaluation:
    raw_manifests = tuple(manifests)
    if not raw_manifests:
        raise ValueError("manifests must not be empty")
    if not all(isinstance(item, AttemptFencingSequenceManifest) for item in raw_manifests):
        raise TypeError("manifests must contain AttemptFencingSequenceManifest values")
    manifest_tuple = tuple(_validated_manifest(item) for item in raw_manifests)
    if len({item.case_id for item in manifest_tuple}) != len(manifest_tuple):
        raise ValueError("manifest case IDs must be unique")
    trials = tuple(
        run_s1_sequence_trial(policy_id, manifest)
        for manifest in manifest_tuple
        for policy_id in PolicyID
    )
    summary = summarize_correctness(tuple(item.evaluation for item in trials))
    return AttemptFencingSequenceEvaluation(
        manifests=manifest_tuple,
        trials=trials,
        summary=summary,
    )
