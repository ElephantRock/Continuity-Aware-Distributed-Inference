from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from continuity.entities import AttemptAuthority, ExecutionStatus, RequestStatus
from simulator import (
    AdapterOutcome,
    AuthoritativeOutcome,
    ContinuityAdapter,
    CoreContinuityAuthority,
    DiscreteEventSimulator,
    EventKind,
    PlacementDecision,
    PolicyID,
    authoritative_outcome,
    build_baseline_policies,
    decide_placement,
)

from .attempt_fencing import (
    _attempt_admission_observation,
    _classify_stale_authority_acceptance,
    _placement_to_dict,
    _scaffold_core,
)
from .correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    CorrectnessSummary,
    ExplicitNonSuccess,
    MetricOpportunityScope,
    RecoveryAction,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)


S1_SEQUENCE_SCHEMA = "cadi.s1-attempt-fencing-sequence-corpus.v1"
S1_SEQUENCE_COHORT_ID = "C4.2b:S1:E0"


class SequenceActionKind(str, Enum):
    REQUEST = "REQUEST"
    ATTEMPT_START = "ATTEMPT_START"
    TIMEOUT = "TIMEOUT"
    RETRY_START = "RETRY_START"
    COMPLETE = "COMPLETE"
    LATE_COMPLETE = "LATE_COMPLETE"
    OBSERVE = "OBSERVE"
    OBSERVE_DUPLICATE = "OBSERVE_DUPLICATE"


@dataclass(frozen=True, slots=True)
class SequenceAction:
    kind: SequenceActionKind
    at: float
    event_id: str
    payload: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SequenceActionKind):
            raise TypeError("kind must be SequenceActionKind")
        if not isinstance(self.at, (int, float)) or isinstance(self.at, bool):
            raise TypeError("at must be numeric")
        numeric = float(self.at)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError("at must be finite and non-negative")
        object.__setattr__(self, "at", numeric)
        if not isinstance(self.event_id, str) or not self.event_id:
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.payload, tuple):
            raise TypeError("payload must be a tuple of string pairs")
        keys: list[str] = []
        normalized: list[tuple[str, str]] = []
        for item in self.payload:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("payload entries must be (name, value) tuples")
            key, value = item
            if not isinstance(key, str) or not key:
                raise ValueError("payload keys must be non-empty strings")
            if not isinstance(value, str) or not value:
                raise ValueError("payload values must be non-empty strings")
            keys.append(key)
            normalized.append((key, value))
        if len(keys) != len(set(keys)):
            raise ValueError("payload keys must be unique")
        object.__setattr__(self, "payload", tuple(sorted(normalized)))

    @property
    def payload_dict(self) -> dict[str, str]:
        return dict(self.payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "at": self.at,
            "event_id": self.event_id,
            "payload": self.payload_dict,
        }


def _action(
    kind: SequenceActionKind,
    at: float,
    event_id: str,
    **payload: str,
) -> SequenceAction:
    return SequenceAction(
        kind=kind,
        at=at,
        event_id=event_id,
        payload=tuple(payload.items()),
    )


