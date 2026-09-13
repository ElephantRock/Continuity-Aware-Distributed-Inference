from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from functools import lru_cache
import hashlib
import json
import math
from typing import Any, Mapping

from continuity.core import ContinuityCore
from continuity.entities import (
    BindingStatus,
    ContinuationLifecycle,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ReconcileOutcome,
)
from continuity.errors import ContinuityError
from continuity.invariants import InvariantOracle
from experiments.c7_protocol import (
    C6_ARTIFACT_SHA256,
    C6_SCIENTIFIC_FINGERPRINT,
    C7_PROTOCOL_FINGERPRINT,
    C7ExperimentManifest,
    ExperimentSeries,
    ParameterSource,
    WorkloadClass,
)
from experiments.c74_migration_protocol import (
    C74_CANONICAL_DETERMINISTIC_SEED,
    C74_C44_BINDING_SAFETY_MERGE,
    C74_COMPARATIVE_RESULT_INSPECTION,
    C74_PARTIAL_UNRANKED_LABEL,
    C74_PROTOCOL_FINGERPRINT,
    C74_SEMANTIC_INVALID_LABEL,
    C74_TIMING_EVIDENCE,
    C74CrossoverClass,
    C74CrossoverManifest,
    C74FailoverManifest,
    C74RecoveryAction,
    C74ScenarioID,
    C74ScenarioRanking,
    crossover_class,
    p5_state_bytes,
    recomputation_ratio_components,
    scenario_spec,
    validate_c74_base_manifest,
)
from simulator.continuity_policy import (
    CoreContinuityAuthority,
    MigrationDisposition,
    build_baseline_policies,
)
from simulator.inference_cost_runtime import (
    C64F_ARTIFACT_SHA256,
    C64F_EVIDENCE_CLASS,
    C64F_SCIENTIFIC_FINGERPRINT,
    ValidatedRuntimeCostProfile,
    load_c64f_runtime_profiles,
)
from simulator.policies import (
    InformationField,
    PolicyID,
    PolicyObservation,
    PolicyView,
    project_observation,
)


C74B_ENGINE_SCHEMA = "cadi.c7.4b.migration-efficiency-engine.v1"
C74B_CROSSOVER_RESULT_SCHEMA = "cadi.c7.4b.p5-crossover-cell.v1"
C74B_FAILOVER_RESULT_SCHEMA = "cadi.c7.4b.failover-cell-result.v1"
C74B_PROGRAM_CASE_SCHEMA = "cadi.c7.4b.failover-program-case.v1"
C74B_PHYSICAL_REALIZATION_SCHEMA = "cadi.c7.4b.failover-physical-realization.v1"
C74B_BASE_COMMIT = "de4046fc4bcbf343d372cf8a151cdbb96fc6b45b"
C74B_FROZEN_C74A_FINGERPRINT = (
    "21601257767442204f1acfae4c6e2e42959e37701c9236a2d2b560a2dda486b6"
)
C74B_COMPARATIVE_RESULT_INSPECTION = "NONE"
C74B_SUBJECT_ID = "state:x"
C74B_STATE_ID = "x"
C74B_ELIGIBLE = "ELIGIBLE_FOR_EFFICIENCY_RANKING"
C74B_RECONCILIATION_MISMATCH = "UNRANKED_RECONCILIATION_MISMATCH"


if C74_PROTOCOL_FINGERPRINT != C74B_FROZEN_C74A_FINGERPRINT:
    raise RuntimeError("C7.4b requires the exact merged C7.4a protocol fingerprint")
if C7_PROTOCOL_FINGERPRINT != "706e0d5fff362a1eda8c906b957c914251c6e7949bac6be3c2c143ae21625474":
    raise RuntimeError("C7.4b C7.1 protocol drift")
if C6_SCIENTIFIC_FINGERPRINT != C64F_SCIENTIFIC_FINGERPRINT:
    raise RuntimeError("C7.4b C6 scientific fingerprint drift")
if C6_ARTIFACT_SHA256 != C64F_ARTIFACT_SHA256:
    raise RuntimeError("C7.4b C6 artifact drift")
if C64F_EVIDENCE_CLASS != C74_TIMING_EVIDENCE:
    raise RuntimeError("C7.4b timing evidence-class drift")
if C74_COMPARATIVE_RESULT_INSPECTION != "NONE":
    raise RuntimeError("C7.4b must remain pre-result")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fp(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _canonicalize(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key.value if isinstance(key, Enum) else key): _canonicalize(item)
            for key, item in sorted(
                value.items(),
                key=lambda pair: str(pair[0].value if isinstance(pair[0], Enum) else pair[0]),
            )
        }
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return numeric


@lru_cache(maxsize=1)
def _runtime_profiles() -> Mapping[str, ValidatedRuntimeCostProfile]:
    profiles = load_c64f_runtime_profiles()
    if set(profiles) != {"a100-80gb", "h100-80gb"}:
        raise RuntimeError("C7.4b requires exactly the accepted C6 hardware strata")
    return profiles


