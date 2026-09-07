from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import io
import json
import math
from typing import Iterable, Mapping, Sequence

from .calibration_source import (
    CalibrationArtifactRole,
    VIDUR_CALIBRATION_SOURCES,
)
from .calibration_validation import (
    VIDUR_PINNED_COMMIT,
    VIDUR_PINNED_REPOSITORY,
    CalibrationReferencePoint,
    ReferenceKind,
    ReferencePartition,
    SourceArtifactRef,
    VidurComputeComponents,
    compose_vidur_llama2_7b_tp1_model_seconds,
    split_reference_family,
)


C6_PROJECTION_SCHEMA = "cadi.c6.vidur-projection-fit.v1"
C6_PROJECTION_AGGREGATION = "equal-weight-mean-of-distinct-source-row-medians-v1"

# Physical file-byte fingerprints measured before any fit in source-probe workflow
# 34157422899. Git blob identities remain authoritative in C6.2 manifests; these
# SHA-256 values independently fence the downloaded raw bytes used by C6.3b.
VIDUR_SOURCE_SHA256: dict[str, dict[str, str]] = {
    "a100-80gb": {
        "attention": "4e3ddcf049d5fcc3b86732523a126d7f49a07d12b17b489bf2a560d6dd9ecbf3",
        "mlp": "2cfacca979f32e83257753cf6fd815a71af8ba9a7a9d0643b18d9f54e8a37688",
        "send_recv": "7e5968cf616af8208a1420816c4ffba9ee857cd989148ae54ebf46239e477a72",
    },
    "h100-80gb": {
        "attention": "6b44955921aab09b2f367dce876a503df934b11bfde667e6847b1d437a0969d0",
        "mlp": "b1a2def08857d10e5f66c3b5b2d046b6e8caed5c30b2bc920a0462598e3b2a59",
        "send_recv": "605b758cd4791cbbd1fab8d5d990463b237346e9365823d768299368fc50c4c9",
    },
}


_MLP_COMPONENT_COLUMNS = (
    "time_stats.attn_pre_proj.median",
    "time_stats.attn_post_proj.median",
    "time_stats.attn_rope.median",
    "time_stats.input_layernorm.median",
    "time_stats.post_attention_layernorm.median",
    "time_stats.mlp_up_proj.median",
    "time_stats.mlp_down_proj.median",
    "time_stats.mlp_act.median",
    "time_stats.add.median",
)


