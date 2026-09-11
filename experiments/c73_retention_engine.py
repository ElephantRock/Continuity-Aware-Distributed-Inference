from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from continuity.entities import ContinuationLifecycle, StateLifecycle
from experiments.c7_protocol import (
    C6_ARTIFACT_SHA256,
    C6_EVIDENCE_CLASS,
    C6_SCIENTIFIC_FINGERPRINT,
    C7ExperimentManifest,
    ExperimentSeries,
    ParameterSource,
)
from experiments.c73_retention_protocol import (
    C73_DEFAULT_STATE_BYTES,
    C73_DEFAULT_STATE_TOKENS,
    C73_EVENT_ORDER,
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    C73_STATE_SIZE_MAP_FINGERPRINT,
    C73RetentionManifest,
    CapacityOutcome,
    ResidencyEndReason,
    RetentionPolicyID,
    derive_state_lifecycle,
    lifecycle_eviction_key,
    lru_eviction_key,
    residency_interval_byte_seconds,
    tool_return_ttft_seconds,
)
from simulator.inference_cost import InferenceCostWorkload
from simulator.inference_cost_runtime import (
    ValidatedRuntimeCostProfile,
    estimate_validated_runtime_cost,
)


C73B_ENGINE_SCHEMA = "cadi.c7.3b.retention-engine.v1"
C73B_CASE_SCHEMA = "cadi.c7.3b.retention-program-case.v1"
C73B_RESULT_SCHEMA = "cadi.c7.3b.retention-policy-result.v1"
C73B_AUDIT_SCHEMA = "cadi.c7.3b.retention-audit-record.v1"
C73B_TTFT_SCHEMA = "cadi.c7.3b.tool-return-ttft-projection.v1"
C73B_BASE_COMMIT = "2b8a76c54501d9728d6520c5847b41c0e5bb2e82"
C73B_FROZEN_RETENTION_PROTOCOL_FINGERPRINT = (
    "f6165cb9248f5f4292846942e797e5ddcd8d004027ad503b2a7de35210021379"
)
C73B_FROZEN_STATE_SIZE_MAP_FINGERPRINT = (
    "36bacaad6462ca3e5eeb2688040e4dcf0d829849158c5b5ad9bad3c58afe173b"
)

_EXPECTED_EVENT_ORDER = (
    "VALIDITY_INVALIDATION_AND_COMMON_INVALID_RELEASE",
    "GROUND_TRUTH_CONTINUATION_SESSION_TRANSITIONS_AND_LIFECYCLE_RECOMPUTE",
    "POLICY_SPECIFIC_LIFECYCLE_OR_SESSION_END_RELEASE",
    "FIXED_TTL_EXPIRY",
    "REUSE_LOOKUP_AND_SEMANTIC_VALIDITY",
    "SUCCESSFUL_REUSE_TOUCH",
    "STATE_ADMISSION",
    "CAPACITY_ENFORCEMENT",
    "INTERVAL_ACCOUNTING_CHECKPOINT",
)
if C73_EVENT_ORDER != _EXPECTED_EVENT_ORDER:
    raise RuntimeError("C7.3b event engine does not match the frozen C7.3a event order")
if C73_RETENTION_PROTOCOL_FINGERPRINT != C73B_FROZEN_RETENTION_PROTOCOL_FINGERPRINT:
    raise RuntimeError("C7.3b imported retention protocol fingerprint drift")
