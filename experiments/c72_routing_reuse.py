from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import fmean
import hashlib
import json
import math
from typing import Any, Mapping, Protocol

from continuity.entities import AttemptAuthority
from experiments.c7_protocol import (
    C7ExperimentManifest,
    C7_PROTOCOL_FINGERPRINT,
    EfficiencyEligibility,
    ExperimentSeries,
    WorkloadClass,
    cold_continuation_rate,
    efficiency_eligibility,
    recomputation_ratio,
    state_reuse_ratio,
    state_reuse_token_ratio,
    synthetic_request_c6_admissible,
    source_request_c6_admissible,
)
from experiments.mooncake_trace import (
    MOONCAKE_NORMALIZATION_VERSION,
    MOONCAKE_SOURCE_REVISION,
    MOONCAKE_SOURCE_SHA256,
    load_pinned_mooncake_trace,
)
from experiments.trace_workload import (
    NormalizedTraceDataset,
    NormalizedTraceRecord,
    TraceField,
    TraceSourceManifest,
)
from simulator.continuity_policy import decide_paired_placements
from simulator.inference_cost import InferenceCostWorkload
from simulator.inference_cost_runtime import (
    C64F_ARTIFACT_SHA256,
    C64F_EVIDENCE_CLASS,
    C64F_SCIENTIFIC_FINGERPRINT,
    ValidatedRuntimeCostProfile,
    estimate_validated_runtime_cost,
)
from simulator.policies import PlacementDecision, PolicyID, PolicyObservation


