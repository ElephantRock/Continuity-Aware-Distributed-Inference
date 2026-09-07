from dataclasses import replace
import math

import pytest

from simulator.calibration_validation import (
    C6_VALIDATION_MAPE_LIMIT,
    C6_VALIDATION_MAX_APE_LIMIT,
    C6_VALIDATION_REPORT_SCHEMA,
    VIDUR_LLAMA2_7B_TP1_DOMAIN,
    VIDUR_PINNED_COMMIT,
    AdequacyDecision,
    CalibrationReferencePoint,
    ReferenceKind,
    SourceArtifactRef,
    VidurComputeComponents,
    compose_vidur_llama2_7b_tp1_model_seconds,
    evaluate_reference_predictions,
    split_reference_family,
)


ATTENTION = SourceArtifactRef(
    path="data/profiling/compute/a100/meta-llama/Llama-2-7b-hf/attention.csv",
    git_blob_sha1="6ce0a3beab1618969d429b4313666b5dff6850dd",
)
MLP = SourceArtifactRef(
    path="data/profiling/compute/a100/meta-llama/Llama-2-7b-hf/mlp.csv",
    git_blob_sha1="479ed2f6ed22049ac444bca9fa44578532cbf28a",
)
SEND_RECV = SourceArtifactRef(
    path="data/profiling/network/a100_dgx/send_recv.csv",
    git_blob_sha1="418cd50858fdd604c3da21eb3aaa06e583059865",
)


def _point(
    point_id: str,
    axis: int,
    observed: float = 1.0,
    *,
    kind: ReferenceKind = ReferenceKind.COLD_PREFILL,
    hardware: str = "a100-80gb",
) -> CalibrationReferencePoint:
    artifacts = (SEND_RECV,) if kind is ReferenceKind.POINT_TO_POINT_TRANSFER else (ATTENTION, MLP)
    return CalibrationReferencePoint(
        point_id=point_id,
        hardware_id=hardware,
        kind=kind,
        axis_value=axis,
        observed_seconds=observed,
        derivation_id="test-derivation",
        source_commit=VIDUR_PINNED_COMMIT,
        source_artifacts=artifacts,
    )


def test_declared_vidur_reference_domain_is_narrow_and_fixed() -> None:
    domain = VIDUR_LLAMA2_7B_TP1_DOMAIN
    assert domain.model_id == "meta-llama/Llama-2-7b-hf"
    assert domain.hardware_ids == ("a100-80gb", "h100-80gb")
    assert domain.num_layers == 32
    assert domain.tensor_parallel_size == 1
    assert domain.pipeline_stages == 1
    assert domain.attention_block_size == 16
    assert domain.max_model_len == 4096
    assert domain.attention_backend == "FLASH_ATTENTION"


def test_vidur_tp1_model_time_composition_matches_execution_time_semantics() -> None:
    components = VidurComputeComponents(
        attn_pre_proj_ms=1,
        attn_post_proj_ms=2,
        attn_rope_ms=3,
        attn_kv_cache_save_ms=4,
        attention_kernel_ms=5,
        input_layernorm_ms=6,
        post_attention_layernorm_ms=7,
        mlp_up_proj_ms=8,
        mlp_down_proj_ms=9,
        mlp_act_ms=10,
        add_ms=11,
    )
    assert components.per_layer_ms == 66.0
    assert compose_vidur_llama2_7b_tp1_model_seconds(components) == pytest.approx(
        66 * 32 * 1e-3
    )


def test_compute_components_fail_closed_on_invalid_timings() -> None:
    kwargs = dict(
        attn_pre_proj_ms=0,
        attn_post_proj_ms=0,
        attn_rope_ms=0,
        attn_kv_cache_save_ms=0,
        attention_kernel_ms=0,
        input_layernorm_ms=0,
        post_attention_layernorm_ms=0,
        mlp_up_proj_ms=0,
        mlp_down_proj_ms=0,
        mlp_act_ms=0,
        add_ms=0,
    )
    with pytest.raises(ValueError, match="finite and non-negative"):
        VidurComputeComponents(**{**kwargs, "attention_kernel_ms": -1})
    with pytest.raises(ValueError, match="finite and non-negative"):
        VidurComputeComponents(**{**kwargs, "attention_kernel_ms": math.nan})


def test_reference_point_carries_exact_source_provenance_and_axis_units() -> None:
    prefill = _point("p-prefill", 128)
    decode = _point("p-decode", 256, kind=ReferenceKind.SINGLE_TOKEN_DECODE)
    transfer = _point("p-transfer", 4096, kind=ReferenceKind.POINT_TO_POINT_TRANSFER)

    assert prefill.axis_unit == "input-tokens"
    assert decode.axis_unit == "context-tokens"
    assert transfer.axis_unit == "bytes"
    assert prefill.to_dict()["source_commit"] == VIDUR_PINNED_COMMIT
    assert prefill.to_dict()["source_repository"] == "microsoft/vidur"

    reordered = replace(prefill, source_artifacts=tuple(reversed(prefill.source_artifacts)))
    assert prefill.to_dict()["source_artifacts"] == reordered.to_dict()["source_artifacts"]

    with pytest.raises(ValueError, match="pinned C6.2 Vidur commit"):
        replace(prefill, source_commit="0" * 40)
    with pytest.raises(ValueError, match="outside the declared"):
        replace(prefill, hardware_id="other")


