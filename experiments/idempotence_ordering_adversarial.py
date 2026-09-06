from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from continuity.entities import (
    AttemptAuthority,
    BindingStatus,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    RequestStatus,
    SemanticEvent,
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
from .idempotence_ordering import (
    S5_NOW,
    S5_REQUEST_ID,
    _base_core,
    _capture_finalize,
    _inject_second_finalization,
    _request_snapshot,
    _valid_finalize_core,
)


S5_ADVERSARIAL_SCHEMA = "cadi.c4.6b.idempotence-ordering-adversarial-e0.v1"
S5_ADVERSARIAL_COHORT_ID = "C4.6b:S5:EV0:ADVERSARIAL"


class S5PermutationFamily(str, Enum):
    DUPLICATE_FINALIZE = "P1_DUPLICATE_FINALIZE"
    CONFLICTING_OUTPUT = "P2_CONFLICTING_OUTPUT"
    ATTEMPT_GENERATION = "P3_ATTEMPT_GENERATION"
    DUPLICATE_EVENT_ID = "P4_DUPLICATE_EVENT_ID"
    STALE_BINDING_LOSER = "P5_STALE_BINDING_LOSER"


@dataclass(frozen=True, slots=True)
class S5PermutationVariant:
    variant_id: str
    family: S5PermutationFamily
    actions: tuple[str, ...]
    fault_class: str

    def __post_init__(self) -> None:
        if not isinstance(self.variant_id, str) or not self.variant_id:
            raise ValueError("variant_id must be a non-empty string")
        if not isinstance(self.family, S5PermutationFamily):
            raise TypeError("family must be S5PermutationFamily")
        if not self.actions or not all(isinstance(item, str) and item for item in self.actions):
            raise ValueError("actions must contain non-empty action tokens")
        if not isinstance(self.fault_class, str) or not self.fault_class:
            raise ValueError("fault_class must be a non-empty string")

    @property
    def fault_id(self) -> str:
        return f"S5:ADV:{self.variant_id}"


S5_ADVERSARIAL_CANONICAL_ACTIONS: Mapping[S5PermutationFamily, tuple[str, ...]] = {
    S5PermutationFamily.DUPLICATE_FINALIZE: ("F", "U"),
    S5PermutationFamily.CONFLICTING_OUTPUT: ("F1", "U"),
    S5PermutationFamily.ATTEMPT_GENERATION: ("NEW", "U"),
    S5PermutationFamily.DUPLICATE_EVENT_ID: ("E", "U"),
    S5PermutationFamily.STALE_BINDING_LOSER: ("WIN", "U"),
}

S5_ADVERSARIAL_VARIANTS = (
    S5PermutationVariant(
        "P1-A", S5PermutationFamily.DUPLICATE_FINALIZE, ("F", "F", "U"),
        "same finalization duplicated before unrelated observation",
    ),
    S5PermutationVariant(
        "P1-B", S5PermutationFamily.DUPLICATE_FINALIZE, ("F", "U", "F"),
        "same finalization duplicated around unrelated observation",
    ),
    S5PermutationVariant(
        "P1-C", S5PermutationFamily.DUPLICATE_FINALIZE, ("U", "F", "F"),
        "same finalization duplicated after unrelated observation",
    ),
    S5PermutationVariant(
        "P2-A", S5PermutationFamily.CONFLICTING_OUTPUT, ("F1", "F2", "U"),
        "conflicting output delivered immediately after authoritative completion",
    ),
    S5PermutationVariant(
        "P2-B", S5PermutationFamily.CONFLICTING_OUTPUT, ("F1", "U", "F2"),
        "conflicting output delivered after unrelated observation",
    ),
    S5PermutationVariant(
        "P2-C", S5PermutationFamily.CONFLICTING_OUTPUT, ("U", "F1", "F2"),
        "unrelated observation precedes authoritative and conflicting outputs",
    ),
    S5PermutationVariant(
        "P3-A", S5PermutationFamily.ATTEMPT_GENERATION, ("OLD", "NEW", "U"),
        "superseded A1 finalization delivered before current A2 finalization",
    ),
    S5PermutationVariant(
        "P3-B", S5PermutationFamily.ATTEMPT_GENERATION, ("NEW", "OLD", "U"),
        "superseded A1 finalization delivered after current A2 finalization",
    ),
    S5PermutationVariant(
        "P3-C", S5PermutationFamily.ATTEMPT_GENERATION, ("U", "OLD", "NEW"),
        "unrelated observation then superseded A1 then current A2 finalization",
    ),
    S5PermutationVariant(
        "P4-A", S5PermutationFamily.DUPLICATE_EVENT_ID, ("E", "E", "U"),
        "identical EventID duplicated before unrelated EventID",
    ),
    S5PermutationVariant(
        "P4-B", S5PermutationFamily.DUPLICATE_EVENT_ID, ("E", "U", "E"),
        "identical EventID duplicated around unrelated EventID",
    ),
    S5PermutationVariant(
        "P4-C", S5PermutationFamily.DUPLICATE_EVENT_ID, ("U", "E", "E"),
        "identical EventID duplicated after unrelated EventID",
    ),
    S5PermutationVariant(
        "P5-A", S5PermutationFamily.STALE_BINDING_LOSER, ("WIN", "LATE", "U"),
        "stale concurrent Binding loser delivered immediately after winner commit",
    ),
    S5PermutationVariant(
        "P5-B", S5PermutationFamily.STALE_BINDING_LOSER, ("WIN", "U", "LATE"),
        "stale concurrent Binding loser delivered after unrelated observation",
    ),
)
S5_ADVERSARIAL_VARIANT_IDS = tuple(item.variant_id for item in S5_ADVERSARIAL_VARIANTS)
_VARIANT_BY_ID: Mapping[str, S5PermutationVariant] = {
    item.variant_id: item for item in S5_ADVERSARIAL_VARIANTS
}

_REQUEST_FAMILIES = frozenset(
    {
        S5PermutationFamily.DUPLICATE_FINALIZE,
        S5PermutationFamily.CONFLICTING_OUTPUT,
        S5PermutationFamily.ATTEMPT_GENERATION,
    }
)


@dataclass(frozen=True, slots=True)
class _TracePresentation:
    outcomes: tuple[str, ...]
    snapshot: Mapping[str, Any]
    finalization_effects: tuple[str, ...]
    completed_request_id: str | None


@dataclass(frozen=True, slots=True)
class S5AdversarialTrial:
    policy_id: PolicyID
    variant: S5PermutationVariant
    evaluation: CorrectnessEvaluationRecord
    canonical_snapshot: Mapping[str, Any]
    observed_snapshot: Mapping[str, Any]
    outcomes: tuple[str, ...]
    finalization_effects: tuple[str, ...]
    invariant_violations: tuple[str, ...]
    completed_request_id: str | None
    injected_duplicate_finalization: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if not isinstance(self.variant, S5PermutationVariant):
            raise TypeError("variant must be S5PermutationVariant")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy must match trial policy")
        if self.evaluation.scenario_id != self.variant.variant_id:
            raise ValueError("evaluation scenario must match variant")


@dataclass(frozen=True, slots=True)
class S5AdversarialEvaluation:
    trials: tuple[S5AdversarialTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (variant.variant_id, policy_id)
            for variant in S5_ADVERSARIAL_VARIANTS
            for policy_id in PolicyID
        )
        actual = tuple((trial.variant.variant_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("S5 adversarial trials must use canonical variant then B0-B4 order")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _record_finalize_evidence(core: Any, attempt_id: str, evidence_id: str) -> str:
    evidence = Evidence(
        id=evidence_id,
        claim=f"attempt {attempt_id} terminal outcome succeeded",
        source="C4.6b deterministic permutation fixture",
        authority=EvidenceAuthority.EXACT_OBSERVATION,
        status=EvidenceStatus.VALID,
        observed_at=S5_NOW,
        scope=frozenset({("attempt", attempt_id)}),
        claim_key=f"attempt:{attempt_id}:terminal-outcome",
        claim_value="SUCCEEDED",
    )
    core.record_evidence(evidence)
    return evidence.id


def _attempt_generation_core() -> Any:
    core = _base_core()
    core.create_request(S5_REQUEST_ID, "c")
    core.start_attempt("a1", S5_REQUEST_ID)
    core.complete_attempt("a1", succeeded=True)
    ev1 = _record_finalize_evidence(core, "a1", "ev-a1")
    core.create_output("o1", "a1", True, (ev1,))

    core.start_attempt("a2", S5_REQUEST_ID)
    core.complete_attempt("a2", succeeded=True)
    ev2 = _record_finalize_evidence(core, "a2", "ev-a2")
    core.create_output("o2", "a2", True, (ev2,))
    return core


def _binding_core() -> Any:
    core = _base_core()
    core.activate_initial_binding("b1", "subject", "w1")
    b2 = core.propose_binding("b2", "subject", "w2")
    core.begin_migration("b2")
    b3 = core.propose_binding("b3", "subject", "w3")
    core.begin_migration("b3")
    for binding in (b2, b3):
        core.record_evidence(
            Evidence(
                id=f"ev-{binding.id}",
                claim=f"candidate {binding.id} transfer complete",
                source="C4.6b deterministic permutation fixture",
                authority=EvidenceAuthority.EXACT_OBSERVATION,
                status=EvidenceStatus.VALID,
                observed_at=S5_NOW,
                scope=frozenset(
                    {("binding", binding.id), ("epoch", str(binding.epoch))}
                ),
                claim_key=f"binding:{binding.id}:transfer-complete",
                claim_value="TRUE",
            )
        )
    return core


def _unrelated_event() -> SemanticEvent:
    return SemanticEvent(
        id="event-u",
        kind="OBSERVATION",
        subject_type="continuation",
        subject_id="c",
        payload=frozenset({("note", "UNRELATED")}),
    )


def _primary_event() -> SemanticEvent:
    return SemanticEvent(
        id="event-1",
        kind="OBSERVATION",
        subject_type="attempt",
        subject_id="a1",
        payload=frozenset({("result", "SUCCEEDED")}),
    )


def _request_projection(core: Any, *, attempts: tuple[str, ...]) -> dict[str, Any]:
    request = core.requests[S5_REQUEST_ID]
    return {
        "request_status": request.status.name,
        "committed_attempt_id": request.committed_attempt_id,
        "authoritative_output_id": request.authoritative_output_id,
        "attempt_authority": {
            attempt_id: core.attempts[attempt_id].authority_status.name
            for attempt_id in attempts
        },
    }


def _event_projection(core: Any) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for event_id in sorted(core.events):
        event = core.events[event_id]
        events.append(
            {
                "id": event.id,
                "kind": event.kind,
                "subject_type": event.subject_type,
                "subject_id": event.subject_id,
                "payload": [list(item) for item in sorted(event.payload)],
            }
        )
    return {"events": events}


def _binding_projection(core: Any) -> dict[str, Any]:
    return {
        "current_binding": core.current_binding_by_subject.get("subject"),
        "current_epoch": core.current_epoch_by_subject.get("subject"),
        "bindings": {
            binding_id: {
                "epoch": core.bindings[binding_id].epoch,
                "status": core.bindings[binding_id].status.name,
            }
            for binding_id in ("b1", "b2", "b3")
        },
    }


def _independent_target(family: S5PermutationFamily) -> dict[str, Any]:
    if family in {
        S5PermutationFamily.DUPLICATE_FINALIZE,
        S5PermutationFamily.CONFLICTING_OUTPUT,
    }:
        return {
            "request_status": RequestStatus.COMPLETED.name,
            "committed_attempt_id": "a1",
            "authoritative_output_id": "o1",
            "attempt_authority": {"a1": AttemptAuthority.COMMITTED.name},
        }
    if family is S5PermutationFamily.ATTEMPT_GENERATION:
        return {
            "request_status": RequestStatus.COMPLETED.name,
            "committed_attempt_id": "a2",
            "authoritative_output_id": "o2",
            "attempt_authority": {
                "a1": AttemptAuthority.SUPERSEDED.name,
                "a2": AttemptAuthority.COMMITTED.name,
            },
        }
    if family is S5PermutationFamily.DUPLICATE_EVENT_ID:
        return {
            "events": [
                {
                    "id": "event-1",
                    "kind": "OBSERVATION",
                    "subject_type": "attempt",
                    "subject_id": "a1",
                    "payload": [["result", "SUCCEEDED"]],
                },
                {
                    "id": "event-u",
                    "kind": "OBSERVATION",
                    "subject_type": "continuation",
                    "subject_id": "c",
                    "payload": [["note", "UNRELATED"]],
                },
            ]
        }
    if family is S5PermutationFamily.STALE_BINDING_LOSER:
        return {
            "current_binding": "b2",
            "current_epoch": 2,
            "bindings": {
                "b1": {"epoch": 1, "status": BindingStatus.SUPERSEDED.name},
                "b2": {"epoch": 2, "status": BindingStatus.ACTIVE.name},
                "b3": {"epoch": 3, "status": BindingStatus.MIGRATING.name},
            },
        }
    raise AssertionError("unknown S5 permutation family")


def _execute_trace(
    family: S5PermutationFamily,
    actions: tuple[str, ...],
    *,
    inject_duplicate_finalization: bool = False,
) -> _TracePresentation:
    outcomes: list[str] = []
    finalization_effects: list[str] = []

    if family is S5PermutationFamily.DUPLICATE_FINALIZE:
        core = _valid_finalize_core()
    elif family is S5PermutationFamily.CONFLICTING_OUTPUT:
        core = _valid_finalize_core(create_second_output=True)
    elif family is S5PermutationFamily.ATTEMPT_GENERATION:
        core = _attempt_generation_core()
    elif family is S5PermutationFamily.DUPLICATE_EVENT_ID:
        core = _base_core()
    elif family is S5PermutationFamily.STALE_BINDING_LOSER:
        core = _binding_core()
    else:  # pragma: no cover
        raise AssertionError("unknown S5 permutation family")

    primary = _primary_event()
    unrelated = _unrelated_event()

    for action in actions:
        if action == "U":
            existed = unrelated.id in core.events
            core.record_event(unrelated)
            outcomes.append("U:IDEMPOTENT" if existed else "U:APPLIED")
            continue
        if action == "F":
            outcomes.append(f"F:{_capture_finalize(core, 'o1', finalization_effects)}")
            continue
        if action == "F1":
            outcomes.append(f"F1:{_capture_finalize(core, 'o1', finalization_effects)}")
            continue
        if action == "F2":
            outcomes.append(f"F2:{_capture_finalize(core, 'o2', finalization_effects)}")
            continue
        if action == "OLD":
            outcomes.append(f"OLD:{_capture_finalize(core, 'o1', finalization_effects)}")
            continue
        if action == "NEW":
            outcomes.append(f"NEW:{_capture_finalize(core, 'o2', finalization_effects)}")
            continue
        if action == "E":
            existed = primary.id in core.events
            core.record_event(primary)
            outcomes.append("E:IDEMPOTENT" if existed else "E:APPLIED")
            continue
        if action == "WIN":
            try:
                core.commit_migration("b2", ("ev-b2",), now=S5_NOW)
                outcomes.append("WIN:APPLIED")
            except ContinuityError as exc:
                outcomes.append(f"WIN:REJECTED:{type(exc).__name__}")
            continue
        if action == "LATE":
            try:
                core.commit_migration("b3", ("ev-b3",), now=S5_NOW)
                outcomes.append("LATE:APPLIED")
            except ContinuityError as exc:
                outcomes.append(f"LATE:REJECTED:{type(exc).__name__}")
            continue
        raise ValueError(f"unknown S5 permutation action {action!r}")

    if inject_duplicate_finalization:
        if family is not S5PermutationFamily.DUPLICATE_FINALIZE:
            raise ValueError("anti-false-zero injection is defined only for P1")
        _inject_second_finalization(core, finalization_effects)
        outcomes.append("INJECT:SECOND_FINALIZATION")

    if family in {
        S5PermutationFamily.DUPLICATE_FINALIZE,
        S5PermutationFamily.CONFLICTING_OUTPUT,
    }:
        snapshot = _request_projection(core, attempts=("a1",))
    elif family is S5PermutationFamily.ATTEMPT_GENERATION:
        snapshot = _request_projection(core, attempts=("a1", "a2"))
    elif family is S5PermutationFamily.DUPLICATE_EVENT_ID:
        snapshot = _event_projection(core)
    else:
        snapshot = _binding_projection(core)

    completed_request_id: str | None = None
    if family in _REQUEST_FAMILIES:
        request = core.requests[S5_REQUEST_ID]
        if (
            request.status is RequestStatus.COMPLETED
            and request.committed_attempt_id is not None
            and request.authoritative_output_id is not None
        ):
            completed_request_id = S5_REQUEST_ID

    return _TracePresentation(
        outcomes=tuple(outcomes),
        snapshot=snapshot,
        finalization_effects=tuple(finalization_effects),
        completed_request_id=completed_request_id,
    )


def canonical_trace_snapshot(family: S5PermutationFamily) -> Mapping[str, Any]:
    target = _independent_target(family)
    presentation = _execute_trace(family, S5_ADVERSARIAL_CANONICAL_ACTIONS[family])
    if presentation.snapshot != target:
        raise AssertionError(
            f"canonical trace for {family.value} does not match independent target"
        )
    if family in _REQUEST_FAMILIES and len(presentation.finalization_effects) != 1:
        raise AssertionError("canonical request trace must have exactly one finalization effect")
    return target


def _run_s5_adversarial_trial(
    policy_id: PolicyID,
    variant_id: str,
    *,
    inject_duplicate_finalization: bool = False,
) -> S5AdversarialTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    variant = _VARIANT_BY_ID.get(variant_id)
    if variant is None:
        raise ValueError(f"variant_id must be one of {S5_ADVERSARIAL_VARIANT_IDS!r}")
    if inject_duplicate_finalization and variant.family is not S5PermutationFamily.DUPLICATE_FINALIZE:
        raise ValueError("anti-false-zero injection requires a P1 variant")

    target = dict(canonical_trace_snapshot(variant.family))
    presentation = _execute_trace(
        variant.family,
        variant.actions,
        inject_duplicate_finalization=inject_duplicate_finalization,
    )

    violations: list[str] = []
    if presentation.snapshot != target:
        violations.append("SNAPSHOT_MISMATCH")
    duplicate_finalization = len(presentation.finalization_effects) > 1
    if duplicate_finalization:
        violations.append("DUPLICATE_FINALIZATION")

    if variant.family in _REQUEST_FAMILIES and presentation.completed_request_id is None:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.FAIL,
        )
    elif violations:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )
    else:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=True,
        )

    dfr_event_id: str | None = None
    opportunities: tuple[CorrectnessMetric, ...] = ()
    opportunity_event_ids: tuple[str, ...] = ()
    opportunity_scopes: tuple[MetricOpportunityScope, ...] = ()
    metric_violations: tuple[CorrectnessMetric, ...] = ()
    violation_event_ids: tuple[str, ...] = ()
    if presentation.completed_request_id is not None:
        dfr_event_id = (
            f"S5:ADV:{variant.variant_id}:completed-request:"
            f"{presentation.completed_request_id}"
        )
        opportunities = (CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,)
        opportunity_event_ids = (dfr_event_id,)
        opportunity_scopes = (MetricOpportunityScope.POLICY_DERIVED,)
        if duplicate_finalization:
            metric_violations = (CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,)
            violation_event_ids = (dfr_event_id,)

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S5_ADVERSARIAL_COHORT_ID,
        trial_id=variant.variant_id,
        operation_id=f"s5-adversarial:{variant.variant_id}",
        policy_id=policy_id,
        scenario_id=variant.variant_id,
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth={
            "schema": S5_ADVERSARIAL_SCHEMA,
            "family": variant.family.value,
            "variant_id": variant.variant_id,
            "canonical_actions": list(S5_ADVERSARIAL_CANONICAL_ACTIONS[variant.family]),
            "variant_actions": list(variant.actions),
            "canonical_target_snapshot": target,
            "semantic_authority": "C1_COMMON_TO_B0_B4",
            "dfr_denominator_scope": "POLICY_DERIVED_COMPLETED_LOGICAL_REQUEST",
        },
        observed_evidence={
            "application_outcomes": list(presentation.outcomes),
            "observed_snapshot": dict(presentation.snapshot),
            "snapshot_matches_canonical_target": presentation.snapshot == target,
            "finalization_effects": list(presentation.finalization_effects),
            "semantic_finalization_count": len(presentation.finalization_effects),
            "completed_request_id": presentation.completed_request_id,
            "dfr_opportunity_observed": dfr_event_id is not None,
            "invariant_violations": violations,
            "injected_duplicate_finalization": inject_duplicate_finalization,
        },
        policy_decision={
            "semantic_authority": "C1_COMMON_TO_B0_B4",
            "policy_specific_s5_information_used": False,
            "common_commit_guards_preserved": True,
        },
        semantic_result=semantic_result,
        metric_opportunities=opportunities,
        metric_opportunity_event_ids=opportunity_event_ids,
        metric_opportunity_scopes=opportunity_scopes,
        metric_violations=metric_violations,
        metric_violation_event_ids=violation_event_ids,
        fault_id=variant.fault_id,
        fault_class=variant.fault_class,
    )
    return S5AdversarialTrial(
        policy_id=policy_id,
        variant=variant,
        evaluation=evaluation,
        canonical_snapshot=target,
        observed_snapshot=presentation.snapshot,
        outcomes=presentation.outcomes,
        finalization_effects=presentation.finalization_effects,
        invariant_violations=tuple(violations),
        completed_request_id=presentation.completed_request_id,
        injected_duplicate_finalization=inject_duplicate_finalization,
    )


def run_s5_adversarial_trial(policy_id: PolicyID, variant_id: str) -> S5AdversarialTrial:
    return _run_s5_adversarial_trial(policy_id, variant_id)


def run_s5_adversarial_paired() -> S5AdversarialEvaluation:
    trials = tuple(
        run_s5_adversarial_trial(policy_id, variant.variant_id)
        for variant in S5_ADVERSARIAL_VARIANTS
        for policy_id in PolicyID
    )
    return S5AdversarialEvaluation(
        trials=trials,
        summary=summarize_correctness(tuple(item.evaluation for item in trials)),
    )
