from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from continuity.core import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionContext,
    ReconcileOutcome,
    RequestStatus,
)
from continuity.errors import ContinuityError
from simulator import PolicyID

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
from .evidence_safety import (
    S4_E0_ATTEMPT_ID,
    S4_E0_NOW,
    S4_E0_OUTPUT_ID,
    S4_E0_REQUEST_ID,
    _attempt_finalize,
    _evidence_dict,
    _policy_visible_projection,
    _scaffold_core,
)


S4_ADVERSARIAL_SCHEMA = "cadi.c4.5b.evidence-safety-adversarial-e0.v1"
S4_ADVERSARIAL_COHORT_ID = "C4.5b:S4:EV0"


class EvidencePressureFamily(str, Enum):
    CONTRADICTION = "A_CONTRADICTION"
    FRESHNESS = "B_FRESHNESS_BOUNDARY"
    AUTHORITY_DERIVATION = "C_AUTHORITY_DERIVATION"
    SCOPE = "D_SCOPE"
    SET_INTEGRITY = "E_SET_INTEGRITY"


class EvidenceAdversarialMode(str, Enum):
    CONTRADICTORY_VALID_EXACT = "CONTRADICTORY_VALID_EXACT"
    IDENTICAL_VALID_EXACT_MULTI = "IDENTICAL_VALID_EXACT_MULTI"
    VALID_PLUS_AMBIGUOUS_WRONG_SCOPE = "VALID_PLUS_AMBIGUOUS_WRONG_SCOPE"
    VALID_UNTIL_EQUAL_NOW = "VALID_UNTIL_EQUAL_NOW"
    VALID_UNTIL_EXPIRED = "VALID_UNTIL_EXPIRED"
    OLD_OBSERVATION_NO_EXPIRY = "OLD_OBSERVATION_NO_EXPIRY"
    AUTHORITATIVE_VALID = "AUTHORITATIVE_VALID"
    DERIVED_ONLY_WITH_EXACT_SUPPORT = "DERIVED_ONLY_WITH_EXACT_SUPPORT"
    GOOD_PLUS_WRONG_SCOPE_VALID = "GOOD_PLUS_WRONG_SCOPE_VALID"
    WRONG_SCOPE_ONLY = "WRONG_SCOPE_ONLY"
    EMPTY_EVIDENCE_SET = "EMPTY_EVIDENCE_SET"


@dataclass(frozen=True, slots=True)
class EvidenceAdversarialManifest:
    case_id: str
    pressure_family: EvidencePressureFamily
    mode: EvidenceAdversarialMode
    oracle_reconciliation: ReconcileOutcome
    semantic_commit_allowed: bool
    fault_class: str | None
    acr_event_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("case_id must be a non-empty string")
        if not isinstance(self.pressure_family, EvidencePressureFamily):
            raise TypeError("pressure_family must be EvidencePressureFamily")
        if not isinstance(self.mode, EvidenceAdversarialMode):
            raise TypeError("mode must be EvidenceAdversarialMode")
        if not isinstance(self.oracle_reconciliation, ReconcileOutcome):
            raise TypeError("oracle_reconciliation must be ReconcileOutcome")
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
            raise ValueError("faulted adversarial cases require one ACR EventID")
        if self.semantic_commit_allowed and self.fault_class is not None:
            raise ValueError("faulted adversarial cases are non-committable by oracle")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S4:ADV:{self.case_id}"


def _manifest(
    case_id: str,
    family: EvidencePressureFamily,
    mode: EvidenceAdversarialMode,
    oracle: ReconcileOutcome,
    *,
    commit_allowed: bool,
    fault_class: str | None = None,
) -> EvidenceAdversarialManifest:
    return EvidenceAdversarialManifest(
        case_id=case_id,
        pressure_family=family,
        mode=mode,
        oracle_reconciliation=oracle,
        semantic_commit_allowed=commit_allowed,
        fault_class=fault_class,
        acr_event_id=(
            None if fault_class is None else f"S4:ADV:{case_id}:ambiguous-commit-opportunity"
        ),
    )