def test_reference_split_is_canonical_and_even_odd() -> None:
    points = (
        _point("p256", 256),
        _point("p32", 32),
        _point("p128", 128),
        _point("p64", 64),
    )
    partition = split_reference_family(points)
    assert [point.point_id for point in partition.fit] == ["p32", "p128"]
    assert [point.point_id for point in partition.validation] == ["p64", "p256"]

    assert split_reference_family(tuple(reversed(points))) == partition


def test_reference_split_rejects_ambiguous_or_underpowered_families() -> None:
    points = [_point("p1", 1), _point("p2", 2), _point("p3", 3), _point("p4", 4)]

    with pytest.raises(ValueError, match="at least four"):
        split_reference_family(points[:3])
    with pytest.raises(ValueError, match="axis values must be unique"):
        split_reference_family([points[0], replace(points[1], axis_value=1), points[2], points[3]])
    with pytest.raises(ValueError, match="point IDs must be unique"):
        split_reference_family([points[0], replace(points[1], point_id="p1"), points[2], points[3]])
    with pytest.raises(ValueError, match="one hardware_id and one kind"):
        split_reference_family([points[0], points[1], points[2], replace(points[3], hardware_id="h100-80gb")])


def test_exact_predictions_pass_and_report_schema_is_canonical() -> None:
    partition = split_reference_family(
        [_point("p1", 1), _point("p2", 2), _point("p3", 3), _point("p4", 4)]
    )
    report = evaluate_reference_predictions(partition, {"p2": 1.0, "p4": 1.0})

    assert report.mae_seconds == 0.0
    assert report.mape == 0.0
    assert report.max_ape == 0.0
    assert report.zero_reference_count == 0
    assert report.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN
    payload = report.to_dict()
    assert payload["schema"] == C6_VALIDATION_REPORT_SCHEMA
    assert payload["thresholds"]["mape_limit"] == C6_VALIDATION_MAPE_LIMIT
    assert payload["thresholds"]["max_ape_limit"] == C6_VALIDATION_MAX_APE_LIMIT
    assert report.to_json() == report.to_json()


def test_predeclared_thresholds_are_inclusive() -> None:
    partition = split_reference_family(
        [_point("p1", 1), _point("p2", 2), _point("p3", 3), _point("p4", 4)]
    )
    report = evaluate_reference_predictions(partition, {"p2": 1.1, "p4": 1.0})
    assert report.mape == pytest.approx(0.05)
    assert report.max_ape == pytest.approx(0.10)
    assert report.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN


def test_max_ape_gate_can_fail_even_when_mape_passes() -> None:
    points = [_point(f"p{i}", i) for i in range(1, 9)]
    partition = split_reference_family(points)
    predictions = {point.point_id: 1.0 for point in partition.validation}
    predictions[partition.validation[0].point_id] = 1.101
    report = evaluate_reference_predictions(partition, predictions)

    assert report.mape is not None and report.mape < C6_VALIDATION_MAPE_LIMIT
    assert report.max_ape is not None and report.max_ape > C6_VALIDATION_MAX_APE_LIMIT
    assert report.decision is AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION


def test_zero_reference_percentage_is_null_and_mismatch_fails_closed() -> None:
    points = [
        _point("p1", 1, 1.0),
        _point("p2", 2, 0.0),
        _point("p3", 3, 1.0),
        _point("p4", 4, 1.0),
    ]
    partition = split_reference_family(points)

    exact_zero = evaluate_reference_predictions(partition, {"p2": 0.0, "p4": 1.0})
    assert exact_zero.zero_reference_count == 1
    assert exact_zero.errors[0].absolute_percentage_error is None
    assert exact_zero.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN

    mismatch = evaluate_reference_predictions(partition, {"p2": 0.001, "p4": 1.0})
    assert mismatch.errors[0].absolute_percentage_error is None
    assert mismatch.decision is AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION


def test_all_zero_validation_has_no_percentage_metric_and_is_inadequate() -> None:
    points = [
        _point("p1", 1, 1.0),
        _point("p2", 2, 0.0),
        _point("p3", 3, 1.0),
        _point("p4", 4, 0.0),
    ]
    partition = split_reference_family(points)
    report = evaluate_reference_predictions(partition, {"p2": 0.0, "p4": 0.0})
    assert report.mape is None
    assert report.max_ape is None
    assert report.zero_reference_count == 2
    assert report.decision is AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION


def test_prediction_keys_and_values_fail_closed() -> None:
    partition = split_reference_family(
        [_point("p1", 1), _point("p2", 2), _point("p3", 3), _point("p4", 4)]
    )
    with pytest.raises(ValueError, match="prediction key mismatch"):
        evaluate_reference_predictions(partition, {"p2": 1.0})
    with pytest.raises(ValueError, match="prediction key mismatch"):
        evaluate_reference_predictions(partition, {"p2": 1.0, "p4": 1.0, "extra": 1.0})
    with pytest.raises(ValueError, match="finite and non-negative"):
        evaluate_reference_predictions(partition, {"p2": -1.0, "p4": 1.0})
