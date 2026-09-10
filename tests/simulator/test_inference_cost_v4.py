from dataclasses import replace
import math

import pytest

from simulator.inference_cost import (
    InferenceCostWorkload,
    ParameterProvenance,
    ParameterSourceClass,
    SourcedScalar,
)
from simulator.inference_cost_v4 import (
    C64E_EVALUATION_KERNEL_ID,
    C64E_EXHAUSTIVE_PROTOCOL,
    C64E_INVALIDATED_BOUNDARY_STATUS,
    C64E_PREFILL_COMPONENT_IDS,
    C64E_REPRESENTATION_ID,
    C64E_SOURCE_PROTOCOL_FINGERPRINT,
    C64E_TRANSFER_BYTES_PER_TOKEN,
    ExhaustiveSourceEquivalenceProtocol,
    SourceKernelCostProfile,
    SourceOrderedPolynomialModel,
    assert_c64e_numerical_runtime,
    estimate_source_kernel_cost,
    export_source_ordered_polynomial,
    materialize_source_kernel_domain,
    source_dense_polynomial_matrix,
    source_dense_polynomial_row,
    source_polynomial_powers,
)


P_SRC2 = ParameterProvenance(
    source_class=ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE,
    reference="test-only source-kernel fixture",
)


def _hyperparameters(
    *, degree: int = 1, include_bias: bool = True,
    interaction_only: bool = False, fit_intercept: bool = True,
) -> tuple[tuple[str, str], ...]:
    return (
        ("linearregression__fit_intercept", str(fit_intercept).lower()),
        ("polynomialfeatures__degree", str(degree)),
        ("polynomialfeatures__include_bias", str(include_bias).lower()),
        ("polynomialfeatures__interaction_only", str(interaction_only).lower()),
    )


class PolynomialFeatures:
    def __init__(self, *, feature_names, degree, include_bias, interaction_only, powers):
        self.feature_names_in_ = list(feature_names)
        self.n_features_in_ = len(feature_names)
        self.degree = degree
        self.include_bias = include_bias
        self.interaction_only = interaction_only
        self.order = "C"
        self.powers_ = [list(row) for row in powers]


class LinearRegression:
    def __init__(self, *, coefficients, intercept, fit_intercept):
        self.coef_ = list(coefficients)
        self.intercept_ = intercept
        self.fit_intercept = fit_intercept


class Pipeline:
    def __init__(self, polynomial, regression, feature_names):
        self.named_steps = {"polynomialfeatures": polynomial, "linearregression": regression}
        self.feature_names_in_ = list(feature_names)


def _poly(
    component_id: str,
    *,
    hardware_id: str = "a100-80gb",
    feature_names: tuple[str, ...] = ("num_tokens",),
    degree: int = 1,
    include_bias: bool = True,
    interaction_only: bool = False,
    fit_intercept: bool = True,
    coefficients: tuple[float, ...] | None = None,
    intercept: float = 0.0,
) -> SourceOrderedPolynomialModel:
    powers = source_polynomial_powers(len(feature_names), degree, interaction_only, include_bias)
    if coefficients is None:
        coefficients = tuple(0.0 if not any(power) else 1.0 for power in powers)
    polynomial = PolynomialFeatures(
        feature_names=feature_names, degree=degree, include_bias=include_bias,
        interaction_only=interaction_only, powers=powers,
    )
    regression = LinearRegression(
        coefficients=coefficients, intercept=intercept, fit_intercept=fit_intercept
    )
    return export_source_ordered_polynomial(
        Pipeline(polynomial, regression, feature_names),
        component_id=component_id, hardware_id=hardware_id,
        expected_feature_names=feature_names,
        source_reference="synthetic structurally verified fitted-pipeline fixture",
    )

def _scalar(value: float, unit: str) -> SourcedScalar:
    return SourcedScalar(value=value, unit=unit, provenance=P_SRC2)


def _profile() -> SourceKernelCostProfile:
    components = tuple(_poly(component_id) for component_id in C64E_PREFILL_COMPONENT_IDS)
    return SourceKernelCostProfile(
        profile_id="test-v4",
        model_id="meta-llama/Llama-2-7b-hf",
        hardware_id="a100-80gb",
        prefill_components=components,
        prefill_attention=_poly(
            "attn_prefill",
            feature_names=("kv_cache_size", "prefill_chunk_size_squared"),
        ),
        transfer=_poly("send_recv"),
        decode_fixed_seconds_per_output_token=_scalar(0.01, "seconds/output-token"),
        decode_seconds_per_context_token_step=_scalar(
            0.001, "seconds/context-token-step"
        ),
        state_fixed_bytes=_scalar(0.0, "bytes"),
        state_bytes_per_token=_scalar(float(C64E_TRANSFER_BYTES_PER_TOKEN), "bytes/token"),
        memory_capacity_bytes=_scalar(float(C64E_TRANSFER_BYTES_PER_TOKEN * 100), "bytes"),
    )


