from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping


NORMALIZED_TRACE_SCHEMA = "cadi.c5.1.normalized-trace.v1"

_DATASET_KEYS = frozenset({"schema", "manifest", "records"})
_MANIFEST_KEYS = frozenset(
    {
        "source_id",
        "source_name",
        "source_uri",
        "source_version",
        "license_id",
        "source_sha256",
        "normalization_version",
        "normalization_steps",
        "field_origins",
    }
)
_RECORD_KEYS = frozenset(
    {
        "record_id",
        "source_record_id",
        "source_ordinal",
        "arrival_time_s",
        "input_tokens",
        "output_tokens",
        "prefix_group_id",
        "prefix_tokens",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TraceField(str, Enum):
    ARRIVAL_TIME_S = "arrival_time_s"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    PREFIX_GROUP_ID = "prefix_group_id"
    PREFIX_TOKENS = "prefix_tokens"


class TraceFieldOrigin(str, Enum):
    """Permitted provenance inside the source-derived trace layer.

    Synthetic Continuity structure is intentionally absent. Session, branch,
    tool-wait, retry, and fault augmentation belongs to a later C5 layer.
    """

    SOURCE_OBSERVED = "SOURCE_OBSERVED"
    TRACE_DERIVED = "TRACE_DERIVED"


_FIELD_ATTR: Mapping[TraceField, str] = {
    TraceField.ARRIVAL_TIME_S: "arrival_time_s",
    TraceField.INPUT_TOKENS: "input_tokens",
    TraceField.OUTPUT_TOKENS: "output_tokens",
    TraceField.PREFIX_GROUP_ID: "prefix_group_id",
    TraceField.PREFIX_TOKENS: "prefix_tokens",
}


def _require_nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_optional_nonempty_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _require_nonempty_string(value, name)


def _require_nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _require_optional_nonnegative_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    return _require_nonnegative_int(value, name)


def _require_nonnegative_finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], name: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            f"{name} fields must exactly match schema; "
            f"missing={missing}, unexpected={unexpected}"
        )


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _load_json_no_duplicates(value: str) -> Any:
    if not isinstance(value, str):
        raise TypeError("trace JSON must be text")
    return json.loads(
        value,
        object_pairs_hook=_reject_duplicate_members,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON constant: {token}")
        ),
    )


@dataclass(frozen=True, slots=True)
class TraceSourceManifest:
    source_id: str
    source_name: str
    source_uri: str
    source_version: str
    license_id: str
    source_sha256: str
    normalization_version: str
    normalization_steps: tuple[str, ...]
    field_origins: tuple[tuple[TraceField, TraceFieldOrigin], ...]

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "source_name",
            "source_uri",
            "source_version",
            "license_id",
            "normalization_version",
        ):
            _require_nonempty_string(getattr(self, name), name)

        if not isinstance(self.source_sha256, str) or not _SHA256_RE.fullmatch(
            self.source_sha256
        ):
            raise ValueError("source_sha256 must be a lowercase SHA-256 hex digest")

        if not isinstance(self.normalization_steps, tuple) or not self.normalization_steps:
            raise ValueError("normalization_steps must be a non-empty tuple")
        for step in self.normalization_steps:
            _require_nonempty_string(step, "normalization step")

        if not isinstance(self.field_origins, tuple) or not self.field_origins:
            raise ValueError("field_origins must be a non-empty tuple")
        origins: dict[TraceField, TraceFieldOrigin] = {}
        for item in self.field_origins:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("field_origins entries must be (TraceField, TraceFieldOrigin)")
            field, origin = item
            if not isinstance(field, TraceField):
                raise TypeError("field origin key must be TraceField")
            if not isinstance(origin, TraceFieldOrigin):
                raise TypeError("field origin value must be TraceFieldOrigin")
            if field in origins:
                raise ValueError(f"duplicate field origin declaration: {field.value}")
            origins[field] = origin

        if TraceField.ARRIVAL_TIME_S not in origins:
            raise ValueError("arrival_time_s origin must be declared")

        object.__setattr__(
            self,
            "field_origins",
            tuple(sorted(origins.items(), key=lambda item: item[0].value)),
        )

    @property
    def origin_by_field(self) -> Mapping[TraceField, TraceFieldOrigin]:
        return dict(self.field_origins)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_uri": self.source_uri,
            "source_version": self.source_version,
            "license_id": self.license_id,
            "source_sha256": self.source_sha256,
            "normalization_version": self.normalization_version,
            "normalization_steps": list(self.normalization_steps),
            "field_origins": {
                field.value: origin.value for field, origin in self.field_origins
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TraceSourceManifest:
        if not isinstance(value, Mapping):
            raise TypeError("manifest must be a JSON object")
        _require_exact_keys(value, _MANIFEST_KEYS, "manifest")

        steps = value["normalization_steps"]
        if not isinstance(steps, list):
            raise TypeError("normalization_steps must be a JSON array")

        raw_origins = value["field_origins"]
        if not isinstance(raw_origins, Mapping):
            raise TypeError("field_origins must be a JSON object")
        origins: list[tuple[TraceField, TraceFieldOrigin]] = []
        for raw_field, raw_origin in raw_origins.items():
            try:
                field = TraceField(raw_field)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown trace field origin key: {raw_field!r}") from exc
            try:
                origin = TraceFieldOrigin(raw_origin)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid trace field origin for {field.value}: {raw_origin!r}"
                ) from exc
            origins.append((field, origin))

        return cls(
            source_id=value["source_id"],
            source_name=value["source_name"],
            source_uri=value["source_uri"],
            source_version=value["source_version"],
            license_id=value["license_id"],
            source_sha256=value["source_sha256"],
            normalization_version=value["normalization_version"],
            normalization_steps=tuple(steps),
            field_origins=tuple(origins),
        )


