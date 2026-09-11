from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import struct
import subprocess
import tempfile
from typing import Any, Iterable, Sequence

from simulator.c64b_protocol import (
    C64B_HARDWARE_CONFIG,
    C64B_PREDICTOR_CONFIG,
    C64B_REFERENCE_EVIDENCE,
    C64B_SARATHI_CONFIG,
    C64B_UPSTREAM_CODE_BLOBS,
)
from simulator.c64d_export import (
    C63_CARRIED_DECODE,
    C64D_STATE_SOURCE_BLOBS,
    state_memory_scalars,
)
from simulator.calibration_adequacy import (
    C63C_CORPUS_FINGERPRINTS,
    evaluate_family_adequacy,
)
from simulator.calibration_projection import (
    VIDUR_SOURCE_SHA256,
    derive_vidur_reference_corpus,
)
from simulator.calibration_source import (
    CalibrationArtifactRole,
    VIDUR_CALIBRATION_SOURCES,
)
from simulator.calibration_validation import (
    AdequacyDecision,
    ReferenceKind,
    ReferencePartition,
    VIDUR_PINNED_COMMIT,
    split_reference_family,
)
from simulator.inference_cost_v4 import (
    C64E_EQUIVALENCE_ULP_BUDGET,
    C64E_EXHAUSTIVE_PROTOCOL,
    C64E_INVALIDATED_BOUNDARY_STATUS,
    C64E_NUMERICAL_SOURCE_BLOBS,
    C64E_PREFILL_COMPONENT_IDS,
    C64E_REFERENCE_EVIDENCE,
    C64E_REPORT_MAPE_LIMIT,
    C64E_REPORT_MAX_APE_LIMIT,
    C64E_REPRESENTATION_ID,
    C64E_TRANSFER_BYTES_PER_TOKEN,
    assert_c64e_numerical_runtime,
    export_source_ordered_polynomial,
    materialize_source_kernel_domain,
    profile_from_source_models,
)