C72_RESULT_SCHEMA = "cadi.c7.2.routing-reuse-result.v1"
C72_OPERATION_SCHEMA = "cadi.c7.2.routing-reuse-operation.v1"
C72_ADMISSIBLE_NORMALIZATION_VERSION = "cadi.c7.2.c6-admissible-trace.v1"
C72_BASE_COMMIT = "990ef4f081b0edebb1e2ca83bddaf252eb39e549"
C5_MOONCAKE_NORMALIZED_FINGERPRINT = (
    "22ead90d97ae218f229f94378f8f019499ede0e4050e6cbf8092b455ae047718"
)
C72_SOURCE_FILTER_STEP = (
    "retain only unmodified records satisfying the frozen C7.1 C6-domain predicate: "
    "input_tokens>=1, output_tokens>=1, input_tokens+output_tokens<=4096"
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _nonnegative_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _finite_nonnegative(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def derive_c7_admissible_trace(source: NormalizedTraceDataset) -> NormalizedTraceDataset:
    """Derive a C6-domain subset without changing any retained record field."""

    if not isinstance(source, NormalizedTraceDataset):
        raise TypeError("source must be NormalizedTraceDataset")
    source.require_fields(
        (
            TraceField.ARRIVAL_TIME_S,
            TraceField.INPUT_TOKENS,
            TraceField.OUTPUT_TOKENS,
            TraceField.PREFIX_GROUP_ID,
            TraceField.PREFIX_TOKENS,
        )
    )

    retained: list[NormalizedTraceRecord] = []
    for record in source.source_order:
        if record.input_tokens is None or record.output_tokens is None:
            raise AssertionError("required token fields unexpectedly missing")
        if source_request_c6_admissible(record.input_tokens, record.output_tokens):
            retained.append(record)
    if not retained:
        raise ValueError("C7.1 C6-domain predicate retained no source records")

    manifest = source.manifest
    derived_manifest = TraceSourceManifest(
        source_id=manifest.source_id,
        source_name=manifest.source_name,
        source_uri=manifest.source_uri,
        source_version=manifest.source_version,
        license_id=manifest.license_id,
        source_sha256=manifest.source_sha256,
        normalization_version=C72_ADMISSIBLE_NORMALIZATION_VERSION,
        normalization_steps=manifest.normalization_steps + (C72_SOURCE_FILTER_STEP,),
        field_origins=manifest.field_origins,
    )
    derived = NormalizedTraceDataset(derived_manifest, tuple(retained))

    source_by_id = {record.record_id: record for record in source.source_order}
    for record in derived.source_order:
        if record.to_dict() != source_by_id[record.record_id].to_dict():
            raise AssertionError("C7 admissibility derivation mutated a retained record")
    return derived


def derive_pinned_mooncake_c7_admissible(
    source: NormalizedTraceDataset,
) -> NormalizedTraceDataset:
    """Evidence-bound derivation from only the exact closed C5 Mooncake dataset."""

    if not isinstance(source, NormalizedTraceDataset):
        raise TypeError("source must be NormalizedTraceDataset")
    manifest = source.manifest
    if manifest.source_id != "mooncake-fast25-conversation":
        raise ValueError("unexpected C7 source_id")
    if manifest.source_version != MOONCAKE_SOURCE_REVISION:
        raise ValueError("unexpected Mooncake source revision")
    if manifest.source_sha256 != MOONCAKE_SOURCE_SHA256:
        raise ValueError("unexpected Mooncake source SHA-256")
    if manifest.normalization_version != MOONCAKE_NORMALIZATION_VERSION:
        raise ValueError("unexpected Mooncake normalization version")
    if source.fingerprint != C5_MOONCAKE_NORMALIZED_FINGERPRINT:
        raise ValueError("Mooncake normalized dataset fingerprint drift")
    return derive_c7_admissible_trace(source)


@dataclass(frozen=True, slots=True)
class C72AdmissibleSourceSummary:
    source_dataset_fingerprint: str
    admissible_dataset_fingerprint: str
    total_records: int
    retained_records: int
    retained_fraction: float
    arrival_min_s: float
    arrival_max_s: float
    input_min: int
    input_max: int
    input_mean: float
    output_min: int
    output_max: int
    output_mean: float
    reusable_records: int
    reusable_fraction: float
    prefix_tokens_mean: float
    prefix_tokens_max: int

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())


def summarize_pinned_mooncake_c7_admissible(raw: bytes) -> C72AdmissibleSourceSummary:
    source = load_pinned_mooncake_trace(raw)
    derived = derive_pinned_mooncake_c7_admissible(source)
    records = derived.source_order

    inputs = [record.input_tokens for record in records]
    outputs = [record.output_tokens for record in records]
    prefixes = [record.prefix_tokens for record in records]
    if any(value is None for value in inputs + outputs + prefixes):
        raise AssertionError("admissible Mooncake records unexpectedly contain missing fields")
    input_values = [int(value) for value in inputs]
    output_values = [int(value) for value in outputs]
    prefix_values = [int(value) for value in prefixes]
    arrivals = [record.arrival_time_s for record in records]
    reusable = sum(value > 0 for value in prefix_values)

    return C72AdmissibleSourceSummary(
        source_dataset_fingerprint=source.fingerprint,
        admissible_dataset_fingerprint=derived.fingerprint,
        total_records=len(source.records),
        retained_records=len(records),
        retained_fraction=len(records) / len(source.records),
        arrival_min_s=min(arrivals),
        arrival_max_s=max(arrivals),
        input_min=min(input_values),
        input_max=max(input_values),
        input_mean=fmean(input_values),
        output_min=min(output_values),
        output_max=max(output_values),
        output_mean=fmean(output_values),
        reusable_records=reusable,
        reusable_fraction=reusable / len(records),
        prefix_tokens_mean=fmean(prefix_values),
        prefix_tokens_max=max(prefix_values),
    )


class C72ProgramTopology(str, Enum):
    SERIAL = "SERIAL"
    FANOUT = "FANOUT"


class C72SemanticOracle(Protocol):
    def attempt_current(self, request_id: str, attempt_id: str) -> bool:
        ...

    def state_compatible(
        self,
        state_id: str,
        *,
        program_id: str,
        session_id: str,
        continuation_id: str,
        request_id: str,
        attempt_id: str,
    ) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class C72OperationCase:
    operation_id: str
    observation: PolicyObservation
    input_tokens: int
    output_tokens: int
    eligible_reuse_tokens: int

    def __post_init__(self) -> None:
        _nonempty(self.operation_id, "operation_id")
        if not isinstance(self.observation, PolicyObservation):
            raise TypeError("observation must be PolicyObservation")
        _nonnegative_int(self.input_tokens, "input_tokens")
        _nonnegative_int(self.output_tokens, "output_tokens")
        eligible = _nonnegative_int(self.eligible_reuse_tokens, "eligible_reuse_tokens")
        if eligible > self.input_tokens:
            raise ValueError("eligible_reuse_tokens cannot exceed input_tokens")
        if not synthetic_request_c6_admissible(self.input_tokens, self.output_tokens):
            raise ValueError("operation lies outside the frozen C7.1/C6 runtime domain")
        if self.observation.program_id is None:
            raise ValueError("C7.2 operation requires program_id")
        if self.observation.session_id is None:
            raise ValueError("C7.2 operation requires session_id")
        if self.observation.continuation_id is None:
            raise ValueError("C7.2 operation requires continuation_id")
        if self.observation.attempt_id is None:
            raise ValueError("C7.2 operation requires attempt_id")
        if self.observation.attempt_authority != AttemptAuthority.CURRENT.name:
            raise ValueError("C7.2 comparison input must present CURRENT attempt authority")
        if eligible > 0:
            if self.observation.state_candidate_key is None:
                raise ValueError("reuse-eligible operation requires state_candidate_key")
            if self.observation.exact_state_id is None:
                raise ValueError("reuse-eligible operation requires exact_state_id for oracle checks")
            if not self.observation.state_locations:
                raise ValueError("reuse-eligible operation requires physical State locations")


@dataclass(frozen=True, slots=True)
class C72ProgramCase:
    program_id: str
    series: ExperimentSeries
    topology: C72ProgramTopology
    operations: tuple[C72OperationCase, ...]

    def __post_init__(self) -> None:
        _nonempty(self.program_id, "program_id")
        if self.series not in {
            ExperimentSeries.P1_DEEP_REUSE,
            ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
            ExperimentSeries.P7_STATELESS_OVERHEAD,
        }:
            raise ValueError("C7.2 supports only P1, P4, and P7")
        if not isinstance(self.topology, C72ProgramTopology):
            raise TypeError("topology must be C72ProgramTopology")
        if not isinstance(self.operations, tuple) or not self.operations:
            raise ValueError("operations must be a non-empty tuple")
        if not all(isinstance(item, C72OperationCase) for item in self.operations):
            raise TypeError("operations must contain C72OperationCase values")
        ids = [item.operation_id for item in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("operation_id values must be unique")
        if any(item.observation.program_id != self.program_id for item in self.operations):
            raise ValueError("every operation must bind to the Program ID")
        if self.series is ExperimentSeries.P1_DEEP_REUSE and self.topology is not C72ProgramTopology.SERIAL:
            raise ValueError("P1 requires SERIAL topology")
        if self.series is ExperimentSeries.P4_FANOUT_SHARED_PREFIX:
            if self.topology is not C72ProgramTopology.FANOUT:
                raise ValueError("P4 requires FANOUT topology")
            if len(self.operations) < 2:
                raise ValueError("P4 fan-out requires at least two branch operations")
        if self.series is ExperimentSeries.P7_STATELESS_OVERHEAD:
            if self.topology is not C72ProgramTopology.SERIAL or len(self.operations) != 1:
                raise ValueError("P7 C7.2 control Program is exactly one SERIAL request")
            operation = self.operations[0]
            if operation.eligible_reuse_tokens != 0:
                raise ValueError("P7 must not fabricate a reuse opportunity")
            if operation.observation.state_locations:
                raise ValueError("P7 must not expose physical State locality")


@dataclass(frozen=True, slots=True)
class C72OperationPolicyResult:
    operation_id: str
    policy_id: PolicyID
    worker_id: str | None
    ranked_worker_ids: tuple[str, ...]
    placement_reason: str
    eligible_reuse_tokens: int
    consumed_reuse_tokens: int
    semantic_violation_count: int
    efficiency_eligibility: EfficiencyEligibility
    cold_prefill_seconds: float | None
    decode_seconds: float | None
    recompute_seconds: float | None
    modeled_compute_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C72_OPERATION_SCHEMA,
            "operation_id": self.operation_id,
            "policy_id": self.policy_id.value,
            "worker_id": self.worker_id,
            "ranked_worker_ids": list(self.ranked_worker_ids),
            "placement_reason": self.placement_reason,
            "eligible_reuse_tokens": self.eligible_reuse_tokens,
            "consumed_reuse_tokens": self.consumed_reuse_tokens,
            "semantic_violation_count": self.semantic_violation_count,
            "efficiency_eligibility": self.efficiency_eligibility.value,
            "cold_prefill_seconds": self.cold_prefill_seconds,
            "decode_seconds": self.decode_seconds,
            "recompute_seconds": self.recompute_seconds,
            "modeled_compute_seconds": self.modeled_compute_seconds,
        }


@dataclass(frozen=True, slots=True)
class C72PolicyProgramResult:
    manifest_fingerprint: str
    protocol_fingerprint: str
    series: ExperimentSeries
    policy_id: PolicyID
    program_id: str
    operation_results: tuple[C72OperationPolicyResult, ...]
    eligible_reuse_opportunities: int
    consumed_reuse_opportunities: int
    eligible_reuse_tokens: int
    consumed_reuse_tokens: int
    semantic_violation_count: int
    efficiency_eligibility: EfficiencyEligibility
    recomputation_ratio: float
    state_reuse_ratio: float
    state_reuse_token_ratio: float
    cold_continuation_rate: float
    program_completed: bool
    program_completion_time_seconds: float | None
    fanout_completion_time_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C72_RESULT_SCHEMA,
            "manifest_fingerprint": self.manifest_fingerprint,
            "protocol_fingerprint": self.protocol_fingerprint,
            "series": self.series.value,
            "policy_id": self.policy_id.value,
            "program_id": self.program_id,
            "operation_results": [item.to_dict() for item in self.operation_results],
            "eligible_reuse_opportunities": self.eligible_reuse_opportunities,
            "consumed_reuse_opportunities": self.consumed_reuse_opportunities,
            "eligible_reuse_tokens": self.eligible_reuse_tokens,
            "consumed_reuse_tokens": self.consumed_reuse_tokens,
            "semantic_violation_count": self.semantic_violation_count,
            "efficiency_eligibility": self.efficiency_eligibility.value,
            "recomputation_ratio": self.recomputation_ratio,
            "state_reuse_ratio": self.state_reuse_ratio,
            "state_reuse_token_ratio": self.state_reuse_token_ratio,
            "cold_continuation_rate": self.cold_continuation_rate,
            "program_completed": self.program_completed,
            "program_completion_time_seconds": self.program_completion_time_seconds,
            "fanout_completion_time_seconds": self.fanout_completion_time_seconds,
        }

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())


