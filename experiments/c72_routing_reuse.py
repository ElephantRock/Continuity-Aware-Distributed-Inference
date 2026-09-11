from __future__ import annotations

from dataclasses import dataclass, replace
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
    ParameterSource,
    WorkloadClass,
    cold_continuation_rate,
    efficiency_eligibility,
    recomputation_ratio,
    source_request_c6_admissible,
    state_reuse_ratio,
    state_reuse_token_ratio,
    synthetic_request_c6_admissible,
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

C72_RESULT_SCHEMA = "cadi.c7.2.routing-reuse-result.v2"
C72_OPERATION_SCHEMA = "cadi.c7.2.routing-reuse-operation.v2"
C72_PAIRED_SCHEMA = "cadi.c7.2.paired-routing-reuse.v2"
C72_ADMISSIBLE_NORMALIZATION_VERSION = "cadi.c7.2.c6-admissible-trace.v1"
C72_BASE_COMMIT = "990ef4f081b0edebb1e2ca83bddaf252eb39e549"
C5_MOONCAKE_NORMALIZED_FINGERPRINT = "22ead90d97ae218f229f94378f8f019499ede0e4050e6cbf8092b455ae047718"
C72_SOURCE_FILTER_STEP = (
    "retain only unmodified records satisfying the frozen C7.1 C6-domain predicate: "
    "input_tokens>=1, output_tokens>=1, input_tokens+output_tokens<=4096"
)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fp(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _nni(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _nnf(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def derive_c7_admissible_trace(source: NormalizedTraceDataset) -> NormalizedTraceDataset:
    if not isinstance(source, NormalizedTraceDataset):
        raise TypeError("source must be NormalizedTraceDataset")
    source.require_fields((TraceField.INPUT_TOKENS, TraceField.OUTPUT_TOKENS))
    retained = tuple(
        record
        for record in source.source_order
        if source_request_c6_admissible(record.input_tokens, record.output_tokens)  # type: ignore[arg-type]
    )
    if not retained:
        raise ValueError("C7.1 C6-domain predicate retained no source records")
    manifest = source.manifest
    derived = NormalizedTraceDataset(
        TraceSourceManifest(
            source_id=manifest.source_id,
            source_name=manifest.source_name,
            source_uri=manifest.source_uri,
            source_version=manifest.source_version,
            license_id=manifest.license_id,
            source_sha256=manifest.source_sha256,
            normalization_version=C72_ADMISSIBLE_NORMALIZATION_VERSION,
            normalization_steps=manifest.normalization_steps + (C72_SOURCE_FILTER_STEP,),
            field_origins=manifest.field_origins,
        ),
        retained,
    )
    original = {record.record_id: record.to_dict() for record in source.source_order}
    if any(record.to_dict() != original[record.record_id] for record in derived.source_order):
        raise AssertionError("C7 admissibility derivation mutated a retained record")
    return derived


def derive_pinned_mooncake_c7_admissible(source: NormalizedTraceDataset) -> NormalizedTraceDataset:
    manifest = source.manifest
    checks = (
        (manifest.source_id, "mooncake-fast25-conversation", "source_id"),
        (manifest.source_version, MOONCAKE_SOURCE_REVISION, "source revision"),
        (manifest.source_sha256, MOONCAKE_SOURCE_SHA256, "source SHA-256"),
        (manifest.normalization_version, MOONCAKE_NORMALIZATION_VERSION, "normalization version"),
        (source.fingerprint, C5_MOONCAKE_NORMALIZED_FINGERPRINT, "normalized dataset fingerprint"),
    )
    for actual, expected, name in checks:
        if actual != expected:
            raise ValueError(f"Mooncake {name} drift")
    return derive_c7_admissible_trace(source)


@dataclass(frozen=True, slots=True)
class C72AdmissibleSourceSummary:
    source_dataset_fingerprint: str
    admissible_dataset_fingerprint: str
    total_records: int
    retained_records: int
    excluded_records: int
    retained_fraction: float
    arrival_min_s: float
    arrival_max_s: float
    input_min: int
    input_max: int
    input_mean: float
    output_min: int
    output_max: int
    output_mean: float
    total_tokens_max: int
    reusable_records: int
    reusable_fraction: float
    prefix_tokens_mean: float
    prefix_tokens_max: int

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


def summarize_pinned_mooncake_c7_admissible(raw: bytes) -> C72AdmissibleSourceSummary:
    source = load_pinned_mooncake_trace(raw)
    derived = derive_pinned_mooncake_c7_admissible(source)
    records = derived.source_order
    inputs = [record.input_tokens for record in records]
    outputs = [record.output_tokens for record in records]
    prefixes = [record.prefix_tokens for record in records]
    if any(value is None for value in inputs + outputs + prefixes):
        raise AssertionError("admissible Mooncake records unexpectedly contain missing fields")
    iv = [int(v) for v in inputs]
    ov = [int(v) for v in outputs]
    pv = [int(v) for v in prefixes]
    arrivals = [record.arrival_time_s for record in records]
    reusable = sum(value > 0 for value in pv)
    return C72AdmissibleSourceSummary(
        source.fingerprint,
        derived.fingerprint,
        len(source.records),
        len(records),
        len(source.records) - len(records),
        len(records) / len(source.records),
        min(arrivals), max(arrivals),
        min(iv), max(iv), fmean(iv),
        min(ov), max(ov), fmean(ov),
        max(i + o for i, o in zip(iv, ov, strict=True)),
        reusable,
        reusable / len(records),
        fmean(pv),
        max(pv),
    )


class C72ProgramTopology(str, Enum):
    SERIAL = "SERIAL"
    FANOUT = "FANOUT"


class C72SemanticOracle(Protocol):
    def attempt_current(self, request_id: str, attempt_id: str) -> bool: ...
    def state_compatible(
        self, state_id: str, *, program_id: str, session_id: str,
        continuation_id: str, request_id: str, attempt_id: str
    ) -> bool: ...


def _observation_dict(observation: PolicyObservation) -> dict[str, Any]:
    return {
        "request_id": observation.request_id,
        "workers": [
            {
                "worker_id": w.worker_id, "available": w.available, "capacity": w.capacity,
                "active_tasks": w.active_tasks, "queued_tasks": w.queued_tasks,
            }
            for w in observation.workers
        ],
        "attempt_id": observation.attempt_id,
        "attempt_authority": observation.attempt_authority,
        "session_id": observation.session_id,
        "session_preferred_location": observation.session_preferred_location,
        "continuation_id": observation.continuation_id,
        "continuation_ancestry": list(observation.continuation_ancestry),
        "state_candidate_key": observation.state_candidate_key,
        "exact_state_id": observation.exact_state_id,
        "state_locations": list(observation.state_locations),
        "state_provenance": [list(item) for item in observation.state_provenance],
        "producer_attempt_id": observation.producer_attempt_id,
        "binding_id": observation.binding_id,
        "binding_epoch": observation.binding_epoch,
        "evidence_authority": observation.evidence_authority,
        "evidence_status": observation.evidence_status,
        "evidence_freshness": observation.evidence_freshness,
        "program_id": observation.program_id,
        "state_lifecycle": observation.state_lifecycle,
        "reconciliation": observation.reconciliation,
    }


@dataclass(frozen=True, slots=True)
class C72OperationCase:
    operation_id: str
    observation: PolicyObservation
    input_tokens: int
    output_tokens: int
    eligible_reuse_tokens: int
    release_time_seconds: float = 0.0

    def __post_init__(self) -> None:
        _nonempty(self.operation_id, "operation_id")
        if not isinstance(self.observation, PolicyObservation):
            raise TypeError("observation must be PolicyObservation")
        _nni(self.input_tokens, "input_tokens")
        _nni(self.output_tokens, "output_tokens")
        eligible = _nni(self.eligible_reuse_tokens, "eligible_reuse_tokens")
        object.__setattr__(self, "release_time_seconds", _nnf(self.release_time_seconds, "release_time_seconds"))
        if eligible > self.input_tokens:
            raise ValueError("eligible_reuse_tokens cannot exceed input_tokens")
        if not synthetic_request_c6_admissible(self.input_tokens, self.output_tokens):
            raise ValueError("operation lies outside the frozen C7.1/C6 runtime domain")
        for name in ("program_id", "session_id", "continuation_id", "attempt_id"):
            if getattr(self.observation, name) is None:
                raise ValueError(f"C7.2 operation requires {name}")
        if self.observation.attempt_authority != AttemptAuthority.CURRENT.name:
            raise ValueError("C7.2 comparison input must present CURRENT attempt authority")
        if eligible > 0:
            if self.observation.state_candidate_key is None:
                raise ValueError("reuse-eligible operation requires state_candidate_key")
            if self.observation.exact_state_id is None:
                raise ValueError("reuse-eligible operation requires exact_state_id for oracle checks")

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "observation": _observation_dict(self.observation),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "eligible_reuse_tokens": self.eligible_reuse_tokens,
            "release_time_seconds": self.release_time_seconds,
        }


def _worker_shape(operation: C72OperationCase) -> tuple[tuple[str, int], ...]:
    return tuple((w.worker_id, w.capacity) for w in operation.observation.workers)


def _validate_p7(operation: C72OperationCase) -> None:
    o = operation.observation
    forbidden = {
        "session_preferred_location": o.session_preferred_location,
        "state_candidate_key": o.state_candidate_key,
        "exact_state_id": o.exact_state_id,
        "producer_attempt_id": o.producer_attempt_id,
        "binding_id": o.binding_id,
        "binding_epoch": o.binding_epoch,
        "evidence_authority": o.evidence_authority,
        "evidence_status": o.evidence_status,
        "evidence_freshness": o.evidence_freshness,
        "state_lifecycle": o.state_lifecycle,
        "reconciliation": o.reconciliation,
    }
    present = sorted(k for k, v in forbidden.items() if v is not None)
    if present or o.continuation_ancestry or o.state_locations or o.state_provenance:
        raise ValueError("P7 stateless control forbids cross-request locality/continuity hints")


@dataclass(frozen=True, slots=True)
class C72ProgramCase:
    program_id: str
    series: ExperimentSeries
    topology: C72ProgramTopology
    operations: tuple[C72OperationCase, ...]
    program_start_time_seconds: float = 0.0
    worker_ready_times_seconds: tuple[tuple[str, float], ...] = ()

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
        if not self.operations or not all(isinstance(x, C72OperationCase) for x in self.operations):
            raise ValueError("operations must be a non-empty tuple of C72OperationCase")
        if len({x.operation_id for x in self.operations}) != len(self.operations):
            raise ValueError("operation_id values must be unique")
        if any(x.observation.program_id != self.program_id for x in self.operations):
            raise ValueError("every operation must bind to the Program ID")
        start = _nnf(self.program_start_time_seconds, "program_start_time_seconds")
        object.__setattr__(self, "program_start_time_seconds", start)
        if any(x.release_time_seconds < start for x in self.operations):
            raise ValueError("operation release time cannot precede Program start")
        shape = _worker_shape(self.operations[0])
        if not shape or any(_worker_shape(x) != shape for x in self.operations[1:]):
            raise ValueError("C7.2 Program must keep worker identity/capacity fixed")
        worker_ids = tuple(worker_id for worker_id, _ in shape)
        if not self.worker_ready_times_seconds:
            ready = tuple((worker_id, start) for worker_id in worker_ids)
        else:
            ready = tuple((wid, _nnf(t, f"ready time {wid}")) for wid, t in self.worker_ready_times_seconds)
            ids = tuple(wid for wid, _ in ready)
            if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)) or ids != tuple(sorted(worker_ids)):
                raise ValueError("worker_ready_times_seconds must uniquely cover Program workers")
        if any(t < start for _, t in ready):
            raise ValueError("worker ready time cannot precede Program start")
        object.__setattr__(self, "worker_ready_times_seconds", ready)
        if self.series is ExperimentSeries.P1_DEEP_REUSE and self.topology is not C72ProgramTopology.SERIAL:
            raise ValueError("P1 requires SERIAL topology")
        if self.series is ExperimentSeries.P4_FANOUT_SHARED_PREFIX:
            if self.topology is not C72ProgramTopology.FANOUT or len(self.operations) < 2:
                raise ValueError("P4 requires FANOUT topology with at least two branches")
        if self.series is ExperimentSeries.P7_STATELESS_OVERHEAD:
            if self.topology is not C72ProgramTopology.SERIAL or len(self.operations) != 1:
                raise ValueError("P7 C7.2 control Program is exactly one SERIAL request")
            if self.operations[0].eligible_reuse_tokens:
                raise ValueError("P7 must not fabricate a reuse opportunity")
            _validate_p7(self.operations[0])

    @property
    def worker_count(self) -> int:
        return len(self.operations[0].observation.workers)

    @property
    def fingerprint(self) -> str:
        return _fp({
            "program_id": self.program_id,
            "series": self.series.value,
            "topology": self.topology.value,
            "program_start_time_seconds": self.program_start_time_seconds,
            "worker_ready_times_seconds": [list(x) for x in self.worker_ready_times_seconds],
            "operations": [x.to_dict() for x in self.operations],
        })


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
    modeled_routing_control_seconds: float | None
    modeled_service_seconds: float | None
    release_time_seconds: float
    service_start_time_seconds: float | None = None
    completion_time_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"schema": C72_OPERATION_SCHEMA, **{
            name: (getattr(self, name).value if isinstance(getattr(self, name), Enum) else
                   list(getattr(self, name)) if name == "ranked_worker_ids" else getattr(self, name))
            for name in self.__dataclass_fields__
        }}


