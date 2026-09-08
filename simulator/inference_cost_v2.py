from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from .calibration_validation import (
    VIDUR_PINNED_COMMIT,
    ReferenceKind,
    ReferencePartition,
)
from .inference_cost import (
    InferenceCostWorkload,
    ParameterSourceClass,
    SourcedScalar,
)


C64A_CURVE_SCHEMA = "cadi.c6.4a.piecewise-linear-cost-curve.v1"
C64A_PROFILE_SCHEMA = "cadi.c6.4a.cost-profile-v2.v1"
C64A_ESTIMATE_SCHEMA = "cadi.c6.4a.cost-estimate-v2.v1"
C64A_BOUNDARY_SCHEMA = "cadi.c6.4a.fresh-adequacy-boundary.v1"
C64A_REPRESENTATION_ID = "cadi.c6.cost-representation.v2.curves+decode-step-affine"
C64A_KNOT_PARTITION = "C6.3_FIT"
C64A_FRESH_REFERENCE_EVIDENCE = "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"

C64A_FRESH_PREFILL_AXES = (127, 257, 509, 1021, 2039, 3079, 4001)
C64A_FRESH_TRANSFER_AXES = (
    8192,
    32768,
    131072,
    524288,
    2105344,
    8396800,
    33562624,
    58728448,
)


@dataclass(frozen=True, slots=True)
class CurveKnot:
    """One source-backed knot admitted to a C6.4 replacement curve.

    C6.4a deliberately permits only the already-designated C6.3 FIT partition as
    knot evidence. A C6.3 VALIDATION point cannot be converted into a knot by
    changing a label at a later stage.
    """

    axis_value: int
    seconds: SourcedScalar
    source_point_id: str
    source_partition: str = C64A_KNOT_PARTITION

    def __post_init__(self) -> None:
        if not isinstance(self.axis_value, int) or isinstance(self.axis_value, bool):
            raise TypeError("axis_value must be an integer")
        if self.axis_value <= 0:
            raise ValueError("axis_value must be positive")
        if not isinstance(self.seconds, SourcedScalar):
            raise TypeError("seconds must be SourcedScalar")
        if self.seconds.unit != "seconds":
            raise ValueError("curve knot seconds unit must be 'seconds'")
        if (
            self.seconds.provenance.source_class
            is not ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE
        ):
            raise ValueError("replacement curve knots must be P-SRC2 evidence")
        if self.seconds.value < 0:
            raise ValueError("curve knot seconds must be non-negative")
        if (
            self.seconds.sensitivity is not None
            and self.seconds.sensitivity.low < 0
        ):
            raise ValueError("curve knot sensitivity must remain non-negative")
        _require_nonempty(self.source_point_id, "source_point_id")
        if self.source_partition != C64A_KNOT_PARTITION:
            raise ValueError("replacement curve knots must come only from C6.3 FIT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_value": self.axis_value,
            "seconds": self.seconds.to_dict(),
            "source_point_id": self.source_point_id,
            "source_partition": self.source_partition,
        }


