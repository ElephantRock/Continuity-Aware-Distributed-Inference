from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from continuity.core import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    ContinuationLifecycle,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionContext,
    ReplicaStatus,
    RequestStatus,
    StateValidity,
)
from simulator import (
    CoreContinuityAuthority,
    PlacementDecision,
    PolicyID,
    PolicyObservation,
    WorkerObservation,
    build_baseline_policies,
    decide_placement,
)

from .correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    CorrectnessSummary,
    MetricOpportunityScope,
    RecoveryAction,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)


S2_E0_COHORT_ID = "C4.3a:S2:E0"
S2_E0_SCENARIOS = (
    "FTR4",
    "FTR5",
    "FTR14",
    "S2-SIMILAR-DIFFERENT",
    "S2-VALID-ANCESTOR",
    "FTR6",
)

_FAULT_CLASS: Mapping[str, str | None] = {
    "FTR4": "wrong sibling state",
    "FTR5": "superseded-producer state",
    "FTR14": "residual abandoned-branch state",
    "S2-SIMILAR-DIFFERENT": "similar-but-different state",
    "S2-VALID-ANCESTOR": None,
    "FTR6": "total state loss",
}

_WBRR_EVENT_ID: Mapping[str, str | None] = {
    "FTR4": "S2:FTR4:wrong-branch-reuse-opportunity",
    "FTR5": None,
    "FTR14": "S2:FTR14:wrong-branch-reuse-opportunity",
    "S2-SIMILAR-DIFFERENT": "S2:SIMILAR:wrong-branch-reuse-opportunity",
    "S2-VALID-ANCESTOR": None,
    "FTR6": None,
}

_EXECUTION_RULE = "CONSUME_DECLARED_CANDIDATE_IF_VALID_REPLICA_IS_LOCAL"


@dataclass(frozen=True, slots=True)
class StateConsumptionDirective:
    directive_id: str
    state_id: str
    execution_rule: str = _EXECUTION_RULE

    def __post_init__(self) -> None:
        if not isinstance(self.directive_id, str) or not self.directive_id:
            raise ValueError("directive_id must be a non-empty string")
        if not isinstance(self.state_id, str) or not self.state_id:
            raise ValueError("state_id must be a non-empty string")
        if self.execution_rule != _EXECUTION_RULE:
            raise ValueError("unsupported S2 E0 execution rule")

    def to_dict(self) -> dict[str, str]:
        return {
            "directive_id": self.directive_id,
            "state_id": self.state_id,
            "execution_rule": self.execution_rule,
        }


@dataclass(frozen=True, slots=True)
class StateConsumptionEvent:
    event_id: str
    directive_id: str
    state_id: str
    replica_id: str
    worker_id: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.event_id, "event_id"),
            (self.directive_id, "directive_id"),
            (self.state_id, "state_id"),
            (self.replica_id, "replica_id"),
            (self.worker_id, "worker_id"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "directive_id": self.directive_id,
            "state_id": self.state_id,
            "replica_id": self.replica_id,
            "worker_id": self.worker_id,
        }


@dataclass(frozen=True, slots=True)
class _ScenarioRuntime:
    core: ContinuityCore
    observation: PolicyObservation
    candidate_state_id: str
    consumer_context: ExecutionContext
    candidate_replica_id: str | None
    compatible_alternative_state_id: str | None
    consumption_directive: StateConsumptionDirective
    fault_id: str | None
    fault_class: str | None
    wbrr_event_id: str | None


