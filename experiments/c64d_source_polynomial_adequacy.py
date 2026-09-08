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


def _training_frames(prefill_predictor: Any, transfer_predictor: Any) -> dict[str, tuple[Any, tuple[str, ...]]]:
    compute = prefill_predictor._load_compute_df(prefill_predictor._compute_input_file)
    compute = prefill_predictor._get_compute_df_with_derived_features(compute)
    attention = prefill_predictor._load_attention_df(prefill_predictor._attention_input_file)
    attention = prefill_predictor._get_attention_df_with_derived_features(attention)
    prefill_attention = attention[~attention["is_decode"]]
    send_recv = transfer_predictor._load_send_recv_df(transfer_predictor._send_recv_input_file)
    send_recv = transfer_predictor._get_send_recv_df_with_derived_features(send_recv)

    result: dict[str, tuple[Any, tuple[str, ...]]] = {
        component_id: (compute, ("num_tokens",)) for component_id in _COMPUTE_COMPONENTS
    }
    result["attn_kv_cache_save"] = (attention, ("num_tokens",))
    result["attn_prefill"] = (
        prefill_attention,
        ("kv_cache_size", "prefill_chunk_size_squared"),
    )
    result["send_recv"] = (send_recv, ("num_tokens",))
    return result


def _equivalence(
    *,
    profile: SourcePolynomialCostProfile,
    prefill_predictor: Any,
    transfer_predictor: Any,
) -> dict[str, Any]:
    frames = _training_frames(prefill_predictor, transfer_predictor)
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
        "training_domain_only": True,
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
    prefill_predictor = _predictor(
        vidur_root=vidur_root,
        hardware_id=hardware_id,
        pipeline_stages=1,
        work_dir=work_root / f"{hardware_id}-compile-prefill",
    )
    transfer_predictor = _predictor(
        vidur_root=vidur_root,
        hardware_id=hardware_id,
        pipeline_stages=2,
        work_dir=work_root / f"{hardware_id}-compile-transfer",
    )

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
    from vidur.entities import Batch, Request

    # Fresh-reference predictors are independently instantiated after canonical
    # compilation and the source-equivalence fence have completed.
    prefill_predictor = _predictor(
        vidur_root=vidur_root,
        hardware_id=hardware_id,
        pipeline_stages=1,
        work_dir=work_root / f"{hardware_id}-fresh-prefill",
    )
    prefill: dict[int, float] = {}
    for axis in C64C_FRESH_PREFILL_AXES:
        request = Request(
            arrived_at=0.0,
            num_prefill_tokens=axis,
            num_decode_tokens=1,
            num_processed_tokens=0,
        )
        batch = Batch(replica_id=0, requests=[request], num_tokens=[axis])
        prefill[axis] = float(prefill_predictor.get_execution_time(batch, 0).model_time)

    transfer_predictor = _predictor(
        vidur_root=vidur_root,
        hardware_id=hardware_id,
        pipeline_stages=2,
        work_dir=work_root / f"{hardware_id}-fresh-transfer",
    )
    transfer: dict[int, float] = {}
    for axis in C64C_FRESH_TRANSFER_AXES:
        domain = transfer_lookup_domain(axis)
        if not domain.evaluable or domain.num_tokens is None:
            raise ValueError(f"frozen C6.4c transfer boundary is outside source domain: {axis}")
        milliseconds = float(transfer_predictor._predictions["send_recv"][(domain.num_tokens,)])
        transfer[axis] = milliseconds * 1e-3
    return prefill, transfer


def evaluate_once(vidur_root: Path) -> dict[str, Any]:
    snapshot = verify_upstream_snapshot(vidur_root)
    state_source_blobs = _verify_state_source_blobs(vidur_root)
    hardware_records: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="c64d-") as temp_dir:
        work_root = Path(temp_dir)
        compiled: dict[str, tuple[SourcePolynomialCostProfile, dict[str, Any], dict[str, Any]]] = {}
        equivalence_pass = True
        for hardware_id in sorted(C64B_HARDWARE_CONFIG):
            profile, equivalence = _compile_hardware(
                vidur_root=vidur_root,
                hardware_id=hardware_id,
                work_root=work_root,
            )
            decode = _validate_carried_decode(vidur_root, hardware_id)
            compiled[hardware_id] = (profile, equivalence, decode)
            equivalence_pass = equivalence_pass and equivalence["decision"] == "PASS"

        if not equivalence_pass:
            for hardware_id in sorted(compiled):
                profile, equivalence, decode = compiled[hardware_id]
                hardware_records.append(
                    {
                        "hardware_id": hardware_id,
                        "profile": profile.to_dict(),
                        "profile_fingerprint": profile.fingerprint,
                        "source_equivalence": equivalence,
                        "carried_single_token_decode": decode,
                        "fresh_evaluation_status": C64D_EQUIVALENCE_FAILED,
                        "fresh_prefill": None,
                        "fresh_transfer": None,
                        "decision": AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION.value,
                    }
                )
        else:
            # Only after every hardware profile passes the independent export
            # equivalence fence do we consume any C6.4c fresh reference timing.
            for hardware_id in sorted(compiled):
                profile, equivalence, decode = compiled[hardware_id]
                source_prefill, source_transfer = _fresh_source_values(
                    vidur_root=vidur_root,
                    hardware_id=hardware_id,
                    work_root=work_root,
                )
                predicted_prefill = {
                    axis: profile.prefill_seconds(axis) for axis in C64C_FRESH_PREFILL_AXES
                }
                predicted_transfer = {
                    axis: profile.transfer_seconds(float(axis))
                    for axis in C64C_FRESH_TRANSFER_AXES
                }
                prefill_report = _family_report(
                    hardware_id=hardware_id,
                    kind=ReferenceKind.COLD_PREFILL,
                    axes=C64C_FRESH_PREFILL_AXES,
                    references=source_prefill,
                    predictions=predicted_prefill,
                )
                transfer_report = _family_report(
                    hardware_id=hardware_id,
                    kind=ReferenceKind.POINT_TO_POINT_TRANSFER,
                    axes=C64C_FRESH_TRANSFER_AXES,
                    references=source_transfer,
                    predictions=predicted_transfer,
                )
                adequate = (
                    prefill_report["decision"]
                    == AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
                    and transfer_report["decision"]
                    == AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
                    and decode["decision"]
                    == AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
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
                        "fresh_evaluation_status": C64D_EVALUATED,
                        "fresh_prefill": prefill_report,
                        "fresh_transfer": transfer_report,
                        "decision": (
                            AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
                            if adequate
                            else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION.value
                        ),
                    }
                )

    parent_adequate = all(
        item["decision"] == AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
        for item in hardware_records
    )
    result: dict[str, Any] = {
        "schema": C64D_RESULT_SCHEMA,
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
        },
        "hardware": hardware_records,
        "decision": (
            AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
            if parent_adequate
            else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION.value
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
        help="compile is timing-free with respect to the frozen C6.4c boundary",
    )
    args = parser.parse_args()
    vidur_root = args.vidur_root.resolve()
    result = compile_once(vidur_root) if args.mode == "compile" else evaluate_once(vidur_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_canonical_json(result) + "\n", encoding="utf-8")
    print(_canonical_json(result))


if __name__ == "__main__":
    main()