@dataclass(frozen=True, slots=True)
class PiecewiseLinearCostCurve:
    curve_id: str
    axis_unit: str
    knots: tuple[CurveKnot, ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.curve_id, "curve_id")
        if self.axis_unit not in {"input-tokens", "bytes"}:
            raise ValueError("axis_unit must be 'input-tokens' or 'bytes'")
        if not isinstance(self.knots, tuple) or len(self.knots) < 2:
            raise ValueError("piecewise-linear curve requires at least two knots")
        if not all(isinstance(item, CurveKnot) for item in self.knots):
            raise TypeError("knots must contain CurveKnot values")
        axes = [item.axis_value for item in self.knots]
        if axes != sorted(axes) or len(axes) != len(set(axes)):
            raise ValueError("curve knot axes must be strictly increasing and unique")
        point_ids = [item.source_point_id for item in self.knots]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("curve knot source point IDs must be unique")

    @property
    def minimum_axis(self) -> int:
        return self.knots[0].axis_value

    @property
    def maximum_axis(self) -> int:
        return self.knots[-1].axis_value

    def evaluate(self, axis_value: float) -> float:
        axis = _finite_nonnegative(axis_value, "axis_value")
        if axis < self.minimum_axis or axis > self.maximum_axis:
            raise ValueError(
                f"curve evaluation would extrapolate outside [{self.minimum_axis}, "
                f"{self.maximum_axis}] {self.axis_unit}"
            )

        for knot in self.knots:
            if axis == knot.axis_value:
                return knot.seconds.value

        for left, right in zip(self.knots, self.knots[1:]):
            if left.axis_value < axis < right.axis_value:
                fraction = (axis - left.axis_value) / (
                    right.axis_value - left.axis_value
                )
                value = math.fsum(
                    [
                        left.seconds.value,
                        fraction * (right.seconds.value - left.seconds.value),
                    ]
                )
                if not math.isfinite(value) or value < 0:
                    raise ValueError("curve interpolation produced invalid seconds")
                return value

        raise AssertionError("curve interpolation failed to bracket an in-domain axis")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C64A_CURVE_SCHEMA,
            "curve_id": self.curve_id,
            "axis_unit": self.axis_unit,
            "interpolation": "piecewise-linear",
            "extrapolation": "forbidden",
            "knots": [item.to_dict() for item in self.knots],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FreshAdequacyBoundary:
    reference_kind: ReferenceKind
    axis_unit: str
    axes: tuple[int, ...]
    source_commit: str = VIDUR_PINNED_COMMIT
    evidence_class: str = C64A_FRESH_REFERENCE_EVIDENCE

    def __post_init__(self) -> None:
        if self.reference_kind not in {
            ReferenceKind.COLD_PREFILL,
            ReferenceKind.POINT_TO_POINT_TRANSFER,
        }:
            raise ValueError("fresh boundary exists only for revised C6.4a families")
        expected_unit = (
            "input-tokens"
            if self.reference_kind is ReferenceKind.COLD_PREFILL
            else "bytes"
        )
        if self.axis_unit != expected_unit:
            raise ValueError(f"fresh boundary axis_unit must be {expected_unit!r}")
        if not isinstance(self.axes, tuple) or not self.axes:
            raise ValueError("fresh boundary axes must be a non-empty tuple")
        if any(
            not isinstance(axis, int) or isinstance(axis, bool) or axis <= 0
            for axis in self.axes
        ):
            raise ValueError("fresh boundary axes must be positive integers")
        if tuple(sorted(self.axes)) != self.axes or len(set(self.axes)) != len(self.axes):
            raise ValueError("fresh boundary axes must be strictly increasing and unique")
        expected_axes = (
            C64A_FRESH_PREFILL_AXES
            if self.reference_kind is ReferenceKind.COLD_PREFILL
            else C64A_FRESH_TRANSFER_AXES
        )
        if self.axes != expected_axes:
            raise ValueError("fresh boundary axes differ from the frozen C6.4a boundary")
        if self.source_commit != VIDUR_PINNED_COMMIT:
            raise ValueError("fresh boundary source commit must remain pinned to Vidur")
        if self.evidence_class != C64A_FRESH_REFERENCE_EVIDENCE:
            raise ValueError("fresh boundary evidence class is frozen")

    def to_dict(self) -> dict[str, Any]:
        # Deliberately contains identities only. C6.4a must not serialize or
        # compute fresh reference timing values.
        return {
            "schema": C64A_BOUNDARY_SCHEMA,
            "reference_kind": self.reference_kind.value,
            "axis_unit": self.axis_unit,
            "axes": list(self.axes),
            "source_commit": self.source_commit,
            "evidence_class": self.evidence_class,
            "contains_reference_timings": False,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )


C64A_FRESH_PREFILL_BOUNDARY = FreshAdequacyBoundary(
    reference_kind=ReferenceKind.COLD_PREFILL,
    axis_unit="input-tokens",
    axes=C64A_FRESH_PREFILL_AXES,
)
C64A_FRESH_TRANSFER_BOUNDARY = FreshAdequacyBoundary(
    reference_kind=ReferenceKind.POINT_TO_POINT_TRANSFER,
    axis_unit="bytes",
    axes=C64A_FRESH_TRANSFER_AXES,
)