@dataclass(frozen=True, slots=True)
class StateLineageTrial:
    policy_id: PolicyID
    scenario_id: str
    evaluation: CorrectnessEvaluationRecord
    placement_decision: PlacementDecision
    candidate_state_id: str
    consumption_event: StateConsumptionEvent | None
    independent_oracle_compatible: bool
    c1_compatible: bool

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.scenario_id not in S2_E0_SCENARIOS:
            raise ValueError("scenario_id must be one of the mandatory S2 E0 scenarios")
        if not isinstance(self.evaluation, CorrectnessEvaluationRecord):
            raise TypeError("evaluation must be CorrectnessEvaluationRecord")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy_id must match trial policy_id")
        if self.evaluation.scenario_id != self.scenario_id:
            raise ValueError("evaluation scenario_id must match trial scenario_id")
        if not isinstance(self.placement_decision, PlacementDecision):
            raise TypeError("placement_decision must be PlacementDecision")
        if self.placement_decision.policy_id is not self.policy_id:
            raise ValueError("placement_decision policy_id must match trial policy_id")
        if not isinstance(self.candidate_state_id, str) or not self.candidate_state_id:
            raise ValueError("candidate_state_id must be a non-empty string")
        if self.consumption_event is not None and not isinstance(
            self.consumption_event, StateConsumptionEvent
        ):
            raise TypeError("consumption_event must be StateConsumptionEvent or None")
        if not isinstance(self.independent_oracle_compatible, bool):
            raise TypeError("independent_oracle_compatible must be bool")
        if not isinstance(self.c1_compatible, bool):
            raise TypeError("c1_compatible must be bool")
        if self.independent_oracle_compatible != self.c1_compatible:
            raise ValueError("C1 compatibility must agree with the independent S2 oracle")

    @property
    def candidate_reused(self) -> bool:
        return self.consumption_event is not None

    @property
    def state_consumption_event_id(self) -> str | None:
        return None if self.consumption_event is None else self.consumption_event.event_id