S4_ADVERSARIAL_MANIFESTS = (
    _manifest(
        "A1-CONTRADICTORY-VALID-EXACT-SAME-SCOPE",
        EvidencePressureFamily.CONTRADICTION,
        EvidenceAdversarialMode.CONTRADICTORY_VALID_EXACT,
        ReconcileOutcome.AMBIGUOUS,
        commit_allowed=False,
        fault_class="mutually exclusive VALID exact terminal-outcome observations",
    ),
    _manifest(
        "A2-IDENTICAL-VALID-EXACT-MULTI-CONTROL",
        EvidencePressureFamily.CONTRADICTION,
        EvidenceAdversarialMode.IDENTICAL_VALID_EXACT_MULTI,
        ReconcileOutcome.MATCHED,
        commit_allowed=True,
    ),
    _manifest(
        "A3-VALID-PLUS-AMBIGUOUS-WRONG-SCOPE",
        EvidencePressureFamily.CONTRADICTION,
        EvidenceAdversarialMode.VALID_PLUS_AMBIGUOUS_WRONG_SCOPE,
        ReconcileOutcome.AMBIGUOUS,
        commit_allowed=False,
        fault_class="supplied AMBIGUOUS Evidence coexists with sufficient current-attempt Evidence",
    ),
    _manifest(
        "B1-VALID-UNTIL-EQUAL-NOW-CONTROL",
        EvidencePressureFamily.FRESHNESS,
        EvidenceAdversarialMode.VALID_UNTIL_EQUAL_NOW,
        ReconcileOutcome.MATCHED,
        commit_allowed=True,
    ),
    _manifest(
        "B2-VALID-UNTIL-EPSILON-EXPIRED",
        EvidencePressureFamily.FRESHNESS,
        EvidenceAdversarialMode.VALID_UNTIL_EXPIRED,
        ReconcileOutcome.WAIT,
        commit_allowed=False,
        fault_class="valid_until expired immediately before semantic commit",
    ),
    _manifest(
        "B3-OLD-OBSERVATION-NO-EXPIRY-CONTROL",
        EvidencePressureFamily.FRESHNESS,
        EvidenceAdversarialMode.OLD_OBSERVATION_NO_EXPIRY,
        ReconcileOutcome.MATCHED,
        commit_allowed=True,
    ),
    _manifest(
        "C1-AUTHORITATIVE-VALID-CONTROL",
        EvidencePressureFamily.AUTHORITY_DERIVATION,
        EvidenceAdversarialMode.AUTHORITATIVE_VALID,
        ReconcileOutcome.MATCHED,
        commit_allowed=True,
    ),
    _manifest(
        "C2-DERIVED-ONLY-WITH-EXACT-SUPPORT",
        EvidencePressureFamily.AUTHORITY_DERIVATION,
        EvidenceAdversarialMode.DERIVED_ONLY_WITH_EXACT_SUPPORT,
        ReconcileOutcome.WAIT,
        commit_allowed=False,
        fault_class="DERIVED output Evidence below finalize authority minimum",
    ),
    _manifest(
        "D1-GOOD-PLUS-WRONG-SCOPE-VALID-CONTROL",
        EvidencePressureFamily.SCOPE,
        EvidenceAdversarialMode.GOOD_PLUS_WRONG_SCOPE_VALID,
        ReconcileOutcome.MATCHED,
        commit_allowed=True,
    ),
    _manifest(
        "D2-WRONG-SCOPE-ONLY",
        EvidencePressureFamily.SCOPE,
        EvidenceAdversarialMode.WRONG_SCOPE_ONLY,
        ReconcileOutcome.WAIT,
        commit_allowed=False,
        fault_class="EXACT_OBSERVATION Evidence scoped only to another Attempt",
    ),
    _manifest(
        "E1-EMPTY-EVIDENCE-SET",
        EvidencePressureFamily.SET_INTEGRITY,
        EvidenceAdversarialMode.EMPTY_EVIDENCE_SET,
        ReconcileOutcome.WAIT,
        commit_allowed=False,
        fault_class="semantic finalization attempted without Evidence",
    ),
)

S4_ADVERSARIAL_CASE_IDS = tuple(item.case_id for item in S4_ADVERSARIAL_MANIFESTS)
_MANIFEST_BY_ID: Mapping[str, EvidenceAdversarialManifest] = {
    item.case_id: item for item in S4_ADVERSARIAL_MANIFESTS
}