@dataclass(frozen=True, slots=True)
class AttemptFencingSequenceManifest:
    case_id: str
    seed: int
    pressure_labels: tuple[str, ...]
    actions: tuple[SequenceAction, ...]
    stale_presentation_event_ids: tuple[str, ...]
    expected_committed_attempt_id: str
    schema: str = S1_SEQUENCE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != S1_SEQUENCE_SCHEMA:
            raise ValueError(f"schema must be {S1_SEQUENCE_SCHEMA!r}")
        if not isinstance(self.case_id, str) or not self.case_id:
            raise ValueError("case_id must be a non-empty string")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if not isinstance(self.pressure_labels, tuple) or not self.pressure_labels:
            raise ValueError("pressure_labels must be a non-empty tuple")
        if not all(isinstance(item, str) and item for item in self.pressure_labels):
            raise ValueError("pressure_labels must contain non-empty strings")
        if len(self.pressure_labels) != len(set(self.pressure_labels)):
            raise ValueError("pressure_labels must be unique")
        if not isinstance(self.actions, tuple) or not self.actions:
            raise ValueError("actions must be a non-empty tuple")
        if not all(isinstance(item, SequenceAction) for item in self.actions):
            raise TypeError("actions must contain SequenceAction values")
        event_ids = tuple(item.event_id for item in self.actions)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("action event IDs must be unique within a case")
        if tuple(item.at for item in self.actions) != tuple(
            sorted(item.at for item in self.actions)
        ):
            raise ValueError("actions must be ordered by non-decreasing delivery time")
        if not isinstance(self.expected_committed_attempt_id, str) or not self.expected_committed_attempt_id:
            raise ValueError("expected_committed_attempt_id must be a non-empty string")
        if not isinstance(self.stale_presentation_event_ids, tuple) or not self.stale_presentation_event_ids:
            raise ValueError("every corpus case must include at least one stale authority presentation")
        if len(self.stale_presentation_event_ids) != len(set(self.stale_presentation_event_ids)):
            raise ValueError("stale_presentation_event_ids must be unique")

        action_by_id = {item.event_id: item for item in self.actions}
        for event_id in self.stale_presentation_event_ids:
            action = action_by_id.get(event_id)
            if action is None:
                raise ValueError("stale presentation event ID must exist in actions")
            if action.kind not in {
                SequenceActionKind.OBSERVE,
                SequenceActionKind.OBSERVE_DUPLICATE,
            }:
                raise ValueError("stale presentation event IDs must reference observation actions")
            attempt_id = action.payload_dict.get("attempt_id")
            if attempt_id == self.expected_committed_attempt_id:
                raise ValueError("stale presentation cannot target the expected committed Attempt")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "case_id": self.case_id,
            "seed": self.seed,
            "pressure_labels": list(self.pressure_labels),
            "actions": [item.to_dict() for item in self.actions],
            "stale_presentation_event_ids": list(self.stale_presentation_event_ids),
            "expected_committed_attempt_id": self.expected_committed_attempt_id,
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AttemptFencingSequenceTrial:
    policy_id: PolicyID
    manifest: AttemptFencingSequenceManifest
    evaluation: CorrectnessEvaluationRecord
    authoritative_outcome: AuthoritativeOutcome
    stale_admission_decisions: tuple[PlacementDecision, ...]
    finalization_applied_count: int


