from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, combinations_with_replacement
import hashlib
import json
import math
import os
import platform
from typing import Any, Sequence

from .c64b_protocol import C64B_RUNTIME
from .c64d_export import carried_decode_scalars, state_memory_scalars
from .calibration_validation import VIDUR_LLAMA2_7B_TP1_DOMAIN
from .inference_cost import InferenceCostWorkload, SourcedScalar


C64E_POLYNOMIAL_SCHEMA = "cadi.c6.4e.source-ordered-polynomial.v1"
C64E_PROFILE_SCHEMA = "cadi.c6.4e.cost-profile-v4.v1"
C64E_ESTIMATE_SCHEMA = "cadi.c6.4e.cost-estimate-v4.v1"
C64E_EXHAUSTIVE_PROTOCOL_SCHEMA = "cadi.c6.4e.exhaustive-source-equivalence-protocol.v1"
C64E_REPRESENTATION_ID = (
    "cadi.c6.cost-representation.v4."
    "source-ordered-polynomial+source-linear-algebra+decode-step-affine"
)
C64E_EVALUATION_KERNEL_ID = (
    "sklearn-1.5.2-dense-polynomial-order+"
    "numpy-1.26.4-float64-matmul+intercept+"
    "openblas-0.3.23.dev-haswell-pthreads-1.v1"
)
C64E_SOURCE_PROTOCOL_ID = "cadi.c6.4b.vidur-linear-regression-source-model.v2"
C64E_SOURCE_PROTOCOL_FINGERPRINT = (
    "9decab129b64921d59696c58242f0047ad986c76b82a22819eb93166b5d3a717"
)
C64E_REFERENCE_EVIDENCE = "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"
C64E_EQUIVALENCE_ULP_BUDGET = 8
C64E_REPORT_MAPE_LIMIT = 0.05
C64E_REPORT_MAX_APE_LIMIT = 0.10
C64E_PREFILL_TOKEN_MIN = 1
C64E_PREFILL_TOKEN_MAX = 4096
C64E_TRANSFER_PREDICTOR_TOKEN_MIN = 1
C64E_TRANSFER_PREDICTOR_TOKEN_MAX = 4096
C64E_TRANSFER_BYTES_PER_TOKEN = 8192
C64E_INVALIDATED_BOUNDARY_STATUS = (
    "INVALIDATED_BY_SUPERSEDED_PREDICTOR_MATERIALIZATION"
)
C64E_NUMERICAL_SOURCE_BLOBS = {
    "scikit-learn/sklearn/preprocessing/_polynomial.py": (
        "f4c9fb032cfb05b1e97640ff000116e8b3b8cf87"
    ),
    "scikit-learn/sklearn/linear_model/_base.py": (
        "02a72355e36029f8339838a03949a2c83f8b0654"
    ),
    "vidur/entities/execution_time.py": (
        "a5100f86b7e4d885ff62a5a66f845682a998add8"
    ),
}
C64E_SUPPORTED_HARDWARE_IDS = frozenset({"a100-80gb", "h100-80gb"})
C64E_REQUIRED_HYPERPARAMETER_KEYS = (
    "linearregression__fit_intercept",
    "polynomialfeatures__degree",
    "polynomialfeatures__include_bias",
    "polynomialfeatures__interaction_only",
)
C64E_PREFILL_COMPONENT_IDS = (
    "add",
    "attn_kv_cache_save",
    "attn_post_proj",
    "attn_pre_proj",
    "attn_rope",
    "input_layernorm",
    "mlp_act",
    "mlp_down_proj",
    "mlp_up_proj",
    "post_attention_layernorm",
)
C64E_EXACT_PREFILL_COMPONENT_IDS = frozenset({"attn_kv_cache_save"})


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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


def _integral_axis(value: float, name: str) -> int:
    numeric = _finite(value, name)
    if numeric < 0 or not numeric.is_integer():
        raise ValueError(f"{name} must be a finite non-negative integer value")
    return int(numeric)


