from __future__ import annotations

import hashlib

import pytest

from experiments.mooncake_trace import (
    MOONCAKE_EXPECTED_REQUESTS,
    MOONCAKE_NORMALIZATION_VERSION,
    MOONCAKE_PREFIX_BLOCK_TOKENS,
    MOONCAKE_SOURCE_REVISION,
    MOONCAKE_SOURCE_SHA256,
    MOONCAKE_SOURCE_URI,
    MooncakeSourceRow,
    load_pinned_mooncake_trace,
    mooncake_manifest,
    normalize_mooncake_rows,
    parse_mooncake_jsonl,
)
from experiments.trace_workload import (
    NormalizedTraceDataset,
    TraceField,
    TraceFieldOrigin,
)


FIXTURE_SHA = "a" * 64


def _row(
    timestamp: int,
    input_length: int,
    output_length: int,
    hash_ids: list[int],
) -> MooncakeSourceRow:
    return MooncakeSourceRow.from_mapping(
        {
            "timestamp": timestamp,
            "input_length": input_length,
            "output_length": output_length,
            "hash_ids": hash_ids,
        }
    )


def _dataset(*rows: MooncakeSourceRow) -> NormalizedTraceDataset:
    return normalize_mooncake_rows(rows, source_sha256=FIXTURE_SHA)


def test_manifest_freezes_source_and_field_provenance() -> None:
    manifest = mooncake_manifest()
    assert manifest.source_version == MOONCAKE_SOURCE_REVISION
    assert manifest.source_uri == MOONCAKE_SOURCE_URI
    assert manifest.source_sha256 == MOONCAKE_SOURCE_SHA256
    assert manifest.normalization_version == MOONCAKE_NORMALIZATION_VERSION
    assert manifest.license_id == "Apache-2.0"
    assert manifest.origin_by_field == {
        TraceField.ARRIVAL_TIME_S: TraceFieldOrigin.SOURCE_OBSERVED,
        TraceField.INPUT_TOKENS: TraceFieldOrigin.SOURCE_OBSERVED,
        TraceField.OUTPUT_TOKENS: TraceFieldOrigin.SOURCE_OBSERVED,
        TraceField.PREFIX_GROUP_ID: TraceFieldOrigin.TRACE_DERIVED,
        TraceField.PREFIX_TOKENS: TraceFieldOrigin.TRACE_DERIVED,
    }


def test_parse_valid_jsonl_and_millisecond_normalization() -> None:
    raw = (
        b'{"timestamp":3000,"input_length":1024,"output_length":7,"hash_ids":[10,11]}\n'
        b'{"timestamp":5999,"input_length":512,"output_length":3,"hash_ids":[10]}\n'
    )
    rows = parse_mooncake_jsonl(raw)
    dataset = normalize_mooncake_rows(rows, source_sha256=hashlib.sha256(raw).hexdigest())
    assert [record.arrival_time_s for record in dataset.source_order] == [3.0, 5.999]
    assert [record.input_tokens for record in dataset.source_order] == [1024, 512]
    assert [record.output_tokens for record in dataset.source_order] == [7, 3]


def test_stable_source_and_normalized_identities() -> None:
    dataset = _dataset(
        _row(0, 512, 1, [5]),
        _row(0, 512, 2, [6]),
    )
    assert [record.record_id for record in dataset.source_order] == [
        "mooncake-fast25:00000",
        "mooncake-fast25:00001",
    ]
    assert [record.source_record_id for record in dataset.source_order] == [
        "jsonl-line:1",
        "jsonl-line:2",
    ]
    assert [record.source_ordinal for record in dataset.source_order] == [0, 1]


def test_no_prior_reuse_is_zero_tokens_and_null_group() -> None:
    record = _dataset(_row(0, 1024, 1, [10, 11])).source_order[0]
    assert record.prefix_tokens == 0
    assert record.prefix_group_id is None


def test_longest_prior_prefix_is_sequence_exact() -> None:
    dataset = _dataset(
        _row(0, 2048, 1, [1, 2, 3, 4]),
        _row(1, 2048, 1, [1, 2, 9, 10]),
        _row(2, 2048, 1, [1, 2, 3, 8]),
    )
    record = dataset.source_order[2]
    assert record.prefix_tokens == 3 * MOONCAKE_PREFIX_BLOCK_TOKENS
    assert record.prefix_group_id == "mooncake-hash:3"


def test_prefix_match_does_not_mix_positions_from_different_prior_requests() -> None:
    dataset = _dataset(
        _row(0, 2048, 1, [1, 2]),
        _row(1, 2048, 1, [1, 3, 4]),
        _row(2, 2048, 1, [1, 2, 4]),
    )
    record = dataset.source_order[2]
    assert record.prefix_tokens == 2 * MOONCAKE_PREFIX_BLOCK_TOKENS
    assert record.prefix_group_id == "mooncake-hash:2"


def test_prefix_tokens_are_capped_by_input_length() -> None:
    dataset = _dataset(
        _row(0, 2048, 1, [7, 8]),
        _row(1, 600, 1, [7, 8]),
    )
    assert dataset.source_order[1].prefix_tokens == 600


