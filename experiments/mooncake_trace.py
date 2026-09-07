from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from statistics import fmean
import sys
from typing import Any, Iterable, Mapping

from experiments.trace_workload import (
    NormalizedTraceDataset,
    NormalizedTraceRecord,
    TraceField,
    TraceFieldOrigin,
    TraceSourceManifest,
)


MOONCAKE_SOURCE_REVISION = "3cca71daccf2a7afb8fe3f0295358f70e3a69fdb"
MOONCAKE_DOC_REVISION = "1d0e4c7587b57b78a1997be370b349b79828c5dd"
MOONCAKE_DOC_URI = (
    "https://github.com/kvcache-ai/Mooncake/blob/"
    f"{MOONCAKE_DOC_REVISION}/FAST25-release/README.md"
)
MOONCAKE_SOURCE_URI = (
    "https://raw.githubusercontent.com/kvcache-ai/Mooncake/"
    f"{MOONCAKE_SOURCE_REVISION}/FAST25-release/traces/conversation_trace.jsonl"
)
MOONCAKE_SOURCE_SHA256 = "b8cbb061a85206d729d91cdc2981f43c9e0d99209dce588d3af5f7934408b9df"
MOONCAKE_EXPECTED_REQUESTS = 12_031
MOONCAKE_PREFIX_BLOCK_TOKENS = 512
MOONCAKE_NORMALIZATION_VERSION = "cadi.c5.2.mooncake-fast25.v1"

_UPSTREAM_KEYS = frozenset({"timestamp", "input_length", "output_length", "hash_ids"})


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
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


def _require_nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


@dataclass(frozen=True, slots=True)
class MooncakeSourceRow:
    timestamp_ms: int
    input_tokens: int
    output_tokens: int
    hash_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.timestamp_ms, "timestamp")
        _require_nonnegative_int(self.input_tokens, "input_length")
        _require_nonnegative_int(self.output_tokens, "output_length")
        if not isinstance(self.hash_ids, tuple):
            raise TypeError("hash_ids must be a tuple")
        for index, hash_id in enumerate(self.hash_ids):
            _require_nonnegative_int(hash_id, f"hash_ids[{index}]")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MooncakeSourceRow":
        if not isinstance(value, Mapping):
            raise TypeError("Mooncake source row must be a JSON object")
        _require_exact_keys(value, _UPSTREAM_KEYS, "Mooncake source row")
        raw_hash_ids = value["hash_ids"]
        if not isinstance(raw_hash_ids, list):
            raise TypeError("hash_ids must be a JSON array")
        return cls(
            timestamp_ms=_require_nonnegative_int(value["timestamp"], "timestamp"),
            input_tokens=_require_nonnegative_int(value["input_length"], "input_length"),
            output_tokens=_require_nonnegative_int(
                value["output_length"], "output_length"
            ),
            hash_ids=tuple(
                _require_nonnegative_int(item, f"hash_ids[{index}]")
                for index, item in enumerate(raw_hash_ids)
            ),
        )


class _PriorPrefixTrie:
    """Exact prior-sequence prefix membership, never per-position mixing."""

    def __init__(self) -> None:
        self._root: dict[int, dict] = {}

    def longest_seen_prefix(self, hash_ids: tuple[int, ...]) -> int:
        node = self._root
        shared = 0
        for hash_id in hash_ids:
            child = node.get(hash_id)
            if child is None:
                break
            node = child
            shared += 1
        return shared

    def add(self, hash_ids: tuple[int, ...]) -> None:
        node = self._root
        for hash_id in hash_ids:
            node = node.setdefault(hash_id, {})


def parse_mooncake_jsonl(raw: bytes) -> tuple[MooncakeSourceRow, ...]:
    if not isinstance(raw, bytes):
        raise TypeError("Mooncake source artifact must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Mooncake source artifact must be UTF-8") from exc

    rows: list[MooncakeSourceRow] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise ValueError(f"blank JSONL record at line {line_number}")
        try:
            decoded = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_members,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON constant: {token}")
                ),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid Mooncake JSONL at line {line_number}") from exc
        rows.append(MooncakeSourceRow.from_mapping(decoded))

    if not rows:
        raise ValueError("Mooncake source artifact must contain at least one request")
    return tuple(rows)


def mooncake_manifest() -> TraceSourceManifest:
    return TraceSourceManifest(
        source_id="mooncake-fast25-conversation",
        source_name="Mooncake FAST'25 conversation trace",
        source_uri=MOONCAKE_SOURCE_URI,
        source_version=MOONCAKE_SOURCE_REVISION,
        license_id="Apache-2.0",
        source_sha256=MOONCAKE_SOURCE_SHA256,
        normalization_version=MOONCAKE_NORMALIZATION_VERSION,
        normalization_steps=(
            "preserve zero-based JSONL source ordinal and derive stable row identities",
            "convert source timestamp milliseconds to arrival_time_s by division by 1000",
            "preserve input_length and output_length as source-observed token counts",
            (
                "interpret hash_ids as 512-token reusable prefix blocks per "
                f"Mooncake FAST25 trace documentation revision {MOONCAKE_DOC_REVISION}"
            ),
            "derive longest reusable prior prefix from exact prior hash-id sequence paths",
            "map each shared 512-token hash block prefix to bounded prefix_tokens",
        ),
        field_origins=(
            (TraceField.ARRIVAL_TIME_S, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.INPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.OUTPUT_TOKENS, TraceFieldOrigin.SOURCE_OBSERVED),
            (TraceField.PREFIX_GROUP_ID, TraceFieldOrigin.TRACE_DERIVED),
            (TraceField.PREFIX_TOKENS, TraceFieldOrigin.TRACE_DERIVED),
        ),
    )


