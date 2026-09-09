from __future__ import annotations

from dataclasses import replace
import math

import pytest

from simulator.c64d_export import (
    C64D_DEVICE_CAPACITY_BYTES,
    C64D_EQUIVALENCE_ULP_BUDGET,
    C64D_KV_BYTES_PER_TOKEN,
    carried_decode_scalars,
    compile_source_polynomial_profile,
    export_canonical_polynomial,
    source_equivalence_report,
    state_memory_scalars,
)
from simulator.inference_cost_v3 import C64C_REPRESENTATION_ID


class _Array:
    def __init__(self, value):
        self._value = value

    def tolist(self):
        return self._value


def _named_class(name: str):
    return type(name, (), {})


def _pipeline(
    *,
    features=("num_tokens",),
    powers=((0,), (1,), (2,)),
    coefficients=(1.5, 2.0, 3.0),
    intercept=4.0,
    degree=2,
    include_bias=True,
    interaction_only=False,
    fit_intercept=True,
):
    PolynomialFeatures = _named_class("PolynomialFeatures")
    LinearRegression = _named_class("LinearRegression")
    Pipeline = _named_class("Pipeline")

    poly = PolynomialFeatures()
    poly.feature_names_in_ = _Array(list(features))
    poly.n_features_in_ = len(features)
    poly.degree = degree
    poly.include_bias = include_bias
    poly.interaction_only = interaction_only
    poly.powers_ = _Array([list(row) for row in powers])

    regression = LinearRegression()
    regression.fit_intercept = fit_intercept
    regression.coef_ = _Array(list(coefficients))
    regression.intercept_ = intercept

    pipeline = Pipeline()
    pipeline.feature_names_in_ = _Array(list(features))
    pipeline.named_steps = {
        "polynomialfeatures": poly,
        "linearregression": regression,
    }
    return pipeline


def _export(pipeline=None, *, component="add", features=("num_tokens",)):
    return export_canonical_polynomial(
        _pipeline(features=features) if pipeline is None else pipeline,
        component_id=component,
        hardware_id="a100-80gb",
        expected_feature_names=features,
        source_reference="synthetic source-pipeline fixture",
    )


def test_export_folds_source_bias_and_canonicalizes_power_order() -> None:
    pipeline = _pipeline(
        features=("x", "y"),
        powers=((0, 0), (1, 0), (0, 1), (2, 0), (1, 1), (0, 2)),
        coefficients=(7.0, 2.0, 3.0, 5.0, 11.0, 13.0),
        intercept=17.0,
        degree=2,
    )
    model = export_canonical_polynomial(
        pipeline,
        component_id="attn_prefill",
        hardware_id="a100-80gb",
        expected_feature_names=("x", "y"),
        source_reference="fixture",
    )
    assert model.intercept == 24.0
    assert model.powers == ((0, 1), (0, 2), (1, 0), (1, 1), (2, 0))
    assert model.coefficients == (3.0, 13.0, 2.0, 11.0, 5.0)
    assert model.evaluate((2.0, 3.0)) == pytest.approx(
        24.0 + 3.0 * 3 + 13.0 * 9 + 2.0 * 2 + 11.0 * 6 + 5.0 * 4
    )


def test_export_binds_selected_source_hyperparameters() -> None:
    model = _export()
    assert model.upstream_hyperparameters == (
        ("linearregression__fit_intercept", "true"),
        ("polynomialfeatures__degree", "2"),
        ("polynomialfeatures__include_bias", "true"),
        ("polynomialfeatures__interaction_only", "false"),
    )


def test_export_fails_closed_on_pipeline_or_feature_drift() -> None:
    pipeline = _pipeline()
    pipeline.named_steps = {"linearregression": pipeline.named_steps["linearregression"]}
    with pytest.raises(ValueError, match="exactly PolynomialFeatures"):
        _export(pipeline)

    with pytest.raises(ValueError, match="feature order mismatch"):
        export_canonical_polynomial(
            _pipeline(features=("other",)),
            component_id="add",
            hardware_id="a100-80gb",
            expected_feature_names=("num_tokens",),
            source_reference="fixture",
        )