@dataclass(frozen=True, slots=True)
class C72PolicyProgramResult:
    manifest_fingerprint: str
    protocol_fingerprint: str
    program_case_fingerprint: str
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
    program_start_time_seconds: float
    program_terminal_time_seconds: float | None
    program_completion_time_seconds: float | None
    fanout_start_time_seconds: float | None
    fanout_terminal_time_seconds: float | None
    fanout_completion_time_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"schema": C72_RESULT_SCHEMA}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name == "operation_results":
                result[name] = [x.to_dict() for x in value]
            elif isinstance(value, Enum):
                result[name] = value.value
            else:
                result[name] = value
        return result

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


@dataclass(frozen=True, slots=True)
class C72PairedResult:
    program_id: str
    series: ExperimentSeries
    protocol_fingerprint: str
    program_case_fingerprint: str
    policy_results: tuple[C72PolicyProgramResult, ...]

    def __post_init__(self) -> None:
        if self.protocol_fingerprint != C7_PROTOCOL_FINGERPRINT:
            raise ValueError("paired result must bind to frozen C7.1 protocol")
        if tuple(x.policy_id for x in self.policy_results) != tuple(PolicyID):
            raise ValueError("paired result must contain B0 through B4 in canonical order")
        if any(x.program_case_fingerprint != self.program_case_fingerprint for x in self.policy_results):
            raise ValueError("paired policy results must bind to one Program case")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C72_PAIRED_SCHEMA,
            "program_id": self.program_id,
            "series": self.series.value,
            "protocol_fingerprint": self.protocol_fingerprint,
            "program_case_fingerprint": self.program_case_fingerprint,
            "policy_results": [x.to_dict() for x in self.policy_results],
        }

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