@dataclass(frozen=True, slots=True)
class C72PairedResult:
    program_id: str
    series: ExperimentSeries
    protocol_fingerprint: str
    policy_results: tuple[C72PolicyProgramResult, ...]

    def __post_init__(self) -> None:
        if self.protocol_fingerprint != C7_PROTOCOL_FINGERPRINT:
            raise ValueError("paired result must bind to frozen C7.1 protocol")
        policy_ids = tuple(item.policy_id for item in self.policy_results)
        if policy_ids != tuple(PolicyID):
            raise ValueError("paired result must contain B0 through B4 in canonical order")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "cadi.c7.2.paired-routing-reuse.v1",
            "program_id": self.program_id,
            "series": self.series.value,
            "protocol_fingerprint": self.protocol_fingerprint,
            "policy_results": [item.to_dict() for item in self.policy_results],
        }

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())


def _validate_profile(profile: ValidatedRuntimeCostProfile, hardware_id: str) -> None:
    if not isinstance(profile, ValidatedRuntimeCostProfile):
        raise TypeError("profile must be ValidatedRuntimeCostProfile")
    if profile.hardware_id != hardware_id:
        raise ValueError("manifest hardware_id does not match C6 runtime profile")
    if profile.scientific_fingerprint != C64F_SCIENTIFIC_FINGERPRINT:
        raise ValueError("C6 runtime scientific fingerprint drift")
    if profile.artifact_sha256 != C64F_ARTIFACT_SHA256:
        raise ValueError("C6 runtime artifact SHA-256 drift")
    if profile.evidence_class != C64F_EVIDENCE_CLASS:
        raise ValueError("C6 runtime evidence class drift")