def test_export_fails_closed_on_basis_or_coefficient_drift() -> None:
    with pytest.raises(ValueError, match="powers_ and LinearRegression coef_"):
        _export(_pipeline(coefficients=(1.0, 2.0)))

    with pytest.raises(ValueError, match="exactly match the frozen PolynomialFeatures"):
        _export(
            _pipeline(
                powers=((0,), (1,), (3,)),
                coefficients=(0.0, 1.0, 2.0),
                degree=2,
            )
        )


def test_source_equivalence_report_enforces_eight_ulp_budget() -> None:
    model = _export(
        _pipeline(
            powers=((0,), (1,)),
            coefficients=(0.0, 2.0),
            intercept=1.0,
            degree=1,
        )
    )
    rows = [(1.0,), (2.0,), (3.0,)]
    exact = [3.0, 5.0, 7.0]
    report = source_equivalence_report(
        model, feature_rows=rows, source_predictions=exact
    )
    assert report["decision"] == "PASS"
    assert report["violation_count"] == 0

    too_far = list(exact)
    too_far[1] = 5.0 + (C64D_EQUIVALENCE_ULP_BUDGET + 1) * math.ulp(5.0)
    report = source_equivalence_report(
        model, feature_rows=rows, source_predictions=too_far
    )
    assert report["decision"] == "FAIL"
    assert report["violation_count"] == 1


def test_source_equivalence_counts_true_binary64_steps_across_binade() -> None:
    model = _export(
        _pipeline(
            powers=((0,), (1,)),
            coefficients=(0.0, 0.0),
            intercept=1.0,
            degree=1,
        )
    )
    reference = 1.0
    for _ in range(16):
        reference = math.nextafter(reference, 0.0)
    report = source_equivalence_report(
        model, feature_rows=[(1.0,)], source_predictions=[reference]
    )
    assert report["max_ulp_distance"] == 16
    assert report["violation_count"] == 1
    assert report["decision"] == "FAIL"


def test_carried_decode_is_exact_c63_evidence() -> None:
    a_fixed, a_slope = carried_decode_scalars("a100-80gb")
    h_fixed, h_slope = carried_decode_scalars("h100-80gb")
    assert a_fixed.value == 0.00954034447272924
    assert a_slope.value == 3.7204476520608805e-07
    assert h_fixed.value == 0.005806526349997292
    assert h_slope.value == 2.1500094784162922e-07
    assert "C6.3 held-out adequate" in a_fixed.provenance.reference


def test_state_memory_scalars_are_source_derived_vidur_mechanics() -> None:
    fixed, per_token, capacity = state_memory_scalars("a100-80gb")
    assert fixed.value == 0.0
    assert per_token.value == float(C64D_KV_BYTES_PER_TOKEN) == 524288.0
    assert capacity.value == float(C64D_DEVICE_CAPACITY_BYTES) == 85899345920.0
    assert "memory_planner.py" in per_token.provenance.reference
    assert state_memory_scalars("h100-80gb")[2].value == capacity.value


def test_compile_profile_requires_exact_v3_component_contract() -> None:
    component_ids = (
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
    components = tuple(_export(component=component) for component in component_ids)
    attention = export_canonical_polynomial(
        _pipeline(
            features=("kv_cache_size", "prefill_chunk_size_squared"),
            powers=((0, 0), (1, 0), (0, 1)),
            coefficients=(0.0, 0.0, 1.0),
            degree=1,
        ),
        component_id="attn_prefill",
        hardware_id="a100-80gb",
        expected_feature_names=("kv_cache_size", "prefill_chunk_size_squared"),
        source_reference="fixture",
    )
    transfer = _export(component="send_recv")
    profile = compile_source_polynomial_profile(
        hardware_id="a100-80gb",
        prefill_components=components,
        prefill_attention=attention,
        transfer=transfer,
    )
    assert profile.representation_id == C64C_REPRESENTATION_ID
    assert profile.state_bytes_per_token.value == 524288.0
    assert len(profile.fingerprint) == 64

    with pytest.raises(ValueError, match="exact canonical Vidur component order"):
        replace(profile, prefill_components=tuple(reversed(components)))
