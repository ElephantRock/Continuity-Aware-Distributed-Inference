from __future__ import annotations

import json
import math

import pytest

from experiments.continuity_augmentation import (
    AugmentationProvenance,
    ContinuityAnnotation,
    ContinuityAugmentationConfig,
    ContinuityAugmentationDataset,
    augment_trace,
)
from experiments.trace_workload import (
    NormalizedTraceDataset,
    NormalizedTraceRecord,
    TraceField,
    TraceFieldOrigin,
    TraceSourceManifest,
)
from simulator.faults import FaultClass


EXPECTED_VECTOR_FINGERPRINT = (
    "384dc1f910a51c598087bcb42fdbbfcd55058e5b2428da6b8d5ef5bb730544a7"
)


def _source(*, count: int = 6, input_delta: int = 0) -> NormalizedTraceDataset:
    manifest = TraceSourceManifest(
        source_id="fixture",
        source_name="fixture",
        source_uri="https://example.invalid/fixture",
        source_version="v1",
        license_id="TEST",
        source_sha256="a" * 64,
        normalization_version="fixture-v1",
        normalization_steps=("fixture",),
        field_origins=(
            (TraceField.ARRIVAL_TIME_S, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.INPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.OUTPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
        ),
    )
    records = tuple(
        NormalizedTraceRecord(
            record_id=f"r{index}",
            source_record_id=f"src{index}",
            source_ordinal=index,
            arrival_time_s=float(index),
            input_tokens=100 + index + (input_delta if index == 0 else 0),
            output_tokens=10 + index,
        )
        for index in range(count)
    )
    return NormalizedTraceDataset(manifest=manifest, records=records)


def _config(
    *,
    seed: int = 7,
    session_length_records: int = 4,
    tool_wait_probability: float = 0.5,
    tool_wait_seconds: tuple[float, ...] = (1.0, 5.0),
    branch_probability: float = 0.5,
    branch_lookback_records: int = 2,
    fault_probability: float = 0.5,
    fault_classes: tuple[FaultClass, ...] = (
        FaultClass.DELIVERY_DELAY,
        FaultClass.WORKER_FAILURE,
    ),
) -> ContinuityAugmentationConfig:
    return ContinuityAugmentationConfig(
        seed=seed,
        session_length_records=session_length_records,
        tool_wait_probability=tool_wait_probability,
        tool_wait_seconds=tool_wait_seconds,
        branch_probability=branch_probability,
        branch_lookback_records=branch_lookback_records,
        fault_probability=fault_probability,
        fault_classes=fault_classes,
    )


def _zero_config(*, seed: int = 0, session_length_records: int = 4) -> ContinuityAugmentationConfig:
    return _config(
        seed=seed,
        session_length_records=session_length_records,
        tool_wait_probability=0.0,
        tool_wait_seconds=(),
        branch_probability=0.0,
        fault_probability=0.0,
        fault_classes=(),
    )


def test_source_is_immutable_and_augmentation_is_separate_synthetic_layer() -> None:
    source = _source()
    before = source.to_json()
    augmentation = augment_trace(source, _config())
    assert source.to_json() == before
    assert augmentation.provenance is AugmentationProvenance.SYNTHETIC
    assert augmentation.source_dataset_fingerprint == source.fingerprint
    assert tuple(a.record_id for a in augmentation.annotations) == tuple(
        r.record_id for r in source.source_order
    )
    assert set(augmentation.annotations[0].to_dict()) == {
        "record_id",
        "session_id",
        "continuation_id",
        "parent_continuation_id",
        "branch_group_id",
        "tool_wait_before_s",
        "fault_class",
    }


def test_fixed_vector_is_cross_version_stable() -> None:
    augmentation = augment_trace(_source(), _config())
    assert augmentation.source_dataset_fingerprint == (
        "470411ee63b37fa85be2bede12f6d5cc515bca875ce8ca4d3cb2aed1cecb0650"
    )
    assert augmentation.fingerprint == EXPECTED_VECTOR_FINGERPRINT
    assert [annotation.to_dict() for annotation in augmentation.annotations] == [
        {
            "record_id": "r0",
            "session_id": "syn-session:000000",
            "continuation_id": "syn-session:000000:c:0000",
            "parent_continuation_id": None,
            "branch_group_id": None,
            "tool_wait_before_s": None,
            "fault_class": "WORKER_FAILURE",
        },
        {
            "record_id": "r1",
            "session_id": "syn-session:000000",
            "continuation_id": "syn-session:000000:c:0001",
            "parent_continuation_id": "syn-session:000000:c:0000",
            "branch_group_id": None,
            "tool_wait_before_s": None,
            "fault_class": None,
        },
        {
            "record_id": "r2",
            "session_id": "syn-session:000000",
            "continuation_id": "syn-session:000000:c:0002",
            "parent_continuation_id": "syn-session:000000:c:0000",
            "branch_group_id": "syn-session:000000:fork:0000",
            "tool_wait_before_s": 5.0,
            "fault_class": None,
        },
        {
            "record_id": "r3",
            "session_id": "syn-session:000000",
            "continuation_id": "syn-session:000000:c:0003",
            "parent_continuation_id": "syn-session:000000:c:0002",
            "branch_group_id": None,
            "tool_wait_before_s": 1.0,
            "fault_class": None,
        },
        {
            "record_id": "r4",
            "session_id": "syn-session:000001",
            "continuation_id": "syn-session:000001:c:0000",
            "parent_continuation_id": None,
            "branch_group_id": None,
            "tool_wait_before_s": None,
            "fault_class": None,
        },
        {
            "record_id": "r5",
            "session_id": "syn-session:000001",
            "continuation_id": "syn-session:000001:c:0001",
            "parent_continuation_id": "syn-session:000001:c:0000",
            "branch_group_id": None,
            "tool_wait_before_s": 5.0,
            "fault_class": None,
        },
    ]


def test_same_source_config_seed_reproduces_byte_identically() -> None:
    source = _source()
    left = augment_trace(source, _config())
    right = augment_trace(source, _config())
    assert left.to_json() == right.to_json()
    assert left.fingerprint == right.fingerprint
    assert left.assert_reproducible(source) is left


def test_changed_seed_or_config_changes_augmentation_fingerprint() -> None:
    source = _source()
    baseline = augment_trace(source, _zero_config(seed=1))
    changed_seed = augment_trace(source, _zero_config(seed=2))
    changed_config = augment_trace(
        source,
        _zero_config(seed=1, session_length_records=3),
    )
    assert baseline.fingerprint != changed_seed.fingerprint
    assert baseline.fingerprint != changed_config.fingerprint


def test_config_dictionary_order_is_not_semantic() -> None:
    original = _config().to_dict()
    reordered = dict(reversed(tuple(original.items())))
    rebuilt = ContinuityAugmentationConfig.from_dict(reordered)
    source = _source()
    assert augment_trace(source, rebuilt).to_json() == augment_trace(
        source, _config()
    ).to_json()


def test_session_boundaries_and_linear_parent_links_without_branching() -> None:
    augmentation = augment_trace(
        _source(count=5),
        _zero_config(session_length_records=2),
    )
    annotations = augmentation.annotations
    assert [a.session_id for a in annotations] == [
        "syn-session:000000",
        "syn-session:000000",
        "syn-session:000001",
        "syn-session:000001",
        "syn-session:000002",
    ]
    assert annotations[0].parent_continuation_id is None
    assert annotations[1].parent_continuation_id == annotations[0].continuation_id
    assert annotations[2].parent_continuation_id is None
    assert annotations[3].parent_continuation_id == annotations[2].continuation_id
    assert annotations[4].parent_continuation_id is None
    assert all(a.branch_group_id is None for a in annotations)


def test_branching_uses_configured_same_session_lookback() -> None:
    config = _config(
        session_length_records=5,
        tool_wait_probability=0.0,
        tool_wait_seconds=(),
        branch_probability=1.0,
        branch_lookback_records=2,
        fault_probability=0.0,
        fault_classes=(),
    )
    annotations = augment_trace(_source(count=5), config).annotations
    assert annotations[0].parent_continuation_id is None
    assert annotations[1].parent_continuation_id == annotations[0].continuation_id
    assert annotations[1].branch_group_id is None
    assert annotations[2].parent_continuation_id == annotations[0].continuation_id
    assert annotations[2].branch_group_id == "syn-session:000000:fork:0000"
    assert annotations[3].parent_continuation_id == annotations[1].continuation_id
    assert annotations[3].branch_group_id == "syn-session:000000:fork:0001"
    assert annotations[4].parent_continuation_id == annotations[2].continuation_id
    assert annotations[4].branch_group_id == "syn-session:000000:fork:0002"


def test_tool_waits_skip_roots_and_use_only_explicit_choices() -> None:
    config = _config(
        session_length_records=3,
        tool_wait_probability=1.0,
        tool_wait_seconds=(2.0, 9.0),
        branch_probability=0.0,
        fault_probability=0.0,
        fault_classes=(),
    )
    annotations = augment_trace(_source(count=6), config).annotations
    assert annotations[0].tool_wait_before_s is None
    assert annotations[3].tool_wait_before_s is None
    assert all(
        annotation.tool_wait_before_s in (None, 2.0, 9.0)
        for annotation in annotations
    )
    assert all(
        annotation.tool_wait_before_s is not None
        for index, annotation in enumerate(annotations)
        if index not in (0, 3)
    )


def test_fault_annotations_use_only_explicit_policy_neutral_fault_classes() -> None:
    allowed = (FaultClass.DELIVERY_DUPLICATE, FaultClass.REPLICA_EVICTION)
    config = _config(
        tool_wait_probability=0.0,
        tool_wait_seconds=(),
        branch_probability=0.0,
        fault_probability=1.0,
        fault_classes=allowed,
    )
    annotations = augment_trace(_source(), config).annotations
    assert all(annotation.fault_class in allowed for annotation in annotations)


def test_zero_probability_control_has_no_tool_branch_or_fault_annotations() -> None:
    annotations = augment_trace(_source(), _zero_config()).annotations
    assert all(annotation.tool_wait_before_s is None for annotation in annotations)
    assert all(annotation.branch_group_id is None for annotation in annotations)
    assert all(annotation.fault_class is None for annotation in annotations)


@pytest.mark.parametrize(
    "kwargs, error_type",
    [
        ({"seed": -1}, ValueError),
        ({"seed": True}, TypeError),
        ({"session_length_records": 0}, ValueError),
        ({"tool_wait_probability": -0.1}, ValueError),
        ({"tool_wait_probability": 1.1}, ValueError),
        ({"tool_wait_probability": math.inf}, ValueError),
        ({"tool_wait_probability": True}, TypeError),
        ({"tool_wait_probability": 0.5, "tool_wait_seconds": ()}, ValueError),
        ({"tool_wait_seconds": (0.0,)}, ValueError),
        ({"tool_wait_seconds": (1.0, 1.0)}, ValueError),
        ({"branch_lookback_records": 1}, ValueError),
        (
            {
                "session_length_records": 2,
                "branch_probability": 0.5,
                "branch_lookback_records": 2,
            },
            ValueError,
        ),
        ({"fault_probability": -0.1}, ValueError),
        ({"fault_probability": 0.5, "fault_classes": ()}, ValueError),
        ({"fault_classes": (FaultClass.DELIVERY_DELAY, FaultClass.DELIVERY_DELAY)}, ValueError),
        ({"fault_classes": ("DELIVERY_DELAY",)}, TypeError),
    ],
)
def test_config_validation(kwargs: dict[str, object], error_type: type[Exception]) -> None:
    values: dict[str, object] = {
        "seed": 7,
        "session_length_records": 4,
        "tool_wait_probability": 0.5,
        "tool_wait_seconds": (1.0, 5.0),
        "branch_probability": 0.5,
        "branch_lookback_records": 2,
        "fault_probability": 0.5,
        "fault_classes": (FaultClass.DELIVERY_DELAY, FaultClass.WORKER_FAILURE),
    }
    values.update(kwargs)
    with pytest.raises(error_type):
        ContinuityAugmentationConfig(**values)  # type: ignore[arg-type]


def test_source_fingerprint_and_record_order_mismatch_reject() -> None:
    source = _source()
    augmentation = augment_trace(source, _config())
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        augmentation.assert_compatible_source(_source(input_delta=1))

    payload = augmentation.to_dict()
    payload["annotations"][0]["record_id"] = "other"
    tampered = ContinuityAugmentationDataset.from_json(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )
    with pytest.raises(ValueError, match="record linkage"):
        tampered.assert_compatible_source(source)


def test_canonical_json_round_trip_and_reproducibility() -> None:
    source = _source()
    augmentation = augment_trace(source, _config())
    restored = ContinuityAugmentationDataset.from_json(augmentation.to_json())
    assert restored == augmentation
    assert restored.to_json() == augmentation.to_json()
    assert restored.fingerprint == augmentation.fingerprint
    assert restored.assert_reproducible(source) is restored


def test_semantically_tampered_but_structural_artifact_fails_reproducibility() -> None:
    source = _source()
    config = _config(
        fault_probability=0.0,
        fault_classes=(FaultClass.DELIVERY_DELAY,),
    )
    augmentation = augment_trace(source, config)
    payload = augmentation.to_dict()
    payload["annotations"][0]["fault_class"] = "DELIVERY_DELAY"
    tampered = ContinuityAugmentationDataset.from_json(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )
    with pytest.raises(ValueError, match="does not reproduce"):
        tampered.assert_reproducible(source)


def test_malformed_parent_and_session_resumption_reject() -> None:
    source = _source()
    payload = augment_trace(source, _zero_config()).to_dict()
    payload["annotations"][1]["parent_continuation_id"] = "missing-parent"
    with pytest.raises(ValueError, match="parent Continuation"):
        ContinuityAugmentationDataset.from_json(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )

    payload = augment_trace(
        source, _zero_config(session_length_records=2)
    ).to_dict()
    payload["annotations"][4]["session_id"] = payload["annotations"][0]["session_id"]
    payload["annotations"][4]["continuation_id"] = "resumed:c:0000"
    with pytest.raises(ValueError, match="contiguous"):
        ContinuityAugmentationDataset.from_json(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


def test_unknown_missing_duplicate_nonfinite_and_bad_provenance_json_reject() -> None:
    augmentation = augment_trace(_source(), _config())

    payload = augmentation.to_dict()
    payload["source_input_tokens"] = [1]
    with pytest.raises(ValueError, match="fields must exactly match"):
        ContinuityAugmentationDataset.from_json(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )

    payload = augmentation.to_dict()
    del payload["provenance"]
    with pytest.raises(ValueError, match="fields must exactly match"):
        ContinuityAugmentationDataset.from_json(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )

    duplicate = augmentation.to_json()[:-1] + (
        ',"schema":"cadi.c5.3.continuity-augmentation.v1"}'
    )
    with pytest.raises(ValueError, match="duplicate JSON member"):
        ContinuityAugmentationDataset.from_json(duplicate)

    nonfinite = augmentation.to_json().replace(
        '"branch_probability":0.5', '"branch_probability":NaN'
    )
    with pytest.raises(ValueError, match="non-finite"):
        ContinuityAugmentationDataset.from_json(nonfinite)

    payload = augmentation.to_dict()
    payload["provenance"] = "TRACE_DERIVED"
    with pytest.raises(ValueError, match="provenance"):
        ContinuityAugmentationDataset.from_json(
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )


def test_annotation_rejects_unconfigured_wait_or_fault_when_dataset_constructed() -> None:
    config = _zero_config()
    root = ContinuityAnnotation(
        record_id="r0",
        session_id="s0",
        continuation_id="c0",
        parent_continuation_id=None,
        branch_group_id=None,
        tool_wait_before_s=None,
        fault_class=FaultClass.DELIVERY_DELAY,
    )
    with pytest.raises(ValueError, match="fault_class"):
        ContinuityAugmentationDataset(
            source_dataset_fingerprint="b" * 64,
            config=config,
            annotations=(root,),
        )
