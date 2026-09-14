from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from experiments.c7_protocol import (
    C6_ARTIFACT_SHA256,
    C6_EVIDENCE_CLASS,
    C6_SCIENTIFIC_FINGERPRINT,
    C7_PROTOCOL_FINGERPRINT,
    C7_STOCHASTIC_SEEDS,
)
from experiments.c72_routing_reuse import derive_pinned_mooncake_c7_admissible, evaluate_paired_program
from experiments.c75_g2_execute import (
    C75B_HARDWARE_STRATA,
    C75AlwaysValidAuthority,
    C75ProgramRow,
    build_manifests_for_case,
    build_p1_program_case,
    build_p4_program_case,
    build_p7_program_case,
)
from experiments.c75_g2_finalize import expected_program_row_count, finalize_from_rows
from experiments.c75_g2_protocol import (
    C75_C72_ADMISSIBLE_DATASET_FINGERPRINT,
    C75_PROTOCOL_FINGERPRINT,
    C75_SOURCE_SELECTION_FINGERPRINT,
    p1_cells,
    p4_cells,
    p7_control_cells,
)
from experiments.c75_g2_stage2_contract import (
    frozen_source_records,
    source_record_for_seed,
    summarize_verified_paired_result,
    validate_result_rows_against_frozen_mapping,
)
from experiments.mooncake_trace import load_pinned_mooncake_trace
from simulator.continuity_policy import build_baseline_policies
from simulator.inference_cost_runtime import load_c64f_runtime_profiles


C75B_STAGE1_REVIEW_SHA = "6aeb5a3147951645a270088aac0647c03084a73c"
C75B_RUNNER_SCHEMA = "cadi.c7.5b.frozen-sweep-runner.v1"
C75B_RESULT_ARTIFACT_SCHEMA = "cadi.c7.5b.g2-result-artifact.v1"
C75B_COMPARATIVE_EXECUTION_READY = "READY_NOT_RUN"
C75B_COMPARATIVE_EXECUTION_COMPLETE = "COMPLETE"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _execution_sha(value: str) -> str:
    if not isinstance(value, str) or _SHA40.fullmatch(value) is None:
        raise ValueError("execution Git SHA must be lowercase 40-hex")
    return value


def execution_plan() -> dict[str, Any]:
    p1 = p1_cells()
    p4 = p4_cells()
    p7 = p7_control_cells()
    programs = (len(p1) + len(p4) + len(p7)) * len(C7_STOCHASTIC_SEEDS) * len(C75B_HARDWARE_STRATA)
    return {
        "schema": C75B_RUNNER_SCHEMA,
        "stage1_review_sha": C75B_STAGE1_REVIEW_SHA,
        "c7_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
        "c75_protocol_fingerprint": C75_PROTOCOL_FINGERPRINT,
        "source_selection_fingerprint": C75_SOURCE_SELECTION_FINGERPRINT,
        "c6_scientific_fingerprint": C6_SCIENTIFIC_FINGERPRINT,
        "c6_artifact_sha256": C6_ARTIFACT_SHA256,
        "c6_evidence_class": C6_EVIDENCE_CLASS,
        "hardware_strata": list(C75B_HARDWARE_STRATA),
        "seed_count": len(C7_STOCHASTIC_SEEDS),
        "p1_cells": len(p1),
        "p4_cells": len(p4),
        "p7_cells": len(p7),
        "paired_program_evaluations": programs,
        "program_row_count": expected_program_row_count(),
        "comparative_execution": C75B_COMPARATIVE_EXECUTION_READY,
    }


def _rows_sha256(rows: Iterable[C75ProgramRow]) -> str:
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        if not isinstance(row, C75ProgramRow):
            raise TypeError("rows must contain C75ProgramRow values")
        digest.update(_json(row.to_dict()).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    if count != expected_program_row_count():
        raise ValueError("program-row hash requires the complete frozen result set")
    return digest.hexdigest()


def _evaluate_case(
    *,
    case,
    record,
    seed: int,
    selected,
    hardware_id: str,
    execution_git_sha: str,
    profile,
) -> tuple[C75ProgramRow, ...]:
    manifests = build_manifests_for_case(
        case,
        record,
        seed=seed,
        hardware_id=hardware_id,
        execution_git_sha=execution_git_sha,
    )
    authority = C75AlwaysValidAuthority()
    paired = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=manifests,
        case=case,
        oracle=authority,
        profile=profile,
    )
    return summarize_verified_paired_result(
        case=case,
        paired=paired,
        manifests=manifests,
        hardware_id=hardware_id,
        seed=seed,
        selected_source_records=selected,
        source_record=record,
    )