def source_polynomial_powers(
    feature_count: int,
    degree: int,
    interaction_only: bool,
    include_bias: bool,
) -> tuple[tuple[int, ...], ...]:
    """Reproduce sklearn 1.5.2 PolynomialFeatures.powers_ row order."""
    if not isinstance(feature_count, int) or isinstance(feature_count, bool):
        raise TypeError("feature_count must be an integer")
    if feature_count <= 0:
        raise ValueError("feature_count must be positive")
    if not isinstance(degree, int) or isinstance(degree, bool) or degree < 1:
        raise ValueError("degree must be a positive integer")
    if not isinstance(interaction_only, bool) or not isinstance(include_bias, bool):
        raise TypeError("interaction_only/include_bias must be boolean")
    rows: list[tuple[int, ...]] = []
    if include_bias:
        rows.append((0,) * feature_count)
    comb = combinations if interaction_only else combinations_with_replacement
    for total_degree in range(1, degree + 1):
        for indices in comb(range(feature_count), total_degree):
            exponents = [0] * feature_count
            for index in indices:
                exponents[index] += 1
            rows.append(tuple(exponents))
    return tuple(rows)


def _numpy_module():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("v4 numerical evaluation requires frozen NumPy") from exc
    expected = str(C64B_RUNTIME["numpy"])
    if np.__version__ != expected:
        raise RuntimeError(
            f"NumPy version drift: expected={expected}, observed={np.__version__}"
        )
    return np


def source_dense_polynomial_row(
    feature_values: Sequence[float],
    *,
    degree: int,
    interaction_only: bool,
    include_bias: bool,
) -> Any:
    """Reproduce the sklearn 1.5.2 dense transform algorithm for one row."""
    np = _numpy_module()
    values = tuple(
        _finite(value, f"feature_values[{index}]")
        for index, value in enumerate(feature_values)
    )
    if not values:
        raise ValueError("feature_values must be non-empty")
    powers = source_polynomial_powers(
        len(values), degree, interaction_only, include_bias
    )
    X = np.asarray([values], dtype=np.float64, order="F")
    _, n_features = X.shape
    XP = np.empty((1, len(powers)), dtype=X.dtype, order="C")
    if include_bias:
        XP[:, 0] = 1
        current_col = 1
    else:
        current_col = 0
    XP[:, current_col : current_col + n_features] = X
    index = list(range(current_col, current_col + n_features))
    current_col += n_features
    index.append(current_col)
    for _ in range(2, degree + 1):
        new_index: list[int] = []
        end = index[-1]
        for feature_idx in range(n_features):
            start = index[feature_idx]
            new_index.append(current_col)
            if interaction_only:
                start += index[feature_idx + 1] - index[feature_idx]
            next_col = current_col + end - start
            if next_col <= current_col:
                break
            np.multiply(
                XP[:, start:end],
                X[:, feature_idx : feature_idx + 1],
                out=XP[:, current_col:next_col],
                casting="no",
            )
            current_col = next_col
        new_index.append(current_col)
        index = new_index
    if current_col != len(powers):
        raise RuntimeError("source polynomial construction disagrees with powers")
    return XP[0]


def assert_c64e_numerical_runtime() -> dict[str, Any]:
    """Fail closed unless the accepted C6.4b v2 numerical substrate is active."""
    np = _numpy_module()
    try:
        import threadpoolctl
        from threadpoolctl import threadpool_info
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("threadpoolctl is required for v4 runtime fencing") from exc
    if platform.python_implementation() != str(C64B_RUNTIME["python_implementation"]):
        raise RuntimeError("Python implementation drift")
    if platform.python_version() != str(C64B_RUNTIME["python"]):
        raise RuntimeError("Python version drift")
    if platform.machine() != str(C64B_RUNTIME["architecture"]):
        raise RuntimeError("architecture drift")
    if threadpoolctl.__version__ != str(C64B_RUNTIME["threadpoolctl"]):
        raise RuntimeError("threadpoolctl version drift")
    expected_env = dict(C64B_RUNTIME["environment"])
    observed_env = {key: os.environ.get(key) for key in expected_env}
    if observed_env != expected_env:
        raise RuntimeError(
            f"numerical environment drift: expected={expected_env}, observed={observed_env}"
        )
    np.dot(np.ones((2, 2)), np.ones((2, 2)))
    blas = [x for x in threadpool_info() if x.get("internal_api") == "openblas"]
    if len(blas) != 1:
        raise RuntimeError(f"expected exactly one OpenBLAS runtime, observed={blas}")
    observed_blas = {
        "version": str(blas[0].get("version")),
        "coretype": str(blas[0].get("architecture")),
        "threading_layer": str(blas[0].get("threading_layer")),
        "num_threads": int(blas[0].get("num_threads", -1)),
    }
    expected_blas = dict(C64B_RUNTIME["openblas"])
    if observed_blas != expected_blas:
        raise RuntimeError(
            f"OpenBLAS drift: expected={expected_blas}, observed={observed_blas}"
        )
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "threadpoolctl": threadpoolctl.__version__,
        "openblas": observed_blas,
        "environment": observed_env,
    }