@dataclass(frozen=True, slots=True)
class AttemptFencingSequenceEvaluation:
    manifests: tuple[AttemptFencingSequenceManifest, ...]
    trials: tuple[AttemptFencingSequenceTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        if not self.manifests:
            raise ValueError("manifests must not be empty")
        expected = tuple(
            (manifest.case_id, policy_id)
            for manifest in self.manifests
            for policy_id in PolicyID
        )
        actual = tuple(
            (trial.manifest.case_id, trial.policy_id) for trial in self.trials
        )
        if actual != expected:
            raise ValueError("sequence trials must use canonical case then B0-B4 ordering")


@dataclass(frozen=True, slots=True)
class _ReplayResult:
    core: Any
    adapter: ContinuityAdapter
    stale_preconditions: tuple[dict[str, Any], ...]
    stale_presentations: tuple[dict[str, Any], ...]
    retry_checks: tuple[dict[str, Any], ...]
    late_completion_checks: tuple[dict[str, Any], ...]
    stale_admission_decisions: tuple[PlacementDecision, ...]


def _obs_payload(
    attempt_id: str,
    evidence_id: str,
    output_id: str,
) -> dict[str, str]:
    return {
        "request_id": "r",
        "attempt_id": attempt_id,
        "evidence_id": evidence_id,
        "output_id": output_id,
    }


def _two_attempt_case(
    case_id: str,
    pressure_labels: tuple[str, ...],
    middle_actions: Sequence[SequenceAction],
    *,
    stale_at: float,
    fresh_at: float,
    stale_duplicate_at: float | None = None,
    fresh_before_stale: bool = False,
) -> AttemptFencingSequenceManifest:
    prefix = f"c4.2b:{case_id}"
    stale_id = f"{prefix}:observe:a1:stale"
    fresh_id = f"{prefix}:observe:a2:fresh"
    stale = _action(
        SequenceActionKind.OBSERVE,
        stale_at,
        stale_id,
        **_obs_payload("a1", f"{prefix}:e:a1", f"{prefix}:o:a1"),
    )
    fresh = _action(
        SequenceActionKind.OBSERVE,
        fresh_at,
        fresh_id,
        **_obs_payload("a2", f"{prefix}:e:a2", f"{prefix}:o:a2"),
    )
    tail: list[SequenceAction] = [fresh, stale] if fresh_before_stale else [stale, fresh]
    stale_ids = [stale_id]
    if stale_duplicate_at is not None:
        duplicate_id = f"{prefix}:observe:a1:stale:duplicate"
        tail.append(
            _action(
                SequenceActionKind.OBSERVE_DUPLICATE,
                stale_duplicate_at,
                duplicate_id,
                **_obs_payload("a1", f"{prefix}:e:a1", f"{prefix}:o:a1"),
            )
        )
        stale_ids.append(duplicate_id)
    tail.sort(key=lambda item: item.at)

    actions = [
        _action(SequenceActionKind.REQUEST, 0.0, f"{prefix}:request", request_id="r"),
        _action(
            SequenceActionKind.ATTEMPT_START,
            1.0,
            f"{prefix}:start:a1",
            request_id="r",
            attempt_id="a1",
        ),
        *middle_actions,
        *tail,
    ]
    actions.sort(key=lambda item: item.at)
    return AttemptFencingSequenceManifest(
        case_id=case_id,
        seed=0,
        pressure_labels=pressure_labels,
        actions=tuple(actions),
        stale_presentation_event_ids=tuple(stale_ids),
        expected_committed_attempt_id="a2",
    )


def _build_default_manifests() -> tuple[AttemptFencingSequenceManifest, ...]:
    manifests: list[AttemptFencingSequenceManifest] = []

    def timeout(prefix: str, at: float, suffix: str = "timeout") -> SequenceAction:
        return _action(
            SequenceActionKind.TIMEOUT,
            at,
            f"c4.2b:{prefix}:{suffix}",
            request_id="r",
            timed_out_attempt_id="a1",
            retry_attempt_id="a2",
        )

    def complete(prefix: str, attempt_id: str, at: float, *, late: bool = False, suffix: str = "complete") -> SequenceAction:
        return _action(
            SequenceActionKind.LATE_COMPLETE if late else SequenceActionKind.COMPLETE,
            at,
            f"c4.2b:{prefix}:{suffix}:{attempt_id}",
            attempt_id=attempt_id,
        )

    manifests.append(
        _two_attempt_case(
            "A-timeout-before-a1-success",
            ("A.timeout-timing", "timeout-before-a1-success"),
            (
                timeout("A-timeout-before-a1-success", 2.0),
                complete("A-timeout-before-a1-success", "a1", 3.0, late=True),
                complete("A-timeout-before-a1-success", "a2", 4.0),
            ),
            stale_at=5.0,
            fresh_at=6.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "A-timeout-after-a1-success",
            ("A.timeout-timing", "timeout-after-a1-success-before-observation"),
            (
                complete("A-timeout-after-a1-success", "a1", 2.0),
                timeout("A-timeout-after-a1-success", 3.0),
                complete("A-timeout-after-a1-success", "a2", 4.0),
            ),
            stale_at=5.0,
            fresh_at=6.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "A-simultaneous-complete-then-timeout",
            ("A.timeout-timing", "simultaneous-complete-before-timeout"),
            (
                complete("A-simultaneous-complete-then-timeout", "a1", 2.0),
                timeout("A-simultaneous-complete-then-timeout", 2.0),
                complete("A-simultaneous-complete-then-timeout", "a2", 3.0),
            ),
            stale_at=4.0,
            fresh_at=5.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "A-simultaneous-timeout-then-complete",
            ("A.timeout-timing", "simultaneous-timeout-before-complete"),
            (
                timeout("A-simultaneous-timeout-then-complete", 2.0),
                complete("A-simultaneous-timeout-then-complete", "a1", 2.0),
                complete("A-simultaneous-timeout-then-complete", "a2", 3.0),
            ),
            stale_at=4.0,
            fresh_at=5.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "B-duplicate-timeout",
            ("B.retry-duplication-reorder", "duplicate-timeout"),
            (
                timeout("B-duplicate-timeout", 2.0, "timeout:1"),
                timeout("B-duplicate-timeout", 2.0, "timeout:2"),
                complete("B-duplicate-timeout", "a1", 3.0, late=True),
                complete("B-duplicate-timeout", "a2", 4.0),
            ),
            stale_at=5.0,
            fresh_at=6.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "B-duplicate-retry-start",
            ("B.retry-duplication-reorder", "duplicate-retry-start"),
            (
                timeout("B-duplicate-retry-start", 2.0),
                _action(
                    SequenceActionKind.RETRY_START,
                    2.1,
                    "c4.2b:B-duplicate-retry-start:retry-duplicate",
                    request_id="r",
                    superseded_attempt_id="a1",
                    retry_attempt_id="a2",
                ),
                complete("B-duplicate-retry-start", "a1", 3.0, late=True),
                complete("B-duplicate-retry-start", "a2", 4.0),
            ),
            stale_at=5.0,
            fresh_at=6.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "B-stale-retry-after-commit",
            ("B.retry-duplication-reorder", "stale-retry-after-commit"),
            (
                timeout("B-stale-retry-after-commit", 2.0),
                complete("B-stale-retry-after-commit", "a1", 3.0, late=True),
                complete("B-stale-retry-after-commit", "a2", 4.0),
                _action(
                    SequenceActionKind.RETRY_START,
                    6.0,
                    "c4.2b:B-stale-retry-after-commit:retry-stale",
                    request_id="r",
                    superseded_attempt_id="a1",
                    retry_attempt_id="a2",
                ),
            ),
            stale_at=7.0,
            fresh_at=5.0,
            fresh_before_stale=True,
        )
    )
    manifests.append(
        _two_attempt_case(
            "B-delayed-retry-start",
            ("B.retry-duplication-reorder", "delayed-retry-start"),
            (
                complete("B-delayed-retry-start", "a1", 2.0),
                _action(
                    SequenceActionKind.RETRY_START,
                    3.0,
                    "c4.2b:B-delayed-retry-start:retry",
                    request_id="r",
                    superseded_attempt_id="a1",
                    retry_attempt_id="a2",
                ),
                complete("B-delayed-retry-start", "a2", 4.0),
            ),
            stale_at=5.0,
            fresh_at=6.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "C-late-before-a2-complete",
            ("C.late-completion", "late-a1-before-a2-complete"),
            (
                timeout("C-late-before-a2-complete", 2.0),
                complete("C-late-before-a2-complete", "a1", 2.5, late=True),
                complete("C-late-before-a2-complete", "a2", 4.0),
            ),
            stale_at=5.0,
            fresh_at=6.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "C-late-after-a2-complete",
            ("C.late-completion", "late-a1-after-a2-complete-before-observation"),
            (
                timeout("C-late-after-a2-complete", 2.0),
                complete("C-late-after-a2-complete", "a2", 3.0),
                complete("C-late-after-a2-complete", "a1", 4.0, late=True),
            ),
            stale_at=5.0,
            fresh_at=6.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "C-late-after-a2-commit",
            ("C.late-completion", "late-a1-after-a2-commit"),
            (
                timeout("C-late-after-a2-commit", 2.0),
                complete("C-late-after-a2-commit", "a2", 3.0),
                complete("C-late-after-a2-commit", "a1", 5.0, late=True),
            ),
            stale_at=6.0,
            fresh_at=4.0,
            fresh_before_stale=True,
        )
    )
    manifests.append(
        _two_attempt_case(
            "C-duplicate-late-success",
            ("C.late-completion", "duplicate-late-physical-success"),
            (
                timeout("C-duplicate-late-success", 2.0),
                complete("C-duplicate-late-success", "a1", 3.0, late=True, suffix="late:1"),
                complete("C-duplicate-late-success", "a1", 3.1, late=True, suffix="late:2"),
                complete("C-duplicate-late-success", "a2", 4.0),
            ),
            stale_at=5.0,
            fresh_at=6.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "D-stale-immediate-after-supersession",
            ("D.stale-presentation", "stale-immediately-after-supersession"),
            (
                complete("D-stale-immediate-after-supersession", "a1", 1.5),
                timeout("D-stale-immediate-after-supersession", 2.0),
                complete("D-stale-immediate-after-supersession", "a2", 3.0),
            ),
            stale_at=2.1,
            fresh_at=4.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "D-duplicate-stale-presentation",
            ("D.stale-presentation", "duplicate-stale-authority-presentation"),
            (
                timeout("D-duplicate-stale-presentation", 2.0),
                complete("D-duplicate-stale-presentation", "a1", 3.0, late=True),
                complete("D-duplicate-stale-presentation", "a2", 4.0),
            ),
            stale_at=5.0,
            stale_duplicate_at=5.1,
            fresh_at=6.0,
        )
    )
    manifests.append(
        _two_attempt_case(
            "D-fresh-before-stale",
            ("D.stale-presentation", "reordered-fresh-before-stale"),
            (
                timeout("D-fresh-before-stale", 2.0),
                complete("D-fresh-before-stale", "a1", 3.0, late=True),
                complete("D-fresh-before-stale", "a2", 4.0),
            ),
            stale_at=6.0,
            fresh_at=5.0,
            fresh_before_stale=True,
        )
    )

    def three_generation_case(
        case_id: str,
        *,
        commit_before_stale: bool,
        duplicate_stale: bool,
    ) -> AttemptFencingSequenceManifest:
        prefix = f"c4.2b:{case_id}"
        actions: list[SequenceAction] = [
            _action(SequenceActionKind.REQUEST, 0.0, f"{prefix}:request", request_id="r"),
            _action(
                SequenceActionKind.ATTEMPT_START,
                1.0,
                f"{prefix}:start:a1",
                request_id="r",
                attempt_id="a1",
            ),
            _action(SequenceActionKind.COMPLETE, 1.5, f"{prefix}:complete:a1", attempt_id="a1"),
            _action(
                SequenceActionKind.TIMEOUT,
                2.0,
                f"{prefix}:timeout:a1:a2",
                request_id="r",
                timed_out_attempt_id="a1",
                retry_attempt_id="a2",
            ),
            _action(SequenceActionKind.COMPLETE, 3.0, f"{prefix}:complete:a2", attempt_id="a2"),
            _action(
                SequenceActionKind.TIMEOUT,
                4.0,
                f"{prefix}:timeout:a2:a3",
                request_id="r",
                timed_out_attempt_id="a2",
                retry_attempt_id="a3",
            ),
            _action(SequenceActionKind.COMPLETE, 5.0, f"{prefix}:complete:a3", attempt_id="a3"),
        ]
        fresh = _action(
            SequenceActionKind.OBSERVE,
            6.0 if commit_before_stale else 9.0,
            f"{prefix}:observe:a3:fresh",
            **_obs_payload("a3", f"{prefix}:e:a3", f"{prefix}:o:a3"),
        )
        stale_actions: list[SequenceAction] = [
            _action(
                SequenceActionKind.OBSERVE,
                7.0 if commit_before_stale else 6.0,
                f"{prefix}:observe:a1:stale",
                **_obs_payload("a1", f"{prefix}:e:a1", f"{prefix}:o:a1"),
            ),
            _action(
                SequenceActionKind.OBSERVE,
                8.0 if commit_before_stale else 7.0,
                f"{prefix}:observe:a2:stale",
                **_obs_payload("a2", f"{prefix}:e:a2", f"{prefix}:o:a2"),
            ),
        ]
        stale_ids = [item.event_id for item in stale_actions]
        if duplicate_stale:
            stale_actions.extend(
                [
                    _action(
                        SequenceActionKind.OBSERVE_DUPLICATE,
                        7.1,
                        f"{prefix}:observe:a1:stale:duplicate",
                        **_obs_payload("a1", f"{prefix}:e:a1", f"{prefix}:o:a1"),
                    ),
                    _action(
                        SequenceActionKind.OBSERVE_DUPLICATE,
                        8.1,
                        f"{prefix}:observe:a2:stale:duplicate",
                        **_obs_payload("a2", f"{prefix}:e:a2", f"{prefix}:o:a2"),
                    ),
                ]
            )
            stale_ids.extend(item.event_id for item in stale_actions[-2:])
        actions.extend([fresh, *stale_actions])
        actions.sort(key=lambda item: item.at)
        return AttemptFencingSequenceManifest(
            case_id=case_id,
            seed=0,
            pressure_labels=(
                "E.repeated-supersession",
                "three-generations",
                "stale-after-commit" if commit_before_stale else "stale-before-commit",
            ),
            actions=tuple(actions),
            stale_presentation_event_ids=tuple(stale_ids),
            expected_committed_attempt_id="a3",
        )

    manifests.append(
        three_generation_case(
            "E-three-generations-stale-before-commit",
            commit_before_stale=False,
            duplicate_stale=False,
        )
    )
    manifests.append(
        three_generation_case(
            "E-three-generations-stale-after-commit-duplicates",
            commit_before_stale=True,
            duplicate_stale=True,
        )
    )

    return tuple(manifests)


S1_SEQUENCE_MANIFESTS = _build_default_manifests()


def _schedule_action(adapter: ContinuityAdapter, action: SequenceAction) -> None:
    data = action.payload_dict
    if action.kind is SequenceActionKind.REQUEST:
        adapter.schedule_request(
            data["request_id"],
            "c",
            at=action.at,
            event_id=action.event_id,
        )
    elif action.kind is SequenceActionKind.ATTEMPT_START:
        adapter.schedule_attempt_start(
            data["request_id"],
            data["attempt_id"],
            at=action.at,
            event_id=action.event_id,
        )
    elif action.kind is SequenceActionKind.TIMEOUT:
        adapter.schedule_timeout(
            data["request_id"],
            data["timed_out_attempt_id"],
            data["retry_attempt_id"],
            at=action.at,
            event_id=action.event_id,
        )
    elif action.kind is SequenceActionKind.RETRY_START:
        adapter.schedule_retry_start(
            data["request_id"],
            data["superseded_attempt_id"],
            data["retry_attempt_id"],
            at=action.at,
            event_id=action.event_id,
        )
    elif action.kind in {SequenceActionKind.COMPLETE, SequenceActionKind.LATE_COMPLETE}:
        adapter.schedule_attempt_completion(
            data["attempt_id"],
            at=action.at,
            late=action.kind is SequenceActionKind.LATE_COMPLETE,
            event_id=action.event_id,
        )
    elif action.kind in {SequenceActionKind.OBSERVE, SequenceActionKind.OBSERVE_DUPLICATE}:
        adapter.schedule_observation(
            data["request_id"],
            data["attempt_id"],
            data["evidence_id"],
            data["output_id"],
            at=action.at,
            observed_at=action.at,
            duplicated=action.kind is SequenceActionKind.OBSERVE_DUPLICATE,
            event_id=action.event_id,
        )
    else:
        raise AssertionError(f"unhandled sequence action kind: {action.kind!r}")


def _replay_manifest(
    policy_id: PolicyID,
    manifest: AttemptFencingSequenceManifest,
) -> _ReplayResult:
    sim = DiscreteEventSimulator(seed=manifest.seed)
    core = _scaffold_core()
    stale_ids = frozenset(manifest.stale_presentation_event_ids)
    action_by_id = {item.event_id: item for item in manifest.actions}
    stale_preconditions: dict[str, dict[str, Any]] = {}
    stale_presentations: list[dict[str, Any]] = []
    retry_checks: list[dict[str, Any]] = []
    late_completion_checks: list[dict[str, Any]] = []
    stale_admission_decisions: list[PlacementDecision] = []

    def on_stale_precondition(_sim: DiscreteEventSimulator, event: Any) -> None:
        if event.event_id not in stale_ids:
            return
        action = action_by_id[event.event_id]
        data = action.payload_dict
        attempt = core.attempts[data["attempt_id"]]
        request = core.requests[data["request_id"]]
        if attempt.authority_status is not AttemptAuthority.SUPERSEDED:
            raise AssertionError(
                "manifest-labeled SAAR presentation must be SUPERSEDED before finalization"
            )
        if attempt.execution_status is not ExecutionStatus.SUCCEEDED:
            raise AssertionError(
                "manifest-labeled SAAR presentation requires delivered SUCCEEDED execution"
            )
        stale_preconditions[event.event_id] = {
            "event_id": event.event_id,
            "time": sim.now,
            "request_id": data["request_id"],
            "attempt_id": data["attempt_id"],
            "attempt_authority_before": attempt.authority_status.name,
            "attempt_execution_before": attempt.execution_status.name,
            "request_current_attempt_id_before": request.current_attempt_id,
            "request_committed_attempt_id_before": request.committed_attempt_id,
        }

    sim.register_handler(EventKind.OBSERVATION_CREATED, on_stale_precondition)
    sim.register_handler(EventKind.OBSERVATION_DUPLICATED, on_stale_precondition)

    adapter = ContinuityAdapter(sim, core)
    policy = build_baseline_policies(CoreContinuityAuthority(core))[policy_id]

    def on_retry(_sim: DiscreteEventSimulator, event: Any) -> None:
        data = dict(event.payload)
        retry_id = data.get("retry_attempt_id")
        superseded_id = data.get("superseded_attempt_id")
        request_id = data.get("request_id")
        retry = core.attempts.get(retry_id) if isinstance(retry_id, str) else None
        superseded = core.attempts.get(superseded_id) if isinstance(superseded_id, str) else None
        request = core.requests.get(request_id) if isinstance(request_id, str) else None
        retry_checks.append(
            {
                "event_id": event.event_id,
                "time": sim.now,
                "request_id": request_id,
                "superseded_attempt_id": superseded_id,
                "retry_attempt_id": retry_id,
                "request_status": None if request is None else request.status.name,
                "request_current_attempt_id": None if request is None else request.current_attempt_id,
                "superseded_authority": None if superseded is None else superseded.authority_status.name,
                "superseded_execution": None if superseded is None else superseded.execution_status.name,
                "retry_authority": None if retry is None else retry.authority_status.name,
                "retry_execution": None if retry is None else retry.execution_status.name,
            }
        )

    def on_late_result(_sim: DiscreteEventSimulator, event: Any) -> None:
        data = dict(event.payload)
        attempt_id = data.get("attempt_id")
        attempt = core.attempts.get(attempt_id) if isinstance(attempt_id, str) else None
        if attempt is None:
            return
        request = core.requests[attempt.request_id]
        stale = attempt.authority_status is AttemptAuthority.SUPERSEDED
        late_completion_checks.append(
            {
                "event_id": event.event_id,
                "time": sim.now,
                "attempt_id": attempt.id,
                "attempt_authority": attempt.authority_status.name,
                "attempt_execution": attempt.execution_status.name,
                "request_current_attempt_id": request.current_attempt_id,
                "request_committed_attempt_id": request.committed_attempt_id,
                "stale_at_delivery": stale,
            }
        )
        if stale:
            stale_admission_decisions.append(
                decide_placement(
                    policy,
                    _attempt_admission_observation(
                        request_id=attempt.request_id,
                        attempt_id=attempt.id,
                        attempt_authority=attempt.authority_status.name,
                    ),
                )
            )

    def on_stale_presentation(_sim: DiscreteEventSimulator, event: Any) -> None:
        if event.event_id not in stale_ids:
            return
        precondition = stale_preconditions.get(event.event_id)
        if precondition is None:
            raise AssertionError("stale precondition missing before finalization")
        action = action_by_id[event.event_id]
        data = action.payload_dict
        attempt = core.attempts[data["attempt_id"]]
        request = core.requests[data["request_id"]]
        accepted = _classify_stale_authority_acceptance(
            attempt_id=attempt.id,
            attempt_authority_before=precondition["attempt_authority_before"],
            attempt_execution_before=precondition["attempt_execution_before"],
            committed_attempt_id_after=request.committed_attempt_id,
            attempt_authority_after=attempt.authority_status.name,
        )
        stale_presentations.append(
            {
                "event_id": event.event_id,
                "time": sim.now,
                "request_id": request.id,
                "attempt_id": attempt.id,
                "attempt_authority_after": attempt.authority_status.name,
                "attempt_execution_after": attempt.execution_status.name,
                "request_current_attempt_id_after": request.current_attempt_id,
                "request_committed_attempt_id_after": request.committed_attempt_id,
                "accepted_authoritatively": accepted,
            }
        )

    sim.register_handler(EventKind.RETRY_STARTED, on_retry)
    sim.register_handler(EventKind.LATE_RESULT, on_late_result)
    sim.register_handler(EventKind.OBSERVATION_CREATED, on_stale_presentation)
    sim.register_handler(EventKind.OBSERVATION_DUPLICATED, on_stale_presentation)

    for action in manifest.actions:
        _schedule_action(adapter, action)
    sim.run()

    if tuple(stale_preconditions) != manifest.stale_presentation_event_ids:
        raise AssertionError(
            "every exogenous stale presentation must capture one pre-finalization stale snapshot"
        )
    observed_ids = tuple(item["event_id"] for item in stale_presentations)
    if observed_ids != manifest.stale_presentation_event_ids:
        raise AssertionError(
            "runtime stale presentations must exactly match the exogenous manifest order"
        )

    return _ReplayResult(
        core=core,
        adapter=adapter,
        stale_preconditions=tuple(
            stale_preconditions[event_id]
            for event_id in manifest.stale_presentation_event_ids
        ),
        stale_presentations=tuple(stale_presentations),
        retry_checks=tuple(retry_checks),
        late_completion_checks=tuple(late_completion_checks),
        stale_admission_decisions=tuple(stale_admission_decisions),
    )


def run_s1_sequence_trial(
    policy_id: PolicyID,
    manifest: AttemptFencingSequenceManifest,
) -> AttemptFencingSequenceTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    if not isinstance(manifest, AttemptFencingSequenceManifest):
        raise TypeError("manifest must be AttemptFencingSequenceManifest")

    replay = _replay_manifest(policy_id, manifest)
    core = replay.core
    adapter = replay.adapter
    request = core.requests["r"]
    outcome = authoritative_outcome(core, "r")

    finalization_records = tuple(
        record for record in adapter.records if record.operation == "finalize_request"
    )
    stale_finalization_ids = {
        record.event_id
        for record in finalization_records
        if record.event_id in set(manifest.stale_presentation_event_ids)
    }
    if stale_finalization_ids != set(manifest.stale_presentation_event_ids):
        raise AssertionError(
            "every SAAR opportunity must reach one terminal finalization attempt"
        )

    accepted_event_ids = {
        item["event_id"]
        for item in replay.stale_presentations
        if item["accepted_authoritatively"]
    }
    finalization_applied_count = sum(
        record.outcome is AdapterOutcome.APPLIED for record in finalization_records
    )

    reported_success = (
        request.status is RequestStatus.COMPLETED
        and outcome.authoritative_output_id is not None
        and outcome.committed_attempt_id is not None
    )
    semantic_correct = (
        reported_success
        and outcome.committed_attempt_id == manifest.expected_committed_attempt_id
    )

    opportunities: list[CorrectnessMetric] = []
    opportunity_event_ids: list[str] = []
    opportunity_scopes: list[MetricOpportunityScope] = []
    violations: list[CorrectnessMetric] = []
    violation_event_ids: list[str] = []

    for event_id in manifest.stale_presentation_event_ids:
        opportunities.append(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
        opportunity_event_ids.append(event_id)
        opportunity_scopes.append(MetricOpportunityScope.EXOGENOUS_PAIRED)
        if event_id in accepted_event_ids:
            violations.append(CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE)
            violation_event_ids.append(event_id)

    if reported_success:
        completed_request_event_id = f"{manifest.case_id}:completed-request:r"
        opportunities.append(CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
        opportunity_event_ids.append(completed_request_event_id)
        opportunity_scopes.append(MetricOpportunityScope.POLICY_DERIVED)
        if finalization_applied_count > 1:
            violations.append(CorrectnessMetric.DUPLICATE_FINALIZATION_RATE)
            violation_event_ids.append(completed_request_event_id)

    ground_truth = {
        "corpus_schema": manifest.schema,
        "case_id": manifest.case_id,
        "seed": manifest.seed,
        "manifest_fingerprint": manifest.fingerprint,
        "pressure_labels": list(manifest.pressure_labels),
        "ordered_actions": [item.to_dict() for item in manifest.actions],
        "stale_presentation_event_ids": list(manifest.stale_presentation_event_ids),
        "expected_committed_attempt_id": manifest.expected_committed_attempt_id,
    }
    observed_evidence = {
        "stale_authority_preconditions": list(replay.stale_preconditions),
        "stale_authority_presentations": list(replay.stale_presentations),
        "retry_checks": list(replay.retry_checks),
        "late_completion_checks": list(replay.late_completion_checks),
        "authoritative_outcome": {
            "request_status": outcome.request_status,
            "current_attempt_id": outcome.current_attempt_id,
            "committed_attempt_id": outcome.committed_attempt_id,
            "authoritative_output_id": outcome.authoritative_output_id,
        },
        "semantic_action_records": [
            {
                "event_id": record.event_id,
                "event_kind": record.event_kind.value,
                "operation": record.operation,
                "outcome": record.outcome.value,
                "error_type": record.error_type,
            }
            for record in adapter.records
        ],
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "stale_attempt_admission_probe_is_gate_metric": False,
        "stale_attempt_admission_decisions": [
            _placement_to_dict(item) for item in replay.stale_admission_decisions
        ],
    }

    if reported_success:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=semantic_correct,
            recovery_actions=(RecoveryAction.RETRY,),
        )
    else:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.FAIL,
            recovery_actions=(RecoveryAction.RETRY,),
        )

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S1_SEQUENCE_COHORT_ID,
        trial_id=manifest.case_id,
        operation_id="r",
        policy_id=policy_id,
        scenario_id=manifest.case_id,
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth=ground_truth,
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=semantic_result,
        metric_opportunities=tuple(opportunities),
        metric_opportunity_event_ids=tuple(opportunity_event_ids),
        metric_opportunity_scopes=tuple(opportunity_scopes),
        metric_violations=tuple(violations),
        metric_violation_event_ids=tuple(violation_event_ids),
        fault_id=f"S1-sequence:{manifest.case_id}",
        fault_class=";".join(manifest.pressure_labels),
    )

    return AttemptFencingSequenceTrial(
        policy_id=policy_id,
        manifest=manifest,
        evaluation=evaluation,
        authoritative_outcome=outcome,
        stale_admission_decisions=replay.stale_admission_decisions,
        finalization_applied_count=finalization_applied_count,
    )


def run_s1_sequence_paired(
    manifests: Sequence[AttemptFencingSequenceManifest] = S1_SEQUENCE_MANIFESTS,
) -> AttemptFencingSequenceEvaluation:
    manifest_tuple = tuple(manifests)
    if not manifest_tuple:
        raise ValueError("manifests must not be empty")
    if len({item.case_id for item in manifest_tuple}) != len(manifest_tuple):
        raise ValueError("manifest case IDs must be unique")
    trials = tuple(
        run_s1_sequence_trial(policy_id, manifest)
        for manifest in manifest_tuple
        for policy_id in PolicyID
    )
    summary = summarize_correctness(tuple(item.evaluation for item in trials))
    return AttemptFencingSequenceEvaluation(
        manifests=manifest_tuple,
        trials=trials,
        summary=summary,
    )
