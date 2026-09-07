import json

import pytest

from experiments.trace_workload import (
    NORMALIZED_TRACE_SCHEMA,
    NormalizedTraceDataset,
    NormalizedTraceRecord,
    TraceField,
    TraceFieldOrigin,
    TraceSourceManifest,
)


def _manifest(*, field_origins=None, source_sha256="a" * 64):
    if field_origins is None:
        field_origins = (
            (TraceField.ARRIVAL_TIME_S, TraceFieldOrigin.TRACE_DERIVED),
            (TraceField.INPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.OUTPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.PREFIX_GROUP_ID, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.PREFIX_TOKENS, TraceFieldOrigin.TRACE_DERIVED),
        )
    return TraceSourceManifest(
        source_id="fixture-source",
        source_name="Synthetic C5.1 contract fixture",
        source_uri="fixture://c5.1",
        source_version="v1",
        license_id="fixture-only",
        source_sha256=source_sha256,
        normalization_version="c5.1-test-v1",
        normalization_steps=(
            "convert source timestamps to seconds",
            "preserve missing optional fields as null",
        ),
        field_origins=field_origins,
    )


def _record(
    record_id,
    source_record_id,
    source_ordinal,
    arrival_time_s,
    *,
    input_tokens=100,
    output_tokens=20,
    prefix_group_id="prefix-a",
    prefix_tokens=50,
):
    return NormalizedTraceRecord(
        record_id=record_id,
        source_record_id=source_record_id,
        source_ordinal=source_ordinal,
        arrival_time_s=arrival_time_s,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        prefix_group_id=prefix_group_id,
        prefix_tokens=prefix_tokens,
    )


def _dataset():
    return NormalizedTraceDataset(
        manifest=_manifest(),
        records=(
            _record("r2", "src-2", 2, 0.5, input_tokens=80, prefix_tokens=20),
            _record("r0", "src-0", 0, 1.0),
            _record(
                "r1",
                "src-1",
                1,
                0.5,
                input_tokens=None,
                output_tokens=None,
                prefix_group_id=None,
                prefix_tokens=None,
            ),
        ),
    )


def test_complete_record_and_manifest_are_valid():
    record = _record("r0", "src-0", 0, 0)
    dataset = NormalizedTraceDataset(_manifest(), (record,))

    assert dataset.source_order == (record,)
    assert dataset.to_dict()["schema"] == NORMALIZED_TRACE_SCHEMA
    assert dataset.manifest.origin_by_field[TraceField.INPUT_TOKENS] is (
        TraceFieldOrigin.SOURCE_OBSERVED
    )


def test_optional_source_fields_preserve_explicit_missingness():
    record = _record(
        "r0",
        "src-0",
        0,
        0.0,
        input_tokens=None,
        output_tokens=None,
        prefix_group_id=None,
        prefix_tokens=None,
    )
    dataset = NormalizedTraceDataset(_manifest(), (record,))
    encoded = dataset.to_dict()["records"][0]

    assert encoded["input_tokens"] is None
    assert encoded["output_tokens"] is None
    assert encoded["prefix_group_id"] is None
    assert encoded["prefix_tokens"] is None


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"source_ordinal": -1}, ValueError),
        ({"source_ordinal": True}, TypeError),
        ({"arrival_time_s": -0.1}, ValueError),
        ({"arrival_time_s": float("inf")}, ValueError),
        ({"arrival_time_s": True}, TypeError),
        ({"input_tokens": -1}, ValueError),
        ({"input_tokens": True}, TypeError),
        ({"output_tokens": -1}, ValueError),
        ({"prefix_tokens": -1}, ValueError),
        ({"prefix_group_id": ""}, ValueError),
    ],
)
def test_record_numeric_type_and_range_validation(kwargs, error):
    values = {
        "record_id": "r0",
        "source_record_id": "src-0",
        "source_ordinal": 0,
        "arrival_time_s": 0.0,
        "input_tokens": 10,
        "output_tokens": 2,
        "prefix_group_id": "p",
        "prefix_tokens": 5,
    }
    values.update(kwargs)
    with pytest.raises(error):
        NormalizedTraceRecord(**values)


def test_prefix_tokens_require_input_and_cannot_exceed_it():
    with pytest.raises(ValueError, match="requires input_tokens"):
        _record("r0", "src-0", 0, 0.0, input_tokens=None, prefix_tokens=1)

    with pytest.raises(ValueError, match="cannot exceed"):
        _record("r0", "src-0", 0, 0.0, input_tokens=4, prefix_tokens=5)


def test_dataset_rejects_duplicate_normalized_source_ids_and_ordinals():
    base = _record("r0", "src-0", 0, 0.0)

    with pytest.raises(ValueError, match="duplicate normalized record_id"):
        NormalizedTraceDataset(
            _manifest(),
            (base, _record("r0", "src-1", 1, 1.0)),
        )

    with pytest.raises(ValueError, match="duplicate source_record_id"):
        NormalizedTraceDataset(
            _manifest(),
            (base, _record("r1", "src-0", 1, 1.0)),
        )

    with pytest.raises(ValueError, match="duplicate source_ordinal"):
        NormalizedTraceDataset(
            _manifest(),
            (base, _record("r1", "src-1", 0, 1.0)),
        )


