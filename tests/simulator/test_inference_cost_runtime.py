from __future__ import annotations

import math
from pathlib import Path
import tomllib

import pytest

from simulator import DiscreteEventSimulator, InferenceCostWorkload, ResourceModel, TaskStatus
from simulator.inference_cost_runtime import (
    C64F_ARTIFACT_SHA256,
    C64F_EVIDENCE_CLASS,
    C64F_REPRESENTATION_ID,
    C64F_SCIENTIFIC_FINGERPRINT,
    C64G_TRANSFER_BYTES_PER_TOKEN,
    estimate_validated_runtime_cost,
    enqueue_validated_inference_task,
    load_c64f_runtime_profiles,
)


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts" / "c6.4f" / "exhaustive-source-equivalence.json"


@pytest.fixture(scope="module")
def profiles():
    return load_c64f_runtime_profiles(ARTIFACT)


def test_exact_accepted_artifact_loads_both_hardware_profiles(profiles) -> None:
    assert set(profiles) == {"a100-80gb", "h100-80gb"}
    for hardware_id, profile in profiles.items():
        assert profile.hardware_id == hardware_id
        assert profile.artifact_sha256 == C64F_ARTIFACT_SHA256
        assert profile.scientific_fingerprint == C64F_SCIENTIFIC_FINGERPRINT
        assert profile.representation_id == C64F_REPRESENTATION_ID
        assert profile.evidence_class == C64F_EVIDENCE_CLASS
        assert len(profile.prefill_seconds_by_input_token) == 4096
        assert len(profile.transfer_seconds_by_predictor_token) == 4096
        assert all(math.isfinite(value) and value >= 0 for value in profile.prefill_seconds_by_input_token)
        assert all(math.isfinite(value) and value >= 0 for value in profile.transfer_seconds_by_predictor_token)


@pytest.mark.parametrize("hardware_id", ["a100-80gb", "h100-80gb"])
def test_prefill_and_transfer_lookup_preserve_exact_finite_domains(profiles, hardware_id: str) -> None:
    profile = profiles[hardware_id]

    assert profile.prefill_seconds(0) == 0.0
    assert profile.prefill_seconds(1) == profile.prefill_seconds_by_input_token[0]
    assert profile.prefill_seconds(4096) == profile.prefill_seconds_by_input_token[-1]
    with pytest.raises(ValueError, match="outside C6.4f accepted domain"):
        profile.prefill_seconds(4097)
    with pytest.raises(TypeError, match="integer"):
        profile.prefill_seconds(True)

    assert profile.transfer_seconds(0) == 0.0
    assert profile.transfer_seconds(C64G_TRANSFER_BYTES_PER_TOKEN) == (
        profile.transfer_seconds_by_predictor_token[0]
    )
    assert profile.transfer_seconds(4096 * C64G_TRANSFER_BYTES_PER_TOKEN) == (
        profile.transfer_seconds_by_predictor_token[-1]
    )
    with pytest.raises(ValueError, match="8192-byte"):
        profile.transfer_seconds(1)
    with pytest.raises(ValueError, match="accepted lookup domain"):
        profile.transfer_seconds(4097 * C64G_TRANSFER_BYTES_PER_TOKEN)
    with pytest.raises(ValueError, match="exact integer"):
        profile.transfer_seconds(8192.5)


@pytest.mark.parametrize("hardware_id", ["a100-80gb", "h100-80gb"])
def test_runtime_estimate_preserves_v4_decode_recompute_state_and_transfer(
    profiles, hardware_id: str
) -> None:
    profile = profiles[hardware_id]
    workload = InferenceCostWorkload(
        input_tokens=32,
        output_tokens=4,
        reusable_prefix_tokens=20,
        state_tokens=1,
    )

    estimate = estimate_validated_runtime_cost(profile, workload)
    expected_steps = 4 * 32 + 4 * 3 // 2
    expected_decode = (
        4 * profile.decode_fixed_seconds_per_output_token
        + profile.decode_seconds_per_context_token_step * expected_steps
    )
    expected_state = profile.state_fixed_bytes + profile.state_bytes_per_token

    assert estimate.prefill_seconds == profile.prefill_seconds(32)
    assert estimate.decode_context_token_steps == expected_steps
    assert estimate.decode_seconds == expected_decode
    assert estimate.recompute_tokens == 12
    assert estimate.recompute_seconds == profile.prefill_seconds(12)
    assert estimate.state_bytes == expected_state
    assert estimate.transfer_seconds == profile.transfer_seconds(expected_state)
    assert estimate.memory_capacity_fraction == expected_state / profile.memory_capacity_bytes
    assert estimate.compute_seconds == (
        estimate.prefill_seconds + estimate.decode_seconds + estimate.recompute_seconds
    )


@pytest.mark.parametrize("hardware_id", ["a100-80gb", "h100-80gb"])
def test_zero_workload_is_exact_zero(profiles, hardware_id: str) -> None:
    estimate = estimate_validated_runtime_cost(
        profiles[hardware_id],
        InferenceCostWorkload(
            input_tokens=0,
            output_tokens=0,
            reusable_prefix_tokens=0,
            state_tokens=0,
        ),
    )
    assert estimate.prefill_seconds == 0.0
    assert estimate.decode_seconds == 0.0
    assert estimate.recompute_seconds == 0.0
    assert estimate.transfer_seconds == 0.0
    assert estimate.state_bytes == 0.0
    assert estimate.memory_capacity_fraction == 0.0
    assert estimate.compute_seconds == 0.0


def test_transfer_domain_failure_propagates_from_state_size(profiles) -> None:
    # Frozen Llama-2-7B KV state is 64 predictor tokens per state token.
    # 65 state tokens therefore require predictor key 4160, outside 1..4096.
    with pytest.raises(ValueError, match="accepted lookup domain"):
        estimate_validated_runtime_cost(
            profiles["a100-80gb"],
            InferenceCostWorkload(
                input_tokens=1,
                output_tokens=0,
                reusable_prefix_tokens=1,
                state_tokens=65,
            ),
        )


def test_artifact_tampering_fails_before_runtime_projection(tmp_path: Path) -> None:
    raw = ARTIFACT.read_bytes()
    tampered = raw.replace(
        C64F_SCIENTIFIC_FINGERPRINT.encode("ascii"),
        ("0" * 64).encode("ascii"),
        1,
    )
    assert tampered != raw
    path = tmp_path / "tampered.json"
    path.write_bytes(tampered)
    with pytest.raises(ValueError, match="artifact SHA-256"):
        load_c64f_runtime_profiles(path)


def test_resource_adapter_schedules_compute_only_and_returns_full_estimate(profiles) -> None:
    simulator = DiscreteEventSimulator(seed=7)
    resources = ResourceModel(simulator)
    resources.add_worker("worker-a")
    workload = InferenceCostWorkload(
        input_tokens=1,
        output_tokens=1,
        reusable_prefix_tokens=1,
        state_tokens=0,
    )

    scheduled = enqueue_validated_inference_task(
        resources,
        profiles["a100-80gb"],
        workload,
        worker_id="worker-a",
        task_id="request-1",
    )

    assert scheduled.task.duration == scheduled.estimate.compute_seconds
    assert scheduled.estimate.transfer_seconds == 0.0
    simulator.run()
    completed = resources.tasks["request-1"]
    assert completed.status is TaskStatus.COMPLETED
    assert completed.completed_at == scheduled.estimate.compute_seconds


def test_runtime_package_keeps_zero_mandatory_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == []
