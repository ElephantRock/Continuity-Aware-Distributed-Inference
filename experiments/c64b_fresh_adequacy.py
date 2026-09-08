from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping

from simulator.c64b_protocol import (
    C64B_HARDWARE_CONFIG,
    C64B_PREDICTOR_CONFIG,
    C64B_PROTOCOL_ID,
    C64B_REFERENCE_EVIDENCE,
    C64B_SARATHI_CONFIG,
    C64B_UPSTREAM_CODE_BLOBS,
    c64b_protocol_fingerprint,
    transfer_lookup_domain,
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
    C6_THRESHOLD_ULP_BUDGET,
    C6_VALIDATION_MAPE_LIMIT,
    C6_VALIDATION_MAX_APE_LIMIT,
    AdequacyDecision,
    ReferenceKind,
    ReferencePartition,
    VIDUR_PINNED_COMMIT,
    split_reference_family,
)
from simulator.inference_cost import (
    ParameterProvenance,
    ParameterSourceClass,
    SourcedScalar,
)
from simulator.inference_cost_v2 import (
    C64A_FRESH_PREFILL_AXES,
    C64A_FRESH_PREFILL_BOUNDARY,
    C64A_FRESH_TRANSFER_AXES,
    C64A_FRESH_TRANSFER_BOUNDARY,
    CurveKnot,
    PiecewiseLinearCostCurve,
    assert_knots_match_c63_fit,
    verify_fresh_boundary_against_c63_partition,
)


C64B_RESULT_SCHEMA = "cadi.c6.4b.fresh-adequacy-result.v1"
EVALUATED = "EVALUATED"
OUT_OF_SOURCE_LOOKUP_DOMAIN = "OUT_OF_SOURCE_LOOKUP_DOMAIN"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _within_limit(value: float, limit: float) -> bool:
    return value <= limit or value - limit <= C6_THRESHOLD_ULP_BUDGET * math.ulp(limit)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True
    ).strip()


def verify_upstream_snapshot(vidur_root: Path) -> dict[str, Any]:
    head = _git(vidur_root, "rev-parse", "HEAD")
    if head != VIDUR_PINNED_COMMIT:
        raise ValueError(f"Vidur HEAD drift: expected={VIDUR_PINNED_COMMIT}, observed={head}")
    observed_blobs = {
        path: _git(vidur_root, "rev-parse", f"HEAD:{path}")
        for path in sorted(C64B_UPSTREAM_CODE_BLOBS)
    }
    if observed_blobs != dict(sorted(C64B_UPSTREAM_CODE_BLOBS.items())):
        raise ValueError(
            f"Vidur behavioral blob drift: expected={C64B_UPSTREAM_CODE_BLOBS}, "
            f"observed={observed_blobs}"
        )

    raw_sha256: dict[str, dict[str, str]] = {}
    for source in VIDUR_CALIBRATION_SOURCES:
        roles = {
            CalibrationArtifactRole.COMPUTE_ATTENTION: "attention",
            CalibrationArtifactRole.COMPUTE_MLP: "mlp",
            CalibrationArtifactRole.NETWORK_SEND_RECV: "send_recv",
        }
        hardware_hashes: dict[str, str] = {}
        for artifact in source.artifacts:
            if artifact.role not in roles:
                continue
            path = vidur_root / artifact.path
            git_blob = _git(vidur_root, "rev-parse", f"HEAD:{artifact.path}")
            if git_blob != artifact.git_blob_sha1:
                raise ValueError(
                    f"source blob drift for {source.hardware_id}/{artifact.path}: "
                    f"expected={artifact.git_blob_sha1}, observed={git_blob}"
                )
            hardware_hashes[roles[artifact.role]] = hashlib.sha256(path.read_bytes()).hexdigest()
        if hardware_hashes != VIDUR_SOURCE_SHA256[source.hardware_id]:
            raise ValueError(
                f"raw source SHA-256 drift for {source.hardware_id}: "
                f"expected={VIDUR_SOURCE_SHA256[source.hardware_id]}, "
                f"observed={hardware_hashes}"
            )
        raw_sha256[source.hardware_id] = hardware_hashes
    return {"head": head, "behavioral_git_blobs": observed_blobs, "raw_sha256": raw_sha256}


