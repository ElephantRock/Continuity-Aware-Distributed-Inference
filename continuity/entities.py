from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import FrozenSet, Optional

class ProgramStatus(Enum):
    CREATED = auto(); RUNNING = auto(); COMPLETED = auto(); FAILED = auto(); CANCELLED = auto()

class ContinuationLifecycle(Enum):
    CREATED = auto(); ACTIVE = auto(); WAITING = auto(); SPECULATIVE = auto(); JOINING = auto(); TERMINAL = auto(); ABANDONED = auto()

class RequestStatus(Enum):
    CREATED = auto(); READY = auto(); RUNNING = auto(); COMPLETED = auto(); FAILED = auto(); CANCELLED = auto()

class ExecutionStatus(Enum):
    CREATED = auto(); DISPATCHED = auto(); RUNNING = auto(); SUCCEEDED = auto(); FAILED = auto(); CANCELLED = auto()

class AttemptAuthority(Enum):
    NONE = auto(); CURRENT = auto(); COMMITTED = auto(); SUPERSEDED = auto()

class PhaseType(Enum):
    PREFILL = auto(); DECODE = auto(); STATE_PULL = auto(); STATE_TRANSFER = auto(); OTHER = auto()

class PhaseStatus(Enum):
    CREATED = auto(); RUNNING = auto(); COMPLETED = auto(); FAILED = auto(); CANCELLED = auto()

class StateLifecycle(Enum):
    ACTIVE = auto(); WAITING = auto(); SPECULATIVE = auto(); TERMINAL = auto()

class StateValidity(Enum):
    VALID = auto(); INVALID = auto()

class ReplicaStatus(Enum):
    MATERIALIZING = auto(); VALID = auto(); STALE = auto(); TRANSFERRING = auto(); EVICTING = auto(); LOST = auto(); INVALID = auto()

class BindingStatus(Enum):
    PROPOSED = auto(); ACTIVE = auto(); MIGRATING = auto(); SUPERSEDED = auto(); RELEASED = auto(); INVALID = auto()

class EvidenceAuthority(IntEnum):
    ESTIMATED = 1
    DERIVED = 2
    EXACT_OBSERVATION = 3
    AUTHORITATIVE = 4

class EvidenceStatus(Enum):
    VALID = auto(); STALE = auto(); UNKNOWN = auto(); FAILED = auto(); AMBIGUOUS = auto()

class ReconcileOutcome(Enum):
    MATCHED = auto(); WAIT = auto(); RETRY = auto(); RECOMPUTE = auto(); MIGRATE = auto(); REJECT = auto(); REPAIR = auto(); FAIL = auto(); AMBIGUOUS = auto()

@dataclass(frozen=True)
class SemanticEvent:
    id: str
    kind: str
    subject_type: str
    subject_id: str
    payload: FrozenSet[tuple[str, str]] = frozenset()

@dataclass(frozen=True)
class Program:
    id: str
    status: ProgramStatus = ProgramStatus.CREATED

@dataclass(frozen=True)
class Session:
    id: str
    program_id: str

@dataclass(frozen=True)
class Continuation:
    id: str
    session_id: str
    parent_ids: FrozenSet[str] = frozenset()
    lifecycle: ContinuationLifecycle = ContinuationLifecycle.CREATED

@dataclass(frozen=True)
class LogicalRequest:
    id: str
    continuation_id: str
    status: RequestStatus = RequestStatus.CREATED
    current_attempt_id: Optional[str] = None
    committed_attempt_id: Optional[str] = None
    authoritative_output_id: Optional[str] = None

@dataclass(frozen=True)
class Attempt:
    id: str
    request_id: str
    generation: int
    execution_status: ExecutionStatus = ExecutionStatus.CREATED
    authority_status: AttemptAuthority = AttemptAuthority.NONE

@dataclass(frozen=True)
class Phase:
    id: str
    attempt_id: str
    ordinal: int
    phase_type: PhaseType
    status: PhaseStatus = PhaseStatus.CREATED

@dataclass(frozen=True)
class ExecutionContext:
    program_id: str
    session_id: str
    continuation_id: str
    request_id: str
    attempt_id: str
    phase_id: Optional[str] = None

@dataclass(frozen=True)
class ReusableState:
    id: str
    origin_type: str
    origin_id: str
    origin_continuation_id: str
    origin_request_id: Optional[str] = None
    producer_attempt_id: Optional[str] = None
    semantic_type: str = "PREFIX"
    representation: str = "OPAQUE"
    lifecycle: StateLifecycle = StateLifecycle.TERMINAL
    validity: StateValidity = StateValidity.VALID
    derived_from: FrozenSet[str] = frozenset()
    producer_phase_id: Optional[str] = None

@dataclass(frozen=True)
class StateReplica:
    id: str
    state_id: str
    location_id: str
    status: ReplicaStatus = ReplicaStatus.MATERIALIZING
    binding_id: Optional[str] = None
    binding_epoch: Optional[int] = None

@dataclass(frozen=True)
class Binding:
    id: str
    subject_id: str
    location_id: str
    base_epoch: int
    epoch: int
    status: BindingStatus = BindingStatus.PROPOSED

@dataclass(frozen=True)
class Evidence:
    id: str
    claim: str
    source: str
    authority: EvidenceAuthority
    status: EvidenceStatus
    observed_at: float
    scope: FrozenSet[tuple[str, str]] = frozenset()
    valid_until: Optional[float] = None
    confidence: Optional[float] = None
    derived_from: FrozenSet[str] = frozenset()
    derivation_rule: Optional[str] = None

@dataclass(frozen=True)
class Output:
    id: str
    attempt_id: str
    terminal: bool
    evidence_ids: FrozenSet[str] = frozenset()