def test_source_power_order_preserves_bias_and_sklearn_combination_order() -> None:
    assert source_polynomial_powers(2, 2, False, True) == (
        (0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2)
    )
    assert source_polynomial_powers(3, 2, True, False) == (
        (1, 0, 0), (0, 1, 0), (0, 0, 1),
        (1, 1, 0), (1, 0, 1), (0, 1, 1),
    )


def test_model_rejects_canonical_reordering_or_constant_folding() -> None:
    model = _poly(
        "quadratic", degree=2,
        coefficients=(7.0, 2.0, 3.0), intercept=-5.0,
    )
    assert model.powers == ((0,), (1,), (2,))
    assert model.coefficients[0] == 7.0
    assert model.intercept == -5.0
    with pytest.raises(ValueError, match="source row order"):
        replace(model, powers=((1,), (2,), (0,)))
    with pytest.raises(ValueError, match="source row order"):
        replace(model, powers=((1,), (2,)), coefficients=(2.0, 3.0))


def test_model_fails_closed_on_kernel_source_and_grid_drift() -> None:
    model = _poly("identity")
    assert model.evaluation_kernel_id == C64E_EVALUATION_KERNEL_ID
    assert model.source_protocol_fingerprint == C64E_SOURCE_PROTOCOL_FINGERPRINT
    with pytest.raises(ValueError, match="kernel"):
        replace(model, evaluation_kernel_id="different")
    with pytest.raises(ValueError, match="source protocol"):
        replace(model, source_protocol_fingerprint="different")
    with pytest.raises(ValueError, match="grid keys"):
        replace(model, upstream_hyperparameters=model.upstream_hyperparameters[:-1])
    with pytest.raises(ValueError, match="fit_intercept=false"):
        _poly("bad-intercept", fit_intercept=False, intercept=1.0)


def test_profile_binds_exact_component_identity_and_domains_without_evaluation() -> None:
    profile = _profile()
    assert profile.representation_id == C64E_REPRESENTATION_ID
    swapped = list(profile.prefill_components)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(ValueError, match="component order"):
        replace(profile, prefill_components=tuple(swapped))
    with pytest.raises(ValueError, match="prefill_attention"):
        replace(profile, prefill_attention=_poly("wrong-attention"))
    with pytest.raises(ValueError, match="transfer"):
        replace(profile, transfer=_poly("wrong-transfer"))


def test_exhaustive_protocol_is_timing_free_and_has_no_replacement_holdout() -> None:
    protocol = C64E_EXHAUSTIVE_PROTOCOL
    assert protocol.prefill_point_count == 4096
    assert protocol.transfer_point_count == 4096
    record = protocol.to_dict()
    assert record["contains_reference_timings"] is False
    assert record["source_predictions_evaluated_by_this_protocol_freeze"] is False
    boundary = record["boundary_accounting"]
    assert boundary["c64c_boundary_status"] == C64E_INVALIDATED_BOUNDARY_STATUS
    assert boundary["replacement_fresh_boundary"] is None
    assert boundary["holdout_claim"] is False
    assert boundary["generalization_outside_declared_domain_claim"] is False
    encoded = str(record).lower()
    assert "reference_seconds" not in encoded
    assert "observed_seconds" not in encoded
    with pytest.raises(ValueError, match="finite-domain"):
        replace(protocol, prefill_token_max=4095)


def test_exhaustive_protocol_fingerprint_is_deterministic() -> None:
    assert C64E_EXHAUSTIVE_PROTOCOL.fingerprint == ExhaustiveSourceEquivalenceProtocol().fingerprint


def test_dense_transform_matches_sklearn_152_for_frozen_grid_shapes() -> None:
    np = pytest.importorskip("numpy")
    sklearn = pytest.importorskip("sklearn")
    if np.__version__ != "1.26.4" or sklearn.__version__ != "1.5.2":
        pytest.skip("exact C6.4e numerical package versions required")
    from sklearn.preprocessing import PolynomialFeatures

    rows = ((2.0,), (2.0, 3.0), (0.125, -2.5))
    for values in rows:
        for degree in range(1, 6):
            for include_bias in (True, False):
                for interaction_only in (True, False):
                    source = PolynomialFeatures(
                        degree=degree,
                        include_bias=include_bias,
                        interaction_only=interaction_only,
                    )
                    expected = source.fit_transform(np.asarray([values], dtype=np.float64))[0]
                    actual = source_dense_polynomial_row(
                        values,
                        degree=degree,
                        interaction_only=interaction_only,
                        include_bias=include_bias,
                    )
                    assert np.array_equal(actual, expected)
                    expected_powers = tuple(tuple(int(v) for v in row) for row in source.powers_)
                    assert source_polynomial_powers(
                        len(values), degree, interaction_only, include_bias
                    ) == expected_powers