def _validate_profile(profile: ValidatedRuntimeCostProfile, hardware_id: str) -> None:
    if not isinstance(profile, ValidatedRuntimeCostProfile):
        raise TypeError("profile must be ValidatedRuntimeCostProfile")
    checks = (
        (profile.hardware_id, hardware_id, "hardware_id"),
        (profile.scientific_fingerprint, C64F_SCIENTIFIC_FINGERPRINT, "scientific fingerprint"),
        (profile.artifact_sha256, C64F_ARTIFACT_SHA256, "artifact SHA-256"),
        (profile.evidence_class, C64F_EVIDENCE_CLASS, "evidence class"),
    )
    for actual, expected, name in checks:
        if actual != expected:
            raise ValueError(f"C6 runtime {name} drift")


def _common_manifest_payload(manifest: C7ExperimentManifest) -> dict[str, Any]:
    payload = manifest.to_dict()
    payload.pop("policy_id")
    return payload


def _require_parameter(manifest: C7ExperimentManifest, name: str) -> int | float:
    params = dict(manifest.parameters)
    if name not in params:
        raise ValueError(f"C7.2 manifest requires realized structural axis {name}")
    return params[name]


def _validate_axes(manifest: C7ExperimentManifest, case: C72ProgramCase) -> None:
    params = dict(manifest.parameters)
    if case.series is ExperimentSeries.P1_DEEP_REUSE:
        if _require_parameter(manifest, "session_depth") != len(case.operations):
            raise ValueError("manifest session_depth does not match realized Program depth")
        if _require_parameter(manifest, "worker_count") != case.worker_count:
            raise ValueError("manifest worker_count does not match realized workers")
        fraction = float(_require_parameter(manifest, "reusable_prefix_fraction"))
        for op in case.operations[1:]:
            realized = op.eligible_reuse_tokens / op.input_tokens if op.input_tokens else 0.0
            if realized != fraction:
                raise ValueError("manifest reusable_prefix_fraction does not match realized P1 continuation")
    elif case.series is ExperimentSeries.P4_FANOUT_SHARED_PREFIX:
        if _require_parameter(manifest, "fanout_width") != len(case.operations):
            raise ValueError("manifest fanout_width does not match realized branch count")
        fraction = float(_require_parameter(manifest, "shared_prefix_fraction"))
        for op in case.operations:
            realized = op.eligible_reuse_tokens / op.input_tokens if op.input_tokens else 0.0
            if realized != fraction:
                raise ValueError("manifest shared_prefix_fraction does not match realized P4 branch")
        if "worker_count" in params and params["worker_count"] != case.worker_count:
            raise ValueError("manifest worker_count does not match realized workers")
    elif _require_parameter(manifest, "worker_count") != case.worker_count:
        raise ValueError("manifest worker_count does not match realized workers")


