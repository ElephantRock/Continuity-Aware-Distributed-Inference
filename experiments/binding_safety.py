from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping

from continuity.core import ContinuityCore
from continuity.entities import (
    BindingStatus,
    ContinuationLifecycle,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
)
from continuity.errors import ContinuityError
from continuity.invariants import InvariantOracle
from simulator import (
    CoreContinuityAuthority,
    MigrationDecision,
    PolicyID,
    PolicyObservation,
    build_baseline_policies,
    project_observation,
)

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


S3_E0_SCHEMA = "cadi.c4.4a.binding-safety-e0.v1"
S3_E0_COHORT_ID = "C4.4a:S3:EV0"
S3_E0_SUBJECT_ID = "state:x"


class BindingScenarioMode(str, Enum):
    PARTIAL_MIGRATION = "PARTIAL_MIGRATION"
    STALE_EPOCH_CANDIDATE = "STALE_EPOCH_CANDIDATE"
    LATE_OLD_OWNER = "LATE_OLD_OWNER"
    CONCURRENT_MIGRATION = "CONCURRENT_MIGRATION"
    SUCCESS_CONTROL = "SUCCESS_CONTROL"


@dataclass(frozen=True, slots=True)
class BindingScenarioSpec:
    scenario_id: str
    mode: BindingScenarioMode
    fault_class: str | None
    sbdr_event_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise ValueError("scenario_id must be a non-empty string")
        if not isinstance(self.mode, BindingScenarioMode):
            raise TypeError("mode must be BindingScenarioMode")
        if self.fault_class is not None and (
            not isinstance(self.fault_class, str) or not self.fault_class
        ):
            raise ValueError("fault_class must be a non-empty string or None")
        if self.sbdr_event_id is not None and (
            not isinstance(self.sbdr_event_id, str) or not self.sbdr_event_id
        ):
            raise ValueError("sbdr_event_id must be a non-empty string or None")
        if (self.fault_class is None) != (self.sbdr_event_id is None):
            raise ValueError("faulted S3 scenarios require exactly one SBDR presentation")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S3:EV0:{self.scenario_id}"


S3_E0_SCENARIO_SPECS = (
    BindingScenarioSpec(
        "S3-PARTIAL-MIGRATION",
        BindingScenarioMode.PARTIAL_MIGRATION,
        "partial migration without sufficient commit evidence",
        "S3:EV0:partial:migration-commit-presentation",
    ),
    BindingScenarioSpec(
        "S3-STALE-EPOCH-CANDIDATE",
        BindingScenarioMode.STALE_EPOCH_CANDIDATE,
        "stale candidate created from an old Binding base epoch",
        "S3:EV0:stale:candidate-commit-presentation",
    ),
    BindingScenarioSpec(
        "FTR8-LATE-OLD-OWNER",
        BindingScenarioMode.LATE_OLD_OWNER,
        "late old-owner Binding event after migration commit",
        "S3:EV0:ftr8:late-old-owner-presentation",
    ),
    BindingScenarioSpec(
        "S3-CONCURRENT-MIGRATION",
        BindingScenarioMode.CONCURRENT_MIGRATION,
        "concurrent migration candidates racing from one base epoch",
        "S3:EV0:concurrent:loser-commit-presentation",
    ),
    BindingScenarioSpec(
        "S3-SUCCESS-CONTROL",
        BindingScenarioMode.SUCCESS_CONTROL,
        None,
        None,
    ),
)
S3_E0_SCENARIOS = tuple(item.scenario_id for item in S3_E0_SCENARIO_SPECS)
_SPEC_BY_ID: Mapping[str, BindingScenarioSpec] = {
    item.scenario_id: item for item in S3_E0_SCENARIO_SPECS
}