def test_source_order_is_preserved_separately_from_chronological_replay_order():
    dataset = _dataset()

    assert tuple(record.record_id for record in dataset.source_order) == ("r0", "r1", "r2")
    assert tuple(record.record_id for record in dataset.chronological_order) == (
        "r1",
        "r2",
        "r0",
    )


def test_field_coverage_is_mechanical_and_does_not_impute_missing_values():
    coverage = _dataset().field_coverage()

    assert coverage[TraceField.ARRIVAL_TIME_S] == (3, 3)
    assert coverage[TraceField.INPUT_TOKENS] == (2, 3)
    assert coverage[TraceField.OUTPUT_TOKENS] == (2, 3)
    assert coverage[TraceField.PREFIX_GROUP_ID] == (2, 3)
    assert coverage[TraceField.PREFIX_TOKENS] == (2, 3)


def test_require_fields_accepts_complete_fields_and_rejects_partial_fields():
    dataset = _dataset()

    assert dataset.require_fields((TraceField.ARRIVAL_TIME_S,)) is dataset
    with pytest.raises(ValueError, match="input_tokens.*1/3"):
        dataset.require_fields((TraceField.INPUT_TOKENS,))
    with pytest.raises(TypeError):
        dataset.require_fields(("input_tokens",))  # type: ignore[arg-type]


def test_present_source_field_requires_manifest_provenance_declaration():
    manifest = _manifest(
        field_origins=((TraceField.ARRIVAL_TIME_S, TraceFieldOrigin.TRACE_DERIVED),)
    )
    with pytest.raises(ValueError, match="input_tokens.*provenance"):
        NormalizedTraceDataset(manifest, (_record("r0", "src-0", 0, 0.0),))


def test_manifest_requires_arrival_origin_and_canonical_lowercase_sha256():
    with pytest.raises(ValueError, match="arrival_time_s origin"):
        _manifest(
            field_origins=(
                (TraceField.INPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
            )
        )

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        _manifest(source_sha256="A" * 64)
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        _manifest(source_sha256="abc")


def test_manifest_rejects_duplicate_field_origin_declarations():
    with pytest.raises(ValueError, match="duplicate field origin"):
        _manifest(
            field_origins=(
                (TraceField.ARRIVAL_TIME_S, TraceFieldOrigin.SOURCE_OBSERVED),
                (TraceField.ARRIVAL_TIME_S, TraceFieldOrigin.TRACE_DERIVED),
            )
        )


def test_canonical_json_round_trip_and_member_order_independence():
    dataset = _dataset()
    noncanonical_member_order = json.dumps(dataset.to_dict(), sort_keys=False)
    restored = NormalizedTraceDataset.from_json(noncanonical_member_order)

    assert restored == dataset
    assert restored.to_json() == dataset.to_json()
    assert restored.fingerprint == dataset.fingerprint


def test_fingerprint_is_stable_and_sensitive_to_semantic_record_mutation():
    dataset = _dataset()
    same = NormalizedTraceDataset.from_json(dataset.to_json())
    mutated_records = tuple(
        _record(
            record.record_id,
            record.source_record_id,
            record.source_ordinal,
            record.arrival_time_s,
            input_tokens=record.input_tokens,
            output_tokens=(
                21 if record.record_id == "r0" else record.output_tokens
            ),
            prefix_group_id=record.prefix_group_id,
            prefix_tokens=record.prefix_tokens,
        )
        for record in dataset.records
    )
    mutated = NormalizedTraceDataset(dataset.manifest, mutated_records)

    assert same.fingerprint == dataset.fingerprint
    assert mutated.fingerprint != dataset.fingerprint


def test_deserialize_rejects_unknown_or_missing_dataset_and_record_members():
    payload = _dataset().to_dict()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="unexpected"):
        NormalizedTraceDataset.from_json(json.dumps(payload))

    payload = _dataset().to_dict()
    del payload["manifest"]["license_id"]
    with pytest.raises(ValueError, match="missing"):
        NormalizedTraceDataset.from_json(json.dumps(payload))

    payload = _dataset().to_dict()
    payload["records"][0]["session_id"] = "synthetic-session"
    with pytest.raises(ValueError, match="session_id"):
        NormalizedTraceDataset.from_json(json.dumps(payload))


def test_deserialize_rejects_nonfinite_json_and_duplicate_members():
    value = _dataset().to_json().replace('"arrival_time_s":1.0', '"arrival_time_s":NaN', 1)
    with pytest.raises(ValueError, match="non-finite JSON constant"):
        NormalizedTraceDataset.from_json(value)

    duplicate_schema = _dataset().to_json().replace(
        '{"manifest":',
        '{"schema":"duplicate","manifest":',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON member: schema"):
        NormalizedTraceDataset.from_json(duplicate_schema)


def test_deserialize_rejects_synthetic_field_origin_inside_source_layer():
    payload = _dataset().to_dict()
    payload["manifest"]["field_origins"]["input_tokens"] = "SYNTHETIC_AUGMENTATION"

    with pytest.raises(ValueError, match="invalid trace field origin"):
        NormalizedTraceDataset.from_json(json.dumps(payload))


def test_empty_dataset_is_rejected():
    with pytest.raises(ValueError, match="non-empty tuple"):
        NormalizedTraceDataset(_manifest(), ())
