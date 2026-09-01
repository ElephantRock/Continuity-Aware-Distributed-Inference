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
        if not all(isinstance(item, PlacementDecision) for item in self.stale_admission_decisions):
            raise TypeError("stale_admission_decisions must contain PlacementDecision values")


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
        actual_order = tuple((trial.scenario_id, trial.policy_id) for trial in self.trials)
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


def _run_semantic_trace(
    policy_id: PolicyID,
    scenario_id: str,
) -> tuple[
    ContinuityCore,
    ContinuityAdapter,
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[PlacementDecision, ...],
]:
    definition = scenario_definition(scenario_id)
    schedule = definition.build(seed=0)
    sim = DiscreteEventSimulator(seed=0)
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    policies = build_baseline_policies(CoreContinuityAuthority(core))
    policy = policies[policy_id]

    supersession_checks: list[dict[str, Any]] = []
    stale_presentations: list[dict[str, Any]] = []
    admission_decisions: list[PlacementDecision] = []
    supersession_times: dict[str, float] = {}

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

        stale = {
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
        stale_presentations.append(stale)

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

    sim.register_handler(EventKind.RETRY_STARTED, on_retry_started)
    sim.register_handler(EventKind.LATE_RESULT, on_late_result)
    schedule.apply(sim)
    sim.run()

    if scenario_id in _RETRY_SCENARIOS:
        if not supersession_checks:
            raise AssertionError("mandatory S1 retry scenario did not produce supersession evidence")
        if not stale_presentations:
            raise AssertionError("mandatory S1 retry scenario did not present a stale Attempt result")

    return (
        core,
        adapter,
        tuple(supersession_checks),
        tuple(stale_presentations),
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
    core, adapter, supersession_checks, stale_presentations, admission_decisions = (
        _run_semantic_trace(policy_id, scenario_id)
    )
    outcome = authoritative_outcome(core, "r")

    if core.requests["r"].status is not RequestStatus.COMPLETED:
        raise AssertionError(f"{scenario_id} must end in a completed LogicalRequest")
    if outcome.authoritative_output_id is None:
        raise AssertionError(f"{scenario_id} must produce one authoritative output")

    stale_attempt_ids = tuple(sorted({item["attempt_id"] for item in stale_presentations}))
    stale_event_ids = tuple(item["event_id"] for item in stale_presentations)
    stale_accepted = outcome.committed_attempt_id in set(stale_attempt_ids)

    finalization_records = tuple(
        record for record in adapter.records if record.operation == "finalize_request"
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
        if stale_accepted:
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
        "stale_attempt_ids": list(stale_attempt_ids),
        "stale_result_event_ids": list(stale_event_ids),
    }
    observed_evidence = {
        "supersession_checks": list(supersession_checks),
        "stale_result_presentations": list(stale_presentations),
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
                "event_id": stale_event_id,
                **_placement_to_dict(decision),
            }
            for stale_event_id, decision in zip(
                stale_event_ids, admission_decisions, strict=True
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
