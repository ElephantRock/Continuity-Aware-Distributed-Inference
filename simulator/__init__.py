"""Deterministic discrete-event simulation substrate for CADI C2."""

from .engine import DiscreteEventSimulator
from .events import EventKind, SimEvent
from .faults import FaultClass, FaultInjector, FaultRecord, ProbabilisticFaultDecision
from .fault_linkage import (
    CrossLayerFaultInjector,
    FaultOutcomeClass,
    FaultOutcomeLinker,
    FaultOutcomeRecord,
)
from .fault_campaign import (
    FAULT_CAMPAIGN_SCHEMA,
    POLICY_FAULT_BINDING_SCHEMA,
    FaultCampaignManifest,
    FaultReplayEntry,
    FaultReplayError,
    FaultScheduleReplayer,
    PolicyFaultBinding,
    assert_paired_policy_reuse,
)
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
    "FaultClass",
    "FaultInjector",
    "FaultRecord",
    "ProbabilisticFaultDecision",
    "CrossLayerFaultInjector",
    "FaultOutcomeClass",
    "FaultOutcomeLinker",
    "FaultOutcomeRecord",
    "FAULT_CAMPAIGN_SCHEMA",
    "POLICY_FAULT_BINDING_SCHEMA",
    "FaultCampaignManifest",
    "FaultReplayEntry",
    "FaultReplayError",
    "FaultScheduleReplayer",
    "PolicyFaultBinding",
    "assert_paired_policy_reuse",
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