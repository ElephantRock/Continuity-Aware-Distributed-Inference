from __future__ import annotations

import json

import pytest

from experiments.c7_protocol import (
    AXES,
    C6_MAX_VALIDATED_TRANSFER_BYTES,
    C7ExperimentManifest,
    C7_P5_STATE_TOKENS,
    C7_PROTOCOL_FINGERPRINT,
    C7_STOCHASTIC_SEEDS,
    C7_SUPPORTED_HARDWARE_IDS,
    EfficiencyEligibility,
    ExperimentSeries,
    FROZEN_C7_PROTOCOL,
    MetricID,
    ParameterSource,
    WorkloadClass,
    cold_continuation_rate,
    efficiency_eligibility,
    recomputation_ratio,
    residency_ratios,
    source_request_c6_admissible,
    state_reuse_ratio,
    state_reuse_token_ratio,
    synthetic_request_c6_admissible,
    validated_transfer_bytes_for_state_tokens,
    validated_transfer_state_admissible,
)
from simulator.policies import PolicyID


EXPECTED_PROTOCOL_FINGERPRINT = (
    "706e0d5fff362a1eda8c906b957c914251c6e7949bac6be3c2c143ae21625474"
)


def _manifest(**overrides: object) -> C7ExperimentManifest:
    values: dict[str, object] = {
        "experiment_id": "c7.1-test",
        "git_commit": "f" * 40,
        "protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
        "series": ExperimentSeries.P1_DEEP_REUSE,
        "policy_id": PolicyID.B4,
        "workload_class": WorkloadClass.TRACE_AUGMENTED,
        "hardware_id": "a100-80gb",
        "program_objective": "all requests in the synthetic Session complete",
        "seed": 0,
        "source_dataset_fingerprint": "a" * 64,
        "augmentation_fingerprint": "b" * 64,
        "parameters": (("session_depth", 8),),
        "parameter_sources": (("session_depth", ParameterSource.P_SRC4),),
    }
    values.update(overrides)
    return C7ExperimentManifest(**values)  # type: ignore[arg-type]


def test_source_admissibility_is_exact_and_never_clips() -> None:
    assert source_request_c6_admissible(1, 1)
    assert source_request_c6_admissible(4095, 1)
    assert not source_request_c6_admissible(4096, 1)
    assert not source_request_c6_admissible(1, 4096)
    assert not source_request_c6_admissible(0, 1)
    assert not source_request_c6_admissible(1, 0)
    with pytest.raises(ValueError):
        source_request_c6_admissible(-1, 1)


def test_synthetic_controls_respect_c6_runtime_domain() -> None:
    assert synthetic_request_c6_admissible(0, 0)
    assert synthetic_request_c6_admissible(4096, 0)
    assert synthetic_request_c6_admissible(4095, 1)
    assert not synthetic_request_c6_admissible(4096, 1)
    assert not synthetic_request_c6_admissible(0, 1)


def test_p5_transfer_points_are_exact_not_merely_bounded() -> None:
    for state_tokens in C7_P5_STATE_TOKENS:
        assert validated_transfer_state_admissible(state_tokens)
        transfer_bytes = validated_transfer_bytes_for_state_tokens(state_tokens)
        assert transfer_bytes <= C6_MAX_VALIDATED_TRANSFER_BYTES
        assert transfer_bytes % 8192 == 0

    for state_tokens in (0, 2, 3, 8, 32, 63, 65):
        assert not validated_transfer_state_admissible(state_tokens)
        with pytest.raises(ValueError):
            validated_transfer_bytes_for_state_tokens(state_tokens)


def test_sweep_axis_choices_are_p_src4_not_cost_evidence() -> None:
    assert AXES["state_tokens"].values == C7_P5_STATE_TOKENS
    assert all(axis.source is ParameterSource.P_SRC4 for axis in AXES.values())


