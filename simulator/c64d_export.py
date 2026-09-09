from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Any, Iterable, Sequence

from .calibration_validation import VIDUR_PINNED_COMMIT, VIDUR_LLAMA2_7B_TP1_DOMAIN
from .inference_cost import ParameterProvenance, ParameterSourceClass, SourcedScalar
from .inference_cost_v3 import CanonicalPolynomialModel, SourcePolynomialCostProfile


C64D_EXPORT_SCHEMA = "cadi.c6.4d.source-polynomial-export.v1"
C64D_EQUIVALENCE_ULP_BUDGET = 8

# These are the exact C6.3 single-token-decode primitives already adjudicated as
# adequate in spec/41-c6.3c-representation-adequacy-result.md. C6.4d carries
# them unchanged; it does not refit decode.
C63_CARRIED_DECODE: dict[str, tuple[float, float]] = {
    "a100-80gb": (0.00954034447272924, 3.7204476520608805e-07),
    "h100-80gb": (0.005806526349997292, 2.1500094784162922e-07),
}

# State-size/memory inputs are independently source-derived, not fitted to any
# request-level adequacy boundary. The pinned Vidur memory planner uses two bytes
# per float, K and V, head_dim * kv_heads, and all model layers; device capacity
# is total_memory_gb * 1024**3. The pinned Llama-2-7B and A100/H100 configs give
# 32 layers, 32 KV heads, head_dim=4096/32=128, and 80 GB devices.
C64D_STATE_SOURCE_BLOBS: dict[str, str] = {
    "vidur/config/model_config.py": "722299bbb556ccbab2b82609598be6b8c2963c29",
    "vidur/config/device_sku_config.py": "8ac9bf57ac03070cd42cdf48792ccf8ffd73ca04",
    "vidur/scheduler/utils/memory_planner.py": "e769e7b161721dec29b83393717ede4940e34f12",
}
C64D_KV_BYTES_PER_TOKEN = 2 * 2 * 128 * 32 * 32
C64D_DEVICE_CAPACITY_BYTES = 80 * 1024**3

_EXPECTED_HYPERPARAMETER_KEYS = (
    "linearregression__fit_intercept",
    "polynomialfeatures__degree",
    "polynomialfeatures__include_bias",
    "polynomialfeatures__interaction_only",
)


def _float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _tolist(value: Any) -> Any:
    return value.tolist() if hasattr(value, "tolist") else value


def _float_vector(value: Any, name: str) -> tuple[float, ...]:
    raw = _tolist(value)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return (_float(raw, name),)
    if not isinstance(raw, (list, tuple)):
        raise TypeError(f"{name} must be a one-dimensional numeric sequence")
    if raw and isinstance(raw[0], (list, tuple)):
        if len(raw) != 1:
            raise ValueError(f"{name} must describe one regression output")
        raw = raw[0]
    return tuple(_float(item, f"{name}[{index}]") for index, item in enumerate(raw))


def _powers(value: Any) -> tuple[tuple[int, ...], ...]:
    raw = _tolist(value)
    if not isinstance(raw, (list, tuple)):
        raise TypeError("PolynomialFeatures.powers_ must be a matrix")
    result: list[tuple[int, ...]] = []
    for row_index, row in enumerate(raw):
        row = _tolist(row)
        if not isinstance(row, (list, tuple)):
            raise TypeError("PolynomialFeatures.powers_ rows must be sequences")
        exponents: list[int] = []
        for column_index, value in enumerate(row):
            if isinstance(value, bool):
                raise TypeError("polynomial powers must be integers")
            numeric = int(value)
            if numeric != value or numeric < 0:
                raise ValueError(
                    f"invalid polynomial power at {row_index}/{column_index}"
                )
            exponents.append(numeric)
        result.append(tuple(exponents))
    return tuple(result)


def _feature_names(estimator: Any, polynomial: Any) -> tuple[str, ...]:
    candidates = []
    for owner in (estimator, polynomial):
        if hasattr(owner, "feature_names_in_"):
            raw = _tolist(getattr(owner, "feature_names_in_"))
            if not isinstance(raw, (list, tuple)):
                raise TypeError("feature_names_in_ must be a sequence")
            candidates.append(tuple(str(item) for item in raw))
    if not candidates:
        raise ValueError("trained source pipeline must expose feature_names_in_")
    if any(item != candidates[0] for item in candidates[1:]):
        raise ValueError("pipeline and PolynomialFeatures feature order disagree")
    return candidates[0]


def _bool_text(value: Any, name: str) -> str:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be boolean")
    return "true" if value else "false"


