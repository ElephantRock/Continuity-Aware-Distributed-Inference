from dataclasses import replace
import math

import pytest

from simulator.calibration_validation import ReferenceKind
from simulator.inference_cost import (
    InferenceCostWorkload,
    ParameterProvenance,
    ParameterSourceClass,
    SourcedScalar,
)
from simulator.inference_cost_v2 import (
    C64A_FRESH_PREFILL_AXES,
    C64A_FRESH_TRANSFER_AXES,
)
from simulator.inference_cost_v3 import (
    C64C_BOUNDARY_ALGORITHM_ID,
    C64C_BOUNDARY_SEED,
    C64C_FRESH_PREFILL_AXES,
    C64C_FRESH_PREFILL_BOUNDARY,
    C64C_FRESH_TRANSFER_AXES,
    C64C_FRESH_TRANSFER_BOUNDARY,
    C64C_REPRESENTATION_ID,
    C64C_SOURCE_PROTOCOL_FINGERPRINT,
    C64C_TRANSFER_BYTES_PER_TOKEN,
    CanonicalPolynomialModel,
    SecondFreshAdequacyBoundary,
    SourcePolynomialCostProfile,
    estimate_source_polynomial_cost,
    select_second_boundary_axes,
    verify_second_boundary_against_history,
)


