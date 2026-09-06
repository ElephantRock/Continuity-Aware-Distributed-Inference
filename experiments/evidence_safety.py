from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping

from continuity.core import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ReconcileOutcome,
    RequestStatus,
)
from continuity.errors import ContinuityError
from simulator import PolicyID, PolicyObservation, project_observation
from simulator.policies import InformationField

from .correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    CorrectnessSummary,
    ExplicitNonSuccess,
    MetricOpportunityScope,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)


S4_E0_SCHEMA = "cadi.c4.5a.evidence-safety-e0.v1"
S4_E0_COHORT_ID = "C4.5a:S4:EV0"
S4_E0_NOW = 2.0
S4_E0_ATTEMPT_ID = "a1"
S4_E0_REQUEST_ID = "r1"
S4_E0_OUTPUT_ID = "o1"


class EvidenceScenarioMode(str, Enum):
    AMBIGUOUS_ONLY = "AMBIGUOUS_ONLY"
    VALID_PLUS_AMBIGUOUS = "VALID_PLUS_AMBIGUOUS"
    STALE_ONLY = "STALE_ONLY"
    UNKNOWN_ONLY = "UNKNOWN_ONLY"
    FAILED_ONLY = "FAILED_ONLY"
    ESTIMATED_ONLY = "ESTIMATED_ONLY"
    DERIVED_ONLY = "DERIVED_ONLY"
    WRONG_SCOPE = "WRONG_SCOPE"
    EXPIRED_VALID_UNTIL = "EXPIRED_VALID_UNTIL"
    VALID_EXACT_CONTROL = "VALID_EXACT_CONTROL"


@dataclass(frozen=True, slots=True)
class EvidenceScenarioSpec:
    scenario_id: str
    mode: EvidenceScenarioMode
    expected_reconciliation: ReconcileOutcome
    semantic_commit_allowed: bool
    fault_class: str | None
    acr_event_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise ValueError("scenario_id must be a non-empty string")
        if not isinstance(self.mode, EvidenceScenarioMode):
            raise TypeError("mode must be EvidenceScenarioMode")
        if not isinstance(self.expected_reconciliation, ReconcileOutcome):
            raise TypeError("expected_reconciliation must be ReconcileOutcome")
        if not isinstance(self.semantic_commit_allowed, bool):
            raise TypeError("semantic_commit_allowed must be bool")
        if self.fault_class is not None and (
            not isinstance(self.fault_class, str) or not self.fault_class
        ):
            raise ValueError("fault_class must be a non-empty string or None")
        if self.acr_event_id is not None and (
            not isinstance(self.acr_event_id, str) or not self.acr_event_id
        ):
            raise ValueError("acr_event_id must be a non-empty string or None")
        if (self.fault_class is None) != (self.acr_event_id is None):
            raise ValueError("faulted Evidence scenarios require one ACR EventID")
        if self.semantic_commit_allowed and self.fault_class is not None:
            raise ValueError("faulted Evidence scenarios are non-committable by oracle")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S4:EV0:{self.scenario_id}"