@dataclass(frozen=True, slots=True)
class ProjectionReference:
    point: CalibrationReferencePoint
    mlp_replicates: int
    attention_replicates: int
    transfer_replicates: int

    def __post_init__(self) -> None:
        if not isinstance(self.point, CalibrationReferencePoint):
            raise TypeError("point must be CalibrationReferencePoint")
        for name in ("mlp_replicates", "attention_replicates", "transfer_replicates"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.point.kind is ReferenceKind.POINT_TO_POINT_TRANSFER:
            if self.transfer_replicates <= 0 or self.mlp_replicates or self.attention_replicates:
                raise ValueError("transfer reference must carry only transfer replicates")
        else:
            if self.mlp_replicates <= 0 or self.attention_replicates <= 0:
                raise ValueError("compute reference requires MLP and attention replicates")
            if self.transfer_replicates:
                raise ValueError("compute reference cannot carry transfer replicates")

    def to_dict(self) -> dict[str, object]:
        return {
            "point": self.point.to_dict(),
            "replicates": {
                "mlp": self.mlp_replicates,
                "attention": self.attention_replicates,
                "transfer": self.transfer_replicates,
            },
        }


@dataclass(frozen=True, slots=True)
class AffineFit:
    hardware_id: str
    kind: ReferenceKind
    intercept_seconds: float
    slope_seconds_per_axis_unit: float
    fit_point_ids: tuple[str, ...]
    validation_point_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.hardware_id not in VIDUR_SOURCE_SHA256:
            raise ValueError("hardware_id is outside the frozen C6.3b domain")
        if not isinstance(self.kind, ReferenceKind):
            raise TypeError("kind must be ReferenceKind")
        intercept = _finite_nonnegative(self.intercept_seconds, "intercept_seconds")
        slope = _finite_nonnegative(
            self.slope_seconds_per_axis_unit, "slope_seconds_per_axis_unit"
        )
        if slope == 0:
            raise ValueError("affine slope must be strictly positive")
        if len(self.fit_point_ids) < 2 or len(self.validation_point_ids) < 2:
            raise ValueError("fit requires at least two FIT and two VALIDATION IDs")
        if len(set(self.fit_point_ids + self.validation_point_ids)) != (
            len(self.fit_point_ids) + len(self.validation_point_ids)
        ):
            raise ValueError("fit/validation point IDs must be unique")
        object.__setattr__(self, "intercept_seconds", intercept)
        object.__setattr__(self, "slope_seconds_per_axis_unit", slope)

    @property
    def c6_mapping(self) -> dict[str, float]:
        if self.kind is ReferenceKind.COLD_PREFILL:
            return {
                "prefill_fixed_seconds": self.intercept_seconds,
                "prefill_seconds_per_input_token": self.slope_seconds_per_axis_unit,
            }
        if self.kind is ReferenceKind.SINGLE_TOKEN_DECODE:
            return {
                "decode_fixed_seconds": self.intercept_seconds,
                "decode_seconds_per_context_token_step": self.slope_seconds_per_axis_unit,
            }
        return {
            "transfer_latency_seconds": self.intercept_seconds,
            "transfer_bandwidth_bytes_per_second": 1.0
            / self.slope_seconds_per_axis_unit,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "hardware_id": self.hardware_id,
            "kind": self.kind.value,
            "intercept_seconds": self.intercept_seconds,
            "slope_seconds_per_axis_unit": self.slope_seconds_per_axis_unit,
            "fit_point_ids": list(self.fit_point_ids),
            "validation_point_ids": list(self.validation_point_ids),
            "c6_mapping": self.c6_mapping,
        }


@dataclass(frozen=True, slots=True)
class ProjectionBundle:
    hardware_id: str
    source_sha256: Mapping[str, str]
    references: tuple[ProjectionReference, ...]
    fits: tuple[AffineFit, ...]

    def __post_init__(self) -> None:
        if self.hardware_id not in VIDUR_SOURCE_SHA256:
            raise ValueError("hardware_id is outside the frozen C6.3b domain")
        observed = dict(self.source_sha256)
        expected = VIDUR_SOURCE_SHA256[self.hardware_id]
        if observed != expected:
            raise ValueError(
                f"source SHA-256 snapshot mismatch for {self.hardware_id}: "
                f"expected={expected}, observed={observed}"
            )
        if not self.references:
            raise ValueError("projection bundle requires references")
        kinds = {reference.point.kind for reference in self.references}
        if kinds != set(ReferenceKind):
            raise ValueError("projection bundle must contain all three reference families")
        fit_kinds = [fit.kind for fit in self.fits]
        if len(self.fits) != len(ReferenceKind) or len(set(fit_kinds)) != len(ReferenceKind):
            raise ValueError("projection bundle must contain exactly one fit per family")
        if any(
            reference.point.hardware_id != self.hardware_id
            for reference in self.references
        ):
            raise ValueError("all references must match bundle hardware")
        if any(fit.hardware_id != self.hardware_id for fit in self.fits):
            raise ValueError("all fits must match bundle hardware")

    @property
    def corpus_fingerprint(self) -> str:
        payload = {
            "hardware_id": self.hardware_id,
            "source_sha256": dict(sorted(self.source_sha256.items())),
            "aggregation": C6_PROJECTION_AGGREGATION,
            "references": [item.to_dict() for item in self.references],
        }
        return _sha256_json(payload)

    @property
    def fit_fingerprint(self) -> str:
        payload = {
            "hardware_id": self.hardware_id,
            "corpus_fingerprint": self.corpus_fingerprint,
            "fits": [fit.to_dict() for fit in self.fits],
        }
        return _sha256_json(payload)

    def to_dict(self) -> dict[str, object]:
        source = _source_manifest(self.hardware_id)
        return {
            "schema": C6_PROJECTION_SCHEMA,
            "hardware_id": self.hardware_id,
            "source_repository": VIDUR_PINNED_REPOSITORY,
            "source_commit": VIDUR_PINNED_COMMIT,
            "source_manifest_id": source.source_id,
            "source_sha256": dict(sorted(self.source_sha256.items())),
            "aggregation": C6_PROJECTION_AGGREGATION,
            "references": [reference.to_dict() for reference in self.references],
            "fits": [fit.to_dict() for fit in self.fits],
            "corpus_fingerprint": self.corpus_fingerprint,
            "fit_fingerprint": self.fit_fingerprint,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )


def build_pinned_vidur_projection(
    hardware_id: str,
    *,
    attention_bytes: bytes,
    mlp_bytes: bytes,
    send_recv_bytes: bytes,
) -> ProjectionBundle:
    """Derive and fit one exact C6.3b hardware projection.

    Raw byte hashes are checked before parsing. Held-out validation values are
    never consumed by the fitter; only the canonical C6.3a FIT partition enters
    each OLS calculation.
    """

    if hardware_id not in VIDUR_SOURCE_SHA256:
        raise ValueError("hardware_id is outside the frozen C6.3b domain")
    blobs = {
        "attention": _require_bytes(attention_bytes, "attention_bytes"),
        "mlp": _require_bytes(mlp_bytes, "mlp_bytes"),
        "send_recv": _require_bytes(send_recv_bytes, "send_recv_bytes"),
    }
    observed_sha256 = {
        name: hashlib.sha256(content).hexdigest() for name, content in blobs.items()
    }
    expected = VIDUR_SOURCE_SHA256[hardware_id]
    if observed_sha256 != expected:
        raise ValueError(
            f"source SHA-256 snapshot mismatch for {hardware_id}: "
            f"expected={expected}, observed={observed_sha256}"
        )

    references = derive_vidur_reference_corpus(
        hardware_id,
        attention_csv=blobs["attention"].decode("utf-8-sig"),
        mlp_csv=blobs["mlp"].decode("utf-8-sig"),
        send_recv_csv=blobs["send_recv"].decode("utf-8-sig"),
    )
    fits = tuple(
        fit_affine_reference_family(
            split_reference_family(
                [reference.point for reference in references if reference.point.kind is kind]
            )
        )
        for kind in ReferenceKind
    )
    return ProjectionBundle(
        hardware_id=hardware_id,
        source_sha256=observed_sha256,
        references=references,
        fits=fits,
    )


def derive_vidur_reference_corpus(
    hardware_id: str,
    *,
    attention_csv: str,
    mlp_csv: str,
    send_recv_csv: str,
) -> tuple[ProjectionReference, ...]:
    """Project raw Vidur profiling rows into unique-axis C6.3a references."""

    if hardware_id not in VIDUR_SOURCE_SHA256:
        raise ValueError("hardware_id is outside the frozen C6.3b domain")
    attention_rows = _drop_exact_duplicate_rows(_read_csv(attention_csv, "attention_csv"))
    mlp_rows = _drop_exact_duplicate_rows(_read_csv(mlp_csv, "mlp_csv"))
    send_rows = _drop_exact_duplicate_rows(_read_csv(send_recv_csv, "send_recv_csv"))

    mlp_domain = [row for row in mlp_rows if _is_mlp_domain_row(row)]
    attention_domain = [
        row for row in attention_rows if _is_attention_domain_row(row)
    ]
    cold_rows = [
        row
        for row in attention_domain
        if _int(row, "batch_size") == 1
        and _int(row, "kv_cache_size") == 0
        and _int(row, "prefill_chunk_size") > 0
        and _bool(row, "is_prefill")
    ]
    decode_rows = [
        row
        for row in attention_domain
        if _int(row, "batch_size") == 1
        and _int(row, "prefill_chunk_size") == 0
        and _int(row, "kv_cache_size") > 0
        and not _bool(row, "is_prefill")
    ]
    transfer_rows = [
        row
        for row in send_rows
        if _text(row, "collective") == "send_recv"
        and _int(row, "rank") == 0
        and _int(row, "num_workers") == 2
        and _int(row, "devices_per_node") == 2
    ]

    if not mlp_domain or not cold_rows or not decode_rows or not transfer_rows:
        raise ValueError("one or more C6.3b source domains are empty")

    mlp_by_tokens = _group_by_int(mlp_domain, "num_tokens")
    cold_by_axis = _group_by_int(cold_rows, "prefill_chunk_size")
    decode_by_axis = _group_by_int(decode_rows, "kv_cache_size")
    transfer_by_axis = _group_by_int(transfer_rows, "size")

    if 1 not in mlp_by_tokens:
        raise ValueError("single-token decode requires MLP num_tokens=1")

    compute_artifacts = _artifacts_for_kind(hardware_id, ReferenceKind.COLD_PREFILL)
    transfer_artifacts = _artifacts_for_kind(
        hardware_id, ReferenceKind.POINT_TO_POINT_TRANSFER
    )

    projected: list[ProjectionReference] = []
    for axis in sorted(cold_by_axis):
        if axis not in mlp_by_tokens:
            raise ValueError(f"cold-prefill axis {axis} has no matching MLP row")
        attention_group = cold_by_axis[axis]
        mlp_group = mlp_by_tokens[axis]
        components = _compose_components(
            mlp_group=mlp_group,
            attention_group=attention_group,
            attention_target="time_stats.attn_prefill.median",
        )
        projected.append(
            _compute_reference(
                hardware_id=hardware_id,
                kind=ReferenceKind.COLD_PREFILL,
                axis=axis,
                components=components,
                mlp_group=mlp_group,
                attention_group=attention_group,
                artifacts=compute_artifacts,
            )
        )

    decode_mlp_group = mlp_by_tokens[1]
    for axis in sorted(decode_by_axis):
        attention_group = decode_by_axis[axis]
        components = _compose_components(
            mlp_group=decode_mlp_group,
            attention_group=attention_group,
            attention_target="time_stats.attn_decode.median",
        )
        projected.append(
            _compute_reference(
                hardware_id=hardware_id,
                kind=ReferenceKind.SINGLE_TOKEN_DECODE,
                axis=axis,
                components=components,
                mlp_group=decode_mlp_group,
                attention_group=attention_group,
                artifacts=compute_artifacts,
            )
        )

    for axis in sorted(transfer_by_axis):
        group = transfer_by_axis[axis]
        observed_seconds = (
            _mean(_float(row, "time_stats.send_recv.median") for row in group) * 1e-3
        )
        derivation_id = _derivation_id(
            hardware_id=hardware_id,
            kind=ReferenceKind.POINT_TO_POINT_TRANSFER,
            axis=axis,
            mlp_rows=(),
            attention_rows=(),
            transfer_rows=group,
        )
        point = CalibrationReferencePoint(
            point_id=_point_id(
                hardware_id, ReferenceKind.POINT_TO_POINT_TRANSFER, axis
            ),
            hardware_id=hardware_id,
            kind=ReferenceKind.POINT_TO_POINT_TRANSFER,
            axis_value=axis,
            observed_seconds=observed_seconds,
            derivation_id=derivation_id,
            source_commit=VIDUR_PINNED_COMMIT,
            source_artifacts=transfer_artifacts,
        )
        projected.append(
            ProjectionReference(
                point=point,
                mlp_replicates=0,
                attention_replicates=0,
                transfer_replicates=len(group),
            )
        )

    projected.sort(
        key=lambda item: (
            tuple(ReferenceKind).index(item.point.kind),
            item.point.axis_value,
            item.point.point_id,
        )
    )
    for kind in ReferenceKind:
        family = [item.point for item in projected if item.point.kind is kind]
        # This both checks minimum family size and freezes canonical parity.
        split_reference_family(family)
    return tuple(projected)


def fit_affine_reference_family(partition: ReferencePartition) -> AffineFit:
    """Fit y=intercept+slope*x using only the canonical FIT points."""

    if not isinstance(partition, ReferencePartition):
        raise TypeError("partition must be ReferencePartition")
    xs = [float(point.axis_value) for point in partition.fit]
    ys = [point.observed_seconds for point in partition.fit]
    if len(set(xs)) < 2:
        raise ValueError("affine fit requires at least two distinct FIT axes")

    x_mean = math.fsum(xs) / len(xs)
    y_mean = math.fsum(ys) / len(ys)
    centered_x = [value - x_mean for value in xs]
    sxx = math.fsum(value * value for value in centered_x)
    if not math.isfinite(sxx) or sxx <= 0:
        raise ValueError("affine fit has non-positive x variance")
    sxy = math.fsum(
        dx * (y - y_mean) for dx, y in zip(centered_x, ys, strict=True)
    )
    slope = sxy / sxx
    intercept = y_mean - slope * x_mean

    if not math.isfinite(slope) or not math.isfinite(intercept):
        raise ValueError("affine fit produced a non-finite coefficient")
    if slope <= 0:
        raise ValueError(
            f"{partition.hardware_id}/{partition.kind.value} fit slope must be positive"
        )
    if intercept < 0:
        raise ValueError(
            f"{partition.hardware_id}/{partition.kind.value} fit intercept must be non-negative"
        )

    return AffineFit(
        hardware_id=partition.hardware_id,
        kind=partition.kind,
        intercept_seconds=intercept,
        slope_seconds_per_axis_unit=slope,
        fit_point_ids=tuple(point.point_id for point in partition.fit),
        validation_point_ids=tuple(point.point_id for point in partition.validation),
    )


def _compute_reference(
    *,
    hardware_id: str,
    kind: ReferenceKind,
    axis: int,
    components: VidurComputeComponents,
    mlp_group: Sequence[Mapping[str, str]],
    attention_group: Sequence[Mapping[str, str]],
    artifacts: tuple[SourceArtifactRef, ...],
) -> ProjectionReference:
    observed_seconds = compose_vidur_llama2_7b_tp1_model_seconds(components)
    derivation_id = _derivation_id(
        hardware_id=hardware_id,
        kind=kind,
        axis=axis,
        mlp_rows=mlp_group,
        attention_rows=attention_group,
        transfer_rows=(),
    )
    point = CalibrationReferencePoint(
        point_id=_point_id(hardware_id, kind, axis),
        hardware_id=hardware_id,
        kind=kind,
        axis_value=axis,
        observed_seconds=observed_seconds,
        derivation_id=derivation_id,
        source_commit=VIDUR_PINNED_COMMIT,
        source_artifacts=artifacts,
    )
    return ProjectionReference(
        point=point,
        mlp_replicates=len(mlp_group),
        attention_replicates=len(attention_group),
        transfer_replicates=0,
    )


def _compose_components(
    *,
    mlp_group: Sequence[Mapping[str, str]],
    attention_group: Sequence[Mapping[str, str]],
    attention_target: str,
) -> VidurComputeComponents:
    means = {
        column: _mean(_float(row, column) for row in mlp_group)
        for column in _MLP_COMPONENT_COLUMNS
    }
    attention_kernel = _mean(_float(row, attention_target) for row in attention_group)
    kv_cache_save = _mean(
        _optional_float(row, "time_stats.attn_kv_cache_save.median", default=0.0)
        for row in attention_group
    )
    return VidurComputeComponents(
        attn_pre_proj_ms=means["time_stats.attn_pre_proj.median"],
        attn_post_proj_ms=means["time_stats.attn_post_proj.median"],
        attn_rope_ms=means["time_stats.attn_rope.median"],
        attn_kv_cache_save_ms=kv_cache_save,
        attention_kernel_ms=attention_kernel,
        input_layernorm_ms=means["time_stats.input_layernorm.median"],
        post_attention_layernorm_ms=means[
            "time_stats.post_attention_layernorm.median"
        ],
        mlp_up_proj_ms=means["time_stats.mlp_up_proj.median"],
        mlp_down_proj_ms=means["time_stats.mlp_down_proj.median"],
        mlp_act_ms=means["time_stats.mlp_act.median"],
        add_ms=means["time_stats.add.median"],
    )


def _source_manifest(hardware_id: str):
    matches = [
        source for source in VIDUR_CALIBRATION_SOURCES if source.hardware_id == hardware_id
    ]
    if len(matches) != 1:
        raise RuntimeError("C6.2 manifest lookup must return exactly one source")
    return matches[0]


def _artifacts_for_kind(
    hardware_id: str, kind: ReferenceKind
) -> tuple[SourceArtifactRef, ...]:
    source = _source_manifest(hardware_id)
    if kind is ReferenceKind.POINT_TO_POINT_TRANSFER:
        roles = {CalibrationArtifactRole.NETWORK_SEND_RECV}
    else:
        roles = {
            CalibrationArtifactRole.MODEL_CONFIG,
            CalibrationArtifactRole.COMPUTE_ATTENTION,
            CalibrationArtifactRole.COMPUTE_MLP,
        }
    refs = tuple(
        SourceArtifactRef(path=item.path, git_blob_sha1=item.git_blob_sha1)
        for item in source.artifacts
        if item.role in roles
    )
    if len(refs) != len(roles):
        raise RuntimeError("C6.2 source manifest is missing a required projection role")
    return tuple(sorted(refs, key=lambda item: (item.path, item.git_blob_sha1)))


def _is_mlp_domain_row(row: Mapping[str, str]) -> bool:
    return (
        _int(row, "n_head") == 32
        and _int(row, "n_kv_head") == 32
        and _int(row, "n_embd") == 4096
        and _int(row, "n_expanded_embd") == 11008
        and _int(row, "vocab_size") == 32768
        and _bool(row, "use_gated_mlp")
        and _int(row, "num_tensor_parallel_workers") == 1
    )


def _is_attention_domain_row(row: Mapping[str, str]) -> bool:
    return (
        _int(row, "n_embd") == 4096
        and _int(row, "n_q_head") == 32
        and _int(row, "n_kv_head") == 32
        and _int(row, "block_size") == 16
        and _int(row, "num_tensor_parallel_workers") == 1
        and _int(row, "max_model_len") == 4096
        and _text(row, "attention_backend").endswith("FLASH_ATTENTION")
    )


def _read_csv(content: str, name: str) -> list[dict[str, str]]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"{name} must be non-empty CSV text")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError(f"{name} has no header")
    rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError(f"{name} has no data rows")
    return rows


