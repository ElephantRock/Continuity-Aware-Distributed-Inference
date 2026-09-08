from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Iterable

from simulator.calibration_source import (
    CalibrationArtifactRole,
    VIDUR_CALIBRATION_SOURCES,
)
from simulator.calibration_validation import ReferenceKind, VIDUR_PINNED_COMMIT
from simulator.inference_cost_v2 import (
    C64A_FRESH_PREFILL_AXES,
    C64A_FRESH_TRANSFER_AXES,
)


C64C_BOUNDARY_IDENTITY_SCHEMA = "cadi.c6.4c.boundary-identity-selection.v1"
C64C_BOUNDARY_ALGORITHM_ID = "sha256-four-per-equal-width-quartile-v1"
C64C_BOUNDARY_SEED = (
    "cadi.c6.4c.boundary.v1|291fa8f9bd5c7d0e72e46163715cb04bd772d205"
)
C64C_DOMAIN_MIN = 1
C64C_DOMAIN_MAX = 4096
C64C_TRANSFER_BYTES_PER_TOKEN = 2 * 4096
C64C_POINTS_PER_STRATUM = 4
C64C_STRATA = ((1, 1024), (1025, 2048), (2049, 3072), (3073, 4096))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _require_clean_pinned_checkout(vidur_root: Path) -> None:
    status = _git(vidur_root, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise ValueError(f"Vidur worktree must be clean: status={status!r}")
    head = _git(vidur_root, "rev-parse", "HEAD")
    if head != VIDUR_PINNED_COMMIT:
        raise ValueError(
            f"Vidur HEAD drift: expected={VIDUR_PINNED_COMMIT}, observed={head}"
        )
    for source in VIDUR_CALIBRATION_SOURCES:
        for artifact in source.artifacts:
            observed = _git(vidur_root, "rev-parse", f"HEAD:{artifact.path}")
            if observed != artifact.git_blob_sha1:
                raise ValueError(
                    f"source blob drift for {source.hardware_id}/{artifact.path}: "
                    f"expected={artifact.git_blob_sha1}, observed={observed}"
                )


def _artifact_path(vidur_root: Path, hardware_id: str, role: CalibrationArtifactRole) -> Path:
    source = next(
        item for item in VIDUR_CALIBRATION_SOURCES if item.hardware_id == hardware_id
    )
    artifact = next(item for item in source.artifacts if item.role is role)
    return vidur_root / artifact.path


def _identity_rows(path: Path, columns: tuple[str, ...]) -> Iterable[dict[str, str]]:
    """Yield only requested identity fields; timing columns are never inspected."""

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        missing = sorted(set(columns) - set(reader.fieldnames))
        if missing:
            raise ValueError(f"CSV identity columns missing from {path}: {missing}")
        for row in reader:
            yield {name: row[name] for name in columns}


def _integer(row: dict[str, str], name: str) -> int:
    return int(float(row[name]))


def _boolean(row: dict[str, str], name: str) -> bool:
    value = row[name].strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"invalid boolean identity {name}={row[name]!r}")


