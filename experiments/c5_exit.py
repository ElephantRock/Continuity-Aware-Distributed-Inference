from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from experiments.continuity_augmentation import (
    AugmentationProvenance,
    ContinuityAugmentationConfig,
    ContinuityAugmentationDataset,
    augment_trace,
)
from experiments.mooncake_trace import (
    MOONCAKE_EXPECTED_REQUESTS,
    MOONCAKE_SOURCE_SHA256,
    load_pinned_mooncake_trace,
)
from experiments.trace_workload import NormalizedTraceDataset
from experiments.workload_modes import (
    FullySyntheticWorkloadConfig,
    FullySyntheticWorkloadDataset,
    SyntheticWorkloadProvenance,
    WorkloadEnvelope,
    WorkloadMode,
    generate_fully_synthetic_workload,
)
from simulator.faults import FaultClass


C5_EXIT_VERSION = "cadi.c5.5.exit-reproducibility.v1"
C5_EXIT_EXPECTED_SOURCE_DATASET_FINGERPRINT = (
    "22ead90d97ae218f229f94378f8f019499ede0e4050e6cbf8092b455ae047718"
)
C5_EXIT_EXPECTED_SYNTHETIC_DATASET_FINGERPRINT = (
    "d78dbb41571aec557c2a6fc581e454a0cd50011b8f3582bcaa9eaa1c495c4051"
)


def c5_exit_augmentation_config() -> ContinuityAugmentationConfig:
    """Frozen reproducibility control, not a prevalence/realism estimate."""

    return ContinuityAugmentationConfig(
        seed=20_260_907,
        session_length_records=8,
        tool_wait_probability=0.20,
        tool_wait_seconds=(0.25, 1.0, 5.0),
        branch_probability=0.10,
        branch_lookback_records=2,
        fault_probability=0.05,
        fault_classes=(
            FaultClass.DELIVERY_DELAY,
            FaultClass.DELIVERY_DROP,
            FaultClass.WORKER_FAILURE,
        ),
    )


def c5_exit_synthetic_config() -> FullySyntheticWorkloadConfig:
    """Exact C5.4 frozen mechanics vector."""

    return FullySyntheticWorkloadConfig(
        seed=11,
        request_count=4,
        interarrival_s_choices=(0.5, 1.25),
        input_tokens_choices=(100, 400),
        output_tokens_choices=(5, 20),
        prefix_fraction_choices=(0.0, 0.5),
        prefix_group_count=2,
    )


@dataclass(frozen=True, slots=True)
class _C5ExitArtifacts:
    source: NormalizedTraceDataset
    source_envelope: WorkloadEnvelope
    augmentation: ContinuityAugmentationDataset
    trace_envelope: WorkloadEnvelope
    synthetic: FullySyntheticWorkloadDataset
    synthetic_envelope: WorkloadEnvelope

    @property
    def replay_vector(self) -> tuple[str, ...]:
        return (
            self.source.to_json(),
            self.source_envelope.to_json(),
            self.augmentation.to_json(),
            self.trace_envelope.to_json(),
            self.synthetic.to_json(),
            self.synthetic_envelope.to_json(),
        )


def _construct(raw: bytes) -> _C5ExitArtifacts:
    source = load_pinned_mooncake_trace(raw)
    source_before = source.to_json()
    if source.fingerprint != C5_EXIT_EXPECTED_SOURCE_DATASET_FINGERPRINT:
        raise ValueError(
            "C5 source normalized fingerprint drift: "
            f"{source.fingerprint} != {C5_EXIT_EXPECTED_SOURCE_DATASET_FINGERPRINT}"
        )
    if len(source.source_order) != MOONCAKE_EXPECTED_REQUESTS:
        raise ValueError("C5 source request-count drift")

    source_envelope = WorkloadEnvelope.source_derived(source)
    source_envelope.assert_matches_source(source)

    augmentation = augment_trace(source, c5_exit_augmentation_config())
    augmentation.assert_reproducible(source)
    trace_envelope = WorkloadEnvelope.trace_augmented(source, augmentation)
    trace_envelope.assert_matches_trace_augmented(source, augmentation)

    synthetic = generate_fully_synthetic_workload(c5_exit_synthetic_config())
    if synthetic.fingerprint != C5_EXIT_EXPECTED_SYNTHETIC_DATASET_FINGERPRINT:
        raise ValueError(
            "C5 fully synthetic fingerprint drift: "
            f"{synthetic.fingerprint} != "
            f"{C5_EXIT_EXPECTED_SYNTHETIC_DATASET_FINGERPRINT}"
        )
    synthetic_envelope = WorkloadEnvelope.fully_synthetic(synthetic)
    synthetic_envelope.assert_matches_fully_synthetic(synthetic)

    modes = {
        source_envelope.mode,
        trace_envelope.mode,
        synthetic_envelope.mode,
    }
    if modes != {
        WorkloadMode.SOURCE_DERIVED,
        WorkloadMode.TRACE_AUGMENTED,
        WorkloadMode.FULLY_SYNTHETIC,
    }:
        raise ValueError("C5 workload modes are not explicit and distinct")

    if augmentation.provenance is not AugmentationProvenance.SYNTHETIC:
        raise ValueError("C5 trace augmentation provenance drift")
    if synthetic.provenance is not SyntheticWorkloadProvenance.SYNTHETIC:
        raise ValueError("C5 fully synthetic provenance drift")
    if augmentation.source_dataset_fingerprint != source.fingerprint:
        raise ValueError("C5 trace augmentation source binding drift")
    if source.to_json() != source_before:
        raise ValueError("C5 source artifact mutated during workload construction")

    return _C5ExitArtifacts(
        source=source,
        source_envelope=source_envelope,
        augmentation=augmentation,
        trace_envelope=trace_envelope,
        synthetic=synthetic,
        synthetic_envelope=synthetic_envelope,
    )