def test_model_prediction_uses_source_matmul_and_intercept_exactly() -> None:
    np = pytest.importorskip("numpy")
    sklearn = pytest.importorskip("sklearn")
    if np.__version__ != "1.26.4" or sklearn.__version__ != "1.5.2":
        pytest.skip("exact C6.4e numerical package versions required")
    from sklearn.preprocessing import PolynomialFeatures as SkPolynomialFeatures

    values = (257.0, 66049.0)
    poly = SkPolynomialFeatures(degree=3, include_bias=True, interaction_only=False)
    transformed = poly.fit_transform(np.asarray([values], dtype=np.float64))[0]
    coefficients = tuple(float(v) for v in np.linspace(-0.75, 1.25, len(transformed)))
    intercept = 0.375
    source_poly = PolynomialFeatures(
        feature_names=("kv_cache_size", "prefill_chunk_size_squared"),
        degree=3, include_bias=True, interaction_only=False,
        powers=tuple(tuple(int(v) for v in row) for row in poly.powers_),
    )
    source_reg = LinearRegression(coefficients=coefficients, intercept=intercept, fit_intercept=True)
    model = export_source_ordered_polynomial(
        Pipeline(source_poly, source_reg, ("kv_cache_size", "prefill_chunk_size_squared")),
        component_id="attn_prefill", hardware_id="a100-80gb",
        expected_feature_names=("kv_cache_size", "prefill_chunk_size_squared"),
        source_reference="synthetic numerical-kernel fixture",
    )
    rows = tuple((float(i), float(i * i)) for i in range(1, 4097))
    transformed_batch = poly.transform(np.asarray(rows, dtype=np.float64))
    expected = transformed_batch @ np.asarray(coefficients) + np.float64(intercept)
    actual = np.asarray(model.evaluate_source_batch(rows))
    assert np.array_equal(actual, expected)
    with pytest.raises(RuntimeError, match="singleton"):
        model.evaluate(values)


def test_full_cost_mechanics_execute_only_under_frozen_numpy() -> None:
    np = pytest.importorskip("numpy")
    if np.__version__ != "1.26.4":
        pytest.skip("exact C6.4e NumPy required")
    try:
        assert_c64e_numerical_runtime()
    except RuntimeError:
        pytest.skip("exact C6.4e numerical runtime required")
    materialized = materialize_source_kernel_domain(_profile())
    estimate = estimate_source_kernel_cost(
        materialized,
        InferenceCostWorkload(
            input_tokens=9,
            output_tokens=4,
            reusable_prefix_tokens=4,
            state_tokens=1,
        ),
    )
    assert math.isfinite(estimate.prefill_seconds)
    assert estimate.recompute_tokens == 5
    assert estimate.decode_context_token_steps == 42
    assert estimate.decode_seconds == pytest.approx(0.082)
    assert estimate.state_bytes == C64E_TRANSFER_BYTES_PER_TOKEN
    with pytest.raises(ValueError, match="token-multiple"):
        materialized.transfer_seconds(C64E_TRANSFER_BYTES_PER_TOKEN + 1)
    with pytest.raises(ValueError, match="lookup domain"):
        materialized.transfer_seconds(C64E_TRANSFER_BYTES_PER_TOKEN * 4097)


def test_exporter_and_content_binding_fail_closed() -> None:
    model = _poly("bound")
    with pytest.raises(ValueError, match="content binding"):
        replace(model, coefficients=tuple(value + 1.0 for value in model.coefficients))
    class NotPipeline:
        pass
    with pytest.raises(TypeError, match="Pipeline"):
        export_source_ordered_polynomial(
            NotPipeline(), component_id="x", hardware_id="a100-80gb",
            expected_feature_names=("num_tokens",), source_reference="bad",
        )


def test_protocol_freezes_source_materialization_batch_shapes() -> None:
    record = C64E_EXHAUSTIVE_PROTOCOL.to_dict()["source_materialization_batches"]
    assert record["compute_and_send_recv"]["row_count"] == 4096
    assert record["attn_prefill"]["row_count"] == 266240
    assert "singleton" in record["request_projection"]
    assert "mandatory in-process" in record["runtime_fence"]


def test_dense_transform_matrix_matches_sklearn_for_multirow_batch() -> None:
    np = pytest.importorskip("numpy")
    sklearn = pytest.importorskip("sklearn")
    if np.__version__ != "1.26.4" or sklearn.__version__ != "1.5.2":
        pytest.skip("exact C6.4e numerical package versions required")
    from sklearn.preprocessing import PolynomialFeatures as SkPolynomialFeatures
    rows = np.asarray([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)], dtype=np.float64)
    source = SkPolynomialFeatures(degree=3, include_bias=True, interaction_only=False)
    expected = source.fit_transform(rows)
    actual = source_dense_polynomial_matrix(
        rows, degree=3, interaction_only=False, include_bias=True
    )
    assert np.array_equal(actual, expected)