def derive_mooncake_records(
    rows: Iterable[MooncakeSourceRow],
) -> tuple[NormalizedTraceRecord, ...]:
    """Derive normalized records without asserting source-artifact provenance."""

    source_rows = tuple(rows)
    if not source_rows:
        raise ValueError("Mooncake rows must not be empty")
    if not all(isinstance(row, MooncakeSourceRow) for row in source_rows):
        raise TypeError("rows must contain MooncakeSourceRow values")

    trie = _PriorPrefixTrie()
    records: list[NormalizedTraceRecord] = []

    for ordinal, row in enumerate(source_rows):
        shared_blocks = trie.longest_seen_prefix(row.hash_ids)
        prefix_tokens = min(
            row.input_tokens,
            MOONCAKE_PREFIX_BLOCK_TOKENS * shared_blocks,
        )
        prefix_group_id = (
            None
            if shared_blocks == 0
            else f"mooncake-hash:{row.hash_ids[shared_blocks - 1]}"
        )
        records.append(
            NormalizedTraceRecord(
                record_id=f"mooncake-fast25:{ordinal:05d}",
                source_record_id=f"jsonl-line:{ordinal + 1}",
                source_ordinal=ordinal,
                arrival_time_s=row.timestamp_ms / 1000.0,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                prefix_group_id=prefix_group_id,
                prefix_tokens=prefix_tokens,
            )
        )
        trie.add(row.hash_ids)

    return tuple(records)


def load_pinned_mooncake_trace(raw: bytes) -> NormalizedTraceDataset:
    """Validate exact pinned bytes before parsing, deriving, or binding provenance."""

    if not isinstance(raw, bytes):
        raise TypeError("Mooncake source artifact must be bytes")
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != MOONCAKE_SOURCE_SHA256:
        raise ValueError(
            "Mooncake source SHA-256 mismatch: "
            f"expected {MOONCAKE_SOURCE_SHA256}, got {actual_sha256}"
        )
    return NormalizedTraceDataset(
        manifest=mooncake_manifest(),
        records=derive_mooncake_records(parse_mooncake_jsonl(raw)),
    )


@dataclass(frozen=True, slots=True)
class MooncakeValidationSummary:
    raw_sha256: str
    raw_bytes: int
    request_count: int
    arrival_min_s: float
    arrival_max_s: float
    input_min: int
    input_max: int
    input_mean: float
    output_min: int
    output_max: int
    output_mean: float
    reusable_requests: int
    reuse_fraction: float
    prefix_tokens_mean: float
    prefix_tokens_max: int
    dataset_fingerprint: str
    field_coverage: Mapping[str, tuple[int, int]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_sha256": self.raw_sha256,
            "raw_bytes": self.raw_bytes,
            "request_count": self.request_count,
            "arrival_min_s": self.arrival_min_s,
            "arrival_max_s": self.arrival_max_s,
            "input_min": self.input_min,
            "input_max": self.input_max,
            "input_mean": self.input_mean,
            "output_min": self.output_min,
            "output_max": self.output_max,
            "output_mean": self.output_mean,
            "reusable_requests": self.reusable_requests,
            "reuse_fraction": self.reuse_fraction,
            "prefix_tokens_mean": self.prefix_tokens_mean,
            "prefix_tokens_max": self.prefix_tokens_max,
            "dataset_fingerprint": self.dataset_fingerprint,
            "field_coverage": {
                key: list(value) for key, value in sorted(self.field_coverage.items())
            },
        }


def summarize_pinned_mooncake_trace(raw: bytes) -> MooncakeValidationSummary:
    normalized = load_pinned_mooncake_trace(raw)
    actual_sha256 = hashlib.sha256(raw).hexdigest()

    records = normalized.source_order
    if len(records) != MOONCAKE_EXPECTED_REQUESTS:
        raise ValueError(
            f"unexpected Mooncake request count: "
            f"{len(records)} != {MOONCAKE_EXPECTED_REQUESTS}"
        )

    arrivals = [record.arrival_time_s for record in records]
    inputs = [record.input_tokens for record in records]
    outputs = [record.output_tokens for record in records]
    prefixes = [record.prefix_tokens for record in records]
    if any(value is None for value in inputs + outputs + prefixes):
        raise ValueError("Mooncake normalized source unexpectedly contains missing core fields")

    input_values = [int(value) for value in inputs]
    output_values = [int(value) for value in outputs]
    prefix_values = [int(value) for value in prefixes]
    reusable = sum(value > 0 for value in prefix_values)
    coverage = {
        field.value: result for field, result in normalized.field_coverage().items()
    }

    return MooncakeValidationSummary(
        raw_sha256=actual_sha256,
        raw_bytes=len(raw),
        request_count=len(records),
        arrival_min_s=min(arrivals),
        arrival_max_s=max(arrivals),
        input_min=min(input_values),
        input_max=max(input_values),
        input_mean=fmean(input_values),
        output_min=min(output_values),
        output_max=max(output_values),
        output_mean=fmean(output_values),
        reusable_requests=reusable,
        reuse_fraction=reusable / len(records),
        prefix_tokens_mean=fmean(prefix_values),
        prefix_tokens_max=max(prefix_values),
        dataset_fingerprint=normalized.fingerprint,
        field_coverage=coverage,
    )


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: python -m experiments.mooncake_trace SOURCE_JSONL")
    raw = Path(argv[1]).read_bytes()
    summary = summarize_pinned_mooncake_trace(raw)
    print(
        json.dumps(
            summary.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
