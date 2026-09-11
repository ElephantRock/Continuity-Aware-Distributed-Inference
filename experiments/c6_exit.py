from __future__ import annotations

import hashlib
import json
from pathlib import Path

from simulator import InferenceCostWorkload
from simulator.inference_cost_runtime import (
    C64F_ARTIFACT_SHA256,
    C64F_EVIDENCE_CLASS,
    C64F_REPRESENTATION_ID,
    C64F_SCIENTIFIC_FINGERPRINT,
    load_c64f_runtime_profiles,
    estimate_validated_runtime_cost,
)


C6_EXIT_SCHEMA = "cadi.c6.5.exit-reproducibility.v1"
SUPPORTED_HARDWARE = ("a100-80gb", "h100-80gb")
ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "c6.4f" / "exhaustive-source-equivalence.json"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _estimate_vector(profile, workload: InferenceCostWorkload) -> dict[str, object]:
    estimate = estimate_validated_runtime_cost(profile, workload)
    return estimate.to_dict()


def build_exit_summary() -> dict[str, object]:
    raw = ARTIFACT.read_bytes()
    artifact_sha256 = hashlib.sha256(raw).hexdigest()
    if artifact_sha256 != C64F_ARTIFACT_SHA256:
        raise RuntimeError("canonical C6.4f artifact SHA-256 drift")

    profiles = load_c64f_runtime_profiles(ARTIFACT)
    if tuple(sorted(profiles)) != SUPPORTED_HARDWARE:
        raise RuntimeError("unexpected C6.4f runtime hardware family")

    workloads = {
        "zero": InferenceCostWorkload(
            input_tokens=0,
            output_tokens=0,
            reusable_prefix_tokens=0,
            state_tokens=0,
        ),
        "interior": InferenceCostWorkload(
            input_tokens=32,
            output_tokens=4,
            reusable_prefix_tokens=20,
            state_tokens=1,
        ),
        "upper_transfer_boundary": InferenceCostWorkload(
            input_tokens=4095,
            output_tokens=1,
            reusable_prefix_tokens=4095,
            state_tokens=64,
        ),
    }

    runtime: dict[str, object] = {}
    for hardware_id in SUPPORTED_HARDWARE:
        profile = profiles[hardware_id]
        if len(profile.prefill_seconds_by_input_token) != 4096:
            raise RuntimeError("prefill table cardinality drift")
        if len(profile.transfer_seconds_by_predictor_token) != 4096:
            raise RuntimeError("transfer table cardinality drift")
        runtime[hardware_id] = {
            name: _estimate_vector(profile, workload)
            for name, workload in workloads.items()
        }

        zero = runtime[hardware_id]["zero"]
        for field in (
            "prefill_seconds",
            "decode_seconds",
            "recompute_seconds",
            "transfer_seconds",
            "state_bytes",
            "memory_capacity_fraction",
        ):
            if zero[field] != 0.0:
                raise RuntimeError(f"zero workload drift for {hardware_id}/{field}")

    rejected: list[str] = []
    rejection_cases = {
        "decode_zero_context": InferenceCostWorkload(0, 1, 0, 0),
        "decode_over_max_model_length": InferenceCostWorkload(4096, 1, 4096, 0),
        "transfer_over_accepted_domain": InferenceCostWorkload(1, 0, 1, 65),
    }
    for case_name, workload in rejection_cases.items():
        try:
            estimate_validated_runtime_cost(profiles["a100-80gb"], workload)
        except ValueError:
            rejected.append(case_name)
        else:
            raise RuntimeError(f"expected fail-closed runtime rejection: {case_name}")

    return {
        "schema": C6_EXIT_SCHEMA,
        "artifact_sha256": artifact_sha256,
        "scientific_fingerprint": C64F_SCIENTIFIC_FINGERPRINT,
        "representation_id": C64F_REPRESENTATION_ID,
        "evidence_class": C64F_EVIDENCE_CLASS,
        "hardware": list(SUPPORTED_HARDWARE),
        "prefill_points_per_hardware": 4096,
        "transfer_points_per_hardware": 4096,
        "runtime": runtime,
        "rejected_out_of_domain_cases": rejected,
    }


def main() -> None:
    first = build_exit_summary()
    second = build_exit_summary()
    first_json = _canonical_json(first)
    second_json = _canonical_json(second)
    if first_json != second_json:
        raise RuntimeError("C6.5 runtime replay is not byte-identical")
    payload = dict(first)
    payload["replay_identical"] = True
    print(_canonical_json(payload))


if __name__ == "__main__":
    main()
