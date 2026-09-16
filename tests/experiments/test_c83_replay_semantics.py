from __future__ import annotations

from experiments.c83_replay_protocol import (
    C83A_TRACE_SPECS,
    C83Layer,
    normalize_raw_outcome,
)
from experiments.c83_replay_semantics import run_c1_trace, run_c2_trace


def test_c1_and_c2_execute_all_frozen_trace_checkpoints() -> None:
    for spec in C83A_TRACE_SPECS:
        c1 = run_c1_trace(spec.trace_id)
        c2 = run_c2_trace(spec.trace_id)
        expected_ids = tuple(checkpoint.checkpoint_id for checkpoint in spec.checkpoints)
        assert tuple(item.checkpoint_id for item in c1) == expected_ids
        assert tuple(item.checkpoint_id for item in c2) == expected_ids

        for layer, observations in ((C83Layer.C1, c1), (C83Layer.C2, c2)):
            for checkpoint, observation in zip(spec.checkpoints, observations, strict=True):
                normalized, violation = normalize_raw_outcome(
                    spec.trace_id,
                    checkpoint.checkpoint_id,
                    layer,
                    observation.raw_outcome,
                )
                assert normalized is checkpoint.expected
                assert violation is False
                assert tuple(observation.projection) == checkpoint.semantic_projection_fields


def test_c1_and_c2_semantic_projections_match_before_c8_execution() -> None:
    for spec in C83A_TRACE_SPECS:
        c1 = run_c1_trace(spec.trace_id)
        c2 = run_c2_trace(spec.trace_id)
        assert [item.projection for item in c1] == [item.projection for item in c2]
