from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping

from continuity.core import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    BindingStatus,
    ContinuationLifecycle,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionStatus,
    RequestStatus,
    SemanticEvent,
    StateValidity,
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


S5_E0_SCHEMA = "cadi.c4.6a.idempotence-ordering-e0.v1"
S5_E0_COHORT_ID = "C4.6a:S5:EV0"
S5_REQUEST_ID = "r1"
S5_ATTEMPT_ID = "a1"
S5_OUTPUT_ID = "o1"
S5_NOW = 10.0


class S5DeterministicMode(str, Enum):
    SAME_OUTPUT_FINALIZE_TWICE = "SAME_OUTPUT_FINALIZE_TWICE"
    CONFLICTING_OUTPUT_AFTER_COMPLETION = "CONFLICTING_OUTPUT_AFTER_COMPLETION"
    DUPLICATE_SEMANTIC_EVENT_ID = "DUPLICATE_SEMANTIC_EVENT_ID"
    CONFLICTING_DUPLICATE_EVENT_ID = "CONFLICTING_DUPLICATE_EVENT_ID"
    DUPLICATE_MIGRATION_COMMIT = "DUPLICATE_MIGRATION_COMMIT"
    TERMINAL_CONTINUATION_REPLAY = "TERMINAL_CONTINUATION_REPLAY"
    INVALID_STATE_REPLAY = "INVALID_STATE_REPLAY"
    FIRST_VALID_FINALIZATION_CONTROL = "FIRST_VALID_FINALIZATION_CONTROL"


@dataclass(frozen=True, slots=True)
class S5DeterministicScenario:
    scenario_id: str
    mode: S5DeterministicMode
    fault_class: str | None
    creates_dfr_opportunity: bool

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise ValueError("scenario_id must be a non-empty string")
        if not isinstance(self.mode, S5DeterministicMode):
            raise TypeError("mode must be S5DeterministicMode")
        if not isinstance(self.creates_dfr_opportunity, bool):
            raise TypeError("creates_dfr_opportunity must be bool")
        if self.fault_class is not None and (
            not isinstance(self.fault_class, str) or not self.fault_class
        ):
            raise ValueError("fault_class must be a non-empty string or None")
        if self.creates_dfr_opportunity and self.fault_class is None:
            raise ValueError("expected DFR Gate opportunities must be faulted operations")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S5:EV0:{self.scenario_id}"


S5_E0_SCENARIOS = (
    S5DeterministicScenario(
        "S5-D1-SAME-OUTPUT-FINALIZE-TWICE",
        S5DeterministicMode.SAME_OUTPUT_FINALIZE_TWICE,
        "same finalization delivered twice",
        True,
    ),
    S5DeterministicScenario(
        "S5-D2-CONFLICTING-OUTPUT-AFTER-COMPLETION",
        S5DeterministicMode.CONFLICTING_OUTPUT_AFTER_COMPLETION,
        "late conflicting output presented after request completion",
        True,
    ),
    S5DeterministicScenario(
        "S5-D3-DUPLICATE-SEMANTIC-EVENT-ID",
        S5DeterministicMode.DUPLICATE_SEMANTIC_EVENT_ID,
        "same semantic EventID delivered twice",
        False,
    ),
    S5DeterministicScenario(
        "S5-D4-CONFLICTING-DUPLICATE-EVENT-ID",
        S5DeterministicMode.CONFLICTING_DUPLICATE_EVENT_ID,
        "same EventID reused with conflicting payload",
        False,
    ),
    S5DeterministicScenario(
        "S5-D5-DUPLICATE-MIGRATION-COMMIT",
        S5DeterministicMode.DUPLICATE_MIGRATION_COMMIT,
        "migration commit replayed after authority advanced",
        False,
    ),
    S5DeterministicScenario(
        "S5-D6-TERMINAL-CONTINUATION-REPLAY",
        S5DeterministicMode.TERMINAL_CONTINUATION_REPLAY,
        "terminal continuation update replayed then resurrection attempted",
        False,
    ),
    S5DeterministicScenario(
        "S5-D7-INVALID-STATE-REPLAY",
        S5DeterministicMode.INVALID_STATE_REPLAY,
        "state invalidation replayed then resurrection attempted",
        False,
    ),
    S5DeterministicScenario(
        "S5-D8-FIRST-VALID-FINALIZATION-CONTROL",
        S5DeterministicMode.FIRST_VALID_FINALIZATION_CONTROL,
        None,
        False,
    ),
)
S5_E0_SCENARIO_IDS = tuple(item.scenario_id for item in S5_E0_SCENARIOS)
_SCENARIO_BY_ID: Mapping[str, S5DeterministicScenario] = {
    item.scenario_id: item for item in S5_E0_SCENARIOS
}


