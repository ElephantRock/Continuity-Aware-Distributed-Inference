from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol

from continuity.core import ContinuityCore
from continuity.entities import AttemptAuthority, ExecutionContext, ReconcileOutcome, StateLifecycle

from .policies import (
    CacheAwarePolicy,
    InformationField,
    PlacementDecision,
    PlacementPolicy,
    PolicyID,
    PolicyObservation,
    PolicyView,
    RequestCentricPolicy,
    SessionAffinityPolicy,
    StateAwarePolicy,
    WorkerObservation,
    _available_workers,
    _require_policy_view,
    _state_locations_from_view,
    _worker_load_key,
    _workers_from_view,
    decide_placement,
)


class RetentionDisposition(str, Enum):
    PROTECT = "PROTECT"
    RETAIN = "RETAIN"
    BEST_EFFORT = "BEST_EFFORT"
    RELEASE = "RELEASE"


class MigrationDisposition(str, Enum):
    ALLOW_COMMIT = "ALLOW_COMMIT"
    WAIT = "WAIT"


@dataclass(frozen=True, slots=True)
class StateRetentionDecision:
    policy_id: PolicyID
    state_id: str
    lifecycle: str
    priority: int
    disposition: RetentionDisposition

    def __post_init__(self) -> None:
        if self.policy_id is not PolicyID.B4:
            raise ValueError("StateRetentionDecision is defined only for B4")
        _require_nonempty(self.state_id, "state_id")
        _require_nonempty(self.lifecycle, "lifecycle")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool) or self.priority < 0:
            raise ValueError("priority must be a non-negative integer")
        if not isinstance(self.disposition, RetentionDisposition):
            raise TypeError("disposition must be RetentionDisposition")


@dataclass(frozen=True, slots=True)
class MigrationDecision:
    policy_id: PolicyID
    binding_id: str | None
    binding_epoch: int | None
    disposition: MigrationDisposition
    reason: str

    def __post_init__(self) -> None:
        if self.policy_id is not PolicyID.B4:
            raise ValueError("MigrationDecision is defined only for B4")
        if self.binding_id is not None:
            _require_nonempty(self.binding_id, "binding_id")
        if self.binding_epoch is not None:
            if not isinstance(self.binding_epoch, int) or isinstance(self.binding_epoch, bool) or self.binding_epoch < 0:
                raise ValueError("binding_epoch must be a non-negative integer")
        if not isinstance(self.disposition, MigrationDisposition):
            raise TypeError("disposition must be MigrationDisposition")
        _require_nonempty(self.reason, "reason")


class ContinuitySemanticAuthority(Protocol):
    def attempt_current(self, request_id: str, attempt_id: str) -> bool:
        ...

    def state_compatible(
        self,
        state_id: str,
        *,
        program_id: str,
        session_id: str,
        continuation_id: str,
        request_id: str,
        attempt_id: str,
    ) -> bool:
        ...


class CoreContinuityAuthority:
    """Read-only B4 semantic delegate over the closed C1 ContinuityCore."""

    def __init__(self, core: ContinuityCore) -> None:
        if not isinstance(core, ContinuityCore):
            raise TypeError("core must be ContinuityCore")
        self._core = core

    def attempt_current(self, request_id: str, attempt_id: str) -> bool:
        _require_nonempty(request_id, "request_id")
        _require_nonempty(attempt_id, "attempt_id")
        request = self._core.requests.get(request_id)
        attempt = self._core.attempts.get(attempt_id)
        if request is None or attempt is None:
            return False
        return (
            attempt.request_id == request_id
            and request.current_attempt_id == attempt_id
            and attempt.authority_status is AttemptAuthority.CURRENT
        )

    def state_compatible(
        self,
        state_id: str,
        *,
        program_id: str,
        session_id: str,
        continuation_id: str,
        request_id: str,
        attempt_id: str,
    ) -> bool:
        for value, name in (
            (state_id, "state_id"),
            (program_id, "program_id"),
            (session_id, "session_id"),
            (continuation_id, "continuation_id"),
            (request_id, "request_id"),
            (attempt_id, "attempt_id"),
        ):
            _require_nonempty(value, name)
        return self._core.state_compatible(
            state_id,
            ExecutionContext(
                program_id=program_id,
                session_id=session_id,
                continuation_id=continuation_id,
                request_id=request_id,
                attempt_id=attempt_id,
            ),
        )


