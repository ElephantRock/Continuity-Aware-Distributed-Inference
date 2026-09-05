from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from continuity.core import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    ContinuationLifecycle,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionContext,
    PhaseStatus,
    PhaseType,
    RequestStatus,
    StateValidity,
)
from simulator import (
    CoreContinuityAuthority,
    PlacementDecision,
    PolicyID,
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
from .state_lineage import (
    ApplicationEffect,
    ApplicationExecutionProfile,
    StateConsumptionDirective,
    StateConsumptionEvent,
    TerminalExecutionEvent,
    _ScenarioRuntime,
    _base_core,
    _execute_consumption_directive,
    _execute_terminal_outcome,
    _observation,
    _placement_to_dict,
)


S2_ADVERSARIAL_SCHEMA = "cadi.c4.3b.state-lineage-adversarial.v1"
S2_ADVERSARIAL_COHORT_ID = "C4.3b:S2:EV0"


class StateLineagePressureFamily(str, Enum):
    BRANCH = "A_BRANCH_ANCESTRY"
    PRODUCER = "B_PRODUCER_AUTHORITY"
    PHASE = "C_PHASE_CONTEXT"
    DERIVED = "D_DERIVED_DEPENDENCY"
    VALIDITY = "E_STATE_VALIDITY"


@dataclass(frozen=True, slots=True)
class StateLineageAdversarialManifest:
    case_id: str
    pressure_family: StateLineagePressureFamily
    fault_class: str | None
    wbrr_event_id: str | None
    application_effect: ApplicationEffect
    expected_exact_compatible: bool

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("case_id must be a non-empty string")
        if not isinstance(self.pressure_family, StateLineagePressureFamily):
            raise TypeError("pressure_family must be StateLineagePressureFamily")
        if self.fault_class is not None and (
            not isinstance(self.fault_class, str) or not self.fault_class
        ):
            raise ValueError("fault_class must be a non-empty string or None")
        if self.wbrr_event_id is not None and (
            not isinstance(self.wbrr_event_id, str) or not self.wbrr_event_id
        ):
            raise ValueError("wbrr_event_id must be a non-empty string or None")
        if not isinstance(self.application_effect, ApplicationEffect):
            raise TypeError("application_effect must be ApplicationEffect")
        if not isinstance(self.expected_exact_compatible, bool):
            raise TypeError("expected_exact_compatible must be bool")
        if self.wbrr_event_id is not None and self.fault_class is None:
            raise ValueError("WBRR opportunity must belong to a faulted case")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S2B:{self.case_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": S2_ADVERSARIAL_SCHEMA,
            "case_id": self.case_id,
            "pressure_family": self.pressure_family.value,
            "fault_class": self.fault_class,
            "wbrr_event_id": self.wbrr_event_id,
            "application_effect": self.application_effect.value,
            "expected_exact_compatible": self.expected_exact_compatible,
        }


def _manifest(
    case_id: str,
    family: StateLineagePressureFamily,
    *,
    fault_class: str | None = None,
    wbrr: bool = False,
    effect: ApplicationEffect = ApplicationEffect.CORRECT_RESULT,
    compatible: bool,
) -> StateLineageAdversarialManifest:
    return StateLineageAdversarialManifest(
        case_id=case_id,
        pressure_family=family,
        fault_class=fault_class,
        wbrr_event_id=(
            f"S2B:{case_id}:wrong-branch-reuse-opportunity" if wbrr else None
        ),
        application_effect=effect,
        expected_exact_compatible=compatible,
    )


S2_ADVERSARIAL_MANIFESTS = (
    _manifest(
        "S2A-DEEP-SIBLING",
        StateLineagePressureFamily.BRANCH,
        fault_class="deep wrong sibling state",
        wbrr=True,
        effect=ApplicationEffect.WRONG_UNDETECTED,
        compatible=False,
    ),
    _manifest(
        "S2A-DEEP-ABANDONED",
        StateLineagePressureFamily.BRANCH,
        fault_class="deep abandoned-branch residual state",
        wbrr=True,
        effect=ApplicationEffect.DETECT_AND_RECOMPUTE,
        compatible=False,
    ),
    _manifest(
        "S2A-DEEP-ANCESTOR-CONTROL",
        StateLineagePressureFamily.BRANCH,
        compatible=True,
    ),
    _manifest(
        "S2B-THREE-GEN-SUPERSEDED",
        StateLineagePressureFamily.PRODUCER,
        fault_class="three-generation superseded producer state",
        effect=ApplicationEffect.CORRECT_RESULT,
        compatible=False,
    ),
    _manifest(
        "S2B-COMMITTED-PRODUCER-CONTROL",
        StateLineagePressureFamily.PRODUCER,
        compatible=True,
    ),
    _manifest(
        "S2B-REQUEST-ORIGIN-CONTROL",
        StateLineagePressureFamily.PRODUCER,
        compatible=True,
    ),
    _manifest(
        "S2C-PHASE-LATER-CONTROL",
        StateLineagePressureFamily.PHASE,
        compatible=True,
    ),
    _manifest(
        "S2C-PHASE-SAME",
        StateLineagePressureFamily.PHASE,
        fault_class="same-Phase State reuse",
        effect=ApplicationEffect.WRONG_UNDETECTED,
        compatible=False,
    ),
    _manifest(
        "S2C-PHASE-EARLIER",
        StateLineagePressureFamily.PHASE,
        fault_class="earlier-Phase consumer of later-Phase State",
        effect=ApplicationEffect.DETECT_AND_RECOMPUTE,
        compatible=False,
    ),
    _manifest(
        "S2C-PHASE-NO-CONTEXT",
        StateLineagePressureFamily.PHASE,
        fault_class="Phase-origin State without consumer Phase context",
        effect=ApplicationEffect.CORRECT_RESULT,
        compatible=False,
    ),
    _manifest(
        "S2D-DERIVED-VALID-CONTROL",
        StateLineagePressureFamily.DERIVED,
        compatible=True,
    ),
    _manifest(
        "S2D-DERIVED-INVALID-DEPENDENCY",
        StateLineagePressureFamily.DERIVED,
        fault_class="derived State with invalid dependency",
        effect=ApplicationEffect.WRONG_UNDETECTED,
        compatible=False,
    ),
    _manifest(
        "S2D-DERIVED-SUPERSEDED-DEPENDENCY",
        StateLineagePressureFamily.DERIVED,
        fault_class="derived State with superseded producer dependency",
        effect=ApplicationEffect.CORRECT_RESULT,
        compatible=False,
    ),
    _manifest(
        "S2D-DERIVED-MIXED-DEPENDENCY",
        StateLineagePressureFamily.DERIVED,
        fault_class="derived State with mixed valid and superseded dependencies",
        effect=ApplicationEffect.DETECT_AND_RECOMPUTE,
        compatible=False,
    ),
    _manifest(
        "S2E-TOP-LEVEL-INVALID",
        StateLineagePressureFamily.VALIDITY,
        fault_class="logically invalid State with valid physical replica",
        effect=ApplicationEffect.WRONG_UNDETECTED,
        compatible=False,
    ),
    _manifest(
        "S2E-VALID-CONTROL",
        StateLineagePressureFamily.VALIDITY,
        compatible=True,
    ),
)

S2_ADVERSARIAL_CASE_IDS = tuple(item.case_id for item in S2_ADVERSARIAL_MANIFESTS)
_MANIFEST_BY_ID = {item.case_id: item for item in S2_ADVERSARIAL_MANIFESTS}


@dataclass(frozen=True, slots=True)
class StateLineageAdversarialTrial:
    policy_id: PolicyID
    manifest: StateLineageAdversarialManifest
    evaluation: CorrectnessEvaluationRecord
    placement_decision: PlacementDecision
    candidate_state_id: str
    consumption_event: StateConsumptionEvent | None
    terminal_event: TerminalExecutionEvent
    independent_oracle_compatible: bool
    c1_exact_context_compatible: bool
    b4_effective_compatible: bool
    safe_conservative_b4: bool

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if not isinstance(self.manifest, StateLineageAdversarialManifest):
            raise TypeError("manifest must be StateLineageAdversarialManifest")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy_id must match trial policy_id")
        if self.evaluation.scenario_id != self.manifest.case_id:
            raise ValueError("evaluation scenario_id must match manifest case_id")
        if self.placement_decision.policy_id is not self.policy_id:
            raise ValueError("placement decision must match trial policy")
        if self.independent_oracle_compatible != self.c1_exact_context_compatible:
            raise ValueError("direct C1 exact-context compatibility must match oracle")
        if self.c1_exact_context_compatible != self.manifest.expected_exact_compatible:
            raise ValueError("manifest compatibility expectation does not match exact C1")
        expected_conservative = (
            self.policy_id is PolicyID.B4
            and self.c1_exact_context_compatible
            and not self.b4_effective_compatible
        )
        if self.safe_conservative_b4 != expected_conservative:
            raise ValueError("safe_conservative_b4 is inconsistent with compatibility views")
        consumed = None if self.consumption_event is None else self.consumption_event.state_id
        if self.terminal_event.consumed_state_id != consumed:
            raise ValueError("terminal event must reference actual State consumption")


@dataclass(frozen=True, slots=True)
class StateLineageAdversarialEvaluation:
    trials: tuple[StateLineageAdversarialTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = len(S2_ADVERSARIAL_MANIFESTS) * len(tuple(PolicyID))
        if len(self.trials) != expected:
            raise ValueError(f"adversarial evaluation must contain exactly {expected} trials")
        canonical = tuple(
            (manifest.case_id, policy_id)
            for manifest in S2_ADVERSARIAL_MANIFESTS
            for policy_id in PolicyID
        )
        actual = tuple((trial.manifest.case_id, trial.policy_id) for trial in self.trials)
        if actual != canonical:
            raise ValueError("adversarial trials must use canonical case then B0-B4 order")

    @property
    def safe_conservative_b4_case_ids(self) -> tuple[str, ...]:
        return tuple(
            trial.manifest.case_id
            for trial in self.trials
            if trial.safe_conservative_b4
        )


def _commit_attempt(core: ContinuityCore, request_id: str, attempt_id: str) -> None:
    core.complete_attempt(attempt_id)
    evidence_id = f"S2B:{attempt_id}:terminal-evidence"
    core.record_evidence(
        Evidence(
            id=evidence_id,
            claim="producer terminal success",
            source="C4.3b deterministic producer fixture",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=0.0,
            scope=frozenset({("attempt", attempt_id)}),
        )
    )
    output_id = f"S2B:{attempt_id}:terminal-output"
    core.create_output(output_id, attempt_id, terminal=True, evidence_ids=(evidence_id,))
    core.finalize_request(request_id, output_id, now=0.0)


def _consumer(core: ContinuityCore, continuation_id: str) -> ExecutionContext:
    core.create_request("r", continuation_id)
    core.start_attempt("a", "r")
    return ExecutionContext("p", "s", continuation_id, "r", "a")


def _runtime(
    manifest: StateLineageAdversarialManifest,
    core: ContinuityCore,
    candidate_state_id: str,
    ctx: ExecutionContext,
    *,
    replica_id: str = "rho-candidate",
    state_locations: tuple[str, ...] = ("w1",),
) -> _ScenarioRuntime:
    observation = _observation(
        core,
        candidate_state_id=candidate_state_id,
        consumer_context=ctx,
        state_locations=state_locations,
    )
    return _ScenarioRuntime(
        scenario_id=manifest.case_id,
        core=core,
        observation=observation,
        candidate_state_id=candidate_state_id,
        consumer_context=ctx,
        candidate_replica_id=replica_id,
        compatible_alternative_state_id=None,
        consumption_directive=StateConsumptionDirective(
            directive_id=f"S2B:{manifest.case_id}:candidate-consumption-directive",
            state_id=candidate_state_id,
        ),
        application_profile=ApplicationExecutionProfile(
            profile_id=f"S2B:{manifest.case_id}:application-profile",
            effect=manifest.application_effect,
        ),
        fault_id=manifest.fault_id,
        fault_class=manifest.fault_class,
        wbrr_event_id=manifest.wbrr_event_id,
    )


def _build_runtime(manifest: StateLineageAdversarialManifest) -> _ScenarioRuntime:
    core = _base_core()
    case_id = manifest.case_id

    if case_id == "S2A-DEEP-SIBLING":
        core.create_continuation("ca", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_continuation("ca1", "s", ("ca",), ContinuationLifecycle.ACTIVE)
        core.create_state("x", origin_type="continuation", origin_id="ca1", representation="KV")
        core.add_replica("rho-candidate", "x", "w1")
        core.create_continuation("cb", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_continuation("cb1", "s", ("cb",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "cb1"))

    if case_id == "S2A-DEEP-ABANDONED":
        core.create_continuation("ca", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_continuation("ca1", "s", ("ca",), ContinuationLifecycle.ACTIVE)
        core.create_state("x", origin_type="continuation", origin_id="ca1", representation="KV")
        core.add_replica("rho-candidate", "x", "w1")
        core.set_continuation_lifecycle("ca1", ContinuationLifecycle.ABANDONED)
        core.create_continuation("cb", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_continuation("cb1", "s", ("cb",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "cb1"))

    if case_id == "S2A-DEEP-ANCESTOR-CONTROL":
        core.create_continuation("c1", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_continuation("c2", "s", ("c1",), ContinuationLifecycle.ACTIVE)
        core.create_state("x", origin_type="continuation", origin_id="c2", representation="KV")
        core.add_replica("rho-candidate", "x", "w1")
        core.create_continuation("c3", "s", ("c2",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "c3"))

    if case_id == "S2B-THREE-GEN-SUPERSEDED":
        core.create_continuation("c1", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_request("rp", "c1")
        core.start_attempt("a1", "rp")
        core.create_state("x", origin_type="attempt", origin_id="a1", representation="KV")
        core.add_replica("rho-candidate", "x", "w1")
        core.start_attempt("a2", "rp")
        core.start_attempt("a3", "rp")
        _commit_attempt(core, "rp", "a3")
        core.create_continuation("c2", "s", ("c1",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "c2"))

    if case_id == "S2B-COMMITTED-PRODUCER-CONTROL":
        core.create_continuation("c1", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_request("rp", "c1")
        core.start_attempt("ap", "rp")
        core.create_state("x", origin_type="attempt", origin_id="ap", representation="KV")
        core.add_replica("rho-candidate", "x", "w1")
        _commit_attempt(core, "rp", "ap")
        core.create_continuation("c2", "s", ("c1",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "c2"))

    if case_id == "S2B-REQUEST-ORIGIN-CONTROL":
        core.create_continuation("c1", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_request("rp", "c1")
        core.start_attempt("ap", "rp")
        _commit_attempt(core, "rp", "ap")
        core.create_state("x", origin_type="request", origin_id="rp", representation="KV")
        core.add_replica("rho-candidate", "x", "w1")
        core.create_continuation("c2", "s", ("c1",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "c2"))

    if case_id in {
        "S2C-PHASE-LATER-CONTROL",
        "S2C-PHASE-SAME",
        "S2C-PHASE-EARLIER",
        "S2C-PHASE-NO-CONTEXT",
    }:
        core.create_request("r", "c0")
        core.start_attempt("a", "r")
        core.create_phase("p1", "a", PhaseType.PREFILL)
        core.set_phase_status("p1", PhaseStatus.RUNNING)
        core.complete_phase("p1")
        if case_id == "S2C-PHASE-EARLIER":
            core.create_phase("p2", "a", PhaseType.DECODE)
            core.set_phase_status("p2", PhaseStatus.RUNNING)
            core.complete_phase("p2")
            core.create_state("x", origin_type="phase", origin_id="p2", representation="KV")
            ctx = ExecutionContext("p", "s", "c0", "r", "a", phase_id="p1")
        else:
            core.create_state("x", origin_type="phase", origin_id="p1", representation="KV")
            if case_id == "S2C-PHASE-LATER-CONTROL":
                core.create_phase("p2", "a", PhaseType.DECODE)
                core.set_phase_status("p2", PhaseStatus.RUNNING)
                ctx = ExecutionContext("p", "s", "c0", "r", "a", phase_id="p2")
            elif case_id == "S2C-PHASE-SAME":
                ctx = ExecutionContext("p", "s", "c0", "r", "a", phase_id="p1")
            else:
                ctx = ExecutionContext("p", "s", "c0", "r", "a")
        core.add_replica("rho-candidate", "x", "w1")
        return _runtime(manifest, core, "x", ctx)

    if case_id == "S2D-DERIVED-VALID-CONTROL":
        core.create_state("x0", origin_type="continuation", origin_id="c0", representation="KV")
        core.create_continuation("c1", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_state(
            "x", origin_type="continuation", origin_id="c1", representation="KV", derived_from=("x0",)
        )
        core.add_replica("rho-candidate", "x", "w1")
        core.create_continuation("c2", "s", ("c1",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "c2"))

    if case_id == "S2D-DERIVED-INVALID-DEPENDENCY":
        core.create_state("x0", origin_type="continuation", origin_id="c0", representation="KV")
        core.create_continuation("c1", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_state(
            "x", origin_type="continuation", origin_id="c1", representation="KV", derived_from=("x0",)
        )
        core.set_state_validity("x0", StateValidity.INVALID)
        core.add_replica("rho-candidate", "x", "w1")
        core.create_continuation("c2", "s", ("c1",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "c2"))

    if case_id in {
        "S2D-DERIVED-SUPERSEDED-DEPENDENCY",
        "S2D-DERIVED-MIXED-DEPENDENCY",
    }:
        if case_id == "S2D-DERIVED-MIXED-DEPENDENCY":
            core.create_state("xgood", origin_type="continuation", origin_id="c0", representation="KV")
        core.create_continuation("c1", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        core.create_request("rp", "c1")
        core.start_attempt("a1", "rp")
        core.create_state("xbad", origin_type="attempt", origin_id="a1", representation="KV")
        dependencies = (
            ("xgood", "xbad")
            if case_id == "S2D-DERIVED-MIXED-DEPENDENCY"
            else ("xbad",)
        )
        core.create_state(
            "x", origin_type="continuation", origin_id="c1", representation="KV", derived_from=dependencies
        )
        core.add_replica("rho-candidate", "x", "w1")
        core.start_attempt("a2", "rp")
        _commit_attempt(core, "rp", "a2")
        core.create_continuation("c2", "s", ("c1",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "c2"))

    if case_id == "S2E-TOP-LEVEL-INVALID":
        core.create_state("x", origin_type="continuation", origin_id="c0", representation="KV")
        core.set_state_validity("x", StateValidity.INVALID)
        core.add_replica("rho-candidate", "x", "w1")
        core.create_continuation("c1", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "c1"))

    if case_id == "S2E-VALID-CONTROL":
        core.create_state("x", origin_type="continuation", origin_id="c0", representation="KV")
        core.add_replica("rho-candidate", "x", "w1")
        core.create_continuation("c1", "s", ("c0",), ContinuationLifecycle.ACTIVE)
        return _runtime(manifest, core, "x", _consumer(core, "c1"))

    raise AssertionError(f"unhandled C4.3b case: {case_id}")


def _ancestors(core: ContinuityCore, continuation_id: str) -> set[str]:
    if continuation_id not in core.continuations:
        return set()
    result = {continuation_id}
    todo = list(core.continuations[continuation_id].parent_ids)
    while todo:
        current = todo.pop()
        if current in result:
            continue
        result.add(current)
        node = core.continuations.get(current)
        if node is None:
            return set()
        todo.extend(node.parent_ids)
    return result


def _context_consistent(core: ContinuityCore, ctx: ExecutionContext) -> bool:
    program = core.programs.get(ctx.program_id)
    session = core.sessions.get(ctx.session_id)
    continuation = core.continuations.get(ctx.continuation_id)
    request = core.requests.get(ctx.request_id)
    attempt = core.attempts.get(ctx.attempt_id)
    if any(item is None for item in (program, session, continuation, request, attempt)):
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
    if ctx.phase_id is not None:
        phase = core.phases.get(ctx.phase_id)
        if phase is None or phase.attempt_id != attempt.id:
            return False
        if phase.status not in {PhaseStatus.RUNNING, PhaseStatus.COMPLETED}:
            return False
    return True


def _independent_compatible(
    core: ContinuityCore,
    state_id: str,
    ctx: ExecutionContext,
    visiting: set[str] | None = None,
) -> bool:
    if not _context_consistent(core, ctx):
        return False
    visiting = set() if visiting is None else visiting
    state = core.states.get(state_id)
    if state is None or state.validity is not StateValidity.VALID:
        return False
    if state_id in visiting:
        return False
    visiting.add(state_id)
    try:
        origin = core.continuations.get(state.origin_continuation_id)
        consumer = core.continuations.get(ctx.continuation_id)
        if origin is None or consumer is None or origin.session_id != consumer.session_id:
            return False
        if state.origin_continuation_id not in _ancestors(core, ctx.continuation_id):
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
            producer_phase = core.phases.get(state.producer_phase_id)
            if producer_phase is None or producer_phase.status is not PhaseStatus.COMPLETED:
                return False
            if state.producer_attempt_id == ctx.attempt_id:
                if ctx.phase_id is None:
                    return False
                consumer_phase = core.phases.get(ctx.phase_id)
                if consumer_phase is None or consumer_phase.attempt_id != ctx.attempt_id:
                    return False
                if consumer_phase.ordinal <= producer_phase.ordinal:
                    return False

        for dependency_id in state.derived_from:
            if not _independent_compatible(core, dependency_id, ctx, visiting):
                return False
        return True
    finally:
        visiting.remove(state_id)


def _ground_truth(
    runtime: _ScenarioRuntime,
    manifest: StateLineageAdversarialManifest,
    exact_compatible: bool,
    b4_effective_compatible: bool,
) -> dict[str, Any]:
    core = runtime.core
    state = core.states[runtime.candidate_state_id]
    replica = core.replicas[runtime.candidate_replica_id] if runtime.candidate_replica_id else None
    producer_attempt = (
        core.attempts[state.producer_attempt_id]
        if state.producer_attempt_id is not None
        else None
    )
    producer_phase = (
        core.phases[state.producer_phase_id]
        if state.producer_phase_id is not None
        else None
    )
    dependencies = tuple(
        {
            "state_id": dependency_id,
            "validity": core.states[dependency_id].validity.name,
            "origin_continuation_id": core.states[dependency_id].origin_continuation_id,
            "producer_attempt_id": core.states[dependency_id].producer_attempt_id,
            "producer_attempt_authority": (
                None
                if core.states[dependency_id].producer_attempt_id is None
                else core.attempts[core.states[dependency_id].producer_attempt_id].authority_status.name
            ),
        }
        for dependency_id in sorted(state.derived_from)
    )
    return {
        "corpus_schema": S2_ADVERSARIAL_SCHEMA,
        "manifest": manifest.to_dict(),
        "candidate_key": runtime.observation.state_candidate_key,
        "candidate_state_id": state.id,
        "candidate_state_validity": state.validity.name,
        "candidate_semantic_type": state.semantic_type,
        "candidate_representation": state.representation,
        "candidate_origin_continuation_id": state.origin_continuation_id,
        "candidate_origin_request_id": state.origin_request_id,
        "candidate_producer_attempt_id": state.producer_attempt_id,
        "candidate_producer_attempt_authority": (
            None if producer_attempt is None else producer_attempt.authority_status.name
        ),
        "candidate_producer_phase_id": state.producer_phase_id,
        "candidate_producer_phase_ordinal": (
            None if producer_phase is None else producer_phase.ordinal
        ),
        "candidate_dependencies": dependencies,
        "candidate_replica_id": None if replica is None else replica.id,
        "candidate_replica_status": None if replica is None else replica.status.name,
        "candidate_physical_location": None if replica is None else replica.location_id,
        "consumer_context": {
            "program_id": runtime.consumer_context.program_id,
            "session_id": runtime.consumer_context.session_id,
            "continuation_id": runtime.consumer_context.continuation_id,
            "request_id": runtime.consumer_context.request_id,
            "attempt_id": runtime.consumer_context.attempt_id,
            "phase_id": runtime.consumer_context.phase_id,
        },
        "independent_oracle_compatible": exact_compatible,
        "c1_exact_context_compatible": exact_compatible,
        "b4_effective_compatible_without_phase": b4_effective_compatible,
        "safe_context_conservatism": exact_compatible and not b4_effective_compatible,
        "wrong_branch_reuse_opportunity_event_id": manifest.wbrr_event_id,
        "application_profile": runtime.application_profile.to_dict(),
    }


def run_s2_adversarial_trial(
    policy_id: PolicyID,
    manifest: StateLineageAdversarialManifest,
    *,
    injected_consumption_event: StateConsumptionEvent | None = None,
) -> StateLineageAdversarialTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    if not isinstance(manifest, StateLineageAdversarialManifest):
        raise TypeError("manifest must be StateLineageAdversarialManifest")

    runtime = _build_runtime(manifest)
    core = runtime.core
    independent = _independent_compatible(
        core, runtime.candidate_state_id, runtime.consumer_context
    )
    c1_exact = core.state_compatible(runtime.candidate_state_id, runtime.consumer_context)
    if independent != c1_exact:
        raise AssertionError("C1 exact-context compatibility diverges from C4.3b oracle")
    if independent != manifest.expected_exact_compatible:
        raise AssertionError("manifest exact-compatibility expectation is wrong")

    authority = CoreContinuityAuthority(core)
    b4_effective = authority.state_compatible(
        runtime.candidate_state_id,
        program_id=runtime.consumer_context.program_id,
        session_id=runtime.consumer_context.session_id,
        continuation_id=runtime.consumer_context.continuation_id,
        request_id=runtime.consumer_context.request_id,
        attempt_id=runtime.consumer_context.attempt_id,
    )
    policy = build_baseline_policies(authority)[policy_id]
    decision = decide_placement(policy, runtime.observation)

    ground_truth = _ground_truth(runtime, manifest, independent, b4_effective)
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

    incompatible_consumption = consumption_event is not None and not independent
    wrong_branch_reuse = manifest.wbrr_event_id is not None and incompatible_consumption

    opportunities: list[CorrectnessMetric] = []
    opportunity_event_ids: list[str] = []
    opportunity_scopes: list[MetricOpportunityScope] = []
    violations: list[CorrectnessMetric] = []
    violation_event_ids: list[str] = []

    if manifest.wbrr_event_id is not None:
        opportunities.append(CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
        opportunity_event_ids.append(manifest.wbrr_event_id)
        opportunity_scopes.append(MetricOpportunityScope.EXOGENOUS_PAIRED)
        if wrong_branch_reuse:
            violations.append(CorrectnessMetric.WRONG_BRANCH_REUSE_RATE)
            violation_event_ids.append(manifest.wbrr_event_id)

    if consumption_event is not None and manifest.fault_id is not None:
        opportunities.append(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
        opportunity_event_ids.append(consumption_event.event_id)
        opportunity_scopes.append(MetricOpportunityScope.POLICY_DERIVED)
        if incompatible_consumption:
            violations.append(CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE)
            violation_event_ids.append(consumption_event.event_id)

    terminal_event = _execute_terminal_outcome(runtime, consumption_event)
    semantic_result = SemanticResult(
        reported_success=terminal_event.reported_success,
        authoritative_commit=terminal_event.authoritative_commit,
        semantically_correct=terminal_event.semantically_correct,
        recovery_actions=(
            (RecoveryAction.RECOMPUTE,) if terminal_event.used_recompute else ()
        ),
    )

    observed_evidence = {
        "c1_exact_context_compatible": c1_exact,
        "b4_effective_compatible_without_phase": b4_effective,
        "selected_worker_id": decision.worker_id,
        "consumption_event": (
            None if consumption_event is None else consumption_event.to_dict()
        ),
        "terminal_event": terminal_event.to_dict(),
    }
    policy_decision = {
        "placement": _placement_to_dict(decision),
        "frozen_c3_phase_id_visible": False,
        "c1_exact_context_phase_id": runtime.consumer_context.phase_id,
        "state_consumption_is_execution_event_not_policy_decision": True,
        "application_profile_is_not_policy_visible": True,
        "terminal_oracle_is_not_policy_visible": True,
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S2_ADVERSARIAL_COHORT_ID,
        trial_id=manifest.case_id,
        operation_id=runtime.consumer_context.request_id,
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
        fault_id=manifest.fault_id,
        fault_class=manifest.fault_class,
    )

    return StateLineageAdversarialTrial(
        policy_id=policy_id,
        manifest=manifest,
        evaluation=evaluation,
        placement_decision=decision,
        candidate_state_id=runtime.candidate_state_id,
        consumption_event=consumption_event,
        terminal_event=terminal_event,
        independent_oracle_compatible=independent,
        c1_exact_context_compatible=c1_exact,
        b4_effective_compatible=b4_effective,
        safe_conservative_b4=(
            policy_id is PolicyID.B4 and c1_exact and not b4_effective
        ),
    )


def run_s2_adversarial_case(
    policy_id: PolicyID,
    case_id: str,
) -> StateLineageAdversarialTrial:
    try:
        manifest = _MANIFEST_BY_ID[case_id]
    except KeyError as exc:
        raise ValueError(f"unknown C4.3b case_id: {case_id}") from exc
    return run_s2_adversarial_trial(policy_id, manifest)


def run_s2_adversarial_paired() -> StateLineageAdversarialEvaluation:
    trials = tuple(
        run_s2_adversarial_trial(policy_id, manifest)
        for manifest in S2_ADVERSARIAL_MANIFESTS
        for policy_id in PolicyID
    )
    summary = summarize_correctness(tuple(trial.evaluation for trial in trials))
    return StateLineageAdversarialEvaluation(trials=trials, summary=summary)
