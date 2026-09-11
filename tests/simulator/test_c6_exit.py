from __future__ import annotations

import json

import pytest

from experiments.c6_exit import C6_EXIT_SCHEMA, build_exit_summary
from simulator import InferenceCostWorkload
from simulator.inference_cost_runtime import (
    C64F_ARTIFACT_SHA256,
    C64F_EVIDENCE_CLASS,
    C64F_REPRESENTATION_ID,
    C64F_SCIENTIFIC_FINGERPRINT,
    estimate_validated_runtime_cost,
    load_c64f_runtime_profiles,
)


def test_c6_exit_summary_is_deterministic_and_provenance_bound() -> None:
    first = build_exit_summary()
    second = build_exit_summary()
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
    assert first["schema"] == C6_EXIT_SCHEMA
    assert first["artifact_sha256"] == C64F_ARTIFACT_SHA256
    assert first["scientific_fingerprint"] == C64F_SCIENTIFIC_FINGERPRINT
    assert first["representation_id"] == C64F_REPRESENTATION_ID
    assert first["evidence_class"] == C64F_EVIDENCE_CLASS
    assert first["hardware"] == ["a100-80gb", "h100-80gb"]
    assert first["prefill_points_per_hardware"] == 4096
    assert first["transfer_points_per_hardware"] == 4096
    assert first["rejected_out_of_domain_cases"] == [
        "decode_zero_context",
        "decode_over_max_model_length",
        "transfer_over_accepted_domain",
    ]


@pytest.mark.parametrize("hardware_id", ["a100-80gb", "h100-80gb"])
def test_c6_exit_boundary_valid_workload_is_accepted(hardware_id: str) -> None:
    profile = load_c64f_runtime_profiles()[hardware_id]
    estimate = estimate_validated_runtime_cost(
        profile,
        InferenceCostWorkload(
            input_tokens=4095,
            output_tokens=1,
            reusable_prefix_tokens=4095,
            state_tokens=64,
        ),
    )
    assert estimate.prefill_seconds == profile.prefill_seconds(4095)
    assert estimate.decode_context_token_steps == 4095
    assert estimate.recompute_seconds == 0.0
    assert estimate.state_bytes == 64 * 524288
    assert estimate.transfer_seconds == profile.transfer_seconds(4096 * 8192)


@pytest.mark.parametrize("hardware_id", ["a100-80gb", "h100-80gb"])
def test_decode_fails_closed_outside_carried_c63_domain(hardware_id: str) -> None:
    profile = load_c64f_runtime_profiles()[hardware_id]
    with pytest.raises(ValueError, match="positive context"):
        estimate_validated_runtime_cost(
            profile,
            InferenceCostWorkload(0, 1, 0, 0),
        )
    with pytest.raises(ValueError, match="max-model-length"):
        estimate_validated_runtime_cost(
            profile,
            InferenceCostWorkload(4096, 1, 4096, 0),
        )


def test_transfer_fails_closed_above_accepted_source_domain() -> None:
    profile = load_c64f_runtime_profiles()["a100-80gb"]
    with pytest.raises(ValueError, match="accepted lookup domain"):
        estimate_validated_runtime_cost(
            profile,
            InferenceCostWorkload(1, 0, 1, 65),
        )