def export_canonical_polynomial(
    estimator: Any,
    *,
    component_id: str,
    hardware_id: str,
    expected_feature_names: Sequence[str],
    source_reference: str,
) -> CanonicalPolynomialModel:
    """Export one fitted Vidur sklearn pipeline into the frozen v3 schema.

    The implementation intentionally uses structural attributes instead of
    importing sklearn so deterministic-core can test the fail-closed contract
    without adding sklearn as a repository-wide dependency.
    """

    if estimator.__class__.__name__ != "Pipeline":
        raise TypeError("source estimator must be sklearn Pipeline")
    if not hasattr(estimator, "named_steps"):
        raise TypeError("source estimator must expose named_steps")
    named_steps = estimator.named_steps
    if tuple(named_steps) != ("polynomialfeatures", "linearregression"):
        raise ValueError(
            "source pipeline must contain exactly PolynomialFeatures then LinearRegression"
        )
    polynomial = named_steps["polynomialfeatures"]
    regression = named_steps["linearregression"]
    if polynomial.__class__.__name__ != "PolynomialFeatures":
        raise TypeError("polynomialfeatures step must be PolynomialFeatures")
    if regression.__class__.__name__ != "LinearRegression":
        raise TypeError("linearregression step must be LinearRegression")

    feature_names = _feature_names(estimator, polynomial)
    expected = tuple(expected_feature_names)
    if feature_names != expected:
        raise ValueError(
            f"source feature order mismatch: expected={expected}, observed={feature_names}"
        )
    if int(getattr(polynomial, "n_features_in_", -1)) != len(feature_names):
        raise ValueError("PolynomialFeatures n_features_in_ disagrees with feature names")

    degree = getattr(polynomial, "degree", None)
    if isinstance(degree, bool) or not isinstance(degree, int) or degree < 1:
        raise ValueError("selected polynomial degree must be a positive integer")
    include_bias = getattr(polynomial, "include_bias", None)
    interaction_only = getattr(polynomial, "interaction_only", None)
    fit_intercept = getattr(regression, "fit_intercept", None)
    hyperparameters = (
        ("linearregression__fit_intercept", _bool_text(fit_intercept, "fit_intercept")),
        ("polynomialfeatures__degree", str(degree)),
        ("polynomialfeatures__include_bias", _bool_text(include_bias, "include_bias")),
        (
            "polynomialfeatures__interaction_only",
            _bool_text(interaction_only, "interaction_only"),
        ),
    )
    if tuple(key for key, _ in hyperparameters) != _EXPECTED_HYPERPARAMETER_KEYS:
        raise RuntimeError("canonical hyperparameter key order drift")

    source_powers = _powers(getattr(polynomial, "powers_", None))
    coefficients = _float_vector(getattr(regression, "coef_", None), "coef_")
    if len(source_powers) != len(coefficients):
        raise ValueError("PolynomialFeatures powers_ and LinearRegression coef_ differ")
    if any(len(power) != len(feature_names) for power in source_powers):
        raise ValueError("polynomial power width differs from source feature count")

    intercept_vector = _float_vector(getattr(regression, "intercept_", None), "intercept_")
    if len(intercept_vector) != 1:
        raise ValueError("source regression must have exactly one intercept")
    intercept = intercept_vector[0]

    nonconstant: list[tuple[tuple[int, ...], float]] = []
    for power, coefficient in zip(source_powers, coefficients, strict=True):
        if all(exponent == 0 for exponent in power):
            intercept = math.fsum([intercept, coefficient])
        else:
            nonconstant.append((power, coefficient))
    nonconstant.sort(key=lambda item: item[0])
    canonical_powers = tuple(power for power, _ in nonconstant)
    canonical_coefficients = tuple(coefficient for _, coefficient in nonconstant)

    return CanonicalPolynomialModel(
        component_id=component_id,
        hardware_id=hardware_id,
        feature_names=feature_names,
        powers=canonical_powers,
        coefficients=canonical_coefficients,
        intercept=intercept,
        upstream_hyperparameters=hyperparameters,
        source_reference=source_reference,
    )


def _ordered_binary64(value: float) -> int:
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    sign = 1 << 63
    mask = (1 << 64) - 1
    return ((~bits) & mask) if bits & sign else bits | sign


def _ulp_distance(left: float, right: float) -> int:
    if left == right:
        return 0
    return abs(_ordered_binary64(left) - _ordered_binary64(right))