@dataclass(frozen=True, slots=True)
class BindingPresentationResult:
    event_id: str
    binding_id: str
    binding_epoch: int
    before: Mapping[str, Any]
    after: Mapping[str, Any]
    commit_outcome: str
    error_type: str | None
    diverged_from_oracle: bool
    invariant_error_type: str | None

    def __post_init__(self) -> None:
        for value, name in (
            (self.event_id, "event_id"),
            (self.binding_id, "binding_id"),
            (self.commit_outcome, "commit_outcome"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.binding_epoch, int) or isinstance(self.binding_epoch, bool):
            raise TypeError("binding_epoch must be int")
        if self.binding_epoch < 0:
            raise ValueError("binding_epoch must be non-negative")
        if not isinstance(self.before, Mapping) or not isinstance(self.after, Mapping):
            raise TypeError("before/after must be mappings")
        if self.error_type is not None and (
            not isinstance(self.error_type, str) or not self.error_type
        ):
            raise ValueError("error_type must be non-empty string or None")
        if self.invariant_error_type is not None and (
            not isinstance(self.invariant_error_type, str) or not self.invariant_error_type
        ):
            raise ValueError("invariant_error_type must be non-empty string or None")
        if not isinstance(self.diverged_from_oracle, bool):
            raise TypeError("diverged_from_oracle must be bool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "binding_id": self.binding_id,
            "binding_epoch": self.binding_epoch,
            "before": dict(self.before),
            "after": dict(self.after),
            "commit_outcome": self.commit_outcome,
            "error_type": self.error_type,
            "diverged_from_oracle": self.diverged_from_oracle,
            "invariant_error_type": self.invariant_error_type,
        }


@dataclass(frozen=True, slots=True)
class BindingSafetyTrial:
    policy_id: PolicyID
    scenario_id: str
    evaluation: CorrectnessEvaluationRecord
    policy_migration_decisions: tuple[MigrationDecision, ...]
    presentation: BindingPresentationResult | None
    expected_binding_id: str
    expected_epoch: int
    final_binding_id: str
    final_epoch: int
    injected_divergence: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.scenario_id not in S3_E0_SCENARIOS:
            raise ValueError("scenario_id must be canonical S3 EV0 scenario")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy must match trial policy")
        if self.evaluation.scenario_id != self.scenario_id:
            raise ValueError("evaluation scenario must match trial scenario")
        if not all(isinstance(item, MigrationDecision) for item in self.policy_migration_decisions):
            raise TypeError("policy_migration_decisions must contain MigrationDecision")
        if self.policy_id is not PolicyID.B4 and self.policy_migration_decisions:
            raise ValueError("only B4 has the frozen migration-admission policy surface")
        for value, name in (
            (self.expected_binding_id, "expected_binding_id"),
            (self.final_binding_id, "final_binding_id"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        for value, name in (
            (self.expected_epoch, "expected_epoch"),
            (self.final_epoch, "final_epoch"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        if not isinstance(self.injected_divergence, bool):
            raise TypeError("injected_divergence must be bool")


@dataclass(frozen=True, slots=True)
class BindingSafetyEvaluation:
    trials: tuple[BindingSafetyTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (spec.scenario_id, policy_id)
            for spec in S3_E0_SCENARIO_SPECS
            for policy_id in PolicyID
        )
        actual = tuple((trial.scenario_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("S3 E0 trials must use canonical scenario then B0-B4 ordering")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _scaffold_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s", lifecycle=ContinuationLifecycle.ACTIVE)
    core.create_state("x", origin_type="continuation", origin_id="c")
    core.activate_initial_binding("b1", S3_E0_SUBJECT_ID, "w1")
    return core


def _authority_snapshot(core: ContinuityCore) -> dict[str, Any]:
    current_id = core.current_binding_by_subject[S3_E0_SUBJECT_ID]
    current_epoch = core.current_epoch_by_subject[S3_E0_SUBJECT_ID]
    return {
        "subject_id": S3_E0_SUBJECT_ID,
        "current_binding_id": current_id,
        "current_epoch": current_epoch,
        "bindings": [
            {
                "binding_id": binding.id,
                "location_id": binding.location_id,
                "base_epoch": binding.base_epoch,
                "epoch": binding.epoch,
                "status": binding.status.name,
            }
            for binding in sorted(core.bindings.values(), key=lambda item: (item.epoch, item.id))
            if binding.subject_id == S3_E0_SUBJECT_ID
        ],
    }


def _record_commit_evidence(
    core: ContinuityCore,
    *,
    binding_id: str,
    evidence_id: str,
    observed_at: float,
) -> str:
    binding = core.bindings[binding_id]
    core.record_evidence(
        Evidence(
            id=evidence_id,
            claim="migration commit readiness",
            source="C4.4a deterministic migration oracle fixture",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=observed_at,
            scope=frozenset(
                {
                    ("binding", binding.id),
                    ("epoch", str(binding.epoch)),
                }
            ),
        )
    )
    return evidence_id


def _b4_migration_decision(
    core: ContinuityCore,
    policy_id: PolicyID,
    *,
    binding_id: str,
    reconciliation: str,
) -> MigrationDecision | None:
    if policy_id is not PolicyID.B4:
        return None
    policy = build_baseline_policies(CoreContinuityAuthority(core))[PolicyID.B4]
    decide_migration = getattr(policy, "decide_migration", None)
    if not callable(decide_migration):
        raise AssertionError("B4 policy must expose decide_migration")
    binding = core.bindings[binding_id]
    observation = PolicyObservation(
        request_id=f"binding-op:{binding_id}",
        workers=(),
        binding_id=binding.id,
        binding_epoch=binding.epoch,
        reconciliation=reconciliation,
    )
    return decide_migration(project_observation(observation, PolicyID.B4))


def _attempt_binding_commit(
    core: ContinuityCore,
    *,
    event_id: str,
    binding_id: str,
    evidence_ids: tuple[str, ...],
    expected_binding_id: str,
    expected_epoch: int,
    now: float,
    inject_divergence: bool,
) -> BindingPresentationResult:
    binding = core.bindings[binding_id]
    before = _authority_snapshot(core)
    error: ContinuityError | None = None
    try:
        core.commit_migration(binding_id, evidence_ids, now=now)
    except ContinuityError as exc:
        error = exc
    after_c1 = _authority_snapshot(core)

    commit_outcome = (
        "APPLIED"
        if after_c1 != before
        else ("REJECTED" if error is not None else "IDEMPOTENT")
    )

    if inject_divergence:
        previous_id = core.current_binding_by_subject[S3_E0_SUBJECT_ID]
        if previous_id != binding_id:
            previous = core.bindings[previous_id]
            core.bindings[previous_id] = replace(
                previous, status=BindingStatus.SUPERSEDED
            )
        core.bindings[binding_id] = replace(binding, status=BindingStatus.ACTIVE)
        core.current_binding_by_subject[S3_E0_SUBJECT_ID] = binding_id
        core.current_epoch_by_subject[S3_E0_SUBJECT_ID] = binding.epoch

    after = _authority_snapshot(core)
    diverged = (
        after["current_binding_id"] != expected_binding_id
        or after["current_epoch"] != expected_epoch
    )

    invariant_error: Exception | None = None
    try:
        InvariantOracle(core).assert_all()
    except Exception as exc:
        invariant_error = exc

    return BindingPresentationResult(
        event_id=event_id,
        binding_id=binding.id,
        binding_epoch=binding.epoch,
        before=before,
        after=after,
        commit_outcome=commit_outcome,
        error_type=None if error is None else type(error).__name__,
        diverged_from_oracle=diverged,
        invariant_error_type=(
            None if invariant_error is None else type(invariant_error).__name__
        ),
    )


def _commit_setup_winner(
    core: ContinuityCore,
    binding_id: str,
    *,
    evidence_id: str,
    now: float,
) -> None:
    _record_commit_evidence(
        core,
        binding_id=binding_id,
        evidence_id=evidence_id,
        observed_at=now,
    )
    core.commit_migration(binding_id, (evidence_id,), now=now)
    InvariantOracle(core).assert_all()


def _run_sequence(
    spec: BindingScenarioSpec,
    policy_id: PolicyID,
    *,
    inject_divergence: bool,
) -> tuple[
    ContinuityCore,
    tuple[MigrationDecision, ...],
    BindingPresentationResult | None,
    str,
    int,
    Mapping[str, Any],
]:
    core = _scaffold_core()
    decisions: list[MigrationDecision] = []
    setup: dict[str, Any] = {
        "initial_authority": _authority_snapshot(core),
        "physical_events": [],
        "setup_commits": [],
    }

    if spec.mode is BindingScenarioMode.PARTIAL_MIGRATION:
        candidate = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(candidate.id)
        setup["physical_events"].append(
            {
                "event": "PARTIAL_MATERIALIZATION_OBSERVED",
                "binding_id": candidate.id,
                "epoch": candidate.epoch,
                "location_id": candidate.location_id,
                "semantic_commit_evidence": False,
            }
        )
        decision = _b4_migration_decision(
            core,
            policy_id,
            binding_id=candidate.id,
            reconciliation="WAIT",
        )
        if decision is not None:
            decisions.append(decision)
        expected_binding_id, expected_epoch = "b1", 1
        presentation = _attempt_binding_commit(
            core,
            event_id=spec.sbdr_event_id or "",
            binding_id=candidate.id,
            evidence_ids=(),
            expected_binding_id=expected_binding_id,
            expected_epoch=expected_epoch,
            now=2.0,
            inject_divergence=inject_divergence,
        )

    elif spec.mode is BindingScenarioMode.STALE_EPOCH_CANDIDATE:
        winner = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(winner.id)
        stale = core.propose_binding("b3", S3_E0_SUBJECT_ID, "w3")
        core.begin_migration(stale.id)
        _commit_setup_winner(
            core,
            winner.id,
            evidence_id="S3:EV0:stale:winner-evidence",
            now=2.0,
        )
        setup["setup_commits"].append(
            {"binding_id": winner.id, "epoch": winner.epoch, "location_id": winner.location_id}
        )
        stale_evidence = _record_commit_evidence(
            core,
            binding_id=stale.id,
            evidence_id="S3:EV0:stale:candidate-evidence",
            observed_at=3.0,
        )
        decision = _b4_migration_decision(
            core,
            policy_id,
            binding_id=stale.id,
            reconciliation="MATCHED",
        )
        if decision is not None:
            decisions.append(decision)
        expected_binding_id, expected_epoch = winner.id, winner.epoch
        presentation = _attempt_binding_commit(
            core,
            event_id=spec.sbdr_event_id or "",
            binding_id=stale.id,
            evidence_ids=(stale_evidence,),
            expected_binding_id=expected_binding_id,
            expected_epoch=expected_epoch,
            now=3.0,
            inject_divergence=inject_divergence,
        )

    elif spec.mode is BindingScenarioMode.LATE_OLD_OWNER:
        winner = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(winner.id)
        _commit_setup_winner(
            core,
            winner.id,
            evidence_id="S3:EV0:ftr8:winner-evidence",
            now=2.0,
        )
        setup["setup_commits"].append(
            {"binding_id": winner.id, "epoch": winner.epoch, "location_id": winner.location_id}
        )
        old_evidence = _record_commit_evidence(
            core,
            binding_id="b1",
            evidence_id="S3:EV0:ftr8:late-old-owner-evidence",
            observed_at=1.5,
        )
        decision = _b4_migration_decision(
            core,
            policy_id,
            binding_id="b1",
            reconciliation="MATCHED",
        )
        if decision is not None:
            decisions.append(decision)
        expected_binding_id, expected_epoch = winner.id, winner.epoch
        presentation = _attempt_binding_commit(
            core,
            event_id=spec.sbdr_event_id or "",
            binding_id="b1",
            evidence_ids=(old_evidence,),
            expected_binding_id=expected_binding_id,
            expected_epoch=expected_epoch,
            now=3.0,
            inject_divergence=inject_divergence,
        )

    elif spec.mode is BindingScenarioMode.CONCURRENT_MIGRATION:
        winner = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(winner.id)
        loser = core.propose_binding("b3", S3_E0_SUBJECT_ID, "w3")
        core.begin_migration(loser.id)
        setup["physical_events"].append(
            {
                "event": "CONCURRENT_MIGRATION_CANDIDATES",
                "bindings": [winner.id, loser.id],
                "shared_base_epoch": winner.base_epoch,
            }
        )
        for candidate in (winner, loser):
            decision = _b4_migration_decision(
                core,
                policy_id,
                binding_id=candidate.id,
                reconciliation="MATCHED",
            )
            if decision is not None:
                decisions.append(decision)
        _commit_setup_winner(
            core,
            winner.id,
            evidence_id="S3:EV0:concurrent:winner-evidence",
            now=2.0,
        )
        setup["setup_commits"].append(
            {"binding_id": winner.id, "epoch": winner.epoch, "location_id": winner.location_id}
        )
        loser_evidence = _record_commit_evidence(
            core,
            binding_id=loser.id,
            evidence_id="S3:EV0:concurrent:loser-evidence",
            observed_at=2.5,
        )
        expected_binding_id, expected_epoch = winner.id, winner.epoch
        presentation = _attempt_binding_commit(
            core,
            event_id=spec.sbdr_event_id or "",
            binding_id=loser.id,
            evidence_ids=(loser_evidence,),
            expected_binding_id=expected_binding_id,
            expected_epoch=expected_epoch,
            now=2.5,
            inject_divergence=inject_divergence,
        )

    elif spec.mode is BindingScenarioMode.SUCCESS_CONTROL:
        candidate = core.propose_binding("b2", S3_E0_SUBJECT_ID, "w2")
        core.begin_migration(candidate.id)
        decision = _b4_migration_decision(
            core,
            policy_id,
            binding_id=candidate.id,
            reconciliation="MATCHED",
        )
        if decision is not None:
            decisions.append(decision)
        evidence_id = _record_commit_evidence(
            core,
            binding_id=candidate.id,
            evidence_id="S3:EV0:control:commit-evidence",
            observed_at=2.0,
        )
        before = _authority_snapshot(core)
        core.commit_migration(candidate.id, (evidence_id,), now=2.0)
        after = _authority_snapshot(core)
        InvariantOracle(core).assert_all()
        if before == after:
            raise AssertionError("success control must advance authoritative Binding")
        expected_binding_id, expected_epoch = candidate.id, candidate.epoch
        presentation = None
        setup["setup_commits"].append(
            {"binding_id": candidate.id, "epoch": candidate.epoch, "location_id": candidate.location_id}
        )

    else:
        raise AssertionError("unhandled S3 E0 scenario mode")

    setup["expected_final_authority"] = {
        "binding_id": expected_binding_id,
        "epoch": expected_epoch,
    }
    setup["final_authority"] = _authority_snapshot(core)
    return (
        core,
        tuple(decisions),
        presentation,
        expected_binding_id,
        expected_epoch,
        setup,
    )


def _semantic_result(
    spec: BindingScenarioSpec,
    *,
    final_matches_oracle: bool,
    inject_divergence: bool,
) -> SemanticResult:
    if inject_divergence:
        return SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )
    if spec.mode is BindingScenarioMode.PARTIAL_MIGRATION:
        return SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.WAIT,
        )
    return SemanticResult(
        reported_success=True,
        authoritative_commit=True,
        semantically_correct=final_matches_oracle,
    )


def _run_s3_e0_trial(
    policy_id: PolicyID,
    scenario_id: str,
    *,
    inject_divergence: bool = False,
) -> BindingSafetyTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    spec = _SPEC_BY_ID.get(scenario_id)
    if spec is None:
        raise ValueError(f"scenario_id must be one of {S3_E0_SCENARIOS!r}")
    if inject_divergence and spec.fault_id is None:
        raise ValueError("anti-false-zero divergence injection requires a faulted scenario")

    (
        core,
        migration_decisions,
        presentation,
        expected_binding_id,
        expected_epoch,
        setup,
    ) = _run_sequence(spec, policy_id, inject_divergence=inject_divergence)

    final = _authority_snapshot(core)
    final_binding_id = str(final["current_binding_id"])
    final_epoch = int(final["current_epoch"])
    final_matches_oracle = (
        final_binding_id == expected_binding_id and final_epoch == expected_epoch
    )
    diverged = not final_matches_oracle
    if presentation is not None and presentation.diverged_from_oracle != diverged:
        raise AssertionError("presentation divergence must match final authority divergence")

    opportunities: tuple[CorrectnessMetric, ...] = ()
    opportunity_event_ids: tuple[str, ...] = ()
    opportunity_scopes: tuple[MetricOpportunityScope, ...] = ()
    violations: tuple[CorrectnessMetric, ...] = ()
    violation_event_ids: tuple[str, ...] = ()
    if spec.sbdr_event_id is not None:
        opportunities = (CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,)
        opportunity_event_ids = (spec.sbdr_event_id,)
        opportunity_scopes = (MetricOpportunityScope.EXOGENOUS_PAIRED,)
        if diverged:
            violations = (CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,)
            violation_event_ids = (spec.sbdr_event_id,)

    ground_truth = {
        "schema": S3_E0_SCHEMA,
        "scenario_id": spec.scenario_id,
        "mode": spec.mode.value,
        "subject_id": S3_E0_SUBJECT_ID,
        "fault_class": spec.fault_class,
        "sbdr_event_id": spec.sbdr_event_id,
        "initial_binding_id": "b1",
        "initial_epoch": 1,
        "expected_final_binding_id": expected_binding_id,
        "expected_final_epoch": expected_epoch,
        "oracle_rule": "STALE_OR_CONFLICTING_BINDING_PRESENTATION_MUST_NOT_CHANGE_CURRENT_AUTHORITY",
        "semantic_authority": "C1_COMMON_TO_B0_B4",
    }
    observed_evidence = {
        "sequence": dict(setup),
        "migration_presentation": (
            None if presentation is None else presentation.to_dict()
        ),
        "final_authority": final,
        "injected_divergence": inject_divergence,
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "binding_information_contract": (
            "B4_BINDING_ID_EPOCH_RECONCILIATION"
            if policy_id is PolicyID.B4
            else "NO_BINDING_AWARE_MIGRATION_POLICY_SURFACE"
        ),
        "migration_decisions": [
            {
                "binding_id": decision.binding_id,
                "binding_epoch": decision.binding_epoch,
                "disposition": decision.disposition.value,
                "reason": decision.reason,
            }
            for decision in migration_decisions
        ],
        "c1_commit_is_authoritative_not_policy_decision": True,
        "oracle_expected_authority_is_not_policy_visible": True,
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S3_E0_COHORT_ID,
        trial_id=spec.scenario_id,
        operation_id=f"binding:{S3_E0_SUBJECT_ID}",
        policy_id=policy_id,
        scenario_id=spec.scenario_id,
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth=ground_truth,
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=_semantic_result(
            spec,
            final_matches_oracle=final_matches_oracle,
            inject_divergence=inject_divergence,
        ),
        metric_opportunities=opportunities,
        metric_opportunity_event_ids=opportunity_event_ids,
        metric_opportunity_scopes=opportunity_scopes,
        metric_violations=violations,
        metric_violation_event_ids=violation_event_ids,
        fault_id=spec.fault_id,
        fault_class=spec.fault_class,
    )

    return BindingSafetyTrial(
        policy_id=policy_id,
        scenario_id=scenario_id,
        evaluation=evaluation,
        policy_migration_decisions=migration_decisions,
        presentation=presentation,
        expected_binding_id=expected_binding_id,
        expected_epoch=expected_epoch,
        final_binding_id=final_binding_id,
        final_epoch=final_epoch,
        injected_divergence=inject_divergence,
    )


def run_s3_e0_trial(policy_id: PolicyID, scenario_id: str) -> BindingSafetyTrial:
    return _run_s3_e0_trial(policy_id, scenario_id)


def run_s3_e0_paired() -> BindingSafetyEvaluation:
    trials = tuple(
        run_s3_e0_trial(policy_id, spec.scenario_id)
        for spec in S3_E0_SCENARIO_SPECS
        for policy_id in PolicyID
    )
    return BindingSafetyEvaluation(
        trials=trials,
        summary=summarize_correctness(tuple(item.evaluation for item in trials)),
    )