def _source_paths(vidur_root: Path, hardware_id: str) -> dict[str, Path]:
    source = next(item for item in VIDUR_CALIBRATION_SOURCES if item.hardware_id == hardware_id)
    by_role = {item.role: vidur_root / item.path for item in source.artifacts}
    return {
        "attention": by_role[CalibrationArtifactRole.COMPUTE_ATTENTION],
        "mlp": by_role[CalibrationArtifactRole.COMPUTE_MLP],
        "all_reduce": by_role[CalibrationArtifactRole.NETWORK_ALL_REDUCE],
        "send_recv": by_role[CalibrationArtifactRole.NETWORK_SEND_RECV],
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
    result: dict[ReferenceKind, ReferencePartition] = {}
    for kind in ReferenceKind:
        result[kind] = split_reference_family(
            [item.point for item in references if item.point.kind is kind]
        )
    return result


def _curve(partition: ReferencePartition) -> PiecewiseLinearCostCurve:
    provenance = ParameterProvenance(
        source_class=ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE,
        reference=(
            f"microsoft/vidur@{VIDUR_PINNED_COMMIT}; complete C6.3 FIT knot evidence"
        ),
    )
    curve = PiecewiseLinearCostCurve(
        curve_id=f"c6.4b:{partition.hardware_id}:{partition.kind.value}",
        hardware_id=partition.hardware_id,
        reference_kind=partition.kind,
        axis_unit=(
            "input-tokens"
            if partition.kind is ReferenceKind.COLD_PREFILL
            else "bytes"
        ),
        knots=tuple(
            CurveKnot(
                hardware_id=point.hardware_id,
                reference_kind=point.kind,
                axis_value=point.axis_value,
                seconds=SourcedScalar(
                    value=point.observed_seconds,
                    unit="seconds",
                    provenance=provenance,
                ),
                source_point_id=point.point_id,
            )
            for point in partition.fit
        ),
    )
    assert_knots_match_c63_fit(curve, partition)
    return curve


def _fresh_family_report(
    *,
    hardware_id: str,
    kind: ReferenceKind,
    curve: PiecewiseLinearCostCurve,
    source_values: Mapping[int, float | None],
    status: Mapping[int, str],
) -> dict[str, Any]:
    axes = (
        C64A_FRESH_PREFILL_AXES
        if kind is ReferenceKind.COLD_PREFILL
        else C64A_FRESH_TRANSFER_AXES
    )
    points: list[dict[str, Any]] = []
    evaluated_apes: list[float] = []
    evaluated_abs: list[float] = []
    all_evaluated = True
    zero_reference_exact = True
    for axis in axes:
        predicted = curve.evaluate(axis)
        observed = source_values.get(axis)
        point_status = status[axis]
        if point_status != EVALUATED:
            all_evaluated = False
            points.append(
                {
                    "axis_value": axis,
                    "status": point_status,
                    "reference_seconds": None,
                    "replacement_curve_seconds": predicted,
                    "absolute_error_seconds": None,
                    "absolute_percentage_error": None,
                }
            )
            continue
        if observed is None or not math.isfinite(observed) or observed < 0:
            raise ValueError(f"invalid source reference at {hardware_id}/{kind.value}/{axis}")
        absolute = abs(predicted - observed)
        if observed == 0:
            ape = None
            zero_reference_exact = zero_reference_exact and absolute == 0
        else:
            ape = absolute / observed
            evaluated_apes.append(ape)
        evaluated_abs.append(absolute)
        points.append(
            {
                "axis_value": axis,
                "status": point_status,
                "reference_seconds": observed,
                "replacement_curve_seconds": predicted,
                "absolute_error_seconds": absolute,
                "absolute_percentage_error": ape,
            }
        )

    subset_mae = math.fsum(evaluated_abs) / len(evaluated_abs) if evaluated_abs else None
    subset_mape = math.fsum(evaluated_apes) / len(evaluated_apes) if evaluated_apes else None
    subset_max_ape = max(evaluated_apes) if evaluated_apes else None
    adequate = (
        all_evaluated
        and zero_reference_exact
        and subset_mape is not None
        and subset_max_ape is not None
        and _within_limit(subset_mape, C6_VALIDATION_MAPE_LIMIT)
        and _within_limit(subset_max_ape, C6_VALIDATION_MAX_APE_LIMIT)
    )
    decision = (
        AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
        if adequate
        else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION
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
            "all_frozen_axes_must_be_source_evaluable": True,
        },
        "points": points,
        "all_frozen_axes_evaluated": all_evaluated,
        "evaluated_subset_mae_seconds": subset_mae,
        "evaluated_subset_mape": subset_mape,
        "evaluated_subset_max_ape": subset_max_ape,
        "decision": decision.value,
    }


def _predictor(
    *,
    vidur_root: Path,
    hardware_id: str,
    pipeline_stages: int,
    work_dir: Path,
):
    # Imported only after the caller has placed the pinned Vidur checkout on
    # PYTHONPATH. C6.4b does not vendor or alter the upstream implementation.
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
    return LinearRegressionExecutionTimePredictor(
        predictor_config=predictor_config,
        replica_config=replica_config,
        replica_scheduler_config=scheduler_config,
        metrics_config=metrics_config,
    )


