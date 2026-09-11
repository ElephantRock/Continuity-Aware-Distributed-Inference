from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .inference_cost import InferenceCostWorkload
from .resources import ResourceModel, ResourceTask


C64G_RUNTIME_SCHEMA = "cadi.c6.4g.validated-runtime-profile.v1"
C64G_ESTIMATE_SCHEMA = "cadi.c6.4g.validated-runtime-estimate.v1"
C64F_RESULT_SCHEMA = "cadi.c6.4f.exhaustive-source-equivalence-result.v1"
C64F_RESULT_KIND = "EXHAUSTIVE_FINITE_DECLARED_DOMAIN_SOURCE_EQUIVALENCE"
C64F_REPRESENTATION_ID = (
    "cadi.c6.cost-representation.v4."
    "source-ordered-polynomial+source-linear-algebra+decode-step-affine"
)
C64F_EVIDENCE_CLASS = "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"
C64F_DECISION = "ADEQUATE_WITHIN_DECLARED_DOMAIN"
C64F_SCIENTIFIC_FINGERPRINT = (
    "cc1b62c4e38a1612720f601375c425c0bd7d67b4eff11b101bfdf87b79a7a61a"
)
C64F_ARTIFACT_SHA256 = (
    "93990386135ecbf6fc38e579841eace3431a068b10ee59209fe6ad7084bd8889"
)
C64G_SUPPORTED_HARDWARE_IDS = frozenset({"a100-80gb", "h100-80gb"})
C64G_PREFILL_TOKEN_MAX = 4096
C64G_MAX_MODEL_LENGTH = 4096
C64G_TRANSFER_BYTES_PER_TOKEN = 8192
C64G_TRANSFER_PREDICTOR_TOKEN_MAX = 4096


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _default_artifact_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "artifacts"
        / "c6.4f"
        / "exhaustive-source-equivalence.json"
    )


def _finite_nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return numeric


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _require_exact(value: Any, expected: Any, name: str) -> None:
    if value != expected:
        raise ValueError(f"{name} drift: expected={expected!r}, observed={value!r}")


@dataclass(frozen=True, slots=True)
class ValidatedRuntimeCostProfile:
    """Dependency-free request-time projection of the C6.4f-accepted v4 domain."""

    hardware_id: str
    source_profile_fingerprint: str
    materialized_domain_fingerprint: str
    prefill_seconds_by_input_token: tuple[float, ...]
    transfer_seconds_by_predictor_token: tuple[float, ...]
    decode_fixed_seconds_per_output_token: float
    decode_seconds_per_context_token_step: float
    state_fixed_bytes: float
    state_bytes_per_token: float
    memory_capacity_bytes: float
    scientific_fingerprint: str = C64F_SCIENTIFIC_FINGERPRINT
    artifact_sha256: str = C64F_ARTIFACT_SHA256
    representation_id: str = C64F_REPRESENTATION_ID
    evidence_class: str = C64F_EVIDENCE_CLASS

    def __post_init__(self) -> None:
        if self.hardware_id not in C64G_SUPPORTED_HARDWARE_IDS:
            raise ValueError("hardware_id is outside the C6.4f accepted family")
        if not isinstance(self.source_profile_fingerprint, str) or not self.source_profile_fingerprint:
            raise ValueError("source_profile_fingerprint must be non-empty")
        if (
            not isinstance(self.materialized_domain_fingerprint, str)
            or not self.materialized_domain_fingerprint
        ):
            raise ValueError("materialized_domain_fingerprint must be non-empty")
        if not isinstance(self.prefill_seconds_by_input_token, tuple):
            raise TypeError("prefill_seconds_by_input_token must be a tuple")
        if not isinstance(self.transfer_seconds_by_predictor_token, tuple):
            raise TypeError("transfer_seconds_by_predictor_token must be a tuple")
        if len(self.prefill_seconds_by_input_token) != C64G_PREFILL_TOKEN_MAX:
            raise ValueError("prefill runtime table must contain exactly 4096 values")
        if len(self.transfer_seconds_by_predictor_token) != C64G_TRANSFER_PREDICTOR_TOKEN_MAX:
            raise ValueError("transfer runtime table must contain exactly 4096 values")
        for family_name, family in (
            ("prefill", self.prefill_seconds_by_input_token),
            ("transfer", self.transfer_seconds_by_predictor_token),
        ):
            if not all(math.isfinite(value) and value >= 0 for value in family):
                raise ValueError(f"{family_name} runtime table contains an invalid value")
        for field_name in (
            "decode_fixed_seconds_per_output_token",
            "decode_seconds_per_context_token_step",
            "state_fixed_bytes",
            "state_bytes_per_token",
            "memory_capacity_bytes",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite_nonnegative(getattr(self, field_name), field_name),
            )
        if self.memory_capacity_bytes <= 0:
            raise ValueError("memory_capacity_bytes must be positive")
        _require_exact(
            self.scientific_fingerprint,
            C64F_SCIENTIFIC_FINGERPRINT,
            "scientific_fingerprint",
        )
        _require_exact(self.artifact_sha256, C64F_ARTIFACT_SHA256, "artifact_sha256")
        _require_exact(self.representation_id, C64F_REPRESENTATION_ID, "representation_id")
        _require_exact(self.evidence_class, C64F_EVIDENCE_CLASS, "evidence_class")

    @property
    def profile_id(self) -> str:
        return f"c6.4g:{self.hardware_id}:accepted-c6.4f-runtime"

    def prefill_seconds(self, input_tokens: int) -> float:
        if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
            raise TypeError("input_tokens must be an integer")
        if input_tokens == 0:
            return 0.0
        if not 1 <= input_tokens <= C64G_PREFILL_TOKEN_MAX:
            raise ValueError("cold-prefill evaluation outside C6.4f accepted domain")
        return self.prefill_seconds_by_input_token[input_tokens - 1]

    def transfer_seconds(self, state_bytes: float) -> float:
        axis_bytes = _finite_nonnegative(state_bytes, "state_bytes")
        if not axis_bytes.is_integer():
            raise ValueError("state_bytes must be an exact integer byte count")
        integer_bytes = int(axis_bytes)
        if integer_bytes == 0:
            return 0.0
        if integer_bytes % C64G_TRANSFER_BYTES_PER_TOKEN:
            raise ValueError("transfer bytes outside exact 8192-byte source-token domain")
        predictor_tokens = integer_bytes // C64G_TRANSFER_BYTES_PER_TOKEN
        if not 1 <= predictor_tokens <= C64G_TRANSFER_PREDICTOR_TOKEN_MAX:
            raise ValueError("transfer bytes outside C6.4f accepted lookup domain")
        return self.transfer_seconds_by_predictor_token[predictor_tokens - 1]


