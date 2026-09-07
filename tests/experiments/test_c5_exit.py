from __future__ import annotations

import hashlib

import pytest

import experiments.c5_exit as c5_exit
from experiments.continuity_augmentation import AugmentationProvenance
from experiments.trace_workload import (
    NormalizedTraceDataset,
    NormalizedTraceRecord,
    TraceField,
    TraceFieldOrigin,
    TraceSourceManifest,
)
from experiments.workload_modes import SyntheticWorkloadProvenance, WorkloadMode


def _fixture_source() -> NormalizedTraceDataset:
    return NormalizedTraceDataset(
        manifest=TraceSourceManifest(
            source_id="c5-exit-fixture",
            source_name="C5 exit bounded fixture",
            source_uri="https://example.invalid/c5-exit-fixture",
            source_version="fixture-v1",
            license_id="test-only",
            source_sha256="a" * 64,
            normalization_version="fixture-v1",
            normalization_steps=("bounded test fixture",),
            field_origins=(
                (TraceField.ARRIVAL_TIME_S, TraceFieldOrigin.SOURCE_OBSERVED),
                (TraceField.INPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
                (TraceField.OUTPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
                (TraceField.PREFIX_GROUP_ID, TraceFieldOrigin.TRACE_DERIVED),
                (TraceField.PREFIX_TOKENS, TraceFieldOrigin.TRACE_DERIVED),
            ),
        ),
        records=tuple(
            NormalizedTraceRecord(
                record_id=f"fixture:{ordinal:02d}",
                source_record_id=f"line:{ordinal + 1}",
                source_ordinal=ordinal,
                arrival_time_s=float(ordinal),
                input_tokens=100 + ordinal * 10,
                output_tokens=5 + ordinal,
                prefix_group_id=(None if ordinal < 2 else "fixture-prefix"),
                prefix_tokens=(0 if ordinal < 2 else 20),
            )
            for ordinal in range(12)
        ),
    )


def _patch_source(monkeypatch: pytest.MonkeyPatch) -> tuple[bytes, NormalizedTraceDataset]:
    raw = b"bounded-c5-exit-fixture"
    source = _fixture_source()
    monkeypatch.setattr(c5_exit, "MOONCAKE_SOURCE_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(c5_exit, "MOONCAKE_EXPECTED_REQUESTS", len(source.source_order))
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_SOURCE_DATASET_FINGERPRINT",
        source.fingerprint,
    )
    monkeypatch.setattr(c5_exit, "load_pinned_mooncake_trace", lambda candidate: source)

    artifacts = c5_exit._construct(raw)
    annotations = artifacts.augmentation.annotations
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_SOURCE_ENVELOPE_FINGERPRINT",
        artifacts.source_envelope.fingerprint,
    )
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_AUGMENTATION_FINGERPRINT",
        artifacts.augmentation.fingerprint,
    )
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_AUGMENTATION_ANNOTATIONS",
        len(annotations),
    )
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_AUGMENTATION_SESSIONS",
        len({annotation.session_id for annotation in annotations}),
    )
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_AUGMENTATION_TOOL_WAITS",
        sum(annotation.tool_wait_before_s is not None for annotation in annotations),
    )
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_AUGMENTATION_BRANCHES",
        sum(annotation.branch_group_id is not None for annotation in annotations),
    )
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_AUGMENTATION_FAULTS",
        sum(annotation.fault_class is not None for annotation in annotations),
    )
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_TRACE_AUGMENTED_ENVELOPE_FINGERPRINT",
        artifacts.trace_envelope.fingerprint,
    )
    monkeypatch.setattr(
        c5_exit,
        "C5_EXIT_EXPECTED_SYNTHETIC_ENVELOPE_FINGERPRINT",
        artifacts.synthetic_envelope.fingerprint,
    )
    return raw, source


def test_bounded_exit_replay_is_identical_and_modes_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, source = _patch_source(monkeypatch)
    source_before = source.to_json()

    first = c5_exit.run_c5_exit(raw)
    second = c5_exit.run_c5_exit(raw)

    assert first.to_json() == second.to_json()
    assert first.replay_identical is True
    assert first.modes == (
        WorkloadMode.SOURCE_DERIVED.value,
        WorkloadMode.TRACE_AUGMENTED.value,
        WorkloadMode.FULLY_SYNTHETIC.value,
    )
    assert len(set(first.modes)) == 3
    assert first.source_dataset_fingerprint == source.fingerprint
    assert first.augmentation_provenance == AugmentationProvenance.SYNTHETIC.value
    assert first.synthetic_provenance == SyntheticWorkloadProvenance.SYNTHETIC.value
    assert first.synthetic_dataset_fingerprint == (
        c5_exit.C5_EXIT_EXPECTED_SYNTHETIC_DATASET_FINGERPRINT
    )
    assert source.to_json() == source_before


def test_bounded_exit_artifact_match_functions_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, source = _patch_source(monkeypatch)
    artifacts = c5_exit._construct(raw)

    artifacts.source_envelope.assert_matches_source(source)
    artifacts.augmentation.assert_reproducible(source)
    artifacts.trace_envelope.assert_matches_trace_augmented(
        source, artifacts.augmentation
    )
    artifacts.synthetic.assert_reproducible()
    artifacts.synthetic_envelope.assert_matches_fully_synthetic(artifacts.synthetic)


def test_bounded_exit_summary_counts_are_internally_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, _ = _patch_source(monkeypatch)
    summary = c5_exit.run_c5_exit(raw)

    assert summary.source_requests == 12
    assert summary.synthetic_requests == 4
    assert summary.augmentation_annotations == summary.source_requests
    assert 1 <= summary.augmentation_sessions <= summary.augmentation_annotations
    assert 0 <= summary.augmentation_tool_waits <= summary.augmentation_annotations
    assert 0 <= summary.augmentation_branches <= summary.augmentation_annotations
    assert 0 <= summary.augmentation_faults <= summary.augmentation_annotations
    assert len(summary.source_envelope_fingerprint) == 64
    assert len(summary.augmentation_fingerprint) == 64
    assert len(summary.trace_augmented_envelope_fingerprint) == 64
    assert len(summary.synthetic_envelope_fingerprint) == 64
    assert summary.source_field_origins == {
        TraceField.ARRIVAL_TIME_S.value: TraceFieldOrigin.SOURCE_OBSERVED.value,
        TraceField.INPUT_TOKENS.value: TraceFieldOrigin.SOURCE_OBSERVED.value,
        TraceField.OUTPUT_TOKENS.value: TraceFieldOrigin.SOURCE_OBSERVED.value,
        TraceField.PREFIX_GROUP_ID.value: TraceFieldOrigin.TRACE_DERIVED.value,
        TraceField.PREFIX_TOKENS.value: TraceFieldOrigin.TRACE_DERIVED.value,
    }


def test_frozen_exit_vector_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, _ = _patch_source(monkeypatch)
    monkeypatch.setattr(c5_exit, "C5_EXIT_EXPECTED_AUGMENTATION_FAULTS", -1)
    with pytest.raises(ValueError, match="frozen augmentation_faults drift"):
        c5_exit.run_c5_exit(raw)


def test_non_pinned_raw_bytes_fail_before_exit_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected_loader(raw: bytes) -> NormalizedTraceDataset:
        nonlocal called
        called = True
        return _fixture_source()

    monkeypatch.setattr(c5_exit, "load_pinned_mooncake_trace", unexpected_loader)
    with pytest.raises(ValueError, match="source SHA-256 mismatch"):
        c5_exit.run_c5_exit(b"not-the-pinned-source")
    assert called is False