def source_equivalence_report(
    canonical: CanonicalPolynomialModel,
    *,
    feature_rows: Iterable[Sequence[float]],
    source_predictions: Iterable[float],
) -> dict[str, Any]:
    rows = [tuple(float(value) for value in row) for row in feature_rows]
    source = [_float(value, "source_prediction") for value in source_predictions]
    if not rows:
        raise ValueError("source-equivalence fence requires at least one feature row")
    if len(rows) != len(source):
        raise ValueError("feature rows and source predictions must have equal length")

    max_absolute_error = 0.0
    max_ulp_distance = 0
    violations = 0
    for row, reference in zip(rows, source, strict=True):
        predicted = canonical.evaluate(row)
        absolute = abs(predicted - reference)
        distance = _ulp_distance(predicted, reference)
        max_absolute_error = max(max_absolute_error, absolute)
        max_ulp_distance = max(max_ulp_distance, distance)
        if distance > C64D_EQUIVALENCE_ULP_BUDGET:
            violations += 1
    return {
        "schema": C64D_EXPORT_SCHEMA,
        "component_id": canonical.component_id,
        "hardware_id": canonical.hardware_id,
        "sample_count": len(rows),
        "ulp_budget": C64D_EQUIVALENCE_ULP_BUDGET,
        "max_absolute_error": max_absolute_error,
        "max_ulp_distance": max_ulp_distance,
        "violation_count": violations,
        "decision": "PASS" if violations == 0 else "FAIL",
    }


def carried_decode_scalars(hardware_id: str) -> tuple[SourcedScalar, SourcedScalar]:
    if hardware_id not in C63_CARRIED_DECODE:
        raise ValueError("hardware_id is outside the carried C6.3 decode family")
    fixed, slope = C63_CARRIED_DECODE[hardware_id]
    provenance = ParameterProvenance(
        source_class=ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE,
        reference=(
            "C6.3 held-out adequate single-token decode primitive; "
            "spec/41-c6.3c-representation-adequacy-result.md; "
            "merge 8ed4b803c9ac4c65fadb6a7085063fe6c97eb1af"
        ),
    )
    return (
        SourcedScalar(
            value=fixed,
            unit="seconds/output-token",
            provenance=provenance,
        ),
        SourcedScalar(
            value=slope,
            unit="seconds/context-token-step",
            provenance=provenance,
        ),
    )


def state_memory_scalars(hardware_id: str) -> tuple[SourcedScalar, SourcedScalar, SourcedScalar]:
    if hardware_id not in VIDUR_LLAMA2_7B_TP1_DOMAIN.hardware_ids:
        raise ValueError("hardware_id is outside the frozen Vidur hardware family")
    provenance = ParameterProvenance(
        source_class=ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE,
        reference=(
            f"microsoft/vidur@{VIDUR_PINNED_COMMIT}; "
            "vidur/scheduler/utils/memory_planner.py + Llama2_7BModelConfig + "
            "A100/H100 DeviceSKUConfig; KV bytes/token = "
            "2 bytes * (K,V) * head_dim 128 * 32 KV heads * 32 layers; "
            "device bytes = 80 * 1024**3"
        ),
    )
    return (
        SourcedScalar(value=0.0, unit="bytes", provenance=provenance),
        SourcedScalar(
            value=float(C64D_KV_BYTES_PER_TOKEN),
            unit="bytes/token",
            provenance=provenance,
        ),
        SourcedScalar(
            value=float(C64D_DEVICE_CAPACITY_BYTES),
            unit="bytes",
            provenance=provenance,
        ),
    )


def compile_source_polynomial_profile(
    *,
    hardware_id: str,
    prefill_components: Sequence[CanonicalPolynomialModel],
    prefill_attention: CanonicalPolynomialModel,
    transfer: CanonicalPolynomialModel,
) -> SourcePolynomialCostProfile:
    decode_fixed, decode_slope = carried_decode_scalars(hardware_id)
    state_fixed, state_per_token, memory_capacity = state_memory_scalars(hardware_id)
    return SourcePolynomialCostProfile(
        profile_id=f"c6.4d:{hardware_id}:source-polynomial-v3",
        model_id=VIDUR_LLAMA2_7B_TP1_DOMAIN.model_id,
        hardware_id=hardware_id,
        prefill_components=tuple(prefill_components),
        prefill_attention=prefill_attention,
        transfer=transfer,
        decode_fixed_seconds_per_output_token=decode_fixed,
        decode_seconds_per_context_token_step=decode_slope,
        state_fixed_bytes=state_fixed,
        state_bytes_per_token=state_per_token,
        memory_capacity_bytes=memory_capacity,
    )