@dataclass(frozen=True, slots=True)
class ValidatedRuntimeCostEstimate:
    profile_id: str
    hardware_id: str
    source_profile_fingerprint: str
    materialized_domain_fingerprint: str
    scientific_fingerprint: str
    artifact_sha256: str
    prefill_seconds: float
    decode_seconds: float
    recompute_seconds: float
    transfer_seconds: float
    state_bytes: float
    memory_capacity_fraction: float
    recompute_tokens: int
    decode_context_token_steps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C64G_ESTIMATE_SCHEMA,
            **{name: getattr(self, name) for name in self.__dataclass_fields__},
        }


class ValidatedComputePhase(str, Enum):
    PREFILL = "PREFILL"
    DECODE = "DECODE"
    RECOMPUTE = "RECOMPUTE"


@dataclass(frozen=True, slots=True)
class ScheduledValidatedInference:
    phase: ValidatedComputePhase
    task: ResourceTask
    estimate: ValidatedRuntimeCostEstimate


def _runtime_family_table(record: Mapping[str, Any], family_name: str) -> tuple[float, ...]:
    family = _require_mapping(record.get(family_name), family_name)
    _require_exact(family.get("decision"), "PASS", f"{family_name}.decision")
    _require_exact(family.get("point_count"), 4096, f"{family_name}.point_count")
    _require_exact(family.get("max_ulp_distance"), 0, f"{family_name}.max_ulp_distance")
    _require_exact(family.get("ulp_violation_count"), 0, f"{family_name}.ulp_violation_count")
    points = family.get("points")
    if not isinstance(points, list) or len(points) != 4096:
        raise ValueError(f"{family_name}.points must contain exactly 4096 entries")

    values: list[float] = []
    for expected_axis, raw_point in enumerate(points, start=1):
        point = _require_mapping(raw_point, f"{family_name}.points[{expected_axis - 1}]")
        _require_exact(point.get("axis_value"), expected_axis, f"{family_name}.axis_value")
        _require_exact(point.get("ulp_distance"), 0, f"{family_name}.ulp_distance")
        _require_exact(point.get("within_ulp_budget"), True, f"{family_name}.within_ulp_budget")
        source = _finite_nonnegative(
            point.get("source_seconds"), f"{family_name}.source_seconds"
        )
        projected = _finite_nonnegative(
            point.get("v4_seconds"), f"{family_name}.v4_seconds"
        )
        if source != projected:
            raise ValueError(f"{family_name} accepted point is not exact source-equivalent")
        if family_name == "transfer":
            _require_exact(
                point.get("public_bytes"),
                expected_axis * C64G_TRANSFER_BYTES_PER_TOKEN,
                "transfer.public_bytes",
            )
        values.append(projected)
    return tuple(values)