@dataclass(frozen=True, slots=True)
class C5ExitSummary:
    version: str
    raw_sha256: str
    source_requests: int
    source_dataset_fingerprint: str
    source_envelope_fingerprint: str
    source_field_origins: Mapping[str, str]
    augmentation_fingerprint: str
    augmentation_annotations: int
    augmentation_sessions: int
    augmentation_tool_waits: int
    augmentation_branches: int
    augmentation_faults: int
    augmentation_provenance: str
    trace_augmented_envelope_fingerprint: str
    synthetic_dataset_fingerprint: str
    synthetic_requests: int
    synthetic_provenance: str
    synthetic_envelope_fingerprint: str
    modes: tuple[str, ...]
    replay_identical: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "raw_sha256": self.raw_sha256,
            "source_requests": self.source_requests,
            "source_dataset_fingerprint": self.source_dataset_fingerprint,
            "source_envelope_fingerprint": self.source_envelope_fingerprint,
            "source_field_origins": dict(sorted(self.source_field_origins.items())),
            "augmentation_fingerprint": self.augmentation_fingerprint,
            "augmentation_annotations": self.augmentation_annotations,
            "augmentation_sessions": self.augmentation_sessions,
            "augmentation_tool_waits": self.augmentation_tool_waits,
            "augmentation_branches": self.augmentation_branches,
            "augmentation_faults": self.augmentation_faults,
            "augmentation_provenance": self.augmentation_provenance,
            "trace_augmented_envelope_fingerprint": (
                self.trace_augmented_envelope_fingerprint
            ),
            "synthetic_dataset_fingerprint": self.synthetic_dataset_fingerprint,
            "synthetic_requests": self.synthetic_requests,
            "synthetic_provenance": self.synthetic_provenance,
            "synthetic_envelope_fingerprint": self.synthetic_envelope_fingerprint,
            "modes": list(self.modes),
            "replay_identical": self.replay_identical,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


def run_c5_exit(raw: bytes) -> C5ExitSummary:
    if not isinstance(raw, bytes):
        raise TypeError("C5 exit source artifact must be bytes")
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    if raw_sha256 != MOONCAKE_SOURCE_SHA256:
        raise ValueError(
            "C5 exit source SHA-256 mismatch: "
            f"{raw_sha256} != {MOONCAKE_SOURCE_SHA256}"
        )

    first = _construct(raw)
    second = _construct(raw)
    if first.replay_vector != second.replay_vector:
        raise ValueError("C5 end-to-end canonical replay is not deterministic")

    augmentation = first.augmentation
    annotations = augmentation.annotations
    origins = {
        field.value: origin.value
        for field, origin in first.source.manifest.field_origins
    }

    return C5ExitSummary(
        version=C5_EXIT_VERSION,
        raw_sha256=raw_sha256,
        source_requests=len(first.source.source_order),
        source_dataset_fingerprint=first.source.fingerprint,
        source_envelope_fingerprint=first.source_envelope.fingerprint,
        source_field_origins=origins,
        augmentation_fingerprint=augmentation.fingerprint,
        augmentation_annotations=len(annotations),
        augmentation_sessions=len({annotation.session_id for annotation in annotations}),
        augmentation_tool_waits=sum(
            annotation.tool_wait_before_s is not None for annotation in annotations
        ),
        augmentation_branches=sum(
            annotation.branch_group_id is not None for annotation in annotations
        ),
        augmentation_faults=sum(
            annotation.fault_class is not None for annotation in annotations
        ),
        augmentation_provenance=augmentation.provenance.value,
        trace_augmented_envelope_fingerprint=first.trace_envelope.fingerprint,
        synthetic_dataset_fingerprint=first.synthetic.fingerprint,
        synthetic_requests=len(first.synthetic.records),
        synthetic_provenance=first.synthetic.provenance.value,
        synthetic_envelope_fingerprint=first.synthetic_envelope.fingerprint,
        modes=(
            first.source_envelope.mode.value,
            first.trace_envelope.mode.value,
            first.synthetic_envelope.mode.value,
        ),
        replay_identical=True,
    )


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: python -m experiments.c5_exit SOURCE_JSONL")
    summary = run_c5_exit(Path(argv[1]).read_bytes())
    print(summary.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