@dataclass(frozen=True, slots=True)
class SourceOrderedPolynomialModel:
    component_id: str
    hardware_id: str
    feature_names: tuple[str, ...]
    powers: tuple[tuple[int, ...], ...]
    coefficients: tuple[float, ...]
    intercept: float
    upstream_hyperparameters: tuple[tuple[str, str], ...]
    source_reference: str
    source_protocol_fingerprint: str = C64E_SOURCE_PROTOCOL_FINGERPRINT
    evaluation_kernel_id: str = C64E_EVALUATION_KERNEL_ID
    polynomial_output_order: str = "C"
    output_unit: str = "milliseconds"

    def __post_init__(self) -> None:
        _require_nonempty(self.component_id, "component_id")
        _require_nonempty(self.hardware_id, "hardware_id")
        _require_nonempty(self.source_reference, "source_reference")
        if self.hardware_id not in C64E_SUPPORTED_HARDWARE_IDS:
            raise ValueError("hardware_id outside frozen v4 source family")
        if self.source_protocol_fingerprint != C64E_SOURCE_PROTOCOL_FINGERPRINT:
            raise ValueError("source protocol fingerprint drift")
        if self.evaluation_kernel_id != C64E_EVALUATION_KERNEL_ID:
            raise ValueError("evaluation kernel identifier drift")
        if self.polynomial_output_order != "C":
            raise ValueError("source PolynomialFeatures output order must remain 'C'")
        if self.output_unit != "milliseconds":
            raise ValueError("source polynomial output_unit must be 'milliseconds'")
        if not isinstance(self.feature_names, tuple) or not self.feature_names:
            raise ValueError("feature_names must be a non-empty tuple")
        if len(set(self.feature_names)) != len(self.feature_names) or not all(
            isinstance(name, str) and name.strip() for name in self.feature_names
        ):
            raise ValueError("feature_names must contain unique non-empty strings")
        if not isinstance(self.upstream_hyperparameters, tuple):
            raise TypeError("upstream_hyperparameters must be a tuple")
        encoded: dict[str, str] = {}
        keys: list[str] = []
        for pair in self.upstream_hyperparameters:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("hyperparameters must be key/value tuples")
            key, value = pair
            _require_nonempty(key, "hyperparameter key")
            _require_nonempty(value, "hyperparameter value")
            keys.append(key)
            encoded[key] = value
        if tuple(keys) != C64E_REQUIRED_HYPERPARAMETER_KEYS:
            raise ValueError("hyperparameters must preserve exact frozen Vidur grid keys")
        if encoded["polynomialfeatures__degree"] not in {"1", "2", "3", "4", "5"}:
            raise ValueError("polynomial degree outside frozen choices")
        for key in (
            "linearregression__fit_intercept",
            "polynomialfeatures__include_bias",
            "polynomialfeatures__interaction_only",
        ):
            if encoded[key] not in {"true", "false"}:
                raise ValueError(f"{key} must use canonical true/false encoding")
        expected_powers = source_polynomial_powers(
            len(self.feature_names),
            int(encoded["polynomialfeatures__degree"]),
            encoded["polynomialfeatures__interaction_only"] == "true",
            encoded["polynomialfeatures__include_bias"] == "true",
        )
        if self.powers != expected_powers:
            raise ValueError(
                "powers must exactly preserve sklearn PolynomialFeatures source row order"
            )
        if not isinstance(self.coefficients, tuple):
            raise TypeError("coefficients must be a tuple")
        if len(self.coefficients) != len(self.powers):
            raise ValueError("coefficients must align one-to-one with source powers")
        object.__setattr__(
            self,
            "coefficients",
            tuple(
                _finite(value, f"coefficients[{index}]")
                for index, value in enumerate(self.coefficients)
            ),
        )
        object.__setattr__(self, "intercept", _finite(self.intercept, "intercept"))
        if encoded["linearregression__fit_intercept"] == "false" and self.intercept != 0.0:
            raise ValueError("source fit_intercept=false requires zero intercept")

    @property
    def degree(self) -> int:
        return int(dict(self.upstream_hyperparameters)["polynomialfeatures__degree"])

    @property
    def include_bias(self) -> bool:
        return dict(self.upstream_hyperparameters)["polynomialfeatures__include_bias"] == "true"

    @property
    def interaction_only(self) -> bool:
        return dict(self.upstream_hyperparameters)["polynomialfeatures__interaction_only"] == "true"

    def evaluate(self, feature_values: Sequence[float]) -> float:
        if len(feature_values) != len(self.feature_names):
            raise ValueError("feature_values length must match feature_names")
        np = _numpy_module()
        transformed = source_dense_polynomial_row(
            feature_values,
            degree=self.degree,
            interaction_only=self.interaction_only,
            include_bias=self.include_bias,
        )
        coefficients = np.asarray(self.coefficients, dtype=np.float64)
        if transformed.shape != coefficients.shape:
            raise RuntimeError("source row and coefficient shape disagree")
        predicted = (
            np.asarray([transformed], dtype=np.float64) @ coefficients
            + np.float64(self.intercept)
        )
        result = float(predicted[0])
        if not math.isfinite(result):
            raise ValueError("source-kernel polynomial evaluation became non-finite")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C64E_POLYNOMIAL_SCHEMA,
            "component_id": self.component_id,
            "hardware_id": self.hardware_id,
            "feature_names": list(self.feature_names),
            "powers": [list(power) for power in self.powers],
            "coefficients": list(self.coefficients),
            "intercept": self.intercept,
            "upstream_hyperparameters": [list(x) for x in self.upstream_hyperparameters],
            "polynomial_output_order": self.polynomial_output_order,
            "evaluation_kernel_id": self.evaluation_kernel_id,
            "source_protocol_id": C64E_SOURCE_PROTOCOL_ID,
            "source_protocol_fingerprint": self.source_protocol_fingerprint,
            "source_reference": self.source_reference,
            "numerical_source_blobs": dict(sorted(C64E_NUMERICAL_SOURCE_BLOBS.items())),
            "output_unit": self.output_unit,
        }

    @property
    def fingerprint(self) -> str:
        return _sha256_json(self.to_dict())


