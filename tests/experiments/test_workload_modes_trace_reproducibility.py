from __future__ import annotations

import pytest

from experiments.continuity_augmentation import (
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
from experiments.workload_modes import WorkloadEnvelope


def _source() -> NormalizedTraceDataset:
    return NormalizedTraceDataset(
        manifest=TraceSourceManifest(
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
            ),
        ),
        records=(
            NormalizedTraceRecord(
                record_id="r0",
                source_record_id="line:1",
                source_ordinal=0,
                arrival_time_s=0.0,
                input_tokens=100,
                output_tokens=5,
            ),
            NormalizedTraceRecord(
                record_id="r1",
                source_record_id="line:2",
                source_ordinal=1,
                arrival_time_s=1.0,
                input_tokens=200,
                output_tokens=7,
            ),
        ),
    )


def _augmentation(source: NormalizedTraceDataset) -> ContinuityAugmentationDataset:
    return augment_trace(
        source,
        ContinuityAugmentationConfig(
            seed=3,
            session_length_records=2,
            tool_wait_probability=0.0,
            tool_wait_seconds=(),
            branch_probability=0.0,
            branch_lookback_records=2,
            fault_probability=0.0,
            fault_classes=(),
        ),
    )


def _source_compatible_semantic_tamper(
    source: NormalizedTraceDataset,
) -> tuple[ContinuityAugmentationDataset, ContinuityAugmentationDataset]:
    original = _augmentation(source)
    first, second = original.annotations
    tampered = ContinuityAugmentationDataset(
        source_dataset_fingerprint=original.source_dataset_fingerprint,
        config=original.config,
        annotations=(
            first,
            ContinuityAnnotation(
                record_id=second.record_id,
                session_id="tampered-session",
                continuation_id="tampered-session:c:0000",
                parent_continuation_id=None,
                branch_group_id=None,
                tool_wait_before_s=None,
                fault_class=None,
            ),
        ),
    )
    tampered.assert_compatible_source(source)
    return original, tampered


def test_trace_augmented_constructor_rejects_source_compatible_nonreproducible_overlay() -> None:
    source = _source()
    _original, tampered = _source_compatible_semantic_tamper(source)

    with pytest.raises(ValueError, match="does not reproduce"):
        WorkloadEnvelope.trace_augmented(source, tampered)


def test_trace_augmented_match_rejects_source_compatible_nonreproducible_overlay() -> None:
    source = _source()
    original, tampered = _source_compatible_semantic_tamper(source)
    envelope = WorkloadEnvelope.trace_augmented(source, original)

    with pytest.raises(ValueError, match="does not reproduce"):
        envelope.assert_matches_trace_augmented(source, tampered)
