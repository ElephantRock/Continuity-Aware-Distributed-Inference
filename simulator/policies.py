from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .resources import ResourceModel, WorkerStatus


POLICY_CONTRACT_SCHEMA = "cadi.policy-information-contract.v1"


class PolicyID(str, Enum):
    B0 = "B0"
    B1 = "B1"
    B2 = "B2"
    B3 = "B3"
    B4 = "B4"


class InformationField(str, Enum):
    LOGICAL_REQUEST_ID = "logical_request_id"
    ATTEMPT_ID = "attempt_id"
    ATTEMPT_AUTHORITY = "attempt_authority"
    SESSION_ID = "session_id"
    SESSION_PREFERRED_LOCATION = "session_preferred_location"
    CONTINUATION_ID = "continuation_id"
    CONTINUATION_ANCESTRY = "continuation_ancestry"
    STATE_CANDIDATE_KEY = "state_candidate_key"
    EXACT_STATE_ID = "exact_state_id"
    STATE_LOCATION = "state_location"
    STATE_PROVENANCE = "state_provenance"
    PRODUCER_ATTEMPT = "producer_attempt"
    BINDING_ID = "binding_id"
    BINDING_EPOCH = "binding_epoch"
    EVIDENCE_AUTHORITY = "evidence_authority"
    EVIDENCE_STATUS = "evidence_status"
    EVIDENCE_FRESHNESS = "evidence_freshness"
    RESOURCE_LOAD = "resource_load"


@dataclass(frozen=True, slots=True)
class InformationContract:
    policy_id: PolicyID
    label: str
    fields: frozenset[InformationField]

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("label must be a non-empty string")
        if not isinstance(self.fields, frozenset) or not all(
            isinstance(field, InformationField) for field in self.fields
        ):
            raise TypeError("fields must be frozenset[InformationField]")
        if InformationField.LOGICAL_REQUEST_ID not in self.fields:
            raise ValueError("every baseline must receive LogicalRequest identity")

    def allows(self, field: InformationField) -> bool:
        if not isinstance(field, InformationField):
            raise TypeError("field must be InformationField")
        return field in self.fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": POLICY_CONTRACT_SCHEMA,
            "policy_id": self.policy_id.value,
            "label": self.label,
            "fields": sorted(field.value for field in self.fields),
        }


_B0_FIELDS = frozenset(
    {
        InformationField.LOGICAL_REQUEST_ID,
        InformationField.RESOURCE_LOAD,
    }
)
_B1_FIELDS = _B0_FIELDS | frozenset(
    {
        InformationField.STATE_CANDIDATE_KEY,
        InformationField.STATE_LOCATION,
    }
)
_B2_FIELDS = _B1_FIELDS | frozenset(
    {
        InformationField.SESSION_ID,
        InformationField.SESSION_PREFERRED_LOCATION,
    }
)
_B3_FIELDS = _B0_FIELDS | frozenset(
    {
        InformationField.STATE_CANDIDATE_KEY,
        InformationField.EXACT_STATE_ID,
        InformationField.STATE_LOCATION,
    }
)
_B4_FIELDS = frozenset(InformationField)


INFORMATION_CONTRACTS: Mapping[PolicyID, InformationContract] = MappingProxyType(
    {
        PolicyID.B0: InformationContract(PolicyID.B0, "request-centric", _B0_FIELDS),
        PolicyID.B1: InformationContract(PolicyID.B1, "cache-aware", _B1_FIELDS),
        PolicyID.B2: InformationContract(PolicyID.B2, "session-affinity", _B2_FIELDS),
        PolicyID.B3: InformationContract(PolicyID.B3, "state-aware", _B3_FIELDS),
        PolicyID.B4: InformationContract(PolicyID.B4, "continuity-aware", _B4_FIELDS),
    }
)


