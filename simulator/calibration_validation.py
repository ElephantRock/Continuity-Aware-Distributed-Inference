from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import re
from typing import Mapping, Sequence


C6_VALIDATION_REPORT_SCHEMA = "cadi.c6.validation-report.v1"
VIDUR_PINNED_REPOSITORY = "microsoft/vidur"
VIDUR_PINNED_COMMIT = "abae7f63aa857300f5cdc6f5e0d27860cd24721b"
VIDUR_LLAMA2_7B_NUM_LAYERS = 32
C6_VALIDATION_MAPE_LIMIT = 0.05
C6_VALIDATION_MAX_APE_LIMIT = 0.10
C6_MIN_FIT_POINTS = 2
C6_MIN_VALIDATION_POINTS = 2
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


class ReferenceKind(str, Enum):
    COLD_PREFILL = "COLD_PREFILL"
    SINGLE_TOKEN_DECODE = "SINGLE_TOKEN_DECODE"
    POINT_TO_POINT_TRANSFER = "POINT_TO_POINT_TRANSFER"


class AdequacyDecision(str, Enum):
    ADEQUATE_WITHIN_DECLARED_DOMAIN = "ADEQUATE_WITHIN_DECLARED_DOMAIN"
    INADEQUATE_REVISE_REPRESENTATION = "INADEQUATE_REVISE_REPRESENTATION"


@dataclass(frozen=True, slots=True)
class VidurReferenceDomain:
    model_id: str
    hardware_ids: tuple[str, ...]
    num_layers: int
    tensor_parallel_size: int
    pipeline_stages: int
    attention_block_size: int
    max_model_len: int
    attention_backend: str

    def __post_init__(self) -> None:
        _require_nonempty(self.model_id, "model_id")
        if not self.hardware_ids or not all(
            isinstance(value, str) and value.strip() for value in self.hardware_ids
        ):
            raise ValueError("hardware_ids must contain non-empty strings")
        if len(set(self.hardware_ids)) != len(self.hardware_ids):
            raise ValueError("hardware_ids must be unique")
        for name in (
            "num_layers",
            "tensor_parallel_size",
            "pipeline_stages",
            "attention_block_size",
            "max_model_len",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        _require_nonempty(self.attention_backend, "attention_backend")


VIDUR_LLAMA2_7B_TP1_DOMAIN = VidurReferenceDomain(
    model_id="meta-llama/Llama-2-7b-hf",
    hardware_ids=("a100-80gb", "h100-80gb"),
    num_layers=VIDUR_LLAMA2_7B_NUM_LAYERS,
    tensor_parallel_size=1,
    pipeline_stages=1,
    attention_block_size=16,
    max_model_len=4096,
    attention_backend="FLASH_ATTENTION",
)


@dataclass(frozen=True, slots=True)
class VidurComputeComponents:
    """Per-layer median timings, in milliseconds, for the TP1/PP1 domain.

    `attention_kernel_ms` is exactly one of the cold-prefill or single-token
    decode attention-kernel medians. CPU overhead, embedding/final softmax,
    tensor-parallel communication, and pipeline-parallel communication are not
    part of this composition.
    """

    attn_pre_proj_ms: float
    attn_post_proj_ms: float
    attn_rope_ms: float
    attn_kv_cache_save_ms: float
    attention_kernel_ms: float
    input_layernorm_ms: float
    post_attention_layernorm_ms: float
    mlp_up_proj_ms: float
    mlp_down_proj_ms: float
    mlp_act_ms: float
    add_ms: float

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = _finite_nonnegative(getattr(self, name), name)
            object.__setattr__(self, name, value)

    @property
    def per_layer_ms(self) -> float:
        return sum(getattr(self, name) for name in self.__dataclass_fields__)


def compose_vidur_llama2_7b_tp1_model_seconds(
    components: VidurComputeComponents,
) -> float:
    """Reproduce Vidur `ExecutionTime.model_time` for the declared TP1/PP1 domain."""

    if not isinstance(components, VidurComputeComponents):
        raise TypeError("components must be VidurComputeComponents")
    return components.per_layer_ms * VIDUR_LLAMA2_7B_NUM_LAYERS * 1e-3


@dataclass(frozen=True, slots=True)
class SourceArtifactRef:
    path: str
    git_blob_sha1: str

    def __post_init__(self) -> None:
        _require_nonempty(self.path, "path")
        if not _SHA1_RE.fullmatch(self.git_blob_sha1):
            raise ValueError("git_blob_sha1 must be 40 lowercase hexadecimal characters")

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "git_blob_sha1": self.git_blob_sha1}