def execute_frozen_sweep(
    *, source_bytes: bytes, execution_git_sha: str, c64f_artifact: Path
) -> dict[str, Any]:
    execution_git_sha = _execution_sha(execution_git_sha)
    source = load_pinned_mooncake_trace(source_bytes)
    admissible = derive_pinned_mooncake_c7_admissible(source)
    if admissible.fingerprint != C75_C72_ADMISSIBLE_DATASET_FINGERPRINT:
        raise ValueError("C7.5b admissible Mooncake dataset fingerprint drift")
    selected = frozen_source_records(
        admissible.source_order,
        admissible_dataset_fingerprint=admissible.fingerprint,
    )
    profiles = load_c64f_runtime_profiles(c64f_artifact)
    if tuple(sorted(profiles)) != tuple(sorted(C75B_HARDWARE_STRATA)):
        raise ValueError("C6 runtime profile hardware strata drift")

    rows: list[C75ProgramRow] = []

    for depth, fraction, workers in p1_cells():
        for seed in C7_STOCHASTIC_SEEDS:
            record = source_record_for_seed(selected, seed)
            case = build_p1_program_case(
                record,
                seed=seed,
                session_depth=depth,
                reusable_prefix_fraction=fraction,
                worker_count=workers,
            )
            for hardware_id in C75B_HARDWARE_STRATA:
                rows.extend(
                    _evaluate_case(
                        case=case,
                        record=record,
                        seed=seed,
                        selected=selected,
                        hardware_id=hardware_id,
                        execution_git_sha=execution_git_sha,
                        profile=profiles[hardware_id],
                    )
                )

    for width, fraction in p4_cells():
        for seed in C7_STOCHASTIC_SEEDS:
            record = source_record_for_seed(selected, seed)
            case = build_p4_program_case(
                record,
                seed=seed,
                fanout_width=width,
                shared_prefix_fraction=fraction,
            )
            for hardware_id in C75B_HARDWARE_STRATA:
                rows.extend(
                    _evaluate_case(
                        case=case,
                        record=record,
                        seed=seed,
                        selected=selected,
                        hardware_id=hardware_id,
                        execution_git_sha=execution_git_sha,
                        profile=profiles[hardware_id],
                    )
                )

    for workers in p7_control_cells():
        for seed in C7_STOCHASTIC_SEEDS:
            record = source_record_for_seed(selected, seed)
            case = build_p7_program_case(record, seed=seed, worker_count=workers)
            for hardware_id in C75B_HARDWARE_STRATA:
                rows.extend(
                    _evaluate_case(
                        case=case,
                        record=record,
                        seed=seed,
                        selected=selected,
                        hardware_id=hardware_id,
                        execution_git_sha=execution_git_sha,
                        profile=profiles[hardware_id],
                    )
                )

    result_rows = tuple(rows)
    if len(result_rows) != expected_program_row_count():
        raise AssertionError("frozen sweep did not produce the exact expected Program-row count")
    validate_result_rows_against_frozen_mapping(result_rows, selected)
    final = finalize_from_rows(result_rows)
    program_rows_sha256 = _rows_sha256(result_rows)
    final_payload = final.to_dict()
    scientific_core = {
        "c7_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
        "c75_protocol_fingerprint": C75_PROTOCOL_FINGERPRINT,
        "source_selection_fingerprint": C75_SOURCE_SELECTION_FINGERPRINT,
        "c6_scientific_fingerprint": C6_SCIENTIFIC_FINGERPRINT,
        "c6_evidence_class": C6_EVIDENCE_CLASS,
        "program_row_count": len(result_rows),
        "program_rows_sha256": program_rows_sha256,
        "final_adjudication": final_payload,
    }
    scientific_fingerprint = _sha256(scientific_core)
    artifact = {
        "schema": C75B_RESULT_ARTIFACT_SCHEMA,
        "runner_schema": C75B_RUNNER_SCHEMA,
        "stage1_review_sha": C75B_STAGE1_REVIEW_SHA,
        "execution_git_sha": execution_git_sha,
        "comparative_execution": C75B_COMPARATIVE_EXECUTION_COMPLETE,
        "c7_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
        "c75_protocol_fingerprint": C75_PROTOCOL_FINGERPRINT,
        "source_selection_fingerprint": C75_SOURCE_SELECTION_FINGERPRINT,
        "c6_scientific_fingerprint": C6_SCIENTIFIC_FINGERPRINT,
        "c6_artifact_sha256": C6_ARTIFACT_SHA256,
        "c6_evidence_class": C6_EVIDENCE_CLASS,
        "program_row_count": len(result_rows),
        "program_rows_sha256": program_rows_sha256,
        "scientific_fingerprint": scientific_fingerprint,
        "final_adjudication": final_payload,
    }
    return artifact


def write_artifact(path: Path, artifact: dict[str, Any]) -> tuple[str, int]:
    data = (_json(artifact) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), len(data)


def compact_summary(artifact: dict[str, Any], *, artifact_sha256: str, artifact_bytes: int) -> dict[str, Any]:
    final = artifact["final_adjudication"]
    h4 = final["h4"]
    h7 = final["h7"]
    return {
        "schema": "cadi.c7.5b.g2-result-summary.v1",
        "comparative_execution": artifact["comparative_execution"],
        "execution_git_sha": artifact["execution_git_sha"],
        "program_row_count": artifact["program_row_count"],
        "program_rows_sha256": artifact["program_rows_sha256"],
        "scientific_fingerprint": artifact["scientific_fingerprint"],
        "artifact_sha256": artifact_sha256,
        "artifact_bytes": artifact_bytes,
        "h4_decision": h4["decision"],
        "h4_qualifying_regions": h4["qualifying_regions"],
        "h7_decision": h7["decision"],
        "h7_p7_pass": h7["p7_pass"],
        "h7_qualifying_runs": h7["qualifying_runs"],
        "gate_g2": final["gate_g2"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source")
    parser.add_argument("--c64f-artifact")
    parser.add_argument("--execution-sha")
    parser.add_argument("--output")
    args = parser.parse_args()
    if not args.execute:
        print(_json(execution_plan()))
        return
    required = {
        "--source": args.source,
        "--c64f-artifact": args.c64f_artifact,
        "--execution-sha": args.execution_sha,
        "--output": args.output,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        parser.error("--execute requires " + ", ".join(missing))
    artifact = execute_frozen_sweep(
        source_bytes=Path(args.source).read_bytes(),
        execution_git_sha=args.execution_sha,
        c64f_artifact=Path(args.c64f_artifact),
    )
    artifact_sha256, artifact_bytes = write_artifact(Path(args.output), artifact)
    print(_json(compact_summary(artifact, artifact_sha256=artifact_sha256, artifact_bytes=artifact_bytes)))


if __name__ == "__main__":
    main()