S4_E0_SCENARIO_SPECS = (
    EvidenceScenarioSpec(
        "S4-AMBIGUOUS-ONLY",
        EvidenceScenarioMode.AMBIGUOUS_ONLY,
        ReconcileOutcome.AMBIGUOUS,
        False,
        "explicit AMBIGUOUS terminal-output Evidence",
        "S4:EV0:A1:ambiguous-only-finalization",
    ),
    EvidenceScenarioSpec(
        "S4-VALID-PLUS-AMBIGUOUS-SAME-SCOPE",
        EvidenceScenarioMode.VALID_PLUS_AMBIGUOUS,
        ReconcileOutcome.AMBIGUOUS,
        False,
        "otherwise sufficient Evidence mixed with same-scope AMBIGUOUS Evidence",
        "S4:EV0:A2:mixed-valid-ambiguous-finalization",
    ),
    EvidenceScenarioSpec(
        "S4-STALE-ONLY",
        EvidenceScenarioMode.STALE_ONLY,
        ReconcileOutcome.WAIT,
        False,
        "STALE terminal-output Evidence",
        "S4:EV0:A3:stale-finalization",
    ),
    EvidenceScenarioSpec(
        "S4-UNKNOWN-ONLY",
        EvidenceScenarioMode.UNKNOWN_ONLY,
        ReconcileOutcome.WAIT,
        False,
        "UNKNOWN terminal-output Evidence",
        "S4:EV0:A4:unknown-finalization",
    ),
    EvidenceScenarioSpec(
        "S4-FAILED-ONLY",
        EvidenceScenarioMode.FAILED_ONLY,
        ReconcileOutcome.WAIT,
        False,
        "FAILED terminal-output Evidence",
        "S4:EV0:A5:failed-finalization",
    ),
    EvidenceScenarioSpec(
        "S4-ESTIMATED-ONLY",
        EvidenceScenarioMode.ESTIMATED_ONLY,
        ReconcileOutcome.WAIT,
        False,
        "ESTIMATED Evidence below finalize authority minimum",
        "S4:EV0:A6:estimated-finalization",
    ),
    EvidenceScenarioSpec(
        "S4-DERIVED-ONLY",
        EvidenceScenarioMode.DERIVED_ONLY,
        ReconcileOutcome.WAIT,
        False,
        "DERIVED Evidence below finalize authority minimum",
        "S4:EV0:A7:derived-finalization",
    ),
    EvidenceScenarioSpec(
        "S4-WRONG-SCOPE",
        EvidenceScenarioMode.WRONG_SCOPE,
        ReconcileOutcome.WAIT,
        False,
        "EXACT_OBSERVATION Evidence scoped to the wrong Attempt",
        "S4:EV0:A8:wrong-scope-finalization",
    ),
    EvidenceScenarioSpec(
        "S4-EXPIRED-VALID-UNTIL",
        EvidenceScenarioMode.EXPIRED_VALID_UNTIL,
        ReconcileOutcome.WAIT,
        False,
        "otherwise sufficient Evidence expired before finalization",
        "S4:EV0:A9:expired-finalization",
    ),
    EvidenceScenarioSpec(
        "S4-VALID-EXACT-CONTROL",
        EvidenceScenarioMode.VALID_EXACT_CONTROL,
        ReconcileOutcome.MATCHED,
        True,
        None,
        None,
    ),
)
S4_E0_SCENARIOS = tuple(item.scenario_id for item in S4_E0_SCENARIO_SPECS)
_SPEC_BY_ID: Mapping[str, EvidenceScenarioSpec] = {
    item.scenario_id: item for item in S4_E0_SCENARIO_SPECS
}


@dataclass(frozen=True, slots=True)
class EvidenceFinalizePresentation:
    before_request_status: str
    after_request_status: str
    before_attempt_authority: str
    after_attempt_authority: str
    reconciliation: ReconcileOutcome
    commit_outcome: str
    error_type: str | None
    oracle_commit_allowed: bool
    authoritative_commit: bool
    diverged_from_oracle: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "before_request_status": self.before_request_status,
            "after_request_status": self.after_request_status,
            "before_attempt_authority": self.before_attempt_authority,
            "after_attempt_authority": self.after_attempt_authority,
            "reconciliation": self.reconciliation.name,
            "commit_outcome": self.commit_outcome,
            "error_type": self.error_type,
            "oracle_commit_allowed": self.oracle_commit_allowed,
            "authoritative_commit": self.authoritative_commit,
            "diverged_from_oracle": self.diverged_from_oracle,
        }