C64F_RESULT_SCHEMA = "cadi.c6.4f.exhaustive-source-equivalence-result.v1"
C64F_RESULT_KIND = "EXHAUSTIVE_FINITE_DECLARED_DOMAIN_SOURCE_EQUIVALENCE"
C64F_EXPECTED_PROTOCOL_FINGERPRINT = (
    "adc341d4e3eddf72c82e3a0c7b139b703b9ed0e662a724870aa8d74abe1f7df8"
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _git_blob_sha1_bytes(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _verify_sklearn_source_blobs() -> dict[str, str]:
    import sklearn.linear_model._base as linear_base
    import sklearn.preprocessing._polynomial as polynomial

    module_by_key = {
        "scikit-learn/sklearn/preprocessing/_polynomial.py": polynomial,
        "scikit-learn/sklearn/linear_model/_base.py": linear_base,
    }
    observed: dict[str, str] = {}
    for key, module in module_by_key.items():
        module_path = Path(module.__file__).resolve()
        observed[key] = _git_blob_sha1_bytes(module_path.read_bytes())
    expected = {key: C64E_NUMERICAL_SOURCE_BLOBS[key] for key in sorted(module_by_key)}
    if observed != expected:
        raise ValueError(
            f"installed sklearn source drift: expected={expected}, observed={observed}"
        )
    return observed


def _source_paths(vidur_root: Path, hardware_id: str) -> dict[str, Path]:
    source = next(item for item in VIDUR_CALIBRATION_SOURCES if item.hardware_id == hardware_id)
    by_role = {item.role: vidur_root / item.path for item in source.artifacts}
    return {
        "attention": by_role[CalibrationArtifactRole.COMPUTE_ATTENTION],
        "mlp": by_role[CalibrationArtifactRole.COMPUTE_MLP],
        "all_reduce": by_role[CalibrationArtifactRole.NETWORK_ALL_REDUCE],
        "send_recv": by_role[CalibrationArtifactRole.NETWORK_SEND_RECV],
    }


def _verify_upstream_snapshot(vidur_root: Path) -> dict[str, Any]:
    worktree_status = _git(vidur_root, "status", "--porcelain", "--untracked-files=all")
    if worktree_status:
        raise ValueError(
            "Vidur working tree must be clean before source-model evaluation: "
            f"status={worktree_status!r}"
        )
    head = _git(vidur_root, "rev-parse", "HEAD")
    if head != VIDUR_PINNED_COMMIT:
        raise ValueError(f"Vidur HEAD drift: expected={VIDUR_PINNED_COMMIT}, observed={head}")

    expected_vidur_blobs = dict(C64B_UPSTREAM_CODE_BLOBS)
    expected_vidur_blobs.update(
        {
            key: value
            for key, value in C64E_NUMERICAL_SOURCE_BLOBS.items()
            if key.startswith("vidur/")
        }
    )
    expected_vidur_blobs.update(C64D_STATE_SOURCE_BLOBS)
    observed_vidur_blobs = {
        path: _git(vidur_root, "rev-parse", f"HEAD:{path}")
        for path in sorted(expected_vidur_blobs)
    }
    if observed_vidur_blobs != dict(sorted(expected_vidur_blobs.items())):
        raise ValueError(
            f"Vidur source blob drift: expected={expected_vidur_blobs}, "
            f"observed={observed_vidur_blobs}"
        )

    raw_sha256: dict[str, dict[str, str]] = {}
    role_names = {
        CalibrationArtifactRole.COMPUTE_ATTENTION: "attention",
        CalibrationArtifactRole.COMPUTE_MLP: "mlp",
        CalibrationArtifactRole.NETWORK_SEND_RECV: "send_recv",
    }
    for source in VIDUR_CALIBRATION_SOURCES:
        hardware_hashes: dict[str, str] = {}
        for artifact in source.artifacts:
            if artifact.role not in role_names:
                continue
            source_path = vidur_root / artifact.path
            observed_git_blob = _git(vidur_root, "rev-parse", f"HEAD:{artifact.path}")
            if observed_git_blob != artifact.git_blob_sha1:
                raise ValueError(
                    f"source artifact blob drift for {source.hardware_id}/{artifact.path}"
                )
            hardware_hashes[role_names[artifact.role]] = hashlib.sha256(
                source_path.read_bytes()
            ).hexdigest()
        if hardware_hashes != VIDUR_SOURCE_SHA256[source.hardware_id]:
            raise ValueError(
                f"raw source SHA-256 drift for {source.hardware_id}: "
                f"expected={VIDUR_SOURCE_SHA256[source.hardware_id]}, "
                f"observed={hardware_hashes}"
            )
        raw_sha256[source.hardware_id] = hardware_hashes

    return {
        "head": head,
        "vidur_git_blobs": observed_vidur_blobs,
        "sklearn_git_blobs": _verify_sklearn_source_blobs(),
        "raw_source_sha256": raw_sha256,
    }


def _reference_partitions(
    vidur_root: Path, hardware_id: str
) -> dict[ReferenceKind, ReferencePartition]:
    paths = _source_paths(vidur_root, hardware_id)
    references = derive_vidur_reference_corpus(
        hardware_id,
        attention_csv=paths["attention"].read_text(encoding="utf-8-sig"),
        mlp_csv=paths["mlp"].read_text(encoding="utf-8-sig"),
        send_recv_csv=paths["send_recv"].read_text(encoding="utf-8-sig"),
    )
    return {
        kind: split_reference_family(
            [item.point for item in references if item.point.kind is kind]
        )
        for kind in ReferenceKind
    }


def _predictor(
    *,
    vidur_root: Path,
    hardware_id: str,
    pipeline_stages: int,
    work_dir: Path,
):
    from vidur.config import (
        LinearRegressionExecutionTimePredictorConfig,
        MetricsConfig,
        ReplicaConfig,
        SarathiSchedulerConfig,
    )
    from vidur.execution_time_predictor.linear_regression_execution_time_predictor import (
        LinearRegressionExecutionTimePredictor,
    )

    paths = _source_paths(vidur_root, hardware_id)
    predictor_config = LinearRegressionExecutionTimePredictorConfig(
        **C64B_PREDICTOR_CONFIG,
        compute_input_file=str(paths["mlp"]),
        attention_input_file=str(paths["attention"]),
        all_reduce_input_file=str(paths["all_reduce"]),
        send_recv_input_file=str(paths["send_recv"]),
        cpu_overhead_input_file=str(work_dir / "unused-cpu-overheads.csv"),
    )
    hardware = C64B_HARDWARE_CONFIG[hardware_id]
    replica_config = ReplicaConfig(
        model_name="meta-llama/Llama-2-7b-hf",
        num_pipeline_stages=pipeline_stages,
        tensor_parallel_size=1,
        device=hardware["vidur_device"],
        network_device=hardware["network_device"],
    )
    scheduler_config = SarathiSchedulerConfig(**C64B_SARATHI_CONFIG)
    metrics_config = MetricsConfig(
        write_metrics=False,
        enable_chrome_trace=False,
        store_plots=False,
        store_operation_metrics=False,
        store_token_completion_metrics=False,
        store_request_metrics=False,
        store_batch_metrics=False,
        store_utilization_metrics=False,
        output_dir=str(work_dir / "output"),
        cache_dir=str(work_dir / "cache"),
    )
    # Source training/materialization is numerical work too. Fence the exact runtime
    # in this process immediately before upstream construction triggers it.
    assert_c64e_numerical_runtime()
    return LinearRegressionExecutionTimePredictor(
        predictor_config=predictor_config,
        replica_config=replica_config,
        replica_scheduler_config=scheduler_config,
        metrics_config=metrics_config,
    )


def _source_reference(hardware_id: str, component_id: str) -> str:
    return (
        f"microsoft/vidur@{VIDUR_PINNED_COMMIT}; hardware={hardware_id}; "
        f"fitted LinearRegressionExecutionTimePredictor component={component_id}; "
        f"protocol={C64F_EXPECTED_PROTOCOL_FINGERPRINT}"
    )


def _export_profile(prefill_predictor: Any, transfer_predictor: Any, hardware_id: str):
    missing_prefill = [
        component_id
        for component_id in C64E_PREFILL_COMPONENT_IDS
        if component_id not in prefill_predictor._models
    ]
    if missing_prefill:
        raise ValueError(f"required prefill source models missing: {missing_prefill}")
    if "attn_prefill" not in prefill_predictor._models:
        raise ValueError("required attn_prefill source model missing")
    if "send_recv" not in transfer_predictor._models:
        raise ValueError("required send_recv source model missing")

    components = tuple(
        export_source_ordered_polynomial(
            prefill_predictor._models[component_id],
            component_id=component_id,
            hardware_id=hardware_id,
            expected_feature_names=("num_tokens",),
            source_reference=_source_reference(hardware_id, component_id),
        )
        for component_id in C64E_PREFILL_COMPONENT_IDS
    )
    attention = export_source_ordered_polynomial(
        prefill_predictor._models["attn_prefill"],
        component_id="attn_prefill",
        hardware_id=hardware_id,
        expected_feature_names=("kv_cache_size", "prefill_chunk_size_squared"),
        source_reference=_source_reference(hardware_id, "attn_prefill"),
    )
    transfer = export_source_ordered_polynomial(
        transfer_predictor._models["send_recv"],
        component_id="send_recv",
        hardware_id=hardware_id,
        expected_feature_names=("num_tokens",),
        source_reference=_source_reference(hardware_id, "send_recv"),
    )
    return profile_from_source_models(
        hardware_id=hardware_id,
        prefill_components=components,
        prefill_attention=attention,
        transfer=transfer,
    )


def _ordered_binary64(value: float) -> int:
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    sign = 1 << 63
    mask = (1 << 64) - 1
    return ((~bits) & mask) if bits & sign else bits | sign


def _ulp_distance(left: float, right: float) -> int:
    if left == right:
        return 0
    return abs(_ordered_binary64(left) - _ordered_binary64(right))


def _family_report(
    *,
    family: str,
    axes: Iterable[int],
    source_values: Sequence[float],
    projected_values: Sequence[float],
    bytes_per_axis_unit: int | None = None,
) -> dict[str, Any]:
    axis_values = tuple(axes)
    source = tuple(float(value) for value in source_values)
    projected = tuple(float(value) for value in projected_values)
    if len(axis_values) != len(source) or len(source) != len(projected) or not source:
        raise ValueError("exhaustive family axes/source/projected lengths must match and be non-empty")

    points: list[dict[str, Any]] = []
    apes: list[float] = []
    absolute_errors: list[float] = []
    max_ulp = 0
    violations = 0
    zero_reference_exact = True
    for axis, reference, prediction in zip(axis_values, source, projected, strict=True):
        if not math.isfinite(reference) or reference < 0:
            raise ValueError(f"invalid source value for {family}/{axis}: {reference}")
        if not math.isfinite(prediction) or prediction < 0:
            raise ValueError(f"invalid projected value for {family}/{axis}: {prediction}")
        absolute = abs(prediction - reference)
        distance = _ulp_distance(prediction, reference)
        max_ulp = max(max_ulp, distance)
        if distance > C64E_EQUIVALENCE_ULP_BUDGET:
            violations += 1
        absolute_errors.append(absolute)
        if reference == 0.0:
            ape = None
            zero_reference_exact = zero_reference_exact and prediction == 0.0
        else:
            ape = absolute / reference
            apes.append(ape)
        point: dict[str, Any] = {
            "axis_value": axis,
            "source_seconds": reference,
            "v4_seconds": prediction,
            "absolute_error_seconds": absolute,
            "absolute_percentage_error": ape,
            "ulp_distance": distance,
            "within_ulp_budget": distance <= C64E_EQUIVALENCE_ULP_BUDGET,
        }
        if bytes_per_axis_unit is not None:
            point["public_bytes"] = axis * bytes_per_axis_unit
        points.append(point)

    mape = math.fsum(apes) / len(apes) if apes else 0.0
    max_ape = max(apes) if apes else 0.0
    mae = math.fsum(absolute_errors) / len(absolute_errors)
    aggregate_pass = (
        zero_reference_exact
        and mape <= C64E_REPORT_MAPE_LIMIT
        and max_ape <= C64E_REPORT_MAX_APE_LIMIT
    )
    passed = violations == 0 and aggregate_pass
    return {
        "family": family,
        "evidence_class": C64E_REFERENCE_EVIDENCE,
        "point_count": len(points),
        "ulp_budget": C64E_EQUIVALENCE_ULP_BUDGET,
        "max_ulp_distance": max_ulp,
        "ulp_violation_count": violations,
        "zero_reference_requires_exact_zero": True,
        "zero_reference_exact": zero_reference_exact,
        "mae_seconds": mae,
        "mape": mape,
        "max_ape": max_ape,
        "aggregate_limits": {
            "mape": C64E_REPORT_MAPE_LIMIT,
            "max_ape": C64E_REPORT_MAX_APE_LIMIT,
            "cannot_excuse_ulp_violation": True,
        },
        "points": points,
        "decision": "PASS" if passed else "FAIL",
    }


def _source_prefill_seconds(prefill_predictor: Any) -> tuple[float, ...]:
    from vidur.entities import Batch, Request

    values: list[float] = []
    for axis in range(1, 4097):
        request = Request(
            arrived_at=0.0,
            num_prefill_tokens=axis,
            num_decode_tokens=1,
            num_processed_tokens=0,
        )
        batch = Batch(replica_id=0, requests=[request], num_tokens=[axis])
        values.append(float(prefill_predictor.get_execution_time(batch, 0).model_time))
    return tuple(values)


def _source_transfer_seconds(transfer_predictor: Any) -> tuple[float, ...]:
    table = transfer_predictor._predictions.get("send_recv")
    if not isinstance(table, dict):
        raise ValueError("source send_recv prediction table missing")
    return tuple(float(table[(tokens,)]) * 1e-3 for tokens in range(1, 4097))


def _validate_carried_decode(
    *, hardware_id: str, partitions: dict[ReferenceKind, ReferencePartition], profile: Any
) -> dict[str, Any]:
    record = evaluate_family_adequacy(partitions[ReferenceKind.SINGLE_TOKEN_DECODE])
    expected_fixed, expected_slope = C63_CARRIED_DECODE[hardware_id]
    fit = record.fit
    if fit is None:
        raise ValueError("carried decode adequacy record has no fitted primitive")
    coefficients_match = (
        fit.intercept_seconds == expected_fixed
        and fit.slope_seconds_per_axis_unit == expected_slope
        and profile.decode_fixed_seconds_per_output_token.value == expected_fixed
        and profile.decode_seconds_per_context_token_step.value == expected_slope
    )
    adequate = (
        coefficients_match
        and record.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
    )
    return {
        "coefficients_match_frozen_c63": coefficients_match,
        "record": record.to_dict(),
        "decision": "PASS" if adequate else "FAIL",
    }


def _validate_state_memory(hardware_id: str, profile: Any) -> dict[str, Any]:
    state_fixed, state_per_token, memory_capacity = state_memory_scalars(hardware_id)
    observed = {
        "state_fixed_bytes": profile.state_fixed_bytes.to_dict(),
        "state_bytes_per_token": profile.state_bytes_per_token.to_dict(),
        "memory_capacity_bytes": profile.memory_capacity_bytes.to_dict(),
    }
    expected = {
        "state_fixed_bytes": state_fixed.to_dict(),
        "state_bytes_per_token": state_per_token.to_dict(),
        "memory_capacity_bytes": memory_capacity.to_dict(),
    }
    passed = observed == expected
    return {
        "expected": expected,
        "observed": observed,
        "decision": "PASS" if passed else "FAIL",
    }


def _hardware_record(
    *, vidur_root: Path, hardware_id: str, work_root: Path
) -> dict[str, Any]:
    partitions = _reference_partitions(vidur_root, hardware_id)

    prefill_predictor = _predictor(
        vidur_root=vidur_root,
        hardware_id=hardware_id,
        pipeline_stages=1,
        work_dir=work_root / f"{hardware_id}-prefill",
    )
    transfer_predictor = _predictor(
        vidur_root=vidur_root,
        hardware_id=hardware_id,
        pipeline_stages=2,
        work_dir=work_root / f"{hardware_id}-transfer",
    )

    profile = _export_profile(prefill_predictor, transfer_predictor, hardware_id)
    materialized = materialize_source_kernel_domain(profile)

    source_prefill = _source_prefill_seconds(prefill_predictor)
    source_transfer = _source_transfer_seconds(transfer_predictor)
    v4_prefill = tuple(materialized.prefill_seconds(axis) for axis in range(1, 4097))
    v4_transfer = tuple(
        materialized.transfer_seconds(tokens * C64E_TRANSFER_BYTES_PER_TOKEN)
        for tokens in range(1, 4097)
    )

    prefill_report = _family_report(
        family="cold_prefill",
        axes=range(1, 4097),
        source_values=source_prefill,
        projected_values=v4_prefill,
    )
    transfer_report = _family_report(
        family="point_to_point_transfer",
        axes=range(1, 4097),
        source_values=source_transfer,
        projected_values=v4_transfer,
        bytes_per_axis_unit=C64E_TRANSFER_BYTES_PER_TOKEN,
    )
    decode_report = _validate_carried_decode(
        hardware_id=hardware_id,
        partitions=partitions,
        profile=profile,
    )
    state_memory_report = _validate_state_memory(hardware_id, profile)
    passed = all(
        item["decision"] == "PASS"
        for item in (
            prefill_report,
            transfer_report,
            decode_report,
            state_memory_report,
        )
    )
    return {
        "hardware_id": hardware_id,
        "source_corpus_fingerprint": C63C_CORPUS_FINGERPRINTS[hardware_id],
        "source_sha256": VIDUR_SOURCE_SHA256[hardware_id],
        "profile_fingerprint": profile.fingerprint,
        "materialized_domain_fingerprint": materialized.fingerprint,
        "prefill": prefill_report,
        "transfer": transfer_report,
        "carried_decode": decode_report,
        "state_memory": state_memory_report,
        "decision": (
            AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
            if passed
            else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION.value
        ),
    }


def evaluate_once(vidur_root: Path) -> dict[str, Any]:
    protocol_fingerprint = C64E_EXHAUSTIVE_PROTOCOL.fingerprint
    if protocol_fingerprint != C64F_EXPECTED_PROTOCOL_FINGERPRINT:
        raise ValueError(
            "C6.4f frozen protocol identity drift before source evaluation: "
            f"expected={C64F_EXPECTED_PROTOCOL_FINGERPRINT}, "
            f"observed={protocol_fingerprint}"
        )
    if C64E_EXHAUSTIVE_PROTOCOL.to_dict()["source_predictions_evaluated_by_this_protocol_freeze"]:
        raise ValueError("C6.4e protocol record unexpectedly claims source evaluation")

    runtime = assert_c64e_numerical_runtime()
    snapshot = _verify_upstream_snapshot(vidur_root)
    hardware_records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="c64f-") as temp_dir:
        work_root = Path(temp_dir)
        for hardware_id in sorted(C64B_HARDWARE_CONFIG):
            hardware_records.append(
                _hardware_record(
                    vidur_root=vidur_root,
                    hardware_id=hardware_id,
                    work_root=work_root,
                )
            )

    parent_pass = all(
        item["decision"] == AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
        for item in hardware_records
    )
    result: dict[str, Any] = {
        "schema": C64F_RESULT_SCHEMA,
        "result_kind": C64F_RESULT_KIND,
        "representation_id": C64E_REPRESENTATION_ID,
        "protocol_fingerprint": protocol_fingerprint,
        "evidence_class": C64B_REFERENCE_EVIDENCE,
        "runtime": runtime,
        "source_snapshot": snapshot,
        "boundary_accounting": {
            "c64c_boundary_status": C64E_INVALIDATED_BOUNDARY_STATUS,
            "replacement_fresh_boundary": None,
            "holdout_claim": False,
            "validation_mode": "EXHAUSTIVE_FINITE_DECLARED_DOMAIN_EQUIVALENCE",
            "out_of_domain_generalization_claim": False,
        },
        "hardware": hardware_records,
        "post_hoc_repairs": [],
        "decision": (
            AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
            if parent_pass
            else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION.value
        ),
    }
    result["scientific_fingerprint"] = _sha256_json(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vidur-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if platform.python_implementation() != "CPython":
        raise SystemExit("C6.4f requires CPython")
    result = evaluate_once(args.vidur_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_canonical_json(result) + "\n", encoding="utf-8")
    print(
        _canonical_json(
            {
                "decision": result["decision"],
                "protocol_fingerprint": result["protocol_fingerprint"],
                "scientific_fingerprint": result["scientific_fingerprint"],
                "hardware": [
                    {
                        "hardware_id": record["hardware_id"],
                        "decision": record["decision"],
                        "prefill_max_ulp": record["prefill"]["max_ulp_distance"],
                        "prefill_violations": record["prefill"]["ulp_violation_count"],
                        "transfer_max_ulp": record["transfer"]["max_ulp_distance"],
                        "transfer_violations": record["transfer"]["ulp_violation_count"],
                    }
                    for record in result["hardware"]
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