def _drop_exact_duplicate_rows(
    rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for row in rows:
        canonical = json.dumps(
            dict(sorted(row.items())),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(dict(row))
    return result


def _group_by_int(
    rows: Sequence[Mapping[str, str]], column: str
) -> dict[int, tuple[Mapping[str, str], ...]]:
    groups: dict[int, list[Mapping[str, str]]] = {}
    for row in rows:
        groups.setdefault(_int(row, column), []).append(row)
    return {key: tuple(value) for key, value in groups.items()}


def _derivation_id(
    *,
    hardware_id: str,
    kind: ReferenceKind,
    axis: int,
    mlp_rows: Sequence[Mapping[str, str]],
    attention_rows: Sequence[Mapping[str, str]],
    transfer_rows: Sequence[Mapping[str, str]],
) -> str:
    payload = {
        "aggregation": C6_PROJECTION_AGGREGATION,
        "hardware_id": hardware_id,
        "kind": kind.value,
        "axis": axis,
        "mlp_rows": _canonical_rows(mlp_rows),
        "attention_rows": _canonical_rows(attention_rows),
        "transfer_rows": _canonical_rows(transfer_rows),
    }
    return f"vidur-c6.3b-v1:{_sha256_json(payload)}"


def _canonical_rows(
    rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    values = [dict(sorted(row.items())) for row in rows]
    return sorted(
        values,
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )


def _point_id(hardware_id: str, kind: ReferenceKind, axis: int) -> str:
    return f"c6.3b:{hardware_id}:{kind.value}:{axis}"


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mean(values: Iterable[float]) -> float:
    materialized = tuple(values)
    if not materialized:
        raise ValueError("cannot average an empty source replicate set")
    if not all(math.isfinite(value) and value >= 0 for value in materialized):
        raise ValueError("source timing replicates must be finite and non-negative")
    result = math.fsum(materialized) / len(materialized)
    if not math.isfinite(result) or result < 0:
        raise ValueError("source timing mean must be finite and non-negative")
    return result


def _text(row: Mapping[str, str], column: str) -> str:
    if column not in row or row[column] is None or not str(row[column]).strip():
        raise ValueError(f"missing required source column/value {column!r}")
    return str(row[column]).strip()


def _int(row: Mapping[str, str], column: str) -> int:
    text = _text(row, column)
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"source value {column!r} must be an integer") from exc
    return value


def _bool(row: Mapping[str, str], column: str) -> bool:
    text = _text(row, column).lower()
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"source value {column!r} must be True or False")


def _float(row: Mapping[str, str], column: str) -> float:
    text = _text(row, column)
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"source value {column!r} must be numeric") from exc
    return _finite_nonnegative(value, column)


def _optional_float(
    row: Mapping[str, str], column: str, *, default: float
) -> float:
    value = row.get(column)
    if value is None or not str(value).strip():
        return default
    try:
        parsed = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"source value {column!r} must be numeric") from exc
    return _finite_nonnegative(parsed, column)


def _finite_nonnegative(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def _require_bytes(value: bytes, name: str) -> bytes:
    if not isinstance(value, bytes) or not value:
        raise ValueError(f"{name} must be non-empty bytes")
    return value
