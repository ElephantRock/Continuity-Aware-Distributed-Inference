from __future__ import annotations

import json

from simulator.c64b_protocol import (
    C64B_PREDICTOR_CLASS,
    C64B_PROTOCOL_ID,
    C64B_REFERENCE_EVIDENCE,
    C64B_RUNTIME,
    C64B_UPSTREAM_CODE_BLOBS,
    c64b_protocol_fingerprint,
    c64b_protocol_json,
    c64b_protocol_manifest,
    transfer_lookup_domain,
)
from simulator.inference_cost_v2 import (
    C64A_FRESH_PREFILL_AXES,
    C64A_FRESH_TRANSFER_AXES,
)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        result = set(value)
        for item in value.values():
            result.update(_all_keys(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_all_keys(item))
        return result
    return set()


def test_protocol_is_timing_free_and_binds_frozen_axes() -> None:
    manifest = c64b_protocol_manifest()
    assert manifest["protocol_id"] == C64B_PROTOCOL_ID
    assert manifest["reference_evidence"] == C64B_REFERENCE_EVIDENCE
    assert manifest["contains_reference_timings"] is False
    assert manifest["prefill"]["axes_input_tokens"] == list(C64A_FRESH_PREFILL_AXES)
    assert manifest["transfer"]["axes_bytes"] == list(C64A_FRESH_TRANSFER_AXES)

    keys = _all_keys(manifest)
    assert "observed_seconds" not in keys
    assert "predicted_seconds" not in keys
    assert "reference_seconds" not in keys


def test_protocol_selects_deterministic_upstream_linear_regression() -> None:
    assert C64B_PREDICTOR_CLASS.endswith("LinearRegressionExecutionTimePredictor")
    manifest = c64b_protocol_manifest()
    config = manifest["predictor_config"]
    assert config["num_training_job_threads"] == 1
    assert config["no_cache"] is True
    assert config["k_fold_cv_splits"] == 10
    assert config["polynomial_degree"] == [1, 2, 3, 4, 5]
    assert config["polynomial_include_bias"] == [True, False]
    assert config["polynomial_interaction_only"] == [True, False]
    assert config["fit_intercept"] == [True, False]
    assert C64B_RUNTIME["environment"]["PYTHONHASHSEED"] == "0"
    assert C64B_RUNTIME["environment"]["OMP_NUM_THREADS"] == "1"


def test_upstream_behavioral_files_are_sha_fenced() -> None:
    expected_paths = {
        "vidur/config/config.py",
        "vidur/execution_time_predictor/base_execution_time_predictor.py",
        "vidur/execution_time_predictor/sklearn_execution_time_predictor.py",
        "vidur/execution_time_predictor/linear_regression_execution_time_predictor.py",
        "vidur/entities/batch.py",
        "vidur/entities/execution_time.py",
    }
    assert set(C64B_UPSTREAM_CODE_BLOBS) == expected_paths
    assert all(len(sha) == 40 for sha in C64B_UPSTREAM_CODE_BLOBS.values())


def test_transfer_lookup_domain_is_frozen_before_timing_evaluation() -> None:
    domains = [transfer_lookup_domain(axis) for axis in C64A_FRESH_TRANSFER_AXES]
    assert [item.num_tokens for item in domains] == [1, 4, 16, 64, 257, 1025, 4097, 7169]
    assert [item.evaluable for item in domains] == [
        True,
        True,
        True,
        True,
        True,
        True,
        False,
        False,
    ]
    assert "outside frozen source lookup domain" in domains[-1].reason


def test_transfer_lookup_domain_rejects_non_token_multiple() -> None:
    result = transfer_lookup_domain(8193)
    assert result.evaluable is False
    assert result.num_tokens is None
    assert "not an exact" in result.reason


def test_protocol_serialization_and_fingerprint_are_canonical() -> None:
    encoded = c64b_protocol_json()
    assert encoded == json.dumps(
        json.loads(encoded), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    fingerprint = c64b_protocol_fingerprint()
    assert len(fingerprint) == 64
    assert fingerprint == c64b_protocol_fingerprint()