def verify_fresh_boundary_against_c63_partition(
    boundary: FreshAdequacyBoundary,
    partition: ReferencePartition,
) -> None:
    """Fail closed unless every fresh axis is unseen and FIT-bracketed.

    This check uses only source point identity and axis membership; it never reads
    held-out error values or fresh reference timings.
    """

    if not isinstance(boundary, FreshAdequacyBoundary):
        raise TypeError("boundary must be FreshAdequacyBoundary")
    if not isinstance(partition, ReferencePartition):
        raise TypeError("partition must be ReferencePartition")
    if partition.kind is not boundary.reference_kind:
        raise ValueError("boundary reference kind does not match partition")

    observed_axes = {point.axis_value for point in partition.fit + partition.validation}
    fit_axes = tuple(sorted(point.axis_value for point in partition.fit))
    for axis in boundary.axes:
        if axis in observed_axes:
            raise ValueError(f"fresh boundary axis {axis} was already present in C6.3")
        lower = any(value < axis for value in fit_axes)
        upper = any(value > axis for value in fit_axes)
        if not lower or not upper:
            raise ValueError(
                f"fresh boundary axis {axis} is not bracketed by C6.3 FIT knots"
            )


@dataclass(frozen=True, slots=True)
class RevisedInferenceCostProfile:
    """C6.4a machine-readable representation contract.

    This type defines mechanics only. C6.4a intentionally ships no A100/H100
    instance because replacement calibration and fresh adequacy belong to C6.4b.
    """

    profile_id: str
    model_id: str
    hardware_id: str
    prefill_curve: PiecewiseLinearCostCurve
    decode_fixed_seconds_per_output_token: SourcedScalar
    decode_seconds_per_context_token_step: SourcedScalar
    state_fixed_bytes: SourcedScalar
    state_bytes_per_token: SourcedScalar
    memory_capacity_bytes: SourcedScalar
    transfer_curve: PiecewiseLinearCostCurve
    representation_id: str = C64A_REPRESENTATION_ID

    def __post_init__(self) -> None:
        for name in ("profile_id", "model_id", "hardware_id"):
            _require_nonempty(getattr(self, name), name)
        if self.representation_id != C64A_REPRESENTATION_ID:
            raise ValueError("representation_id must equal the frozen C6.4a representation")
        if not isinstance(self.prefill_curve, PiecewiseLinearCostCurve):
            raise TypeError("prefill_curve must be PiecewiseLinearCostCurve")
        if self.prefill_curve.axis_unit != "input-tokens":
            raise ValueError("prefill_curve axis must use input-tokens")
        if not isinstance(self.transfer_curve, PiecewiseLinearCostCurve):
            raise TypeError("transfer_curve must be PiecewiseLinearCostCurve")
        if self.transfer_curve.axis_unit != "bytes":
            raise ValueError("transfer_curve axis must use bytes")

        expected_units = {
            "decode_fixed_seconds_per_output_token": "seconds/output-token",
            "decode_seconds_per_context_token_step": "seconds/context-token-step",
            "state_fixed_bytes": "bytes",
            "state_bytes_per_token": "bytes/token",
            "memory_capacity_bytes": "bytes",
        }
        for name, unit in expected_units.items():
            value = getattr(self, name)
            if not isinstance(value, SourcedScalar):
                raise TypeError(f"{name} must be SourcedScalar")
            if value.unit != unit:
                raise ValueError(f"{name} unit must be {unit!r}")
            if value.value < 0:
                raise ValueError(f"{name} must be non-negative")
            if value.sensitivity is not None and value.sensitivity.low < 0:
                raise ValueError(f"{name} sensitivity must remain non-negative")
        if self.memory_capacity_bytes.value <= 0:
            raise ValueError("memory_capacity_bytes must be positive")
        if (
            self.memory_capacity_bytes.sensitivity is not None
            and self.memory_capacity_bytes.sensitivity.low <= 0
        ):
            raise ValueError("memory_capacity_bytes sensitivity must remain positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C64A_PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "model_id": self.model_id,
            "hardware_id": self.hardware_id,
            "representation_id": self.representation_id,
            "prefill_curve": self.prefill_curve.to_dict(),
            "decode_fixed_seconds_per_output_token": (
                self.decode_fixed_seconds_per_output_token.to_dict()
            ),
            "decode_seconds_per_context_token_step": (
                self.decode_seconds_per_context_token_step.to_dict()
            ),
            "state_fixed_bytes": self.state_fixed_bytes.to_dict(),
            "state_bytes_per_token": self.state_bytes_per_token.to_dict(),
            "memory_capacity_bytes": self.memory_capacity_bytes.to_dict(),
            "transfer_curve": self.transfer_curve.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RevisedInferenceCostEstimate:
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
            "schema": C64A_ESTIMATE_SCHEMA,
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


def estimate_revised_inference_cost(
    profile: RevisedInferenceCostProfile,
    workload: InferenceCostWorkload,
) -> RevisedInferenceCostEstimate:
    """Evaluate the frozen C6.4a mechanics without event-simulator integration."""

    if not isinstance(profile, RevisedInferenceCostProfile):
        raise TypeError("profile must be RevisedInferenceCostProfile")
    if not isinstance(workload, InferenceCostWorkload):
        raise TypeError("workload must be InferenceCostWorkload")

    prefill_seconds = (
        0.0
        if workload.input_tokens == 0
        else profile.prefill_curve.evaluate(workload.input_tokens)
    )

    decode_steps = (
        workload.output_tokens * workload.input_tokens
        + workload.output_tokens * (workload.output_tokens - 1) // 2
    )
    decode_seconds = (
        0.0
        if workload.output_tokens == 0
        else math.fsum(
            [
                workload.output_tokens
                * profile.decode_fixed_seconds_per_output_token.value,
                profile.decode_seconds_per_context_token_step.value * decode_steps,
            ]
        )
    )

    recompute_tokens = workload.input_tokens - workload.reusable_prefix_tokens
    recompute_seconds = (
        0.0
        if recompute_tokens == 0
        else profile.prefill_curve.evaluate(recompute_tokens)
    )

    state_bytes = math.fsum(
        [
            profile.state_fixed_bytes.value,
            profile.state_bytes_per_token.value * workload.state_tokens,
        ]
    )
    transfer_seconds = (
        0.0 if state_bytes == 0 else profile.transfer_curve.evaluate(state_bytes)
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
        raise ValueError("revised cost evaluation produced an invalid result")

    return RevisedInferenceCostEstimate(
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


def knot_point_ids(curve: PiecewiseLinearCostCurve) -> tuple[str, ...]:
    if not isinstance(curve, PiecewiseLinearCostCurve):
        raise TypeError("curve must be PiecewiseLinearCostCurve")
    return tuple(knot.source_point_id for knot in curve.knots)


def assert_knots_match_c63_fit(
    curve: PiecewiseLinearCostCurve,
    partition: ReferencePartition,
) -> None:
    """Reject any replacement curve containing old VALIDATION or unknown points."""

    if not isinstance(curve, PiecewiseLinearCostCurve):
        raise TypeError("curve must be PiecewiseLinearCostCurve")
    if not isinstance(partition, ReferencePartition):
        raise TypeError("partition must be ReferencePartition")
    expected_fit = {point.point_id for point in partition.fit}
    supplied = set(knot_point_ids(curve))
    validation_ids = {point.point_id for point in partition.validation}
    leaked = sorted(supplied & validation_ids)
    if leaked:
        raise ValueError(f"C6.3 VALIDATION points cannot become curve knots: {leaked}")
    unknown = sorted(supplied - expected_fit)
    if unknown:
        raise ValueError(f"curve contains non-FIT/unknown source point IDs: {unknown}")
    if supplied != expected_fit:
        missing = sorted(expected_fit - supplied)
        raise ValueError(f"curve must use the complete frozen C6.3 FIT knot set: {missing}")


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
