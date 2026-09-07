from __future__ import annotations

import json
import random

import pytest

from experiments.continuity_augmentation import (
    ContinuityAugmentationConfig,
    augment_trace,
)
from experiments.trace_workload import (
    NormalizedTraceDataset,
    NormalizedTraceRecord,
    TraceField,
    TraceFieldOrigin,
    TraceSourceManifest,
)
from experiments.workload_modes import (
    FULLY_SYNTHETIC_WORKLOAD_SCHEMA,
    FullySyntheticWorkloadConfig,
    FullySyntheticWorkloadDataset,
    FullySyntheticWorkloadRecord,
    SyntheticWorkloadProvenance,
    WorkloadEnvelope,
    WorkloadMode,
    generate_fully_synthetic_workload,
)


def _source(*, input_tokens: int = 100) -> NormalizedTraceDataset:
    manifest = TraceSourceManifest(
        source_id="fixture-source",
        source_name="fixture source",
        source_uri="https://example.invalid/fixture",
        source_version="v1",
        license_id="test-only",
        source_sha256="a" * 64,
        normalization_version="fixture-v1",
        normalization_steps=("fixture construction",),
        field_origins=(
            (TraceField.ARRIVAL_TIME_S, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.INPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.OUTPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.PREFIX_GROUP_ID, TraceFieldOrigin.TRACE_DERIVED),
            (TraceField.PREFIX_TOKENS, TraceFieldOrigin.TRACE_DERIVED),
        ),
    )
    return NormalizedTraceDataset(
        manifest=manifest,
        records=(
            NormalizedTraceRecord(
                record_id="r0",
                source_record_id="line:1",
                source_ordinal=0,
                arrival_time_s=0.0,
                input_tokens=input_tokens,
                output_tokens=5,
                prefix_group_id=None,
                prefix_tokens=0,
            ),
            NormalizedTraceRecord(
                record_id="r1",
                source_record_id="line:2",
                source_ordinal=1,
                arrival_time_s=1.0,
                input_tokens=200,
                output_tokens=7,
                prefix_group_id="p1",
                prefix_tokens=50,
            ),
        ),
    )


def _augmentation(source: NormalizedTraceDataset):
    config = ContinuityAugmentationConfig(
        seed=3,
        session_length_records=2,
        tool_wait_probability=0.0,
        tool_wait_seconds=(),
        branch_probability=0.0,
        branch_lookback_records=2,
        fault_probability=0.0,
        fault_classes=(),
    )
    return augment_trace(source, config)


def _synthetic_config(**updates: object) -> FullySyntheticWorkloadConfig:
    values: dict[str, object] = {
        "seed": 11,
        "request_count": 4,
        "interarrival_s_choices": (0.5, 1.25),
        "input_tokens_choices": (100, 400),
        "output_tokens_choices": (5, 20),
        "prefix_fraction_choices": (0.0, 0.5),
        "prefix_group_count": 2,
    }
    values.update(updates)
    return FullySyntheticWorkloadConfig(**values)


def test_frozen_fully_synthetic_replay_vector() -> None:
    dataset = generate_fully_synthetic_workload(_synthetic_config())
    assert [record.to_dict() for record in dataset.records] == [
        {
            "record_id": "synthetic:000000",
            "ordinal": 0,
            "arrival_time_s": 0.0,
            "input_tokens": 100,
            "output_tokens": 5,
            "prefix_group_id": "synthetic-prefix:0000",
            "prefix_tokens": 50,
        },
        {
            "record_id": "synthetic:000001",
            "ordinal": 1,
            "arrival_time_s": 0.5,
            "input_tokens": 100,
            "output_tokens": 20,
            "prefix_group_id": None,
            "prefix_tokens": 0,
        },
        {
            "record_id": "synthetic:000002",
            "ordinal": 2,
            "arrival_time_s": 1.75,
            "input_tokens": 400,
            "output_tokens": 5,
            "prefix_group_id": "synthetic-prefix:0000",
            "prefix_tokens": 200,
        },
        {
            "record_id": "synthetic:000003",
            "ordinal": 3,
            "arrival_time_s": 2.25,
            "input_tokens": 400,
            "output_tokens": 5,
            "prefix_group_id": "synthetic-prefix:0000",
            "prefix_tokens": 200,
        },
    ]
    assert (
        dataset.fingerprint
        == "d78dbb41571aec557c2a6fc581e454a0cd50011b8f3582bcaa9eaa1c495c4051"
    )
    assert dataset.provenance is SyntheticWorkloadProvenance.SYNTHETIC
    dataset.assert_reproducible()