def _validate_manifests(manifests: Mapping[PolicyID, C7ExperimentManifest], case: C72ProgramCase) -> None:
    if not isinstance(manifests, Mapping) or set(manifests) != set(PolicyID):
        raise ValueError("paired manifests require exactly B0 through B4")
    common = None
    for policy_id in PolicyID:
        manifest = manifests[policy_id]
        if manifest.policy_id is not policy_id or manifest.series is not case.series:
            raise ValueError("paired manifest identity/series mismatch")
        _validate_axes(manifest, case)
        payload = _common_manifest_payload(manifest)
        if common is None:
            common = payload
        elif payload != common:
            raise ValueError("paired manifests differ outside policy_id")


def _consumed(policy_id: PolicyID, decision: PlacementDecision, operation: C72OperationCase) -> int:
    if not operation.eligible_reuse_tokens or decision.worker_id is None:
        return 0
    if decision.worker_id not in operation.observation.state_locations:
        return 0
    if policy_id is PolicyID.B4 and decision.reason != "COMPATIBLE_STATE_LOCALITY_THEN_LOAD":
        return 0
    return operation.eligible_reuse_tokens


def _operation_result(
    policy_id: PolicyID, decision: PlacementDecision, operation: C72OperationCase,
    oracle: C72SemanticOracle, profile: ValidatedRuntimeCostProfile,
) -> C72OperationPolicyResult:
    consumed = _consumed(policy_id, decision, operation)
    o = operation.observation
    violations = 0
    if decision.worker_id is not None and not oracle.attempt_current(o.request_id, o.attempt_id):  # type: ignore[arg-type]
        violations += 1
    if consumed and not oracle.state_compatible(
        o.exact_state_id,  # type: ignore[arg-type]
        program_id=o.program_id, session_id=o.session_id, continuation_id=o.continuation_id,
        request_id=o.request_id, attempt_id=o.attempt_id,  # type: ignore[arg-type]
    ):
        violations += 1
    if decision.worker_id is None:
        cold = decode = recompute = compute = control = service = None
    else:
        estimate = estimate_validated_runtime_cost(
            profile,
            InferenceCostWorkload(operation.input_tokens, operation.output_tokens, consumed, 0),
        )
        cold, decode, recompute = estimate.prefill_seconds, estimate.decode_seconds, estimate.recompute_seconds
        compute = recompute + decode
        control = 0.0
        service = compute
    return C72OperationPolicyResult(
        operation.operation_id, policy_id, decision.worker_id, decision.ranked_worker_ids,
        decision.reason, operation.eligible_reuse_tokens, consumed, violations,
        efficiency_eligibility(covered_semantic_violations=violations),
        cold, decode, recompute, compute, control, service, operation.release_time_seconds,
    )