def _common_manifest_payload(manifest: C7ExperimentManifest) -> dict[str, Any]:
    payload = manifest.to_dict()
    payload.pop("policy_id")
    return payload


def _validate_paired_manifests(
    manifests: Mapping[PolicyID, C7ExperimentManifest],
    case: C72ProgramCase,
) -> None:
    if not isinstance(manifests, Mapping) or set(manifests) != set(PolicyID):
        raise ValueError("paired manifests require exactly B0 through B4")
    first_common: dict[str, Any] | None = None
    for policy_id in PolicyID:
        manifest = manifests[policy_id]
        if not isinstance(manifest, C7ExperimentManifest):
            raise TypeError("paired manifests must contain C7ExperimentManifest values")
        if manifest.policy_id is not policy_id:
            raise ValueError("paired manifest policy identity mismatch")
        if manifest.series is not case.series:
            raise ValueError("manifest series does not match Program case")
        common = _common_manifest_payload(manifest)
        if first_common is None:
            first_common = common
        elif common != first_common:
            raise ValueError("paired manifests differ outside policy_id")


def _selected_worker_consumes_reuse(
    *, policy_id: PolicyID, decision: PlacementDecision, operation: C72OperationCase
) -> int:
    if operation.eligible_reuse_tokens == 0 or decision.worker_id is None:
        return 0
    if decision.worker_id not in operation.observation.state_locations:
        return 0
    if policy_id is PolicyID.B4 and decision.reason != "COMPATIBLE_STATE_LOCALITY_THEN_LOAD":
        return 0
    # Local execution uses the same engine-level State/cache opportunity for B0-B3.
    # Those policies differ in placement information, not in worker-side cache capability.
    return operation.eligible_reuse_tokens


