from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Mapping, Sequence

from .calibration_projection import (
    C6_PROJECTION_AGGREGATION,
    VIDUR_SOURCE_SHA256,
    ProjectionReference,
    derive_vidur_reference_corpus,
    fit_affine_reference_family,
)
from .calibration_validation import (
    C6_THRESHOLD_ULP_BUDGET,
    C6_VALIDATION_MAPE_LIMIT,
    C6_VALIDATION_MAX_APE_LIMIT,
    AdequacyDecision,
    ReferenceKind,
    ReferencePartition,
    ValidationReport,
    evaluate_reference_predictions,
    split_reference_family,
)


C63C_ADEQUACY_SCHEMA = "cadi.c6.3c.representation-adequacy.v1"
C63C_COMPOSITION_SCHEMA = "cadi.c6.3c.decode-composition.v1"
C63C_CORPUS_FINGERPRINTS: dict[str, str] = {
    "a100-80gb": "f1ff902e51952273e8788a79b2c7309048e224b26d6aa19e49726ab4416a7e32",
    "h100-80gb": "0b379d376b30c1152e4d0d429eab6458ef85f91ee130f5e5969e1b757f8e7077",
}
C63C_COMPOSITION_INPUT_CONTEXTS = (32, 256, 1024, 2048, 4032)
C63C_COMPOSITION_OUTPUT_TOKENS = (2, 4, 8, 16, 32)
C63C_MAX_MODEL_LEN = 4096


class FamilyValidationStatus(str, Enum):
    EVALUATED = "EVALUATED"
    NOT_EVALUABLE_INVALID_FIT = "NOT_EVALUABLE_INVALID_FIT"


@dataclass(frozen=True, slots=True)
class AffineFitDiagnostic:
    hardware_id: str
    kind: ReferenceKind
    intercept_seconds: float
    slope_seconds_per_axis_unit: float
    fit_point_ids: tuple[str, ...]
    validation_point_ids: tuple[str, ...]
    violations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.hardware_id not in C63C_CORPUS_FINGERPRINTS:
            raise ValueError("hardware_id is outside the frozen C6.3c domain")
        if not isinstance(self.kind, ReferenceKind):
            raise TypeError("kind must be ReferenceKind")
        for name in ("intercept_seconds", "slope_seconds_per_axis_unit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be numeric")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if len(self.fit_point_ids) < 2 or len(self.validation_point_ids) < 2:
            raise ValueError("diagnostic requires at least two FIT and VALIDATION points")
        if len(set(self.fit_point_ids + self.validation_point_ids)) != (
            len(self.fit_point_ids) + len(self.validation_point_ids)
        ):
            raise ValueError("diagnostic point IDs must be unique")
        allowed = {"NONPOSITIVE_SLOPE", "NEGATIVE_INTERCEPT"}
        if any(item not in allowed for item in self.violations):
            raise ValueError("unknown affine-fit violation")
        if len(set(self.violations)) != len(self.violations):
            raise ValueError("affine-fit violations must be unique")
        object.__setattr__(self, "intercept_seconds", float(self.intercept_seconds))
        object.__setattr__(
            self,
            "slope_seconds_per_axis_unit",
            float(self.slope_seconds_per_axis_unit),
        )

    @property
    def admissible(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, object]:
        return {
            "hardware_id": self.hardware_id,
            "kind": self.kind.value,
            "intercept_seconds": self.intercept_seconds,
            "slope_seconds_per_axis_unit": self.slope_seconds_per_axis_unit,
            "fit_point_ids": list(self.fit_point_ids),
            "validation_point_ids": list(self.validation_point_ids),
            "violations": list(self.violations),
            "admissible": self.admissible,
        }