class ContinuityAwarePolicy:
    policy_id = PolicyID.B4

    def __init__(self, authority: ContinuitySemanticAuthority) -> None:
        if not callable(getattr(authority, "attempt_current", None)):
            raise TypeError("authority must expose attempt_current")
        if not callable(getattr(authority, "state_compatible", None)):
            raise TypeError("authority must expose state_compatible")
        self._authority = authority

    def decide(self, view: PolicyView) -> PlacementDecision:
        _require_policy_view(view, self.policy_id, "ContinuityAwarePolicy")

        request_id = _optional_string(
            view.value(InformationField.LOGICAL_REQUEST_ID), "logical_request_id"
        )
        attempt_id = _optional_string(view.value(InformationField.ATTEMPT_ID), "attempt_id")
        attempt_authority = _optional_string(
            view.value(InformationField.ATTEMPT_AUTHORITY), "attempt_authority"
        )
        if (
            request_id is None
            or attempt_id is None
            or attempt_authority != AttemptAuthority.CURRENT.name
            or not self._authority.attempt_current(request_id, attempt_id)
        ):
            return PlacementDecision(self.policy_id, None, (), "ATTEMPT_FENCED")

        available = _available_workers(_workers_from_view(view))
        if not available:
            return PlacementDecision(self.policy_id, None, (), "NO_AVAILABLE_WORKER")

        program_id = _optional_string(view.value(InformationField.PROGRAM_ID), "program_id")
        session_id = _optional_string(view.value(InformationField.SESSION_ID), "session_id")
        continuation_id = _optional_string(
            view.value(InformationField.CONTINUATION_ID), "continuation_id"
        )
        if None in {program_id, session_id, continuation_id}:
            return PlacementDecision(
                self.policy_id,
                None,
                (),
                "CONTINUITY_CONTEXT_INCOMPLETE",
            )

        exact_state_id = _optional_string(
            view.value(InformationField.EXACT_STATE_ID), "exact_state_id"
        )
        locations = _state_locations_from_view(view)
        if exact_state_id is None or not locations:
            return _load_fallback(
                self.policy_id,
                available,
                "CONTINUITY_RECOMPUTE_LOAD_FALLBACK",
            )

        reconciliation = _optional_string(
            view.value(InformationField.RECONCILIATION), "reconciliation"
        )
        if reconciliation != ReconcileOutcome.MATCHED.name:
            return _load_fallback(
                self.policy_id,
                available,
                "RECONCILIATION_NOT_MATCHED_RECOMPUTE",
            )

        compatible = self._authority.state_compatible(
            exact_state_id,
            program_id=program_id,
            session_id=session_id,
            continuation_id=continuation_id,
            request_id=request_id,
            attempt_id=attempt_id,
        )
        if not compatible:
            return _load_fallback(
                self.policy_id,
                available,
                "INCOMPATIBLE_STATE_RECOMPUTE",
            )

        local_ids = frozenset(locations)
        if not any(worker.worker_id in local_ids for worker in available):
            return _load_fallback(
                self.policy_id,
                available,
                "COMPATIBLE_STATE_REMOTE_RECOMPUTE",
            )

        ranked_workers = tuple(
            sorted(
                available,
                key=lambda worker: (
                    0 if worker.worker_id in local_ids else 1,
                    *_worker_load_key(worker),
                ),
            )
        )
        ranked = tuple(worker.worker_id for worker in ranked_workers)
        return PlacementDecision(
            self.policy_id,
            ranked[0],
            ranked,
            "COMPATIBLE_STATE_LOCALITY_THEN_LOAD",
        )

    def decide_retention(self, view: PolicyView) -> StateRetentionDecision:
        _require_policy_view(view, self.policy_id, "ContinuityAwarePolicy")
        state_id = _optional_string(view.value(InformationField.EXACT_STATE_ID), "exact_state_id")
        lifecycle = _optional_string(
            view.value(InformationField.STATE_LIFECYCLE), "state_lifecycle"
        )
        if state_id is None or lifecycle is None:
            raise ValueError("B4 retention requires exact_state_id and state_lifecycle")

        table = {
            StateLifecycle.ACTIVE.name: (3, RetentionDisposition.PROTECT),
            StateLifecycle.WAITING.name: (2, RetentionDisposition.RETAIN),
            StateLifecycle.SPECULATIVE.name: (1, RetentionDisposition.BEST_EFFORT),
            StateLifecycle.TERMINAL.name: (0, RetentionDisposition.RELEASE),
        }
        if lifecycle not in table:
            raise ValueError("state_lifecycle is outside the canonical B4 lifecycle classes")
        priority, disposition = table[lifecycle]
        return StateRetentionDecision(
            self.policy_id,
            state_id,
            lifecycle,
            priority,
            disposition,
        )

    def decide_migration(self, view: PolicyView) -> MigrationDecision:
        _require_policy_view(view, self.policy_id, "ContinuityAwarePolicy")
        binding_id = _optional_string(view.value(InformationField.BINDING_ID), "binding_id")
        binding_epoch = view.value(InformationField.BINDING_EPOCH)
        if binding_epoch is not None:
            if not isinstance(binding_epoch, int) or isinstance(binding_epoch, bool) or binding_epoch < 0:
                raise ValueError("binding_epoch must be a non-negative integer")
        reconciliation = _optional_string(
            view.value(InformationField.RECONCILIATION), "reconciliation"
        )

        if binding_id is None or binding_epoch is None:
            return MigrationDecision(
                self.policy_id,
                binding_id,
                binding_epoch,
                MigrationDisposition.WAIT,
                "MISSING_BINDING_CONTEXT",
            )
        if reconciliation != ReconcileOutcome.MATCHED.name:
            return MigrationDecision(
                self.policy_id,
                binding_id,
                binding_epoch,
                MigrationDisposition.WAIT,
                "RECONCILIATION_REQUIRED",
            )
        return MigrationDecision(
            self.policy_id,
            binding_id,
            binding_epoch,
            MigrationDisposition.ALLOW_COMMIT,
            "RECONCILED_BINDING_COMMIT_ELIGIBLE",
        )