def test_synthetic_generation_is_deterministic_and_process_rng_independent() -> None:
    config = _synthetic_config()
    random.seed(1)
    first = generate_fully_synthetic_workload(config)
    for _ in range(100):
        random.random()
    random.seed(999)
    second = generate_fully_synthetic_workload(config)
    assert first.to_json() == second.to_json()
    assert first.fingerprint == second.fingerprint


def test_semantic_config_changes_change_fingerprint() -> None:
    base = generate_fully_synthetic_workload(_synthetic_config())
    changed_seed = generate_fully_synthetic_workload(_synthetic_config(seed=12))
    changed_choice = generate_fully_synthetic_workload(
        _synthetic_config(output_tokens_choices=(5, 21))
    )
    assert base.fingerprint != changed_seed.fingerprint
    assert base.fingerprint != changed_choice.fingerprint


def test_config_json_dictionary_order_is_not_semantic() -> None:
    canonical = _synthetic_config()
    mapping = canonical.to_dict()
    reversed_mapping = dict(reversed(list(mapping.items())))
    restored = FullySyntheticWorkloadConfig.from_dict(reversed_mapping)
    assert restored == canonical
    assert (
        generate_fully_synthetic_workload(restored).fingerprint
        == generate_fully_synthetic_workload(canonical).fingerprint
    )