@dataclass(frozen=True, slots=True)
class _ExecutionPresentation:
    application_outcomes: tuple[str, ...]
    semantic_snapshot: Mapping[str, Any]
    finalization_effects: tuple[str, ...]
    invariant_violations: tuple[str, ...]
    intended_semantic_state_reached: bool
    completed_request_id: str | None


@dataclass(frozen=True, slots=True)
class S5DeterministicTrial:
    policy_id: PolicyID
    scenario: S5DeterministicScenario
    evaluation: CorrectnessEvaluationRecord
    application_outcomes: tuple[str, ...]
    semantic_snapshot: Mapping[str, Any]
    finalization_effects: tuple[str, ...]
    invariant_violations: tuple[str, ...]
    completed_request_id: str | None
    injected_duplicate_finalization: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if not isinstance(self.scenario, S5DeterministicScenario):
            raise TypeError("scenario must be S5DeterministicScenario")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy must match trial policy")
        if self.evaluation.scenario_id != self.scenario.scenario_id:
            raise ValueError("evaluation scenario must match trial scenario")
        if self.completed_request_id is not None and (
            not isinstance(self.completed_request_id, str) or not self.completed_request_id
        ):
            raise ValueError("completed_request_id must be a non-empty string or None")
        if not isinstance(self.injected_duplicate_finalization, bool):
            raise TypeError("injected_duplicate_finalization must be bool")