def _left_add(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("left-add sequence must be non-empty")
    total = _finite(values[0], "values[0]")
    for index, value in enumerate(values[1:], start=1):
        total = total + _finite(value, f"values[{index}]")
    return total


@dataclass(frozen=True, slots=True)
class SourceKernelCostProfile:
    profile_id: str
    model_id: str
    hardware_id: str
    prefill_components: tuple[SourceOrderedPolynomialModel, ...]
    prefill_attention: SourceOrderedPolynomialModel
    transfer: SourceOrderedPolynomialModel
    decode_fixed_seconds_per_output_token: SourcedScalar
    decode_seconds_per_context_token_step: SourcedScalar
    state_fixed_bytes: SourcedScalar
    state_bytes_per_token: SourcedScalar
    memory_capacity_bytes: SourcedScalar
    representation_id: str = C64E_REPRESENTATION_ID
    evaluation_kernel_id: str = C64E_EVALUATION_KERNEL_ID

    def __post_init__(self) -> None:
        for name in ("profile_id", "model_id", "hardware_id"):
            _require_nonempty(getattr(self, name), name)
        if self.model_id != VIDUR_LLAMA2_7B_TP1_DOMAIN.model_id:
            raise ValueError("model_id must equal frozen v4 source model")
        if self.hardware_id not in C64E_SUPPORTED_HARDWARE_IDS:
            raise ValueError("hardware_id outside frozen v4 source family")
        if self.representation_id != C64E_REPRESENTATION_ID:
            raise ValueError("representation_id drift")
        if self.evaluation_kernel_id != C64E_EVALUATION_KERNEL_ID:
            raise ValueError("evaluation_kernel_id drift")
        if not isinstance(self.prefill_components, tuple) or not all(
            isinstance(x, SourceOrderedPolynomialModel) for x in self.prefill_components
        ):
            raise TypeError("prefill_components must be SourceOrderedPolynomialModel tuple")
        if tuple(x.component_id for x in self.prefill_components) != C64E_PREFILL_COMPONENT_IDS:
            raise ValueError("prefill_components must preserve exact Vidur component order")
        if any(
            x.hardware_id != self.hardware_id or x.feature_names != ("num_tokens",)
            for x in self.prefill_components
        ):
            raise ValueError("prefill component hardware/features drift")
        if not isinstance(self.prefill_attention, SourceOrderedPolynomialModel) or (
            self.prefill_attention.hardware_id != self.hardware_id
            or self.prefill_attention.component_id != "attn_prefill"
            or self.prefill_attention.feature_names
            != ("kv_cache_size", "prefill_chunk_size_squared")
        ):
            raise ValueError("prefill_attention source identity/features drift")
        if not isinstance(self.transfer, SourceOrderedPolynomialModel) or (
            self.transfer.hardware_id != self.hardware_id
            or self.transfer.component_id != "send_recv"
            or self.transfer.feature_names != ("num_tokens",)
        ):
            raise ValueError("transfer source identity/features drift")
        for model in self.prefill_components + (self.prefill_attention, self.transfer):
            if model.evaluation_kernel_id != C64E_EVALUATION_KERNEL_ID:
                raise ValueError("profile kernel binding drift")
        units = {
            "decode_fixed_seconds_per_output_token": "seconds/output-token",
            "decode_seconds_per_context_token_step": "seconds/context-token-step",
            "state_fixed_bytes": "bytes",
            "state_bytes_per_token": "bytes/token",
            "memory_capacity_bytes": "bytes",
        }
        for name, unit in units.items():
            value = getattr(self, name)
            if not isinstance(value, SourcedScalar):
                raise TypeError(f"{name} must be SourcedScalar")
            if value.unit != unit or value.value < 0:
                raise ValueError(f"invalid {name}")
        if self.memory_capacity_bytes.value <= 0:
            raise ValueError("memory_capacity_bytes must be positive")

    def prefill_seconds(self, input_tokens: int) -> float:
        if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
            raise TypeError("input_tokens must be an integer")
        if input_tokens == 0:
            return 0.0
        if not C64E_PREFILL_TOKEN_MIN <= input_tokens <= C64E_PREFILL_TOKEN_MAX:
            raise ValueError("cold-prefill evaluation outside frozen source domain")
        rounded = (input_tokens + 7) // 8 * 8
        by_id = {x.component_id: x for x in self.prefill_components}
        def component(name: str) -> float:
            axis = input_tokens if name in C64E_EXACT_PREFILL_COMPONENT_IDS else rounded
            return by_id[name].evaluate((float(axis),))
        # Exact pinned ExecutionTime nesting/order; cold prefill decode/TP/PP are zero.
        attention = _left_add((
            component("attn_pre_proj"),
            component("attn_post_proj"),
            component("attn_rope"),
            component("attn_kv_cache_save"),
            0.0,
            self.prefill_attention.evaluate((0.0, float(input_tokens * input_tokens))),
            0.0,
            component("input_layernorm"),
        ))
        mlp = _left_add((
            component("mlp_up_proj"),
            component("mlp_down_proj"),
            component("mlp_act"),
            0.0,
            component("post_attention_layernorm"),
        ))
        block = _left_add((attention, mlp, component("add")))
        seconds = (block * VIDUR_LLAMA2_7B_TP1_DOMAIN.num_layers + 0.0) * 1e-3
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("source-kernel cold-prefill produced invalid seconds")
        return seconds

    def transfer_seconds(self, state_bytes: float) -> float:
        axis_bytes = _integral_axis(state_bytes, "state_bytes")
        if axis_bytes == 0:
            return 0.0
        if axis_bytes % C64E_TRANSFER_BYTES_PER_TOKEN:
            raise ValueError("transfer bytes outside exact source token-multiple domain")
        tokens = axis_bytes // C64E_TRANSFER_BYTES_PER_TOKEN
        if not C64E_TRANSFER_PREDICTOR_TOKEN_MIN <= tokens <= C64E_TRANSFER_PREDICTOR_TOKEN_MAX:
            raise ValueError("transfer bytes outside frozen source lookup domain")
        seconds = self.transfer.evaluate((float(tokens),)) * 1e-3
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("source-kernel transfer produced invalid seconds")
        return seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C64E_PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "model_id": self.model_id,
            "hardware_id": self.hardware_id,
            "representation_id": self.representation_id,
            "evaluation_kernel_id": self.evaluation_kernel_id,
            "source_protocol_id": C64E_SOURCE_PROTOCOL_ID,
            "source_protocol_fingerprint": C64E_SOURCE_PROTOCOL_FINGERPRINT,
            "prefill_domain_input_tokens": [1, 4096],
            "prefill_compute_rounding": "(n + 7) // 8 * 8",
            "prefill_request_arithmetic": "pinned Vidur ExecutionTime left-associative nesting",
            "prefill_components": [x.to_dict() for x in self.prefill_components],
            "prefill_attention": self.prefill_attention.to_dict(),
            "decode_fixed_seconds_per_output_token": self.decode_fixed_seconds_per_output_token.to_dict(),
            "decode_seconds_per_context_token_step": self.decode_seconds_per_context_token_step.to_dict(),
            "state_fixed_bytes": self.state_fixed_bytes.to_dict(),
            "state_bytes_per_token": self.state_bytes_per_token.to_dict(),
            "memory_capacity_bytes": self.memory_capacity_bytes.to_dict(),
            "transfer": self.transfer.to_dict(),
            "transfer_domain": {
                "bytes_per_predictor_token": 8192,
                "predictor_token_min": 1,
                "predictor_token_max": 4096,
                "rounding": "forbidden",
                "extrapolation": "forbidden",
            },
        }

    @property
    def fingerprint(self) -> str:
        return _sha256_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class SourceKernelCostEstimate:
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
            "schema": C64E_ESTIMATE_SCHEMA,
            **{name: getattr(self, name) for name in self.__dataclass_fields__},
        }


