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
    estimate_source_kernel_cost,
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
    powers = source_polynomial_powers(
        len(feature_names), degree, interaction_only, include_bias
    )
    if coefficients is None:
        coefficients = tuple(0.0 if not any(p) else 1.0 for p in powers)
    return SourceOrderedPolynomialModel(
        component_id=component_id,
        hardware_id=hardware_id,
        feature_names=feature_names,
        powers=powers,
        coefficients=coefficients,
        intercept=intercept,
        upstream_hyperparameters=_hyperparameters(
            degree=degree,
            include_bias=include_bias,
            interaction_only=interaction_only,
            fit_intercept=fit_intercept,
        ),
        source_reference="synthetic structural source fixture",
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
    from sklearn.preprocessing import PolynomialFeatures

    values = (257.0, 66049.0)
    poly = PolynomialFeatures(degree=3, include_bias=True, interaction_only=False)
    transformed = poly.fit_transform(np.asarray([values], dtype=np.float64))[0]
    coefficients = tuple(float(v) for v in np.linspace(-0.75, 1.25, len(transformed)))
    intercept = 0.375
    model = SourceOrderedPolynomialModel(
        component_id="attn_prefill",
        hardware_id="a100-80gb",
        feature_names=("kv_cache_size", "prefill_chunk_size_squared"),
        powers=tuple(tuple(int(v) for v in row) for row in poly.powers_),
        coefficients=coefficients,
        intercept=intercept,
        upstream_hyperparameters=_hyperparameters(degree=3),
        source_reference="synthetic numerical-kernel fixture",
    )
    expected = float((np.asarray([transformed]) @ np.asarray(coefficients) + np.float64(intercept))[0])
    assert model.evaluate(values) == expected


def test_full_cost_mechanics_execute_only_under_frozen_numpy() -> None:
    np = pytest.importorskip("numpy")
    if np.__version__ != "1.26.4":
        pytest.skip("exact C6.4e NumPy required")
    estimate = estimate_source_kernel_cost(
        _profile(),
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
        _profile().transfer_seconds(C64E_TRANSFER_BYTES_PER_TOKEN + 1)
    with pytest.raises(ValueError, match="lookup domain"):
        _profile().transfer_seconds(C64E_TRANSFER_BYTES_PER_TOKEN * 4097)
