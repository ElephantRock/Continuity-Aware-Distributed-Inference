from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Mapping

from experiments.continuity_augmentation import ContinuityAugmentationDataset
from experiments.trace_workload import NormalizedTraceDataset


FULLY_SYNTHETIC_WORKLOAD_SCHEMA = "cadi.c5.4.fully-synthetic-workload.v1"
FULLY_SYNTHETIC_WORKLOAD_VERSION = "cadi.c5.4.fully-synthetic-workload.v1"
WORKLOAD_ENVELOPE_SCHEMA = "cadi.c5.4.workload-envelope.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SYNTHETIC_DATASET_KEYS = frozenset(
    {"schema", "version", "provenance", "config", "records"}
)
_SYNTHETIC_CONFIG_KEYS = frozenset(
    {
        "seed",
        "request_count",
        "interarrival_s_choices",
        "input_tokens_choices",
        "output_tokens_choices",
        "prefix_fraction_choices",
        "prefix_group_count",
    }
)
_SYNTHETIC_RECORD_KEYS = frozenset(
    {
        "record_id",
        "ordinal",
        "arrival_time_s",
        "input_tokens",
        "output_tokens",
        "prefix_group_id",
        "prefix_tokens",
    }
)
_ENVELOPE_KEYS = frozenset(
    {
        "schema",
        "mode",
        "source_dataset_fingerprint",
        "augmentation_fingerprint",
        "augmentation_source_dataset_fingerprint",
        "synthetic_dataset_fingerprint",
    }
)


class SyntheticWorkloadProvenance(str, Enum):
    SYNTHETIC = "SYNTHETIC"


class WorkloadMode(str, Enum):
    SOURCE_DERIVED = "SOURCE_DERIVED"
    TRACE_AUGMENTED = "TRACE_AUGMENTED"
    FULLY_SYNTHETIC = "FULLY_SYNTHETIC"


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


def _load_json_no_duplicates(value: str, name: str) -> Any:
    if not isinstance(value, str):
        raise TypeError(f"{name} JSON must be text")
    return json.loads(
        value,
        object_pairs_hook=_reject_duplicate_members,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON constant: {token}")
        ),
    )


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


def _require_positive_int(value: Any, name: str) -> int:
    result = _require_nonnegative_int(value, name)
    if result == 0:
        raise ValueError(f"{name} must be positive")
    return result


def _require_nonnegative_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if result < 0.0:
        raise ValueError(f"{name} must be nonnegative")
    return result