def build_baseline_policies(
    authority: ContinuitySemanticAuthority,
) -> Mapping[PolicyID, PlacementPolicy]:
    return MappingProxyType(
        {
            PolicyID.B0: RequestCentricPolicy(),
            PolicyID.B1: CacheAwarePolicy(),
            PolicyID.B2: SessionAffinityPolicy(),
            PolicyID.B3: StateAwarePolicy(),
            PolicyID.B4: ContinuityAwarePolicy(authority),
        }
    )


def decide_paired_placements(
    policies: Mapping[PolicyID, PlacementPolicy],
    observation: PolicyObservation,
) -> tuple[PlacementDecision, ...]:
    if not isinstance(policies, Mapping):
        raise TypeError("policies must be a Mapping")
    if set(policies) != set(PolicyID):
        raise ValueError("paired placement requires exactly B0 through B4")
    for policy_id in PolicyID:
        mapped_policy_id = getattr(policies[policy_id], "policy_id", None)
        if mapped_policy_id is not policy_id:
            raise ValueError(
                f"paired placement entry {policy_id.value} must expose matching policy_id"
            )
    if not isinstance(observation, PolicyObservation):
        raise TypeError("observation must be PolicyObservation")
    return tuple(decide_placement(policies[policy_id], observation) for policy_id in PolicyID)


def _load_fallback(
    policy_id: PolicyID,
    available: tuple[WorkerObservation, ...],
    reason: str,
) -> PlacementDecision:
    ranked = tuple(worker.worker_id for worker in sorted(available, key=_worker_load_key))
    return PlacementDecision(policy_id, ranked[0], ranked, reason)


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string or None")
    return value


def _require_nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value
