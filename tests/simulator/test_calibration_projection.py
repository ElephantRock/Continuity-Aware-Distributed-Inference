from dataclasses import replace
import csv
import io

import pytest

from simulator.calibration_projection import (
    C6_PROJECTION_AGGREGATION,
    VIDUR_SOURCE_SHA256,
    derive_vidur_reference_corpus,
    fit_affine_reference_family,
    build_pinned_vidur_projection,
)
from simulator.calibration_validation import (
    ReferenceKind,
    ReferencePartition,
    split_reference_family,
)


MLP_COMPONENTS = (
    "time_stats.attn_pre_proj.median",
    "time_stats.attn_post_proj.median",
    "time_stats.attn_rope.median",
    "time_stats.input_layernorm.median",
    "time_stats.post_attention_layernorm.median",
    "time_stats.mlp_up_proj.median",
    "time_stats.mlp_down_proj.median",
    "time_stats.mlp_act.median",
    "time_stats.add.median",
)


def _csv(fieldnames, rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _mlp_csv(*, duplicate_axis=None, exact_duplicate=False):
    fields = [
        *MLP_COMPONENTS,
        "n_head",
        "n_kv_head",
        "n_embd",
        "n_expanded_embd",
        "vocab_size",
        "use_gated_mlp",
        "num_tokens",
        "num_tensor_parallel_workers",
    ]
    rows = []
    for axis in (1, 64, 128, 192, 256):
        row = {name: "0" for name in MLP_COMPONENTS}
        row["time_stats.attn_pre_proj.median"] = str(1.0 + axis * 0.01)
        row.update(
            n_head="32",
            n_kv_head="32",
            n_embd="4096",
            n_expanded_embd="11008",
            vocab_size="32768",
            use_gated_mlp="True",
            num_tokens=str(axis),
            num_tensor_parallel_workers="1",
        )
        rows.append(row)
        if duplicate_axis == axis:
            duplicate = dict(row)
            duplicate["time_stats.attn_pre_proj.median"] = str(
                float(row["time_stats.attn_pre_proj.median"]) + 2.0
            )
            rows.append(duplicate)
        if exact_duplicate and axis == 192:
            rows.append(dict(row))
    return _csv(fields, rows)


def _attention_csv(*, duplicate_cold_axis=None, exact_duplicate=False):
    fields = [
        "time_stats.attn_prefill.median",
        "time_stats.attn_decode.median",
        "n_embd",
        "n_q_head",
        "n_kv_head",
        "block_size",
        "num_tensor_parallel_workers",
        "max_model_len",
        "batch_size",
        "prefill_chunk_size",
        "kv_cache_size",
        "is_prefill",
        "attention_backend",
    ]
    rows = []
    for axis in (64, 128, 192, 256):
        row = {
            "time_stats.attn_prefill.median": "0.5",
            "time_stats.attn_decode.median": "",
            "n_embd": "4096",
            "n_q_head": "32",
            "n_kv_head": "32",
            "block_size": "16",
            "num_tensor_parallel_workers": "1",
            "max_model_len": "4096",
            "batch_size": "1",
            "prefill_chunk_size": str(axis),
            "kv_cache_size": "0",
            "is_prefill": "True",
            "attention_backend": "AttentionBackend.FLASH_ATTENTION",
        }
        rows.append(row)
        if duplicate_cold_axis == axis:
            duplicate = dict(row)
            duplicate["time_stats.attn_prefill.median"] = "2.5"
            rows.append(duplicate)
        if exact_duplicate and axis == 192:
            rows.append(dict(row))
    for context in (32, 64, 96, 128):
        rows.append(
            {
                "time_stats.attn_prefill.median": "",
                "time_stats.attn_decode.median": str(0.5 + context * 0.001),
                "n_embd": "4096",
                "n_q_head": "32",
                "n_kv_head": "32",
                "block_size": "16",
                "num_tensor_parallel_workers": "1",
                "max_model_len": "4096",
                "batch_size": "1",
                "prefill_chunk_size": "0",
                "kv_cache_size": str(context),
                "is_prefill": "False",
                "attention_backend": "AttentionBackend.FLASH_ATTENTION",
            }
        )
    return _csv(fields, rows)


def _send_csv(*, duplicate_axis=None, exact_duplicate=False):
    fields = [
        "time_stats.send_recv.median",
        "rank",
        "num_workers",
        "size",
        "collective",
        "devices_per_node",
        "max_devices_per_node",
    ]
    rows = []
    for axis in (100, 200, 300, 400):
        row = {
            "time_stats.send_recv.median": str(1.0 + axis * 0.01),
            "rank": "0",
            "num_workers": "2",
            "size": str(axis),
            "collective": "send_recv",
            "devices_per_node": "2",
            "max_devices_per_node": "8",
        }
        rows.append(row)
        if duplicate_axis == axis:
            duplicate = dict(row)
            duplicate["time_stats.send_recv.median"] = str(
                float(row["time_stats.send_recv.median"]) + 2.0
            )
            rows.append(duplicate)
        if exact_duplicate and axis == 300:
            rows.append(dict(row))
    outside = dict(rows[0])
    outside["devices_per_node"] = "1"
    outside["time_stats.send_recv.median"] = "999"
    rows.append(outside)
    return _csv(fields, rows)


def _derive(**kwargs):
    return derive_vidur_reference_corpus(
        "a100-80gb",
        attention_csv=kwargs.get("attention_csv", _attention_csv()),
        mlp_csv=kwargs.get("mlp_csv", _mlp_csv()),
        send_recv_csv=kwargs.get("send_recv_csv", _send_csv()),
    )


def _family(references, kind):
    return [item for item in references if item.point.kind is kind]


def test_projection_derives_three_unique_axis_families_and_source_provenance():
    references = _derive()
    cold = _family(references, ReferenceKind.COLD_PREFILL)
    decode = _family(references, ReferenceKind.SINGLE_TOKEN_DECODE)
    transfer = _family(references, ReferenceKind.POINT_TO_POINT_TRANSFER)

    assert [item.point.axis_value for item in cold] == [64, 128, 192, 256]
    assert [item.point.axis_value for item in decode] == [32, 64, 96, 128]
    assert [item.point.axis_value for item in transfer] == [100, 200, 300, 400]

    assert all(
        item.mlp_replicates == 1 and item.attention_replicates == 1
        for item in cold
    )
    assert all(
        item.mlp_replicates == 1 and item.attention_replicates == 1
        for item in decode
    )
    assert all(item.transfer_replicates == 1 for item in transfer)

    assert len(cold[0].point.source_artifacts) == 3
    assert len(transfer[0].point.source_artifacts) == 1
    assert all(
        item.point.derivation_id.startswith("vidur-c6.3b-v1:")
        for item in references
    )
    assert C6_PROJECTION_AGGREGATION == (
        "equal-weight-mean-of-distinct-source-row-medians-v1"
    )


def test_compute_reference_composition_uses_mlp_token_axis_and_decode_token_one():
    references = _derive()
    cold = _family(references, ReferenceKind.COLD_PREFILL)
    decode = _family(references, ReferenceKind.SINGLE_TOKEN_DECODE)

    assert cold[0].point.observed_seconds == pytest.approx((1.64 + 0.5) * 32e-3)
    assert decode[0].point.observed_seconds == pytest.approx((1.01 + 0.532) * 32e-3)


def test_distinct_replicates_are_equal_weight_averaged_and_exact_duplicates_drop():
    references = _derive(
        attention_csv=_attention_csv(duplicate_cold_axis=128, exact_duplicate=True),
        mlp_csv=_mlp_csv(duplicate_axis=128, exact_duplicate=True),
        send_recv_csv=_send_csv(duplicate_axis=200, exact_duplicate=True),
    )
    cold128 = next(
        item
        for item in _family(references, ReferenceKind.COLD_PREFILL)
        if item.point.axis_value == 128
    )
    cold192 = next(
        item
        for item in _family(references, ReferenceKind.COLD_PREFILL)
        if item.point.axis_value == 192
    )
    transfer200 = next(
        item
        for item in _family(references, ReferenceKind.POINT_TO_POINT_TRANSFER)
        if item.point.axis_value == 200
    )
    transfer300 = next(
        item
        for item in _family(references, ReferenceKind.POINT_TO_POINT_TRANSFER)
        if item.point.axis_value == 300
    )

    assert cold128.mlp_replicates == 2
    assert cold128.attention_replicates == 2
    assert cold128.point.observed_seconds == pytest.approx((3.28 + 1.5) * 32e-3)
    assert cold192.mlp_replicates == 1
    assert cold192.attention_replicates == 1

    assert transfer200.transfer_replicates == 2
    assert transfer200.point.observed_seconds == pytest.approx(0.004)
    assert transfer300.transfer_replicates == 1


def test_projection_is_invariant_to_source_row_order():
    attention = _attention_csv(duplicate_cold_axis=128)
    mlp = _mlp_csv(duplicate_axis=128)
    send = _send_csv(duplicate_axis=200)

    first = derive_vidur_reference_corpus(
        "a100-80gb",
        attention_csv=attention,
        mlp_csv=mlp,
        send_recv_csv=send,
    )

    def reversed_csv(content):
        rows = list(csv.reader(io.StringIO(content)))
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(rows[0])
        writer.writerows(reversed(rows[1:]))
        return output.getvalue()

    second = derive_vidur_reference_corpus(
        "a100-80gb",
        attention_csv=reversed_csv(attention),
        mlp_csv=reversed_csv(mlp),
        send_recv_csv=reversed_csv(send),
    )
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]


