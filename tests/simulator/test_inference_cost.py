from __future__ import annotations

from dataclasses import replace

import pytest

from simulator.inference_cost import (
    C6_COST_ESTIMATE_SCHEMA,
    C6_COST_PROFILE_SCHEMA,
    InferenceCostProfile,
    InferenceCostWorkload,
    ModelValidation,
    ParameterProvenance,
    ParameterSourceClass,
    SensitivityRange,
    SourcedScalar,
    estimate_inference_cost,
)


def _synthetic_scalar(value: float, unit: str, low: float, high: float) -> SourcedScalar:
    return SourcedScalar(
        value=value,
        unit=unit,
        provenance=ParameterProvenance(
            ParameterSourceClass.SYNTHETIC_SENSITIVITY,
            "C6.1 bounded fixture; not a physical calibration",
        ),
        sensitivity=SensitivityRange(low, high),
    )


def _profile() -> InferenceCostProfile:
    return InferenceCostProfile(
        profile_id="fixture-profile-v1",
        model_id="fixture-model",
        hardware_id="fixture-hardware",
        representation_id="fixture-state-representation",
        prefill_fixed_seconds=_synthetic_scalar(1.0, "seconds", 0.5, 1.5),
        prefill_seconds_per_input_token=_synthetic_scalar(
            0.1, "seconds/input-token", 0.05, 0.15
        ),
        decode_fixed_seconds=_synthetic_scalar(2.0, "seconds", 1.0, 3.0),
        decode_seconds_per_context_token_step=_synthetic_scalar(
            0.01, "seconds/context-token-step", 0.005, 0.02
        ),
        state_fixed_bytes=_synthetic_scalar(100.0, "bytes", 50.0, 150.0),
        state_bytes_per_token=_synthetic_scalar(
            10.0, "bytes/token", 5.0, 20.0
        ),
        memory_capacity_bytes=_synthetic_scalar(
            10_000.0, "bytes", 5_000.0, 20_000.0
        ),
        transfer_bandwidth_bytes_per_second=_synthetic_scalar(
            1_000.0, "bytes/second", 500.0, 2_000.0
        ),
        transfer_latency_seconds=_synthetic_scalar(
            0.5, "seconds", 0.25, 1.0
        ),
        validation=ModelValidation(
            metric="bounded-fixture-self-check",
            error_value=0.0,
            unit="fraction",
            reference="C6.1 deterministic fixture only",
        ),
    )


def test_synthetic_parameter_requires_explicit_sensitivity_range() -> None:
    with pytest.raises(ValueError, match="requires an explicit sensitivity range"):
        SourcedScalar(
            value=1.0,
            unit="seconds",
            provenance=ParameterProvenance(
                ParameterSourceClass.SYNTHETIC_SENSITIVITY,
                "synthetic fixture",
            ),
        )


def test_published_parameter_may_be_a_sourced_point_value() -> None:
    value = SourcedScalar(
        value=1.25,
        unit="seconds",
        provenance=ParameterProvenance(
            ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE,
            "example published profile identifier",
        ),
    )
    assert value.value == 1.25
    assert value.sensitivity is None


def test_profile_serialization_and_fingerprint_are_deterministic() -> None:
    profile = _profile()
    assert profile.to_dict()["schema"] == C6_COST_PROFILE_SCHEMA
    assert profile.to_json() == _profile().to_json()
    assert profile.fingerprint == _profile().fingerprint
    assert len(profile.fingerprint) == 64

    changed = replace(
        profile,
        transfer_latency_seconds=_synthetic_scalar(
            0.6, "seconds", 0.25, 1.0
        ),
    )
    assert changed.fingerprint != profile.fingerprint


def test_profile_rejects_unit_ambiguity_and_nonpositive_capacity() -> None:
    profile = _profile()
    with pytest.raises(ValueError, match="unit must be 'seconds/input-token'"):
        replace(
            profile,
            prefill_seconds_per_input_token=_synthetic_scalar(
                0.1, "seconds", 0.05, 0.15
            ),
        )

    with pytest.raises(ValueError, match="memory_capacity_bytes must be positive"):
        replace(
            profile,
            memory_capacity_bytes=_synthetic_scalar(
                0.0, "bytes", -1.0, 1.0
            ),
        )


def test_cost_estimate_is_transparent_and_deterministic() -> None:
    profile = _profile()
    workload = InferenceCostWorkload(
        input_tokens=10,
        output_tokens=3,
        reusable_prefix_tokens=4,
        state_tokens=20,
    )

    estimate = estimate_inference_cost(profile, workload)
    repeat = estimate_inference_cost(profile, workload)

    assert estimate.to_dict()["schema"] == C6_COST_ESTIMATE_SCHEMA
    assert estimate.to_json() == repeat.to_json()
    assert estimate.profile_fingerprint == profile.fingerprint
    assert estimate.prefill_seconds == pytest.approx(2.0)
    assert estimate.decode_context_token_steps == 33
    assert estimate.decode_seconds == pytest.approx(2.33)
    assert estimate.recompute_tokens == 6
    assert estimate.recompute_seconds == pytest.approx(1.6)
    assert estimate.state_bytes == pytest.approx(300.0)
    assert estimate.transfer_seconds == pytest.approx(0.8)
    assert estimate.memory_capacity_fraction == pytest.approx(0.03)


def test_recompute_is_explicitly_derived_from_missing_prefix_work() -> None:
    profile = _profile()
    full_reuse = InferenceCostWorkload(
        input_tokens=10,
        output_tokens=0,
        reusable_prefix_tokens=10,
        state_tokens=10,
    )
    no_reuse = InferenceCostWorkload(
        input_tokens=10,
        output_tokens=0,
        reusable_prefix_tokens=0,
        state_tokens=10,
    )

    warm = estimate_inference_cost(profile, full_reuse)
    cold = estimate_inference_cost(profile, no_reuse)

    assert warm.recompute_tokens == 0
    assert warm.recompute_seconds == 0.0
    assert cold.recompute_tokens == 10
    assert cold.recompute_seconds == cold.prefill_seconds


def test_workload_rejects_invalid_token_domains() -> None:
    with pytest.raises(ValueError, match="reusable_prefix_tokens cannot exceed"):
        InferenceCostWorkload(
            input_tokens=4,
            output_tokens=1,
            reusable_prefix_tokens=5,
            state_tokens=4,
        )

    with pytest.raises(ValueError, match="input_tokens must be a non-negative integer"):
        InferenceCostWorkload(
            input_tokens=-1,
            output_tokens=1,
            reusable_prefix_tokens=0,
            state_tokens=0,
        )