@dataclass(frozen=True, slots=True)
class C74BCrossoverPoint:
    manifest_fingerprint: str
    hardware_id: str
    state_tokens: int
    state_bytes: int
    recompute_tokens: int
    transfer_seconds: float
    recompute_seconds: float
    crossover: C74CrossoverClass

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_fingerprint, str) or len(self.manifest_fingerprint) != 64:
            raise ValueError("manifest_fingerprint must be a SHA-256 hex digest")
        if self.hardware_id not in {"a100-80gb", "h100-80gb"}:
            raise ValueError("hardware_id is outside the accepted C6 family")
        if self.state_bytes != p5_state_bytes(self.state_tokens):
            raise ValueError("state_bytes must match the frozen P5 State mapping")
        transfer = _finite_nonnegative(self.transfer_seconds, "transfer_seconds")
        recompute = _finite_nonnegative(self.recompute_seconds, "recompute_seconds")
        if crossover_class(transfer_seconds=transfer, recompute_seconds=recompute) is not self.crossover:
            raise ValueError("crossover classification does not match exact timings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C74B_CROSSOVER_RESULT_SCHEMA,
            "manifest_fingerprint": self.manifest_fingerprint,
            "hardware_id": self.hardware_id,
            "state_tokens": self.state_tokens,
            "state_bytes": self.state_bytes,
            "recompute_tokens": self.recompute_tokens,
            "transfer_seconds": self.transfer_seconds,
            "recompute_seconds": self.recompute_seconds,
            "crossover": self.crossover.value,
            "timing_evidence": C74_TIMING_EVIDENCE,
            "c6_scientific_fingerprint": C64F_SCIENTIFIC_FINGERPRINT,
            "c6_artifact_sha256": C64F_ARTIFACT_SHA256,
        }

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


@dataclass(frozen=True, slots=True)
class C74BScenarioRealization:
    scenario_id: C74ScenarioID
    ranking: C74ScenarioRanking
    exact_state_physically_available_at_decision: bool
    b3_exact_state_location_visible: bool
    expected_b4_reconciliation: str
    transfer_authoritatively_committable_at_decision: bool
    destination_failure_after_service_before_commit: bool
    physical_source_location: str | None
    destination_location: str

    def __post_init__(self) -> None:
        spec = scenario_spec(self.scenario_id)
        if self.ranking is not spec.ranking:
            raise ValueError("scenario ranking drift")
        if self.exact_state_physically_available_at_decision != spec.exact_state_physically_available_at_decision:
            raise ValueError("physical State availability drift")
        if self.b3_exact_state_location_visible != spec.b3_exact_state_location_visible:
            raise ValueError("B3 State visibility drift")
        if self.expected_b4_reconciliation != spec.b4_reconciliation_at_decision:
            raise ValueError("B4 reconciliation expectation drift")
        if self.transfer_authoritatively_committable_at_decision != spec.transfer_authoritatively_committable_at_decision:
            raise ValueError("transfer committability drift")
        if self.destination_failure_after_service_before_commit != spec.destination_failure_after_service_before_commit:
            raise ValueError("destination-failure ordering drift")
        if self.exact_state_physically_available_at_decision:
            if not isinstance(self.physical_source_location, str) or not self.physical_source_location:
                raise ValueError("available exact State requires a physical source")
        elif self.physical_source_location is not None:
            raise ValueError("unavailable exact State cannot name a physical source")
        if not isinstance(self.destination_location, str) or not self.destination_location:
            raise ValueError("destination_location must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C74B_PHYSICAL_REALIZATION_SCHEMA,
            "scenario_id": self.scenario_id.value,
            "ranking": self.ranking.value,
            "exact_state_physically_available_at_decision": self.exact_state_physically_available_at_decision,
            "b3_exact_state_location_visible": self.b3_exact_state_location_visible,
            "expected_b4_reconciliation": self.expected_b4_reconciliation,
            "transfer_authoritatively_committable_at_decision": self.transfer_authoritatively_committable_at_decision,
            "destination_failure_after_service_before_commit": self.destination_failure_after_service_before_commit,
            "physical_source_location": self.physical_source_location,
            "destination_location": self.destination_location,
            "source_availability_is_common_scenario_fact": True,
            "selected_transfer_uses_one_predeclared_source": True,
            "destination_capacity_available_for_selected_state": True,
            "capacity_pressure_or_eviction_faults": False,
        }

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