def _runtime_profile_from_hardware(record: Mapping[str, Any]) -> ValidatedRuntimeCostProfile:
    hardware_id = record.get("hardware_id")
    if hardware_id not in C64G_SUPPORTED_HARDWARE_IDS:
        raise ValueError("unexpected hardware record in C6.4f artifact")
    _require_exact(record.get("decision"), C64F_DECISION, f"{hardware_id}.decision")

    carried_decode = _require_mapping(record.get("carried_decode"), "carried_decode")
    _require_exact(carried_decode.get("decision"), "PASS", "carried_decode.decision")
    _require_exact(
        carried_decode.get("coefficients_match_frozen_c63"),
        True,
        "carried_decode.coefficients_match_frozen_c63",
    )
    decode_record = _require_mapping(carried_decode.get("record"), "carried_decode.record")
    fit = _require_mapping(decode_record.get("fit"), "carried_decode.record.fit")
    decode_fixed = _finite_nonnegative(
        fit.get("intercept_seconds"), "decode_fixed_seconds_per_output_token"
    )
    decode_slope = _finite_nonnegative(
        fit.get("slope_seconds_per_axis_unit"),
        "decode_seconds_per_context_token_step",
    )

    state_memory = _require_mapping(record.get("state_memory"), "state_memory")
    _require_exact(state_memory.get("decision"), "PASS", "state_memory.decision")
    observed = _require_mapping(state_memory.get("observed"), "state_memory.observed")

    def sourced_value(name: str, expected_unit: str) -> float:
        scalar = _require_mapping(observed.get(name), f"state_memory.observed.{name}")
        _require_exact(scalar.get("unit"), expected_unit, f"{name}.unit")
        return _finite_nonnegative(scalar.get("value"), name)

    return ValidatedRuntimeCostProfile(
        hardware_id=hardware_id,
        source_profile_fingerprint=str(record.get("profile_fingerprint", "")),
        materialized_domain_fingerprint=str(record.get("materialized_domain_fingerprint", "")),
        prefill_seconds_by_input_token=_runtime_family_table(record, "prefill"),
        transfer_seconds_by_predictor_token=_runtime_family_table(record, "transfer"),
        decode_fixed_seconds_per_output_token=decode_fixed,
        decode_seconds_per_context_token_step=decode_slope,
        state_fixed_bytes=sourced_value("state_fixed_bytes", "bytes"),
        state_bytes_per_token=sourced_value("state_bytes_per_token", "bytes/token"),
        memory_capacity_bytes=sourced_value("memory_capacity_bytes", "bytes"),
    )


def load_c64f_runtime_profiles(
    artifact_path: str | Path | None = None,
) -> dict[str, ValidatedRuntimeCostProfile]:
    """Load only the exact accepted C6.4f artifact; no fitting/materialization occurs."""

    path = _default_artifact_path() if artifact_path is None else Path(artifact_path)
    raw = path.read_bytes()
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    _require_exact(observed_sha256, C64F_ARTIFACT_SHA256, "C6.4f artifact SHA-256")

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("C6.4f artifact is not valid JSON") from exc
    result = _require_mapping(result, "C6.4f artifact")

    _require_exact(result.get("schema"), C64F_RESULT_SCHEMA, "schema")
    _require_exact(result.get("result_kind"), C64F_RESULT_KIND, "result_kind")
    _require_exact(result.get("representation_id"), C64F_REPRESENTATION_ID, "representation_id")
    _require_exact(result.get("evidence_class"), C64F_EVIDENCE_CLASS, "evidence_class")
    _require_exact(result.get("decision"), C64F_DECISION, "decision")
    _require_exact(
        result.get("scientific_fingerprint"),
        C64F_SCIENTIFIC_FINGERPRINT,
        "scientific_fingerprint",
    )
    boundary = _require_mapping(result.get("boundary_accounting"), "boundary_accounting")
    _require_exact(boundary.get("holdout_claim"), False, "boundary_accounting.holdout_claim")
    _require_exact(
        boundary.get("out_of_domain_generalization_claim"),
        False,
        "boundary_accounting.out_of_domain_generalization_claim",
    )
    _require_exact(
        boundary.get("validation_mode"),
        "EXHAUSTIVE_FINITE_DECLARED_DOMAIN_EQUIVALENCE",
        "boundary_accounting.validation_mode",
    )
    _require_exact(result.get("post_hoc_repairs"), [], "post_hoc_repairs")

    without_fingerprint = dict(result)
    without_fingerprint.pop("scientific_fingerprint", None)
    recomputed = hashlib.sha256(
        _canonical_json(without_fingerprint).encode("utf-8")
    ).hexdigest()
    _require_exact(
        recomputed,
        C64F_SCIENTIFIC_FINGERPRINT,
        "recomputed scientific_fingerprint",
    )

    hardware = result.get("hardware")
    if not isinstance(hardware, list) or len(hardware) != 2:
        raise ValueError("C6.4f artifact must contain exactly two hardware records")
    profiles = {
        profile.hardware_id: profile
        for profile in (
            _runtime_profile_from_hardware(_require_mapping(item, "hardware record"))
            for item in hardware
        )
    }
    if set(profiles) != C64G_SUPPORTED_HARDWARE_IDS:
        raise ValueError("C6.4f artifact hardware family drift")
    return profiles