def test_generator_invariants_and_prefix_controls() -> None:
    zero = generate_fully_synthetic_workload(
        _synthetic_config(prefix_fraction_choices=(0.0,), prefix_group_count=0)
    )
    assert all(record.prefix_tokens == 0 for record in zero.records)
    assert all(record.prefix_group_id is None for record in zero.records)

    positive = generate_fully_synthetic_workload(
        _synthetic_config(prefix_fraction_choices=(0.5,), prefix_group_count=3)
    )
    assert positive.records[0].arrival_time_s == 0.0
    assert all(
        later.arrival_time_s > earlier.arrival_time_s
        for earlier, later in zip(positive.records, positive.records[1:])
    )
    assert all(
        0 < record.prefix_tokens <= record.input_tokens for record in positive.records
    )
    assert all(record.prefix_group_id is not None for record in positive.records)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed", -1),
        ("seed", True),
        ("request_count", 0),
        ("interarrival_s_choices", ()),
        ("interarrival_s_choices", (0.0,)),
        ("interarrival_s_choices", (1.0, 1.0)),
        ("input_tokens_choices", ()),
        ("input_tokens_choices", (0,)),
        ("output_tokens_choices", ()),
        ("output_tokens_choices", (-1,)),
        ("prefix_fraction_choices", ()),
        ("prefix_fraction_choices", (-0.1,)),
        ("prefix_fraction_choices", (1.1,)),
        ("prefix_group_count", -1),
    ],
)
def test_synthetic_config_validation(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _synthetic_config(**{field: value})


def test_positive_prefix_requires_group_count() -> None:
    with pytest.raises(ValueError, match="prefix_group_count"):
        _synthetic_config(prefix_fraction_choices=(0.5,), prefix_group_count=0)


def test_synthetic_round_trip_and_reproducibility() -> None:
    dataset = generate_fully_synthetic_workload(_synthetic_config())
    restored = FullySyntheticWorkloadDataset.from_json(dataset.to_json())
    assert restored == dataset
    assert restored.fingerprint == dataset.fingerprint
    restored.assert_reproducible()


def test_structurally_valid_semantic_tamper_fails_reproduction() -> None:
    dataset = generate_fully_synthetic_workload(_synthetic_config())
    records = list(dataset.records)
    original = records[0]
    alternate_input = 400 if original.input_tokens == 100 else 100
    alternate_prefix = 200 if original.prefix_tokens > 0 else 0
    alternate_group = "synthetic-prefix:0000" if alternate_prefix > 0 else None
    records[0] = FullySyntheticWorkloadRecord(
        record_id=original.record_id,
        ordinal=original.ordinal,
        arrival_time_s=original.arrival_time_s,
        input_tokens=alternate_input,
        output_tokens=original.output_tokens,
        prefix_group_id=alternate_group,
        prefix_tokens=alternate_prefix,
    )
    tampered = FullySyntheticWorkloadDataset(
        config=dataset.config,
        records=tuple(records),
    )
    with pytest.raises(ValueError, match="does not reproduce"):
        tampered.assert_reproducible()


def test_fully_synthetic_json_rejects_source_derived_schema() -> None:
    source = _source()
    with pytest.raises(ValueError):
        FullySyntheticWorkloadDataset.from_json(source.to_json())
    synthetic = generate_fully_synthetic_workload(_synthetic_config())
    with pytest.raises(ValueError):
        NormalizedTraceDataset.from_json(synthetic.to_json())
    assert synthetic.to_dict()["schema"] == FULLY_SYNTHETIC_WORKLOAD_SCHEMA
    assert "manifest" not in synthetic.to_dict()


def test_workload_envelope_source_derived_preserves_source() -> None:
    source = _source()
    before = source.to_json()
    envelope = WorkloadEnvelope.source_derived(source)
    assert envelope.mode is WorkloadMode.SOURCE_DERIVED
    assert envelope.source_dataset_fingerprint == source.fingerprint
    assert envelope.augmentation_fingerprint is None
    assert envelope.synthetic_dataset_fingerprint is None
    envelope.assert_matches_source(source)
    assert source.to_json() == before


def test_workload_envelope_trace_augmented_preserves_both_artifacts() -> None:
    source = _source()
    augmentation = _augmentation(source)
    source_before = source.to_json()
    augmentation_before = augmentation.to_json()
    envelope = WorkloadEnvelope.trace_augmented(source, augmentation)
    assert envelope.mode is WorkloadMode.TRACE_AUGMENTED
    assert envelope.source_dataset_fingerprint == source.fingerprint
    assert envelope.augmentation_fingerprint == augmentation.fingerprint
    assert envelope.augmentation_source_dataset_fingerprint == source.fingerprint
    envelope.assert_matches_trace_augmented(source, augmentation)
    assert source.to_json() == source_before
    assert augmentation.to_json() == augmentation_before


def test_trace_augmented_rejects_mismatched_source() -> None:
    source = _source()
    augmentation = _augmentation(source)
    different = _source(input_tokens=101)
    with pytest.raises(ValueError, match="source dataset fingerprint mismatch"):
        WorkloadEnvelope.trace_augmented(different, augmentation)


def test_workload_envelope_fully_synthetic() -> None:
    synthetic = generate_fully_synthetic_workload(_synthetic_config())
    envelope = WorkloadEnvelope.fully_synthetic(synthetic)
    assert envelope.mode is WorkloadMode.FULLY_SYNTHETIC
    assert envelope.synthetic_dataset_fingerprint == synthetic.fingerprint
    assert envelope.source_dataset_fingerprint is None
    assert envelope.augmentation_fingerprint is None
    envelope.assert_matches_fully_synthetic(synthetic)


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "mode": WorkloadMode.SOURCE_DERIVED,
            "source_dataset_fingerprint": None,
            "augmentation_fingerprint": None,
            "augmentation_source_dataset_fingerprint": None,
            "synthetic_dataset_fingerprint": None,
        },
        {
            "mode": WorkloadMode.SOURCE_DERIVED,
            "source_dataset_fingerprint": "a" * 64,
            "augmentation_fingerprint": "b" * 64,
            "augmentation_source_dataset_fingerprint": "a" * 64,
            "synthetic_dataset_fingerprint": None,
        },
        {
            "mode": WorkloadMode.TRACE_AUGMENTED,
            "source_dataset_fingerprint": "a" * 64,
            "augmentation_fingerprint": None,
            "augmentation_source_dataset_fingerprint": "a" * 64,
            "synthetic_dataset_fingerprint": None,
        },
        {
            "mode": WorkloadMode.TRACE_AUGMENTED,
            "source_dataset_fingerprint": "a" * 64,
            "augmentation_fingerprint": "b" * 64,
            "augmentation_source_dataset_fingerprint": "c" * 64,
            "synthetic_dataset_fingerprint": None,
        },
        {
            "mode": WorkloadMode.TRACE_AUGMENTED,
            "source_dataset_fingerprint": "a" * 64,
            "augmentation_fingerprint": "b" * 64,
            "augmentation_source_dataset_fingerprint": "a" * 64,
            "synthetic_dataset_fingerprint": "d" * 64,
        },
        {
            "mode": WorkloadMode.FULLY_SYNTHETIC,
            "source_dataset_fingerprint": "a" * 64,
            "augmentation_fingerprint": None,
            "augmentation_source_dataset_fingerprint": None,
            "synthetic_dataset_fingerprint": "d" * 64,
        },
    ],
)
def test_illegal_cross_mode_combinations_reject(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        WorkloadEnvelope(**kwargs)


def test_envelope_mode_is_explicit_and_round_trips() -> None:
    source = _source()
    augmentation = _augmentation(source)
    envelope = WorkloadEnvelope.trace_augmented(source, augmentation)
    restored = WorkloadEnvelope.from_json(envelope.to_json())
    assert restored == envelope
    assert restored.fingerprint == envelope.fingerprint
    assert json.loads(restored.to_json())["mode"] == "TRACE_AUGMENTED"


def test_envelope_artifact_match_checks_fail_closed() -> None:
    source = _source()
    source_envelope = WorkloadEnvelope.source_derived(source)
    with pytest.raises(ValueError, match="mode"):
        source_envelope.assert_matches_fully_synthetic(
            generate_fully_synthetic_workload(_synthetic_config())
        )

    synthetic = generate_fully_synthetic_workload(_synthetic_config())
    synthetic_envelope = WorkloadEnvelope.fully_synthetic(synthetic)
    changed = generate_fully_synthetic_workload(_synthetic_config(seed=12))
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        synthetic_envelope.assert_matches_fully_synthetic(changed)


def test_synthetic_json_rejects_unknown_missing_duplicate_and_nonfinite() -> None:
    dataset = generate_fully_synthetic_workload(_synthetic_config())
    decoded = dataset.to_dict()

    unknown = dict(decoded)
    unknown["unexpected"] = 1
    with pytest.raises(ValueError, match="fields must exactly match"):
        FullySyntheticWorkloadDataset.from_json(json.dumps(unknown))

    missing = dict(decoded)
    missing.pop("provenance")
    with pytest.raises(ValueError, match="fields must exactly match"):
        FullySyntheticWorkloadDataset.from_json(json.dumps(missing))

    duplicate = dataset.to_json().replace(
        '"schema":',
        '"schema":"duplicate","schema":',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON member"):
        FullySyntheticWorkloadDataset.from_json(duplicate)

    nonfinite = dataset.to_json().replace('"request_count":4', '"request_count":NaN')
    with pytest.raises(ValueError, match="non-finite"):
        FullySyntheticWorkloadDataset.from_json(nonfinite)


def test_envelope_json_rejects_invalid_mode_schema_and_duplicate_member() -> None:
    envelope = WorkloadEnvelope.source_derived(_source())
    decoded = envelope.to_dict()

    bad_mode = dict(decoded)
    bad_mode["mode"] = "AUTO"
    with pytest.raises(ValueError, match="WorkloadMode"):
        WorkloadEnvelope.from_json(json.dumps(bad_mode))

    bad_schema = dict(decoded)
    bad_schema["schema"] = "other"
    with pytest.raises(ValueError, match="schema"):
        WorkloadEnvelope.from_json(json.dumps(bad_schema))

    duplicate = envelope.to_json().replace(
        '"mode":',
        '"mode":"SOURCE_DERIVED","mode":',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON member"):
        WorkloadEnvelope.from_json(duplicate)
