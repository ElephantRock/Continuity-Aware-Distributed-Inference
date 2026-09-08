from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .calibration_validation import (
    VIDUR_LLAMA2_7B_TP1_DOMAIN,
    VIDUR_PINNED_COMMIT,
    VIDUR_PINNED_REPOSITORY,
)
from .inference_cost_v2 import (
    C64A_FRESH_PREFILL_AXES,
    C64A_FRESH_TRANSFER_AXES,
)


C64B_PROTOCOL_SCHEMA = "cadi.c6.4b.vidur-source-model-protocol.v1"
C64B_PROTOCOL_ID = "cadi.c6.4b.vidur-linear-regression-source-model.v1"
C64B_REFERENCE_EVIDENCE = "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"
C64B_PREDICTOR_CLASS = (
    "vidur.execution_time_predictor.linear_regression_execution_time_predictor."
    "LinearRegressionExecutionTimePredictor"
)
C64B_REPLICA_SCHEDULER = "SarathiSchedulerConfig"
C64B_PREFILL_RESULT_FIELD = "ExecutionTime.model_time"
C64B_TRANSFER_RESULT_FIELD = "_predictions['send_recv'][(num_tokens,)]"
C64B_TRANSFER_BYTES_PER_TOKEN = 2 * 4096

# C6.4b deliberately chooses Vidur's source-provided linear-regression predictor
# before evaluating any frozen C6.4a reference timing. The alternative upstream
# RandomForestRegressor is instantiated without a random_state at the pinned
# source revision, so it cannot satisfy the repeated exact-source determinism
# requirement without modifying source-model behavior.
C64B_PREDICTOR_CONFIG: dict[str, Any] = {
    "k_fold_cv_splits": 10,
    "no_cache": True,
    "kv_cache_prediction_granularity": 64,
    "prediction_max_prefill_chunk_size": 4096,
    "prediction_max_batch_size": 128,
    "prediction_max_tokens_per_request": 4096,
    "attention_decode_batching_overhead_fraction": 0.1,
    "attention_prefill_batching_overhead_fraction": 0.1,
    "nccl_cpu_launch_overhead_ms": 0.02,
    "nccl_cpu_skew_overhead_per_device_ms": 0.0,
    "num_training_job_threads": 1,
    "skip_cpu_overhead_modeling": True,
    "polynomial_degree": [1, 2, 3, 4, 5],
    "polynomial_include_bias": [True, False],
    "polynomial_interaction_only": [True, False],
    "fit_intercept": [True, False],
}

C64B_SARATHI_CONFIG: dict[str, Any] = {
    "batch_size_cap": 128,
    "block_size": 16,
    "watermark_blocks_fraction": 0.01,
    "num_blocks": None,
    "chunk_size": 512,
}

# Runtime package versions are frozen because the pinned Vidur requirements file
# leaves these dependencies unconstrained. Single-threaded numerical execution
# is part of the protocol so repeated source-model fits do not depend on worker
# scheduling or BLAS thread interleavings.
C64B_RUNTIME: dict[str, Any] = {
    "python": "3.12",
    "numpy": "1.26.4",
    "pandas": "2.2.3",
    "scikit-learn": "1.5.2",
    "scipy": "1.14.1",
    "joblib": "1.4.2",
    "threadpoolctl": "3.5.0",
    "fasteners": "0.19",
    "environment": {
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    },
}

# Git-blob fences for upstream implementation files whose behavior participates
# in configuration resolution, training, request/batch semantics, lookup, or
# model-time composition. Raw profiling artifacts remain independently fenced by
# the C6.2/C6.3 source manifests and SHA-256 snapshots.
C64B_UPSTREAM_CODE_BLOBS: dict[str, str] = {
    "vidur/config/config.py": "9452180c40bebc0d9cf4de29d5b19ef41557ec44",
    "vidur/config/model_config.py": "722299bbb556ccbab2b82609598be6b8c2963c29",
    "vidur/config/device_sku_config.py": "8ac9bf57ac03070cd42cdf48792ccf8ffd73ca04",
    "vidur/config/node_sku_config.py": "ce2271f55f772ab346b6083b2baee3f3ea91e9e7",
    "vidur/execution_time_predictor/base_execution_time_predictor.py": (
        "f399c8ea7fcf66282a473477c67f2dd328d4cb03"
    ),
    "vidur/execution_time_predictor/sklearn_execution_time_predictor.py": (
        "a5a96466eb86d94503711afec6d45218bd38d93e"
    ),
    "vidur/execution_time_predictor/linear_regression_execution_time_predictor.py": (
        "8dd32b76bcd4f190bc820dd0274f1a71c58e997a"
    ),
    "vidur/entities/request.py": "8f2d684b76347ecd8e48a3a955d1228ad7ac9c65",
    "vidur/entities/batch.py": "7cda25ac339788e3e27de888a89e645876f71ffa",
    "vidur/entities/execution_time.py": "a5100f86b7e4d885ff62a5a66f845682a998add8",
}