@dataclass(frozen=True, slots=True)
class EvidenceSafetyTrial:
    policy_id: PolicyID
    scenario_id: str
    evaluation: CorrectnessEvaluationRecord
    presentation: EvidenceFinalizePresentation
    evidence_ids: tuple[str, ...]
    policy_visible_evidence: Mapping[str, Any]
    injected_divergence: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.scenario_id not in S4_E0_SCENARIOS:
            raise ValueError("scenario_id must be a canonical S4 EV0 scenario")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy must match trial policy")
        if self.evaluation.scenario_id != self.scenario_id:
            raise ValueError("evaluation scenario must match trial scenario")
        if not isinstance(self.presentation, EvidenceFinalizePresentation):
            raise TypeError("presentation must be EvidenceFinalizePresentation")
        if not isinstance(self.injected_divergence, bool):
            raise TypeError("injected_divergence must be bool")


@dataclass(frozen=True, slots=True)
class EvidenceSafetyEvaluation:
    trials: tuple[EvidenceSafetyTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (spec.scenario_id, policy_id)
            for spec in S4_E0_SCENARIO_SPECS
            for policy_id in PolicyID
        )
        actual = tuple((trial.scenario_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("S4 EV0 trials must use canonical scenario then B0-B4 ordering")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _scaffold_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request(S4_E0_REQUEST_ID, "c")
    core.start_attempt(S4_E0_ATTEMPT_ID, S4_E0_REQUEST_ID)
    core.complete_attempt(S4_E0_ATTEMPT_ID, succeeded=True)
    return core


def _evidence_dict(evidence: Evidence) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "claim": evidence.claim,
        "source": evidence.source,
        "authority": evidence.authority.name,
        "status": evidence.status.name,
        "observed_at": evidence.observed_at,
        "scope": [list(item) for item in sorted(evidence.scope)],
        "valid_until": evidence.valid_until,
        "confidence": evidence.confidence,
        "derived_from": sorted(evidence.derived_from),
        "derivation_rule": evidence.derivation_rule,
    }


def _record_evidence(
    core: ContinuityCore,
    *,
    evidence_id: str,
    authority: EvidenceAuthority,
    status: EvidenceStatus,
    scope: frozenset[tuple[str, str]],
    valid_until: float | None = None,
    derived_from: frozenset[str] = frozenset(),
    derivation_rule: str | None = None,
) -> Evidence:
    return core.record_evidence(
        Evidence(
            id=evidence_id,
            claim="terminal output observation",
            source="C4.5a deterministic Evidence fixture",
            authority=authority,
            status=status,
            observed_at=1.0,
            scope=scope,
            valid_until=valid_until,
            derived_from=derived_from,
            derivation_rule=derivation_rule,
        )
    )


def _build_evidence(core: ContinuityCore, spec: EvidenceScenarioSpec) -> tuple[str, ...]:
    good_scope = frozenset({("attempt", S4_E0_ATTEMPT_ID)})
    wrong_scope = frozenset({("attempt", "other-attempt")})
    prefix = f"S4:EV0:{spec.scenario_id}"

    if spec.mode is EvidenceScenarioMode.AMBIGUOUS_ONLY:
        evidence = _record_evidence(
            core,
            evidence_id=f"{prefix}:ambiguous",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.AMBIGUOUS,
            scope=good_scope,
        )
        return (evidence.id,)

    if spec.mode is EvidenceScenarioMode.VALID_PLUS_AMBIGUOUS:
        valid = _record_evidence(
            core,
            evidence_id=f"{prefix}:valid",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            scope=good_scope,
        )
        ambiguous = _record_evidence(
            core,
            evidence_id=f"{prefix}:ambiguous",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.AMBIGUOUS,
            scope=good_scope,
        )
        return (valid.id, ambiguous.id)

    if spec.mode is EvidenceScenarioMode.STALE_ONLY:
        evidence = _record_evidence(
            core,
            evidence_id=f"{prefix}:stale",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.STALE,
            scope=good_scope,
        )
        return (evidence.id,)

    if spec.mode is EvidenceScenarioMode.UNKNOWN_ONLY:
        evidence = _record_evidence(
            core,
            evidence_id=f"{prefix}:unknown",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.UNKNOWN,
            scope=good_scope,
        )
        return (evidence.id,)

    if spec.mode is EvidenceScenarioMode.FAILED_ONLY:
        evidence = _record_evidence(
            core,
            evidence_id=f"{prefix}:failed",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.FAILED,
            scope=good_scope,
        )
        return (evidence.id,)

    if spec.mode is EvidenceScenarioMode.ESTIMATED_ONLY:
        evidence = _record_evidence(
            core,
            evidence_id=f"{prefix}:estimated",
            authority=EvidenceAuthority.ESTIMATED,
            status=EvidenceStatus.VALID,
            scope=good_scope,
        )
        return (evidence.id,)

    if spec.mode is EvidenceScenarioMode.DERIVED_ONLY:
        support = _record_evidence(
            core,
            evidence_id=f"{prefix}:support",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            scope=good_scope,
        )
        derived = _record_evidence(
            core,
            evidence_id=f"{prefix}:derived",
            authority=EvidenceAuthority.DERIVED,
            status=EvidenceStatus.VALID,
            scope=good_scope,
            derived_from=frozenset({support.id}),
            derivation_rule="deterministic summary of supporting terminal observation",
        )
        return (derived.id,)

    if spec.mode is EvidenceScenarioMode.WRONG_SCOPE:
        evidence = _record_evidence(
            core,
            evidence_id=f"{prefix}:wrong-scope",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            scope=wrong_scope,
        )
        return (evidence.id,)

    if spec.mode is EvidenceScenarioMode.EXPIRED_VALID_UNTIL:
        evidence = _record_evidence(
            core,
            evidence_id=f"{prefix}:expired",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            scope=good_scope,
            valid_until=1.5,
        )
        return (evidence.id,)

    if spec.mode is EvidenceScenarioMode.VALID_EXACT_CONTROL:
        evidence = _record_evidence(
            core,
            evidence_id=f"{prefix}:valid-control",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            scope=good_scope,
        )
        return (evidence.id,)

    raise AssertionError("unhandled S4 Evidence scenario")


def _policy_visible_projection(
    core: ContinuityCore,
    policy_id: PolicyID,
    evidence_ids: tuple[str, ...],
    reconciliation: ReconcileOutcome,
) -> dict[str, Any]:
    evidence = tuple(core.evidence[item] for item in evidence_ids)
    authority = max((item.authority for item in evidence), default=None)
    if any(item.status is EvidenceStatus.AMBIGUOUS for item in evidence):
        status = EvidenceStatus.AMBIGUOUS
    else:
        status = evidence[0].status if evidence else None
    latest_observed = max((item.observed_at for item in evidence), default=S4_E0_NOW)
    observation = PolicyObservation(
        request_id=S4_E0_REQUEST_ID,
        workers=(),
        attempt_id=S4_E0_ATTEMPT_ID,
        attempt_authority=AttemptAuthority.CURRENT.name,
        evidence_authority=None if authority is None else authority.name,
        evidence_status=None if status is None else status.name,
        evidence_freshness=max(0.0, S4_E0_NOW - latest_observed),
        reconciliation=reconciliation.name,
    )
    view = project_observation(observation, policy_id)
    relevant = (
        InformationField.EVIDENCE_AUTHORITY,
        InformationField.EVIDENCE_STATUS,
        InformationField.EVIDENCE_FRESHNESS,
        InformationField.RECONCILIATION,
    )
    return {
        "available_fields": sorted(field.value for field in view.available_fields),
        "evidence_fields": {
            field.value: view.value(field)
            for field in relevant
            if field in view.available_fields
        },
    }


def _attempt_finalize(
    core: ContinuityCore,
    spec: EvidenceScenarioSpec,
    evidence_ids: tuple[str, ...],
    reconciliation: ReconcileOutcome,
    *,
    inject_divergence: bool,
) -> EvidenceFinalizePresentation:
    core.create_output(S4_E0_OUTPUT_ID, S4_E0_ATTEMPT_ID, True, evidence_ids)
    before_request = core.requests[S4_E0_REQUEST_ID]
    before_attempt = core.attempts[S4_E0_ATTEMPT_ID]
    error: ContinuityError | None = None
    try:
        core.finalize_request(S4_E0_REQUEST_ID, S4_E0_OUTPUT_ID, now=S4_E0_NOW)
    except ContinuityError as exc:
        error = exc

    request = core.requests[S4_E0_REQUEST_ID]
    attempt = core.attempts[S4_E0_ATTEMPT_ID]
    committed_after_c1 = (
        request.status is RequestStatus.COMPLETED
        and request.authoritative_output_id == S4_E0_OUTPUT_ID
        and attempt.authority_status is AttemptAuthority.COMMITTED
    )

    if inject_divergence and not spec.semantic_commit_allowed and not committed_after_c1:
        core.attempts[S4_E0_ATTEMPT_ID] = replace(
            attempt, authority_status=AttemptAuthority.COMMITTED
        )
        core.requests[S4_E0_REQUEST_ID] = replace(
            request,
            status=RequestStatus.COMPLETED,
            current_attempt_id=None,
            committed_attempt_id=S4_E0_ATTEMPT_ID,
            authoritative_output_id=S4_E0_OUTPUT_ID,
        )
        request = core.requests[S4_E0_REQUEST_ID]
        attempt = core.attempts[S4_E0_ATTEMPT_ID]

    authoritative_commit = (
        request.status is RequestStatus.COMPLETED
        and request.authoritative_output_id == S4_E0_OUTPUT_ID
        and attempt.authority_status is AttemptAuthority.COMMITTED
    )
    diverged = authoritative_commit != spec.semantic_commit_allowed
    commit_outcome = (
        "APPLIED"
        if committed_after_c1
        else ("REJECTED" if error is not None else "IDEMPOTENT")
    )
    return EvidenceFinalizePresentation(
        before_request_status=before_request.status.name,
        after_request_status=request.status.name,
        before_attempt_authority=before_attempt.authority_status.name,
        after_attempt_authority=attempt.authority_status.name,
        reconciliation=reconciliation,
        commit_outcome=commit_outcome,
        error_type=None if error is None else type(error).__name__,
        oracle_commit_allowed=spec.semantic_commit_allowed,
        authoritative_commit=authoritative_commit,
        diverged_from_oracle=diverged,
    )


def _explicit_non_success(reconciliation: ReconcileOutcome) -> ExplicitNonSuccess:
    if reconciliation is ReconcileOutcome.AMBIGUOUS:
        return ExplicitNonSuccess.AMBIGUOUS
    return ExplicitNonSuccess.WAIT


def _run_s4_e0_trial(
    policy_id: PolicyID,
    scenario_id: str,
    *,
    inject_divergence: bool = False,
) -> EvidenceSafetyTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    spec = _SPEC_BY_ID.get(scenario_id)
    if spec is None:
        raise ValueError(f"scenario_id must be one of {S4_E0_SCENARIOS!r}")
    if inject_divergence and spec.fault_id is None:
        raise ValueError("anti-false-zero injection requires a faulted scenario")

    core = _scaffold_core()
    evidence_ids = _build_evidence(core, spec)
    reconciliation = core.reconcile(
        "finalize",
        evidence_ids,
        now=S4_E0_NOW,
        required_scope={("attempt", S4_E0_ATTEMPT_ID)},
    )
    if reconciliation is not spec.expected_reconciliation:
        raise AssertionError(
            f"unexpected reconciliation for {spec.scenario_id}: "
            f"{reconciliation.name} != {spec.expected_reconciliation.name}"
        )

    policy_visible = _policy_visible_projection(
        core, policy_id, evidence_ids, reconciliation
    )
    presentation = _attempt_finalize(
        core,
        spec,
        evidence_ids,
        reconciliation,
        inject_divergence=inject_divergence,
    )

    if presentation.diverged_from_oracle:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )
    elif spec.semantic_commit_allowed:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=True,
        )
    else:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=_explicit_non_success(reconciliation),
        )

    opportunities: tuple[CorrectnessMetric, ...] = ()
    opportunity_event_ids: tuple[str, ...] = ()
    opportunity_scopes: tuple[MetricOpportunityScope, ...] = ()
    violations: tuple[CorrectnessMetric, ...] = ()
    violation_event_ids: tuple[str, ...] = ()
    if spec.acr_event_id is not None:
        opportunities = (CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,)
        opportunity_event_ids = (spec.acr_event_id,)
        opportunity_scopes = (MetricOpportunityScope.EXOGENOUS_PAIRED,)
        if presentation.diverged_from_oracle:
            violations = (CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,)
            violation_event_ids = (spec.acr_event_id,)

    evidence_objects = [
        _evidence_dict(core.evidence[item])
        for item in sorted(core.evidence)
    ]
    ground_truth = {
        "schema": S4_E0_SCHEMA,
        "scenario_id": spec.scenario_id,
        "mode": spec.mode.value,
        "semantic_commit_allowed": spec.semantic_commit_allowed,
        "expected_reconciliation": spec.expected_reconciliation.name,
        "acr_event_id": spec.acr_event_id,
        "oracle_rule": "AMBIGUOUS_OR_INSUFFICIENT_EVIDENCE_MUST_NOT_AUTHORITATIVELY_FINALIZE",
        "semantic_authority": "C1_COMMON_TO_B0_B4",
    }
    observed_evidence = {
        "evidence_objects": evidence_objects,
        "output_evidence_ids": list(evidence_ids),
        "reconciliation": reconciliation.name,
        "finalization_presentation": presentation.to_dict(),
        "request_after": {
            "status": core.requests[S4_E0_REQUEST_ID].status.name,
            "current_attempt_id": core.requests[S4_E0_REQUEST_ID].current_attempt_id,
            "committed_attempt_id": core.requests[S4_E0_REQUEST_ID].committed_attempt_id,
            "authoritative_output_id": core.requests[S4_E0_REQUEST_ID].authoritative_output_id,
        },
        "attempt_after": {
            "execution_status": core.attempts[S4_E0_ATTEMPT_ID].execution_status.name,
            "authority_status": core.attempts[S4_E0_ATTEMPT_ID].authority_status.name,
        },
        "injected_divergence": inject_divergence,
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "policy_visible_evidence": dict(policy_visible),
        "reconciliation_is_diagnostic_not_commit_authority": True,
        "oracle_commit_allowed_is_not_policy_visible": True,
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S4_E0_COHORT_ID,
        trial_id=spec.scenario_id,
        operation_id=f"finalize:{S4_E0_REQUEST_ID}",
        policy_id=policy_id,
        scenario_id=spec.scenario_id,
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth=ground_truth,
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=semantic_result,
        metric_opportunities=opportunities,
        metric_opportunity_event_ids=opportunity_event_ids,
        metric_opportunity_scopes=opportunity_scopes,
        metric_violations=violations,
        metric_violation_event_ids=violation_event_ids,
        fault_id=spec.fault_id,
        fault_class=spec.fault_class,
    )

    return EvidenceSafetyTrial(
        policy_id=policy_id,
        scenario_id=spec.scenario_id,
        evaluation=evaluation,
        presentation=presentation,
        evidence_ids=evidence_ids,
        policy_visible_evidence=policy_visible,
        injected_divergence=inject_divergence,
    )


def run_s4_e0_trial(policy_id: PolicyID, scenario_id: str) -> EvidenceSafetyTrial:
    return _run_s4_e0_trial(policy_id, scenario_id)


def run_s4_e0_paired() -> EvidenceSafetyEvaluation:
    trials = tuple(
        run_s4_e0_trial(policy_id, spec.scenario_id)
        for spec in S4_E0_SCENARIO_SPECS
        for policy_id in PolicyID
    )
    return EvidenceSafetyEvaluation(
        trials=trials,
        summary=summarize_correctness(tuple(item.evaluation for item in trials)),
    )
