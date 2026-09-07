from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Optional


C6_COST_PROFILE_SCHEMA = "cadi.c6.cost-profile.v1"
C6_COST_ESTIMATE_SCHEMA = "cadi.c6.cost-estimate.v1"


class ParameterSourceClass(str, Enum):
    """Allowed provenance classes for C6 physical/model parameters."""

    PUBLISHED_OR_VALIDATED_PROFILE = "PUBLISHED_OR_VALIDATED_PROFILE"
    DIRECT_CPU_MEASUREMENT = "DIRECT_CPU_MEASUREMENT"
    SYNTHETIC_SENSITIVITY = "SYNTHETIC_SENSITIVITY"


@dataclass(frozen=True, slots=True)
class ParameterProvenance:
    source_class: ParameterSourceClass
    reference: str
    note: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_class, ParameterSourceClass):
            raise TypeError("source_class must be ParameterSourceClass")
        _require_nonempty(self.reference, "reference")
        if self.note is not None:
            _require_nonempty(self.note, "note")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_class": self.source_class.value,
            "reference": self.reference,
        }
        if self.note is not None:
            result["note"] = self.note
        return result


@dataclass(frozen=True, slots=True)
class SensitivityRange:
    low: float
    high: float

    def __post_init__(self) -> None:
        low = _finite(self.low, "low")
        high = _finite(self.high, "high")
        if low >= high:
            raise ValueError("sensitivity range requires low < high")
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)

    def to_dict(self) -> dict[str, float]:
        return {"low": self.low, "high": self.high}