def _schedule(case: C72ProgramCase, results: tuple[C72OperationPolicyResult, ...]):
    if any(x.worker_id is None for x in results):
        return results, None, None, None, None
    ready = dict(case.worker_ready_times_seconds)
    slots = {
        worker.worker_id: [ready[worker.worker_id]] * worker.capacity
        for worker in case.operations[0].observation.workers
    }
    dependency = case.program_start_time_seconds
    scheduled = []
    for op, result in zip(case.operations, results, strict=True):
        worker_slots = slots[result.worker_id]  # type: ignore[index]
        index = min(range(len(worker_slots)), key=lambda i: (worker_slots[i], i))
        start = max(
            op.release_time_seconds,
            dependency if case.topology is C72ProgramTopology.SERIAL else case.program_start_time_seconds,
            worker_slots[index],
        )
        completion = start + result.modeled_service_seconds  # type: ignore[operator]
        worker_slots[index] = completion
        if case.topology is C72ProgramTopology.SERIAL:
            dependency = completion
        scheduled.append(replace(result, service_start_time_seconds=start, completion_time_seconds=completion))
    terminal = max(x.completion_time_seconds for x in scheduled)  # type: ignore[type-var]
    pct = terminal - case.program_start_time_seconds
    fct = pct if case.topology is C72ProgramTopology.FANOUT else None
    return tuple(scheduled), terminal, pct, terminal if fct is not None else None, fct