def _prefill_history_axes(vidur_root: Path, hardware_id: str) -> tuple[int, ...]:
    attention_columns = (
        "n_embd",
        "n_q_head",
        "n_kv_head",
        "block_size",
        "num_tensor_parallel_workers",
        "max_model_len",
        "attention_backend",
        "batch_size",
        "kv_cache_size",
        "prefill_chunk_size",
        "is_prefill",
    )
    mlp_columns = (
        "n_head",
        "n_kv_head",
        "n_embd",
        "n_expanded_embd",
        "vocab_size",
        "use_gated_mlp",
        "num_tensor_parallel_workers",
        "num_tokens",
    )
    attention_path = _artifact_path(
        vidur_root, hardware_id, CalibrationArtifactRole.COMPUTE_ATTENTION
    )
    mlp_path = _artifact_path(vidur_root, hardware_id, CalibrationArtifactRole.COMPUTE_MLP)

    mlp_axes: set[int] = set()
    for row in _identity_rows(mlp_path, mlp_columns):
        if not (
            _integer(row, "n_head") == 32
            and _integer(row, "n_kv_head") == 32
            and _integer(row, "n_embd") == 4096
            and _integer(row, "n_expanded_embd") == 11008
            and _integer(row, "vocab_size") == 32768
            and _boolean(row, "use_gated_mlp")
            and _integer(row, "num_tensor_parallel_workers") == 1
        ):
            continue
        mlp_axes.add(_integer(row, "num_tokens"))

    attention_axes: set[int] = set()
    for row in _identity_rows(attention_path, attention_columns):
        if not (
            _integer(row, "n_embd") == 4096
            and _integer(row, "n_q_head") == 32
            and _integer(row, "n_kv_head") == 32
            and _integer(row, "block_size") == 16
            and _integer(row, "num_tensor_parallel_workers") == 1
            and _integer(row, "max_model_len") == 4096
            and row["attention_backend"].strip().endswith("FLASH_ATTENTION")
            and _integer(row, "batch_size") == 1
            and _integer(row, "kv_cache_size") == 0
            and _integer(row, "prefill_chunk_size") > 0
            and _boolean(row, "is_prefill")
        ):
            continue
        attention_axes.add(_integer(row, "prefill_chunk_size"))

    axes = tuple(sorted(attention_axes & mlp_axes))
    if len(axes) != 81:
        raise ValueError(
            f"unexpected C6.3 cold-prefill identity count for {hardware_id}: {len(axes)}"
        )
    return axes


def _transfer_history_axes(vidur_root: Path, hardware_id: str) -> tuple[int, ...]:
    columns = ("collective", "rank", "num_workers", "devices_per_node", "size")
    path = _artifact_path(
        vidur_root, hardware_id, CalibrationArtifactRole.NETWORK_SEND_RECV
    )
    axes: set[int] = set()
    for row in _identity_rows(path, columns):
        if not (
            row["collective"].strip() == "send_recv"
            and _integer(row, "rank") == 0
            and _integer(row, "num_workers") == 2
            and _integer(row, "devices_per_node") == 2
        ):
            continue
        axes.add(_integer(row, "size"))
    ordered = tuple(sorted(axes))
    if len(ordered) != 993:
        raise ValueError(
            f"unexpected C6.3 transfer identity count for {hardware_id}: {len(ordered)}"
        )
    return ordered


def _history_identity(hardware_id: str, kind: ReferenceKind, axes: tuple[int, ...]) -> dict[str, object]:
    payload = {
        "hardware_id": hardware_id,
        "reference_kind": kind.value,
        "axes": list(axes),
    }
    return {
        "hardware_id": hardware_id,
        "reference_kind": kind.value,
        "axis_count": len(axes),
        "axis_identity_sha256": _sha256_json(payload),
    }


def _rank(kind: ReferenceKind, axis: int) -> str:
    material = f"{C64C_BOUNDARY_SEED}|{kind.value}|{axis}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def select_boundary_axes(
    kind: ReferenceKind,
    historical_public_axes: set[int],
    previous_public_axes: set[int],
) -> tuple[int, ...]:
    if kind is ReferenceKind.COLD_PREFILL:
        to_public = lambda value: value
    elif kind is ReferenceKind.POINT_TO_POINT_TRANSFER:
        to_public = lambda value: value * C64C_TRANSFER_BYTES_PER_TOKEN
    else:
        raise ValueError("C6.4c boundary exists only for prefill and transfer")

    selected: list[int] = []
    for lower, upper in C64C_STRATA:
        candidates: list[int] = []
        for identity_axis in range(lower, upper + 1):
            public_axis = to_public(identity_axis)
            if public_axis in historical_public_axes or public_axis in previous_public_axes:
                continue
            candidates.append(identity_axis)
        if len(candidates) < C64C_POINTS_PER_STRATUM:
            raise ValueError(
                f"insufficient timing-blind candidates for {kind.value} stratum "
                f"[{lower},{upper}]"
            )
        chosen = sorted(candidates, key=lambda axis: (_rank(kind, axis), axis))[
            :C64C_POINTS_PER_STRATUM
        ]
        selected.extend(to_public(axis) for axis in chosen)
    result = tuple(sorted(selected))
    if len(result) != C64C_POINTS_PER_STRATUM * len(C64C_STRATA):
        raise AssertionError("C6.4c boundary selector produced wrong point count")
    if set(result) & historical_public_axes:
        raise AssertionError("C6.4c boundary collided with C6.3 history")
    if set(result) & previous_public_axes:
        raise AssertionError("C6.4c boundary collided with C6.4a history")
    return result