P_SRC2 = ParameterProvenance(
    source_class=ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE,
    reference="test-only pinned source fixture",
)
HYPERPARAMETERS = (
    ("linearregression__fit_intercept", "true"),
    ("polynomialfeatures__degree", "1"),
    ("polynomialfeatures__include_bias", "true"),
    ("polynomialfeatures__interaction_only", "false"),
)
DEGREE2_HYPERPARAMETERS = (
    ("linearregression__fit_intercept", "true"),
    ("polynomialfeatures__degree", "2"),
    ("polynomialfeatures__include_bias", "true"),
    ("polynomialfeatures__interaction_only", "false"),
)
INTERACTION_ONLY_HYPERPARAMETERS = (
    ("linearregression__fit_intercept", "true"),
    ("polynomialfeatures__degree", "2"),
    ("polynomialfeatures__include_bias", "true"),
    ("polynomialfeatures__interaction_only", "true"),
)
NO_CONSTANT_HYPERPARAMETERS = (
    ("linearregression__fit_intercept", "false"),
    ("polynomialfeatures__degree", "1"),
    ("polynomialfeatures__include_bias", "false"),
    ("polynomialfeatures__interaction_only", "false"),
)
PREFILL_COMPONENT_IDS = (
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


def _scalar(value: float, unit: str) -> SourcedScalar:
    return SourcedScalar(value=value, unit=unit, provenance=P_SRC2)


def _poly(
    component_id: str,
    *,
    hardware_id: str = "a100-80gb",
    feature_names: tuple[str, ...] = ("num_tokens",),
    powers: tuple[tuple[int, ...], ...] = ((1,),),
    coefficients: tuple[float, ...] = (1.0,),
    intercept: float = 0.0,
    upstream_hyperparameters: tuple[tuple[str, str], ...] = HYPERPARAMETERS,
) -> CanonicalPolynomialModel:
    return CanonicalPolynomialModel(
        component_id=component_id,
        hardware_id=hardware_id,
        feature_names=feature_names,
        powers=powers,
        coefficients=coefficients,
        intercept=intercept,
        upstream_hyperparameters=upstream_hyperparameters,
        source_reference="synthetic contract-only source model fixture",
    )


def _profile() -> SourcePolynomialCostProfile:
    components = tuple(_poly(component_id) for component_id in PREFILL_COMPONENT_IDS)
    return SourcePolynomialCostProfile(
        profile_id="test-v3",
        model_id="meta-llama/Llama-2-7b-hf",
        hardware_id="a100-80gb",
        prefill_components=components,
        prefill_attention=_poly(
            "attn_prefill",
            feature_names=("kv_cache_size", "prefill_chunk_size_squared"),
            powers=((0, 1), (1, 0)),
            coefficients=(1.0, 0.0),
        ),
        transfer=_poly("send_recv"),
        decode_fixed_seconds_per_output_token=_scalar(
            0.01, "seconds/output-token"
        ),
        decode_seconds_per_context_token_step=_scalar(
            0.001, "seconds/context-token-step"
        ),
        state_fixed_bytes=_scalar(0.0, "bytes"),
        state_bytes_per_token=_scalar(
            float(C64C_TRANSFER_BYTES_PER_TOKEN), "bytes/token"
        ),
        memory_capacity_bytes=_scalar(
            float(C64C_TRANSFER_BYTES_PER_TOKEN * 100), "bytes"
        ),
    )


def _cold_prefill_axes() -> list[int]:
    return (
        list(range(64, 129, 16))
        + list(range(160, 1025, 32))
        + list(range(1088, 4097, 64))
    )


def _transfer_axes() -> list[int]:
    axes = list(range(2048, 1042432 + 1, 8192))
    axes.append(1048576)
    axes.extend(range(1081344, 16777216 + 1, 32768))
    axes.extend(range(16908288, 67108864 + 1, 131072))
    return axes


def test_canonical_polynomial_has_fixed_expanded_evaluation_semantics() -> None:
    model = _poly(
        "quadratic",
        powers=((1,), (2,)),
        coefficients=(2.0, 3.0),
        intercept=-5.0,
        upstream_hyperparameters=DEGREE2_HYPERPARAMETERS,
    )
    assert model.evaluate((4.0,)) == pytest.approx(-5.0 + 2.0 * 4.0 + 3.0 * 16.0)
    assert model.fingerprint == model.fingerprint
    assert model.source_protocol_fingerprint == C64C_SOURCE_PROTOCOL_FINGERPRINT


def test_canonical_polynomial_rejects_ambiguous_or_invalid_basis() -> None:
    with pytest.raises(ValueError, match="constant monomials"):
        _poly("constant-leak", powers=((0,),))
    with pytest.raises(ValueError, match="lexicographically sorted"):
        _poly(
            "unsorted",
            powers=((2,), (1,)),
            coefficients=(1.0, 1.0),
        )
    with pytest.raises(ValueError, match="finite"):
        _poly("nonfinite", coefficients=(math.inf,))


def test_canonical_polynomial_binds_frozen_hardware_and_grid_search_metadata() -> None:
    with pytest.raises(ValueError, match="frozen C6.4c source family"):
        _poly("wrong-hardware", hardware_id="other-hardware")

    model = _poly("metadata")
    with pytest.raises(ValueError, match="exact frozen Vidur grid-search keys"):
        replace(model, upstream_hyperparameters=HYPERPARAMETERS[:-1])
    with pytest.raises(ValueError, match="canonical true/false"):
        replace(
            model,
            upstream_hyperparameters=(
                ("linearregression__fit_intercept", "1"),
                ("polynomialfeatures__degree", "1"),
                ("polynomialfeatures__include_bias", "true"),
                ("polynomialfeatures__interaction_only", "false"),
            ),
        )


def test_polynomial_basis_must_match_recorded_source_hyperparameters() -> None:
    with pytest.raises(ValueError, match="exactly match the frozen PolynomialFeatures"):
        _poly(
            "degree-mismatch",
            powers=((1,), (2,)),
            coefficients=(1.0, 1.0),
        )

    with pytest.raises(ValueError, match="exactly match the frozen PolynomialFeatures"):
        _poly(
            "interaction-mismatch",
            feature_names=("x", "y"),
            powers=((0, 1), (1, 0), (2, 0)),
            coefficients=(1.0, 1.0, 1.0),
            upstream_hyperparameters=INTERACTION_ONLY_HYPERPARAMETERS,
        )

    valid_interaction = _poly(
        "interaction-valid",
        feature_names=("x", "y"),
        powers=((0, 1), (1, 0), (1, 1)),
        coefficients=(1.0, 1.0, 1.0),
        upstream_hyperparameters=INTERACTION_ONLY_HYPERPARAMETERS,
    )
    assert valid_interaction.evaluate((2.0, 3.0)) == pytest.approx(11.0)


def test_no_source_constant_requires_zero_canonical_intercept() -> None:
    with pytest.raises(ValueError, match="intercept must be zero"):
        _poly(
            "constant-mismatch",
            intercept=0.5,
            upstream_hyperparameters=NO_CONSTANT_HYPERPARAMETERS,
        )
    valid = _poly(
        "constant-valid",
        intercept=0.0,
        upstream_hyperparameters=NO_CONSTANT_HYPERPARAMETERS,
    )
    assert valid.evaluate((3.0,)) == pytest.approx(3.0)


def test_component_predictions_are_not_clipped_before_composition() -> None:
    negative = _poly("negative", coefficients=(-1.0,))
    assert negative.evaluate((3.0,)) == -3.0


def test_profile_binds_source_semantics_and_component_identity() -> None:
    profile = _profile()
    assert profile.representation_id == C64C_REPRESENTATION_ID
    assert profile.to_dict()["source_protocol_fingerprint"] == C64C_SOURCE_PROTOCOL_FINGERPRINT

    swapped = list(profile.prefill_components)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(ValueError, match="exact canonical Vidur component order"):
        replace(profile, prefill_components=tuple(swapped))

    with pytest.raises(TypeError, match="CanonicalPolynomialModel"):
        replace(profile, prefill_components=("not-a-model",))

    with pytest.raises(ValueError, match="cold-prefill model"):
        replace(profile, prefill_attention=_poly("wrong-attention"))

    with pytest.raises(ValueError, match="frozen C6.4c source model"):
        replace(profile, model_id="other-model")

    with pytest.raises(ValueError, match="frozen C6.4c source family"):
        replace(profile, hardware_id="other-hardware")


def test_cold_prefill_composes_rounded_compute_exact_kv_save_and_attention() -> None:
    profile = _profile()
    # n=9 => nine rounded compute components at 16, exact KV-save at 9,
    # and cold attention at (kv_cache=0, n^2=81): 9*16 + 9 + 81 = 234 ms/layer.
    assert profile.prefill_seconds(9) == pytest.approx(234.0 * 32.0 * 1e-3)
    assert profile.prefill_seconds(0) == 0.0
    with pytest.raises(ValueError, match="outside the frozen source domain"):
        profile.prefill_seconds(4097)


def test_recompute_decode_state_transfer_and_memory_mechanics_are_preserved() -> None:
    estimate = estimate_source_polynomial_cost(
        _profile(),
        InferenceCostWorkload(
            input_tokens=9,
            output_tokens=4,
            reusable_prefix_tokens=4,
            state_tokens=1,
        ),
    )
    assert estimate.prefill_seconds == pytest.approx(7.488)
    assert estimate.recompute_tokens == 5
    # n=5 => 9 rounded components at 8 + exact KV-save 5 + n^2 attention 25.
    assert estimate.recompute_seconds == pytest.approx(102.0 * 32.0 * 1e-3)
    assert estimate.decode_context_token_steps == 42
    assert estimate.decode_seconds == pytest.approx(4 * 0.01 + 42 * 0.001)
    assert estimate.state_bytes == pytest.approx(C64C_TRANSFER_BYTES_PER_TOKEN)
    assert estimate.transfer_seconds == pytest.approx(0.001)
    assert estimate.memory_capacity_fraction == pytest.approx(0.01)


def test_transfer_requires_exact_materialized_source_key_domain() -> None:
    profile = _profile()
    assert profile.transfer_seconds(0) == 0.0
    assert profile.transfer_seconds(C64C_TRANSFER_BYTES_PER_TOKEN * 7) == pytest.approx(0.007)
    with pytest.raises(ValueError, match="token-multiple"):
        profile.transfer_seconds(C64C_TRANSFER_BYTES_PER_TOKEN + 1)
    with pytest.raises(ValueError, match="lookup domain"):
        profile.transfer_seconds(C64C_TRANSFER_BYTES_PER_TOKEN * 4097)


def test_second_boundaries_are_timing_free_and_frozen() -> None:
    for boundary in (C64C_FRESH_PREFILL_BOUNDARY, C64C_FRESH_TRANSFER_BOUNDARY):
        record = boundary.to_dict()
        assert record["contains_reference_timings"] is False
        assert record["selection_algorithm_id"] == C64C_BOUNDARY_ALGORITHM_ID
        assert record["selection_seed"] == C64C_BOUNDARY_SEED
        encoded = str(record).lower()
        assert "reference_seconds" not in encoded
        assert "observed_seconds" not in encoded

    with pytest.raises(ValueError, match="differ from the frozen"):
        SecondFreshAdequacyBoundary(
            reference_kind=ReferenceKind.COLD_PREFILL,
            axis_unit="input-tokens",
            axes=C64C_FRESH_PREFILL_AXES[:-1],
        )


def test_prefill_second_boundary_reproduces_from_complete_identity_history() -> None:
    history = _cold_prefill_axes()
    assert len(history) == 81
    assert select_second_boundary_axes(ReferenceKind.COLD_PREFILL, history) == C64C_FRESH_PREFILL_AXES
    verify_second_boundary_against_history(C64C_FRESH_PREFILL_BOUNDARY, history)
    assert not set(C64C_FRESH_PREFILL_AXES) & set(history)
    assert not set(C64C_FRESH_PREFILL_AXES) & set(C64A_FRESH_PREFILL_AXES)


def test_transfer_second_boundary_reproduces_from_complete_identity_history() -> None:
    history = _transfer_axes()
    assert len(history) == 993
    assert (
        select_second_boundary_axes(ReferenceKind.POINT_TO_POINT_TRANSFER, history)
        == C64C_FRESH_TRANSFER_AXES
    )
    verify_second_boundary_against_history(C64C_FRESH_TRANSFER_BOUNDARY, history)
    assert not set(C64C_FRESH_TRANSFER_AXES) & set(history)
    assert not set(C64C_FRESH_TRANSFER_AXES) & set(C64A_FRESH_TRANSFER_AXES)
    assert all(axis % C64C_TRANSFER_BYTES_PER_TOKEN == 0 for axis in C64C_FRESH_TRANSFER_AXES)
    assert all(
        1 <= axis // C64C_TRANSFER_BYTES_PER_TOKEN <= 4096
        for axis in C64C_FRESH_TRANSFER_AXES
    )


def test_second_boundary_verifier_fails_closed_on_incomplete_history() -> None:
    with pytest.raises(ValueError, match="historical axes do not match"):
        verify_second_boundary_against_history(
            C64C_FRESH_PREFILL_BOUNDARY,
            _cold_prefill_axes()[:-1],
        )