@dataclass(frozen=True, slots=True)
class WorkerObservation:
    worker_id: str
    available: bool
    capacity: int
    active_tasks: int
    queued_tasks: int

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str) or not self.worker_id:
            raise ValueError("worker_id must be a non-empty string")
        if not isinstance(self.available, bool):
            raise TypeError("available must be bool")
        if not isinstance(self.capacity, int) or isinstance(self.capacity, bool) or self.capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        for value, name in (
            (self.active_tasks, "active_tasks"),
            (self.queued_tasks, "queued_tasks"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.active_tasks > self.capacity:
            raise ValueError("active_tasks cannot exceed worker capacity")

    @property
    def normalized_load(self) -> float:
        return (self.active_tasks + self.queued_tasks) / self.capacity


@dataclass(frozen=True, slots=True)
class PolicyObservation:
    request_id: str
    workers: tuple[WorkerObservation, ...]
    attempt_id: str | None = None
    attempt_authority: str | None = None
    session_id: str | None = None
    session_preferred_location: str | None = None
    continuation_id: str | None = None
    continuation_ancestry: tuple[str, ...] = ()
    state_candidate_key: str | None = None
    exact_state_id: str | None = None
    state_locations: tuple[str, ...] = ()
    state_provenance: tuple[tuple[str, str], ...] = ()
    producer_attempt_id: str | None = None
    binding_id: str | None = None
    binding_epoch: int | None = None
    evidence_authority: str | None = None
    evidence_status: str | None = None
    evidence_freshness: float | None = None

    def __post_init__(self) -> None:
        _require_id(self.request_id, "request_id")
        if not isinstance(self.workers, tuple) or not all(
            isinstance(worker, WorkerObservation) for worker in self.workers
        ):
            raise TypeError("workers must be tuple[WorkerObservation, ...]")
        ids = [worker.worker_id for worker in self.workers]
        if len(ids) != len(set(ids)):
            raise ValueError("worker observations must have unique worker_id")
        if tuple(sorted(ids)) != tuple(ids):
            raise ValueError("worker observations must be sorted by worker_id")
        for value, name in (
            (self.attempt_id, "attempt_id"),
            (self.attempt_authority, "attempt_authority"),
            (self.session_id, "session_id"),
            (self.session_preferred_location, "session_preferred_location"),
            (self.continuation_id, "continuation_id"),
            (self.state_candidate_key, "state_candidate_key"),
            (self.exact_state_id, "exact_state_id"),
            (self.producer_attempt_id, "producer_attempt_id"),
            (self.binding_id, "binding_id"),
            (self.evidence_authority, "evidence_authority"),
            (self.evidence_status, "evidence_status"),
        ):
            if value is not None:
                _require_id(value, name)
        _require_canonical_id_set_tuple(self.continuation_ancestry, "continuation_ancestry")
        _require_canonical_id_set_tuple(self.state_locations, "state_locations")
        _require_canonical_pairs(self.state_provenance, "state_provenance")
        if self.binding_epoch is not None:
            if not isinstance(self.binding_epoch, int) or isinstance(self.binding_epoch, bool) or self.binding_epoch < 0:
                raise ValueError("binding_epoch must be a non-negative integer")
        if self.evidence_freshness is not None:
            if not isinstance(self.evidence_freshness, (int, float)) or isinstance(self.evidence_freshness, bool):
                raise TypeError("evidence_freshness must be numeric")
            if not math.isfinite(float(self.evidence_freshness)) or self.evidence_freshness < 0:
                raise ValueError("evidence_freshness must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class PolicyView:
    policy_id: PolicyID
    values: tuple[tuple[InformationField, Any], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if not isinstance(self.values, tuple):
            raise TypeError("values must be a tuple")
        fields = [field for field, _ in self.values]
        if not all(isinstance(field, InformationField) for field in fields):
            raise TypeError("PolicyView keys must be InformationField")
        if len(fields) != len(set(fields)):
            raise ValueError("PolicyView fields must be unique")
        if tuple(sorted(fields, key=lambda field: field.value)) != tuple(fields):
            raise ValueError("PolicyView fields must be canonically ordered")
        contract = INFORMATION_CONTRACTS[self.policy_id]
        if set(fields) != set(contract.fields):
            raise ValueError("PolicyView fields must exactly match the policy information contract")

    @property
    def available_fields(self) -> frozenset[InformationField]:
        return frozenset(field for field, _ in self.values)

    def value(self, field: InformationField) -> Any:
        if not isinstance(field, InformationField):
            raise TypeError("field must be InformationField")
        if field not in self.available_fields:
            raise PermissionError(
                f"{self.policy_id.value} information contract does not allow {field.value}"
            )
        for candidate, value in self.values:
            if candidate is field:
                return value
        raise AssertionError("contract field missing from PolicyView")


@dataclass(frozen=True, slots=True)
class PlacementDecision:
    policy_id: PolicyID
    worker_id: str | None
    ranked_worker_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.worker_id is not None:
            _require_id(self.worker_id, "worker_id")
        _require_id_tuple(self.ranked_worker_ids, "ranked_worker_ids")
        _require_id(self.reason, "reason")
        if self.worker_id is None and self.ranked_worker_ids:
            raise ValueError("no-placement decision must not rank workers")
        if self.worker_id is not None:
            if not self.ranked_worker_ids or self.ranked_worker_ids[0] != self.worker_id:
                raise ValueError("selected worker must be first in ranked_worker_ids")


class PlacementPolicy(Protocol):
    policy_id: PolicyID

    def decide(self, view: PolicyView) -> PlacementDecision:
        ...


class RequestCentricPolicy:
    policy_id = PolicyID.B0

    def decide(self, view: PolicyView) -> PlacementDecision:
        _require_policy_view(view, self.policy_id, "RequestCentricPolicy")
        workers = _workers_from_view(view)
        available = _available_workers(workers)
        if not available:
            return PlacementDecision(self.policy_id, None, (), "NO_AVAILABLE_WORKER")
        ranked = _rank_worker_ids_by_load(available)
        return PlacementDecision(self.policy_id, ranked[0], ranked, "LEAST_NORMALIZED_LOAD")


class CacheAwarePolicy:
    policy_id = PolicyID.B1

    def decide(self, view: PolicyView) -> PlacementDecision:
        _require_policy_view(view, self.policy_id, "CacheAwarePolicy")
        workers = _workers_from_view(view)
        available = _available_workers(workers)
        if not available:
            return PlacementDecision(self.policy_id, None, (), "NO_AVAILABLE_WORKER")

        candidate_key = view.value(InformationField.STATE_CANDIDATE_KEY)
        locations = view.value(InformationField.STATE_LOCATION)
        if candidate_key is not None and not isinstance(candidate_key, str):
            raise TypeError("state_candidate_key observation must be string or None")
        if not isinstance(locations, tuple) or not all(
            isinstance(location, str) and location for location in locations
        ):
            raise TypeError("state_location observation must be tuple[str, ...]")

        local_ids = frozenset(locations) if candidate_key is not None else frozenset()
        has_available_locality = any(worker.worker_id in local_ids for worker in available)
        if not has_available_locality:
            ranked = _rank_worker_ids_by_load(available)
            return PlacementDecision(
                self.policy_id,
                ranked[0],
                ranked,
                "CACHE_AWARE_LOAD_FALLBACK",
            )

        ranked_workers = sorted(
            available,
            key=lambda worker: (
                0 if worker.worker_id in local_ids else 1,
                *_worker_load_key(worker),
            ),
        )
        ranked = tuple(worker.worker_id for worker in ranked_workers)
        return PlacementDecision(
            self.policy_id,
            ranked[0],
            ranked,
            "CACHE_LOCALITY_THEN_LOAD",
        )


def observe_resources(resources: ResourceModel) -> tuple[WorkerObservation, ...]:
    if not isinstance(resources, ResourceModel):
        raise TypeError("resources must be ResourceModel")
    observations = []
    for worker_id in sorted(resources.workers):
        worker = resources.workers[worker_id]
        observations.append(
            WorkerObservation(
                worker_id=worker_id,
                available=worker.status is WorkerStatus.UP,
                capacity=worker.capacity,
                active_tasks=len(resources.worker_active[worker_id]),
                queued_tasks=len(resources.worker_queues[worker_id]),
            )
        )
    return tuple(observations)


def project_observation(observation: PolicyObservation, policy_id: PolicyID) -> PolicyView:
    if not isinstance(observation, PolicyObservation):
        raise TypeError("observation must be PolicyObservation")
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    source = {
        InformationField.LOGICAL_REQUEST_ID: observation.request_id,
        InformationField.ATTEMPT_ID: observation.attempt_id,
        InformationField.ATTEMPT_AUTHORITY: observation.attempt_authority,
        InformationField.SESSION_ID: observation.session_id,
        InformationField.SESSION_PREFERRED_LOCATION: observation.session_preferred_location,
        InformationField.CONTINUATION_ID: observation.continuation_id,
        InformationField.CONTINUATION_ANCESTRY: observation.continuation_ancestry,
        InformationField.STATE_CANDIDATE_KEY: observation.state_candidate_key,
        InformationField.EXACT_STATE_ID: observation.exact_state_id,
        InformationField.STATE_LOCATION: observation.state_locations,
        InformationField.STATE_PROVENANCE: observation.state_provenance,
        InformationField.PRODUCER_ATTEMPT: observation.producer_attempt_id,
        InformationField.BINDING_ID: observation.binding_id,
        InformationField.BINDING_EPOCH: observation.binding_epoch,
        InformationField.EVIDENCE_AUTHORITY: observation.evidence_authority,
        InformationField.EVIDENCE_STATUS: observation.evidence_status,
        InformationField.EVIDENCE_FRESHNESS: observation.evidence_freshness,
        InformationField.RESOURCE_LOAD: observation.workers,
    }
    contract = INFORMATION_CONTRACTS[policy_id]
    values = tuple(
        (field, source[field])
        for field in sorted(contract.fields, key=lambda item: item.value)
    )
    return PolicyView(policy_id, values)


def decide_placement(policy: PlacementPolicy, observation: PolicyObservation) -> PlacementDecision:
    policy_id = getattr(policy, "policy_id", None)
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy must expose a PolicyID policy_id")
    view = project_observation(observation, policy_id)
    decision = policy.decide(view)
    if not isinstance(decision, PlacementDecision):
        raise TypeError("policy.decide must return PlacementDecision")
    if decision.policy_id is not policy_id:
        raise ValueError("policy decision identifies a different policy")
    return decision


def _require_policy_view(view: PolicyView, policy_id: PolicyID, policy_name: str) -> None:
    if not isinstance(view, PolicyView):
        raise TypeError("view must be PolicyView")
    if view.policy_id is not policy_id:
        raise ValueError(f"PolicyView does not match {policy_name}")


def _workers_from_view(view: PolicyView) -> tuple[WorkerObservation, ...]:
    workers = view.value(InformationField.RESOURCE_LOAD)
    if not isinstance(workers, tuple) or not all(
        isinstance(worker, WorkerObservation) for worker in workers
    ):
        raise TypeError("resource_load observation must be tuple[WorkerObservation, ...]")
    return workers


def _available_workers(workers: tuple[WorkerObservation, ...]) -> tuple[WorkerObservation, ...]:
    return tuple(worker for worker in workers if worker.available)


def _worker_load_key(worker: WorkerObservation) -> tuple[float, int, int, str]:
    return (
        worker.normalized_load,
        worker.queued_tasks,
        worker.active_tasks,
        worker.worker_id,
    )


def _rank_worker_ids_by_load(workers: tuple[WorkerObservation, ...]) -> tuple[str, ...]:
    return tuple(worker.worker_id for worker in sorted(workers, key=_worker_load_key))


def _require_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_id_tuple(values: tuple[str, ...], name: str) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be tuple[str, ...]")
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError(f"{name} must contain non-empty strings")


def _require_canonical_id_set_tuple(values: tuple[str, ...], name: str) -> None:
    _require_id_tuple(values, name)
    if len(values) != len(set(values)) or values != tuple(sorted(values)):
        raise ValueError(f"{name} must be unique and sorted")


def _require_canonical_pairs(values: tuple[tuple[str, str], ...], name: str) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be tuple[tuple[str, str], ...]")
    normalized = []
    keys = []
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(f"{name} entries must be 2-tuples")
        key, value = item
        _require_id(key, f"{name} key")
        _require_id(value, f"{name} value")
        normalized.append(item)
        keys.append(key)
    if len(keys) != len(set(keys)) or tuple(normalized) != tuple(sorted(normalized)):
        raise ValueError(f"{name} must have unique keys and canonical ordering")