def load_c64f_runtime_profile(
    hardware_id: str,
    artifact_path: str | Path | None = None,
) -> ValidatedRuntimeCostProfile:
    if hardware_id not in C64G_SUPPORTED_HARDWARE_IDS:
        raise ValueError("hardware_id is outside the C6.4f accepted family")
    return load_c64f_runtime_profiles(artifact_path)[hardware_id]


def estimate_validated_runtime_cost(
    profile: ValidatedRuntimeCostProfile,
    workload: InferenceCostWorkload,
) -> ValidatedRuntimeCostEstimate:
    if not isinstance(profile, ValidatedRuntimeCostProfile):
        raise TypeError("profile must be ValidatedRuntimeCostProfile")
    if not isinstance(workload, InferenceCostWorkload):
        raise TypeError("workload must be InferenceCostWorkload")

    prefill = profile.prefill_seconds(workload.input_tokens)
    if workload.output_tokens > 0:
        if workload.input_tokens == 0:
            raise ValueError("decode evaluation requires positive context in carried C6.3 domain")
        if workload.input_tokens + workload.output_tokens > C64G_MAX_MODEL_LENGTH:
            raise ValueError("decode composition outside C6.3 accepted max-model-length domain")
    steps = (
        workload.output_tokens * workload.input_tokens
        + workload.output_tokens * (workload.output_tokens - 1) // 2
    )
    decode = 0.0 if workload.output_tokens == 0 else (
        workload.output_tokens * profile.decode_fixed_seconds_per_output_token
        + profile.decode_seconds_per_context_token_step * steps
    )
    recompute_tokens = workload.input_tokens - workload.reusable_prefix_tokens
    recompute = profile.prefill_seconds(recompute_tokens)
    state = profile.state_fixed_bytes + profile.state_bytes_per_token * workload.state_tokens
    transfer = profile.transfer_seconds(state)
    memory_fraction = state / profile.memory_capacity_bytes
    values = (prefill, decode, recompute, transfer, state, memory_fraction)
    if not all(math.isfinite(value) and value >= 0 for value in values):
        raise ValueError("validated runtime cost evaluation produced an invalid result")

    return ValidatedRuntimeCostEstimate(
        profile_id=profile.profile_id,
        hardware_id=profile.hardware_id,
        source_profile_fingerprint=profile.source_profile_fingerprint,
        materialized_domain_fingerprint=profile.materialized_domain_fingerprint,
        scientific_fingerprint=profile.scientific_fingerprint,
        artifact_sha256=profile.artifact_sha256,
        prefill_seconds=prefill,
        decode_seconds=decode,
        recompute_seconds=recompute,
        transfer_seconds=transfer,
        state_bytes=state,
        memory_capacity_fraction=memory_fraction,
        recompute_tokens=recompute_tokens,
        decode_context_token_steps=steps,
    )


def enqueue_validated_inference_task(
    resources: ResourceModel,
    profile: ValidatedRuntimeCostProfile,
    workload: InferenceCostWorkload,
    *,
    phase: ValidatedComputePhase,
    worker_id: str,
    task_id: str,
) -> ScheduledValidatedInference:
    """Schedule one explicit compute phase; transfer remains ResourceModel networking."""

    if not isinstance(resources, ResourceModel):
        raise TypeError("resources must be ResourceModel")
    if not isinstance(phase, ValidatedComputePhase):
        raise TypeError("phase must be ValidatedComputePhase")
    estimate = estimate_validated_runtime_cost(profile, workload)
    duration = {
        ValidatedComputePhase.PREFILL: estimate.prefill_seconds,
        ValidatedComputePhase.DECODE: estimate.decode_seconds,
        ValidatedComputePhase.RECOMPUTE: estimate.recompute_seconds,
    }[phase]
    task = resources.enqueue_task(
        worker_id,
        task_id,
        duration=duration,
    )
    return ScheduledValidatedInference(phase=phase, task=task, estimate=estimate)
