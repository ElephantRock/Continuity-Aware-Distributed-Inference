from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping

from experiments.c83_replay_protocol import (
    C83A_COMPARISON_SCHEMA,
    C83A_PROTOCOL_FINGERPRINT,
    C83A_ROW_SCHEMA,
    C83A_TRACE_SPECS,
    C83Layer,
    C83NormalizedOutcome,
    normalize_raw_outcome,
    validate_comparison,
    validate_layer_row,
)
from experiments.c83_replay_runtime import C83RealReplayRuntime
from experiments.c83_replay_semantics import ReplayObservation, run_c1_trace, run_c2_trace
from prototype.c8_runtime import resolve_clean_checkout


C83B_RESULT_SCHEMA = "cadi.c8.3b.cross-layer-replay-result.v1"
C83B_BASE_COMMIT = "d8e05ef0fe4ab6bf469202b4a2476dcbb12eede5"
C83B_EVIDENCE_CLASS = "EV1_MEASURED_CPU_DISTRIBUTED_CORRECTNESS"
C83B_GATE_G3_PERFORMANCE_INSPECTION = "NONE"

_EXPLICIT_NON_SUCCESS = frozenset(
    {
        C83NormalizedOutcome.REJECTED,
        C83NormalizedOutcome.WAIT,
        C83NormalizedOutcome.RETRY,
        C83NormalizedOutcome.RECOMPUTE,
        C83NormalizedOutcome.FAIL,
        C83NormalizedOutcome.AMBIGUOUS,
    }
)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def semantic_state_fingerprint(projection: Mapping[str, Any]) -> str:
    if not isinstance(projection, Mapping):
        raise TypeError("semantic projection must be a mapping")
    return sha256_hex(dict(projection))


def _trial_id(trace_id: str) -> str:
    return f"c83b:{trace_id}:trial-0"