def test_affine_fit_uses_only_canonical_fit_points():
    references = _derive()
    cold = _family(references, ReferenceKind.COLD_PREFILL)
    partition = split_reference_family([item.point for item in cold])
    fit = fit_affine_reference_family(partition)

    assert fit.fit_point_ids == (
        "c6.3b:a100-80gb:COLD_PREFILL:64",
        "c6.3b:a100-80gb:COLD_PREFILL:192",
    )
    assert fit.validation_point_ids == (
        "c6.3b:a100-80gb:COLD_PREFILL:128",
        "c6.3b:a100-80gb:COLD_PREFILL:256",
    )
    assert fit.intercept_seconds == pytest.approx(1.5 * 32e-3)
    assert fit.slope_seconds_per_axis_unit == pytest.approx(0.01 * 32e-3)

    changed_validation = tuple(
        replace(point, observed_seconds=999.0) for point in partition.validation
    )
    changed = ReferencePartition(
        hardware_id=partition.hardware_id,
        kind=partition.kind,
        fit=partition.fit,
        validation=changed_validation,
    )
    assert fit_affine_reference_family(changed) == fit


def test_transfer_fit_maps_slope_to_bandwidth():
    references = _derive()
    partition = split_reference_family(
        [
            item.point
            for item in _family(references, ReferenceKind.POINT_TO_POINT_TRANSFER)
        ]
    )
    fit = fit_affine_reference_family(partition)
    assert fit.intercept_seconds == pytest.approx(0.001)
    assert fit.slope_seconds_per_axis_unit == pytest.approx(1e-5)
    assert fit.c6_mapping["transfer_bandwidth_bytes_per_second"] == pytest.approx(
        100000.0
    )


