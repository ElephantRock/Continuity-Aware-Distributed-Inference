"""Deterministic discrete-event simulation substrate for CADI C2."""

from .engine import DiscreteEventSimulator
from .events import EventKind, SimEvent
from .resources import (
    NetworkLink,
    ReplicaRuntime,
    ReplicaRuntimeStatus,
    ResourceModel,
    ResourceTask,
    StateTransfer,
    TaskStatus,
    TransferStatus,
    Worker,
    WorkerStatus,
)
from .semantic_adapter import (
    AdapterOutcome,
    AttemptProjection,
    AuthoritativeOutcome,
    ContinuityAdapter,
    SemanticActionRecord,
    assert_authoritative_equivalent,
    authoritative_outcome,
)

__all__ = [
    "DiscreteEventSimulator",
    "EventKind",
    "SimEvent",
    "NetworkLink",
    "ReplicaRuntime",
    "ReplicaRuntimeStatus",
    "ResourceModel",
    "ResourceTask",
    "StateTransfer",
    "TaskStatus",
    "TransferStatus",
    "Worker",
    "WorkerStatus",
    "AdapterOutcome",
    "AttemptProjection",
    "AuthoritativeOutcome",
    "ContinuityAdapter",
    "SemanticActionRecord",
    "assert_authoritative_equivalent",
    "authoritative_outcome",
]