@dataclass(frozen=True, slots=True)
class FamilyAdequacyRecord:
    fit: AffineFitDiagnostic
    validation_status: FamilyValidationStatus
    validation_report: ValidationReport | None
    decision: AdequacyDecision

    def __post_init__(self) -> None:
        if not isinstance(self.fit, AffineFitDiagnostic):
            raise TypeError("fit must be AffineFitDiagnostic")
        if not isinstance(self.validation_status, FamilyValidationStatus):
            raise TypeError("validation_status must be FamilyValidationStatus")
        if not isinstance(self.decision, AdequacyDecision):
            raise TypeError("decision must be AdequacyDecision")
        if self.fit.admissible:
            if self.validation_status is not FamilyValidationStatus.EVALUATED:
                raise ValueError("admissible fit must be evaluated")
            if not isinstance(self.validation_report, ValidationReport):
                raise ValueError("evaluated fit requires a ValidationReport")
            if self.validation_report.hardware_id != self.fit.hardware_id:
                raise ValueError("validation report hardware does not match fit")
            if self.validation_report.kind is not self.fit.kind:
                raise ValueError("validation report kind does not match fit")
            if self.decision is not self.validation_report.decision:
                raise ValueError("family decision must equal validation report decision")
        else:
            if (
                self.validation_status
                is not FamilyValidationStatus.NOT_EVALUABLE_INVALID_FIT
            ):
                raise ValueError("inadmissible fit must be explicitly not evaluable")
            if self.validation_report is not None:
                raise ValueError("inadmissible fit cannot carry held-out validation metrics")
            if self.decision is not AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION:
                raise ValueError("inadmissible fit must fail representation adequacy")

    def to_dict(self) -> dict[str, object]:
        return {
            "fit": self.fit.to_dict(),
            "validation_status": self.validation_status.value,
            "validation_report": (
                None if self.validation_report is None else self.validation_report.to_dict()
            ),
            "decision": self.decision.value,
        }


@dataclass(frozen=True, slots=True)
class DecodeCompositionPoint:
    input_context_tokens: int
    output_tokens: int
    context_token_steps: int
    source_primitive_sequence_seconds: float
    c6_1_sequence_seconds: float
    signed_error_seconds: float
    absolute_error_seconds: float
    absolute_percentage_error: float
    expected_source_minus_c6_gap_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "input_context_tokens": self.input_context_tokens,
            "output_tokens": self.output_tokens,
            "context_token_steps": self.context_token_steps,
            "source_primitive_sequence_seconds": self.source_primitive_sequence_seconds,
            "c6_1_sequence_seconds": self.c6_1_sequence_seconds,
            "signed_error_seconds": self.signed_error_seconds,
            "absolute_error_seconds": self.absolute_error_seconds,
            "absolute_percentage_error": self.absolute_percentage_error,
            "expected_source_minus_c6_gap_seconds": self.expected_source_minus_c6_gap_seconds,
        }


@dataclass(frozen=True, slots=True)
class DecodeCompositionReport:
    hardware_id: str
    fixed_seconds: float
    slope_seconds_per_context_step: float
    points: tuple[DecodeCompositionPoint, ...]
    mae_seconds: float
    mape: float
    max_ape: float
    decision: AdequacyDecision

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": C63C_COMPOSITION_SCHEMA,
            "evidence_class": "ANALYTICALLY_DERIVED",
            "hardware_id": self.hardware_id,
            "single_token_affine_fit": {
                "fixed_seconds": self.fixed_seconds,
                "slope_seconds_per_context_step": self.slope_seconds_per_context_step,
            },
            "grid": {
                "input_context_tokens": list(C63C_COMPOSITION_INPUT_CONTEXTS),
                "output_tokens": list(C63C_COMPOSITION_OUTPUT_TOKENS),
                "max_model_len": C63C_MAX_MODEL_LEN,
            },
            "algebraic_gap": "source_primitive_sequence - c6_1_sequence = (output_tokens - 1) * fixed_seconds",
            "thresholds": {
                "mape_limit": C6_VALIDATION_MAPE_LIMIT,
                "max_ape_limit": C6_VALIDATION_MAX_APE_LIMIT,
                "floating_boundary_ulp_budget": C6_THRESHOLD_ULP_BUDGET,
            },
            "points": [point.to_dict() for point in self.points],
            "mae_seconds": self.mae_seconds,
            "mape": self.mape,
            "max_ape": self.max_ape,
            "decision": self.decision.value,
        }