def _aggregate(manifest: C7ExperimentManifest, case: C72ProgramCase, results: tuple[C72OperationPolicyResult, ...]):
    eligible_ops = sum(x.eligible_reuse_tokens > 0 for x in results)
    consumed_ops = sum(x.consumed_reuse_tokens > 0 for x in results)
    eligible_tokens = sum(x.eligible_reuse_tokens for x in results)
    consumed_tokens = sum(x.consumed_reuse_tokens for x in results)
    total_input = sum(x.input_tokens for x in case.operations)
    violations = sum(x.semantic_violation_count for x in results)
    executed_eligible = [x for x in results if x.worker_id is not None and x.eligible_reuse_tokens > 0]
    cold = sum(x.consumed_reuse_tokens == 0 for x in executed_eligible)
    completed = all(x.worker_id is not None for x in results)
    if completed:
        results, terminal, pct, fanout_terminal, fct = _schedule(case, results)
    else:
        terminal = pct = fanout_terminal = fct = None
    return C72PolicyProgramResult(
        manifest.fingerprint, C7_PROTOCOL_FINGERPRINT, case.fingerprint, case.series, manifest.policy_id,
        case.program_id, results, eligible_ops, consumed_ops, eligible_tokens, consumed_tokens, violations,
        efficiency_eligibility(covered_semantic_violations=violations),
        recomputation_ratio(total_input_tokens=total_input, eligible_reuse_tokens=eligible_tokens, consumed_reuse_tokens=consumed_tokens),
        state_reuse_ratio(eligible_opportunities=eligible_ops, consumed_opportunities=consumed_ops),
        state_reuse_token_ratio(eligible_tokens=eligible_tokens, consumed_tokens=consumed_tokens),
        cold_continuation_rate(eligible_continuations=len(executed_eligible), fully_reconstructed_continuations=cold),
        completed, case.program_start_time_seconds, terminal, pct,
        case.program_start_time_seconds if completed and case.topology is C72ProgramTopology.FANOUT else None,
        fanout_terminal, fct,
    )