def estimate_source_kernel_cost(
    profile: SourceKernelCostProfile,
    workload: InferenceCostWorkload,
) -> SourceKernelCostEstimate:
    if not isinstance(profile, SourceKernelCostProfile):
        raise TypeError("profile must be SourceKernelCostProfile")
    if not isinstance(workload, InferenceCostWorkload):
        raise TypeError("workload must be InferenceCostWorkload")
    prefill = profile.prefill_seconds(workload.input_tokens)
    steps = (
        workload.output_tokens * workload.input_tokens
        + workload.output_tokens * (workload.output_tokens - 1) // 2
    )
    decode = 0.0 if workload.output_tokens == 0 else (
        workload.output_tokens * profile.decode_fixed_seconds_per_output_token.value
        + profile.decode_seconds_per_context_token_step.value * steps
    )
    recompute_tokens = workload.input_tokens - workload.reusable_prefix_tokens
    recompute = profile.prefill_seconds(recompute_tokens)
    state = profile.state_fixed_bytes.value + profile.state_bytes_per_token.value * workload.state_tokens
    transfer = profile.transfer_seconds(state)
    memory_fraction = state / profile.memory_capacity_bytes.value
    values = (prefill, decode, recompute, transfer, state, memory_fraction)
    if not all(math.isfinite(x) and x >= 0 for x in values):
        raise ValueError("source-kernel cost evaluation produced invalid result")
    return SourceKernelCostEstimate(
        profile.profile_id,
        profile.fingerprint,
        prefill,
        decode,
        recompute,
        transfer,
        state,
        memory_fraction,
        recompute_tokens,
        steps,
    )