@dataclass(frozen=True, slots=True)
class S5DeterministicEvaluation:
    trials: tuple[S5DeterministicTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (scenario.scenario_id, policy_id)
            for scenario in S5_E0_SCENARIOS
            for policy_id in PolicyID
        )
        actual = tuple((trial.scenario.scenario_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("S5 EV0 trials must use canonical scenario then B0-B4 order")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _base_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    return core


def _valid_finalize_core(*, create_second_output: bool = False) -> ContinuityCore:
    core = _base_core()
    core.create_request(S5_REQUEST_ID, "c")
    core.start_attempt(S5_ATTEMPT_ID, S5_REQUEST_ID)
    core.complete_attempt(S5_ATTEMPT_ID, succeeded=True)
    evidence = Evidence(
        id="ev-finalize",
        claim="attempt terminal outcome succeeded",
        source="C4.6a deterministic fixture",
        authority=EvidenceAuthority.EXACT_OBSERVATION,
        status=EvidenceStatus.VALID,
        observed_at=S5_NOW,
        scope=frozenset({("attempt", S5_ATTEMPT_ID)}),
        claim_key=f"attempt:{S5_ATTEMPT_ID}:terminal-outcome",
        claim_value="SUCCEEDED",
    )
    core.record_evidence(evidence)
    core.create_output(S5_OUTPUT_ID, S5_ATTEMPT_ID, True, (evidence.id,))
    if create_second_output:
        core.create_output("o2", S5_ATTEMPT_ID, True, (evidence.id,))
    return core


def _request_snapshot(core: ContinuityCore) -> dict[str, Any]:
    request = core.requests[S5_REQUEST_ID]
    attempt = core.attempts[S5_ATTEMPT_ID]
    return {
        "request_status": request.status.name,
        "current_attempt_id": request.current_attempt_id,
        "committed_attempt_id": request.committed_attempt_id,
        "authoritative_output_id": request.authoritative_output_id,
        "attempt_execution_status": attempt.execution_status.name,
        "attempt_authority_status": attempt.authority_status.name,
    }


def _completed_request_id(snapshot: Mapping[str, Any]) -> str | None:
    if (
        snapshot.get("request_status") == RequestStatus.COMPLETED.name
        and snapshot.get("committed_attempt_id") is not None
        and snapshot.get("authoritative_output_id") is not None
    ):
        return S5_REQUEST_ID
    return None


def _capture_finalize(
    core: ContinuityCore,
    output_id: str,
    effects: list[str],
) -> str:
    before = _request_snapshot(core)
    try:
        core.finalize_request(S5_REQUEST_ID, output_id, now=S5_NOW)
        outcome = "APPLIED_OR_IDEMPOTENT"
    except ContinuityError as exc:
        outcome = f"REJECTED:{type(exc).__name__}"
    after = _request_snapshot(core)
    if (
        after["request_status"] == RequestStatus.COMPLETED.name
        and after["authoritative_output_id"] is not None
        and (
            before["request_status"] != RequestStatus.COMPLETED.name
            or before["authoritative_output_id"] != after["authoritative_output_id"]
        )
    ):
        effects.append(str(after["authoritative_output_id"]))
    return outcome


def _inject_second_finalization(core: ContinuityCore, effects: list[str]) -> None:
    request = core.requests[S5_REQUEST_ID]
    if request.status is not RequestStatus.COMPLETED:
        raise AssertionError("anti-false-zero injection requires a completed request")
    core.requests[S5_REQUEST_ID] = replace(
        request,
        authoritative_output_id="injected-second-output",
    )
    effects.append("injected-second-output")


def _event_snapshot(core: ContinuityCore, event_id: str) -> dict[str, Any]:
    event = core.events[event_id]
    return {
        "event_order": list(core.event_order),
        "event_count": len(core.events),
        "event_payload": sorted(event.payload),
    }


def _run_mode(
    scenario: S5DeterministicScenario,
    *,
    inject_duplicate_finalization: bool = False,
) -> _ExecutionPresentation:
    outcomes: list[str] = []
    finalization_effects: list[str] = []
    violations: list[str] = []
    intended = True
    completed_request_id: str | None = None

    if scenario.mode is S5DeterministicMode.SAME_OUTPUT_FINALIZE_TWICE:
        core = _valid_finalize_core()
        outcomes.append(_capture_finalize(core, S5_OUTPUT_ID, finalization_effects))
        first = _request_snapshot(core)
        outcomes.append(_capture_finalize(core, S5_OUTPUT_ID, finalization_effects))
        second = _request_snapshot(core)
        if first != second:
            violations.append("IDEMPOTENCE_VIOLATION")
        if len(finalization_effects) != 1:
            violations.append("DUPLICATE_FINALIZATION")
        if inject_duplicate_finalization:
            _inject_second_finalization(core, finalization_effects)
            violations.append("IDEMPOTENCE_VIOLATION")
        snapshot = _request_snapshot(core)
        completed_request_id = _completed_request_id(snapshot)
        intended = (
            completed_request_id == S5_REQUEST_ID
            and (
                snapshot["authoritative_output_id"] == S5_OUTPUT_ID
                if not inject_duplicate_finalization
                else snapshot["authoritative_output_id"] == "injected-second-output"
            )
        )

    elif scenario.mode is S5DeterministicMode.CONFLICTING_OUTPUT_AFTER_COMPLETION:
        core = _valid_finalize_core(create_second_output=True)
        outcomes.append(_capture_finalize(core, S5_OUTPUT_ID, finalization_effects))
        first = _request_snapshot(core)
        outcomes.append(_capture_finalize(core, "o2", finalization_effects))
        second = _request_snapshot(core)
        if first != second:
            violations.append("MONOTONICITY_VIOLATION")
        if second["authoritative_output_id"] != S5_OUTPUT_ID:
            violations.append("COMPLETED_OUTPUT_MUTATED")
        if len(finalization_effects) != 1:
            violations.append("DUPLICATE_FINALIZATION")
        snapshot = second
        completed_request_id = _completed_request_id(snapshot)
        intended = (
            completed_request_id == S5_REQUEST_ID
            and second["authoritative_output_id"] == S5_OUTPUT_ID
        )

    elif scenario.mode is S5DeterministicMode.DUPLICATE_SEMANTIC_EVENT_ID:
        core = _base_core()
        event = SemanticEvent(
            id="event-1",
            kind="OBSERVATION",
            subject_type="attempt",
            subject_id="a1",
            payload=frozenset({("result", "SUCCEEDED")}),
        )
        core.record_event(event)
        outcomes.append("APPLIED")
        before = (dict(core.events), tuple(core.event_order))
        core.record_event(event)
        outcomes.append("IDEMPOTENT")
        after = (dict(core.events), tuple(core.event_order))
        if before != after or core.event_order.count(event.id) != 1:
            violations.append("IDEMPOTENCE_VIOLATION")
        snapshot = _event_snapshot(core, event.id)

    elif scenario.mode is S5DeterministicMode.CONFLICTING_DUPLICATE_EVENT_ID:
        core = _base_core()
        first_event = SemanticEvent(
            id="event-1",
            kind="OBSERVATION",
            subject_type="attempt",
            subject_id="a1",
            payload=frozenset({("result", "SUCCEEDED")}),
        )
        conflicting = SemanticEvent(
            id="event-1",
            kind="OBSERVATION",
            subject_type="attempt",
            subject_id="a1",
            payload=frozenset({("result", "FAILED")}),
        )
        core.record_event(first_event)
        outcomes.append("APPLIED")
        before = (dict(core.events), tuple(core.event_order))
        try:
            core.record_event(conflicting)
            outcomes.append("APPLIED_CONFLICT")
        except ContinuityError as exc:
            outcomes.append(f"REJECTED:{type(exc).__name__}")
        after = (dict(core.events), tuple(core.event_order))
        if before != after:
            violations.append("IDENTITY_CORRUPTION")
        if core.events["event-1"] != first_event:
            violations.append("MONOTONICITY_VIOLATION")
        snapshot = _event_snapshot(core, "event-1")

    elif scenario.mode is S5DeterministicMode.DUPLICATE_MIGRATION_COMMIT:
        core = _base_core()
        initial = core.activate_initial_binding("b1", "subject", "w1")
        candidate = core.propose_binding("b2", "subject", "w2")
        core.begin_migration("b2")
        evidence = Evidence(
            id="ev-migration",
            claim="candidate transfer complete",
            source="C4.6a deterministic fixture",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=S5_NOW,
            scope=frozenset({("binding", "b2"), ("epoch", str(candidate.epoch))}),
            claim_key="binding:b2:transfer-complete",
            claim_value="TRUE",
        )
        core.record_evidence(evidence)
        core.commit_migration("b2", (evidence.id,), now=S5_NOW)
        outcomes.append("APPLIED")
        before = {
            "current_binding": core.current_binding_by_subject["subject"],
            "current_epoch": core.current_epoch_by_subject["subject"],
            "b1_status": core.bindings["b1"].status.name,
            "b2_status": core.bindings["b2"].status.name,
        }
        try:
            core.commit_migration("b2", (evidence.id,), now=S5_NOW)
            outcomes.append("APPLIED_DUPLICATE")
        except ContinuityError as exc:
            outcomes.append(f"REJECTED:{type(exc).__name__}")
        after = {
            "current_binding": core.current_binding_by_subject["subject"],
            "current_epoch": core.current_epoch_by_subject["subject"],
            "b1_status": core.bindings["b1"].status.name,
            "b2_status": core.bindings["b2"].status.name,
        }
        if before != after:
            violations.append("IDEMPOTENCE_VIOLATION")
        if after != {
            "current_binding": "b2",
            "current_epoch": candidate.epoch,
            "b1_status": BindingStatus.SUPERSEDED.name,
            "b2_status": BindingStatus.ACTIVE.name,
        }:
            violations.append("BINDING_EPOCH_VIOLATION")
        if initial.epoch >= candidate.epoch:
            violations.append("MONOTONICITY_VIOLATION")
        snapshot = after

    elif scenario.mode is S5DeterministicMode.TERMINAL_CONTINUATION_REPLAY:
        core = _base_core()
        core.set_continuation_lifecycle("c", ContinuationLifecycle.TERMINAL)
        outcomes.append("APPLIED")
        before = core.continuations["c"].lifecycle
        core.set_continuation_lifecycle("c", ContinuationLifecycle.TERMINAL)
        outcomes.append("IDEMPOTENT")
        try:
            core.set_continuation_lifecycle("c", ContinuationLifecycle.ACTIVE)
            outcomes.append("RESURRECTED")
        except ContinuityError as exc:
            outcomes.append(f"REJECTED:{type(exc).__name__}")
        after = core.continuations["c"].lifecycle
        if before is not after or after is not ContinuationLifecycle.TERMINAL:
            violations.append("MONOTONICITY_VIOLATION")
        snapshot = {"continuation_lifecycle": after.name}

    elif scenario.mode is S5DeterministicMode.INVALID_STATE_REPLAY:
        core = _base_core()
        core.create_state("x", origin_type="continuation", origin_id="c")
        core.set_state_validity("x", StateValidity.INVALID)
        outcomes.append("APPLIED")
        before = core.states["x"].validity
        core.set_state_validity("x", StateValidity.INVALID)
        outcomes.append("IDEMPOTENT")
        try:
            core.set_state_validity("x", StateValidity.VALID)
            outcomes.append("RESURRECTED")
        except ContinuityError as exc:
            outcomes.append(f"REJECTED:{type(exc).__name__}")
        after = core.states["x"].validity
        if before is not after or after is not StateValidity.INVALID:
            violations.append("MONOTONICITY_VIOLATION")
        snapshot = {"state_validity": after.name}

    elif scenario.mode is S5DeterministicMode.FIRST_VALID_FINALIZATION_CONTROL:
        core = _valid_finalize_core()
        outcomes.append(_capture_finalize(core, S5_OUTPUT_ID, finalization_effects))
        snapshot = _request_snapshot(core)
        completed_request_id = _completed_request_id(snapshot)
        intended = (
            completed_request_id == S5_REQUEST_ID
            and snapshot["authoritative_output_id"] == S5_OUTPUT_ID
            and snapshot["attempt_execution_status"] == ExecutionStatus.SUCCEEDED.name
            and snapshot["attempt_authority_status"] == AttemptAuthority.COMMITTED.name
            and len(finalization_effects) == 1
        )
        if not intended:
            violations.append("FINALIZATION_CONTROL_FAILED")

    else:  # pragma: no cover
        raise AssertionError("unhandled deterministic S5 mode")

    return _ExecutionPresentation(
        application_outcomes=tuple(outcomes),
        semantic_snapshot=snapshot,
        finalization_effects=tuple(finalization_effects),
        invariant_violations=tuple(dict.fromkeys(violations)),
        intended_semantic_state_reached=intended,
        completed_request_id=completed_request_id,
    )


def _run_s5_e0_trial(
    policy_id: PolicyID,
    scenario_id: str,
    *,
    inject_duplicate_finalization: bool = False,
) -> S5DeterministicTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    scenario = _SCENARIO_BY_ID.get(scenario_id)
    if scenario is None:
        raise ValueError(f"scenario_id must be one of {S5_E0_SCENARIO_IDS!r}")
    if (
        inject_duplicate_finalization
        and scenario.mode is not S5DeterministicMode.SAME_OUTPUT_FINALIZE_TWICE
    ):
        raise ValueError("anti-false-zero injection is defined only for D1")

    presentation = _run_mode(
        scenario,
        inject_duplicate_finalization=inject_duplicate_finalization,
    )
    duplicate_finalization = len(presentation.finalization_effects) > 1
    semantically_correct = (
        not presentation.invariant_violations and not duplicate_finalization
    )

    if not presentation.intended_semantic_state_reached:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.FAIL,
        )
    elif semantically_correct:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=True,
        )
    else:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )

    opportunities: tuple[CorrectnessMetric, ...] = ()
    opportunity_event_ids: tuple[str, ...] = ()
    opportunity_scopes: tuple[MetricOpportunityScope, ...] = ()
    metric_violations: tuple[CorrectnessMetric, ...] = ()
    violation_event_ids: tuple[str, ...] = ()

    # DFR is policy-derived: construct its denominator only from an observed
    # completed LogicalRequest in this faulted execution, never from the scenario label.
    dfr_event_id: str | None = None
    if scenario.fault_id is not None and presentation.completed_request_id is not None:
        dfr_event_id = (
            f"S5:EV0:{scenario.scenario_id}:completed-request:"
            f"{presentation.completed_request_id}"
        )
        opportunities = (CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,)
        opportunity_event_ids = (dfr_event_id,)
        opportunity_scopes = (MetricOpportunityScope.POLICY_DERIVED,)
        if duplicate_finalization:
            metric_violations = (CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,)
            violation_event_ids = (dfr_event_id,)

    ground_truth = {
        "schema": S5_E0_SCHEMA,
        "scenario_id": scenario.scenario_id,
        "mode": scenario.mode.value,
        "expected_duplicate_finalization": False,
        "expected_invariant_violations": [],
        "dfr_denominator_scope": "POLICY_DERIVED_COMPLETED_LOGICAL_REQUEST",
        "dfr_opportunity_expected": scenario.creates_dfr_opportunity,
        "semantic_authority": "C1_COMMON_TO_B0_B4",
    }
    observed_evidence = {
        "application_outcomes": list(presentation.application_outcomes),
        "semantic_snapshot": dict(presentation.semantic_snapshot),
        "finalization_effects": list(presentation.finalization_effects),
        "semantic_finalization_count": len(presentation.finalization_effects),
        "duplicate_finalization_observed": duplicate_finalization,
        "invariant_violations": list(presentation.invariant_violations),
        "completed_request_id": presentation.completed_request_id,
        "dfr_opportunity_observed": dfr_event_id is not None,
        "injected_duplicate_finalization": inject_duplicate_finalization,
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "policy_specific_s5_information_used": False,
        "common_commit_guards_preserved": True,
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S5_E0_COHORT_ID,
        trial_id=scenario.scenario_id,
        operation_id=f"s5:{scenario.scenario_id}",
        policy_id=policy_id,
        scenario_id=scenario.scenario_id,
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth=ground_truth,
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=semantic_result,
        metric_opportunities=opportunities,
        metric_opportunity_event_ids=opportunity_event_ids,
        metric_opportunity_scopes=opportunity_scopes,
        metric_violations=metric_violations,
        metric_violation_event_ids=violation_event_ids,
        fault_id=scenario.fault_id,
        fault_class=scenario.fault_class,
    )
    return S5DeterministicTrial(
        policy_id=policy_id,
        scenario=scenario,
        evaluation=evaluation,
        application_outcomes=presentation.application_outcomes,
        semantic_snapshot=presentation.semantic_snapshot,
        finalization_effects=presentation.finalization_effects,
        invariant_violations=presentation.invariant_violations,
        completed_request_id=presentation.completed_request_id,
        injected_duplicate_finalization=inject_duplicate_finalization,
    )


def run_s5_e0_trial(policy_id: PolicyID, scenario_id: str) -> S5DeterministicTrial:
    return _run_s5_e0_trial(policy_id, scenario_id)


def run_s5_e0_paired() -> S5DeterministicEvaluation:
    trials = tuple(
        run_s5_e0_trial(policy_id, scenario.scenario_id)
        for scenario in S5_E0_SCENARIOS
        for policy_id in PolicyID
    )
    return S5DeterministicEvaluation(
        trials=trials,
        summary=summarize_correctness(tuple(item.evaluation for item in trials)),
    )