@dataclass(frozen=True, slots=True)
class EvidenceAdversarialTrial:
    policy_id: PolicyID
    manifest: EvidenceAdversarialManifest
    evaluation: CorrectnessEvaluationRecord
    evidence_ids: tuple[str, ...]
    observed_reconciliation: ReconcileOutcome
    reconciliation_diverged_from_oracle: bool
    presentation: Any
    policy_visible_evidence: Mapping[str, Any]
    injected_divergence: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if not isinstance(self.manifest, EvidenceAdversarialManifest):
            raise TypeError("manifest must be EvidenceAdversarialManifest")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy must match trial policy")
        if self.evaluation.scenario_id != self.manifest.case_id:
            raise ValueError("evaluation scenario must match manifest case")
        if not isinstance(self.observed_reconciliation, ReconcileOutcome):
            raise TypeError("observed_reconciliation must be ReconcileOutcome")
        expected_divergence = (
            self.observed_reconciliation is not self.manifest.oracle_reconciliation
        )
        if self.reconciliation_diverged_from_oracle != expected_divergence:
            raise ValueError("reconciliation divergence flag is inconsistent")
        if not isinstance(self.injected_divergence, bool):
            raise TypeError("injected_divergence must be bool")


@dataclass(frozen=True, slots=True)
class EvidenceAdversarialEvaluation:
    trials: tuple[EvidenceAdversarialTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (manifest.case_id, policy_id)
            for manifest in S4_ADVERSARIAL_MANIFESTS
            for policy_id in PolicyID
        )
        actual = tuple((trial.manifest.case_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("adversarial trials must use canonical case then B0-B4 order")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _record(
    core: ContinuityCore,
    *,
    evidence_id: str,
    claim: str,
    authority: EvidenceAuthority = EvidenceAuthority.EXACT_OBSERVATION,
    status: EvidenceStatus = EvidenceStatus.VALID,
    observed_at: float = 1.0,
    scope: frozenset[tuple[str, str]] | None = None,
    valid_until: float | None = None,
    derived_from: frozenset[str] = frozenset(),
    derivation_rule: str | None = None,
) -> Evidence:
    return core.record_evidence(
        Evidence(
            id=evidence_id,
            claim=claim,
            source="C4.5b adversarial Evidence fixture",
            authority=authority,
            status=status,
            observed_at=observed_at,
            scope=(
                frozenset({("attempt", S4_E0_ATTEMPT_ID)})
                if scope is None
                else scope
            ),
            valid_until=valid_until,
            derived_from=derived_from,
            derivation_rule=derivation_rule,
        )
    )


def _build_evidence(
    core: ContinuityCore,
    manifest: EvidenceAdversarialManifest,
) -> tuple[str, ...]:
    prefix = f"S4:ADV:{manifest.case_id}"
    good_scope = frozenset({("attempt", S4_E0_ATTEMPT_ID)})
    wrong_scope = frozenset({("attempt", "other-attempt")})
    mode = manifest.mode

    if mode is EvidenceAdversarialMode.CONTRADICTORY_VALID_EXACT:
        success = _record(
            core,
            evidence_id=f"{prefix}:success",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=good_scope,
        )
        failure = _record(
            core,
            evidence_id=f"{prefix}:failure",
            claim="attempt-terminal-outcome=FAILED",
            scope=good_scope,
        )
        return (success.id, failure.id)

    if mode is EvidenceAdversarialMode.IDENTICAL_VALID_EXACT_MULTI:
        one = _record(
            core,
            evidence_id=f"{prefix}:one",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=good_scope,
        )
        two = _record(
            core,
            evidence_id=f"{prefix}:two",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=good_scope,
        )
        return (one.id, two.id)

    if mode is EvidenceAdversarialMode.VALID_PLUS_AMBIGUOUS_WRONG_SCOPE:
        valid = _record(
            core,
            evidence_id=f"{prefix}:valid",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=good_scope,
        )
        ambiguous = _record(
            core,
            evidence_id=f"{prefix}:ambiguous",
            claim="attempt-terminal-outcome=UNKNOWN",
            status=EvidenceStatus.AMBIGUOUS,
            scope=wrong_scope,
        )
        return (valid.id, ambiguous.id)

    if mode is EvidenceAdversarialMode.VALID_UNTIL_EQUAL_NOW:
        evidence = _record(
            core,
            evidence_id=f"{prefix}:boundary",
            claim="attempt-terminal-outcome=SUCCEEDED",
            valid_until=S4_E0_NOW,
        )
        return (evidence.id,)

    if mode is EvidenceAdversarialMode.VALID_UNTIL_EXPIRED:
        evidence = _record(
            core,
            evidence_id=f"{prefix}:expired",
            claim="attempt-terminal-outcome=SUCCEEDED",
            valid_until=S4_E0_NOW - 1e-9,
        )
        return (evidence.id,)

    if mode is EvidenceAdversarialMode.OLD_OBSERVATION_NO_EXPIRY:
        evidence = _record(
            core,
            evidence_id=f"{prefix}:old",
            claim="attempt-terminal-outcome=SUCCEEDED",
            observed_at=-10_000.0,
        )
        return (evidence.id,)

    if mode is EvidenceAdversarialMode.AUTHORITATIVE_VALID:
        evidence = _record(
            core,
            evidence_id=f"{prefix}:authoritative",
            claim="attempt-terminal-outcome=SUCCEEDED",
            authority=EvidenceAuthority.AUTHORITATIVE,
        )
        return (evidence.id,)

    if mode is EvidenceAdversarialMode.DERIVED_ONLY_WITH_EXACT_SUPPORT:
        support = _record(
            core,
            evidence_id=f"{prefix}:support",
            claim="attempt-terminal-outcome=SUCCEEDED",
        )
        derived = _record(
            core,
            evidence_id=f"{prefix}:derived",
            claim="derived-attempt-terminal-outcome=SUCCEEDED",
            authority=EvidenceAuthority.DERIVED,
            derived_from=frozenset({support.id}),
            derivation_rule="deterministic terminal-outcome summary",
        )
        return (derived.id,)

    if mode is EvidenceAdversarialMode.GOOD_PLUS_WRONG_SCOPE_VALID:
        good = _record(
            core,
            evidence_id=f"{prefix}:good",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=good_scope,
        )
        wrong = _record(
            core,
            evidence_id=f"{prefix}:wrong",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=wrong_scope,
        )
        return (good.id, wrong.id)

    if mode is EvidenceAdversarialMode.WRONG_SCOPE_ONLY:
        evidence = _record(
            core,
            evidence_id=f"{prefix}:wrong",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=wrong_scope,
        )
        return (evidence.id,)

    if mode is EvidenceAdversarialMode.EMPTY_EVIDENCE_SET:
        return ()

    raise AssertionError("unhandled adversarial Evidence mode")


def _explicit_non_success(observed: ReconcileOutcome) -> ExplicitNonSuccess:
    if observed is ReconcileOutcome.AMBIGUOUS:
        return ExplicitNonSuccess.AMBIGUOUS
    return ExplicitNonSuccess.WAIT


def _run_s4_adversarial_trial(
    policy_id: PolicyID,
    case_id: str,
    *,
    inject_divergence: bool = False,
) -> EvidenceAdversarialTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    manifest = _MANIFEST_BY_ID.get(case_id)
    if manifest is None:
        raise ValueError(f"case_id must be one of {S4_ADVERSARIAL_CASE_IDS!r}")
    if inject_divergence and manifest.fault_id is None:
        raise ValueError("anti-false-zero injection requires a faulted case")

    core = _scaffold_core()
    evidence_ids = _build_evidence(core, manifest)
    observed = core.reconcile(
        "finalize",
        evidence_ids,
        now=S4_E0_NOW,
        required_scope={("attempt", S4_E0_ATTEMPT_ID)},
    )
    policy_visible = _policy_visible_projection(
        core, policy_id, evidence_ids, observed
    )
    presentation = _attempt_finalize(
        core,
        manifest,
        evidence_ids,
        observed,
        inject_divergence=inject_divergence,
    )

    if presentation.diverged_from_oracle:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )
    elif manifest.semantic_commit_allowed:
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
            explicit_non_success=_explicit_non_success(observed),
        )

    opportunities: tuple[CorrectnessMetric, ...] = ()
    opportunity_event_ids: tuple[str, ...] = ()
    opportunity_scopes: tuple[MetricOpportunityScope, ...] = ()
    violations: tuple[CorrectnessMetric, ...] = ()
    violation_event_ids: tuple[str, ...] = ()
    if manifest.acr_event_id is not None:
        opportunities = (CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,)
        opportunity_event_ids = (manifest.acr_event_id,)
        opportunity_scopes = (MetricOpportunityScope.EXOGENOUS_PAIRED,)
        if presentation.diverged_from_oracle:
            violations = (CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,)
            violation_event_ids = (manifest.acr_event_id,)

    ground_truth = {
        "schema": S4_ADVERSARIAL_SCHEMA,
        "case_id": manifest.case_id,
        "pressure_family": manifest.pressure_family.value,
        "mode": manifest.mode.value,
        "semantic_commit_allowed": manifest.semantic_commit_allowed,
        "oracle_reconciliation": manifest.oracle_reconciliation.name,
        "acr_event_id": manifest.acr_event_id,
        "oracle_rule": (
            "CONTRADICTORY_AMBIGUOUS_OR_INSUFFICIENT_EVIDENCE_MUST_NOT_"
            "AUTHORITATIVELY_FINALIZE"
        ),
        "semantic_authority": "C1_COMMON_TO_B0_B4",
    }
    observed_evidence = {
        "evidence_objects": [
            _evidence_dict(core.evidence[item]) for item in sorted(core.evidence)
        ],
        "output_evidence_ids": list(evidence_ids),
        "observed_reconciliation": observed.name,
        "oracle_reconciliation": manifest.oracle_reconciliation.name,
        "reconciliation_diverged_from_oracle": (
            observed is not manifest.oracle_reconciliation
        ),
        "finalization_presentation": presentation.to_dict(),
        "request_after": {
            "status": core.requests[S4_E0_REQUEST_ID].status.name,
            "current_attempt_id": core.requests[S4_E0_REQUEST_ID].current_attempt_id,
            "committed_attempt_id": core.requests[S4_E0_REQUEST_ID].committed_attempt_id,
            "authoritative_output_id": core.requests[
                S4_E0_REQUEST_ID
            ].authoritative_output_id,
        },
        "attempt_after": {
            "authority_status": core.attempts[S4_E0_ATTEMPT_ID].authority_status.name,
        },
        "injected_divergence": inject_divergence,
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "policy_visible_evidence": dict(policy_visible),
        "oracle_commit_allowed_is_not_policy_visible": True,
        "oracle_reconciliation_is_not_policy_visible": True,
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S4_ADVERSARIAL_COHORT_ID,
        trial_id=manifest.case_id,
        operation_id=f"finalize:{S4_E0_REQUEST_ID}",
        policy_id=policy_id,
        scenario_id=manifest.case_id,
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
        fault_id=manifest.fault_id,
        fault_class=manifest.fault_class,
    )

    return EvidenceAdversarialTrial(
        policy_id=policy_id,
        manifest=manifest,
        evaluation=evaluation,
        evidence_ids=evidence_ids,
        observed_reconciliation=observed,
        reconciliation_diverged_from_oracle=(
            observed is not manifest.oracle_reconciliation
        ),
        presentation=presentation,
        policy_visible_evidence=policy_visible,
        injected_divergence=inject_divergence,
    )


def run_s4_adversarial_trial(
    policy_id: PolicyID, case_id: str
) -> EvidenceAdversarialTrial:
    return _run_s4_adversarial_trial(policy_id, case_id)


def run_s4_adversarial_paired() -> EvidenceAdversarialEvaluation:
    trials = tuple(
        run_s4_adversarial_trial(policy_id, manifest.case_id)
        for manifest in S4_ADVERSARIAL_MANIFESTS
        for policy_id in PolicyID
    )
    return EvidenceAdversarialEvaluation(
        trials=trials,
        summary=summarize_correctness(tuple(item.evaluation for item in trials)),
    )