def _operation_result(
    *,
    policy_id: PolicyID,
    decision: PlacementDecision,
    operation: C72OperationCase,
    oracle: C72SemanticOracle,
    profile: ValidatedRuntimeCostProfile,
) -> C72OperationPolicyResult:
    consumed = _selected_worker_consumes_reuse(
        policy_id=policy_id, decision=decision, operation=operation
    )
    observation = operation.observation
    assert observation.attempt_id is not None
    assert observation.program_id is not None
    assert observation.session_id is not None
    assert observation.continuation_id is not None

    violations = 0
    if decision.worker_id is not None and not oracle.attempt_current(
        observation.request_id, observation.attempt_id
    ):
        violations += 1
    if consumed > 0:
        if observation.exact_state_id is None:
            raise AssertionError("consumed State lacks exact identity for semantic oracle")
        if not oracle.state_compatible(
            observation.exact_state_id,
            program_id=observation.program_id,
            session_id=observation.session_id,
            continuation_id=observation.continuation_id,
            request_id=observation.request_id,
            attempt_id=observation.attempt_id,
        ):
            violations += 1

    eligibility = efficiency_eligibility(covered_semantic_violations=violations)
    if decision.worker_id is None:
        cold_prefill = decode = recompute = modeled = None
    else:
        estimate = estimate_validated_runtime_cost(
            profile,
            InferenceCostWorkload(
                input_tokens=operation.input_tokens,
                output_tokens=operation.output_tokens,
                reusable_prefix_tokens=consumed,
                state_tokens=0,
            ),
        )
        cold_prefill = estimate.prefill_seconds
        decode = estimate.decode_seconds
        recompute = estimate.recompute_seconds
        modeled = recompute + decode

    return C72OperationPolicyResult(
        operation_id=operation.operation_id,
        policy_id=policy_id,
        worker_id=decision.worker_id,
        ranked_worker_ids=decision.ranked_worker_ids,
        placement_reason=decision.reason,
        eligible_reuse_tokens=operation.eligible_reuse_tokens,
        consumed_reuse_tokens=consumed,
        semantic_violation_count=violations,
        efficiency_eligibility=eligibility,
        cold_prefill_seconds=cold_prefill,
        decode_seconds=decode,
        recompute_seconds=recompute,
        modeled_compute_seconds=modeled,
    )


