from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Sequence

from experiments.c64b_fresh_adequacy import (
    _predictor,
    _reference_partitions,
    _source_paths,
    verify_upstream_snapshot,
)
from simulator.c64b_protocol import (
    C64B_HARDWARE_CONFIG,
    C64B_PROTOCOL_ID,
    C64B_REFERENCE_EVIDENCE,
    c64b_protocol_fingerprint,
    transfer_lookup_domain,
)
from simulator.c64d_export import (
    C63_CARRIED_DECODE,
    C64D_STATE_SOURCE_BLOBS,
    compile_source_polynomial_profile,
    export_canonical_polynomial,
    source_equivalence_report,
)
from simulator.calibration_adequacy import evaluate_family_adequacy
from simulator.calibration_projection import VIDUR_SOURCE_SHA256
from simulator.calibration_validation import (
    C6_THRESHOLD_ULP_BUDGET,
    C6_VALIDATION_MAPE_LIMIT,
    C6_VALIDATION_MAX_APE_LIMIT,
    AdequacyDecision,
    ReferenceKind,
    VIDUR_PINNED_COMMIT,
)
from simulator.inference_cost_v3 import (
    C64C_FRESH_PREFILL_AXES,
    C64C_FRESH_TRANSFER_AXES,
    C64C_REPRESENTATION_ID,
    SourcePolynomialCostProfile,
)


C64D_RESULT_SCHEMA = "cadi.c6.4d.source-polynomial-adequacy-result.v1"
C64D_COMPILE_SCHEMA = "cadi.c6.4d.source-polynomial-compile-result.v1"
C64D_EQUIVALENCE_FAILED = "NOT_EVALUATED_EQUIVALENCE_FENCE_FAILED"
C64D_BOUNDARY_INVALIDATED = "NOT_EVALUATED_BOUNDARY_INVALIDATED"
C64D_EVALUATED = "EVALUATED"

_PREFILL_COMPONENTS = (
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
_COMPUTE_COMPONENTS = frozenset(
    {
        "add",
        "attn_post_proj",
        "attn_pre_proj",
        "attn_rope",
        "input_layernorm",
        "mlp_act",
        "mlp_down_proj",
        "mlp_up_proj",
        "post_attention_layernorm",
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _within_limit(value: float, limit: float) -> bool:
    return value <= limit or value - limit <= C6_THRESHOLD_ULP_BUDGET * math.ulp(limit)


def _verify_state_source_blobs(vidur_root: Path) -> dict[str, str]:
    observed = {
        path: _git(vidur_root, "rev-parse", f"HEAD:{path}")
        for path in sorted(C64D_STATE_SOURCE_BLOBS)
    }
    expected = dict(sorted(C64D_STATE_SOURCE_BLOBS.items()))
    if observed != expected:
        raise ValueError(
            f"C6.4d state/memory source blob drift: expected={expected}, observed={observed}"
        )
    return observed


def _source_reference(hardware_id: str, component_id: str) -> str:
    return (
        f"microsoft/vidur@{VIDUR_PINNED_COMMIT}; hardware={hardware_id}; "
        f"source-trained component={component_id}; protocol={C64B_PROTOCOL_ID}; "
        f"protocol_fingerprint={c64b_protocol_fingerprint()}"
    )


def _training_only_predictor(
    *,
    vidur_root: Path,
    hardware_id: str,
    pipeline_stages: int,
    work_dir: Path,
):
    """Run the exact pinned training path without materializing source predictions."""
    from vidur.execution_time_predictor.linear_regression_execution_time_predictor import (
        LinearRegressionExecutionTimePredictor,
    )

    cls = LinearRegressionExecutionTimePredictor
    had_local_override = "_predict_from_models" in cls.__dict__
    original = getattr(cls, "_predict_from_models")
    setattr(cls, "_predict_from_models", lambda self: {})
    try:
        predictor = _predictor(
            vidur_root=vidur_root,
            hardware_id=hardware_id,
            pipeline_stages=pipeline_stages,
            work_dir=work_dir,
        )
    finally:
        if had_local_override:
            setattr(cls, "_predict_from_models", original)
        else:
            delattr(cls, "_predict_from_models")
    if predictor._predictions != {}:
        raise AssertionError("training-only predictor materialized source predictions")
    return predictor


def _export_component(
    predictor: Any,
    *,
    hardware_id: str,
    component_id: str,
    features: Sequence[str],
):
    if component_id not in predictor._models:
        raise ValueError(f"required source model is missing: {component_id}")
    return export_canonical_polynomial(
        predictor._models[component_id],
        component_id=component_id,
        hardware_id=hardware_id,
        expected_feature_names=tuple(features),
        source_reference=_source_reference(hardware_id, component_id),
    )


def _materialization_frames_without_fresh_boundary(
    prefill_predictor: Any, transfer_predictor: Any
) -> dict[str, tuple[Any, tuple[str, ...]]]:
    import numpy as np
    import pandas as pd

    prefill_max = int(prefill_predictor._max_tokens)
    transfer_max = int(transfer_predictor._max_tokens)
    exact_fresh = set(C64C_FRESH_PREFILL_AXES)
    rounded_fresh = {((axis + 7) // 8) * 8 for axis in C64C_FRESH_PREFILL_AXES}
    fresh_transfer_tokens = set()
    for axis in C64C_FRESH_TRANSFER_AXES:
        domain = transfer_lookup_domain(axis)
        if not domain.evaluable or domain.num_tokens is None:
            raise ValueError(f"frozen C6.4c transfer boundary is outside source domain: {axis}")
        fresh_transfer_tokens.add(domain.num_tokens)

    compute = pd.DataFrame({"num_tokens": np.arange(1, prefill_max + 1, dtype=np.int64)})
    compute_nonfresh = compute[~compute["num_tokens"].isin(rounded_fresh)].copy()
    kv_save_nonfresh = compute[~compute["num_tokens"].isin(exact_fresh)].copy()
    send_recv = pd.DataFrame({"num_tokens": np.arange(1, transfer_max + 1, dtype=np.int64)})
    send_recv_nonfresh = send_recv[~send_recv["num_tokens"].isin(fresh_transfer_tokens)].copy()

    granularity = int(prefill_predictor._config.kv_cache_prediction_granularity)
    max_request = int(prefill_predictor._config.prediction_max_tokens_per_request)
    max_chunk = int(prefill_predictor._config.prediction_max_prefill_chunk_size)
    kv_values = np.arange(0, max_request + 1, granularity, dtype=np.int64)
    chunks = np.arange(1, max_chunk + 1, dtype=np.int64)
    attention = pd.DataFrame(
        {
            "kv_cache_size": np.repeat(kv_values, len(chunks)),
            "prefill_chunk_size_squared": np.tile(chunks * chunks, len(kv_values)),
        }
    )
    fresh_squares = {axis * axis for axis in exact_fresh}
    fresh_rows = (attention["kv_cache_size"] == 0) & attention["prefill_chunk_size_squared"].isin(fresh_squares)
    attention_nonfresh = attention[~fresh_rows].copy()

    result: dict[str, tuple[Any, tuple[str, ...]]] = {
        component_id: (compute_nonfresh, ("num_tokens",))
        for component_id in _COMPUTE_COMPONENTS
    }
    result["attn_kv_cache_save"] = (kv_save_nonfresh, ("num_tokens",))
    result["attn_prefill"] = (
        attention_nonfresh,
        ("kv_cache_size", "prefill_chunk_size_squared"),
    )
    result["send_recv"] = (send_recv_nonfresh, ("num_tokens",))
    return result


def _equivalence(
    *,
    profile: SourcePolynomialCostProfile,
    prefill_predictor: Any,
    transfer_predictor: Any,
) -> dict[str, Any]:
    frames = _materialization_frames_without_fresh_boundary(prefill_predictor, transfer_predictor)
    canonical = {
        model.component_id: model for model in profile.prefill_components
    }
    canonical[profile.prefill_attention.component_id] = profile.prefill_attention
    canonical[profile.transfer.component_id] = profile.transfer
    source_models = dict(prefill_predictor._models)
    source_models["send_recv"] = transfer_predictor._models["send_recv"]

    reports: list[dict[str, Any]] = []
    for component_id in sorted(canonical):
        frame, features = frames[component_id]
        X = frame[list(features)]
        source_predictions = source_models[component_id].predict(X)
        rows = X.itertuples(index=False, name=None)
        reports.append(
            source_equivalence_report(
                canonical[component_id],
                feature_rows=rows,
                source_predictions=source_predictions,
            )
        )
    passed = all(item["decision"] == "PASS" for item in reports)
    return {
        "decision": "PASS" if passed else "FAIL",
        "equivalence_domain": "source_materialization_domain_excluding_c64c_boundary_component_identities",
        "fresh_boundary_component_predictions_consumed_current_execution": False,
        "fresh_boundary_values_consumed": False,
        "components": reports,
    }


def _validate_carried_decode(vidur_root: Path, hardware_id: str) -> dict[str, Any]:
    partitions = _reference_partitions(vidur_root, hardware_id)
    record = evaluate_family_adequacy(partitions[ReferenceKind.SINGLE_TOKEN_DECODE])
    expected_fixed, expected_slope = C63_CARRIED_DECODE[hardware_id]
    fit = record.fit
    if fit is None:
        raise ValueError("carried decode adequacy record has no fitted primitive")
    if fit.intercept_seconds != expected_fixed or fit.slope_seconds_per_axis_unit != expected_slope:
        raise ValueError(
            "carried C6.3 decode coefficients drift from the frozen v3 profile inputs"
        )
    if record.decision is not AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN:
        raise ValueError("carried C6.3 decode family is no longer adequate")
    return record.to_dict()


def _compile_hardware(
    *,
    vidur_root: Path,
    hardware_id: str,
    work_root: Path,
) -> tuple[SourcePolynomialCostProfile, dict[str, Any]]:
    prefill_predictor = _training_only_predictor(
        vidur_root=vidur_root,
        hardware_id=hardware_id,
        pipeline_stages=1,
        work_dir=work_root / f"{hardware_id}-compile-prefill",
    )
    transfer_predictor = _training_only_predictor(
        vidur_root=vidur_root,
        hardware_id=hardware_id,
        pipeline_stages=2,
        work_dir=work_root / f"{hardware_id}-compile-transfer",
    )

    if prefill_predictor._predictions or transfer_predictor._predictions:
        raise AssertionError("compile path must not materialize source prediction tables")

    components = tuple(
        _export_component(
            prefill_predictor,
            hardware_id=hardware_id,
            component_id=component_id,
            features=("num_tokens",),
        )
        for component_id in _PREFILL_COMPONENTS
    )
    attention = _export_component(
        prefill_predictor,
        hardware_id=hardware_id,
        component_id="attn_prefill",
        features=("kv_cache_size", "prefill_chunk_size_squared"),
    )
    transfer = _export_component(
        transfer_predictor,
        hardware_id=hardware_id,
        component_id="send_recv",
        features=("num_tokens",),
    )
    profile = compile_source_polynomial_profile(
        hardware_id=hardware_id,
        prefill_components=components,
        prefill_attention=attention,
        transfer=transfer,
    )
    equivalence = _equivalence(
        profile=profile,
        prefill_predictor=prefill_predictor,
        transfer_predictor=transfer_predictor,
    )
    return profile, equivalence


def compile_once(vidur_root: Path) -> dict[str, Any]:
    snapshot = verify_upstream_snapshot(vidur_root)
    state_source_blobs = _verify_state_source_blobs(vidur_root)
    hardware: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="c64d-compile-") as temp_dir:
        work_root = Path(temp_dir)
        for hardware_id in sorted(C64B_HARDWARE_CONFIG):
            profile, equivalence = _compile_hardware(
                vidur_root=vidur_root,
                hardware_id=hardware_id,
                work_root=work_root,
            )
            hardware.append(
                {
                    "hardware_id": hardware_id,
                    "profile": profile.to_dict(),
                    "profile_fingerprint": profile.fingerprint,
                    "source_equivalence": equivalence,
                    "carried_single_token_decode": _validate_carried_decode(
                        vidur_root, hardware_id
                    ),
                }
            )
    result: dict[str, Any] = {
        "schema": C64D_COMPILE_SCHEMA,
        "representation_id": C64C_REPRESENTATION_ID,
        "protocol_id": C64B_PROTOCOL_ID,
        "protocol_fingerprint": c64b_protocol_fingerprint(),
        "source_snapshot": snapshot,
        "state_memory_source_blobs": state_source_blobs,
        "contains_fresh_reference_timings": False,
        "hardware": hardware,
        "decision": (
            "PASS"
            if all(item["source_equivalence"]["decision"] == "PASS" for item in hardware)
            else "FAIL"
        ),
    }
    result["compile_fingerprint"] = _sha256_json(result)
    return result


def _family_report(
    *,
    hardware_id: str,
    kind: ReferenceKind,
    axes: Sequence[int],
    references: dict[int, float],
    predictions: dict[int, float],
) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    apes: list[float] = []
    absolute_errors: list[float] = []
    zero_reference_exact = True
    for axis in axes:
        reference = float(references[axis])
        predicted = float(predictions[axis])
        if not math.isfinite(reference) or reference < 0:
            raise ValueError(f"invalid source reference at {hardware_id}/{kind.value}/{axis}")
        if not math.isfinite(predicted) or predicted < 0:
            raise ValueError(f"invalid canonical prediction at {hardware_id}/{kind.value}/{axis}")
        signed = predicted - reference
        absolute = abs(signed)
        if reference == 0:
            ape = None
            zero_reference_exact = zero_reference_exact and absolute == 0
        else:
            ape = absolute / reference
            apes.append(ape)
        absolute_errors.append(absolute)
        points.append(
            {
                "hardware_id": hardware_id,
                "reference_kind": kind.value,
                "axis": axis,
                "source_model_reference_seconds": reference,
                "canonical_v3_predicted_seconds": predicted,
                "signed_error_seconds": signed,
                "absolute_error_seconds": absolute,
                "absolute_percentage_error": ape,
                "evidence_class": C64B_REFERENCE_EVIDENCE,
                "protocol_fingerprint": c64b_protocol_fingerprint(),
            }
        )
    mape = math.fsum(apes) / len(apes) if apes else 0.0
    max_ape = max(apes) if apes else 0.0
    mae = math.fsum(absolute_errors) / len(absolute_errors)
    adequate = (
        zero_reference_exact
        and _within_limit(mape, C6_VALIDATION_MAPE_LIMIT)
        and _within_limit(max_ape, C6_VALIDATION_MAX_APE_LIMIT)
    )
    return {
        "hardware_id": hardware_id,
        "kind": kind.value,
        "evidence_class": C64B_REFERENCE_EVIDENCE,
        "thresholds": {
            "mape_limit": C6_VALIDATION_MAPE_LIMIT,
            "max_ape_limit": C6_VALIDATION_MAX_APE_LIMIT,
            "floating_boundary_ulp_budget": C6_THRESHOLD_ULP_BUDGET,
            "zero_reference_requires_exact_zero_error": True,
        },
        "points": points,
        "mae_seconds": mae,
        "mape": mape,
        "max_ape": max_ape,
        "zero_reference_exact": zero_reference_exact,
        "decision": (
            AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
            if adequate
            else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION.value
        ),
    }


def _fresh_source_values(
    *,
    vidur_root: Path,
    hardware_id: str,
    work_root: Path,
) -> tuple[dict[int, float], dict[int, float]]:
    raise RuntimeError(
        "C6.4c second boundary was materialized by superseded C6.4d predictor construction; "
        "fresh adequacy evaluation is permanently blocked for this boundary"
    )


def evaluate_once(vidur_root: Path) -> dict[str, Any]:
    snapshot = verify_upstream_snapshot(vidur_root)
    state_source_blobs = _verify_state_source_blobs(vidur_root)
    hardware_records: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="c64d-") as temp_dir:
        work_root = Path(temp_dir)
        for hardware_id in sorted(C64B_HARDWARE_CONFIG):
            profile, equivalence = _compile_hardware(
                vidur_root=vidur_root,
                hardware_id=hardware_id,
                work_root=work_root,
            )
            decode = _validate_carried_decode(vidur_root, hardware_id)
            representation_decision = (
                AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION.value
                if equivalence["decision"] == "FAIL"
                else None
            )
            hardware_records.append(
                {
                    "hardware_id": hardware_id,
                    "source_sha256": VIDUR_SOURCE_SHA256[hardware_id],
                    "profile": profile.to_dict(),
                    "profile_fingerprint": profile.fingerprint,
                    "source_equivalence": equivalence,
                    "carried_single_token_decode": decode,
                    "multi_token_decode_composition": {
                        "evidence_class": "ANALYTICALLY_DERIVED",
                        "mechanic": "m*fixed + slope*(m*n + m*(m-1)/2)",
                        "new_physical_validation": False,
                    },
                    "fresh_evaluation_status": C64D_BOUNDARY_INVALIDATED,
                    "fresh_prefill": None,
                    "fresh_transfer": None,
                    "adequacy_decision": None,
                    "representation_decision": representation_decision,
                }
            )

    equivalence_failed = any(
        item["source_equivalence"]["decision"] == "FAIL"
        for item in hardware_records
    )
    representation_decision = (
        AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION.value
        if equivalence_failed
        else None
    )
    result: dict[str, Any] = {
        "schema": C64D_RESULT_SCHEMA,
        "result_kind": (
            "PRE_FRESH_EQUIVALENCE_FAILURE"
            if equivalence_failed
            else "BOUNDARY_INVALIDATED_REFREEZE_REQUIRED"
        ),
        "representation_id": C64C_REPRESENTATION_ID,
        "protocol_id": C64B_PROTOCOL_ID,
        "protocol_fingerprint": c64b_protocol_fingerprint(),
        "evidence_class": C64B_REFERENCE_EVIDENCE,
        "source_snapshot": snapshot,
        "state_memory_source_blobs": state_source_blobs,
        "fresh_boundary": {
            "prefill_input_tokens": list(C64C_FRESH_PREFILL_AXES),
            "transfer_bytes": list(C64C_FRESH_TRANSFER_AXES),
            "frozen_by_c64c_merge": "041bf75102628da401b771845fd0b436f261cd7e",
            "status": "INVALIDATED_BY_SUPERSEDED_PREDICTOR_MATERIALIZATION",
            "materialized_in_superseded_workflow_runs": [
                34272077193,
                34306392038,
                34307451811,
            ],
            "contains_serialized_fresh_reference_timings": False,
            "source_model_predictions_were_materialized": True,
        },
        "hardware": hardware_records,
        "adequacy_decision": None,
        "representation_decision": representation_decision,
        "decision": (
            representation_decision
            if representation_decision is not None
            else "BOUNDARY_INVALIDATED_REFREEZE_REQUIRED"
        ),
        "post_hoc_repairs": [],
    }
    result["scientific_fingerprint"] = _sha256_json(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vidur-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--mode",
        choices=("compile", "evaluate"),
        default="evaluate",
        help="compile trains/exports without materializing withheld C6.4c boundary predictions",
    )
    args = parser.parse_args()
    vidur_root = args.vidur_root.resolve()
    result = compile_once(vidur_root) if args.mode == "compile" else evaluate_once(vidur_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_canonical_json(result) + "\n", encoding="utf-8")
    print(_canonical_json(result))


if __name__ == "__main__":
    main()