def _fresh_source_values(
    *, vidur_root: Path, hardware_id: str, work_root: Path
) -> tuple[dict[int, float], dict[int, float | None], dict[int, str]]:
    from vidur.entities import Batch, Request

    prefill_predictor = _predictor(
        vidur_root=vidur_root,
        hardware_id=hardware_id,
        pipeline_stages=1,
        work_dir=work_root / f"{hardware_id}-prefill",
    )
    prefill: dict[int, float] = {}
    for axis in C64A_FRESH_PREFILL_AXES:
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
        work_dir=work_root / f"{hardware_id}-transfer",
    )
    transfer: dict[int, float | None] = {}
    transfer_status: dict[int, str] = {}
    for axis in C64A_FRESH_TRANSFER_AXES:
        domain = transfer_lookup_domain(axis)
        if not domain.evaluable:
            transfer[axis] = None
            transfer_status[axis] = OUT_OF_SOURCE_LOOKUP_DOMAIN
            continue
        assert domain.num_tokens is not None
        milliseconds = float(transfer_predictor._predictions["send_recv"][(domain.num_tokens,)])
        transfer[axis] = milliseconds * 1e-3
        transfer_status[axis] = EVALUATED
    return prefill, transfer, transfer_status


def evaluate_once(vidur_root: Path) -> dict[str, Any]:
    snapshot = verify_upstream_snapshot(vidur_root)
    hardware_records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="c64b-") as temp_dir:
        work_root = Path(temp_dir)
        for hardware_id in sorted(C64B_HARDWARE_CONFIG):
            partitions = _reference_partitions(vidur_root, hardware_id)
            prefill_partition = partitions[ReferenceKind.COLD_PREFILL]
            transfer_partition = partitions[ReferenceKind.POINT_TO_POINT_TRANSFER]
            verify_fresh_boundary_against_c63_partition(
                C64A_FRESH_PREFILL_BOUNDARY, prefill_partition
            )
            verify_fresh_boundary_against_c63_partition(
                C64A_FRESH_TRANSFER_BOUNDARY, transfer_partition
            )
            prefill_curve = _curve(prefill_partition)
            transfer_curve = _curve(transfer_partition)

            prefill_source, transfer_source, transfer_status = _fresh_source_values(
                vidur_root=vidur_root,
                hardware_id=hardware_id,
                work_root=work_root,
            )
            prefill_status = {axis: EVALUATED for axis in C64A_FRESH_PREFILL_AXES}
            prefill_report = _fresh_family_report(
                hardware_id=hardware_id,
                kind=ReferenceKind.COLD_PREFILL,
                curve=prefill_curve,
                source_values=prefill_source,
                status=prefill_status,
            )
            transfer_report = _fresh_family_report(
                hardware_id=hardware_id,
                kind=ReferenceKind.POINT_TO_POINT_TRANSFER,
                curve=transfer_curve,
                source_values=transfer_source,
                status=transfer_status,
            )
            decode_record = evaluate_family_adequacy(
                partitions[ReferenceKind.SINGLE_TOKEN_DECODE]
            )
            decode_decision = decode_record.decision
            hardware_adequate = (
                prefill_report["decision"]
                == AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
                and transfer_report["decision"]
                == AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
                and decode_decision
                is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
            )
            hardware_decision = (
                AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
                if hardware_adequate
                else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION
            )
            hardware_records.append(
                {
                    "hardware_id": hardware_id,
                    "frozen_c63_corpus_fingerprint": C63C_CORPUS_FINGERPRINTS[hardware_id],
                    "source_sha256": VIDUR_SOURCE_SHA256[hardware_id],
                    "replacement_curves": {
                        "prefill_fingerprint": prefill_curve.fingerprint,
                        "transfer_fingerprint": transfer_curve.fingerprint,
                        "prefill_fit_point_ids": list(
                            knot.source_point_id for knot in prefill_curve.knots
                        ),
                        "transfer_fit_point_ids": list(
                            knot.source_point_id for knot in transfer_curve.knots
                        ),
                    },
                    "fresh_prefill": prefill_report,
                    "fresh_transfer": transfer_report,
                    "carried_single_token_decode": decode_record.to_dict(),
                    "multi_token_decode_composition": {
                        "evidence_class": "ANALYTICALLY_DERIVED",
                        "mechanic": (
                            "unchanged C6.4a per-output-token fixed decode cost plus "
                            "context-token-step variable cost"
                        ),
                        "new_physical_validation": False,
                    },
                    "decision": hardware_decision.value,
                }
            )

    parent_adequate = all(
        item["decision"] == AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN.value
        for item in hardware_records
    )
    parent_decision = (
        AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
        if parent_adequate
        else AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION
    )
    result: dict[str, Any] = {
        "schema": C64B_RESULT_SCHEMA,
        "protocol_id": C64B_PROTOCOL_ID,
        "protocol_fingerprint": c64b_protocol_fingerprint(),
        "evidence_class": C64B_REFERENCE_EVIDENCE,
        "source_snapshot": snapshot,
        "hardware": hardware_records,
        "decision": parent_decision.value,
        "post_hoc_repairs": [],
    }
    result["scientific_fingerprint"] = _sha256_json(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vidur-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate_once(args.vidur_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_canonical_json(result) + "\n", encoding="utf-8")
    print(_canonical_json(result))


if __name__ == "__main__":
    main()