def test_empty_hash_list_is_valid_but_never_reusable() -> None:
    dataset = _dataset(
        _row(0, 100, 1, []),
        _row(1, 100, 1, []),
    )
    assert [record.prefix_tokens for record in dataset.source_order] == [0, 0]
    assert [record.prefix_group_id for record in dataset.source_order] == [None, None]


def test_same_timestamp_preserves_source_order_and_chronological_tie_break() -> None:
    dataset = _dataset(
        _row(3000, 100, 1, [1]),
        _row(3000, 200, 1, [2]),
        _row(0, 300, 1, [3]),
    )
    assert [r.source_ordinal for r in dataset.source_order] == [0, 1, 2]
    assert [r.source_ordinal for r in dataset.chronological_order] == [2, 0, 1]


@pytest.mark.parametrize(
    "bad",
    [
        {"timestamp": -1, "input_length": 1, "output_length": 1, "hash_ids": []},
        {"timestamp": 1.0, "input_length": 1, "output_length": 1, "hash_ids": []},
        {"timestamp": True, "input_length": 1, "output_length": 1, "hash_ids": []},
        {"timestamp": 0, "input_length": -1, "output_length": 1, "hash_ids": []},
        {"timestamp": 0, "input_length": 1, "output_length": -1, "hash_ids": []},
        {"timestamp": 0, "input_length": 1, "output_length": 1, "hash_ids": [False]},
        {"timestamp": 0, "input_length": 1, "output_length": 1, "hash_ids": [-1]},
    ],
)
def test_noninteger_or_negative_source_values_reject(bad: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        MooncakeSourceRow.from_mapping(bad)


def test_hash_ids_must_be_json_array() -> None:
    with pytest.raises(TypeError, match="hash_ids"):
        MooncakeSourceRow.from_mapping(
            {
                "timestamp": 0,
                "input_length": 1,
                "output_length": 1,
                "hash_ids": (1, 2),
            }
        )


def test_missing_or_unknown_upstream_members_reject() -> None:
    with pytest.raises(ValueError, match="fields must exactly match"):
        MooncakeSourceRow.from_mapping(
            {"timestamp": 0, "input_length": 1, "output_length": 1}
        )
    with pytest.raises(ValueError, match="fields must exactly match"):
        MooncakeSourceRow.from_mapping(
            {
                "timestamp": 0,
                "input_length": 1,
                "output_length": 1,
                "hash_ids": [],
                "session_id": "synthetic",
            }
        )


def test_duplicate_json_members_reject() -> None:
    raw = (
        b'{"timestamp":0,"timestamp":1,"input_length":1,'
        b'"output_length":1,"hash_ids":[]}\n'
    )
    with pytest.raises(ValueError, match="invalid Mooncake JSONL"):
        parse_mooncake_jsonl(raw)


def test_nonfinite_json_rejects() -> None:
    raw = b'{"timestamp":NaN,"input_length":1,"output_length":1,"hash_ids":[]}\n'
    with pytest.raises(ValueError, match="invalid Mooncake JSONL"):
        parse_mooncake_jsonl(raw)


def test_blank_line_empty_artifact_and_invalid_utf8_reject() -> None:
    with pytest.raises(ValueError, match="at least one"):
        parse_mooncake_jsonl(b"")
    with pytest.raises(ValueError, match="blank JSONL"):
        parse_mooncake_jsonl(
            b'{"timestamp":0,"input_length":1,"output_length":1,"hash_ids":[]}\n\n'
            b'{"timestamp":1,"input_length":1,"output_length":1,"hash_ids":[]}\n'
        )
    with pytest.raises(ValueError, match="UTF-8"):
        parse_mooncake_jsonl(b"\xff")


def test_pinned_loader_rejects_checksum_before_parse() -> None:
    malformed = b"{not-json"
    assert hashlib.sha256(malformed).hexdigest() != MOONCAKE_SOURCE_SHA256
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_pinned_mooncake_trace(malformed)


def test_c5_1_round_trip_preserves_adapter_output() -> None:
    dataset = _dataset(
        _row(0, 1024, 2, [1, 2]),
        _row(1000, 900, 3, [1, 2]),
    )
    restored = NormalizedTraceDataset.from_json(dataset.to_json())
    assert restored == dataset
    assert restored.fingerprint == dataset.fingerprint
    assert restored.field_coverage()[TraceField.INPUT_TOKENS] == (2, 2)
    assert restored.field_coverage()[TraceField.PREFIX_TOKENS] == (2, 2)
    assert restored.field_coverage()[TraceField.PREFIX_GROUP_ID] == (1, 2)


def test_normalize_requires_rows_and_row_type() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_mooncake_rows((), source_sha256=FIXTURE_SHA)
    with pytest.raises(TypeError, match="MooncakeSourceRow"):
        normalize_mooncake_rows((object(),), source_sha256=FIXTURE_SHA)


def test_source_contract_constants_are_frozen() -> None:
    assert MOONCAKE_EXPECTED_REQUESTS == 12_031
    assert MOONCAKE_PREFIX_BLOCK_TOKENS == 512
    assert len(MOONCAKE_SOURCE_SHA256) == 64
    assert all(ch in "0123456789abcdef" for ch in MOONCAKE_SOURCE_SHA256)