@dataclass(frozen=True, slots=True)
class SourcedScalar:
    """One explicitly sourced scalar parameter.

    Synthetic parameters are permitted only when accompanied by a non-degenerate
    sensitivity range containing the nominal value.
    """

    value: float
    unit: str
    provenance: ParameterProvenance
    sensitivity: Optional[SensitivityRange] = None

    def __post_init__(self) -> None:
        value = _finite(self.value, "value")
        _require_nonempty(self.unit, "unit")
        if not isinstance(self.provenance, ParameterProvenance):
            raise TypeError("provenance must be ParameterProvenance")
        if self.sensitivity is not None and not isinstance(
            self.sensitivity, SensitivityRange
        ):
            raise TypeError("sensitivity must be SensitivityRange")
        if self.provenance.source_class is ParameterSourceClass.SYNTHETIC_SENSITIVITY:
            if self.sensitivity is None:
                raise ValueError(
                    "synthetic parameter requires an explicit sensitivity range"
                )
        if self.sensitivity is not None and not (
            self.sensitivity.low <= value <= self.sensitivity.high
        ):
            raise ValueError("nominal value must lie within sensitivity range")
        object.__setattr__(self, "value", value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "provenance": self.provenance.to_dict(),
            "sensitivity": (
                None if self.sensitivity is None else self.sensitivity.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class ModelValidation:
    metric: str
    error_value: float
    unit: str
    reference: str

    def __post_init__(self) -> None:
        _require_nonempty(self.metric, "metric")
        error = _finite_nonnegative(self.error_value, "error_value")
        _require_nonempty(self.unit, "unit")
        _require_nonempty(self.reference, "reference")
        object.__setattr__(self, "error_value", error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "error_value": self.error_value,
            "unit": self.unit,
            "reference": self.reference,
        }


@dataclass(frozen=True, slots=True)
class InferenceCostProfile:
    """Provider-neutral EV3 inference-cost profile.

    C6.1 deliberately fixes transparent mechanics, not a physical calibration.
    Every numeric parameter is individually source-tagged.
    """

    profile_id: str
    model_id: str
    hardware_id: str
    representation_id: str
    prefill_fixed_seconds: SourcedScalar
    prefill_seconds_per_input_token: SourcedScalar
    decode_fixed_seconds: SourcedScalar
    decode_seconds_per_context_token_step: SourcedScalar
    state_fixed_bytes: SourcedScalar
    state_bytes_per_token: SourcedScalar
    memory_capacity_bytes: SourcedScalar
    transfer_bandwidth_bytes_per_second: SourcedScalar
    transfer_latency_seconds: SourcedScalar
    validation: Optional[ModelValidation] = None

    def __post_init__(self) -> None:
        for name in (
            "profile_id",
            "model_id",
            "hardware_id",
            "representation_id",
        ):
            _require_nonempty(getattr(self, name), name)

        expected_units = {
            "prefill_fixed_seconds": "seconds",
            "prefill_seconds_per_input_token": "seconds/input-token",
            "decode_fixed_seconds": "seconds",
            "decode_seconds_per_context_token_step": "seconds/context-token-step",
            "state_fixed_bytes": "bytes",
            "state_bytes_per_token": "bytes/token",
            "memory_capacity_bytes": "bytes",
            "transfer_bandwidth_bytes_per_second": "bytes/second",
            "transfer_latency_seconds": "seconds",
        }
        for field_name, expected_unit in expected_units.items():
            value = getattr(self, field_name)
            if not isinstance(value, SourcedScalar):
                raise TypeError(f"{field_name} must be SourcedScalar")
            if value.unit != expected_unit:
                raise ValueError(
                    f"{field_name} unit must be {expected_unit!r}, got {value.unit!r}"
                )
            if value.value < 0:
                raise ValueError(f"{field_name} must be non-negative")
            if value.sensitivity is not None and value.sensitivity.low < 0:
                raise ValueError(
                    f"{field_name} sensitivity range must remain non-negative"
                )

        if self.memory_capacity_bytes.value <= 0:
            raise ValueError("memory_capacity_bytes must be positive")
        if (
            self.memory_capacity_bytes.sensitivity is not None
            and self.memory_capacity_bytes.sensitivity.low <= 0
        ):
            raise ValueError(
                "memory_capacity_bytes sensitivity range must remain positive"
            )
        if self.transfer_bandwidth_bytes_per_second.value <= 0:
            raise ValueError("transfer_bandwidth_bytes_per_second must be positive")
        if (
            self.transfer_bandwidth_bytes_per_second.sensitivity is not None
            and self.transfer_bandwidth_bytes_per_second.sensitivity.low <= 0
        ):
            raise ValueError(
                "transfer_bandwidth_bytes_per_second sensitivity range must remain positive"
            )
        if self.validation is not None and not isinstance(
            self.validation, ModelValidation
        ):
            raise TypeError("validation must be ModelValidation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C6_COST_PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "model_id": self.model_id,
            "hardware_id": self.hardware_id,
            "representation_id": self.representation_id,
            "prefill_fixed_seconds": self.prefill_fixed_seconds.to_dict(),
            "prefill_seconds_per_input_token": (
                self.prefill_seconds_per_input_token.to_dict()
            ),
            "decode_fixed_seconds": self.decode_fixed_seconds.to_dict(),
            "decode_seconds_per_context_token_step": (
                self.decode_seconds_per_context_token_step.to_dict()
            ),
            "state_fixed_bytes": self.state_fixed_bytes.to_dict(),
            "state_bytes_per_token": self.state_bytes_per_token.to_dict(),
            "memory_capacity_bytes": self.memory_capacity_bytes.to_dict(),
            "transfer_bandwidth_bytes_per_second": (
                self.transfer_bandwidth_bytes_per_second.to_dict()
            ),
            "transfer_latency_seconds": self.transfer_latency_seconds.to_dict(),
            "validation": (
                None if self.validation is None else self.validation.to_dict()
            ),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class InferenceCostWorkload:
    input_tokens: int
    output_tokens: int
    reusable_prefix_tokens: int
    state_tokens: int

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "reusable_prefix_tokens",
            "state_tokens",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.reusable_prefix_tokens > self.input_tokens:
            raise ValueError("reusable_prefix_tokens cannot exceed input_tokens")


@dataclass(frozen=True, slots=True)
class InferenceCostEstimate:
    profile_id: str
    profile_fingerprint: str
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
            "schema": C6_COST_ESTIMATE_SCHEMA,
            "profile_id": self.profile_id,
            "profile_fingerprint": self.profile_fingerprint,
            "prefill_seconds": self.prefill_seconds,
            "decode_seconds": self.decode_seconds,
            "recompute_seconds": self.recompute_seconds,
            "transfer_seconds": self.transfer_seconds,
            "state_bytes": self.state_bytes,
            "memory_capacity_fraction": self.memory_capacity_fraction,
            "recompute_tokens": self.recompute_tokens,
            "decode_context_token_steps": self.decode_context_token_steps,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


def estimate_inference_cost(
    profile: InferenceCostProfile,
    workload: InferenceCostWorkload,
) -> InferenceCostEstimate:
    if not isinstance(profile, InferenceCostProfile):
        raise TypeError("profile must be InferenceCostProfile")
    if not isinstance(workload, InferenceCostWorkload):
        raise TypeError("workload must be InferenceCostWorkload")

    if workload.input_tokens == 0:
        prefill_seconds = 0.0
    else:
        prefill_seconds = (
            profile.prefill_fixed_seconds.value
            + profile.prefill_seconds_per_input_token.value * workload.input_tokens
        )

    decode_steps = (
        workload.output_tokens * workload.input_tokens
        + workload.output_tokens * (workload.output_tokens - 1) // 2
    )
    if workload.output_tokens == 0:
        decode_seconds = 0.0
    else:
        decode_seconds = (
            profile.decode_fixed_seconds.value
            + profile.decode_seconds_per_context_token_step.value * decode_steps
        )

    recompute_tokens = workload.input_tokens - workload.reusable_prefix_tokens
    if recompute_tokens == 0:
        recompute_seconds = 0.0
    else:
        recompute_seconds = (
            profile.prefill_fixed_seconds.value
            + profile.prefill_seconds_per_input_token.value * recompute_tokens
        )

    state_bytes = (
        profile.state_fixed_bytes.value
        + profile.state_bytes_per_token.value * workload.state_tokens
    )
    transfer_seconds = (
        profile.transfer_latency_seconds.value
        + state_bytes / profile.transfer_bandwidth_bytes_per_second.value
    )
    memory_fraction = state_bytes / profile.memory_capacity_bytes.value

    values = (
        prefill_seconds,
        decode_seconds,
        recompute_seconds,
        transfer_seconds,
        state_bytes,
        memory_fraction,
    )
    if not all(math.isfinite(value) and value >= 0 for value in values):
        raise ValueError("cost evaluation produced a non-finite or negative result")

    return InferenceCostEstimate(
        profile_id=profile.profile_id,
        profile_fingerprint=profile.fingerprint,
        prefill_seconds=prefill_seconds,
        decode_seconds=decode_seconds,
        recompute_seconds=recompute_seconds,
        transfer_seconds=transfer_seconds,
        state_bytes=state_bytes,
        memory_capacity_fraction=memory_fraction,
        recompute_tokens=recompute_tokens,
        decode_context_token_steps=decode_steps,
    )


def _require_nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _finite_nonnegative(value: float, name: str) -> float:
    result = _finite(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result