def _row(
    *,
    spec: Any,
    execution_git_commit: str,
    layer: C83Layer,
    observation: ReplayObservation,
    topology: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    checkpoint = next(
        item for item in spec.checkpoints if item.checkpoint_id == observation.checkpoint_id
    )
    if tuple(observation.projection) != checkpoint.semantic_projection_fields:
        raise ValueError("semantic projection field order differs from frozen checkpoint contract")
    normalized, forbidden_violation = normalize_raw_outcome(
        spec.trace_id,
        checkpoint.checkpoint_id,
        layer,
        observation.raw_outcome,
    )
    row = {
        "schema": C83A_ROW_SCHEMA,
        "trace_id": spec.trace_id,
        "trial_id": _trial_id(spec.trace_id),
        "execution_git_commit": execution_git_commit,
        "layer": layer.value,
        "checkpoint_id": checkpoint.checkpoint_id,
        "raw_outcome": observation.raw_outcome,
        "normalized_outcome": normalized.value,
        "opportunities": 1 if checkpoint.opportunity else 0,
        "violations": 1 if checkpoint.opportunity and forbidden_violation else 0,
        "explicit_non_success": normalized in _EXPLICIT_NON_SUCCESS,
        "replay_execution_failure": False,
        "semantic_state_fingerprint": semantic_state_fingerprint(observation.projection),
        "topology_provenance": dict(topology) if topology is not None else None,
        "fault_script_fingerprint": (
            spec.fault_script_fingerprint() if layer is C83Layer.C8 else None
        ),
    }
    return validate_layer_row(row)


def _derive_comparison(
    spec: Any,
    execution_git_commit: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    vectors = {
        layer.value: [
            next(
                row["normalized_outcome"]
                for row in rows
                if row["layer"] == layer.value
                and row["checkpoint_id"] == checkpoint.checkpoint_id
            )
            for checkpoint in spec.checkpoints
        ]
        for layer in C83Layer
    }
    state_vectors = {
        layer.value: [
            next(
                row["semantic_state_fingerprint"]
                for row in rows
                if row["layer"] == layer.value
                and row["checkpoint_id"] == checkpoint.checkpoint_id
            )
            for checkpoint in spec.checkpoints
        ]
        for layer in C83Layer
    }
    expected_vector = [checkpoint.expected.value for checkpoint in spec.checkpoints]
    expected_match = {
        layer.value: vectors[layer.value] == expected_vector for layer in C83Layer
    }
    opportunities = {
        layer.value: sum(
            row["opportunities"] for row in rows if row["layer"] == layer.value
        )
        for layer in C83Layer
    }
    violations = {
        layer.value: sum(
            row["violations"] for row in rows if row["layer"] == layer.value
        )
        for layer in C83Layer
    }
    state_equivalent = len({tuple(items) for items in state_vectors.values()}) == 1
    semantic_equivalent = (
        len({tuple(items) for items in vectors.values()}) == 1 and state_equivalent
    )
    opportunity_counts_match = len(set(opportunities.values())) == 1
    execution_failure_count = sum(1 for row in rows if row["replay_execution_failure"])
    rankable = execution_failure_count == 0 and opportunity_counts_match
    forbidden_violation_free = all(value == 0 for value in violations.values())
    correctness_pass = (
        rankable
        and semantic_equivalent
        and all(expected_match.values())
        and forbidden_violation_free
    )
    comparison = {
        "schema": C83A_COMPARISON_SCHEMA,
        "trace_id": spec.trace_id,
        "trial_id": _trial_id(spec.trace_id),
        "execution_git_commit": execution_git_commit,
        "layer_rows": rows,
        "checkpoint_vector_by_layer": vectors,
        "state_fingerprint_vector_by_layer": state_vectors,
        "state_equivalent": state_equivalent,
        "expected_checkpoint_vector": expected_vector,
        "expected_outcome_match_by_layer": expected_match,
        "semantic_equivalent": semantic_equivalent,
        "opportunity_count_by_layer": opportunities,
        "opportunity_counts_match": opportunity_counts_match,
        "violation_count_by_layer": violations,
        "forbidden_violation_free": forbidden_violation_free,
        "execution_failure_count": execution_failure_count,
        "rankable": rankable,
        "correctness_pass": correctness_pass,
    }
    return validate_comparison(comparison)


def _scientific_row(row: Mapping[str, Any]) -> dict[str, Any]:
    topology = row["topology_provenance"]
    topology_shape = None
    if topology is not None:
        pids = [
            topology["authority_pid"],
            topology["fault_harness_pid"],
            *topology["worker_pids"],
        ]
        topology_shape = {
            "transport_id": topology["transport_id"],
            "real_process_boundary": topology["real_process_boundary"],
            "worker_generation_count": len(topology["worker_pids"]),
            "all_pids_distinct": len(pids) == len(set(pids)),
            "listen_ports_distinct": topology["authority_port"] != topology["worker_port"],
        }
    return {
        "trace_id": row["trace_id"],
        "trial_id": row["trial_id"],
        "layer": row["layer"],
        "checkpoint_id": row["checkpoint_id"],
        "raw_outcome": row["raw_outcome"],
        "normalized_outcome": row["normalized_outcome"],
        "opportunities": row["opportunities"],
        "violations": row["violations"],
        "explicit_non_success": row["explicit_non_success"],
        "replay_execution_failure": row["replay_execution_failure"],
        "semantic_state_fingerprint": row["semantic_state_fingerprint"],
        "topology_shape": topology_shape,
        "fault_script_fingerprint": row["fault_script_fingerprint"],
    }


def scientific_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": C83B_RESULT_SCHEMA,
        "base_commit": result["base_commit"],
        "c83a_protocol_fingerprint": result["c83a_protocol_fingerprint"],
        "evidence_class": result["evidence_class"],
        "gate_g3_performance_inspection": result["gate_g3_performance_inspection"],
        "trace_order": result["trace_order"],
        "rows": [_scientific_row(row) for row in result["rows"]],
        "summary": result["summary"],
    }


def summarize(
    rows: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> dict[str, Any]:
    opportunity_count_by_layer = {
        layer.value: sum(row["opportunities"] for row in rows if row["layer"] == layer.value)
        for layer in C83Layer
    }
    violation_count_by_layer = {
        layer.value: sum(row["violations"] for row in rows if row["layer"] == layer.value)
        for layer in C83Layer
    }
    explicit_non_success_count = sum(bool(row["explicit_non_success"]) for row in rows)
    execution_failure_count = sum(bool(row["replay_execution_failure"]) for row in rows)
    expected_outcome_mismatch_count = sum(
        not all(comparison["expected_outcome_match_by_layer"].values())
        for comparison in comparisons
    )
    return {
        "trace_count": len(comparisons),
        "layer_checkpoint_row_count": len(rows),
        "rankable_trace_count": sum(bool(item["rankable"]) for item in comparisons),
        "semantic_equivalence_mismatch_count": sum(
            not bool(item["semantic_equivalent"]) for item in comparisons
        ),
        "state_equivalence_mismatch_count": sum(
            not bool(item["state_equivalent"]) for item in comparisons
        ),
        "expected_outcome_mismatch_count": expected_outcome_mismatch_count,
        "opportunity_count_by_layer": opportunity_count_by_layer,
        "violation_count_by_layer": violation_count_by_layer,
        "explicit_non_success_count": explicit_non_success_count,
        "physical_replay_execution_failure_count": execution_failure_count,
        "correctness_pass_trace_count": sum(
            bool(item["correctness_pass"]) for item in comparisons
        ),
    }


def generate_result(repo_root: str | Path, work_root: str | Path) -> dict[str, Any]:
    execution_git_commit = resolve_clean_checkout(repo_root)
    work = Path(work_root)
    work.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []

    for spec in C83A_TRACE_SPECS:
        c1 = run_c1_trace(spec.trace_id)
        c2 = run_c2_trace(spec.trace_id)
        runtime = C83RealReplayRuntime(spec.trace_id, work / spec.trace_id)
        c8 = runtime.run()
        topology = runtime.topology()

        expected_ids = tuple(checkpoint.checkpoint_id for checkpoint in spec.checkpoints)
        for observations in (c1, c2, c8):
            if tuple(item.checkpoint_id for item in observations) != expected_ids:
                raise RuntimeError(f"checkpoint order drift for {spec.trace_id}")

        trace_rows: list[dict[str, Any]] = []
        for layer, observations in (
            (C83Layer.C1, c1),
            (C83Layer.C2, c2),
            (C83Layer.C8, c8),
        ):
            for observation in observations:
                trace_rows.append(
                    _row(
                        spec=spec,
                        execution_git_commit=execution_git_commit,
                        layer=layer,
                        observation=observation,
                        topology=topology if layer is C83Layer.C8 else None,
                    )
                )
        rows.extend(trace_rows)
        comparisons.append(
            _derive_comparison(spec, execution_git_commit, trace_rows)
        )

    result: dict[str, Any] = {
        "schema": C83B_RESULT_SCHEMA,
        "base_commit": C83B_BASE_COMMIT,
        "execution_git_commit": execution_git_commit,
        "c83a_protocol_fingerprint": C83A_PROTOCOL_FINGERPRINT,
        "evidence_class": C83B_EVIDENCE_CLASS,
        "gate_g3_performance_inspection": C83B_GATE_G3_PERFORMANCE_INSPECTION,
        "trace_order": [spec.trace_id for spec in C83A_TRACE_SPECS],
        "rows": rows,
        "comparisons": comparisons,
        "summary": summarize(rows, comparisons),
        "scientific_fingerprint": None,
    }
    result["scientific_fingerprint"] = sha256_hex(scientific_payload(result))
    return result


def write_result(path: str | Path, result: Mapping[str, Any]) -> str:
    payload = canonical_json_bytes(dict(result))
    Path(path).write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute frozen C8.3b cross-layer correctness replay")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--work-root", default=None)
    parser.add_argument("--output", required=True)
    parser.add_argument("--second-output", default=None)
    args = parser.parse_args()

    if args.work_root is None:
        with tempfile.TemporaryDirectory(prefix="c83b-replay-") as temp_dir:
            result = generate_result(args.repo_root, temp_dir)
    else:
        result = generate_result(args.repo_root, args.work_root)

    artifact_sha = write_result(args.output, result)
    if args.second_output is not None:
        second_sha = write_result(args.second_output, result)
        if second_sha != artifact_sha or Path(args.output).read_bytes() != Path(args.second_output).read_bytes():
            raise RuntimeError("canonical result serialization is not byte-identical")

    print(f"execution_git_commit={result['execution_git_commit']}")
    print(f"c83a_protocol_fingerprint={result['c83a_protocol_fingerprint']}")
    print(f"scientific_fingerprint={result['scientific_fingerprint']}")
    print(f"artifact_sha256={artifact_sha}")
    print("gate_g3_performance_inspection=NONE")
    print("summary=" + _canonical_json_for_cli(result["summary"]))


def _canonical_json_for_cli(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


if __name__ == "__main__":
    main()
