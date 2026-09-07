from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Mapping

from experiments.trace_workload import NormalizedTraceDataset
from simulator.faults import FaultClass


CONTINUITY_AUGMENTATION_SCHEMA = "cadi.c5.3.continuity-augmentation.v1"
CONTINUITY_AUGMENTATION_VERSION = "cadi.c5.3.synthetic-continuity.v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATASET_KEYS = frozenset(
    {
        "schema",
        "augmentation_version",
        "provenance",
        "source_dataset_fingerprint",
        "config",
        "annotations",
    }
)
_CONFIG_KEYS = frozenset(
    {
        "seed",
        "session_length_records",
        "tool_wait_probability",
        "tool_wait_seconds",
        "branch_probability",
        "branch_lookback_records",
        "fault_probability",
        "fault_classes",
    }
)
_ANNOTATION_KEYS = frozenset(
    {
        "record_id",
        "session_id",
        "continuation_id",
        "parent_continuation_id",
        "branch_group_id",
        "tool_wait_before_s",
        "fault_class",
    }
)


class AugmentationProvenance(str, Enum):
    SYNTHETIC = "SYNTHETIC"


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
        raise TypeError("augmentation JSON must be text")
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


def _require_probability(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if result < 0.0 or result > 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return result


def _require_positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def _require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


@dataclass(frozen=True, slots=True)
class ContinuityAugmentationConfig:
    """Explicit synthetic assumptions; no field is inferred from source facts."""

    seed: int
    session_length_records: int
    tool_wait_probability: float
    tool_wait_seconds: tuple[float, ...]
    branch_probability: float
    branch_lookback_records: int
    fault_probability: float
    fault_classes: tuple[FaultClass, ...]

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.seed, "seed")
        _require_positive_int(self.session_length_records, "session_length_records")
        object.__setattr__(
            self,
            "tool_wait_probability",
            _require_probability(self.tool_wait_probability, "tool_wait_probability"),
        )
        object.__setattr__(
            self,
            "branch_probability",
            _require_probability(self.branch_probability, "branch_probability"),
        )
        object.__setattr__(
            self,
            "fault_probability",
            _require_probability(self.fault_probability, "fault_probability"),
        )

        if not isinstance(self.tool_wait_seconds, tuple):
            raise TypeError("tool_wait_seconds must be a tuple")
        normalized_waits = tuple(
            _require_positive_finite(value, f"tool_wait_seconds[{index}]")
            for index, value in enumerate(self.tool_wait_seconds)
        )
        if len(set(normalized_waits)) != len(normalized_waits):
            raise ValueError("tool_wait_seconds must not contain duplicates")
        if self.tool_wait_probability > 0.0 and not normalized_waits:
            raise ValueError(
                "nonzero tool_wait_probability requires tool_wait_seconds choices"
            )
        object.__setattr__(self, "tool_wait_seconds", normalized_waits)

        lookback = _require_positive_int(
            self.branch_lookback_records, "branch_lookback_records"
        )
        if lookback < 2:
            raise ValueError("branch_lookback_records must be at least 2")
        if (
            self.branch_probability > 0.0
            and self.session_length_records <= lookback
        ):
            raise ValueError(
                "nonzero branch_probability requires session_length_records "
                "greater than branch_lookback_records"
            )

        if not isinstance(self.fault_classes, tuple):
            raise TypeError("fault_classes must be a tuple")
        for fault_class in self.fault_classes:
            if not isinstance(fault_class, FaultClass):
                raise TypeError("fault_classes must contain FaultClass values")
        if len(set(self.fault_classes)) != len(self.fault_classes):
            raise ValueError("fault_classes must not contain duplicates")
        if self.fault_probability > 0.0 and not self.fault_classes:
            raise ValueError("nonzero fault_probability requires fault_classes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "session_length_records": self.session_length_records,
            "tool_wait_probability": self.tool_wait_probability,
            "tool_wait_seconds": list(self.tool_wait_seconds),
            "branch_probability": self.branch_probability,
            "branch_lookback_records": self.branch_lookback_records,
            "fault_probability": self.fault_probability,
            "fault_classes": [fault_class.value for fault_class in self.fault_classes],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContinuityAugmentationConfig:
        if not isinstance(value, Mapping):
            raise TypeError("augmentation config must be a JSON object")
        _require_exact_keys(value, _CONFIG_KEYS, "augmentation config")

        raw_waits = value["tool_wait_seconds"]
        if not isinstance(raw_waits, list):
            raise TypeError("tool_wait_seconds must be a JSON array")
        raw_faults = value["fault_classes"]
        if not isinstance(raw_faults, list):
            raise TypeError("fault_classes must be a JSON array")

        fault_classes: list[FaultClass] = []
        for raw_fault in raw_faults:
            try:
                fault_classes.append(FaultClass(raw_fault))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid FaultClass: {raw_fault!r}") from exc

        return cls(
            seed=value["seed"],
            session_length_records=value["session_length_records"],
            tool_wait_probability=value["tool_wait_probability"],
            tool_wait_seconds=tuple(raw_waits),
            branch_probability=value["branch_probability"],
            branch_lookback_records=value["branch_lookback_records"],
            fault_probability=value["fault_probability"],
            fault_classes=tuple(fault_classes),
        )


@dataclass(frozen=True, slots=True)
class ContinuityAnnotation:
    record_id: str
    session_id: str
    continuation_id: str
    parent_continuation_id: str | None
    branch_group_id: str | None
    tool_wait_before_s: float | None
    fault_class: FaultClass | None

    def __post_init__(self) -> None:
        _require_nonempty_string(self.record_id, "record_id")
        _require_nonempty_string(self.session_id, "session_id")
        _require_nonempty_string(self.continuation_id, "continuation_id")
        _require_optional_nonempty_string(
            self.parent_continuation_id, "parent_continuation_id"
        )
        _require_optional_nonempty_string(self.branch_group_id, "branch_group_id")
        if self.tool_wait_before_s is not None:
            object.__setattr__(
                self,
                "tool_wait_before_s",
                _require_positive_finite(
                    self.tool_wait_before_s, "tool_wait_before_s"
                ),
            )
        if self.fault_class is not None and not isinstance(
            self.fault_class, FaultClass
        ):
            raise TypeError("fault_class must be FaultClass or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "session_id": self.session_id,
            "continuation_id": self.continuation_id,
            "parent_continuation_id": self.parent_continuation_id,
            "branch_group_id": self.branch_group_id,
            "tool_wait_before_s": self.tool_wait_before_s,
            "fault_class": None if self.fault_class is None else self.fault_class.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContinuityAnnotation:
        if not isinstance(value, Mapping):
            raise TypeError("augmentation annotation must be a JSON object")
        _require_exact_keys(value, _ANNOTATION_KEYS, "augmentation annotation")
        raw_fault = value["fault_class"]
        fault_class: FaultClass | None
        if raw_fault is None:
            fault_class = None
        else:
            try:
                fault_class = FaultClass(raw_fault)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid FaultClass: {raw_fault!r}") from exc
        return cls(
            record_id=value["record_id"],
            session_id=value["session_id"],
            continuation_id=value["continuation_id"],
            parent_continuation_id=value["parent_continuation_id"],
            branch_group_id=value["branch_group_id"],
            tool_wait_before_s=value["tool_wait_before_s"],
            fault_class=fault_class,
        )


@dataclass(frozen=True, slots=True)
class ContinuityAugmentationDataset:
    source_dataset_fingerprint: str
    config: ContinuityAugmentationConfig
    annotations: tuple[ContinuityAnnotation, ...]
    provenance: AugmentationProvenance = AugmentationProvenance.SYNTHETIC
    augmentation_version: str = CONTINUITY_AUGMENTATION_VERSION

    def __post_init__(self) -> None:
        _require_sha256(
            self.source_dataset_fingerprint, "source_dataset_fingerprint"
        )
        if not isinstance(self.config, ContinuityAugmentationConfig):
            raise TypeError("config must be ContinuityAugmentationConfig")
        if self.provenance is not AugmentationProvenance.SYNTHETIC:
            raise ValueError("augmentation provenance must be SYNTHETIC")
        if self.augmentation_version != CONTINUITY_AUGMENTATION_VERSION:
            raise ValueError(
                f"unsupported augmentation version: {self.augmentation_version!r}"
            )
        if not isinstance(self.annotations, tuple) or not self.annotations:
            raise ValueError("annotations must be a non-empty tuple")
        if not all(
            isinstance(annotation, ContinuityAnnotation)
            for annotation in self.annotations
        ):
            raise TypeError("annotations must contain ContinuityAnnotation values")

        record_ids = [annotation.record_id for annotation in self.annotations]
        continuation_ids = [
            annotation.continuation_id for annotation in self.annotations
        ]
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("duplicate augmentation record_id")
        if len(set(continuation_ids)) != len(continuation_ids):
            raise ValueError("duplicate continuation_id")

        seen_continuations: dict[str, str] = {}
        current_session: str | None = None
        closed_sessions: set[str] = set()
        last_continuation_by_session: dict[str, str] = {}

        for annotation in self.annotations:
            if annotation.session_id != current_session:
                if current_session is not None:
                    closed_sessions.add(current_session)
                if annotation.session_id in closed_sessions:
                    raise ValueError("synthetic Session records must be contiguous")
                current_session = annotation.session_id
                if annotation.parent_continuation_id is not None:
                    raise ValueError("first Continuation in a Session must be root")
                if annotation.branch_group_id is not None:
                    raise ValueError("Session root cannot have branch_group_id")
                if annotation.tool_wait_before_s is not None:
                    raise ValueError("Session root cannot have tool_wait_before_s")
            else:
                parent = annotation.parent_continuation_id
                if parent is None:
                    raise ValueError("non-root Continuation requires a parent")
                if parent not in seen_continuations:
                    raise ValueError("parent Continuation must precede child")
                if seen_continuations[parent] != annotation.session_id:
                    raise ValueError("parent Continuation must belong to same Session")

                previous = last_continuation_by_session[annotation.session_id]
                is_branch = parent != previous
                if is_branch and annotation.branch_group_id is None:
                    raise ValueError("non-linear parent requires branch_group_id")
                if not is_branch and annotation.branch_group_id is not None:
                    raise ValueError("linear continuation cannot have branch_group_id")

            seen_continuations[annotation.continuation_id] = annotation.session_id
            last_continuation_by_session[annotation.session_id] = (
                annotation.continuation_id
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CONTINUITY_AUGMENTATION_SCHEMA,
            "augmentation_version": self.augmentation_version,
            "provenance": self.provenance.value,
            "source_dataset_fingerprint": self.source_dataset_fingerprint,
            "config": self.config.to_dict(),
            "annotations": [annotation.to_dict() for annotation in self.annotations],
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
    def from_json(cls, value: str) -> ContinuityAugmentationDataset:
        decoded = _load_json_no_duplicates(value)
        if not isinstance(decoded, Mapping):
            raise TypeError("continuity augmentation must be a JSON object")
        _require_exact_keys(decoded, _DATASET_KEYS, "continuity augmentation")
        if decoded["schema"] != CONTINUITY_AUGMENTATION_SCHEMA:
            raise ValueError(
                f"unsupported continuity augmentation schema: {decoded['schema']!r}"
            )
        if decoded["augmentation_version"] != CONTINUITY_AUGMENTATION_VERSION:
            raise ValueError(
                "unsupported augmentation version: "
                f"{decoded['augmentation_version']!r}"
            )
        try:
            provenance = AugmentationProvenance(decoded["provenance"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid augmentation provenance: {decoded['provenance']!r}"
            ) from exc
        if provenance is not AugmentationProvenance.SYNTHETIC:
            raise ValueError("augmentation provenance must be SYNTHETIC")

        raw_annotations = decoded["annotations"]
        if not isinstance(raw_annotations, list):
            raise TypeError("annotations must be a JSON array")

        return cls(
            source_dataset_fingerprint=decoded["source_dataset_fingerprint"],
            config=ContinuityAugmentationConfig.from_dict(decoded["config"]),
            annotations=tuple(
                ContinuityAnnotation.from_dict(item) for item in raw_annotations
            ),
            provenance=provenance,
            augmentation_version=decoded["augmentation_version"],
        )

    def assert_compatible_source(
        self, source: NormalizedTraceDataset
    ) -> ContinuityAugmentationDataset:
        if not isinstance(source, NormalizedTraceDataset):
            raise TypeError("source must be NormalizedTraceDataset")
        if source.fingerprint != self.source_dataset_fingerprint:
            raise ValueError("source dataset fingerprint mismatch")
        source_ids = tuple(record.record_id for record in source.source_order)
        annotation_ids = tuple(
            annotation.record_id for annotation in self.annotations
        )
        if annotation_ids != source_ids:
            raise ValueError("augmentation record linkage does not match source order")
        return self

    def assert_reproducible(
        self, source: NormalizedTraceDataset
    ) -> ContinuityAugmentationDataset:
        self.assert_compatible_source(source)
        expected = augment_trace(source, self.config)
        if expected.to_json() != self.to_json():
            raise ValueError("augmentation does not reproduce from source/config/seed")
        return self


def _decision_digest(
    *,
    seed: int,
    source_fingerprint: str,
    record_id: str,
    channel: str,
) -> bytes:
    payload = "\x1f".join(
        (str(seed), source_fingerprint, record_id, channel)
    ).encode("utf-8")
    return hashlib.sha256(payload).digest()


def _draw(
    *,
    seed: int,
    source_fingerprint: str,
    record_id: str,
    channel: str,
) -> float:
    digest = _decision_digest(
        seed=seed,
        source_fingerprint=source_fingerprint,
        record_id=record_id,
        channel=channel,
    )
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _choice_index(
    *,
    seed: int,
    source_fingerprint: str,
    record_id: str,
    channel: str,
    count: int,
) -> int:
    if count <= 0:
        raise ValueError("choice count must be positive")
    digest = _decision_digest(
        seed=seed,
        source_fingerprint=source_fingerprint,
        record_id=record_id,
        channel=channel,
    )
    return int.from_bytes(digest[8:16], "big") % count


def _selected(
    probability: float,
    *,
    seed: int,
    source_fingerprint: str,
    record_id: str,
    channel: str,
) -> bool:
    if probability <= 0.0:
        return False
    if probability >= 1.0:
        return True
    return (
        _draw(
            seed=seed,
            source_fingerprint=source_fingerprint,
            record_id=record_id,
            channel=channel,
        )
        < probability
    )


def augment_trace(
    source: NormalizedTraceDataset,
    config: ContinuityAugmentationConfig,
) -> ContinuityAugmentationDataset:
    """Create a separate synthetic overlay without mutating source trace facts."""

    if not isinstance(source, NormalizedTraceDataset):
        raise TypeError("source must be NormalizedTraceDataset")
    if not isinstance(config, ContinuityAugmentationConfig):
        raise TypeError("config must be ContinuityAugmentationConfig")

    source_fingerprint = source.fingerprint
    annotations: list[ContinuityAnnotation] = []

    for position, record in enumerate(source.source_order):
        session_index = position // config.session_length_records
        local_index = position % config.session_length_records
        session_id = f"syn-session:{session_index:06d}"
        continuation_id = f"{session_id}:c:{local_index:04d}"

        parent_continuation_id: str | None
        branch_group_id: str | None = None
        tool_wait_before_s: float | None = None

        if local_index == 0:
            parent_continuation_id = None
        else:
            parent_index = local_index - 1
            if (
                local_index >= config.branch_lookback_records
                and _selected(
                    config.branch_probability,
                    seed=config.seed,
                    source_fingerprint=source_fingerprint,
                    record_id=record.record_id,
                    channel="branch",
                )
            ):
                parent_index = local_index - config.branch_lookback_records
                branch_group_id = (
                    f"{session_id}:fork:{parent_index:04d}"
                )
            parent_continuation_id = f"{session_id}:c:{parent_index:04d}"

            if _selected(
                config.tool_wait_probability,
                seed=config.seed,
                source_fingerprint=source_fingerprint,
                record_id=record.record_id,
                channel="tool-wait",
            ):
                wait_index = _choice_index(
                    seed=config.seed,
                    source_fingerprint=source_fingerprint,
                    record_id=record.record_id,
                    channel="tool-wait-choice",
                    count=len(config.tool_wait_seconds),
                )
                tool_wait_before_s = config.tool_wait_seconds[wait_index]

        fault_class: FaultClass | None = None
        if _selected(
            config.fault_probability,
            seed=config.seed,
            source_fingerprint=source_fingerprint,
            record_id=record.record_id,
            channel="fault",
        ):
            fault_index = _choice_index(
                seed=config.seed,
                source_fingerprint=source_fingerprint,
                record_id=record.record_id,
                channel="fault-class",
                count=len(config.fault_classes),
            )
            fault_class = config.fault_classes[fault_index]

        annotations.append(
            ContinuityAnnotation(
                record_id=record.record_id,
                session_id=session_id,
                continuation_id=continuation_id,
                parent_continuation_id=parent_continuation_id,
                branch_group_id=branch_group_id,
                tool_wait_before_s=tool_wait_before_s,
                fault_class=fault_class,
            )
        )

    return ContinuityAugmentationDataset(
        source_dataset_fingerprint=source_fingerprint,
        config=config,
        annotations=tuple(annotations),
    )