def _aggregate_policy_result(
    *,
    manifest: C7ExperimentManifest,
    case: C72ProgramCase,
    operation_results: tuple[C72OperationPolicyResult, ...],
) -> C72PolicyProgramResult:
    eligible_ops = sum(item.eligible_reuse_tokens > 0 for item in operation_results)
    consumed_ops = sum(item.consumed_reuse_tokens > 0 for item in operation_results)
    eligible_tokens = sum(item.eligible_reuse_tokens for item in operation_results)
    consumed_tokens = sum(item.consumed_reuse_tokens for item in operation_results)
    total_input = sum(operation.input_tokens for operation in case.operations)
    violations = sum(item.semantic_violation_count for item in operation_results)
    eligibility = efficiency_eligibility(covered_semantic_violations=violations)

    executed_eligible = [
        item
        for item in operation_results
        if item.worker_id is not None and item.eligible_reuse_tokens > 0
    ]
    cold = sum(item.consumed_reuse_tokens == 0 for item in executed_eligible)
    completed = all(item.worker_id is not None for item in operation_results)
    compute_values = [item.modeled_compute_seconds for item in operation_results]
    if completed:
        if any(value is None for value in compute_values):
            raise AssertionError("completed Program has missing modeled compute cost")
        values = [float(value) for value in compute_values]
        if case.topology is C72ProgramTopology.SERIAL:
            completion = sum(values)
            fanout_completion = None
        else:
            completion = max(values)
            fanout_completion = completion
    else:
        completion = None
        fanout_completion = None

    return C72PolicyProgramResult(
        manifest_fingerprint=manifest.fingerprint,
        protocol_fingerprint=C7_PROTOCOL_FINGERPRINT,
        series=case.series,
        policy_id=manifest.policy_id,
        program_id=case.program_id,
        operation_results=operation_results,
        eligible_reuse_opportunities=eligible_ops,
        consumed_reuse_opportunities=consumed_ops,
        eligible_reuse_tokens=eligible_tokens,
        consumed_reuse_tokens=consumed_tokens,
        semantic_violation_count=violations,
        efficiency_eligibility=eligibility,
        recomputation_ratio=recomputation_ratio(
            total_input_tokens=total_input,
            eligible_reuse_tokens=eligible_tokens,
            consumed_reuse_tokens=consumed_tokens,
        ),
        state_reuse_ratio=state_reuse_ratio(
            eligible_opportunities=eligible_ops,
            consumed_opportunities=consumed_ops,
        ),
        state_reuse_token_ratio=state_reuse_token_ratio(
            eligible_tokens=eligible_tokens,
            consumed_tokens=consumed_tokens,
        ),
        cold_continuation_rate=cold_continuation_rate(
            eligible_continuations=len(executed_eligible),
            fully_reconstructed_continuations=cold,
        ),
        program_completed=completed,
        program_completion_time_seconds=completion,
        fanout_completion_time_seconds=fanout_completion,
    )