C64B_HARDWARE_CONFIG: dict[str, dict[str, Any]] = {
    "a100-80gb": {
        "vidur_device": "a100",
        "network_device": "a100_dgx",
    },
    "h100-80gb": {
        "vidur_device": "h100",
        "network_device": "h100_dgx",
    },
}


@dataclass(frozen=True, slots=True)
class TransferLookupDomain:
    axis_bytes: int
    num_tokens: int | None
    evaluable: bool
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_bytes": self.axis_bytes,
            "num_tokens": self.num_tokens,
            "evaluable": self.evaluable,
            "reason": self.reason,
        }


def transfer_lookup_domain(axis_bytes: int) -> TransferLookupDomain:
    """Resolve one byte axis against Vidur's frozen send/recv lookup domain.

    Vidur derives the send/recv predictor feature as ``size / embedding_dim / 2``
    and, under the frozen Sarathi configuration, materializes lookup keys only for
    integer ``num_tokens`` in [1, prediction_max_tokens_per_request]. This helper
    performs no timing evaluation.
    """

    if not isinstance(axis_bytes, int) or isinstance(axis_bytes, bool) or axis_bytes <= 0:
        raise ValueError("axis_bytes must be a positive integer")
    if axis_bytes % C64B_TRANSFER_BYTES_PER_TOKEN:
        return TransferLookupDomain(
            axis_bytes=axis_bytes,
            num_tokens=None,
            evaluable=False,
            reason="axis is not an exact Vidur send/recv token multiple",
        )
    num_tokens = axis_bytes // C64B_TRANSFER_BYTES_PER_TOKEN
    maximum = int(C64B_PREDICTOR_CONFIG["prediction_max_tokens_per_request"])
    if num_tokens < 1 or num_tokens > maximum:
        return TransferLookupDomain(
            axis_bytes=axis_bytes,
            num_tokens=num_tokens,
            evaluable=False,
            reason=f"num_tokens is outside frozen source lookup domain [1,{maximum}]",
        )
    return TransferLookupDomain(
        axis_bytes=axis_bytes,
        num_tokens=num_tokens,
        evaluable=True,
        reason=None,
    )


def c64b_protocol_manifest() -> dict[str, Any]:
    transfer_domain = [
        transfer_lookup_domain(axis).to_dict() for axis in C64A_FRESH_TRANSFER_AXES
    ]
    return {
        "schema": C64B_PROTOCOL_SCHEMA,
        "protocol_id": C64B_PROTOCOL_ID,
        "reference_evidence": C64B_REFERENCE_EVIDENCE,
        "source_repository": VIDUR_PINNED_REPOSITORY,
        "source_commit": VIDUR_PINNED_COMMIT,
        "model_id": VIDUR_LLAMA2_7B_TP1_DOMAIN.model_id,
        "predictor_class": C64B_PREDICTOR_CLASS,
        "predictor_config": C64B_PREDICTOR_CONFIG,
        "replica_scheduler": C64B_REPLICA_SCHEDULER,
        "replica_scheduler_config": C64B_SARATHI_CONFIG,
        "runtime": C64B_RUNTIME,
        "upstream_code_blobs": dict(sorted(C64B_UPSTREAM_CODE_BLOBS.items())),
        "hardware": C64B_HARDWARE_CONFIG,
        "prefill": {
            "pipeline_stages": 1,
            "tensor_parallel_size": 1,
            "axes_input_tokens": list(C64A_FRESH_PREFILL_AXES),
            "result_field": C64B_PREFILL_RESULT_FIELD,
            "batch_semantics": (
                "one unfinished request; num_tokens=[axis]; Vidur Batch rounds only "
                "compute-component lookup to multiples of 8 while attention/KV-save "
                "use the source predictor's exact request semantics"
            ),
        },
        "transfer": {
            "pipeline_stages_for_model_training": 2,
            "tensor_parallel_size": 1,
            "bytes_per_predictor_token": C64B_TRANSFER_BYTES_PER_TOKEN,
            "axes_bytes": list(C64A_FRESH_TRANSFER_AXES),
            "result_field": C64B_TRANSFER_RESULT_FIELD,
            "lookup_semantics": (
                "read the exact integer num_tokens key materialized by Vidur for the "
                "send_recv source model; do not round, extrapolate, or call the raw "
                "estimator outside the materialized lookup domain"
            ),
            "domain": transfer_domain,
        },
        "contains_reference_timings": False,
    }


def c64b_protocol_json() -> str:
    return json.dumps(
        c64b_protocol_manifest(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def c64b_protocol_fingerprint() -> str:
    return hashlib.sha256(c64b_protocol_json().encode("utf-8")).hexdigest()