@dataclass(frozen=True, slots=True)
class NormalizedTraceRecord:
    record_id: str
    source_record_id: str
    source_ordinal: int
    arrival_time_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    prefix_group_id: str | None = None
    prefix_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_nonempty_string(self.record_id, "record_id")
        _require_nonempty_string(self.source_record_id, "source_record_id")
        _require_nonnegative_int(self.source_ordinal, "source_ordinal")
        object.__setattr__(
            self,
            "arrival_time_s",
            _require_nonnegative_finite_number(self.arrival_time_s, "arrival_time_s"),
        )
        _require_optional_nonnegative_int(self.input_tokens, "input_tokens")
        _require_optional_nonnegative_int(self.output_tokens, "output_tokens")
        _require_optional_nonempty_string(self.prefix_group_id, "prefix_group_id")
        _require_optional_nonnegative_int(self.prefix_tokens, "prefix_tokens")

        if self.prefix_tokens is not None:
            if self.input_tokens is None:
                raise ValueError("prefix_tokens requires input_tokens")
            if self.prefix_tokens > self.input_tokens:
                raise ValueError("prefix_tokens cannot exceed input_tokens")

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "source_record_id": self.source_record_id,
            "source_ordinal": self.source_ordinal,
            "arrival_time_s": self.arrival_time_s,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "prefix_group_id": self.prefix_group_id,
            "prefix_tokens": self.prefix_tokens,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NormalizedTraceRecord:
        if not isinstance(value, Mapping):
            raise TypeError("trace record must be a JSON object")
        _require_exact_keys(value, _RECORD_KEYS, "trace record")
        return cls(
            record_id=value["record_id"],
            source_record_id=value["source_record_id"],
            source_ordinal=value["source_ordinal"],
            arrival_time_s=value["arrival_time_s"],
            input_tokens=value["input_tokens"],
            output_tokens=value["output_tokens"],
            prefix_group_id=value["prefix_group_id"],
            prefix_tokens=value["prefix_tokens"],
        )


@dataclass(frozen=True, slots=True)
class NormalizedTraceDataset:
    manifest: TraceSourceManifest
    records: tuple[NormalizedTraceRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, TraceSourceManifest):
            raise TypeError("manifest must be TraceSourceManifest")
        if not isinstance(self.records, tuple) or not self.records:
            raise ValueError("records must be a non-empty tuple")
        if not all(isinstance(record, NormalizedTraceRecord) for record in self.records):
            raise TypeError("records must contain NormalizedTraceRecord values")

        record_ids = [record.record_id for record in self.records]
        source_record_ids = [record.source_record_id for record in self.records]
        ordinals = [record.source_ordinal for record in self.records]
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("duplicate normalized record_id")
        if len(set(source_record_ids)) != len(source_record_ids):
            raise ValueError("duplicate source_record_id")
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("duplicate source_ordinal")

        declared = set(self.manifest.origin_by_field)
        for record in self.records:
            for field, attr in _FIELD_ATTR.items():
                if getattr(record, attr) is not None and field not in declared:
                    raise ValueError(
                        f"present field {field.value} lacks manifest provenance declaration"
                    )

        object.__setattr__(
            self,
            "records",
            tuple(sorted(self.records, key=lambda record: record.source_ordinal)),
        )

    @property
    def source_order(self) -> tuple[NormalizedTraceRecord, ...]:
        return self.records

    @property
    def chronological_order(self) -> tuple[NormalizedTraceRecord, ...]:
        return tuple(
            sorted(
                self.records,
                key=lambda record: (record.arrival_time_s, record.source_ordinal),
            )
        )

    def field_coverage(self) -> Mapping[TraceField, tuple[int, int]]:
        total = len(self.records)
        result: dict[TraceField, tuple[int, int]] = {}
        for field, attr in _FIELD_ATTR.items():
            present = sum(getattr(record, attr) is not None for record in self.records)
            result[field] = (present, total)
        return result

    def require_fields(self, fields: Iterable[TraceField]) -> NormalizedTraceDataset:
        requested = tuple(fields)
        for field in requested:
            if not isinstance(field, TraceField):
                raise TypeError("require_fields accepts TraceField values")
            attr = _FIELD_ATTR[field]
            missing = sum(getattr(record, attr) is None for record in self.records)
            if missing:
                raise ValueError(
                    f"required trace field {field.value} missing from "
                    f"{missing}/{len(self.records)} records"
                )
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": NORMALIZED_TRACE_SCHEMA,
            "manifest": self.manifest.to_dict(),
            "records": [record.to_dict() for record in self.records],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, value: str) -> NormalizedTraceDataset:
        decoded = _load_json_no_duplicates(value)
        if not isinstance(decoded, Mapping):
            raise TypeError("normalized trace must be a JSON object")
        _require_exact_keys(decoded, _DATASET_KEYS, "normalized trace")
        if decoded["schema"] != NORMALIZED_TRACE_SCHEMA:
            raise ValueError(f"unsupported normalized trace schema: {decoded['schema']!r}")

        raw_records = decoded["records"]
        if not isinstance(raw_records, list):
            raise TypeError("records must be a JSON array")
        return cls(
            manifest=TraceSourceManifest.from_dict(decoded["manifest"]),
            records=tuple(NormalizedTraceRecord.from_dict(item) for item in raw_records),
        )
