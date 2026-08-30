from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping

from .engine import DiscreteEventSimulator
from .events import EventKind, SimEvent, freeze_payload


REPRESENTABILITY_SCHEMA = "cadi.representability.v1"
_TIME_SENTINEL = "$TIME"


class ScenarioFamily(str, Enum):
    WORKLOAD = "WORKLOAD"
    FAILURE = "FAILURE"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _finite_nonnegative(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return numeric


def _seed_jitter(scenario_name: str, seed: int, ordinal: int) -> float:
    digest = hashlib.sha256(f"{scenario_name}\0{seed}\0{ordinal}".encode("utf-8")).digest()
    numerator = int.from_bytes(digest[:8], "big")
    return (numerator / 2**64) * 1e-6


def _payload_dict(payload: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    return {key: value for key, value in payload}


@dataclass(frozen=True, slots=True)
class ScenarioStep:
    offset: float
    kind: EventKind
    label: str
    payload: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        _finite_nonnegative(self.offset, "offset")
        if not isinstance(self.kind, EventKind):
            raise TypeError("kind must be EventKind")
        _require_id(self.label, "label")
        if not isinstance(self.payload, tuple):
            raise TypeError("payload must be frozen")
        rebuilt = freeze_payload(_payload_dict(self.payload))
        if rebuilt != self.payload:
            raise ValueError("payload must be canonical frozen event payload")


@dataclass(frozen=True, slots=True)
class ScenarioEvent:
    time: float
    event_id: str
    kind: EventKind
    payload: tuple[tuple[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "event_id": self.event_id,
            "kind": self.kind.value,
            "payload": _payload_dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class ScenarioSchedule:
    stable_name: str
    catalogue_id: str
    family: ScenarioFamily
    seed: int
    events: tuple[ScenarioEvent, ...]

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REPRESENTABILITY_SCHEMA,
            "stable_name": self.stable_name,
            "catalogue_id": self.catalogue_id,
            "family": self.family.value,
            "seed": self.seed,
            "events": [event.to_dict() for event in self.events],
        }

    def apply(self, simulator: DiscreteEventSimulator) -> tuple[SimEvent, ...]:
        if not isinstance(simulator, DiscreteEventSimulator):
            raise TypeError("simulator must be DiscreteEventSimulator")
        scheduled = []
        for event in self.events:
            scheduled.append(
                simulator.schedule(
                    event.kind,
                    at=event.time,
                    event_id=event.event_id,
                    payload=_payload_dict(event.payload),
                )
            )
        return tuple(scheduled)


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    stable_name: str
    catalogue_id: str
    family: ScenarioFamily
    summary: str
    steps: tuple[ScenarioStep, ...]
    c1_reference: str | None = None
    executable_authoritative_equivalence: bool = False

    def __post_init__(self) -> None:
        _require_id(self.stable_name, "stable_name")
        _require_id(self.catalogue_id, "catalogue_id")
        _require_id(self.summary, "summary")
        if not isinstance(self.family, ScenarioFamily):
            raise TypeError("family must be ScenarioFamily")
        if not isinstance(self.steps, tuple) or not self.steps:
            raise ValueError("steps must be a non-empty tuple")
        expected_prefix = "W" if self.family is ScenarioFamily.WORKLOAD else "FTR"
        suffix = self.catalogue_id.removeprefix(expected_prefix)
        if not self.catalogue_id.startswith(expected_prefix) or not suffix.isdigit():
            raise ValueError("catalogue_id does not match scenario family")
        if self.c1_reference is not None:
            _require_id(self.c1_reference, "c1_reference")
        if self.executable_authoritative_equivalence and self.c1_reference is None:
            raise ValueError("executable equivalence requires a C1 reference")

    def build(self, *, seed: int = 0) -> ScenarioSchedule:
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise TypeError("seed must be an integer")
        events = []
        previous = -1.0
        for ordinal, step in enumerate(self.steps):
            event_time = float(step.offset) + _seed_jitter(self.stable_name, seed, ordinal)
            if event_time < previous:
                raise AssertionError("scenario template ordering is not monotonic")
            previous = event_time
            payload = _payload_dict(step.payload)
            for key, value in tuple(payload.items()):
                if value == _TIME_SENTINEL:
                    payload[key] = event_time
            events.append(
                ScenarioEvent(
                    time=event_time,
                    event_id=f"scenario:{self.stable_name}:{ordinal:02d}:{step.label}",
                    kind=step.kind,
                    payload=freeze_payload(payload),
                )
            )
        return ScenarioSchedule(
            stable_name=self.stable_name,
            catalogue_id=self.catalogue_id,
            family=self.family,
            seed=seed,
            events=tuple(events),
        )


def _step(offset: float, kind: EventKind, label: str, **payload: Any) -> ScenarioStep:
    return ScenarioStep(offset, kind, label, freeze_payload(payload))


C1_FAILURE_REFERENCES: Mapping[str, str] = {
    "FTR1": "tests/counterexamples/test_failure_model_traces.py::test_ftr1_late_stale_completion_cannot_regain_authority",
    "FTR2": "tests/counterexamples/test_failure_model_traces.py::test_ftr2_duplicate_result_finalizes_once",
    "FTR3": "tests/counterexamples/test_failure_model_traces.py::test_ftr3_reordered_old_retry_event_cannot_override_committed_attempt",
    "FTR4": "tests/counterexamples/test_failure_model_traces.py::test_ftr4_wrong_sibling_state_is_rejected",
    "FTR5": "tests/invariants/test_state.py::test_superseded_producer_state_rejected",
    "FTR6": "tests/counterexamples/test_failure_model_traces.py::test_ftr5_total_physical_state_loss_preserves_logical_provenance",
    "FTR7": "tests/counterexamples/test_failure_model_traces.py::test_ftr6_partial_migration_does_not_commit_destination",
    "FTR8": "tests/counterexamples/test_failure_model_traces.py::test_ftr7_destination_failure_before_commit_leaves_source_authoritative",
    "FTR9": "tests/counterexamples/test_failure_model_traces.py::test_ftr8_late_old_binding_observation_cannot_restore_old_owner",
    "FTR10": "tests/invariants/test_binding_evidence.py::test_concurrent_migration_candidate_fencing",
    "FTR11": "tests/counterexamples/test_failure_model_traces.py::test_ftr9_ambiguous_ownership_fails_closed",
    "FTR12": "tests/counterexamples/test_failure_model_traces.py::test_ftr10_stale_high_authority_evidence_is_insufficient",
    "FTR13": "tests/counterexamples/test_failure_model_traces.py::test_ftr11_tool_wait_eviction_preserves_lineage_but_forces_cold_resume",
    "FTR14": "tests/counterexamples/test_failure_model_traces.py::test_ftr12_abandoned_branch_residual_state_cannot_reactivate_or_cross_branch",
}


SCENARIOS: tuple[ScenarioDefinition, ...] = (
    ScenarioDefinition("independent-requests", "W1", ScenarioFamily.WORKLOAD, "Independent requests with no cross-request continuity.", (
        _step(0, EventKind.REQUEST_CREATED, "r1-created", request_id="r1", continuation_id="c1"),
        _step(1, EventKind.ATTEMPT_STARTED, "a1-started", request_id="r1", attempt_id="a1"),
        _step(2, EventKind.ATTEMPT_COMPLETED, "a1-completed", attempt_id="a1"),
        _step(3, EventKind.REQUEST_CREATED, "r2-created", request_id="r2", continuation_id="c2"),
        _step(4, EventKind.ATTEMPT_STARTED, "a2-started", request_id="r2", attempt_id="a2"),
        _step(5, EventKind.ATTEMPT_COMPLETED, "a2-completed", attempt_id="a2"),
    )),
    ScenarioDefinition("deep-stateful-session", "W2", ScenarioFamily.WORKLOAD, "Linear continuation chain with reusable ancestor State.", (
        _step(0, EventKind.REQUEST_CREATED, "root-request", request_id="r0", continuation_id="c0"),
        _step(1, EventKind.STATE_CREATED, "root-state", state_id="x0", continuation_id="c0"),
        _step(2, EventKind.REQUEST_CREATED, "child-request", request_id="r1", continuation_id="c1"),
        _step(3, EventKind.STATE_CREATED, "child-state", state_id="x1", continuation_id="c1", parent_state_id="x0"),
        _step(4, EventKind.REQUEST_CREATED, "deep-request", request_id="r2", continuation_id="c2"),
    )),
    ScenarioDefinition("tool-gap-resume", "W3", ScenarioFamily.WORKLOAD, "Continuation waits on an external tool and resumes as a child Continuation.", (
        _step(0, EventKind.REQUEST_CREATED, "pretool-request", request_id="r1", continuation_id="c1"),
        _step(1, EventKind.TOOL_WAIT_STARTED, "tool-wait", continuation_id="c1"),
        _step(4, EventKind.TOOL_RETURNED, "tool-return", continuation_id="c1", child_continuation_id="c2"),
        _step(5, EventKind.REQUEST_CREATED, "resume-request", request_id="r2", continuation_id="c2"),
        _step(6, EventKind.CONTINUATION_TERMINATED, "parent-terminal", continuation_id="c1"),
    )),
    ScenarioDefinition("retry-race", "W4", ScenarioFamily.WORKLOAD, "Overlapping retry attempts with a late completion from the superseded Attempt.", (
        _step(0, EventKind.REQUEST_CREATED, "request", request_id="r", continuation_id="c"),
        _step(1, EventKind.ATTEMPT_STARTED, "a1-start", request_id="r", attempt_id="a1"),
        _step(2, EventKind.ATTEMPT_TIMEOUT, "a1-timeout", request_id="r", timed_out_attempt_id="a1", retry_attempt_id="a2"),
        _step(4, EventKind.ATTEMPT_COMPLETED, "a2-complete", attempt_id="a2"),
        _step(5, EventKind.LATE_RESULT, "a1-late", attempt_id="a1"),
    )),
    ScenarioDefinition("stateful-failover", "W5", ScenarioFamily.WORKLOAD, "Worker failure while reusable State is physically resident.", (
        _step(0, EventKind.STATE_CREATED, "state", state_id="x", continuation_id="c"),
        _step(1, EventKind.STATE_MATERIALIZED, "state-on-w1", state_id="x", replica_id="rp1", location_id="w1"),
        _step(2, EventKind.WORKER_FAILED, "w1-failed", worker_id="w1"),
        _step(3, EventKind.STATE_LOST, "replica-lost", replica_id="rp1", state_id="x"),
        _step(4, EventKind.RETRY_STARTED, "retry", request_id="r", superseded_attempt_id="a1", retry_attempt_id="a2"),
    )),
    ScenarioDefinition("branching-program", "W6", ScenarioFamily.WORKLOAD, "Sibling Continuations with physically attractive but causally distinct State.", (
        _step(0, EventKind.CONTINUATION_FORKED, "fork-c1", parent_continuation_id="c0", child_continuation_id="c1"),
        _step(1, EventKind.CONTINUATION_FORKED, "fork-c2", parent_continuation_id="c0", child_continuation_id="c2"),
        _step(2, EventKind.STATE_CREATED, "c1-state", state_id="x1", continuation_id="c1"),
        _step(3, EventKind.REQUEST_CREATED, "c2-request", request_id="r2", continuation_id="c2"),
    )),
    ScenarioDefinition("fanout-fanin", "W7", ScenarioFamily.WORKLOAD, "Shared ancestor fans out to parallel branches and later joins.", (
        _step(0, EventKind.STATE_CREATED, "shared-state", state_id="x0", continuation_id="c0"),
        _step(1, EventKind.CONTINUATION_FORKED, "fork-c1", parent_continuation_id="c0", child_continuation_id="c1"),
        _step(2, EventKind.CONTINUATION_FORKED, "fork-c2", parent_continuation_id="c0", child_continuation_id="c2"),
        _step(3, EventKind.CONTINUATION_FORKED, "fork-c3", parent_continuation_id="c0", child_continuation_id="c3"),
        _step(6, EventKind.CONTINUATION_JOINED, "join", parent_continuation_ids="c1,c2,c3", continuation_id="cj"),
    )),
    ScenarioDefinition("cache-pressure", "W8", ScenarioFamily.WORKLOAD, "ACTIVE, WAITING, SPECULATIVE, and TERMINAL State compete for limited capacity.", (
        _step(0, EventKind.STATE_CREATED, "active-state", state_id="xa", lifecycle="ACTIVE"),
        _step(1, EventKind.STATE_CREATED, "waiting-state", state_id="xw", lifecycle="WAITING"),
        _step(2, EventKind.STATE_CREATED, "speculative-state", state_id="xs", lifecycle="SPECULATIVE"),
        _step(3, EventKind.STATE_CREATED, "terminal-state", state_id="xt", lifecycle="TERMINAL"),
        _step(4, EventKind.STATE_EVICTED, "pressure-eviction", replica_id="rp-terminal", state_id="xt"),
    )),
    ScenarioDefinition("stale-evidence", "W9", ScenarioFamily.WORKLOAD, "Observation delivery is delayed relative to the fact it reports.", (
        _step(0, EventKind.OBSERVATION_CREATED, "fresh", evidence_id="e-fresh", subject_id="x", observed_at=_TIME_SENTINEL),
        _step(3, EventKind.OBSERVATION_DELAYED, "delayed", evidence_id="e-old", subject_id="x", observed_at=0.5),
        _step(5, EventKind.OBSERVATION_CREATED, "contradictory", evidence_id="e-new", subject_id="x", observed_at=_TIME_SENTINEL),
    )),
    ScenarioDefinition("state-migration", "W10", ScenarioFamily.WORKLOAD, "Physical State transfer and migration commit are represented separately.", (
        _step(0, EventKind.STATE_TRANSFER_STARTED, "transfer-start", transfer_id="t1", replica_id="rp", source_id="w1", destination_id="w2"),
        _step(1, EventKind.MIGRATION_STARTED, "migration-start", binding_id="b2", base_epoch=1, epoch=2),
        _step(4, EventKind.STATE_TRANSFER_COMPLETED, "transfer-complete", transfer_id="t1"),
        _step(5, EventKind.STATE_MOVED, "state-moved", replica_id="rp", source_id="w1", destination_id="w2"),
        _step(6, EventKind.MIGRATION_COMMITTED, "migration-commit", binding_id="b2", epoch=2),
    )),
    ScenarioDefinition("late-stale-completion", "FTR1", ScenarioFamily.FAILURE, "Late completion from A1 after A2 becomes authoritative and commits.", (
        _step(0, EventKind.REQUEST_CREATED, "request", request_id="r", continuation_id="c"),
        _step(1, EventKind.ATTEMPT_STARTED, "a1-start", request_id="r", attempt_id="a1"),
        _step(2, EventKind.ATTEMPT_TIMEOUT, "a1-timeout", request_id="r", timed_out_attempt_id="a1", retry_attempt_id="a2"),
        _step(4, EventKind.ATTEMPT_COMPLETED, "a2-complete", attempt_id="a2"),
        _step(5, EventKind.OBSERVATION_CREATED, "a2-observe", request_id="r", attempt_id="a2", evidence_id="e2", output_id="o2", observed_at=_TIME_SENTINEL),
        _step(6, EventKind.LATE_RESULT, "a1-late", attempt_id="a1"),
    ), C1_FAILURE_REFERENCES["FTR1"], True),
    ScenarioDefinition("duplicate-result", "FTR2", ScenarioFamily.FAILURE, "Duplicate terminal observation must remain one logical finalization.", (
        _step(0, EventKind.REQUEST_CREATED, "request", request_id="r", continuation_id="c"),
        _step(1, EventKind.ATTEMPT_STARTED, "a1-start", request_id="r", attempt_id="a1"),
        _step(2, EventKind.ATTEMPT_COMPLETED, "a1-complete", attempt_id="a1"),
        _step(3, EventKind.OBSERVATION_CREATED, "first-observe", request_id="r", attempt_id="a1", evidence_id="e1", output_id="o1", observed_at=_TIME_SENTINEL),
        _step(4, EventKind.OBSERVATION_DUPLICATED, "duplicate-observe", request_id="r", attempt_id="a1", evidence_id="e1", output_id="o1", observed_at=3.0),
    ), C1_FAILURE_REFERENCES["FTR2"], True),
    ScenarioDefinition("reordered-retry-events", "FTR3", ScenarioFamily.FAILURE, "Older retry completion is delivered after a newer Attempt has committed.", (
        _step(0, EventKind.REQUEST_CREATED, "request", request_id="r", continuation_id="c"),
        _step(1, EventKind.ATTEMPT_STARTED, "a1-start", request_id="r", attempt_id="a1"),
        _step(2, EventKind.RETRY_STARTED, "a2-start", request_id="r", superseded_attempt_id="a1", retry_attempt_id="a2"),
        _step(3, EventKind.ATTEMPT_COMPLETED, "a2-complete", attempt_id="a2"),
        _step(4, EventKind.OBSERVATION_CREATED, "a2-observe", request_id="r", attempt_id="a2", evidence_id="e2", output_id="o2", observed_at=_TIME_SENTINEL),
        _step(5, EventKind.LATE_RESULT, "a1-late", attempt_id="a1"),
        _step(6, EventKind.OBSERVATION_CREATED, "a1-observe", request_id="r", attempt_id="a1", evidence_id="e1", output_id="o1", observed_at=_TIME_SENTINEL),
    ), C1_FAILURE_REFERENCES["FTR3"], True),
    ScenarioDefinition("wrong-sibling-state", "FTR4", ScenarioFamily.FAILURE, "State from sibling C1 is presented while executing C2.", (
        _step(0, EventKind.CONTINUATION_FORKED, "fork-c1", parent_continuation_id="c0", child_continuation_id="c1"),
        _step(1, EventKind.CONTINUATION_FORKED, "fork-c2", parent_continuation_id="c0", child_continuation_id="c2"),
        _step(2, EventKind.STATE_CREATED, "sibling-state", state_id="x1", continuation_id="c1"),
        _step(3, EventKind.REQUEST_CREATED, "consumer", request_id="r2", continuation_id="c2"),
    ), C1_FAILURE_REFERENCES["FTR4"]),
    ScenarioDefinition("superseded-producer-state", "FTR5", ScenarioFamily.FAILURE, "State produced by A1 remains physically present after A2 supersedes it.", (
        _step(0, EventKind.REQUEST_CREATED, "request", request_id="r", continuation_id="c"),
        _step(1, EventKind.ATTEMPT_STARTED, "a1-start", request_id="r", attempt_id="a1"),
        _step(2, EventKind.STATE_CREATED, "a1-state", state_id="x1", producer_attempt_id="a1", continuation_id="c"),
        _step(3, EventKind.RETRY_STARTED, "a2-start", request_id="r", superseded_attempt_id="a1", retry_attempt_id="a2"),
        _step(4, EventKind.STATE_MATERIALIZED, "residual-state", state_id="x1", replica_id="rp1", location_id="w1"),
    ), C1_FAILURE_REFERENCES["FTR5"]),
    ScenarioDefinition("lost-valid-state", "FTR6", ScenarioFamily.FAILURE, "All physical replicas disappear while logical State provenance remains known.", (
        _step(0, EventKind.STATE_CREATED, "state", state_id="x", continuation_id="c"),
        _step(1, EventKind.STATE_MATERIALIZED, "replica", state_id="x", replica_id="rp", location_id="w1"),
        _step(2, EventKind.STATE_LOST, "replica-lost", state_id="x", replica_id="rp"),
    ), C1_FAILURE_REFERENCES["FTR6"]),
    ScenarioDefinition("partial-migration", "FTR7", ScenarioFamily.FAILURE, "Destination materialization remains partial and migration is not committed.", (
        _step(0, EventKind.MIGRATION_STARTED, "migration-start", binding_id="b2", base_epoch=1, epoch=2),
        _step(1, EventKind.STATE_TRANSFER_STARTED, "transfer-start", transfer_id="t", source_id="w1", destination_id="w2"),
        _step(3, EventKind.STATE_TRANSFER_FAILED, "transfer-failed", transfer_id="t"),
        _step(4, EventKind.MIGRATION_FAILED, "migration-failed", binding_id="b2", epoch=2),
    ), C1_FAILURE_REFERENCES["FTR7"]),
    ScenarioDefinition("destination-crash-before-commit", "FTR8", ScenarioFamily.FAILURE, "Migration destination fails before the ownership commit point.", (
        _step(0, EventKind.MIGRATION_STARTED, "migration-start", binding_id="b2", base_epoch=1, epoch=2),
        _step(1, EventKind.STATE_TRANSFER_STARTED, "transfer-start", transfer_id="t", source_id="w1", destination_id="w2"),
        _step(2, EventKind.WORKER_FAILED, "destination-failed", worker_id="w2"),
        _step(3, EventKind.STATE_TRANSFER_FAILED, "transfer-failed", transfer_id="t"),
        _step(4, EventKind.MIGRATION_FAILED, "migration-failed", binding_id="b2", epoch=2),
    ), C1_FAILURE_REFERENCES["FTR8"]),
    ScenarioDefinition("late-old-binding-event", "FTR9", ScenarioFamily.FAILURE, "An observation for the old Binding arrives after the new Binding commits.", (
        _step(0, EventKind.MIGRATION_STARTED, "migration-start", binding_id="b2", base_epoch=1, epoch=2),
        _step(2, EventKind.MIGRATION_COMMITTED, "migration-commit", binding_id="b2", epoch=2),
        _step(4, EventKind.OBSERVATION_DELAYED, "old-binding-observed", binding_id="b1", epoch=1, location_id="w1", observed_at=1.0),
    ), C1_FAILURE_REFERENCES["FTR9"]),
    ScenarioDefinition("losing-concurrent-migration-candidate", "FTR10", ScenarioFamily.FAILURE, "Two candidates share a base epoch; the losing candidate completes late.", (
        _step(0, EventKind.MIGRATION_STARTED, "candidate-b2", binding_id="b2", base_epoch=1, epoch=2),
        _step(1, EventKind.MIGRATION_STARTED, "candidate-b3", binding_id="b3", base_epoch=1, epoch=3),
        _step(3, EventKind.MIGRATION_COMMITTED, "b2-commit", binding_id="b2", epoch=2),
        _step(5, EventKind.STATE_TRANSFER_COMPLETED, "b3-late-transfer", transfer_id="t3", binding_id="b3", epoch=3),
        _step(6, EventKind.MIGRATION_FAILED, "b3-fenced", binding_id="b3", epoch=3),
    ), C1_FAILURE_REFERENCES["FTR10"]),
    ScenarioDefinition("ambiguous-ownership", "FTR11", ScenarioFamily.FAILURE, "Contradictory ownership observations remain explicitly ambiguous.", (
        _step(0, EventKind.OBSERVATION_CREATED, "owner-w1", binding_id="b2", epoch=2, location_id="w1", status="AMBIGUOUS", observed_at=_TIME_SENTINEL),
        _step(1, EventKind.OBSERVATION_CREATED, "owner-w2", binding_id="b2", epoch=2, location_id="w2", status="AMBIGUOUS", observed_at=_TIME_SENTINEL),
    ), C1_FAILURE_REFERENCES["FTR11"]),
    ScenarioDefinition("stale-high-authority-evidence", "FTR12", ScenarioFamily.FAILURE, "High-authority Evidence is delivered after its action-valid freshness window.", (
        _step(0, EventKind.OBSERVATION_CREATED, "authoritative-observation", evidence_id="e", subject_id="a", authority="AUTHORITATIVE", observed_at=_TIME_SENTINEL),
        _step(5, EventKind.OBSERVATION_DELAYED, "stale-delivery", evidence_id="e", subject_id="a", authority="AUTHORITATIVE", observed_at=0.0),
    ), C1_FAILURE_REFERENCES["FTR12"]),
    ScenarioDefinition("tool-wait-eviction", "FTR13", ScenarioFamily.FAILURE, "State is evicted while the parent Continuation waits on an external tool.", (
        _step(0, EventKind.STATE_CREATED, "state", state_id="x", continuation_id="c1"),
        _step(1, EventKind.TOOL_WAIT_STARTED, "wait", continuation_id="c1"),
        _step(2, EventKind.STATE_EVICTED, "evict", state_id="x", replica_id="rp"),
        _step(5, EventKind.TOOL_RETURNED, "return", continuation_id="c1", child_continuation_id="c2"),
        _step(6, EventKind.REQUEST_CREATED, "resume", request_id="r2", continuation_id="c2"),
        _step(7, EventKind.CONTINUATION_TERMINATED, "parent-terminal", continuation_id="c1"),
    ), C1_FAILURE_REFERENCES["FTR13"]),
    ScenarioDefinition("abandoned-branch-residual-state", "FTR14", ScenarioFamily.FAILURE, "Residual State survives after its branch is abandoned and must not reactivate it.", (
        _step(0, EventKind.CONTINUATION_FORKED, "fork-c1", parent_continuation_id="c0", child_continuation_id="c1"),
        _step(1, EventKind.CONTINUATION_FORKED, "fork-c2", parent_continuation_id="c0", child_continuation_id="c2"),
        _step(2, EventKind.STATE_CREATED, "c1-state", state_id="x1", continuation_id="c1"),
        _step(3, EventKind.STATE_MATERIALIZED, "residual-replica", state_id="x1", replica_id="rp1", location_id="w1"),
        _step(4, EventKind.CONTINUATION_ABANDONED, "abandon-c1", continuation_id="c1"),
        _step(5, EventKind.REQUEST_CREATED, "c2-request", request_id="r2", continuation_id="c2"),
    ), C1_FAILURE_REFERENCES["FTR14"]),
)


SCENARIO_BY_NAME: Mapping[str, ScenarioDefinition] = {item.stable_name: item for item in SCENARIOS}
SCENARIO_BY_CATALOGUE_ID: Mapping[str, ScenarioDefinition] = {item.catalogue_id: item for item in SCENARIOS}
WORKLOAD_SCENARIOS = tuple(item for item in SCENARIOS if item.family is ScenarioFamily.WORKLOAD)
FAILURE_SCENARIOS = tuple(item for item in SCENARIOS if item.family is ScenarioFamily.FAILURE)


def _validate_registry() -> None:
    if len(SCENARIO_BY_NAME) != len(SCENARIOS):
        raise AssertionError("representability registry contains duplicate stable scenario names")
    if len(SCENARIO_BY_CATALOGUE_ID) != len(SCENARIOS):
        raise AssertionError("representability registry contains duplicate catalogue IDs")
    expected_workloads = {f"W{index}" for index in range(1, 11)}
    expected_failures = {f"FTR{index}" for index in range(1, 15)}
    actual_workloads = {item.catalogue_id for item in WORKLOAD_SCENARIOS}
    actual_failures = {item.catalogue_id for item in FAILURE_SCENARIOS}
    if actual_workloads != expected_workloads:
        raise AssertionError(f"W1-W10 representability coverage mismatch: {actual_workloads!r}")
    if actual_failures != expected_failures:
        raise AssertionError(f"FTR1-FTR14 representability coverage mismatch: {actual_failures!r}")
    if set(C1_FAILURE_REFERENCES) != expected_failures:
        raise AssertionError("C1 semantic-reference map must cover canonical FTR1-FTR14")


_validate_registry()


def scenario_definition(name_or_catalogue_id: str) -> ScenarioDefinition:
    _require_id(name_or_catalogue_id, "name_or_catalogue_id")
    definition = SCENARIO_BY_NAME.get(name_or_catalogue_id)
    if definition is None:
        definition = SCENARIO_BY_CATALOGUE_ID.get(name_or_catalogue_id)
    if definition is None:
        raise KeyError(f"unknown representability scenario: {name_or_catalogue_id}")
    return definition


def build_scenario_schedule(name_or_catalogue_id: str, *, seed: int = 0) -> ScenarioSchedule:
    return scenario_definition(name_or_catalogue_id).build(seed=seed)


def trace_fingerprint(trace: tuple[SimEvent, ...]) -> str:
    value = [
        {
            "time": float(event.time),
            "sequence": event.sequence,
            "event_id": event.event_id,
            "kind": event.kind.value,
            "payload": _payload_dict(event.payload),
        }
        for event in trace
    ]
    return _fingerprint(value)


def replay_scenario(name_or_catalogue_id: str, *, seed: int = 0) -> tuple[str, str]:
    schedule = build_scenario_schedule(name_or_catalogue_id, seed=seed)
    simulator = DiscreteEventSimulator(seed=seed)
    schedule.apply(simulator)
    simulator.run()
    return schedule.fingerprint, trace_fingerprint(simulator.trace)


def assert_same_seed_replay(name_or_catalogue_id: str, *, seed: int = 0) -> tuple[str, str]:
    left = replay_scenario(name_or_catalogue_id, seed=seed)
    right = replay_scenario(name_or_catalogue_id, seed=seed)
    if left != right:
        raise AssertionError(
            f"same-seed representability replay diverged for {name_or_catalogue_id}: "
            f"{left!r} != {right!r}"
        )
    return left