def profile_from_source_models(
    *,
    hardware_id: str,
    prefill_components: Sequence[SourceOrderedPolynomialModel],
    prefill_attention: SourceOrderedPolynomialModel,
    transfer: SourceOrderedPolynomialModel,
) -> SourceKernelCostProfile:
    decode_fixed, decode_slope = carried_decode_scalars(hardware_id)
    state_fixed, state_per_token, memory_capacity = state_memory_scalars(hardware_id)
    return SourceKernelCostProfile(
        profile_id=f"c6.4f:{hardware_id}:source-kernel-v4",
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


@dataclass(frozen=True, slots=True)
class ExhaustiveSourceEquivalenceProtocol:
    representation_id: str = C64E_REPRESENTATION_ID
    evaluation_kernel_id: str = C64E_EVALUATION_KERNEL_ID
    source_protocol_id: str = C64E_SOURCE_PROTOCOL_ID
    source_protocol_fingerprint: str = C64E_SOURCE_PROTOCOL_FINGERPRINT
    evidence_class: str = C64E_REFERENCE_EVIDENCE
    prefill_token_min: int = 1
    prefill_token_max: int = 4096
    transfer_predictor_token_min: int = 1
    transfer_predictor_token_max: int = 4096
    transfer_bytes_per_token: int = 8192
    ulp_budget: int = 8
    report_mape_limit: float = 0.05
    report_max_ape_limit: float = 0.10
    invalidated_boundary_status: str = C64E_INVALIDATED_BOUNDARY_STATUS

    def __post_init__(self) -> None:
        if self.representation_id != C64E_REPRESENTATION_ID:
            raise ValueError("exhaustive protocol representation drift")
        if self.evaluation_kernel_id != C64E_EVALUATION_KERNEL_ID:
            raise ValueError("exhaustive protocol kernel drift")
        if self.source_protocol_id != C64E_SOURCE_PROTOCOL_ID or self.source_protocol_fingerprint != C64E_SOURCE_PROTOCOL_FINGERPRINT:
            raise ValueError("exhaustive protocol source drift")
        if self.evidence_class != C64E_REFERENCE_EVIDENCE:
            raise ValueError("exhaustive protocol evidence class drift")
        if (
            self.prefill_token_min,
            self.prefill_token_max,
            self.transfer_predictor_token_min,
            self.transfer_predictor_token_max,
            self.transfer_bytes_per_token,
            self.ulp_budget,
        ) != (1, 4096, 1, 4096, 8192, 8):
            raise ValueError("exhaustive finite-domain protocol drift")
        if self.report_mape_limit != 0.05 or self.report_max_ape_limit != 0.10:
            raise ValueError("aggregate report guard drift")
        if self.invalidated_boundary_status != C64E_INVALIDATED_BOUNDARY_STATUS:
            raise ValueError("invalidated boundary accounting drift")

    @property
    def prefill_point_count(self) -> int:
        return self.prefill_token_max - self.prefill_token_min + 1

    @property
    def transfer_point_count(self) -> int:
        return self.transfer_predictor_token_max - self.transfer_predictor_token_min + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C64E_EXHAUSTIVE_PROTOCOL_SCHEMA,
            "representation_id": self.representation_id,
            "evaluation_kernel_id": self.evaluation_kernel_id,
            "source_protocol_id": self.source_protocol_id,
            "source_protocol_fingerprint": self.source_protocol_fingerprint,
            "evidence_class": self.evidence_class,
            "numerical_runtime": {
                "python": C64B_RUNTIME["python"],
                "numpy": C64B_RUNTIME["numpy"],
                "threadpoolctl": C64B_RUNTIME["threadpoolctl"],
                "openblas": dict(C64B_RUNTIME["openblas"]),
                "environment": dict(C64B_RUNTIME["environment"]),
            },
            "numerical_source_blobs": dict(sorted(C64E_NUMERICAL_SOURCE_BLOBS.items())),
            "domains": {
                "cold_prefill_input_tokens": {"min": 1, "max": 4096, "point_count": 4096, "enumeration": "every integer axis"},
                "point_to_point_transfer": {"predictor_token_min": 1, "predictor_token_max": 4096, "bytes_per_predictor_token": 8192, "point_count": 4096, "enumeration": "every integer predictor-token axis"},
                "recompute": "same cold-prefill function; exhaustively covered",
                "decode": "unchanged carried C6.3 primitive/composition",
                "state_memory": "unchanged source-derived algebra",
            },
            "oracle": {
                "source_equivalence_ulp_budget": 8,
                "require_every_point_within_ulp_budget": True,
                "aggregate_mape_limit": 0.05,
                "aggregate_max_ape_limit": 0.10,
                "aggregate_guards_cannot_excuse_equivalence_violation": True,
                "parent_pass_requires_both_hardware_profiles": True,
            },
            "boundary_accounting": {
                "c64c_boundary_status": C64E_INVALIDATED_BOUNDARY_STATUS,
                "replacement_fresh_boundary": None,
                "validation_mode": "EXHAUSTIVE_FINITE_DECLARED_DOMAIN_EQUIVALENCE",
                "holdout_claim": False,
                "generalization_outside_declared_domain_claim": False,
            },
            "contains_reference_timings": False,
            "source_predictions_evaluated_by_this_protocol_freeze": False,
        }

    @property
    def fingerprint(self) -> str:
        return _sha256_json(self.to_dict())


C64E_EXHAUSTIVE_PROTOCOL = ExhaustiveSourceEquivalenceProtocol()