@dataclass(frozen=True, slots=True)
class C74BFailoverResult:
    base_c7_manifest_fingerprint: str
    failover_manifest_fingerprint: str
    scenario_id: C74ScenarioID
    policy_id: PolicyID
    hardware_id: str
    state_tokens: int
    state_bytes: int
    recompute_tokens: int
    cost_point_fingerprint: str
    program_case_fingerprint: str
    physical_realization_fingerprint: str
    policy_view_fingerprint: str
    pre_authority_fingerprint: str
    post_authority_fingerprint: str
    observed_reconciliation: str
    expected_reconciliation: str
    action: C74RecoveryAction
    action_reason: str
    commit_attempted: bool
    commit_succeeded: bool
    commit_error_type: str | None
    fallback_recompute: bool
    transfer_seconds_charged: float
    recompute_seconds_charged: float
    recovery_time_seconds: float
    state_transfer_volume_bytes: int
    recomputation_numerator_tokens: int
    recomputation_denominator_tokens: int
    redundant_transfer_bytes: int
    redundant_recompute_tokens: int
    semantic_violation_count: int
    invariant_error_type: str | None
    efficiency_status: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.base_c7_manifest_fingerprint, "base_c7_manifest_fingerprint"),
            (self.failover_manifest_fingerprint, "failover_manifest_fingerprint"),
            (self.cost_point_fingerprint, "cost_point_fingerprint"),
            (self.program_case_fingerprint, "program_case_fingerprint"),
            (self.physical_realization_fingerprint, "physical_realization_fingerprint"),
            (self.policy_view_fingerprint, "policy_view_fingerprint"),
            (self.pre_authority_fingerprint, "pre_authority_fingerprint"),
            (self.post_authority_fingerprint, "post_authority_fingerprint"),
        ):
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"{name} must be a SHA-256 hex digest")
        if not isinstance(self.scenario_id, C74ScenarioID):
            raise TypeError("scenario_id must be C74ScenarioID")
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.state_bytes != p5_state_bytes(self.state_tokens):
            raise ValueError("state_bytes drift")
        if not isinstance(self.action, C74RecoveryAction):
            raise TypeError("action must be C74RecoveryAction")
        if not isinstance(self.action_reason, str) or not self.action_reason:
            raise ValueError("action_reason must be non-empty")
        for value, name in (
            (self.commit_attempted, "commit_attempted"),
            (self.commit_succeeded, "commit_succeeded"),
            (self.fallback_recompute, "fallback_recompute"),
        ):
            if not isinstance(value, bool):
                raise TypeError(f"{name} must be bool")
        transfer = _finite_nonnegative(self.transfer_seconds_charged, "transfer_seconds_charged")
        recompute = _finite_nonnegative(self.recompute_seconds_charged, "recompute_seconds_charged")
        recovery = _finite_nonnegative(self.recovery_time_seconds, "recovery_time_seconds")
        if not math.isclose(recovery, transfer + recompute, rel_tol=0.0, abs_tol=0.0):
            raise ValueError("Recovery Time must equal charged transfer + recompute service")
        for value, name in (
            (self.state_transfer_volume_bytes, "state_transfer_volume_bytes"),
            (self.recomputation_numerator_tokens, "recomputation_numerator_tokens"),
            (self.recomputation_denominator_tokens, "recomputation_denominator_tokens"),
            (self.redundant_transfer_bytes, "redundant_transfer_bytes"),
            (self.redundant_recompute_tokens, "redundant_recompute_tokens"),
            (self.semantic_violation_count, "semantic_violation_count"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.recomputation_denominator_tokens != self.recompute_tokens:
            raise ValueError("recomputation denominator must equal frozen recompute_tokens")
        if self.recomputation_numerator_tokens not in {0, self.recompute_tokens}:
            raise ValueError("recomputation numerator must use the frozen binary opportunity")
        if self.commit_succeeded and not self.commit_attempted:
            raise ValueError("successful commit requires a commit attempt")
        if self.fallback_recompute and self.action is not C74RecoveryAction.TRANSFER:
            raise ValueError("fallback recompute requires an attempted transfer")
        if not isinstance(self.efficiency_status, str) or not self.efficiency_status:
            raise ValueError("efficiency_status must be non-empty")

    @property
    def efficiency_eligible(self) -> bool:
        return self.efficiency_status == C74B_ELIGIBLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C74B_FAILOVER_RESULT_SCHEMA,
            "base_c7_manifest_fingerprint": self.base_c7_manifest_fingerprint,
            "failover_manifest_fingerprint": self.failover_manifest_fingerprint,
            "scenario_id": self.scenario_id.value,
            "policy_id": self.policy_id.value,
            "hardware_id": self.hardware_id,
            "state_tokens": self.state_tokens,
            "state_bytes": self.state_bytes,
            "recompute_tokens": self.recompute_tokens,
            "cost_point_fingerprint": self.cost_point_fingerprint,
            "program_case_fingerprint": self.program_case_fingerprint,
            "physical_realization_fingerprint": self.physical_realization_fingerprint,
            "policy_view_fingerprint": self.policy_view_fingerprint,
            "pre_authority_fingerprint": self.pre_authority_fingerprint,
            "post_authority_fingerprint": self.post_authority_fingerprint,
            "observed_reconciliation": self.observed_reconciliation,
            "expected_reconciliation": self.expected_reconciliation,
            "action": self.action.value,
            "action_reason": self.action_reason,
            "commit_attempted": self.commit_attempted,
            "commit_succeeded": self.commit_succeeded,
            "commit_error_type": self.commit_error_type,
            "fallback_recompute": self.fallback_recompute,
            "transfer_seconds_charged": self.transfer_seconds_charged,
            "recompute_seconds_charged": self.recompute_seconds_charged,
            "recovery_time_seconds": self.recovery_time_seconds,
            "state_transfer_volume_bytes": self.state_transfer_volume_bytes,
            "recomputation_numerator_tokens": self.recomputation_numerator_tokens,
            "recomputation_denominator_tokens": self.recomputation_denominator_tokens,
            "redundant_transfer_bytes": self.redundant_transfer_bytes,
            "redundant_recompute_tokens": self.redundant_recompute_tokens,
            "semantic_violation_count": self.semantic_violation_count,
            "invariant_error_type": self.invariant_error_type,
            "efficiency_status": self.efficiency_status,
            "efficiency_eligible": self.efficiency_eligible,
            "timing_evidence": C74_TIMING_EVIDENCE,
            "direct_hardware_measurement_claim": False,
        }

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


@dataclass(slots=True)
class _ScenarioRuntime:
    core: ContinuityCore
    candidate_binding_id: str
    expected_current_binding_id_before_action: str
    expected_current_epoch_before_action: int
    realization: C74BScenarioRealization


def evaluate_crossover_cell(manifest: C74CrossoverManifest) -> C74BCrossoverPoint:
    if not isinstance(manifest, C74CrossoverManifest):
        raise TypeError("manifest must be C74CrossoverManifest")
    profile = _runtime_profiles()[manifest.hardware_id]
    state_bytes = p5_state_bytes(manifest.state_tokens)
    transfer_seconds = profile.transfer_seconds(state_bytes)
    recompute_seconds = profile.prefill_seconds(manifest.recompute_tokens)
    crossover = crossover_class(
        transfer_seconds=transfer_seconds,
        recompute_seconds=recompute_seconds,
    )
    return C74BCrossoverPoint(
        manifest_fingerprint=manifest.fingerprint,
        hardware_id=manifest.hardware_id,
        state_tokens=manifest.state_tokens,
        state_bytes=state_bytes,
        recompute_tokens=manifest.recompute_tokens,
        transfer_seconds=transfer_seconds,
        recompute_seconds=recompute_seconds,
        crossover=crossover,
    )


def build_crossover_manifest(
    *,
    execution_git_commit: str,
    hardware_id: str,
    state_tokens: int,
    recompute_tokens: int,
) -> C74CrossoverManifest:
    return C74CrossoverManifest(
        execution_git_commit=execution_git_commit,
        protocol_fingerprint=C74_PROTOCOL_FINGERPRINT,
        hardware_id=hardware_id,
        state_tokens=state_tokens,
        recompute_tokens=recompute_tokens,
        seed=C74_CANONICAL_DETERMINISTIC_SEED,
    )


def _scenario_realization(scenario_id: C74ScenarioID) -> C74BScenarioRealization:
    spec = scenario_spec(scenario_id)
    source_location = {
        C74ScenarioID.MATCHED_PLANNED_MIGRATION: "w1",
        C74ScenarioID.SOURCE_FAILURE_VALID_REMOTE: "w3",
        C74ScenarioID.DESTINATION_FAILURE_AFTER_TRANSFER_BEFORE_COMMIT: "w1",
        C74ScenarioID.STALE_BINDING_RESIDUAL: "w3",
        C74ScenarioID.CONCURRENT_CANDIDATE_LOSER: "w4",
        C74ScenarioID.PARTIAL_MATERIALIZATION_NO_COMMIT: None,
    }[scenario_id]
    destination_location = {
        C74ScenarioID.MATCHED_PLANNED_MIGRATION: "w2",
        C74ScenarioID.SOURCE_FAILURE_VALID_REMOTE: "w2",
        C74ScenarioID.DESTINATION_FAILURE_AFTER_TRANSFER_BEFORE_COMMIT: "w2",
        C74ScenarioID.STALE_BINDING_RESIDUAL: "w2",
        C74ScenarioID.CONCURRENT_CANDIDATE_LOSER: "w3",
        C74ScenarioID.PARTIAL_MATERIALIZATION_NO_COMMIT: "w2",
    }[scenario_id]
    return C74BScenarioRealization(
        scenario_id=scenario_id,
        ranking=spec.ranking,
        exact_state_physically_available_at_decision=spec.exact_state_physically_available_at_decision,
        b3_exact_state_location_visible=spec.b3_exact_state_location_visible,
        expected_b4_reconciliation=spec.b4_reconciliation_at_decision,
        transfer_authoritatively_committable_at_decision=spec.transfer_authoritatively_committable_at_decision,
        destination_failure_after_service_before_commit=spec.destination_failure_after_service_before_commit,
        physical_source_location=source_location,
        destination_location=destination_location,
    )


def _scaffold_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s", lifecycle=ContinuationLifecycle.ACTIVE)
    core.create_state(C74B_STATE_ID, origin_type="continuation", origin_id="c")
    core.activate_initial_binding("b1", C74B_SUBJECT_ID, "w1")
    return core


def _record_commit_evidence(core: ContinuityCore, binding_id: str, *, evidence_id: str, now: float) -> str:
    binding = core.bindings[binding_id]
    core.record_evidence(
        Evidence(
            id=evidence_id,
            claim="C7.4b migration commit readiness",
            source="C7.4b deterministic migration efficiency engine",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=now,
            scope=frozenset({("binding", binding.id), ("epoch", str(binding.epoch))}),
        )
    )
    return evidence_id


def _commit_setup_winner(core: ContinuityCore, binding_id: str, *, evidence_id: str, now: float) -> None:
    _record_commit_evidence(core, binding_id, evidence_id=evidence_id, now=now)
    core.commit_migration(binding_id, (evidence_id,), now=now)
    InvariantOracle(core).assert_all()


def _build_scenario_runtime(scenario_id: C74ScenarioID) -> _ScenarioRuntime:
    realization = _scenario_realization(scenario_id)
    core = _scaffold_core()

    if scenario_id in {
        C74ScenarioID.MATCHED_PLANNED_MIGRATION,
        C74ScenarioID.SOURCE_FAILURE_VALID_REMOTE,
        C74ScenarioID.DESTINATION_FAILURE_AFTER_TRANSFER_BEFORE_COMMIT,
        C74ScenarioID.PARTIAL_MATERIALIZATION_NO_COMMIT,
    }:
        candidate = core.propose_binding("b2", C74B_SUBJECT_ID, realization.destination_location)
        core.begin_migration(candidate.id)
    elif scenario_id is C74ScenarioID.STALE_BINDING_RESIDUAL:
        candidate = core.propose_binding("b2", C74B_SUBJECT_ID, realization.destination_location)
        core.begin_migration(candidate.id)
        winner = core.propose_binding("b3", C74B_SUBJECT_ID, "w3")
        core.begin_migration(winner.id)
        _commit_setup_winner(core, winner.id, evidence_id="e:setup:b3", now=1.0)
    elif scenario_id is C74ScenarioID.CONCURRENT_CANDIDATE_LOSER:
        winner = core.propose_binding("b2", C74B_SUBJECT_ID, "w2")
        core.begin_migration(winner.id)
        candidate = core.propose_binding("b3", C74B_SUBJECT_ID, realization.destination_location)
        core.begin_migration(candidate.id)
        _commit_setup_winner(core, winner.id, evidence_id="e:setup:b2", now=1.0)
    else:
        raise AssertionError("unhandled C7.4b scenario")

    current_id = core.current_binding_by_subject[C74B_SUBJECT_ID]
    current_epoch = core.current_epoch_by_subject[C74B_SUBJECT_ID]
    InvariantOracle(core).assert_all()
    return _ScenarioRuntime(
        core=core,
        candidate_binding_id=candidate.id,
        expected_current_binding_id_before_action=current_id,
        expected_current_epoch_before_action=current_epoch,
        realization=realization,
    )


def _authority_snapshot(core: ContinuityCore) -> dict[str, Any]:
    current_id = core.current_binding_by_subject[C74B_SUBJECT_ID]
    return {
        "subject_id": C74B_SUBJECT_ID,
        "current_binding_id": current_id,
        "current_epoch": core.current_epoch_by_subject[C74B_SUBJECT_ID],
        "bindings": [
            {
                "binding_id": item.id,
                "location_id": item.location_id,
                "base_epoch": item.base_epoch,
                "epoch": item.epoch,
                "status": item.status.name,
            }
            for item in sorted(core.bindings.values(), key=lambda candidate: (candidate.epoch, candidate.id))
            if item.subject_id == C74B_SUBJECT_ID
        ],
    }


def _policy_observation(
    runtime: _ScenarioRuntime,
    *,
    observed_reconciliation: str,
) -> PolicyObservation:
    realization = runtime.realization
    candidate = runtime.core.bindings[runtime.candidate_binding_id]
    locations = (
        (realization.physical_source_location,)
        if realization.exact_state_physically_available_at_decision
        and realization.physical_source_location is not None
        else ()
    )
    return PolicyObservation(
        request_id=f"c74b:{realization.scenario_id.value}",
        workers=(),
        program_id="p",
        session_id="s",
        continuation_id="c",
        exact_state_id=(C74B_STATE_ID if realization.exact_state_physically_available_at_decision else None),
        state_locations=locations,
        state_provenance=(("origin_continuation_id", "c"),),
        binding_id=candidate.id,
        binding_epoch=candidate.epoch,
        evidence_authority=EvidenceAuthority.EXACT_OBSERVATION.name,
        evidence_status=EvidenceStatus.VALID.name,
        evidence_freshness=0.0,
        reconciliation=observed_reconciliation,
    )


def _policy_view_fingerprint(view: PolicyView) -> str:
    return _fp(
        {
            "policy_id": view.policy_id.value,
            "values": [
                [field.value, _canonicalize(value)]
                for field, value in view.values
            ],
        }
    )


def _program_case_fingerprint(
    *,
    scenario_id: C74ScenarioID,
    hardware_id: str,
    state_tokens: int,
    recompute_tokens: int,
) -> str:
    return _fp(
        {
            "schema": C74B_PROGRAM_CASE_SCHEMA,
            "c74a_protocol_fingerprint": C74_PROTOCOL_FINGERPRINT,
            "scenario_id": scenario_id.value,
            "hardware_id": hardware_id,
            "state_tokens": state_tokens,
            "state_bytes": p5_state_bytes(state_tokens),
            "recompute_tokens": recompute_tokens,
        }
    )


def _validate_reconciliation(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("observed_reconciliation must be non-empty")
    allowed = {item.name for item in ReconcileOutcome}
    if value not in allowed:
        raise ValueError("observed_reconciliation must be a canonical ReconcileOutcome name")
    return value


def build_failover_manifests(
    *,
    execution_git_commit: str,
    scenario_id: C74ScenarioID,
    policy_id: PolicyID,
    hardware_id: str,
    state_tokens: int,
    recompute_tokens: int,
    observed_reconciliation: str | None = None,
) -> tuple[C7ExperimentManifest, C74FailoverManifest]:
    if not isinstance(scenario_id, C74ScenarioID):
        raise TypeError("scenario_id must be C74ScenarioID")
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    runtime = _build_scenario_runtime(scenario_id)
    reconciliation = _validate_reconciliation(
        runtime.realization.expected_b4_reconciliation
        if observed_reconciliation is None
        else observed_reconciliation
    )
    observation = _policy_observation(runtime, observed_reconciliation=reconciliation)
    view = project_observation(observation, policy_id)
    program_case_fp = _program_case_fingerprint(
        scenario_id=scenario_id,
        hardware_id=hardware_id,
        state_tokens=state_tokens,
        recompute_tokens=recompute_tokens,
    )
    base_manifest = C7ExperimentManifest(
        experiment_id=(
            f"c74b:{scenario_id.value}:{policy_id.value}:"
            f"{hardware_id}:{state_tokens}:{recompute_tokens}"
        ),
        git_commit=execution_git_commit,
        protocol_fingerprint=C7_PROTOCOL_FINGERPRINT,
        series=ExperimentSeries.P5_MIGRATION_VS_RECOMPUTE,
        policy_id=policy_id,
        workload_class=WorkloadClass.SYNTHETIC_STRESS,
        hardware_id=hardware_id,
        program_objective="C7.4b deterministic migration/failover efficiency mechanics",
        seed=C74_CANONICAL_DETERMINISTIC_SEED,
        source_dataset_fingerprint=None,
        augmentation_fingerprint=None,
        parameters=(
            ("recompute_tokens", recompute_tokens),
            ("state_tokens", state_tokens),
        ),
        parameter_sources=(
            ("recompute_tokens", ParameterSource.P_SRC4),
            ("state_tokens", ParameterSource.P_SRC4),
        ),
    )
    failover_manifest = C74FailoverManifest(
        execution_git_commit=execution_git_commit,
        base_c7_manifest_fingerprint=base_manifest.fingerprint,
        protocol_fingerprint=C74_PROTOCOL_FINGERPRINT,
        scenario_id=scenario_id,
        policy_id=policy_id,
        hardware_id=hardware_id,
        state_tokens=state_tokens,
        recompute_tokens=recompute_tokens,
        program_case_fingerprint=program_case_fp,
        physical_fault_fingerprint=runtime.realization.fingerprint,
        policy_view_fingerprint=_policy_view_fingerprint(view),
        seed=C74_CANONICAL_DETERMINISTIC_SEED,
    )
    validate_c74_base_manifest(base_manifest, failover_manifest)
    return base_manifest, failover_manifest


def _select_action(
    runtime: _ScenarioRuntime,
    policy_id: PolicyID,
    view: PolicyView,
    cost: C74BCrossoverPoint,
) -> tuple[C74RecoveryAction, str]:
    transfer_cost_eligible = cost.transfer_seconds <= cost.recompute_seconds
    if policy_id in {PolicyID.B0, PolicyID.B1, PolicyID.B2}:
        return C74RecoveryAction.RECOMPUTE, "CONSERVATIVE_BASELINE_RECOMPUTE"

    if policy_id is PolicyID.B3:
        exact_state_id = view.value(InformationField.EXACT_STATE_ID)
        locations = view.value(InformationField.STATE_LOCATION)
        if exact_state_id is None or not locations:
            return C74RecoveryAction.RECOMPUTE, "B3_NO_EXACT_PHYSICAL_STATE"
        if not transfer_cost_eligible:
            return C74RecoveryAction.RECOMPUTE, "B3_RECOMPUTE_FASTER"
        return C74RecoveryAction.TRANSFER, "B3_EXACT_STATE_TRANSFER_COST_ELIGIBLE"

    policies = build_baseline_policies(CoreContinuityAuthority(runtime.core))
    policy = policies[PolicyID.B4]
    decide_migration = getattr(policy, "decide_migration", None)
    if not callable(decide_migration):
        raise AssertionError("B4 must retain decide_migration")
    migration = decide_migration(view)
    if migration.disposition is not MigrationDisposition.ALLOW_COMMIT:
        return C74RecoveryAction.RECOMPUTE, f"B4_{migration.reason}"
    if not runtime.realization.exact_state_physically_available_at_decision:
        return C74RecoveryAction.RECOMPUTE, "B4_NO_EXACT_PHYSICAL_STATE"
    if not transfer_cost_eligible:
        return C74RecoveryAction.RECOMPUTE, "B4_RECOMPUTE_FASTER"
    return C74RecoveryAction.TRANSFER, "B4_CONTINUITY_TRANSFER_COST_ELIGIBLE"


def _realized_transfer_can_authoritatively_succeed(
    runtime: _ScenarioRuntime,
    *,
    observed_reconciliation: str,
) -> bool:
    return (
        runtime.realization.transfer_authoritatively_committable_at_decision
        and observed_reconciliation == ReconcileOutcome.MATCHED.name
        and not runtime.realization.destination_failure_after_service_before_commit
        and runtime.realization.scenario_id
        in {
            C74ScenarioID.MATCHED_PLANNED_MIGRATION,
            C74ScenarioID.SOURCE_FAILURE_VALID_REMOTE,
        }
    )


def _attempt_transfer_commit(
    runtime: _ScenarioRuntime,
    *,
    observed_reconciliation: str,
) -> tuple[bool, str | None]:
    candidate_id = runtime.candidate_binding_id
    evidence_ids: tuple[str, ...] = ()
    if _realized_transfer_can_authoritatively_succeed(
        runtime,
        observed_reconciliation=observed_reconciliation,
    ) or runtime.realization.scenario_id in {
        C74ScenarioID.STALE_BINDING_RESIDUAL,
        C74ScenarioID.CONCURRENT_CANDIDATE_LOSER,
    }:
        evidence_id = _record_commit_evidence(
            runtime.core,
            candidate_id,
            evidence_id=f"e:commit:{runtime.realization.scenario_id.value}",
            now=2.0,
        )
        evidence_ids = (evidence_id,)

    try:
        runtime.core.commit_migration(candidate_id, evidence_ids, now=2.0)
    except ContinuityError as exc:
        return False, type(exc).__name__
    return True, None


def _inject_authority_corruption(runtime: _ScenarioRuntime) -> None:
    candidate_id = runtime.candidate_binding_id
    current_id = runtime.core.current_binding_by_subject[C74B_SUBJECT_ID]
    if current_id != candidate_id:
        current = runtime.core.bindings[current_id]
        runtime.core.bindings[current_id] = replace(current, status=BindingStatus.SUPERSEDED)
    candidate = runtime.core.bindings[candidate_id]
    runtime.core.bindings[candidate_id] = replace(candidate, status=BindingStatus.ACTIVE)
    runtime.core.current_binding_by_subject[C74B_SUBJECT_ID] = candidate_id
    runtime.core.current_epoch_by_subject[C74B_SUBJECT_ID] = candidate.epoch


def run_failover_cell(
    base_manifest: C7ExperimentManifest,
    failover_manifest: C74FailoverManifest,
    *,
    observed_reconciliation: str | None = None,
    inject_authority_corruption: bool = False,
) -> C74BFailoverResult:
    validate_c74_base_manifest(base_manifest, failover_manifest)
    runtime = _build_scenario_runtime(failover_manifest.scenario_id)
    reconciliation = _validate_reconciliation(
        runtime.realization.expected_b4_reconciliation
        if observed_reconciliation is None
        else observed_reconciliation
    )
    observation = _policy_observation(runtime, observed_reconciliation=reconciliation)
    view = project_observation(observation, failover_manifest.policy_id)
    view_fp = _policy_view_fingerprint(view)
    case_fp = _program_case_fingerprint(
        scenario_id=failover_manifest.scenario_id,
        hardware_id=failover_manifest.hardware_id,
        state_tokens=failover_manifest.state_tokens,
        recompute_tokens=failover_manifest.recompute_tokens,
    )
    if case_fp != failover_manifest.program_case_fingerprint:
        raise ValueError("failover manifest Program/case fingerprint drift")
    if runtime.realization.fingerprint != failover_manifest.physical_fault_fingerprint:
        raise ValueError("failover manifest physical realization fingerprint drift")
    if view_fp != failover_manifest.policy_view_fingerprint:
        raise ValueError("failover manifest policy-view fingerprint drift")

    crossover_manifest = build_crossover_manifest(
        execution_git_commit=failover_manifest.execution_git_commit,
        hardware_id=failover_manifest.hardware_id,
        state_tokens=failover_manifest.state_tokens,
        recompute_tokens=failover_manifest.recompute_tokens,
    )
    cost = evaluate_crossover_cell(crossover_manifest)
    pre_snapshot = _authority_snapshot(runtime.core)
    action, action_reason = _select_action(runtime, failover_manifest.policy_id, view, cost)

    commit_attempted = action is C74RecoveryAction.TRANSFER
    commit_succeeded = False
    commit_error_type: str | None = None
    transfer_seconds_charged = 0.0
    recompute_seconds_charged = 0.0
    fallback_recompute = False

    if action is C74RecoveryAction.RECOMPUTE:
        recompute_seconds_charged = cost.recompute_seconds
    else:
        transfer_seconds_charged = cost.transfer_seconds
        commit_succeeded, commit_error_type = _attempt_transfer_commit(
            runtime,
            observed_reconciliation=reconciliation,
        )
        if not commit_succeeded:
            fallback_recompute = True
            recompute_seconds_charged = cost.recompute_seconds

    if inject_authority_corruption:
        _inject_authority_corruption(runtime)

    expected_current_id = runtime.expected_current_binding_id_before_action
    expected_current_epoch = runtime.expected_current_epoch_before_action
    if commit_succeeded:
        candidate = runtime.core.bindings[runtime.candidate_binding_id]
        expected_current_id = candidate.id
        expected_current_epoch = candidate.epoch

    post_snapshot = _authority_snapshot(runtime.core)
    authority_mismatch = (
        post_snapshot["current_binding_id"] != expected_current_id
        or post_snapshot["current_epoch"] != expected_current_epoch
    )
    invariant_error: Exception | None = None
    try:
        InvariantOracle(runtime.core).assert_all()
    except Exception as exc:
        invariant_error = exc
    semantic_violation_count = 1 if authority_mismatch or invariant_error is not None else 0

    transfer_success = action is C74RecoveryAction.TRANSFER and commit_succeeded
    recompute_num, recompute_den = recomputation_ratio_components(
        recompute_tokens=failover_manifest.recompute_tokens,
        transfer_authoritative_success=transfer_success,
    )
    state_bytes = p5_state_bytes(failover_manifest.state_tokens)
    transfer_volume = state_bytes if action is C74RecoveryAction.TRANSFER else 0
    redundant_transfer = (
        state_bytes
        if action is C74RecoveryAction.TRANSFER and not commit_succeeded
        else 0
    )
    authorizable_realized_transfer = (
        runtime.realization.scenario_id
        in {
            C74ScenarioID.MATCHED_PLANNED_MIGRATION,
            C74ScenarioID.SOURCE_FAILURE_VALID_REMOTE,
        }
        and reconciliation == ReconcileOutcome.MATCHED.name
    )
    redundant_recompute = (
        failover_manifest.recompute_tokens
        if action is C74RecoveryAction.RECOMPUTE
        and authorizable_realized_transfer
        and cost.transfer_seconds < cost.recompute_seconds
        else 0
    )

    reconciliation_mismatch = reconciliation != runtime.realization.expected_b4_reconciliation
    if semantic_violation_count:
        efficiency_status = C74_SEMANTIC_INVALID_LABEL
    elif runtime.realization.ranking is not C74ScenarioRanking.RANKABLE:
        efficiency_status = C74_PARTIAL_UNRANKED_LABEL
    elif reconciliation_mismatch:
        efficiency_status = C74B_RECONCILIATION_MISMATCH
    else:
        efficiency_status = C74B_ELIGIBLE

    return C74BFailoverResult(
        base_c7_manifest_fingerprint=base_manifest.fingerprint,
        failover_manifest_fingerprint=failover_manifest.fingerprint,
        scenario_id=failover_manifest.scenario_id,
        policy_id=failover_manifest.policy_id,
        hardware_id=failover_manifest.hardware_id,
        state_tokens=failover_manifest.state_tokens,
        state_bytes=state_bytes,
        recompute_tokens=failover_manifest.recompute_tokens,
        cost_point_fingerprint=cost.fingerprint,
        program_case_fingerprint=case_fp,
        physical_realization_fingerprint=runtime.realization.fingerprint,
        policy_view_fingerprint=view_fp,
        pre_authority_fingerprint=_fp(pre_snapshot),
        post_authority_fingerprint=_fp(post_snapshot),
        observed_reconciliation=reconciliation,
        expected_reconciliation=runtime.realization.expected_b4_reconciliation,
        action=action,
        action_reason=action_reason,
        commit_attempted=commit_attempted,
        commit_succeeded=commit_succeeded,
        commit_error_type=commit_error_type,
        fallback_recompute=fallback_recompute,
        transfer_seconds_charged=transfer_seconds_charged,
        recompute_seconds_charged=recompute_seconds_charged,
        recovery_time_seconds=transfer_seconds_charged + recompute_seconds_charged,
        state_transfer_volume_bytes=transfer_volume,
        recomputation_numerator_tokens=recompute_num,
        recomputation_denominator_tokens=recompute_den,
        redundant_transfer_bytes=redundant_transfer,
        redundant_recompute_tokens=redundant_recompute,
        semantic_violation_count=semantic_violation_count,
        invariant_error_type=None if invariant_error is None else type(invariant_error).__name__,
        efficiency_status=efficiency_status,
    )


def engine_identity() -> dict[str, Any]:
    payload = {
        "schema": C74B_ENGINE_SCHEMA,
        "base_commit": C74B_BASE_COMMIT,
        "c7_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
        "c74a_protocol_fingerprint": C74_PROTOCOL_FINGERPRINT,
        "c6_scientific_fingerprint": C64F_SCIENTIFIC_FINGERPRINT,
        "c6_artifact_sha256": C64F_ARTIFACT_SHA256,
        "c44_binding_safety_merge": C74_C44_BINDING_SAFETY_MERGE,
        "comparative_result_inspection": C74B_COMPARATIVE_RESULT_INSPECTION,
        "full_comparative_executor_present": False,
        "common_c1_commit_authority": True,
        "transfer_cost_rule": "transfer_seconds <= recompute_seconds",
        "transfer_cost_tie_action": C74RecoveryAction.TRANSFER.value,
    }
    return {**payload, "engine_fingerprint": _fp(payload)}


def main() -> None:
    print(_json(engine_identity()))


if __name__ == "__main__":
    main()
