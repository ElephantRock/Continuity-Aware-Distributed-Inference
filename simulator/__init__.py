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
]