def evaluate_paired_program(
    *, policies: Mapping[PolicyID, object], manifests: Mapping[PolicyID, C7ExperimentManifest],
    case: C72ProgramCase, oracle: C72SemanticOracle, profile: ValidatedRuntimeCostProfile,
) -> C72PairedResult:
    if not isinstance(case, C72ProgramCase):
        raise TypeError("case must be C72ProgramCase")
    if not callable(getattr(oracle, "attempt_current", None)) or not callable(getattr(oracle, "state_compatible", None)):
        raise TypeError("oracle must expose attempt_current and state_compatible")
    _validate_manifests(manifests, case)
    _validate_profile(profile, manifests[PolicyID.B0].hardware_id)
    by_policy = {policy_id: [] for policy_id in PolicyID}
    for operation in case.operations:
        decisions = decide_paired_placements(policies, operation.observation)
        if tuple(x.policy_id for x in decisions) != tuple(PolicyID):
            raise AssertionError("paired placement order drift")
        for decision in decisions:
            by_policy[decision.policy_id].append(_operation_result(decision.policy_id, decision, operation, oracle, profile))
    policy_results = tuple(
        _aggregate(manifests[policy_id], case, tuple(by_policy[policy_id])) for policy_id in PolicyID
    )
    return C72PairedResult(case.program_id, case.series, C7_PROTOCOL_FINGERPRINT, case.fingerprint, policy_results)


def build_paired_manifests(
    *, experiment_id: str, git_commit: str, series: ExperimentSeries, workload_class: WorkloadClass,
    hardware_id: str, program_objective: str, seed: int | None,
    source_dataset_fingerprint: str | None, augmentation_fingerprint: str | None,
    parameters: tuple[tuple[str, int | float], ...],
    parameter_sources: tuple[tuple[str, ParameterSource], ...],
) -> Mapping[PolicyID, C7ExperimentManifest]:
    return {
        policy_id: C7ExperimentManifest(
            experiment_id, git_commit, C7_PROTOCOL_FINGERPRINT, series, policy_id, workload_class,
            hardware_id, program_objective, seed, source_dataset_fingerprint, augmentation_fingerprint,
            parameters, parameter_sources,
        )
        for policy_id in PolicyID
    }


def main() -> None:
    print(_json({
        "schema": "cadi.c7.2.routing-reuse-harness.v2",
        "base_commit": C72_BASE_COMMIT,
        "protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
        "source_filter": C72_SOURCE_FILTER_STEP,
        "result_schema": C72_RESULT_SCHEMA,
        "program_completion_time_model": "deterministic terminal-minus-start over paired release/ready/capacity/placement/C6-service inputs",
        "routing_control_cost_model": "ZERO_UNEVIDENCED_COMPONENT",
        "comparative_result_inspection": "NONE",
    }))


if __name__ == "__main__":
    main()