@dataclass(frozen=True, slots=True)
class HardwareAdequacyRecord:
    hardware_id: str
    source_sha256: Mapping[str, str]
    corpus_fingerprint: str
    families: tuple[FamilyAdequacyRecord, ...]
    decode_composition: DecodeCompositionReport
    decision: AdequacyDecision

    def __post_init__(self) -> None:
        if self.hardware_id not in C63C_CORPUS_FINGERPRINTS:
            raise ValueError("hardware_id is outside the frozen C6.3c domain")
        if dict(self.source_sha256) != VIDUR_SOURCE_SHA256[self.hardware_id]:
            raise ValueError("source SHA-256 does not match the frozen C6.3b snapshot")
        if self.corpus_fingerprint != C63C_CORPUS_FINGERPRINTS[self.hardware_id]:
            raise ValueError("corpus fingerprint does not match the frozen C6.3b corpus")
        kinds = [item.fit.kind for item in self.families]
        if len(self.families) != len(ReferenceKind) or set(kinds) != set(ReferenceKind):
            raise ValueError("hardware record requires exactly one family record per kind")
        if self.decode_composition.hardware_id != self.hardware_id:
            raise ValueError("decode composition hardware does not match")
        adequate = all(
            item.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
            for item in self.families
        ) and (
            self.decode_composition.decision
            is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
        )
        expected = (
            AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
            if adequate
            else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION
        )
        if self.decision is not expected:
            raise ValueError("hardware decision does not match component decisions")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": C63C_ADEQUACY_SCHEMA,
            "hardware_id": self.hardware_id,
            "source_sha256": dict(sorted(self.source_sha256.items())),
            "corpus_fingerprint": self.corpus_fingerprint,
            "families": [item.to_dict() for item in self.families],
            "decode_composition": self.decode_composition.to_dict(),
            "decision": self.decision.value,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )


def fit_affine_reference_family_diagnostic(
    partition: ReferencePartition,
) -> AffineFitDiagnostic:
    """Mirror the frozen C6.3b FIT-only OLS law without hiding inadmissibility."""

    if not isinstance(partition, ReferencePartition):
        raise TypeError("partition must be ReferencePartition")
    xs = [float(point.axis_value) for point in partition.fit]
    ys = [point.observed_seconds for point in partition.fit]
    if len(set(xs)) < 2:
        raise ValueError("affine fit requires at least two distinct FIT axes")

    x_mean = math.fsum(xs) / len(xs)
    y_mean = math.fsum(ys) / len(ys)
    centered_x = [value - x_mean for value in xs]
    sxx = math.fsum(value * value for value in centered_x)
    if not math.isfinite(sxx) or sxx <= 0:
        raise ValueError("affine fit has non-positive x variance")
    slope = math.fsum(
        dx * (y - y_mean) for dx, y in zip(centered_x, ys, strict=True)
    ) / sxx
    intercept = y_mean - slope * x_mean
    if not math.isfinite(slope) or not math.isfinite(intercept):
        raise ValueError("affine fit produced a non-finite coefficient")

    violations: list[str] = []
    if slope <= 0:
        violations.append("NONPOSITIVE_SLOPE")
    if intercept < 0:
        violations.append("NEGATIVE_INTERCEPT")
    return AffineFitDiagnostic(
        hardware_id=partition.hardware_id,
        kind=partition.kind,
        intercept_seconds=intercept,
        slope_seconds_per_axis_unit=slope,
        fit_point_ids=tuple(point.point_id for point in partition.fit),
        validation_point_ids=tuple(point.point_id for point in partition.validation),
        violations=tuple(violations),
    )


