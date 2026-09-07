from __future__ import annotations

import pytest

from experiments.continuity_augmentation import (
    ContinuityAnnotation,
    ContinuityAugmentationConfig,
    ContinuityAugmentationDataset,
)
from simulator.faults import FaultClass


def _config() -> ContinuityAugmentationConfig:
    return ContinuityAugmentationConfig(
        seed=1,
        session_length_records=2,
        tool_wait_probability=0.0,
        tool_wait_seconds=(1.0,),
        branch_probability=0.0,
        branch_lookback_records=2,
        fault_probability=0.0,
        fault_classes=(FaultClass.DELIVERY_DELAY,),
    )


def _root(*, fault_class: FaultClass | None = None) -> ContinuityAnnotation:
    return ContinuityAnnotation(
        record_id="r0",
        session_id="s0",
        continuation_id="c0",
        parent_continuation_id=None,
        branch_group_id=None,
        tool_wait_before_s=None,
        fault_class=fault_class,
    )


def test_dataset_rejects_unconfigured_fault_class() -> None:
    with pytest.raises(ValueError, match="fault_class"):
        ContinuityAugmentationDataset(
            source_dataset_fingerprint="b" * 64,
            config=_config(),
            annotations=(_root(fault_class=FaultClass.WORKER_FAILURE),),
        )


def test_dataset_rejects_unconfigured_tool_wait_value() -> None:
    annotations = (
        _root(),
        ContinuityAnnotation(
            record_id="r1",
            session_id="s0",
            continuation_id="c1",
            parent_continuation_id="c0",
            branch_group_id=None,
            tool_wait_before_s=2.0,
            fault_class=None,
        ),
    )
    with pytest.raises(ValueError, match="tool_wait_before_s"):
        ContinuityAugmentationDataset(
            source_dataset_fingerprint="b" * 64,
            config=_config(),
            annotations=annotations,
        )