def test_affine_fit_rejects_nonpositive_slope_or_negative_intercept():
    references = _derive()
    cold = _family(references, ReferenceKind.COLD_PREFILL)
    partition = split_reference_family([item.point for item in cold])

    flat_fit = tuple(replace(point, observed_seconds=1.0) for point in partition.fit)
    with pytest.raises(ValueError, match="slope must be positive"):
        fit_affine_reference_family(
            ReferencePartition(
                hardware_id=partition.hardware_id,
                kind=partition.kind,
                fit=flat_fit,
                validation=partition.validation,
            )
        )

    negative_intercept_fit = tuple(
        replace(point, observed_seconds=float(point.axis_value - 1))
        for point in partition.fit
    )
    with pytest.raises(ValueError, match="intercept must be non-negative"):
        fit_affine_reference_family(
            ReferencePartition(
                hardware_id=partition.hardware_id,
                kind=partition.kind,
                fit=negative_intercept_fit,
                validation=partition.validation,
            )
        )


def test_source_domain_fails_closed_on_missing_required_component():
    content = _mlp_csv()
    rows = list(csv.DictReader(io.StringIO(content)))
    fields = [field for field in rows[0] if field != "time_stats.add.median"]
    malformed = _csv(
        fields, [{k: v for k, v in row.items() if k in fields} for row in rows]
    )
    with pytest.raises(ValueError, match="time_stats.add.median"):
        _derive(mlp_csv=malformed)


def test_pinned_builder_rejects_source_bytes_before_parsing():
    with pytest.raises(ValueError, match="source SHA-256 snapshot mismatch"):
        build_pinned_vidur_projection(
            "a100-80gb",
            attention_bytes=b"not pinned attention",
            mlp_bytes=b"not pinned mlp",
            send_recv_bytes=b"not pinned send",
        )


def test_frozen_source_hashes_cover_both_hardware_profiles_and_three_files():
    assert set(VIDUR_SOURCE_SHA256) == {"a100-80gb", "h100-80gb"}
    assert all(
        set(snapshot) == {"attention", "mlp", "send_recv"}
        and all(len(value) == 64 for value in snapshot.values())
        for snapshot in VIDUR_SOURCE_SHA256.values()
    )