@dataclass(frozen=True, slots=True)
class CalibrationReferencePoint:
    point_id: str
    hardware_id: str
    kind: ReferenceKind
    axis_value: int
    observed_seconds: float
    derivation_id: str
    source_commit: str
    source_artifacts: tuple[SourceArtifactRef, ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.point_id, "point_id")
        if self.hardware_id not in VIDUR_LLAMA2_7B_TP1_DOMAIN.hardware_ids:
            raise ValueError("hardware_id is outside the declared C6.3 Vidur domain")
        if not isinstance(self.kind, ReferenceKind):
            raise TypeError("kind must be ReferenceKind")
        if not isinstance(self.axis_value, int) or isinstance(self.axis_value, bool):
            raise TypeError("axis_value must be an integer")
        if self.axis_value <= 0:
            raise ValueError("axis_value must be positive")
        observed = _finite_nonnegative(self.observed_seconds, "observed_seconds")
        object.__setattr__(self, "observed_seconds", observed)
        _require_nonempty(self.derivation_id, "derivation_id")
        if self.source_commit != VIDUR_PINNED_COMMIT:
            raise ValueError("source_commit must equal the pinned C6.2 Vidur commit")
        if not isinstance(self.source_artifacts, tuple) or not self.source_artifacts:
            raise ValueError("source_artifacts must be a non-empty tuple")
        if not all(isinstance(item, SourceArtifactRef) for item in self.source_artifacts):
            raise TypeError("source_artifacts must contain SourceArtifactRef values")
        paths = [item.path for item in self.source_artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("source_artifact paths must be unique")

    @property
    def axis_unit(self) -> str:
        if self.kind is ReferenceKind.COLD_PREFILL:
            return "input-tokens"
        if self.kind is ReferenceKind.SINGLE_TOKEN_DECODE:
            return "context-tokens"
        return "bytes"

    def to_dict(self) -> dict[str, object]:
        artifacts = sorted(
            self.source_artifacts, key=lambda item: (item.path, item.git_blob_sha1)
        )
        return {
            "point_id": self.point_id,
            "hardware_id": self.hardware_id,
            "kind": self.kind.value,
            "axis_value": self.axis_value,
            "axis_unit": self.axis_unit,
            "observed_seconds": self.observed_seconds,
            "derivation_id": self.derivation_id,
            "source_repository": VIDUR_PINNED_REPOSITORY,
            "source_commit": self.source_commit,
            "source_artifacts": [item.to_dict() for item in artifacts],
        }


@dataclass(frozen=True, slots=True)
class ReferencePartition:
    hardware_id: str
    kind: ReferenceKind
    fit: tuple[CalibrationReferencePoint, ...]
    validation: tuple[CalibrationReferencePoint, ...]

    def __post_init__(self) -> None:
        if self.hardware_id not in VIDUR_LLAMA2_7B_TP1_DOMAIN.hardware_ids:
            raise ValueError("hardware_id is outside the declared C6.3 Vidur domain")
        if not isinstance(self.kind, ReferenceKind):
            raise TypeError("kind must be ReferenceKind")
        if len(self.fit) < C6_MIN_FIT_POINTS:
            raise ValueError("reference partition requires at least two FIT points")
        if len(self.validation) < C6_MIN_VALIDATION_POINTS:
            raise ValueError("reference partition requires at least two VALIDATION points")


def split_reference_family(
    points: Sequence[CalibrationReferencePoint],
) -> ReferencePartition:
    """Canonicalize one family and assign even ordinals to FIT, odd to VALIDATION."""

    materialized = tuple(points)
    if len(materialized) < C6_MIN_FIT_POINTS + C6_MIN_VALIDATION_POINTS:
        raise ValueError("reference family requires at least four points")
    if not all(isinstance(point, CalibrationReferencePoint) for point in materialized):
        raise TypeError("points must contain CalibrationReferencePoint values")

    hardware_ids = {point.hardware_id for point in materialized}
    kinds = {point.kind for point in materialized}
    if len(hardware_ids) != 1 or len(kinds) != 1:
        raise ValueError("reference family must have one hardware_id and one kind")

    point_ids = [point.point_id for point in materialized]
    if len(point_ids) != len(set(point_ids)):
        raise ValueError("reference point IDs must be unique within a family")
    axis_values = [point.axis_value for point in materialized]
    if len(axis_values) != len(set(axis_values)):
        raise ValueError("reference axis values must be unique within a family")

    ordered = tuple(sorted(materialized, key=lambda point: (point.axis_value, point.point_id)))
    fit = tuple(point for index, point in enumerate(ordered) if index % 2 == 0)
    validation = tuple(point for index, point in enumerate(ordered) if index % 2 == 1)

    return ReferencePartition(
        hardware_id=ordered[0].hardware_id,
        kind=ordered[0].kind,
        fit=fit,
        validation=validation,
    )


@dataclass(frozen=True, slots=True)
class PredictionError:
    point_id: str
    observed_seconds: float
    predicted_seconds: float
    signed_error_seconds: float
    absolute_error_seconds: float
    absolute_percentage_error: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "point_id": self.point_id,
            "observed_seconds": self.observed_seconds,
            "predicted_seconds": self.predicted_seconds,
            "signed_error_seconds": self.signed_error_seconds,
            "absolute_error_seconds": self.absolute_error_seconds,
            "absolute_percentage_error": self.absolute_percentage_error,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    hardware_id: str
    kind: ReferenceKind
    errors: tuple[PredictionError, ...]
    mae_seconds: float
    mape: float | None
    max_ape: float | None
    zero_reference_count: int
    decision: AdequacyDecision

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": C6_VALIDATION_REPORT_SCHEMA,
            "hardware_id": self.hardware_id,
            "kind": self.kind.value,
            "thresholds": {
                "mape_limit": C6_VALIDATION_MAPE_LIMIT,
                "max_ape_limit": C6_VALIDATION_MAX_APE_LIMIT,
                "zero_reference_requires_exact_zero_error": True,
            },
            "errors": [error.to_dict() for error in self.errors],
            "mae_seconds": self.mae_seconds,
            "mape": self.mape,
            "max_ape": self.max_ape,
            "zero_reference_count": self.zero_reference_count,
            "decision": self.decision.value,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )


def evaluate_reference_predictions(
    partition: ReferencePartition,
    predictions: Mapping[str, float],
) -> ValidationReport:
    """Evaluate exactly the held-out points and fail closed on prediction-key drift."""

    if not isinstance(partition, ReferencePartition):
        raise TypeError("partition must be ReferencePartition")
    expected_ids = {point.point_id for point in partition.validation}
    supplied = dict(predictions)
    if set(supplied) != expected_ids:
        missing = sorted(expected_ids - set(supplied))
        extra = sorted(set(supplied) - expected_ids)
        raise ValueError(f"prediction key mismatch: missing={missing}, extra={extra}")

    errors: list[PredictionError] = []
    nonzero_apes: list[float] = []
    zero_reference_count = 0
    zero_reference_mismatch = False

    for point in partition.validation:
        predicted = _finite_nonnegative(supplied[point.point_id], "predicted_seconds")
        signed = predicted - point.observed_seconds
        absolute = abs(signed)
        if point.observed_seconds == 0:
            ape = None
            zero_reference_count += 1
            if absolute != 0:
                zero_reference_mismatch = True
        else:
            ape = absolute / point.observed_seconds
            nonzero_apes.append(ape)
        errors.append(
            PredictionError(
                point_id=point.point_id,
                observed_seconds=point.observed_seconds,
                predicted_seconds=predicted,
                signed_error_seconds=signed,
                absolute_error_seconds=absolute,
                absolute_percentage_error=ape,
            )
        )

    mae = sum(error.absolute_error_seconds for error in errors) / len(errors)
    mape = (
        None if not nonzero_apes else sum(nonzero_apes) / len(nonzero_apes)
    )
    max_ape = None if not nonzero_apes else max(nonzero_apes)

    adequate = (
        mape is not None
        and max_ape is not None
        and mape <= C6_VALIDATION_MAPE_LIMIT
        and max_ape <= C6_VALIDATION_MAX_APE_LIMIT
        and not zero_reference_mismatch
    )
    decision = (
        AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
        if adequate
        else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION
    )

    return ValidationReport(
        hardware_id=partition.hardware_id,
        kind=partition.kind,
        errors=tuple(errors),
        mae_seconds=mae,
        mape=mape,
        max_ape=max_ape,
        zero_reference_count=zero_reference_count,
        decision=decision,
    )


def _require_nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _finite_nonnegative(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result