def _require_positive_finite(value: Any, name: str) -> float:
    result = _require_nonnegative_finite(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def _require_fraction(value: Any, name: str) -> float:
    result = _require_nonnegative_finite(value, name)
    if result > 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return result


def _require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _require_optional_sha256(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, name)


@dataclass(frozen=True, slots=True)
class FullySyntheticWorkloadConfig:
    seed: int
    request_count: int
    interarrival_s_choices: tuple[float, ...]
    input_tokens_choices: tuple[int, ...]
    output_tokens_choices: tuple[int, ...]
    prefix_fraction_choices: tuple[float, ...]
    prefix_group_count: int

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.seed, "seed")
        _require_positive_int(self.request_count, "request_count")

        if not isinstance(self.interarrival_s_choices, tuple):
            raise TypeError("interarrival_s_choices must be a tuple")
        interarrivals = tuple(
            _require_positive_finite(value, f"interarrival_s_choices[{index}]")
            for index, value in enumerate(self.interarrival_s_choices)
        )
        if not interarrivals:
            raise ValueError("interarrival_s_choices must not be empty")
        if len(set(interarrivals)) != len(interarrivals):
            raise ValueError("interarrival_s_choices must not contain duplicates")
        object.__setattr__(self, "interarrival_s_choices", interarrivals)

        if not isinstance(self.input_tokens_choices, tuple):
            raise TypeError("input_tokens_choices must be a tuple")
        inputs = tuple(
            _require_positive_int(value, f"input_tokens_choices[{index}]")
            for index, value in enumerate(self.input_tokens_choices)
        )
        if not inputs:
            raise ValueError("input_tokens_choices must not be empty")
        if len(set(inputs)) != len(inputs):
            raise ValueError("input_tokens_choices must not contain duplicates")

        if not isinstance(self.output_tokens_choices, tuple):
            raise TypeError("output_tokens_choices must be a tuple")
        outputs = tuple(
            _require_nonnegative_int(value, f"output_tokens_choices[{index}]")
            for index, value in enumerate(self.output_tokens_choices)
        )
        if not outputs:
            raise ValueError("output_tokens_choices must not be empty")
        if len(set(outputs)) != len(outputs):
            raise ValueError("output_tokens_choices must not contain duplicates")

        if not isinstance(self.prefix_fraction_choices, tuple):
            raise TypeError("prefix_fraction_choices must be a tuple")
        fractions = tuple(
            _require_fraction(value, f"prefix_fraction_choices[{index}]")
            for index, value in enumerate(self.prefix_fraction_choices)
        )
        if not fractions:
            raise ValueError("prefix_fraction_choices must not be empty")
        if len(set(fractions)) != len(fractions):
            raise ValueError("prefix_fraction_choices must not contain duplicates")
        object.__setattr__(self, "prefix_fraction_choices", fractions)

        group_count = _require_nonnegative_int(
            self.prefix_group_count, "prefix_group_count"
        )
        if any(value > 0.0 for value in fractions) and group_count == 0:
            raise ValueError(
                "positive prefix fractions require positive prefix_group_count"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "request_count": self.request_count,
            "interarrival_s_choices": list(self.interarrival_s_choices),
            "input_tokens_choices": list(self.input_tokens_choices),
            "output_tokens_choices": list(self.output_tokens_choices),
            "prefix_fraction_choices": list(self.prefix_fraction_choices),
            "prefix_group_count": self.prefix_group_count,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "FullySyntheticWorkloadConfig":
        if not isinstance(value, Mapping):
            raise TypeError("fully synthetic workload config must be a JSON object")
        _require_exact_keys(
            value, _SYNTHETIC_CONFIG_KEYS, "fully synthetic workload config"
        )

        array_fields = (
            "interarrival_s_choices",
            "input_tokens_choices",
            "output_tokens_choices",
            "prefix_fraction_choices",
        )
        for field in array_fields:
            if not isinstance(value[field], list):
                raise TypeError(f"{field} must be a JSON array")

        return cls(
            seed=value["seed"],
            request_count=value["request_count"],
            interarrival_s_choices=tuple(value["interarrival_s_choices"]),
            input_tokens_choices=tuple(value["input_tokens_choices"]),
            output_tokens_choices=tuple(value["output_tokens_choices"]),
            prefix_fraction_choices=tuple(value["prefix_fraction_choices"]),
            prefix_group_count=value["prefix_group_count"],
        )


@dataclass(frozen=True, slots=True)
class FullySyntheticWorkloadRecord:
    record_id: str
    ordinal: int
    arrival_time_s: float
    input_tokens: int
    output_tokens: int
    prefix_group_id: str | None
    prefix_tokens: int

    def __post_init__(self) -> None:
        _require_nonempty_string(self.record_id, "record_id")
        _require_nonnegative_int(self.ordinal, "ordinal")
        object.__setattr__(
            self,
            "arrival_time_s",
            _require_nonnegative_finite(self.arrival_time_s, "arrival_time_s"),
        )
        _require_positive_int(self.input_tokens, "input_tokens")
        _require_nonnegative_int(self.output_tokens, "output_tokens")
        _require_optional_nonempty_string(self.prefix_group_id, "prefix_group_id")
        _require_nonnegative_int(self.prefix_tokens, "prefix_tokens")
        if self.prefix_tokens > self.input_tokens:
            raise ValueError("prefix_tokens cannot exceed input_tokens")
        if self.prefix_tokens == 0 and self.prefix_group_id is not None:
            raise ValueError("zero prefix_tokens requires null prefix_group_id")
        if self.prefix_tokens > 0 and self.prefix_group_id is None:
            raise ValueError("positive prefix_tokens requires prefix_group_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "ordinal": self.ordinal,
            "arrival_time_s": self.arrival_time_s,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "prefix_group_id": self.prefix_group_id,
            "prefix_tokens": self.prefix_tokens,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "FullySyntheticWorkloadRecord":
        if not isinstance(value, Mapping):
            raise TypeError("fully synthetic workload record must be a JSON object")
        _require_exact_keys(
            value, _SYNTHETIC_RECORD_KEYS, "fully synthetic workload record"
        )
        return cls(
            record_id=value["record_id"],
            ordinal=value["ordinal"],
            arrival_time_s=value["arrival_time_s"],
            input_tokens=value["input_tokens"],
            output_tokens=value["output_tokens"],
            prefix_group_id=value["prefix_group_id"],
            prefix_tokens=value["prefix_tokens"],
        )


@dataclass(frozen=True, slots=True)
class FullySyntheticWorkloadDataset:
    config: FullySyntheticWorkloadConfig
    records: tuple[FullySyntheticWorkloadRecord, ...]
    provenance: SyntheticWorkloadProvenance = SyntheticWorkloadProvenance.SYNTHETIC
    version: str = FULLY_SYNTHETIC_WORKLOAD_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.config, FullySyntheticWorkloadConfig):
            raise TypeError("config must be FullySyntheticWorkloadConfig")
        if self.provenance is not SyntheticWorkloadProvenance.SYNTHETIC:
            raise ValueError("fully synthetic workload provenance must be SYNTHETIC")
        if self.version != FULLY_SYNTHETIC_WORKLOAD_VERSION:
            raise ValueError(f"unsupported fully synthetic version: {self.version!r}")
        if not isinstance(self.records, tuple) or not self.records:
            raise ValueError("records must be a non-empty tuple")
        if not all(
            isinstance(record, FullySyntheticWorkloadRecord) for record in self.records
        ):
            raise TypeError("records must contain FullySyntheticWorkloadRecord values")
        if len(self.records) != self.config.request_count:
            raise ValueError("record count must equal config.request_count")

        allowed_inputs = set(self.config.input_tokens_choices)
        allowed_outputs = set(self.config.output_tokens_choices)
        allowed_groups = {
            f"synthetic-prefix:{index:04d}"
            for index in range(self.config.prefix_group_count)
        }

        previous_arrival: float | None = None
        for expected_ordinal, record in enumerate(self.records):
            if record.ordinal != expected_ordinal:
                raise ValueError("synthetic record ordinals must be contiguous")
            expected_id = f"synthetic:{expected_ordinal:06d}"
            if record.record_id != expected_id:
                raise ValueError("synthetic record_id must match ordinal")
            if record.input_tokens not in allowed_inputs:
                raise ValueError("input_tokens must come from configured choices")
            if record.output_tokens not in allowed_outputs:
                raise ValueError("output_tokens must come from configured choices")
            if (
                record.prefix_group_id is not None
                and record.prefix_group_id not in allowed_groups
            ):
                raise ValueError(
                    "prefix_group_id must be a configured synthetic group"
                )
            if expected_ordinal == 0:
                if record.arrival_time_s != 0.0:
                    raise ValueError("first synthetic arrival must be zero")
            elif previous_arrival is None or record.arrival_time_s <= previous_arrival:
                raise ValueError("synthetic arrivals must be strictly increasing")
            if not any(
                math.floor(record.input_tokens * fraction) == record.prefix_tokens
                for fraction in self.config.prefix_fraction_choices
            ):
                raise ValueError(
                    "prefix_tokens must be admitted by configured prefix fractions"
                )
            previous_arrival = record.arrival_time_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FULLY_SYNTHETIC_WORKLOAD_SCHEMA,
            "version": self.version,
            "provenance": self.provenance.value,
            "config": self.config.to_dict(),
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
    def from_json(cls, value: str) -> "FullySyntheticWorkloadDataset":
        decoded = _load_json_no_duplicates(value, "fully synthetic workload")
        if not isinstance(decoded, Mapping):
            raise TypeError("fully synthetic workload must be a JSON object")
        _require_exact_keys(
            decoded, _SYNTHETIC_DATASET_KEYS, "fully synthetic workload"
        )
        if decoded["schema"] != FULLY_SYNTHETIC_WORKLOAD_SCHEMA:
            raise ValueError(
                f"unsupported fully synthetic workload schema: {decoded['schema']!r}"
            )
        if decoded["version"] != FULLY_SYNTHETIC_WORKLOAD_VERSION:
            raise ValueError(
                f"unsupported fully synthetic version: {decoded['version']!r}"
            )
        try:
            provenance = SyntheticWorkloadProvenance(decoded["provenance"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid fully synthetic provenance: {decoded['provenance']!r}"
            ) from exc
        if provenance is not SyntheticWorkloadProvenance.SYNTHETIC:
            raise ValueError("fully synthetic workload provenance must be SYNTHETIC")
        raw_records = decoded["records"]
        if not isinstance(raw_records, list):
            raise TypeError("records must be a JSON array")
        return cls(
            config=FullySyntheticWorkloadConfig.from_dict(decoded["config"]),
            records=tuple(
                FullySyntheticWorkloadRecord.from_dict(item) for item in raw_records
            ),
            provenance=provenance,
            version=decoded["version"],
        )

    def assert_reproducible(self) -> "FullySyntheticWorkloadDataset":
        expected = generate_fully_synthetic_workload(self.config)
        if expected.to_json() != self.to_json():
            raise ValueError(
                "fully synthetic workload does not reproduce from config/seed"
            )
        return self


def _decision_digest(*, seed: int, record_id: str, channel: str) -> bytes:
    payload = "\x1f".join((str(seed), record_id, channel)).encode("utf-8")
    return hashlib.sha256(payload).digest()


def _choice_index(
    *,
    seed: int,
    record_id: str,
    channel: str,
    count: int,
) -> int:
    if count <= 0:
        raise ValueError("choice count must be positive")
    digest = _decision_digest(seed=seed, record_id=record_id, channel=channel)
    return int.from_bytes(digest[:8], "big") % count


def generate_fully_synthetic_workload(
    config: FullySyntheticWorkloadConfig,
) -> FullySyntheticWorkloadDataset:
    if not isinstance(config, FullySyntheticWorkloadConfig):
        raise TypeError("config must be FullySyntheticWorkloadConfig")

    records: list[FullySyntheticWorkloadRecord] = []
    arrival = 0.0
    for ordinal in range(config.request_count):
        record_id = f"synthetic:{ordinal:06d}"
        if ordinal > 0:
            interval_index = _choice_index(
                seed=config.seed,
                record_id=record_id,
                channel="interarrival",
                count=len(config.interarrival_s_choices),
            )
            arrival += config.interarrival_s_choices[interval_index]

        input_index = _choice_index(
            seed=config.seed,
            record_id=record_id,
            channel="input-tokens",
            count=len(config.input_tokens_choices),
        )
        output_index = _choice_index(
            seed=config.seed,
            record_id=record_id,
            channel="output-tokens",
            count=len(config.output_tokens_choices),
        )
        fraction_index = _choice_index(
            seed=config.seed,
            record_id=record_id,
            channel="prefix-fraction",
            count=len(config.prefix_fraction_choices),
        )
        input_tokens = config.input_tokens_choices[input_index]
        output_tokens = config.output_tokens_choices[output_index]
        prefix_fraction = config.prefix_fraction_choices[fraction_index]
        prefix_tokens = math.floor(input_tokens * prefix_fraction)

        prefix_group_id: str | None
        if prefix_tokens == 0:
            prefix_group_id = None
        else:
            group_index = _choice_index(
                seed=config.seed,
                record_id=record_id,
                channel="prefix-group",
                count=config.prefix_group_count,
            )
            prefix_group_id = f"synthetic-prefix:{group_index:04d}"

        records.append(
            FullySyntheticWorkloadRecord(
                record_id=record_id,
                ordinal=ordinal,
                arrival_time_s=arrival,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                prefix_group_id=prefix_group_id,
                prefix_tokens=prefix_tokens,
            )
        )

    return FullySyntheticWorkloadDataset(config=config, records=tuple(records))


@dataclass(frozen=True, slots=True)
class WorkloadEnvelope:
    mode: WorkloadMode
    source_dataset_fingerprint: str | None
    augmentation_fingerprint: str | None
    augmentation_source_dataset_fingerprint: str | None
    synthetic_dataset_fingerprint: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, WorkloadMode):
            raise TypeError("mode must be WorkloadMode")
        source = _require_optional_sha256(
            self.source_dataset_fingerprint, "source_dataset_fingerprint"
        )
        augmentation = _require_optional_sha256(
            self.augmentation_fingerprint, "augmentation_fingerprint"
        )
        augmentation_source = _require_optional_sha256(
            self.augmentation_source_dataset_fingerprint,
            "augmentation_source_dataset_fingerprint",
        )
        synthetic = _require_optional_sha256(
            self.synthetic_dataset_fingerprint, "synthetic_dataset_fingerprint"
        )

        if self.mode is WorkloadMode.SOURCE_DERIVED:
            if source is None:
                raise ValueError(
                    "SOURCE_DERIVED requires source dataset fingerprint"
                )
            if (
                augmentation is not None
                or augmentation_source is not None
                or synthetic is not None
            ):
                raise ValueError(
                    "SOURCE_DERIVED forbids augmentation/synthetic artifacts"
                )
        elif self.mode is WorkloadMode.TRACE_AUGMENTED:
            if source is None or augmentation is None or augmentation_source is None:
                raise ValueError(
                    "TRACE_AUGMENTED requires source and augmentation fingerprints"
                )
            if source != augmentation_source:
                raise ValueError(
                    "TRACE_AUGMENTED augmentation source fingerprint mismatch"
                )
            if synthetic is not None:
                raise ValueError(
                    "TRACE_AUGMENTED forbids fully synthetic artifact"
                )
        elif self.mode is WorkloadMode.FULLY_SYNTHETIC:
            if synthetic is None:
                raise ValueError(
                    "FULLY_SYNTHETIC requires synthetic dataset fingerprint"
                )
            if (
                source is not None
                or augmentation is not None
                or augmentation_source is not None
            ):
                raise ValueError(
                    "FULLY_SYNTHETIC forbids source/trace-augmentation artifacts"
                )
        else:
            raise ValueError(f"unsupported WorkloadMode: {self.mode!r}")

    @classmethod
    def source_derived(cls, source: NormalizedTraceDataset) -> "WorkloadEnvelope":
        if not isinstance(source, NormalizedTraceDataset):
            raise TypeError("source must be NormalizedTraceDataset")
        return cls(
            mode=WorkloadMode.SOURCE_DERIVED,
            source_dataset_fingerprint=source.fingerprint,
            augmentation_fingerprint=None,
            augmentation_source_dataset_fingerprint=None,
            synthetic_dataset_fingerprint=None,
        )

    @classmethod
    def trace_augmented(
        cls,
        source: NormalizedTraceDataset,
        augmentation: ContinuityAugmentationDataset,
    ) -> "WorkloadEnvelope":
        if not isinstance(source, NormalizedTraceDataset):
            raise TypeError("source must be NormalizedTraceDataset")
        if not isinstance(augmentation, ContinuityAugmentationDataset):
            raise TypeError("augmentation must be ContinuityAugmentationDataset")
        augmentation.assert_compatible_source(source)
        return cls(
            mode=WorkloadMode.TRACE_AUGMENTED,
            source_dataset_fingerprint=source.fingerprint,
            augmentation_fingerprint=augmentation.fingerprint,
            augmentation_source_dataset_fingerprint=(
                augmentation.source_dataset_fingerprint
            ),
            synthetic_dataset_fingerprint=None,
        )

    @classmethod
    def fully_synthetic(
        cls, synthetic: FullySyntheticWorkloadDataset
    ) -> "WorkloadEnvelope":
        if not isinstance(synthetic, FullySyntheticWorkloadDataset):
            raise TypeError("synthetic must be FullySyntheticWorkloadDataset")
        synthetic.assert_reproducible()
        return cls(
            mode=WorkloadMode.FULLY_SYNTHETIC,
            source_dataset_fingerprint=None,
            augmentation_fingerprint=None,
            augmentation_source_dataset_fingerprint=None,
            synthetic_dataset_fingerprint=synthetic.fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKLOAD_ENVELOPE_SCHEMA,
            "mode": self.mode.value,
            "source_dataset_fingerprint": self.source_dataset_fingerprint,
            "augmentation_fingerprint": self.augmentation_fingerprint,
            "augmentation_source_dataset_fingerprint": (
                self.augmentation_source_dataset_fingerprint
            ),
            "synthetic_dataset_fingerprint": self.synthetic_dataset_fingerprint,
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
    def from_json(cls, value: str) -> "WorkloadEnvelope":
        decoded = _load_json_no_duplicates(value, "workload envelope")
        if not isinstance(decoded, Mapping):
            raise TypeError("workload envelope must be a JSON object")
        _require_exact_keys(decoded, _ENVELOPE_KEYS, "workload envelope")
        if decoded["schema"] != WORKLOAD_ENVELOPE_SCHEMA:
            raise ValueError(
                f"unsupported workload envelope schema: {decoded['schema']!r}"
            )
        try:
            mode = WorkloadMode(decoded["mode"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid WorkloadMode: {decoded['mode']!r}") from exc
        return cls(
            mode=mode,
            source_dataset_fingerprint=decoded["source_dataset_fingerprint"],
            augmentation_fingerprint=decoded["augmentation_fingerprint"],
            augmentation_source_dataset_fingerprint=(
                decoded["augmentation_source_dataset_fingerprint"]
            ),
            synthetic_dataset_fingerprint=decoded["synthetic_dataset_fingerprint"],
        )

    def assert_matches_source(
        self, source: NormalizedTraceDataset
    ) -> "WorkloadEnvelope":
        if self.mode is not WorkloadMode.SOURCE_DERIVED:
            raise ValueError("workload envelope mode is not SOURCE_DERIVED")
        if not isinstance(source, NormalizedTraceDataset):
            raise TypeError("source must be NormalizedTraceDataset")
        if source.fingerprint != self.source_dataset_fingerprint:
            raise ValueError("source dataset fingerprint mismatch")
        return self

    def assert_matches_trace_augmented(
        self,
        source: NormalizedTraceDataset,
        augmentation: ContinuityAugmentationDataset,
    ) -> "WorkloadEnvelope":
        if self.mode is not WorkloadMode.TRACE_AUGMENTED:
            raise ValueError("workload envelope mode is not TRACE_AUGMENTED")
        if not isinstance(source, NormalizedTraceDataset):
            raise TypeError("source must be NormalizedTraceDataset")
        if not isinstance(augmentation, ContinuityAugmentationDataset):
            raise TypeError("augmentation must be ContinuityAugmentationDataset")
        augmentation.assert_compatible_source(source)
        if source.fingerprint != self.source_dataset_fingerprint:
            raise ValueError("source dataset fingerprint mismatch")
        if augmentation.fingerprint != self.augmentation_fingerprint:
            raise ValueError("augmentation fingerprint mismatch")
        return self

    def assert_matches_fully_synthetic(
        self, synthetic: FullySyntheticWorkloadDataset
    ) -> "WorkloadEnvelope":
        if self.mode is not WorkloadMode.FULLY_SYNTHETIC:
            raise ValueError("workload envelope mode is not FULLY_SYNTHETIC")
        if not isinstance(synthetic, FullySyntheticWorkloadDataset):
            raise TypeError("synthetic must be FullySyntheticWorkloadDataset")
        synthetic.assert_reproducible()
        if synthetic.fingerprint != self.synthetic_dataset_fingerprint:
            raise ValueError("synthetic dataset fingerprint mismatch")
        return self
