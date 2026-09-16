from __future__ import annotations

from pathlib import Path

from experiments.c83_replay_protocol import (
    C83A_TRACE_SPECS,
    C83Layer,
    normalize_raw_outcome,
)
from experiments.c83_replay_runtime import C83RealReplayRuntime
from experiments.c83_replay_semantics import run_c1_trace, run_c2_trace


def test_real_c8_replay_executes_all_frozen_trace_checkpoints(tmp_path: Path) -> None:
    for spec in C83A_TRACE_SPECS:
        runtime = C83RealReplayRuntime(spec.trace_id, tmp_path / spec.trace_id)
        observations = runtime.run()
        topology = runtime.topology()

        expected_ids = tuple(checkpoint.checkpoint_id for checkpoint in spec.checkpoints)
        assert tuple(item.checkpoint_id for item in observations) == expected_ids
        assert len(topology["worker_pids"]) == spec.worker_generations
        assert len(
            {
                topology["authority_pid"],
                topology["fault_harness_pid"],
                *topology["worker_pids"],
            }
        ) == 2 + spec.worker_generations
        assert topology["authority_port"] != topology["worker_port"]
        assert topology["real_process_boundary"] is True

        for checkpoint, observation in zip(spec.checkpoints, observations, strict=True):
            normalized, violation = normalize_raw_outcome(
                spec.trace_id,
                checkpoint.checkpoint_id,
                C83Layer.C8,
                observation.raw_outcome,
            )
            assert normalized is checkpoint.expected
            assert violation is False
            assert set(observation.projection) == set(checkpoint.semantic_projection_fields)
            assert len(observation.projection) == len(checkpoint.semantic_projection_fields)


def test_c1_c2_c8_semantic_projection_values_match(tmp_path: Path) -> None:
    for spec in C83A_TRACE_SPECS:
        c1 = run_c1_trace(spec.trace_id)
        c2 = run_c2_trace(spec.trace_id)
        runtime = C83RealReplayRuntime(spec.trace_id, tmp_path / f"projection-{spec.trace_id}")
        c8 = runtime.run()
        assert [item.projection for item in c1] == [item.projection for item in c2]
        assert [item.projection for item in c1] == [item.projection for item in c8]
