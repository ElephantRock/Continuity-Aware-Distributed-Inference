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
    ReferencePartition,
    SourceArtifactRef,
    VidurComputeComponents,
    compose_vidur_llama2_7b_tp1_model_seconds,
    evaluate_reference_predictions,
    split_reference_family,
)


MODEL_CONFIG = SourceArtifactRef(
    path="vidur/config/model_config.py",
    git_blob_sha1="722299bbb556ccbab2b82609598be6b8c2963c29",
)
A100_ATTENTION = SourceArtifactRef(
    path="data/profiling/compute/a100/meta-llama/Llama-2-7b-hf/attention.csv",
    git_blob_sha1="6ce0a3beab1618969d429b4313666b5dff6850dd",
)
A100_MLP = SourceArtifactRef(
    path="data/profiling/compute/a100/meta-llama/Llama-2-7b-hf/mlp.csv",
    git_blob_sha1="479ed2f6ed22049ac444bca9fa44578532cbf28a",
)
A100_SEND_RECV = SourceArtifactRef(
    path="data/profiling/network/a100_dgx/send_recv.csv",
    git_blob_sha1="418cd50858fdd604c3da21eb3aaa06e583059865",
)
H100_ATTENTION = SourceArtifactRef(
    path="data/profiling/compute/h100/meta-llama/Llama-2-7b-hf/attention.csv",
    git_blob_sha1="dfc6b05e232e3d243bf3c89feb3f380a77f5ad0d",
)
H100_MLP = SourceArtifactRef(
    path="data/profiling/compute/h100/meta-llama/Llama-2-7b-hf/mlp.csv",
    git_blob_sha1="661ea954e9508d67f37d3b24f0e07eb96f83ca46",
)
H100_SEND_RECV = SourceArtifactRef(
    path="data/profiling/network/h100_dgx/send_recv.csv",
    git_blob_sha1="fed60792e5d5900ea008e4d26427f39b7285434f",
)


def _artifacts(hardware: str, kind: ReferenceKind) -> tuple[SourceArtifactRef, ...]:
    if hardware == "a100-80gb":
        compute = (MODEL_CONFIG, A100_ATTENTION, A100_MLP)
        transfer = (A100_SEND_RECV,)
    elif hardware == "h100-80gb":
        compute = (MODEL_CONFIG, H100_ATTENTION, H100_MLP)
        transfer = (H100_SEND_RECV,)
    else:
        raise AssertionError("test helper only supports the declared hardware domain")
    return transfer if kind is ReferenceKind.POINT_TO_POINT_TRANSFER else compute


def _point(
    point_id: str,
    axis: int,
    observed: float = 1.0,
    *,
    kind: ReferenceKind = ReferenceKind.COLD_PREFILL,
    hardware: str = "a100-80gb",
) -> CalibrationReferencePoint:
    return CalibrationReferencePoint(
        point_id=point_id,
        hardware_id=hardware,
        kind=kind,
        axis_value=axis,
        observed_seconds=observed,
        derivation_id="test-derivation",
        source_commit=VIDUR_PINNED_COMMIT,
        source_artifacts=_artifacts(hardware, kind),
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
    assert len(prefill.source_artifacts) == 3
    assert len(transfer.source_artifacts) == 1
    assert prefill.to_dict()["source_commit"] == VIDUR_PINNED_COMMIT
    assert prefill.to_dict()["source_repository"] == "microsoft/vidur"

    reordered = replace(prefill, source_artifacts=tuple(reversed(prefill.source_artifacts)))
    assert prefill.to_dict()["source_artifacts"] == reordered.to_dict()["source_artifacts"]

    with pytest.raises(ValueError, match="pinned C6.2 Vidur commit"):
        replace(prefill, source_commit="0" * 40)
    with pytest.raises(ValueError, match="outside the declared"):
        replace(prefill, hardware_id="other")


def test_reference_point_rejects_incomplete_or_wrong_manifest_artifacts() -> None:
    prefill = _point("p-prefill", 128)
    transfer = _point("p-transfer", 4096, kind=ReferenceKind.POINT_TO_POINT_TRANSFER)

    with pytest.raises(ValueError, match="exactly match the pinned C6.2 roles"):
        replace(prefill, source_artifacts=prefill.source_artifacts[:-1])
    with pytest.raises(ValueError, match="exactly match the pinned C6.2 roles"):
        replace(prefill, source_artifacts=(MODEL_CONFIG, H100_ATTENTION, H100_MLP))
    with pytest.raises(ValueError, match="exactly match the pinned C6.2 roles"):
        replace(transfer, source_artifacts=(H100_SEND_RECV,))


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


def test_direct_partition_construction_cannot_bypass_canonical_split() -> None:
    points = tuple(_point(f"p{i}", i) for i in range(1, 5))
    with pytest.raises(ValueError, match="canonical even-FIT/odd-VALIDATION"):
        ReferencePartition(
            hardware_id="a100-80gb",
            kind=ReferenceKind.COLD_PREFILL,
            fit=(points[0], points[3]),
            validation=(points[1], points[2]),
        )


def test_reference_split_rejects_ambiguous_or_underpowered_families() -> None:
    points = [_point("p1", 1), _point("p2", 2), _point("p3", 3), _point("p4", 4)]

    with pytest.raises(ValueError, match="at least four"):
        split_reference_family(points[:3])
    with pytest.raises(ValueError, match="axis values must be unique"):
        split_reference_family(
            [points[0], replace(points[1], axis_value=1), points[2], points[3]]
        )
    with pytest.raises(ValueError, match="point IDs must be unique"):
        split_reference_family(
            [points[0], replace(points[1], point_id="p1"), points[2], points[3]]
        )
    with pytest.raises(ValueError, match="one hardware_id and one kind"):
        split_reference_family(
            [points[0], points[1], points[2], _point("p4-h100", 4, hardware="h100-80gb")]
        )


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


def test_predeclared_thresholds_are_inclusive_but_not_a_tolerance_band() -> None:
    partition = split_reference_family(
        [_point("p1", 1), _point("p2", 2), _point("p3", 3), _point("p4", 4)]
    )
    at_limit = evaluate_reference_predictions(partition, {"p2": 1.1, "p4": 1.0})
    assert at_limit.mape == pytest.approx(0.05)
    assert at_limit.max_ape == pytest.approx(0.10)
    assert at_limit.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN

    over_ape = math.nextafter(math.nextafter(0.10, math.inf), math.inf)
    over_limit = evaluate_reference_predictions(
        partition, {"p2": 1.0 + over_ape, "p4": 1.0}
    )
    assert over_limit.max_ape is not None and over_limit.max_ape > C6_VALIDATION_MAX_APE_LIMIT
    assert over_limit.decision is AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION


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
        evaluate_reference_predictions(
            partition, {"p2": 1.0, "p4": 1.0, "extra": 1.0}
        )
    with pytest.raises(ValueError, match="finite and non-negative"):
        evaluate_reference_predictions(partition, {"p2": -1.0, "p4": 1.0})