def evaluate_paired_program(
    *,
    policies: Mapping[PolicyID, object],
    manifests: Mapping[PolicyID, C7ExperimentManifest],
    case: C72ProgramCase,
    oracle: C72SemanticOracle,
    profile: ValidatedRuntimeCostProfile,
) -> C72PairedResult:
    """Evaluate B0-B4 on identical operation observations and independent semantics."""

    if not isinstance(case, C72ProgramCase):
        raise TypeError("case must be C72ProgramCase")
    if not callable(getattr(oracle, "attempt_current", None)):
        raise TypeError("oracle must expose attempt_current")
    if not callable(getattr(oracle, "state_compatible", None)):
        raise TypeError("oracle must expose state_compatible")
    _validate_paired_manifests(manifests, case)
    hardware_id = manifests[PolicyID.B0].hardware_id
    _validate_profile(profile, hardware_id)

    by_policy: dict[PolicyID, list[C72OperationPolicyResult]] = {
        policy_id: [] for policy_id in PolicyID
    }
    for operation in case.operations:
        decisions = decide_paired_placements(policies, operation.observation)
        if tuple(decision.policy_id for decision in decisions) != tuple(PolicyID):
            raise AssertionError("paired placement order drift")
        for decision in decisions:
            by_policy[decision.policy_id].append(
                _operation_result(
                    policy_id=decision.policy_id,
                    decision=decision,
                    operation=operation,
                    oracle=oracle,
                    profile=profile,
                )
            )

    policy_results = tuple(
        _aggregate_policy_result(
            manifest=manifests[policy_id],
            case=case,
            operation_results=tuple(by_policy[policy_id]),
        )
        for policy_id in PolicyID
    )
    return C72PairedResult(
        program_id=case.program_id,
        series=case.series,
        protocol_fingerprint=C7_PROTOCOL_FINGERPRINT,
        policy_results=policy_results,
    )


def build_paired_manifests(
    *,
    experiment_id: str,
    git_commit: str,
    series: ExperimentSeries,
    workload_class: WorkloadClass,
    hardware_id: str,
    program_objective: str,
    seed: int | None,
    source_dataset_fingerprint: str | None,
    augmentation_fingerprint: str | None,
    parameters: tuple[tuple[str, int | float], ...],
    parameter_sources: tuple[tuple[str, Any], ...],
) -> Mapping[PolicyID, C7ExperimentManifest]:
    """Build five manifests differing only by closed B0-B4 policy identity."""

    result: dict[PolicyID, C7ExperimentManifest] = {}
    for policy_id in PolicyID:
        result[policy_id] = C7ExperimentManifest(
            experiment_id=experiment_id,
            git_commit=git_commit,
            protocol_fingerprint=C7_PROTOCOL_FINGERPRINT,
            series=series,
            policy_id=policy_id,
            workload_class=workload_class,
            hardware_id=hardware_id,
            program_objective=program_objective,
            seed=seed,
            source_dataset_fingerprint=source_dataset_fingerprint,
            augmentation_fingerprint=augmentation_fingerprint,
            parameters=parameters,
            parameter_sources=parameter_sources,  # type: ignore[arg-type]
        )
    return result


def main() -> None:
    print(
        _canonical_json(
            {
                "schema": "cadi.c7.2.routing-reuse-harness.v1",
                "base_commit": C72_BASE_COMMIT,
                "protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
                "source_filter": C72_SOURCE_FILTER_STEP,
                "result_schema": C72_RESULT_SCHEMA,
                "comparative_result_inspection": "NONE",
            }
        )
    )


if __name__ == "__main__":
    main()