if C73_STATE_SIZE_MAP_FINGERPRINT != C73B_FROZEN_STATE_SIZE_MAP_FINGERPRINT:
    raise RuntimeError("C7.3b imported State-size-map fingerprint drift")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fp(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _nni(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _positive_int(value: Any, name: str) -> int:
    result = _nni(value, name)
    if result == 0:
        raise ValueError(f"{name} must be positive")
    return result


def _nnf(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


class RetentionEventKind(str, Enum):
    INVALIDATE = "INVALIDATE"
    DEPENDENTS = "DEPENDENTS"
    SESSION_STATUS = "SESSION_STATUS"
    REUSE = "REUSE"
    ADMIT = "ADMIT"


_EVENT_PHASE = {
    RetentionEventKind.INVALIDATE: 0,
    RetentionEventKind.DEPENDENTS: 1,
    RetentionEventKind.SESSION_STATUS: 1,
    RetentionEventKind.REUSE: 4,
    RetentionEventKind.ADMIT: 6,
}


class RetentionAuditKind(str, Enum):
    INVALIDATED = "INVALIDATED"
    LIFECYCLE_UPDATED = "LIFECYCLE_UPDATED"
    SESSION_STATUS_UPDATED = "SESSION_STATUS_UPDATED"
    POLICY_RELEASED = "POLICY_RELEASED"
    TTL_EXPIRED = "TTL_EXPIRED"
    REUSE_HIT = "REUSE_HIT"
    REUSE_MISS = "REUSE_MISS"
    REUSE_SEMANTIC_REJECT = "REUSE_SEMANTIC_REJECT"
    ADMITTED = "ADMITTED"
    ADMISSION_SKIPPED = "ADMISSION_SKIPPED"
    EVICTED = "EVICTED"
    CAPACITY_INFEASIBLE = "CAPACITY_INFEASIBLE"
    CHECKPOINT = "CHECKPOINT"
    PROGRAM_END_RELEASE = "PROGRAM_END_RELEASE"


@dataclass(frozen=True, slots=True)
class RetentionSessionSpec:
    session_id: str
    initially_live: bool = True

    def __post_init__(self) -> None:
        _nonempty(self.session_id, "session_id")
        if not isinstance(self.initially_live, bool):
            raise TypeError("initially_live must be bool")

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "initially_live": self.initially_live}


@dataclass(frozen=True, slots=True)
class RetentionStateSpec:
    state_id: str
    session_id: str
    admission_ordinal: int
    initial_dependents: tuple[ContinuationLifecycle, ...]
    size_bytes: int = C73_DEFAULT_STATE_BYTES

    def __post_init__(self) -> None:
        _nonempty(self.state_id, "state_id")
        _nonempty(self.session_id, "session_id")
        _nni(self.admission_ordinal, "admission_ordinal")
        if not isinstance(self.initial_dependents, tuple):
            raise TypeError("initial_dependents must be a tuple")
        derive_state_lifecycle(self.initial_dependents)
        if self.size_bytes != C73_DEFAULT_STATE_BYTES:
            raise ValueError("C7.3b State size must equal the frozen C7.3a State byte mapping")

    @property
    def initial_lifecycle(self) -> StateLifecycle:
        return derive_state_lifecycle(self.initial_dependents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "session_id": self.session_id,
            "admission_ordinal": self.admission_ordinal,
            "initial_dependents": [value.name for value in self.initial_dependents],
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class RetentionEvent:
    event_id: str
    time_seconds: float
    ordinal: int
    kind: RetentionEventKind
    state_id: str | None = None
    session_id: str | None = None
    dependent_lifecycles: tuple[ContinuationLifecycle, ...] = ()
    session_live: bool | None = None
    semantic_valid: bool | None = None

    def __post_init__(self) -> None:
        _nonempty(self.event_id, "event_id")
        object.__setattr__(self, "time_seconds", _nnf(self.time_seconds, "time_seconds"))
        _nni(self.ordinal, "ordinal")
        if not isinstance(self.kind, RetentionEventKind):
            raise TypeError("kind must be RetentionEventKind")
        if self.state_id is not None:
            _nonempty(self.state_id, "state_id")
        if self.session_id is not None:
            _nonempty(self.session_id, "session_id")
        if not isinstance(self.dependent_lifecycles, tuple):
            raise TypeError("dependent_lifecycles must be a tuple")

        if self.kind in {
            RetentionEventKind.INVALIDATE,
            RetentionEventKind.DEPENDENTS,
            RetentionEventKind.REUSE,
            RetentionEventKind.ADMIT,
        } and self.state_id is None:
            raise ValueError(f"{self.kind.value} requires state_id")
        if self.kind is RetentionEventKind.SESSION_STATUS and self.session_id is None:
            raise ValueError("SESSION_STATUS requires session_id")
        if self.kind is RetentionEventKind.DEPENDENTS:
            derive_state_lifecycle(self.dependent_lifecycles)
        elif self.dependent_lifecycles:
            raise ValueError("dependent_lifecycles is valid only for DEPENDENTS")
        if self.kind is RetentionEventKind.SESSION_STATUS:
            if not isinstance(self.session_live, bool):
                raise ValueError("SESSION_STATUS requires boolean session_live")
        elif self.session_live is not None:
            raise ValueError("session_live is valid only for SESSION_STATUS")
        if self.kind is RetentionEventKind.REUSE:
            if not isinstance(self.semantic_valid, bool):
                raise ValueError("REUSE requires boolean semantic_valid from the independent oracle")
        elif self.semantic_valid is not None:
            raise ValueError("semantic_valid is valid only for REUSE")

    @property
    def phase(self) -> int:
        return _EVENT_PHASE[self.kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "time_seconds": self.time_seconds,
            "ordinal": self.ordinal,
            "kind": self.kind.value,
            "state_id": self.state_id,
            "session_id": self.session_id,
            "dependent_lifecycles": [value.name for value in self.dependent_lifecycles],
            "session_live": self.session_live,
            "semantic_valid": self.semantic_valid,
        }


@dataclass(frozen=True, slots=True)
class RetentionProgramCase:
    program_id: str
    sessions: tuple[RetentionSessionSpec, ...]
    states: tuple[RetentionStateSpec, ...]
    events: tuple[RetentionEvent, ...]
    program_start_seconds: float
    program_end_seconds: float

    def __post_init__(self) -> None:
        _nonempty(self.program_id, "program_id")
        start = _nnf(self.program_start_seconds, "program_start_seconds")
        end = _nnf(self.program_end_seconds, "program_end_seconds")
        if end < start:
            raise ValueError("Program end cannot precede Program start")
        object.__setattr__(self, "program_start_seconds", start)
        object.__setattr__(self, "program_end_seconds", end)
        if not self.sessions or not all(isinstance(x, RetentionSessionSpec) for x in self.sessions):
            raise ValueError("sessions must be a non-empty tuple of RetentionSessionSpec")
        if not self.states or not all(isinstance(x, RetentionStateSpec) for x in self.states):
            raise ValueError("states must be a non-empty tuple of RetentionStateSpec")
        if not isinstance(self.events, tuple) or not all(isinstance(x, RetentionEvent) for x in self.events):
            raise ValueError("events must be a tuple of RetentionEvent")

        session_ids = tuple(x.session_id for x in self.sessions)
        state_ids = tuple(x.state_id for x in self.states)
        if len(set(session_ids)) != len(session_ids):
            raise ValueError("Session IDs must be unique")
        if len(set(state_ids)) != len(state_ids):
            raise ValueError("State IDs must be unique")
        if len({x.admission_ordinal for x in self.states}) != len(self.states):
            raise ValueError("State admission_ordinal values must be unique")
        session_set = set(session_ids)
        state_set = set(state_ids)
        if any(x.session_id not in session_set for x in self.states):
            raise ValueError("every State must reference a declared Session")
        if len({x.event_id for x in self.events}) != len(self.events):
            raise ValueError("event_id values must be unique")
        if len({(x.time_seconds, x.phase, x.ordinal) for x in self.events}) != len(self.events):
            raise ValueError("events must have unique (time, phase, ordinal) ordering keys")
        for event in self.events:
            if not start <= event.time_seconds <= end:
                raise ValueError("event time lies outside Program bounds")
            if event.state_id is not None and event.state_id not in state_set:
                raise ValueError("event references undeclared State")
            if event.session_id is not None and event.session_id not in session_set:
                raise ValueError("event references undeclared Session")
        admits = {state_id: 0 for state_id in state_ids}
        for event in self.events:
            if event.kind is RetentionEventKind.ADMIT:
                admits[event.state_id] += 1  # type: ignore[index]
        if any(count != 1 for count in admits.values()):
            raise ValueError("every declared State must have exactly one ADMIT event")

        state_by_id = {item.state_id: item for item in self.states}
        ordered_admits = sorted(
            (event for event in self.events if event.kind is RetentionEventKind.ADMIT),
            key=lambda event: (
                event.time_seconds,
                event.phase,
                event.ordinal,
                event.event_id,
            ),
        )
        admit_order_key: dict[str, tuple[float, int, int, str]] = {}
        for expected_ordinal, event in enumerate(ordered_admits):
            state_id = event.state_id
            if state_by_id[state_id].admission_ordinal != expected_ordinal:  # type: ignore[index]
                raise ValueError(
                    "State admission_ordinal must match deterministic global ADMIT order"
                )
            admit_order_key[state_id] = (  # type: ignore[index]
                event.time_seconds,
                event.phase,
                event.ordinal,
                event.event_id,
            )
        for event in self.events:
            if event.kind not in {RetentionEventKind.REUSE, RetentionEventKind.INVALIDATE}:
                continue
            event_key = (
                event.time_seconds,
                event.phase,
                event.ordinal,
                event.event_id,
            )
            if event_key < admit_order_key[event.state_id]:  # type: ignore[index]
                raise ValueError(
                    f"{event.kind.value} cannot occur before ADMIT for the same State"
                )

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C73B_CASE_SCHEMA,
            "program_id": self.program_id,
            "program_start_seconds": self.program_start_seconds,
            "program_end_seconds": self.program_end_seconds,
            "sessions": [item.to_dict() for item in sorted(self.sessions, key=lambda x: x.session_id)],
            "states": [item.to_dict() for item in sorted(self.states, key=lambda x: x.state_id)],
            "events": [
                item.to_dict()
                for item in sorted(
                    self.events,
                    key=lambda x: (x.time_seconds, x.phase, x.ordinal, x.event_id),
                )
            ],
        }


@dataclass(frozen=True, slots=True)
class RetentionAuditRecord:
    sequence: int
    time_seconds: float
    kind: RetentionAuditKind
    policy_id: RetentionPolicyID
    state_id: str | None
    reason: str
    resident_state_ids: tuple[str, ...]
    resident_bytes_after: int
    useful_byte_seconds_total: float
    wasted_byte_seconds_total: float
    capacity_outcome: CapacityOutcome

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C73B_AUDIT_SCHEMA,
            "sequence": self.sequence,
            "time_seconds": self.time_seconds,
            "kind": self.kind.value,
            "policy_id": self.policy_id.value,
            "state_id": self.state_id,
            "reason": self.reason,
            "resident_state_ids": list(self.resident_state_ids),
            "resident_bytes_after": self.resident_bytes_after,
            "useful_byte_seconds_total": self.useful_byte_seconds_total,
            "wasted_byte_seconds_total": self.wasted_byte_seconds_total,
            "capacity_outcome": self.capacity_outcome.value,
        }


@dataclass(frozen=True, slots=True)
class RetentionPolicyResult:
    base_c7_manifest_fingerprint: str
    retention_manifest_fingerprint: str
    retention_protocol_fingerprint: str
    program_case_fingerprint: str
    policy_id: RetentionPolicyID
    capacity_outcome: CapacityOutcome
    eligible_reuse_opportunities: int
    consumed_reuse_opportunities: int
    semantic_rejection_count: int
    useful_byte_seconds: float
    wasted_byte_seconds: float
    total_classified_byte_seconds: float
    useful_residency_fraction: float
    wasted_residency_fraction: float
    max_resident_bytes: int
    capacity_bytes: int
    audit: tuple[RetentionAuditRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C73B_RESULT_SCHEMA,
            "base_c7_manifest_fingerprint": self.base_c7_manifest_fingerprint,
            "retention_manifest_fingerprint": self.retention_manifest_fingerprint,
            "retention_protocol_fingerprint": self.retention_protocol_fingerprint,
            "program_case_fingerprint": self.program_case_fingerprint,
            "policy_id": self.policy_id.value,
            "capacity_outcome": self.capacity_outcome.value,
            "eligible_reuse_opportunities": self.eligible_reuse_opportunities,
            "consumed_reuse_opportunities": self.consumed_reuse_opportunities,
            "semantic_rejection_count": self.semantic_rejection_count,
            "useful_byte_seconds": self.useful_byte_seconds,
            "wasted_byte_seconds": self.wasted_byte_seconds,
            "total_classified_byte_seconds": self.total_classified_byte_seconds,
            "useful_residency_fraction": self.useful_residency_fraction,
            "wasted_residency_fraction": self.wasted_residency_fraction,
            "max_resident_bytes": self.max_resident_bytes,
            "capacity_bytes": self.capacity_bytes,
            "audit": [item.to_dict() for item in self.audit],
        }

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


@dataclass(frozen=True, slots=True)
class C73ToolReturnTTFTProjection:
    hardware_id: str
    profile_id: str
    scientific_fingerprint: str
    artifact_sha256: str
    full_context_tokens: int
    consumed_reuse_tokens: int
    recompute_seconds: float
    first_decode_seconds: float
    queue_delay_seconds: float
    ttft_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {"schema": C73B_TTFT_SCHEMA, **{name: getattr(self, name) for name in self.__dataclass_fields__}}


@dataclass(slots=True)
class _Truth:
    valid: bool
    lifecycle: StateLifecycle
    produced: bool = False


@dataclass(slots=True)
class _Resident:
    state_id: str
    interval_start_seconds: float
    last_touch_time_seconds: float
    admission_time_seconds: float
    admission_ordinal: int
    expiry_seconds: float | None


def validate_c73_base_manifest(
    base_manifest: C7ExperimentManifest,
    retention_manifest: C73RetentionManifest,
) -> None:
    if not isinstance(base_manifest, C7ExperimentManifest):
        raise TypeError("base_manifest must be C7ExperimentManifest")
    if not isinstance(retention_manifest, C73RetentionManifest):
        raise TypeError("retention_manifest must be C73RetentionManifest")
    if base_manifest.fingerprint != retention_manifest.base_c7_manifest_fingerprint:
        raise ValueError("retention manifest does not bind the supplied base C7 manifest")
    if base_manifest.series not in {
        ExperimentSeries.P2_TOOL_GAP_RETENTION,
        ExperimentSeries.P3_BRANCH_CACHE_PRESSURE,
    }:
        raise ValueError("C7.3b base manifest must be P2 or P3")
    params = dict(base_manifest.parameters)
    sources = dict(base_manifest.parameter_sources)
    required_parameters = {
        ExperimentSeries.P2_TOOL_GAP_RETENTION: {
            "cache_capacity_ratio",
            "state_tokens",
            "tool_gap_seconds",
            "tool_return_probability",
        },
        ExperimentSeries.P3_BRANCH_CACHE_PRESSURE: {
            "branch_width",
            "cache_capacity_ratio",
            "speculative_fraction",
            "state_tokens",
        },
    }[base_manifest.series]
    if set(params) != required_parameters:
        raise ValueError(
            "C7.3b base manifest must contain exactly the required parameters for its frozen P2/P3 series"
        )
    if set(sources) != required_parameters:
        raise ValueError(
            "C7.3b base manifest parameter sources must exactly cover the required parameters"
        )
    if params.get("state_tokens") != C73_DEFAULT_STATE_TOKENS:
        raise ValueError("C7.3b base manifest must include frozen state_tokens=16")
    if sources.get("state_tokens") is not ParameterSource.P_SRC4:
        raise ValueError("C7.3b state_tokens design point must remain P-SRC4")
    if params.get("cache_capacity_ratio") != retention_manifest.cache_capacity_ratio:
        raise ValueError("base and retention manifests disagree on cache_capacity_ratio")


def reference_working_set_bytes(case: RetentionProgramCase) -> int:
    if not isinstance(case, RetentionProgramCase):
        raise TypeError("case must be RetentionProgramCase")
    specs = {item.state_id: item for item in case.states}
    truth = {
        item.state_id: _Truth(True, item.initial_lifecycle, False)
        for item in case.states
    }
    maximum = 0
    grouped: dict[float, list[RetentionEvent]] = {}
    for event in case.events:
        grouped.setdefault(event.time_seconds, []).append(event)
    for now in sorted(grouped):
        events = sorted(grouped[now], key=lambda x: (x.phase, x.ordinal, x.event_id))
        for event in events:
            if event.kind is RetentionEventKind.INVALIDATE:
                truth[event.state_id].valid = False  # type: ignore[index]
            elif event.kind is RetentionEventKind.DEPENDENTS:
                truth[event.state_id].lifecycle = derive_state_lifecycle(  # type: ignore[index]
                    event.dependent_lifecycles
                )
            elif event.kind is RetentionEventKind.ADMIT:
                truth[event.state_id].produced = True  # type: ignore[index]
        current = sum(
            specs[state_id].size_bytes
            for state_id, state in truth.items()
            if state.produced and state.valid and state.lifecycle is not StateLifecycle.TERMINAL
        )
        maximum = max(maximum, current)
    if maximum <= 0:
        raise ValueError("ground-truth reference working set must be positive")
    return maximum


class _RetentionRun:
    def __init__(
        self,
        case: RetentionProgramCase,
        base_manifest: C7ExperimentManifest,
        manifest: C73RetentionManifest,
    ) -> None:
        validate_c73_base_manifest(base_manifest, manifest)
        if manifest.program_case_fingerprint != case.fingerprint:
            raise ValueError("retention manifest does not bind the supplied Program case")
        expected_reference = reference_working_set_bytes(case)
        if manifest.reference_working_set_bytes != expected_reference:
            raise ValueError("retention manifest reference working set does not match Program truth")
        self.case = case
        self.base_manifest = base_manifest
        self.manifest = manifest
        self.policy = manifest.retention_policy_id
        self.specs = {item.state_id: item for item in case.states}
        self.truth = {item.state_id: _Truth(True, item.initial_lifecycle) for item in case.states}
        self.sessions = {item.session_id: item.initially_live for item in case.sessions}
        self.resident: dict[str, _Resident] = {}
        self.capacity_outcome = CapacityOutcome.ELIGIBLE
        self.eligible_reuse_opportunities = 0
        self.consumed_reuse_opportunities = 0
        self.semantic_rejection_count = 0
        self.useful = 0.0
        self.wasted = 0.0
        self.max_resident_bytes = 0
        self.audit: list[RetentionAuditRecord] = []
        self._sequence = 0

    @property
    def resident_bytes(self) -> int:
        return sum(self.specs[state_id].size_bytes for state_id in self.resident)

    def _record(
        self,
        now: float,
        kind: RetentionAuditKind,
        state_id: str | None,
        reason: str,
    ) -> None:
        self.audit.append(
            RetentionAuditRecord(
                self._sequence,
                now,
                kind,
                self.policy,
                state_id,
                reason,
                tuple(sorted(self.resident)),
                self.resident_bytes,
                self.useful,
                self.wasted,
                self.capacity_outcome,
            )
        )
        self._sequence += 1
        self.max_resident_bytes = max(self.max_resident_bytes, self.resident_bytes)
        if self.resident_bytes > self.manifest.capacity_bytes:
            raise AssertionError("retention engine exposed capacity overcommit after an observable action")

    def _close_interval(
        self,
        state_id: str,
        now: float,
        reason: ResidencyEndReason,
        *,
        remove: bool,
    ) -> None:
        resident = self.resident[state_id]
        useful, wasted = residency_interval_byte_seconds(
            state_size_bytes=self.specs[state_id].size_bytes,
            start_seconds=resident.interval_start_seconds,
            end_seconds=now,
            end_reason=reason,
        )
        self.useful += useful
        self.wasted += wasted
        if remove:
            del self.resident[state_id]
        else:
            resident.interval_start_seconds = now
            resident.last_touch_time_seconds = now

    def _mark_infeasible(self, now: float, state_id: str, reason: str) -> None:
        self.capacity_outcome = CapacityOutcome.CAPACITY_INFEASIBLE
        self._record(now, RetentionAuditKind.CAPACITY_INFEASIBLE, state_id, reason)

    def _process_invalidation(self, event: RetentionEvent) -> None:
        state_id = event.state_id
        self.truth[state_id].valid = False  # type: ignore[index]
        if state_id in self.resident:
            self._close_interval(state_id, event.time_seconds, ResidencyEndReason.INVALIDATION, remove=True)
        self._record(event.time_seconds, RetentionAuditKind.INVALIDATED, state_id, "COMMON_INVALID_RELEASE")

    def _process_truth_update(self, event: RetentionEvent) -> None:
        if event.kind is RetentionEventKind.DEPENDENTS:
            state_id = event.state_id
            lifecycle = derive_state_lifecycle(event.dependent_lifecycles)
            self.truth[state_id].lifecycle = lifecycle  # type: ignore[index]
            self._record(event.time_seconds, RetentionAuditKind.LIFECYCLE_UPDATED, state_id, lifecycle.name)
        elif event.kind is RetentionEventKind.SESSION_STATUS:
            self.sessions[event.session_id] = bool(event.session_live)  # type: ignore[index]
            self._record(
                event.time_seconds,
                RetentionAuditKind.SESSION_STATUS_UPDATED,
                None,
                f"{event.session_id}:{'LIVE' if event.session_live else 'ENDED'}",
            )

    def _policy_releases(self, now: float) -> None:
        to_release: list[tuple[str, str]] = []
        if self.policy is RetentionPolicyID.SESSION_PINNING:
            for state_id in self.resident:
                session_id = self.specs[state_id].session_id
                if not self.sessions[session_id]:
                    to_release.append((state_id, "SESSION_ENDED"))
        elif self.policy is RetentionPolicyID.LIFECYCLE_B4:
            for state_id in self.resident:
                if self.truth[state_id].lifecycle is StateLifecycle.TERMINAL:
                    to_release.append((state_id, "B4_TERMINAL_RELEASE"))
        for state_id, reason in sorted(to_release):
            self._close_interval(state_id, now, ResidencyEndReason.RELEASE, remove=True)
            self._record(now, RetentionAuditKind.POLICY_RELEASED, state_id, reason)

    def _ttl_expiries(self, now: float) -> None:
        if self.policy is not RetentionPolicyID.FIXED_TTL:
            return
        expired = sorted(
            state_id
            for state_id, resident in self.resident.items()
            if resident.expiry_seconds is not None and resident.expiry_seconds <= now
        )
        for state_id in expired:
            expiry = self.resident[state_id].expiry_seconds
            if expiry != now:
                raise AssertionError("TTL expiry must be processed at its exact logical time")
            self._close_interval(state_id, now, ResidencyEndReason.TTL_EXPIRY, remove=True)
            self._record(now, RetentionAuditKind.TTL_EXPIRED, state_id, "FIXED_FROM_ADMISSION")

    def _reuse(self, event: RetentionEvent) -> None:
        state_id = event.state_id
        self.eligible_reuse_opportunities += 1
        semantic_ok = bool(event.semantic_valid) and self.truth[state_id].valid  # type: ignore[index]
        if not semantic_ok:
            self.semantic_rejection_count += 1
            self._record(
                event.time_seconds,
                RetentionAuditKind.REUSE_SEMANTIC_REJECT,
                state_id,
                "INDEPENDENT_SEMANTIC_ORACLE_REJECT",
            )
            return
        if state_id not in self.resident:
            self._record(event.time_seconds, RetentionAuditKind.REUSE_MISS, state_id, "NOT_RESIDENT")
            return
        self.consumed_reuse_opportunities += 1
        self._close_interval(state_id, event.time_seconds, ResidencyEndReason.REUSE, remove=False)
        self._record(event.time_seconds, RetentionAuditKind.REUSE_HIT, state_id, "RESIDENT_AND_SEMANTICALLY_VALID")

    def _active_protected_bytes(self) -> int:
        return sum(
            self.specs[state_id].size_bytes
            for state_id in self.resident
            if self.truth[state_id].lifecycle is StateLifecycle.ACTIVE
        )

    def _admit(self, event: RetentionEvent) -> None:
        state_id = event.state_id
        spec = self.specs[state_id]  # type: ignore[index]
        truth = self.truth[state_id]  # type: ignore[index]
        truth.produced = True
        if not truth.valid:
            self._record(event.time_seconds, RetentionAuditKind.ADMISSION_SKIPPED, state_id, "INVALID_STATE")
            return
        if state_id in self.resident:
            raise ValueError("State cannot be admitted while already resident")
        if self.policy in {RetentionPolicyID.LRU, RetentionPolicyID.FIXED_TTL}:
            if spec.size_bytes > self.manifest.capacity_bytes:
                self._record(event.time_seconds, RetentionAuditKind.ADMISSION_SKIPPED, state_id, "OBJECT_EXCEEDS_CAPACITY")
                return
        elif self.policy is RetentionPolicyID.SESSION_PINNING:
            if not self.sessions[spec.session_id]:
                self._record(event.time_seconds, RetentionAuditKind.ADMISSION_SKIPPED, state_id, "SESSION_NOT_LIVE")
                return
            if self.resident_bytes + spec.size_bytes > self.manifest.capacity_bytes:
                self._mark_infeasible(event.time_seconds, state_id, "SESSION_PINNED_BYTES_EXCEED_CAPACITY")
                return
        elif self.policy is RetentionPolicyID.LIFECYCLE_B4:
            if truth.lifecycle is StateLifecycle.TERMINAL:
                self._record(event.time_seconds, RetentionAuditKind.ADMISSION_SKIPPED, state_id, "B4_TERMINAL_RELEASE")
                return
            if (
                truth.lifecycle is StateLifecycle.ACTIVE
                and self._active_protected_bytes() + spec.size_bytes > self.manifest.capacity_bytes
            ):
                self._mark_infeasible(event.time_seconds, state_id, "B4_ACTIVE_PROTECTED_BYTES_EXCEED_CAPACITY")
                return

        expiry = None
        if self.policy is RetentionPolicyID.FIXED_TTL:
            expiry = event.time_seconds + float(self.manifest.ttl_seconds)
        self.resident[state_id] = _Resident(
            state_id,
            event.time_seconds,
            event.time_seconds,
            event.time_seconds,
            spec.admission_ordinal,
            expiry,
        )
        # Admission precedes capacity enforcement. The audit snapshot is emitted only
        # after enforcement so the observable engine state never exceeds capacity.

    def _capacity_enforce(self, now: float, admitted_ids: tuple[str, ...]) -> None:
        if self.policy is RetentionPolicyID.SESSION_PINNING:
            if self.resident_bytes > self.manifest.capacity_bytes:
                raise AssertionError("session pinning overcommit escaped pre-admission infeasibility")
        elif self.policy in {RetentionPolicyID.LRU, RetentionPolicyID.FIXED_TTL}:
            while self.resident_bytes > self.manifest.capacity_bytes:
                state_id = min(
                    self.resident,
                    key=lambda sid: lru_eviction_key(
                        last_touch_time_seconds=self.resident[sid].last_touch_time_seconds,
                        admission_ordinal=self.resident[sid].admission_ordinal,
                        state_id=sid,
                    ),
                )
                self._close_interval(state_id, now, ResidencyEndReason.EVICTION, remove=True)
                self._record(now, RetentionAuditKind.EVICTED, state_id, "LRU_CAPACITY")
        elif self.policy is RetentionPolicyID.LIFECYCLE_B4:
            if self._active_protected_bytes() > self.manifest.capacity_bytes:
                self._mark_infeasible(now, None, "B4_ACTIVE_PROTECTED_BYTES_EXCEED_CAPACITY")
            while self.resident_bytes > self.manifest.capacity_bytes:
                candidates = [
                    state_id
                    for state_id in self.resident
                    if self.truth[state_id].lifecycle is not StateLifecycle.ACTIVE
                ]
                if not candidates:
                    raise AssertionError("B4 protected overcapacity cannot be repaired by ordinary eviction")
                state_id = min(
                    candidates,
                    key=lambda sid: lifecycle_eviction_key(
                        lifecycle=self.truth[sid].lifecycle,
                        last_touch_time_seconds=self.resident[sid].last_touch_time_seconds,
                        admission_ordinal=self.resident[sid].admission_ordinal,
                        state_id=sid,
                    ),
                )
                lifecycle = self.truth[state_id].lifecycle.name
                self._close_interval(state_id, now, ResidencyEndReason.EVICTION, remove=True)
                self._record(now, RetentionAuditKind.EVICTED, state_id, f"B4_{lifecycle}_CAPACITY")
        for state_id in admitted_ids:
            if state_id in self.resident:
                self._record(now, RetentionAuditKind.ADMITTED, state_id, "RESIDENT_AFTER_CAPACITY_ENFORCEMENT")
            elif not any(
                record.time_seconds == now
                and record.state_id == state_id
                and record.kind in {RetentionAuditKind.ADMISSION_SKIPPED, RetentionAuditKind.CAPACITY_INFEASIBLE, RetentionAuditKind.EVICTED}
                for record in self.audit
            ):
                self._record(now, RetentionAuditKind.ADMISSION_SKIPPED, state_id, "NOT_RESIDENT_AFTER_CAPACITY_ENFORCEMENT")

    def _next_expiry(self, after: float) -> float | None:
        if self.policy is not RetentionPolicyID.FIXED_TTL:
            return None
        values = [
            resident.expiry_seconds
            for resident in self.resident.values()
            if resident.expiry_seconds is not None and resident.expiry_seconds > after
        ]
        return None if not values else min(values)

    def run(self) -> RetentionPolicyResult:
        external: dict[float, list[RetentionEvent]] = {}
        for event in self.case.events:
            external.setdefault(event.time_seconds, []).append(event)
        external_times = sorted(external)
        external_index = 0
        last_time = self.case.program_start_seconds - 1.0

        while True:
            next_external = (
                external_times[external_index] if external_index < len(external_times) else None
            )
            next_expiry = self._next_expiry(last_time)
            candidates = [
                value
                for value in (next_external, next_expiry)
                if value is not None and value <= self.case.program_end_seconds
            ]
            if not candidates:
                break
            now = min(candidates)
            events = []
            if next_external == now:
                events = sorted(external[now], key=lambda x: (x.phase, x.ordinal, x.event_id))
                external_index += 1

            for event in events:
                if event.kind is RetentionEventKind.INVALIDATE:
                    self._process_invalidation(event)
            for event in events:
                if event.kind in {RetentionEventKind.DEPENDENTS, RetentionEventKind.SESSION_STATUS}:
                    self._process_truth_update(event)
            self._policy_releases(now)
            self._ttl_expiries(now)
            for event in events:
                if event.kind is RetentionEventKind.REUSE:
                    self._reuse(event)
            admitted: list[str] = []
            for event in events:
                if event.kind is RetentionEventKind.ADMIT:
                    before = len(self.resident)
                    self._admit(event)
                    if len(self.resident) > before:
                        admitted.append(event.state_id)  # type: ignore[arg-type]
            self._capacity_enforce(now, tuple(admitted))
            self._record(now, RetentionAuditKind.CHECKPOINT, None, "INTERVAL_ACCOUNTING_CHECKPOINT")
            last_time = now

        if external_index != len(external_times):
            raise AssertionError("retention engine left external events unprocessed")
        # Program end is after the frozen equal-time event phases and closes every
        # remaining classified residency interval as wasted resident byte-time.
        for state_id in sorted(tuple(self.resident)):
            self._close_interval(
                state_id,
                self.case.program_end_seconds,
                ResidencyEndReason.PROGRAM_END,
                remove=True,
            )
            self._record(
                self.case.program_end_seconds,
                RetentionAuditKind.PROGRAM_END_RELEASE,
                state_id,
                "PROGRAM_END",
            )
        self._record(
            self.case.program_end_seconds,
            RetentionAuditKind.CHECKPOINT,
            None,
            "PROGRAM_END_ACCOUNTING_CHECKPOINT",
        )

        total = self.useful + self.wasted
        useful_fraction = 0.0 if total == 0 else self.useful / total
        wasted_fraction = 0.0 if total == 0 else self.wasted / total
        if total and not math.isclose(useful_fraction + wasted_fraction, 1.0, rel_tol=0.0, abs_tol=1e-15):
            raise AssertionError("useful/wasted residency does not partition classified byte-time")
        return RetentionPolicyResult(
            self.base_manifest.fingerprint,
            self.manifest.fingerprint,
            C73_RETENTION_PROTOCOL_FINGERPRINT,
            self.case.fingerprint,
            self.policy,
            self.capacity_outcome,
            self.eligible_reuse_opportunities,
            self.consumed_reuse_opportunities,
            self.semantic_rejection_count,
            self.useful,
            self.wasted,
            total,
            useful_fraction,
            wasted_fraction,
            self.max_resident_bytes,
            self.manifest.capacity_bytes,
            tuple(self.audit),
        )


def run_retention_program(
    *,
    case: RetentionProgramCase,
    base_manifest: C7ExperimentManifest,
    retention_manifest: C73RetentionManifest,
) -> RetentionPolicyResult:
    if not isinstance(case, RetentionProgramCase):
        raise TypeError("case must be RetentionProgramCase")
    return _RetentionRun(case, base_manifest, retention_manifest).run()


def tool_return_ttft_from_profile(
    *,
    profile: ValidatedRuntimeCostProfile,
    expected_hardware_id: str,
    resume_eligibility_seconds: float,
    service_start_seconds: float,
    full_context_tokens: int,
    consumed_reuse_tokens: int,
) -> C73ToolReturnTTFTProjection:
    if not isinstance(profile, ValidatedRuntimeCostProfile):
        raise TypeError("profile must be ValidatedRuntimeCostProfile")
    if profile.hardware_id != expected_hardware_id:
        raise ValueError("C6 runtime profile hardware does not match the base C7 manifest")
    if profile.scientific_fingerprint != C6_SCIENTIFIC_FINGERPRINT:
        raise ValueError("C6 scientific fingerprint drift")
    if profile.artifact_sha256 != C6_ARTIFACT_SHA256:
        raise ValueError("C6 artifact SHA drift")
    if profile.evidence_class != C6_EVIDENCE_CLASS:
        raise ValueError("C6 evidence class drift")
    if profile.state_fixed_bytes != 0.0 or profile.state_bytes_per_token != 524288.0:
        raise ValueError("C6.4f State-memory scalar drift")
    _positive_int(full_context_tokens, "full_context_tokens")
    _nni(consumed_reuse_tokens, "consumed_reuse_tokens")
    workload = InferenceCostWorkload(
        full_context_tokens,
        1,
        consumed_reuse_tokens,
        0,
    )
    estimate = estimate_validated_runtime_cost(profile, workload)
    resume = _nnf(resume_eligibility_seconds, "resume_eligibility_seconds")
    start = _nnf(service_start_seconds, "service_start_seconds")
    ttft = tool_return_ttft_seconds(
        resume_eligibility_seconds=resume,
        service_start_seconds=start,
        recompute_prefill_seconds=estimate.recompute_seconds,
        decode_fixed_seconds=profile.decode_fixed_seconds_per_output_token,
        decode_seconds_per_context_token_step=profile.decode_seconds_per_context_token_step,
        full_context_tokens=full_context_tokens,
    )
    return C73ToolReturnTTFTProjection(
        profile.hardware_id,
        profile.profile_id,
        profile.scientific_fingerprint,
        profile.artifact_sha256,
        full_context_tokens,
        consumed_reuse_tokens,
        estimate.recompute_seconds,
        estimate.decode_seconds,
        start - resume,
        ttft,
    )


def main() -> None:
    print(
        _json(
            {
                "schema": C73B_ENGINE_SCHEMA,
                "base_commit": C73B_BASE_COMMIT,
                "retention_protocol_fingerprint": C73_RETENTION_PROTOCOL_FINGERPRINT,
                "state_size_map_fingerprint": C73_STATE_SIZE_MAP_FINGERPRINT,
                "comparative_result_inspection": "NONE",
                "supported_policies": [policy.value for policy in RetentionPolicyID],
                "result_schema": C73B_RESULT_SCHEMA,
                "audit_schema": C73B_AUDIT_SCHEMA,
            }
        )
    )


if __name__ == "__main__":
    main()