@dataclass(frozen=True, slots=True)
class StateLineageEvaluation:
    trials: tuple[StateLineageTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected_count = len(S2_E0_SCENARIOS) * len(tuple(PolicyID))
        if len(self.trials) != expected_count:
            raise ValueError(
                f"S2 E0 evaluation must contain exactly {expected_count} paired trials"
            )
        expected_order = tuple(
            (scenario_id, policy_id)
            for scenario_id in S2_E0_SCENARIOS
            for policy_id in PolicyID
        )
        actual_order = tuple(
            (trial.scenario_id, trial.policy_id) for trial in self.trials
        )
        if actual_order != expected_order:
            raise ValueError("S2 E0 trials must use canonical scenario then B0-B4 ordering")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _workers() -> tuple[WorkerObservation, ...]:
    return (
        WorkerObservation("w1", True, 1, 1, 0),
        WorkerObservation("w2", True, 1, 0, 0),
    )


def _base_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c0", "s", lifecycle=ContinuationLifecycle.ACTIVE)
    return core


def _add_consumer(core: ContinuityCore, continuation_id: str) -> ExecutionContext:
    core.create_request("r", continuation_id)
    core.start_attempt("a", "r")
    return ExecutionContext(
        program_id="p",
        session_id="s",
        continuation_id=continuation_id,
        request_id="r",
        attempt_id="a",
    )


def _commit_current_attempt(
    core: ContinuityCore,
    *,
    request_id: str,
    attempt_id: str,
) -> None:
    core.complete_attempt(attempt_id)
    evidence_id = f"e-{attempt_id}"
    core.record_evidence(
        Evidence(
            id=evidence_id,
            claim="terminal attempt success",
            source="C4.3a deterministic oracle fixture",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=0.0,
            scope=frozenset({("attempt", attempt_id)}),
        )
    )
    output_id = f"o-{attempt_id}"
    core.create_output(
        output_id,
        attempt_id,
        terminal=True,
        evidence_ids=(evidence_id,),
    )
    core.finalize_request(request_id, output_id, now=0.0)


def _independent_ancestors(core: ContinuityCore, continuation_id: str) -> set[str]:
    if continuation_id not in core.continuations:
        return set()
    result = {continuation_id}
    todo = list(core.continuations[continuation_id].parent_ids)
    while todo:
        current = todo.pop()
        if current in result:
            continue
        result.add(current)
        parent = core.continuations.get(current)
        if parent is None:
            return set()
        todo.extend(parent.parent_ids)
    return result


def _observation(
    core: ContinuityCore,
    *,
    candidate_state_id: str,
    consumer_context: ExecutionContext,
    state_locations: tuple[str, ...],
) -> PolicyObservation:
    state = core.states[candidate_state_id]
    ancestry = tuple(
        sorted(
            _independent_ancestors(core, consumer_context.continuation_id)
            - {consumer_context.continuation_id}
        )
    )
    return PolicyObservation(
        request_id=consumer_context.request_id,
        workers=_workers(),
        program_id=consumer_context.program_id,
        attempt_id=consumer_context.attempt_id,
        attempt_authority="CURRENT",
        session_id=consumer_context.session_id,
        session_preferred_location="w1",
        continuation_id=consumer_context.continuation_id,
        continuation_ancestry=ancestry,
        state_candidate_key="prefix:shared",
        exact_state_id=candidate_state_id,
        state_locations=state_locations,
        state_provenance=(("origin_continuation", state.origin_continuation_id),),
        state_lifecycle=state.lifecycle.name,
        producer_attempt_id=state.producer_attempt_id,
        binding_id=None,
        binding_epoch=None,
        evidence_authority="EXACT_OBSERVATION",
        evidence_status="VALID",
        evidence_freshness=0.0,
        reconciliation="MATCHED",
    )


def _build_runtime(scenario_id: str) -> _ScenarioRuntime:
    if scenario_id not in S2_E0_SCENARIOS:
        raise ValueError(f"scenario_id must be one of {S2_E0_SCENARIOS!r}")

    core = _base_core()
    candidate_state_id: str
    candidate_replica_id: str | None = None
    compatible_alternative_state_id: str | None = None

    if scenario_id == "FTR4":
        core.create_continuation(
            "c1", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        core.create_continuation(
            "c2", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        candidate_state_id = "x-sibling"
        core.create_state(
            candidate_state_id,
            origin_type="continuation",
            origin_id="c1",
            semantic_type="PREFIX",
            representation="KV",
        )
        candidate_replica_id = "rho-sibling"
        core.add_replica(candidate_replica_id, candidate_state_id, "w1")
        consumer_context = _add_consumer(core, "c2")
        state_locations = ("w1",)

    elif scenario_id == "FTR5":
        core.create_continuation(
            "c1", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        core.create_request("rp", "c1")
        core.start_attempt("a1", "rp")
        candidate_state_id = "x-superseded-producer"
        core.create_state(
            candidate_state_id,
            origin_type="attempt",
            origin_id="a1",
            semantic_type="PREFIX",
            representation="KV",
        )
        candidate_replica_id = "rho-superseded-producer"
        core.add_replica(candidate_replica_id, candidate_state_id, "w1")
        core.start_attempt("a2", "rp")
        _commit_current_attempt(core, request_id="rp", attempt_id="a2")
        core.create_continuation(
            "c2", "s", parent_ids=("c1",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        consumer_context = _add_consumer(core, "c2")
        state_locations = ("w1",)

    elif scenario_id == "FTR14":
        core.create_continuation(
            "c1", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        candidate_state_id = "x-abandoned"
        core.create_state(
            candidate_state_id,
            origin_type="continuation",
            origin_id="c1",
            semantic_type="PREFIX",
            representation="KV",
        )
        candidate_replica_id = "rho-abandoned"
        core.add_replica(candidate_replica_id, candidate_state_id, "w1")
        core.set_continuation_lifecycle("c1", ContinuationLifecycle.ABANDONED)
        core.create_continuation(
            "c2", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        consumer_context = _add_consumer(core, "c2")
        state_locations = ("w1",)

    elif scenario_id == "S2-SIMILAR-DIFFERENT":
        core.create_continuation(
            "c1", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        core.create_continuation(
            "c2", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        compatible_alternative_state_id = "x-compatible"
        core.create_state(
            compatible_alternative_state_id,
            origin_type="continuation",
            origin_id="c0",
            semantic_type="PREFIX",
            representation="KV",
        )
        core.add_replica("rho-compatible", compatible_alternative_state_id, "w2")
        candidate_state_id = "x-similar-wrong"
        core.create_state(
            candidate_state_id,
            origin_type="continuation",
            origin_id="c1",
            semantic_type="PREFIX",
            representation="KV",
        )
        candidate_replica_id = "rho-similar-wrong"
        core.add_replica(candidate_replica_id, candidate_state_id, "w1")
        consumer_context = _add_consumer(core, "c2")
        state_locations = ("w1",)

    elif scenario_id == "S2-VALID-ANCESTOR":
        candidate_state_id = "x-ancestor"
        core.create_state(
            candidate_state_id,
            origin_type="continuation",
            origin_id="c0",
            semantic_type="PREFIX",
            representation="KV",
        )
        candidate_replica_id = "rho-ancestor"
        core.add_replica(candidate_replica_id, candidate_state_id, "w1")
        core.create_continuation(
            "c2", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        consumer_context = _add_consumer(core, "c2")
        state_locations = ("w1",)

    elif scenario_id == "FTR6":
        candidate_state_id = "x-lost"
        core.create_state(
            candidate_state_id,
            origin_type="continuation",
            origin_id="c0",
            semantic_type="PREFIX",
            representation="KV",
        )
        candidate_replica_id = "rho-lost"
        core.add_replica(candidate_replica_id, candidate_state_id, "w1")
        core.set_replica_status(candidate_replica_id, ReplicaStatus.LOST)
        core.create_continuation(
            "c1", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
        )
        consumer_context = _add_consumer(core, "c1")
        state_locations = ()

    else:  # pragma: no cover
        raise AssertionError("unhandled S2 scenario")

    observation = _observation(
        core,
        candidate_state_id=candidate_state_id,
        consumer_context=consumer_context,
        state_locations=state_locations,
    )
    fault_class = _FAULT_CLASS[scenario_id]
    return _ScenarioRuntime(
        core=core,
        observation=observation,
        candidate_state_id=candidate_state_id,
        consumer_context=consumer_context,
        candidate_replica_id=candidate_replica_id,
        compatible_alternative_state_id=compatible_alternative_state_id,
        consumption_directive=StateConsumptionDirective(
            directive_id=f"S2:{scenario_id}:local-candidate-consumption",
            state_id=candidate_state_id,
        ),
        fault_id=None if fault_class is None else f"S2:{scenario_id}",
        fault_class=fault_class,
        wbrr_event_id=_WBRR_EVENT_ID[scenario_id],
    )


def _independent_context_consistent(
    core: ContinuityCore,
    ctx: ExecutionContext,
) -> bool:
    program = core.programs.get(ctx.program_id)
    session = core.sessions.get(ctx.session_id)
    continuation = core.continuations.get(ctx.continuation_id)
    request = core.requests.get(ctx.request_id)
    attempt = core.attempts.get(ctx.attempt_id)
    if any(value is None for value in (program, session, continuation, request, attempt)):
        return False
    assert program is not None
    assert session is not None
    assert continuation is not None
    assert request is not None
    assert attempt is not None
    if session.program_id != program.id or continuation.session_id != session.id:
        return False
    if request.continuation_id != continuation.id or attempt.request_id != request.id:
        return False
    if request.current_attempt_id != attempt.id:
        return False
    if attempt.authority_status is not AttemptAuthority.CURRENT:
        return False
    return ctx.phase_id is None


def _independent_lineage_compatible(
    core: ContinuityCore,
    state_id: str,
    ctx: ExecutionContext,
) -> bool:
    if not _independent_context_consistent(core, ctx):
        return False

    state = core.states.get(state_id)
    consumer = core.continuations.get(ctx.continuation_id)
    session = core.sessions.get(ctx.session_id)
    if state is None or consumer is None or session is None:
        return False
    if state.validity is not StateValidity.VALID:
        return False
    origin = core.continuations.get(state.origin_continuation_id)
    if origin is None:
        return False
    if origin.session_id != consumer.session_id or consumer.session_id != session.id:
        return False
    if state.origin_continuation_id not in _independent_ancestors(
        core, ctx.continuation_id
    ):
        return False

    if state.producer_attempt_id is not None:
        producer_attempt = core.attempts.get(state.producer_attempt_id)
        if producer_attempt is None:
            return False
        if producer_attempt.authority_status is AttemptAuthority.SUPERSEDED:
            return False
        if state.origin_request_id is not None:
            producer_request = core.requests.get(state.origin_request_id)
            if producer_request is None:
                return False
            if producer_request.status is RequestStatus.COMPLETED:
                if producer_request.committed_attempt_id != producer_attempt.id:
                    return False
            elif (
                producer_attempt.id != ctx.attempt_id
                or producer_attempt.authority_status is not AttemptAuthority.CURRENT
            ):
                return False
        elif (
            producer_attempt.id != ctx.attempt_id
            or producer_attempt.authority_status is not AttemptAuthority.CURRENT
        ):
            return False

    if state.producer_phase_id is not None:
        raise AssertionError("C4.3a independent oracle excludes Phase-origin State")
    if state.derived_from:
        raise AssertionError("C4.3a independent oracle excludes derived State")
    return True


def _execute_consumption_directive(
    runtime: _ScenarioRuntime,
    decision: PlacementDecision,
) -> StateConsumptionEvent | None:
    """Apply one post-placement worker execution rule to every baseline.

    This function intentionally has no PolicyID parameter and never inspects the
    placement reason. The declared candidate is consumed only when its actual
    physical replica is VALID on the worker selected by the closed C3 policy.
    """
    if decision.worker_id is None or runtime.candidate_replica_id is None:
        return None
    replica = runtime.core.replicas[runtime.candidate_replica_id]
    if replica.state_id != runtime.consumption_directive.state_id:
        raise AssertionError("S2 consumption directive/replica State mismatch")
    if replica.status is not ReplicaStatus.VALID:
        return None
    if replica.location_id != decision.worker_id:
        return None
    return StateConsumptionEvent(
        event_id=(
            f"S2:{runtime.observation.request_id}:consume:"
            f"{runtime.consumption_directive.state_id}@{decision.worker_id}"
        ),
        directive_id=runtime.consumption_directive.directive_id,
        state_id=runtime.consumption_directive.state_id,
        replica_id=replica.id,
        worker_id=decision.worker_id,
    )


def _placement_to_dict(decision: PlacementDecision) -> dict[str, Any]:
    return {
        "policy_id": decision.policy_id.value,
        "worker_id": decision.worker_id,
        "ranked_worker_ids": list(decision.ranked_worker_ids),
        "reason": decision.reason,
    }


def _runtime_ground_truth(runtime: _ScenarioRuntime, scenario_id: str) -> dict[str, Any]:
    core = runtime.core
    state = core.states[runtime.candidate_state_id]
    replica = (
        None
        if runtime.candidate_replica_id is None
        else core.replicas[runtime.candidate_replica_id]
    )
    producer_attempt = (
        None if state.producer_attempt_id is None else core.attempts[state.producer_attempt_id]
    )
    producer_request = (
        None if state.origin_request_id is None else core.requests[state.origin_request_id]
    )
    independent_compatible = _independent_lineage_compatible(
        core, runtime.candidate_state_id, runtime.consumer_context
    )
    return {
        "scenario_id": scenario_id,
        "candidate_key": runtime.observation.state_candidate_key,
        "candidate_state_id": runtime.candidate_state_id,
        "candidate_origin_continuation_id": state.origin_continuation_id,
        "candidate_origin_request_id": state.origin_request_id,
        "candidate_producer_attempt_id": state.producer_attempt_id,
        "candidate_producer_attempt_authority": (
            None if producer_attempt is None else producer_attempt.authority_status.name
        ),
        "candidate_origin_request_committed_attempt_id": (
            None if producer_request is None else producer_request.committed_attempt_id
        ),
        "candidate_semantic_type": state.semantic_type,
        "candidate_representation": state.representation,
        "candidate_state_validity": state.validity.name,
        "candidate_replica_id": runtime.candidate_replica_id,
        "candidate_replica_status": None if replica is None else replica.status.name,
        "candidate_physical_location": None if replica is None else replica.location_id,
        "candidate_policy_visible_locations": list(runtime.observation.state_locations),
        "compatible_alternative_state_id": runtime.compatible_alternative_state_id,
        "consumer_context": {
            "program_id": runtime.consumer_context.program_id,
            "session_id": runtime.consumer_context.session_id,
            "continuation_id": runtime.consumer_context.continuation_id,
            "request_id": runtime.consumer_context.request_id,
            "attempt_id": runtime.consumer_context.attempt_id,
        },
        "consumption_directive": runtime.consumption_directive.to_dict(),
        "independent_oracle_compatible": independent_compatible,
        "wrong_branch_reuse_opportunity_event_id": runtime.wbrr_event_id,
    }


def _run_s2_e0_trial(
    policy_id: PolicyID,
    scenario_id: str,
    *,
    injected_consumption_event: StateConsumptionEvent | None = None,
) -> StateLineageTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    if injected_consumption_event is not None and not isinstance(
        injected_consumption_event, StateConsumptionEvent
    ):
        raise TypeError("injected_consumption_event must be StateConsumptionEvent or None")

    runtime = _build_runtime(scenario_id)
    core = runtime.core
    authority = CoreContinuityAuthority(core)
    policy = build_baseline_policies(authority)[policy_id]
    decision = decide_placement(policy, runtime.observation)

    independent_compatible = _independent_lineage_compatible(
        core, runtime.candidate_state_id, runtime.consumer_context
    )
    c1_compatible = authority.state_compatible(
        runtime.candidate_state_id,
        program_id=runtime.consumer_context.program_id,
        session_id=runtime.consumer_context.session_id,
        continuation_id=runtime.consumer_context.continuation_id,
        request_id=runtime.consumer_context.request_id,
        attempt_id=runtime.consumer_context.attempt_id,
    )
    if c1_compatible != independent_compatible:
        raise AssertionError(
            "C1 State compatibility diverges from the independent S2 lineage oracle"
        )

    consumption_event = (
        injected_consumption_event
        if injected_consumption_event is not None
        else _execute_consumption_directive(runtime, decision)
    )
    if consumption_event is not None:
        if consumption_event.state_id != runtime.candidate_state_id:
            raise AssertionError("consumption event references a different candidate State")
        if consumption_event.directive_id != runtime.consumption_directive.directive_id:
            raise AssertionError("consumption event references a different directive")

    candidate_reused = consumption_event is not None
    incompatible_consumption = candidate_reused and not independent_compatible
    wrong_branch_reuse = runtime.wbrr_event_id is not None and incompatible_consumption

    opportunities: list[CorrectnessMetric] = []
    opportunity_event_ids: list[str] = []
    opportunity_scopes: list[MetricOpportunityScope] = []
    violations: list[CorrectnessMetric] = []
    violation_event_ids: list[str] = []

    if runtime.wbrr_event_id is not None:
        opportunities.append(CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
        opportunity_event_ids.append(runtime.wbrr_event_id)
        opportunity_scopes.append(MetricOpportunityScope.EXOGENOUS_PAIRED)
        if wrong_branch_reuse:
            violations.append(CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
            violation_event_ids.append(runtime.wbrr_event_id)

    if consumption_event is not None and runtime.fault_id is not None:
        opportunities.append(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
        opportunity_event_ids.append(consumption_event.event_id)
        opportunity_scopes.append(MetricOpportunityScope.POLICY_DERIVED)
        if incompatible_consumption:
            violations.append(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
            violation_event_ids.append(consumption_event.event_id)

    if incompatible_consumption:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )
    elif consumption_event is not None:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=True,
        )
    else:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=True,
            recovery_actions=(RecoveryAction.RECOMPUTE,),
        )

    observed_evidence = {
        "c1_state_compatible": c1_compatible,
        "selected_worker_id": decision.worker_id,
        "candidate_replica_id": runtime.candidate_replica_id,
        "candidate_replica_status": (
            None
            if runtime.candidate_replica_id is None
            else core.replicas[runtime.candidate_replica_id].status.name
        ),
        "consumption_event": None if consumption_event is None else consumption_event.to_dict(),
    }
    policy_decision = {
        "placement": _placement_to_dict(decision),
        "c1_lineage_guard_policy_visible": policy_id is PolicyID.B4,
        "state_consumption_is_execution_event_not_policy_decision": True,
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S2_E0_COHORT_ID,
        trial_id=scenario_id,
        operation_id="r",
        policy_id=policy_id,
        scenario_id=scenario_id,
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth=_runtime_ground_truth(runtime, scenario_id),
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=semantic_result,
        metric_opportunities=tuple(opportunities),
        metric_opportunity_event_ids=tuple(opportunity_event_ids),
        metric_opportunity_scopes=tuple(opportunity_scopes),
        metric_violations=tuple(violations),
        metric_violation_event_ids=tuple(violation_event_ids),
        fault_id=runtime.fault_id,
        fault_class=runtime.fault_class,
    )

    return StateLineageTrial(
        policy_id=policy_id,
        scenario_id=scenario_id,
        evaluation=evaluation,
        placement_decision=decision,
        candidate_state_id=runtime.candidate_state_id,
        consumption_event=consumption_event,
        independent_oracle_compatible=independent_compatible,
        c1_compatible=c1_compatible,
    )


def run_s2_e0_trial(policy_id: PolicyID, scenario_id: str) -> StateLineageTrial:
    return _run_s2_e0_trial(policy_id, scenario_id)


def run_s2_e0_paired() -> StateLineageEvaluation:
    trials = tuple(
        run_s2_e0_trial(policy_id, scenario_id)
        for scenario_id in S2_E0_SCENARIOS
        for policy_id in PolicyID
    )
    summary = summarize_correctness(tuple(trial.evaluation for trial in trials))
    return StateLineageEvaluation(trials=trials, summary=summary)