def evaluate_family_adequacy(partition: ReferencePartition) -> FamilyAdequacyRecord:
    """Evaluate one family without turning an inadmissible fit into a predictor."""

    diagnostic = fit_affine_reference_family_diagnostic(partition)
    try:
        normative = fit_affine_reference_family(partition)
    except ValueError as exc:
        if diagnostic.admissible:
            raise
        expected_errors = _expected_admissibility_errors(diagnostic)
        if str(exc) not in expected_errors:
            raise
        return FamilyAdequacyRecord(
            fit=diagnostic,
            validation_status=FamilyValidationStatus.NOT_EVALUABLE_INVALID_FIT,
            validation_report=None,
            decision=AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION,
        )

    if not diagnostic.admissible:
        raise AssertionError("normative C6.3b fitter accepted an inadmissible diagnostic fit")
    if (
        normative.intercept_seconds != diagnostic.intercept_seconds
        or normative.slope_seconds_per_axis_unit
        != diagnostic.slope_seconds_per_axis_unit
    ):
        raise AssertionError("normative and diagnostic C6.3b coefficients disagree")

    predictions = {
        point.point_id: math.fsum(
            [
                diagnostic.intercept_seconds,
                diagnostic.slope_seconds_per_axis_unit * point.axis_value,
            ]
        )
        for point in partition.validation
    }
    report = evaluate_reference_predictions(partition, predictions)
    return FamilyAdequacyRecord(
        fit=diagnostic,
        validation_status=FamilyValidationStatus.EVALUATED,
        validation_report=report,
        decision=report.decision,
    )


def evaluate_decode_composition(
    hardware_id: str,
    *,
    fixed_seconds: float,
    slope_seconds_per_context_step: float,
) -> DecodeCompositionReport:
    """Compare repeated single-token affine composition with frozen C6.1 mechanics."""

    if hardware_id not in C63C_CORPUS_FINGERPRINTS:
        raise ValueError("hardware_id is outside the frozen C6.3c domain")
    fixed = _finite_nonnegative(fixed_seconds, "fixed_seconds")
    slope = _finite_nonnegative(
        slope_seconds_per_context_step, "slope_seconds_per_context_step"
    )
    if slope <= 0:
        raise ValueError("slope_seconds_per_context_step must be strictly positive")

    points: list[DecodeCompositionPoint] = []
    for input_context in C63C_COMPOSITION_INPUT_CONTEXTS:
        for output_tokens in C63C_COMPOSITION_OUTPUT_TOKENS:
            if input_context + output_tokens - 1 > C63C_MAX_MODEL_LEN:
                continue
            steps = (
                output_tokens * input_context
                + output_tokens * (output_tokens - 1) // 2
            )
            variable = slope * steps
            source_seconds = math.fsum([output_tokens * fixed, variable])
            c6_seconds = math.fsum([fixed, variable])
            signed = c6_seconds - source_seconds
            absolute = abs(signed)
            if source_seconds <= 0:
                raise ValueError("decode composition reference must be positive")
            ape = absolute / source_seconds
            expected_gap = (output_tokens - 1) * fixed
            observed_gap = source_seconds - c6_seconds
            gap_tolerance = C6_THRESHOLD_ULP_BUDGET * math.ulp(
                max(abs(source_seconds), abs(c6_seconds), abs(expected_gap), 1.0)
            )
            if not math.isclose(
                observed_gap,
                expected_gap,
                rel_tol=0.0,
                abs_tol=gap_tolerance,
            ):
                raise AssertionError("decode composition algebraic gap identity drifted")
            points.append(
                DecodeCompositionPoint(
                    input_context_tokens=input_context,
                    output_tokens=output_tokens,
                    context_token_steps=steps,
                    source_primitive_sequence_seconds=source_seconds,
                    c6_1_sequence_seconds=c6_seconds,
                    signed_error_seconds=signed,
                    absolute_error_seconds=absolute,
                    absolute_percentage_error=ape,
                    expected_source_minus_c6_gap_seconds=expected_gap,
                )
            )

    if not points:
        raise RuntimeError("frozen C6.3c decode composition grid is empty")
    mae = math.fsum(point.absolute_error_seconds for point in points) / len(points)
    mape = math.fsum(point.absolute_percentage_error for point in points) / len(points)
    max_ape = max(point.absolute_percentage_error for point in points)
    adequate = _within_limit(mape, C6_VALIDATION_MAPE_LIMIT) and _within_limit(
        max_ape, C6_VALIDATION_MAX_APE_LIMIT
    )
    return DecodeCompositionReport(
        hardware_id=hardware_id,
        fixed_seconds=fixed,
        slope_seconds_per_context_step=slope,
        points=tuple(points),
        mae_seconds=mae,
        mape=mape,
        max_ape=max_ape,
        decision=(
            AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
            if adequate
            else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION
        ),
    )