def build_identity_only_boundary(vidur_root: Path) -> dict[str, object]:
    _require_clean_pinned_checkout(vidur_root)
    hardware_ids = tuple(sorted(source.hardware_id for source in VIDUR_CALIBRATION_SOURCES))

    histories: list[dict[str, object]] = []
    prefill_union: set[int] = set()
    transfer_union: set[int] = set()
    for hardware_id in hardware_ids:
        prefill = _prefill_history_axes(vidur_root, hardware_id)
        transfer = _transfer_history_axes(vidur_root, hardware_id)
        histories.append(_history_identity(hardware_id, ReferenceKind.COLD_PREFILL, prefill))
        histories.append(
            _history_identity(hardware_id, ReferenceKind.POINT_TO_POINT_TRANSFER, transfer)
        )
        prefill_union.update(prefill)
        transfer_union.update(transfer)

    prefill_axes = select_boundary_axes(
        ReferenceKind.COLD_PREFILL,
        prefill_union,
        set(C64A_FRESH_PREFILL_AXES),
    )
    transfer_axes = select_boundary_axes(
        ReferenceKind.POINT_TO_POINT_TRANSFER,
        transfer_union,
        set(C64A_FRESH_TRANSFER_AXES),
    )

    prefill_union_payload = {
        "reference_kind": ReferenceKind.COLD_PREFILL.value,
        "axes": sorted(prefill_union),
    }
    transfer_union_payload = {
        "reference_kind": ReferenceKind.POINT_TO_POINT_TRANSFER.value,
        "axes": sorted(transfer_union),
    }
    return {
        "schema": C64C_BOUNDARY_IDENTITY_SCHEMA,
        "selection_algorithm_id": C64C_BOUNDARY_ALGORITHM_ID,
        "selection_seed": C64C_BOUNDARY_SEED,
        "source_commit": VIDUR_PINNED_COMMIT,
        "contains_reference_timings": False,
        "history_identities": sorted(
            histories, key=lambda item: (str(item["hardware_id"]), str(item["reference_kind"]))
        ),
        "history_union": {
            "prefill_axis_count": len(prefill_union),
            "prefill_axis_identity_sha256": _sha256_json(prefill_union_payload),
            "transfer_axis_count": len(transfer_union),
            "transfer_axis_identity_sha256": _sha256_json(transfer_union_payload),
            "previous_prefill_axes": list(C64A_FRESH_PREFILL_AXES),
            "previous_transfer_axes": list(C64A_FRESH_TRANSFER_AXES),
        },
        "candidate_domain": {
            "prefill_input_tokens": [C64C_DOMAIN_MIN, C64C_DOMAIN_MAX],
            "transfer_predictor_tokens": [C64C_DOMAIN_MIN, C64C_DOMAIN_MAX],
            "transfer_bytes_per_predictor_token": C64C_TRANSFER_BYTES_PER_TOKEN,
            "strata": [list(item) for item in C64C_STRATA],
            "points_per_stratum": C64C_POINTS_PER_STRATUM,
        },
        "fresh_prefill_axes_input_tokens": list(prefill_axes),
        "fresh_transfer_axes_bytes": list(transfer_axes),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vidur-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_identity_only_boundary(args.vidur_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_canonical_json(result) + "\n", encoding="utf-8")
    print(_canonical_json(result))


if __name__ == "__main__":
    main()