def test_metric_semantics_are_executable_and_bounded() -> None:
    assert recomputation_ratio(
        total_input_tokens=1000,
        eligible_reuse_tokens=400,
        consumed_reuse_tokens=250,
    ) == pytest.approx(0.15)
    assert state_reuse_ratio(eligible_opportunities=4, consumed_opportunities=3) == 0.75
    assert state_reuse_token_ratio(eligible_tokens=400, consumed_tokens=100) == 0.25
    assert cold_continuation_rate(
        eligible_continuations=10, fully_reconstructed_continuations=2
    ) == 0.2
    useful, wasted = residency_ratios(useful_byte_seconds=3.0, wasted_byte_seconds=1.0)
    assert useful == 0.75
    assert wasted == 0.25

    with pytest.raises(ValueError):
        recomputation_ratio(
            total_input_tokens=100,
            eligible_reuse_tokens=40,
            consumed_reuse_tokens=41,
        )
    with pytest.raises(ValueError):
        state_reuse_ratio(eligible_opportunities=1, consumed_opportunities=2)


def test_every_required_metric_has_one_definition() -> None:
    protocol_metrics = FROZEN_C7_PROTOCOL.to_dict()["metrics"]
    assert {item["metric_id"] for item in protocol_metrics} == {metric.value for metric in MetricID}


def test_semantic_violation_excludes_efficiency_ranking() -> None:
    assert efficiency_eligibility(covered_semantic_violations=0) is EfficiencyEligibility.ELIGIBLE
    assert (
        efficiency_eligibility(covered_semantic_violations=1)
        is EfficiencyEligibility.SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING
    )


def test_protocol_identity_is_canonical_deterministic_and_frozen() -> None:
    first = FROZEN_C7_PROTOCOL.to_dict()
    second = FROZEN_C7_PROTOCOL.to_dict()
    assert first == second
    assert FROZEN_C7_PROTOCOL.fingerprint == C7_PROTOCOL_FINGERPRINT
    assert C7_PROTOCOL_FINGERPRINT == EXPECTED_PROTOCOL_FINGERPRINT
    assert len(C7_PROTOCOL_FINGERPRINT) == 64
    assert C7_STOCHASTIC_SEEDS == tuple(range(64))
    assert C7_SUPPORTED_HARDWARE_IDS == ("a100-80gb", "h100-80gb")
    encoded = json.dumps(first, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert json.loads(encoded) == first


def test_manifest_binds_frozen_protocol_workload_and_hardware_provenance() -> None:
    manifest = _manifest()
    assert manifest.to_dict()["protocol_fingerprint"] == C7_PROTOCOL_FINGERPRINT

    with pytest.raises(ValueError, match="frozen C7.1 protocol"):
        _manifest(protocol_fingerprint="0" * 64)

    with pytest.raises(ValueError, match="accepted C6 runtime family"):
        _manifest(hardware_id="A100-80GB")

    with pytest.raises(ValueError, match="REAL-TRACE"):
        _manifest(
            workload_class=WorkloadClass.REAL_TRACE,
            seed=0,
            augmentation_fingerprint=None,
        )

    real = _manifest(
        workload_class=WorkloadClass.REAL_TRACE,
        seed=None,
        augmentation_fingerprint=None,
    )
    assert real.source_dataset_fingerprint == "a" * 64

    with pytest.raises(ValueError, match="TRACE-AUGMENTED"):
        _manifest(augmentation_fingerprint=None)

    synthetic = _manifest(
        workload_class=WorkloadClass.SYNTHETIC_STRESS,
        source_dataset_fingerprint=None,
        augmentation_fingerprint=None,
    )
    assert synthetic.seed == 0


def test_manifest_requires_canonical_one_to_one_parameter_sources() -> None:
    with pytest.raises(ValueError, match="canonically ordered"):
        _manifest(
            parameters=(("z", 1), ("a", 2)),
            parameter_sources=(("a", ParameterSource.P_SRC4), ("z", ParameterSource.P_SRC4)),
        )
    with pytest.raises(ValueError, match="exactly one source"):
        _manifest(
            parameters=(("session_depth", 8),),
            parameter_sources=(("worker_count", ParameterSource.P_SRC4),),
        )


def test_manifest_fences_seed_sequence_and_frozen_axis_values() -> None:
    with pytest.raises(ValueError, match="0..63"):
        _manifest(seed=64)

    with pytest.raises(ValueError, match="outside the frozen C7.1 axis"):
        _manifest(parameters=(("session_depth", 3),))

    with pytest.raises(ValueError, match="source class"):
        _manifest(
            parameters=(("session_depth", 8),),
            parameter_sources=(("session_depth", ParameterSource.P_SRC2),),
        )