def evaluate_vidur_hardware_adequacy(
    hardware_id: str,
    *,
    attention_bytes: bytes,
    mlp_bytes: bytes,
    send_recv_bytes: bytes,
) -> HardwareAdequacyRecord:
    """Reconstruct one frozen source corpus and execute C6.3c exactly once."""

    if hardware_id not in C63C_CORPUS_FINGERPRINTS:
        raise ValueError("hardware_id is outside the frozen C6.3c domain")
    blobs = {
        "attention": _require_bytes(attention_bytes, "attention_bytes"),
        "mlp": _require_bytes(mlp_bytes, "mlp_bytes"),
        "send_recv": _require_bytes(send_recv_bytes, "send_recv_bytes"),
    }
    observed_sha256 = {
        name: hashlib.sha256(content).hexdigest() for name, content in blobs.items()
    }
    if observed_sha256 != VIDUR_SOURCE_SHA256[hardware_id]:
        raise ValueError(
            f"source SHA-256 snapshot mismatch for {hardware_id}: "
            f"expected={VIDUR_SOURCE_SHA256[hardware_id]}, observed={observed_sha256}"
        )

    references = derive_vidur_reference_corpus(
        hardware_id,
        attention_csv=blobs["attention"].decode("utf-8-sig"),
        mlp_csv=blobs["mlp"].decode("utf-8-sig"),
        send_recv_csv=blobs["send_recv"].decode("utf-8-sig"),
    )
    fingerprint = _corpus_fingerprint(hardware_id, observed_sha256, references)
    if fingerprint != C63C_CORPUS_FINGERPRINTS[hardware_id]:
        raise ValueError(
            f"C6.3b corpus fingerprint mismatch for {hardware_id}: "
            f"expected={C63C_CORPUS_FINGERPRINTS[hardware_id]}, observed={fingerprint}"
        )

    families = tuple(
        evaluate_family_adequacy(
            split_reference_family(
                [item.point for item in references if item.point.kind is kind]
            )
        )
        for kind in ReferenceKind
    )
    decode = next(
        item for item in families if item.fit.kind is ReferenceKind.SINGLE_TOKEN_DECODE
    )
    if not decode.fit.admissible:
        raise ValueError("multi-token composition requires an admissible decode fit")
    composition = evaluate_decode_composition(
        hardware_id,
        fixed_seconds=decode.fit.intercept_seconds,
        slope_seconds_per_context_step=decode.fit.slope_seconds_per_axis_unit,
    )
    adequate = all(
        item.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
        for item in families
    ) and composition.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
    return HardwareAdequacyRecord(
        hardware_id=hardware_id,
        source_sha256=observed_sha256,
        corpus_fingerprint=fingerprint,
        families=families,
        decode_composition=composition,
        decision=(
            AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
            if adequate
            else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION
        ),
    )


def _expected_admissibility_errors(diagnostic: AffineFitDiagnostic) -> set[str]:
    result: set[str] = set()
    if "NONPOSITIVE_SLOPE" in diagnostic.violations:
        result.add(
            f"{diagnostic.hardware_id}/{diagnostic.kind.value} fit slope must be positive"
        )
    if "NEGATIVE_INTERCEPT" in diagnostic.violations:
        result.add(
            f"{diagnostic.hardware_id}/{diagnostic.kind.value} fit intercept must be non-negative"
        )
    return result


def _corpus_fingerprint(
    hardware_id: str,
    source_sha256: Mapping[str, str],
    references: Sequence[ProjectionReference],
) -> str:
    payload = {
        "hardware_id": hardware_id,
        "source_sha256": dict(sorted(source_sha256.items())),
        "aggregation": C6_PROJECTION_AGGREGATION,
        "references": [item.to_dict() for item in references],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _within_limit(value: float, limit: float) -> bool:
    return value <= limit or value - limit <= C6_THRESHOLD_ULP_BUDGET * math.ulp(limit)


def _finite_nonnegative(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def _require_bytes(value: bytes, name: str) -> bytes:
    if not isinstance(value, bytes) or not value:
        raise ValueError(f"{name} must be non-empty bytes")
    return value
