from __future__ import annotations

import json

from simulator.c64b_protocol import (
    C64B_PREDICTOR_CLASS,
    C64B_PROTOCOL_ID,
    C64B_PROTOCOL_SCHEMA,
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
    assert manifest["schema"] == C64B_PROTOCOL_SCHEMA
    assert manifest["protocol_id"] == C64B_PROTOCOL_ID
    assert C64B_PROTOCOL_SCHEMA.endswith(".v2")
    assert C64B_PROTOCOL_ID.endswith(".v2")
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


def test_protocol_manifest_returns_isolated_mutable_configuration() -> None:
    fingerprint = c64b_protocol_fingerprint()
    first = c64b_protocol_manifest()
    first["predictor_config"]["prediction_max_tokens_per_request"] = 1
    first["runtime"]["environment"]["OMP_NUM_THREADS"] = "99"
    first["runtime"]["openblas"]["coretype"] = "mutated"
    first["hardware"]["a100-80gb"]["vidur_device"] = "mutated"

    second = c64b_protocol_manifest()
    assert second["predictor_config"]["prediction_max_tokens_per_request"] == 4096
    assert second["runtime"]["environment"]["OMP_NUM_THREADS"] == "1"
    assert second["runtime"]["openblas"]["coretype"] == "Haswell"
    assert second["hardware"]["a100-80gb"]["vidur_device"] == "a100"
    assert c64b_protocol_fingerprint() == fingerprint


def test_cold_prefill_request_state_is_fully_frozen() -> None:
    state = c64b_protocol_manifest()["prefill"]
    assert state["request_state"] == {
        "arrived_at": 0.0,
        "num_prefill_tokens": "axis",
        "num_decode_tokens": 1,
        "num_processed_tokens": 0,
        "is_prefill_complete_before_evaluation": False,
    }
    assert state["batch_state"] == {
        "replica_id": 0,
        "requests": 1,
        "num_tokens": "[axis]",
    }


def test_numerical_execution_substrate_freezes_blas_not_host_image_version() -> None:
    assert C64B_RUNTIME["python"] == "3.12.14"
    assert C64B_RUNTIME["python_implementation"] == "CPython"
    assert C64B_RUNTIME["os"] == "ubuntu-24.04"
    assert C64B_RUNTIME["architecture"] == "x86_64"
    assert C64B_RUNTIME["github_actions_image_os"] == "ubuntu24"
    assert (
        C64B_RUNTIME["github_actions_image_version_policy"]
        == "record_only_not_equivalence"
    )
    assert "github_actions_image_version" not in C64B_RUNTIME
    assert C64B_RUNTIME["numpy"] == "1.26.4"
    assert C64B_RUNTIME["scikit-learn"] == "1.5.2"
    assert C64B_RUNTIME["scipy"] == "1.14.1"
    assert C64B_RUNTIME["openblas"] == {
        "version": "0.3.23.dev",
        "coretype": "Haswell",
        "threading_layer": "pthreads",
        "num_threads": 1,
    }
    assert C64B_RUNTIME["environment"]["OPENBLAS_CORETYPE"] == "Haswell"
    assert C64B_RUNTIME["environment"]["OPENBLAS_NUM_THREADS"] == "1"


def test_upstream_behavioral_files_are_sha_fenced() -> None:
    expected_paths = {
        "vidur/config/config.py",
        "vidur/config/model_config.py",
        "vidur/config/device_sku_config.py",
        "vidur/config/node_sku_config.py",
        "vidur/execution_time_predictor/base_execution_time_predictor.py",
        "vidur/execution_time_predictor/sklearn_execution_time_predictor.py",
        "vidur/execution_time_predictor/linear_regression_execution_time_predictor.py",
        "vidur/entities/request.py",
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
