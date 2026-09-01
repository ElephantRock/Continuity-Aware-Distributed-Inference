from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from continuity import ContinuityCore
from continuity.entities import AttemptAuthority, ExecutionStatus, RequestStatus
from simulator import (
    AdapterOutcome,
    AuthoritativeOutcome,
    ContinuityAdapter,
    CoreContinuityAuthority,
    DiscreteEventSimulator,
    EventKind,
    PlacementDecision,
    PolicyID,
    PolicyObservation,
    WorkerObservation,
    authoritative_outcome,
    build_baseline_policies,
    decide_placement,
    scenario_definition,
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


S1_E0_SCENARIOS = ("FTR1", "FTR2", "FTR3")
S1_E0_COHORT_ID = "C4.2a:S1:E0"

_EXPECTED_COMMITTED_ATTEMPT: Mapping[str, str] = {
    "FTR1": "a2",
    "FTR2": "a1",
    "FTR3": "a2",
}

_FAULT_CLASS: Mapping[str, str] = {
    "FTR1": "late completion",
    "FTR2": "duplicate result",
    "FTR3": "event reorder",
}

_RETRY_SCENARIOS = frozenset({"FTR1", "FTR3"})


@dataclass(frozen=True, slots=True)
class _AuthorityPresentation:
    event_id: str
    request_id: str
    attempt_id: str
    evidence_id: str
    output_id: str
    at: float
    source: str


@dataclass(frozen=True, slots=True)
class AttemptFencingTrial:
    policy_id: PolicyID
    scenario_id: str
    evaluation: CorrectnessEvaluationRecord
    authoritative_outcome: AuthoritativeOutcome
    stale_result_event_ids: tuple[str, ...]
    stale_attempt_ids: tuple[str, ...]
    finalization_applied_count: int
    stale_admission_decisions: tuple[PlacementDecision, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.scenario_id not in S1_E0_SCENARIOS:
            raise ValueError("scenario_id must be one of the mandatory S1 E0 scenarios")
        if not isinstance(self.evaluation, CorrectnessEvaluationRecord):
            raise TypeError("evaluation must be CorrectnessEvaluationRecord")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy_id must match trial policy_id")
        if self.evaluation.scenario_id != self.scenario_id:
            raise ValueError("evaluation scenario_id must match trial scenario_id")
        if not isinstance(self.authoritative_outcome, AuthoritativeOutcome):
            raise TypeError("authoritative_outcome must be AuthoritativeOutcome")
        if not isinstance(self.finalization_applied_count, int) or isinstance(
            self.finalization_applied_count, bool
        ) or self.finalization_applied_count < 0:
            raise ValueError("finalization_applied_count must be a non-negative integer")
        if not all(
            isinstance(item, PlacementDecision) for item in self.stale_admission_decisions
        ):
            raise TypeError(
                "stale_admission_decisions must contain PlacementDecision values"
            )


@dataclass(frozen=True, slots=True)
class AttemptFencingEvaluation:
    trials: tuple[AttemptFencingTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected_count = len(S1_E0_SCENARIOS) * len(tuple(PolicyID))
        if len(self.trials) != expected_count:
            raise ValueError(
                f"S1 E0 evaluation must contain exactly {expected_count} paired trials"
            )
        expected_order = tuple(
            (scenario_id, policy_id)
            for scenario_id in S1_E0_SCENARIOS
            for policy_id in PolicyID
        )
        actual_order = tuple(
            (trial.scenario_id, trial.policy_id) for trial in self.trials
        )
        if actual_order != expected_order:
            raise ValueError("S1 E0 trials must use canonical scenario then B0-B4 ordering")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _scaffold_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    return core


def _payload(event: Any) -> dict[str, Any]:
    return dict(event.payload)


def _placement_to_dict(decision: PlacementDecision) -> dict[str, Any]:
    return {
        "policy_id": decision.policy_id.value,
        "worker_id": decision.worker_id,
        "ranked_worker_ids": list(decision.ranked_worker_ids),
        "reason": decision.reason,
    }


def _attempt_admission_observation(
    *,
    request_id: str,
    attempt_id: str,
    attempt_authority: str,
) -> PolicyObservation:
    return PolicyObservation(
        request_id=request_id,
        workers=(
            WorkerObservation(
                worker_id="w1",
                available=True,
                capacity=1,
                active_tasks=0,
                queued_tasks=0,
            ),
        ),
        attempt_id=attempt_id,
        attempt_authority=attempt_authority,
        session_id="s",
        continuation_id="c",
        program_id="p",
    )


def _physical_late_inputs(schedule: Any) -> tuple[tuple[str, str], ...]:
    inputs: list[tuple[str, str]] = []
    for event in schedule.events:
        if event.kind is not EventKind.LATE_RESULT:
            continue
        data = dict(event.payload)
        attempt_id = data.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise AssertionError("canonical S1 LATE_RESULT event requires AttemptID")
        inputs.append((event.event_id, attempt_id))
    return tuple(inputs)


def _authority_presentations(
    schedule: Any,
    scenario_id: str,
) -> tuple[_AuthorityPresentation, ...]:
    late_attempt_ids = {attempt_id for _, attempt_id in _physical_late_inputs(schedule)}
    presentations: list[_AuthorityPresentation] = []

    for event in schedule.events:
        if event.kind not in {
            EventKind.OBSERVATION_CREATED,
            EventKind.OBSERVATION_DUPLICATED,
        }:
            continue
        data = dict(event.payload)
        attempt_id = data.get("attempt_id")
        if attempt_id not in late_attempt_ids:
            continue
        request_id = data.get("request_id")
        evidence_id = data.get("evidence_id")
        output_id = data.get("output_id")
        if not all(
            isinstance(value, str) and value
            for value in (request_id, attempt_id, evidence_id, output_id)
        ):
            raise AssertionError(
                "canonical S1 stale authority presentation requires request/attempt/evidence/output IDs"
            )
        presentations.append(
            _AuthorityPresentation(
                event_id=event.event_id,
                request_id=request_id,
                attempt_id=attempt_id,
                evidence_id=evidence_id,
                output_id=output_id,
                at=event.time,
                source="CANONICAL_SCENARIO",
            )
        )

    if scenario_id == "FTR1":
        if len(late_attempt_ids) != 1:
            raise AssertionError("FTR1 must contain exactly one physical late Attempt result")
        last_time = max(event.time for event in schedule.events)
        presentations.append(
            _AuthorityPresentation(
                event_id="c4.2a:FTR1:stale-authority-presentation:a1",
                request_id="r",
                attempt_id=next(iter(late_attempt_ids)),
                evidence_id="c4.2a:FTR1:e1-stale",
                output_id="c4.2a:FTR1:o1-stale",
                at=last_time + 1.0,
                source="C4_SUPPLEMENTAL_PRESENTATION",
            )
        )

    return tuple(presentations)


def _classify_stale_authority_acceptance(
    *,
    attempt_id: str,
    attempt_authority_before: str,
    attempt_execution_before: str,
    committed_attempt_id_after: str | None,
    attempt_authority_after: str,
) -> bool:
    if attempt_authority_before != AttemptAuthority.SUPERSEDED.name:
        raise AssertionError(
            "SAAR opportunity must present a semantically SUPERSEDED Attempt"
        )
    if attempt_execution_before != ExecutionStatus.SUCCEEDED.name:
        raise AssertionError(
            "SAAR authority presentation requires delivered physical success"
        )
    accepted = committed_attempt_id_after == attempt_id
    if accepted and attempt_authority_after != AttemptAuthority.COMMITTED.name:
        raise AssertionError(
            "authoritatively accepted stale Attempt must become COMMITTED"
        )
    return accepted


def _run_semantic_trace(
    policy_id: PolicyID,
    scenario_id: str,
    presentations: tuple[_AuthorityPresentation, ...],
) -> tuple[
    ContinuityCore,
    ContinuityAdapter,
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[PlacementDecision, ...],
]:
    definition = scenario_definition(scenario_id)
    schedule = definition.build(seed=0)
    sim = DiscreteEventSimulator(seed=0)
    core = _scaffold_core()

    supersession_checks: list[dict[str, Any]] = []
    late_completion_checks: list[dict[str, Any]] = []
    authority_preconditions: dict[str, dict[str, Any]] = {}
    authority_presentation_checks: list[dict[str, Any]] = []
    admission_decisions: list[PlacementDecision] = []
    supersession_times: dict[str, float] = {}
    presentation_by_event = {item.event_id: item for item in presentations}

    def on_authority_precondition(
        _sim: DiscreteEventSimulator,
        event: Any,
    ) -> None:
        presentation = presentation_by_event.get(event.event_id)
        if presentation is None:
            return
        attempt = core.attempts[presentation.attempt_id]
        request = core.requests[presentation.request_id]
        if attempt.authority_status is not AttemptAuthority.SUPERSEDED:
            raise AssertionError(
                "SAAR opportunity must be stale before authoritative finalization"
            )
        if attempt.execution_status is not ExecutionStatus.SUCCEEDED:
            raise AssertionError(
                "SAAR opportunity requires delivered physical success before finalization"
            )
        authority_preconditions[event.event_id] = {
            "event_id": event.event_id,
            "time": sim.now,
            "request_id": presentation.request_id,
            "attempt_id": presentation.attempt_id,
            "source": presentation.source,
            "attempt_authority_before": attempt.authority_status.name,
            "attempt_execution_before": attempt.execution_status.name,
            "request_current_attempt_id_before": request.current_attempt_id,
            "request_committed_attempt_id_before": request.committed_attempt_id,
        }

    # This handler must run before ContinuityAdapter's observation handler so a
    # failing implementation that accepts stale authority can still be measured
    # instead of changing SUPERSEDED -> COMMITTED before the stale precondition
    # is captured.
    sim.register_handler(EventKind.OBSERVATION_CREATED, on_authority_precondition)
    sim.register_handler(EventKind.OBSERVATION_DUPLICATED, on_authority_precondition)

    adapter = ContinuityAdapter(sim, core)
    policies = build_baseline_policies(CoreContinuityAuthority(core))
    policy = policies[policy_id]

    def on_retry_started(_sim: DiscreteEventSimulator, event: Any) -> None:
        data = _payload(event)
        request_id = data["request_id"]
        superseded_attempt_id = data["superseded_attempt_id"]
        retry_attempt_id = data["retry_attempt_id"]
        request = core.requests[request_id]
        superseded = core.attempts[superseded_attempt_id]
        retry = core.attempts[retry_attempt_id]
        check = {
            "event_id": event.event_id,
            "time": sim.now,
            "request_id": request_id,
            "superseded_attempt_id": superseded_attempt_id,
            "retry_attempt_id": retry_attempt_id,
            "superseded_execution_status": superseded.execution_status.name,
            "superseded_authority_status": superseded.authority_status.name,
            "retry_authority_status": retry.authority_status.name,
            "request_current_attempt_id": request.current_attempt_id,
        }
        if superseded.execution_status is not ExecutionStatus.RUNNING:
            raise AssertionError(
                "S1 late-superseded-Attempt setup requires A1 physically active/unfinished at supersession"
            )
        if superseded.authority_status is not AttemptAuthority.SUPERSEDED:
            raise AssertionError("S1 superseded Attempt must be semantically SUPERSEDED")
        if retry.authority_status is not AttemptAuthority.CURRENT:
            raise AssertionError("S1 retry Attempt must become CURRENT at supersession")
        if request.current_attempt_id != retry_attempt_id:
            raise AssertionError("S1 retry Attempt must become the request ActiveAttempt")
        supersession_times[superseded_attempt_id] = sim.now
        supersession_checks.append(check)

    def on_late_result(_sim: DiscreteEventSimulator, event: Any) -> None:
        data = _payload(event)
        attempt_id = data["attempt_id"]
        attempt = core.attempts[attempt_id]
        request = core.requests[attempt.request_id]
        superseded_at = supersession_times.get(attempt_id)
        if superseded_at is None:
            raise AssertionError("late Attempt result delivered without recorded supersession")
        if sim.now <= superseded_at:
            raise AssertionError("late Attempt result must be delivered after supersession")
        if attempt.authority_status is not AttemptAuthority.SUPERSEDED:
            raise AssertionError("ground truth must label late Attempt stale at delivery")
        if attempt.execution_status is not ExecutionStatus.SUCCEEDED:
            raise AssertionError("late physical success must be delivered as SUCCEEDED execution")

        late_completion_checks.append(
            {
                "event_id": event.event_id,
                "time": sim.now,
                "request_id": attempt.request_id,
                "attempt_id": attempt_id,
                "attempt_authority": attempt.authority_status.name,
                "attempt_execution_status": attempt.execution_status.name,
                "request_current_attempt_id": request.current_attempt_id,
                "request_committed_attempt_id": request.committed_attempt_id,
                "superseded_at": superseded_at,
                "stale_at_delivery": True,
            }
        )
        admission_decisions.append(
            decide_placement(
                policy,
                _attempt_admission_observation(
                    request_id=attempt.request_id,
                    attempt_id=attempt_id,
                    attempt_authority=attempt.authority_status.name,
                ),
            )
        )

    def on_authority_presentation(
        _sim: DiscreteEventSimulator,
        event: Any,
    ) -> None:
        presentation = presentation_by_event.get(event.event_id)
        if presentation is None:
            return
        precondition = authority_preconditions.get(event.event_id)
        if precondition is None:
            raise AssertionError(
                "stale authority precondition was not captured before finalization"
            )
        attempt = core.attempts[presentation.attempt_id]
        request = core.requests[presentation.request_id]
        accepted = _classify_stale_authority_acceptance(
            attempt_id=presentation.attempt_id,
            attempt_authority_before=precondition["attempt_authority_before"],
            attempt_execution_before=precondition["attempt_execution_before"],
            committed_attempt_id_after=request.committed_attempt_id,
            attempt_authority_after=attempt.authority_status.name,
        )
        authority_presentation_checks.append(
            {
                "event_id": event.event_id,
                "time": sim.now,
                "request_id": presentation.request_id,
                "attempt_id": presentation.attempt_id,
                "source": presentation.source,
                "attempt_authority_after": attempt.authority_status.name,
                "attempt_execution_after": attempt.execution_status.name,
                "request_current_attempt_id_after": request.current_attempt_id,
                "request_committed_attempt_id_after": request.committed_attempt_id,
                "accepted_authoritatively": accepted,
            }
        )

    # These measurement handlers intentionally run after ContinuityAdapter so
    # they observe the semantic effects of retry, late completion, and
    # authoritative finalization.
    sim.register_handler(EventKind.RETRY_STARTED, on_retry_started)
    sim.register_handler(EventKind.LATE_RESULT, on_late_result)
    sim.register_handler(EventKind.OBSERVATION_CREATED, on_authority_presentation)
    sim.register_handler(EventKind.OBSERVATION_DUPLICATED, on_authority_presentation)

    schedule.apply(sim)

    for presentation in presentations:
        if presentation.source != "C4_SUPPLEMENTAL_PRESENTATION":
            continue
        adapter.schedule_observation(
            presentation.request_id,
            presentation.attempt_id,
            presentation.evidence_id,
            presentation.output_id,
            at=presentation.at,
            observed_at=presentation.at,
            event_id=presentation.event_id,
        )

    sim.run()

    if scenario_id in _RETRY_SCENARIOS:
        if not supersession_checks:
            raise AssertionError(
                "mandatory S1 retry scenario did not produce supersession evidence"
            )
        if not late_completion_checks:
            raise AssertionError(
                "mandatory S1 retry scenario did not deliver a stale physical Attempt result"
            )
        expected_ids = tuple(item.event_id for item in presentations)
        pre_ids = tuple(
            event_id for event_id in expected_ids if event_id in authority_preconditions
        )
        observed_ids = tuple(item["event_id"] for item in authority_presentation_checks)
        if pre_ids != expected_ids:
            raise AssertionError(
                "every stale authority presentation must capture its stale precondition"
            )
        if observed_ids != expected_ids:
            raise AssertionError(
                "runtime stale authority presentations must exactly match the exogenous S1 manifest"
            )

    ordered_preconditions = tuple(
        authority_preconditions[item.event_id] for item in presentations
    )
    return (
        core,
        adapter,
        tuple(supersession_checks),
        tuple(late_completion_checks),
        ordered_preconditions,
        tuple(authority_presentation_checks),
        tuple(admission_decisions),
    )


def run_s1_e0_trial(policy_id: PolicyID, scenario_id: str) -> AttemptFencingTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    if scenario_id not in S1_E0_SCENARIOS:
        raise ValueError(f"scenario_id must be one of {S1_E0_SCENARIOS!r}")

    definition = scenario_definition(scenario_id)
    schedule = definition.build(seed=0)
    expected_attempt_id = _EXPECTED_COMMITTED_ATTEMPT[scenario_id]
    physical_late_inputs = _physical_late_inputs(schedule)
    presentations = _authority_presentations(schedule, scenario_id)
    stale_event_ids = tuple(item.event_id for item in presentations)
    stale_attempt_ids = tuple(sorted({item.attempt_id for item in presentations}))

    (
        core,
        adapter,
        supersession_checks,
        late_completion_checks,
        authority_preconditions,
        authority_presentation_checks,
        admission_decisions,
    ) = _run_semantic_trace(policy_id, scenario_id, presentations)

    outcome = authoritative_outcome(core, "r")
    if core.requests["r"].status is not RequestStatus.COMPLETED:
        raise AssertionError(f"{scenario_id} must end in a completed LogicalRequest")
    if outcome.authoritative_output_id is None:
        raise AssertionError(f"{scenario_id} must produce one authoritative output")

    finalization_records = tuple(
        record for record in adapter.records if record.operation == "finalize_request"
    )
    finalization_by_event = {
        record.event_id: record
        for record in finalization_records
        if record.event_id in set(stale_event_ids)
    }
    if set(finalization_by_event) != set(stale_event_ids):
        raise AssertionError(
            "every SAAR opportunity must reach one terminal finalization attempt"
        )

    accepted_event_ids = tuple(
        item["event_id"]
        for item in authority_presentation_checks
        if item["accepted_authoritatively"]
    )
    finalization_applied_count = sum(
        record.outcome is AdapterOutcome.APPLIED for record in finalization_records
    )
    duplicate_finalization = finalization_applied_count > 1
    semantic_correct = outcome.committed_attempt_id == expected_attempt_id
    recovery_actions = (
        (RecoveryAction.RETRY,) if scenario_id in _RETRY_SCENARIOS else ()
    )

    opportunities: list[CorrectnessMetric] = []
    opportunity_event_ids: list[str] = []
    opportunity_scopes: list[MetricOpportunityScope] = []
    violations: list[CorrectnessMetric] = []
    violation_event_ids: list[str] = []

    for event_id in stale_event_ids:
        opportunities.append(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
        opportunity_event_ids.append(event_id)
        opportunity_scopes.append(MetricOpportunityScope.EXOGENOUS_PAIRED)
        if event_id in accepted_event_ids:
            violations.append(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
            violation_event_ids.append(event_id)

    completed_request_event_id = "completed-request:r"
    opportunities.append(CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
    opportunity_event_ids.append(completed_request_event_id)
    opportunity_scopes.append(MetricOpportunityScope.POLICY_DERIVED)
    if duplicate_finalization:
        violations.append(CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
        violation_event_ids.append(completed_request_event_id)

    ground_truth = {
        "scenario_catalogue_id": scenario_id,
        "scenario_stable_name": definition.stable_name,
        "scenario_fingerprint": schedule.fingerprint,
        "request_id": "r",
        "expected_committed_attempt_id": expected_attempt_id,
        "physical_late_result_events": [
            {"event_id": event_id, "attempt_id": attempt_id}
            for event_id, attempt_id in physical_late_inputs
        ],
        "stale_authority_presentations": [
            {
                "event_id": item.event_id,
                "request_id": item.request_id,
                "attempt_id": item.attempt_id,
                "evidence_id": item.evidence_id,
                "output_id": item.output_id,
                "at": item.at,
                "source": item.source,
            }
            for item in presentations
        ],
    }
    observed_evidence = {
        "supersession_checks": list(supersession_checks),
        "late_completion_checks": list(late_completion_checks),
        "stale_authority_preconditions": list(authority_preconditions),
        "stale_authority_presentations": list(authority_presentation_checks),
        "finalization_records": [
            {
                "event_id": record.event_id,
                "event_kind": record.event_kind.value,
                "outcome": record.outcome.value,
                "error_type": record.error_type,
            }
            for record in finalization_records
        ],
        "authoritative_outcome": {
            "request_status": outcome.request_status,
            "current_attempt_id": outcome.current_attempt_id,
            "committed_attempt_id": outcome.committed_attempt_id,
            "authoritative_output_id": outcome.authoritative_output_id,
        },
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "stale_attempt_admission_probe_is_gate_metric": False,
        "stale_attempt_admission_decisions": [
            {
                "event_id": event_id,
                **_placement_to_dict(decision),
            }
            for (event_id, _), decision in zip(
                physical_late_inputs, admission_decisions, strict=True
            )
        ],
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S1_E0_COHORT_ID,
        trial_id=scenario_id,
        operation_id="r",
        policy_id=policy_id,
        scenario_id=scenario_id,
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth=ground_truth,
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=semantic_correct,
            recovery_actions=recovery_actions,
        ),
        metric_opportunities=tuple(opportunities),
        metric_opportunity_event_ids=tuple(opportunity_event_ids),
        metric_opportunity_scopes=tuple(opportunity_scopes),
        metric_violations=tuple(violations),
        metric_violation_event_ids=tuple(violation_event_ids),
        fault_id=f"S1:{scenario_id}",
        fault_class=_FAULT_CLASS[scenario_id],
    )

    return AttemptFencingTrial(
        policy_id=policy_id,
        scenario_id=scenario_id,
        evaluation=evaluation,
        authoritative_outcome=outcome,
        stale_result_event_ids=stale_event_ids,
        stale_attempt_ids=stale_attempt_ids,
        finalization_applied_count=finalization_applied_count,
        stale_admission_decisions=admission_decisions,
    )


def run_s1_e0_paired() -> AttemptFencingEvaluation:
    trials = tuple(
        run_s1_e0_trial(policy_id, scenario_id)
        for scenario_id in S1_E0_SCENARIOS
        for policy_id in PolicyID
    )
    summary = summarize_correctness(tuple(trial.evaluation for trial in trials))
    return AttemptFencingEvaluation(trials=trials, summary=summary)
