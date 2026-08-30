from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Optional

from continuity.entities import AttemptAuthority, ExecutionStatus
from continuity.invariants import InvariantOracle

from .faults import FaultClass, FaultInjector, FaultRecord, _PRESSURE, _SAFE
from .resources import ReplicaRuntimeStatus, ResourceModel, WorkerStatus
from .semantic_adapter import (
    AdapterOutcome,
    AuthoritativeOutcome,
    ContinuityAdapter,
    SemanticActionRecord,
    authoritative_outcome,
)
from .events import freeze_payload


class FaultOutcomeClass(str, Enum):
    PENDING = "PENDING"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"
    DELIVERY_SUPPRESSED = "DELIVERY_SUPPRESSED"
    PHYSICAL_EFFECT = "PHYSICAL_EFFECT"
    SEMANTIC_APPLIED = "SEMANTIC_APPLIED"
    SEMANTIC_IDEMPOTENT = "SEMANTIC_IDEMPOTENT"
    SEMANTIC_IGNORED = "SEMANTIC_IGNORED"
    SEMANTIC_REJECTED = "SEMANTIC_REJECTED"


@dataclass(frozen=True, slots=True)
class FaultOutcomeRecord:
    fault_id: str
    fault_class: FaultClass
    observed_at: float
    outcome_class: FaultOutcomeClass
    related_event_ids: tuple[str, ...]
    semantic_actions: tuple[SemanticActionRecord, ...]
    invariant_violations: tuple[str, ...]
    semantic_error: Optional[str]
    physical_summary: tuple[tuple[str, str], ...]
    request_id: Optional[str] = None
    authoritative_outcome: Optional[AuthoritativeOutcome] = None
    policy: Optional[str] = None
    recovery_action: Optional[str] = None
    recovery_latency: Optional[float] = None


class CrossLayerFaultInjector(FaultInjector):
    """C2.4.2 convenience faults that delegate to existing C2.3/C2.2 surfaces.

    The class extends the C2.4.1 FaultID namespace but does not mutate C1 stores
    directly. Attempt-related disturbances are scheduled through ContinuityAdapter;
    physical eviction is delegated to ResourceModel.
    """

    def inject_attempt_timeout(
        self,
        adapter: ContinuityAdapter,
        request_id: str,
        timed_out_attempt_id: str,
        retry_attempt_id: str,
        *,
        at: float | None = None,
        fault_id: str | None = None,
    ) -> FaultRecord:
        self._require_adapter(adapter)
        request = adapter.core.requests.get(request_id)
        if request is None:
            raise ValueError("timeout fault references unknown request")
        if request.current_attempt_id != timed_out_attempt_id:
            raise ValueError("timeout fault must target the current Attempt at injection time")
        attempt = adapter.core.attempts.get(timed_out_attempt_id)
        if attempt is None or attempt.request_id != request_id:
            raise ValueError("timeout fault Attempt/Request identity mismatch")
        actual_fault_id = self._reserve_fault_id(fault_id)
        delivery_time = self.simulator.now if at is None else at
        event = adapter.schedule_timeout(
            request_id,
            timed_out_attempt_id,
            retry_attempt_id,
            at=delivery_time,
            event_id=f"fault:{actual_fault_id}:attempt-timeout:{timed_out_attempt_id}",
        )
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.ATTEMPT_TIMEOUT,
                target=timed_out_attempt_id,
                injection_time=self.simulator.now,
                duration=event.time - self.simulator.now,
                parameters=freeze_payload(
                    {
                        "request_id": request_id,
                        "retry_attempt_id": retry_attempt_id,
                        "event_id": event.event_id,
                    }
                ),
                ground_truth_effect=(
                    "ATTEMPT_TIMEOUT delivery scheduled; semantic supersession remains C1-authorized"
                ),
                expected_invariant_pressure=_PRESSURE[FaultClass.ATTEMPT_TIMEOUT],
                expected_safe_outcomes=_SAFE[FaultClass.ATTEMPT_TIMEOUT],
                produced_event_ids=(event.event_id,),
            )
        )

    def inject_late_attempt_result(
        self,
        adapter: ContinuityAdapter,
        attempt_id: str,
        *,
        at: float | None = None,
        fault_id: str | None = None,
    ) -> FaultRecord:
        self._require_adapter(adapter)
        attempt = adapter.core.attempts.get(attempt_id)
        if attempt is None:
            raise ValueError("late-result fault references unknown Attempt")
        if attempt.authority_status is not AttemptAuthority.SUPERSEDED:
            raise ValueError("late-result fault requires a SUPERSEDED Attempt at injection time")
        actual_fault_id = self._reserve_fault_id(fault_id)
        delivery_time = self.simulator.now if at is None else at
        event = adapter.schedule_attempt_completion(
            attempt_id,
            at=delivery_time,
            late=True,
            event_id=f"fault:{actual_fault_id}:late-result:{attempt_id}",
        )
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.LATE_ATTEMPT_RESULT,
                target=attempt_id,
                injection_time=self.simulator.now,
                duration=event.time - self.simulator.now,
                parameters=freeze_payload(
                    {
                        "request_id": attempt.request_id,
                        "event_id": event.event_id,
                    }
                ),
                ground_truth_effect=(
                    "late physical success delivery scheduled for an already SUPERSEDED Attempt"
                ),
                expected_invariant_pressure=_PRESSURE[FaultClass.LATE_ATTEMPT_RESULT],
                expected_safe_outcomes=_SAFE[FaultClass.LATE_ATTEMPT_RESULT],
                produced_event_ids=(event.event_id,),
            )
        )

    def inject_stale_attempt_observation(
        self,
        adapter: ContinuityAdapter,
        request_id: str,
        attempt_id: str,
        evidence_id: str,
        output_id: str,
        *,
        at: float | None = None,
        observed_at: float | None = None,
        fault_id: str | None = None,
    ) -> FaultRecord:
        self._require_adapter(adapter)
        attempt = adapter.core.attempts.get(attempt_id)
        if attempt is None or attempt.request_id != request_id:
            raise ValueError("stale observation Attempt/Request identity mismatch")
        if attempt.authority_status is not AttemptAuthority.SUPERSEDED:
            raise ValueError("stale observation requires a SUPERSEDED Attempt at injection time")
        if attempt.execution_status is not ExecutionStatus.SUCCEEDED:
            raise ValueError("stale terminal observation requires a SUCCEEDED Attempt")
        actual_fault_id = self._reserve_fault_id(fault_id)
        delivery_time = self.simulator.now if at is None else at
        observation_time = self.simulator.now if observed_at is None else observed_at
        event = adapter.schedule_observation(
            request_id,
            attempt_id,
            evidence_id,
            output_id,
            at=delivery_time,
            observed_at=observation_time,
            event_id=f"fault:{actual_fault_id}:stale-observation:{attempt_id}",
        )
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.STALE_ATTEMPT_OBSERVATION,
                target=attempt_id,
                injection_time=self.simulator.now,
                duration=event.time - self.simulator.now,
                parameters=freeze_payload(
                    {
                        "request_id": request_id,
                        "evidence_id": evidence_id,
                        "output_id": output_id,
                        "observed_at": observation_time,
                        "event_id": event.event_id,
                    }
                ),
                ground_truth_effect=(
                    "terminal observation delivered for a SUCCEEDED but SUPERSEDED Attempt"
                ),
                expected_invariant_pressure=_PRESSURE[FaultClass.STALE_ATTEMPT_OBSERVATION],
                expected_safe_outcomes=_SAFE[FaultClass.STALE_ATTEMPT_OBSERVATION],
                produced_event_ids=(event.event_id,),
            )
        )

    def evict_replica(
        self,
        replica_id: str,
        *,
        fault_id: str | None = None,
    ) -> FaultRecord:
        resources = self._require_resources()
        actual_fault_id = self._reserve_fault_id(fault_id)
        event = resources.evict_replica(replica_id)
        return self._append(
            FaultRecord(
                id=actual_fault_id,
                fault_class=FaultClass.REPLICA_EVICTION,
                target=replica_id,
                injection_time=self.simulator.now,
                duration=0.0,
                parameters=freeze_payload({"event_id": event.event_id}),
                ground_truth_effect="STATE_EVICTED event scheduled for the target physical replica",
                expected_invariant_pressure=_PRESSURE[FaultClass.REPLICA_EVICTION],
                expected_safe_outcomes=_SAFE[FaultClass.REPLICA_EVICTION],
                produced_event_ids=(event.event_id,),
            )
        )

    def _require_adapter(self, adapter: ContinuityAdapter) -> None:
        if not isinstance(adapter, ContinuityAdapter):
            raise TypeError("adapter must be ContinuityAdapter")
        if adapter.simulator is not self.simulator:
            raise ValueError("adapter must reference the same simulator")


class FaultOutcomeLinker:
    """Correlate injected C2 fault ground truth with observed physical/semantic outcomes."""

    def __init__(
        self,
        injector: FaultInjector,
        *,
        adapter: ContinuityAdapter | None = None,
        resources: ResourceModel | None = None,
    ) -> None:
        if not isinstance(injector, FaultInjector):
            raise TypeError("injector must be FaultInjector")
        if adapter is not None:
            if not isinstance(adapter, ContinuityAdapter):
                raise TypeError("adapter must be ContinuityAdapter or None")
            if adapter.simulator is not injector.simulator:
                raise ValueError("adapter must reference the injector simulator")
        effective_resources = resources if resources is not None else injector.resources
        if effective_resources is not None:
            if not isinstance(effective_resources, ResourceModel):
                raise TypeError("resources must be ResourceModel or None")
            if effective_resources.simulator is not injector.simulator:
                raise ValueError("resources must reference the injector simulator")
        self.injector = injector
        self.adapter = adapter
        self.resources = effective_resources

    def observe(
        self,
        fault_id: str,
        *,
        request_id: str | None = None,
        policy: str | None = None,
        recovery_action: str | None = None,
        recovery_latency: float | None = None,
    ) -> FaultOutcomeRecord:
        record = self._fault(fault_id)
        if recovery_latency is not None:
            if not isinstance(recovery_latency, (int, float)) or isinstance(recovery_latency, bool):
                raise TypeError("recovery_latency must be numeric or None")
            recovery_latency = float(recovery_latency)
            if not math.isfinite(recovery_latency) or recovery_latency < 0:
                raise ValueError("recovery_latency must be finite and non-negative")

        produced_ids = set(record.produced_event_ids)
        semantic_actions: tuple[SemanticActionRecord, ...] = ()
        if self.adapter is not None and produced_ids:
            semantic_actions = tuple(
                action for action in self.adapter.records if action.event_id in produced_ids
            )

        invariant_violations: tuple[str, ...] = ()
        if self.adapter is not None:
            try:
                InvariantOracle(self.adapter.core).assert_all()
            except Exception as exc:  # diagnostic capture; does not authorize recovery
                invariant_violations = (f"{type(exc).__name__}: {exc}",)

        resolved_request_id = request_id or self._request_id(record)
        authoritative: AuthoritativeOutcome | None = None
        if (
            not invariant_violations
            and self.adapter is not None
            and resolved_request_id is not None
            and resolved_request_id in self.adapter.core.requests
        ):
            authoritative = authoritative_outcome(self.adapter.core, resolved_request_id)

        physical_summary = self._physical_summary(record)
        outcome_class = self._classify(record, semantic_actions, invariant_violations)
        errors = tuple(
            f"{action.error_type}: {action.error_message}"
            for action in semantic_actions
            if action.error_type is not None
        )
        semantic_error = " | ".join(errors) if errors else None

        return FaultOutcomeRecord(
            fault_id=record.id,
            fault_class=record.fault_class,
            observed_at=self.injector.simulator.now,
            outcome_class=outcome_class,
            related_event_ids=record.produced_event_ids + record.cancelled_event_ids,
            semantic_actions=semantic_actions,
            invariant_violations=invariant_violations,
            semantic_error=semantic_error,
            physical_summary=physical_summary,
            request_id=resolved_request_id,
            authoritative_outcome=authoritative,
            policy=policy,
            recovery_action=recovery_action,
            recovery_latency=recovery_latency,
        )

    def _fault(self, fault_id: str) -> FaultRecord:
        matches = [record for record in self.injector.records if record.id == fault_id]
        if len(matches) != 1:
            raise KeyError(f"unknown fault_id: {fault_id}")
        return matches[0]

    def _classify(
        self,
        record: FaultRecord,
        semantic_actions: tuple[SemanticActionRecord, ...],
        invariant_violations: tuple[str, ...],
    ) -> FaultOutcomeClass:
        if invariant_violations:
            return FaultOutcomeClass.INVARIANT_VIOLATION
        pending_ids = {event.event_id for event in self.injector.simulator.pending_events}
        trace_ids = {event.event_id for event in self.injector.simulator.trace}
        if any(event_id in pending_ids for event_id in record.produced_event_ids):
            return FaultOutcomeClass.PENDING
        if record.fault_class is FaultClass.DELIVERY_DROP and not record.produced_event_ids:
            return FaultOutcomeClass.DELIVERY_SUPPRESSED
        if semantic_actions:
            outcomes = {action.outcome for action in semantic_actions}
            if AdapterOutcome.REJECTED in outcomes:
                return FaultOutcomeClass.SEMANTIC_REJECTED
            if AdapterOutcome.IGNORED in outcomes:
                return FaultOutcomeClass.SEMANTIC_IGNORED
            if AdapterOutcome.APPLIED in outcomes:
                return FaultOutcomeClass.SEMANTIC_APPLIED
            return FaultOutcomeClass.SEMANTIC_IDEMPOTENT
        if any(event_id in trace_ids for event_id in record.produced_event_ids):
            return FaultOutcomeClass.PHYSICAL_EFFECT
        if record.cancelled_event_ids:
            return FaultOutcomeClass.DELIVERY_SUPPRESSED
        return FaultOutcomeClass.PENDING

    def _request_id(self, record: FaultRecord) -> str | None:
        params = dict(record.parameters)
        request_id = params.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id
        if self.adapter is not None and record.target in self.adapter.core.attempts:
            return self.adapter.core.attempts[record.target].request_id
        return None

    def _physical_summary(self, record: FaultRecord) -> tuple[tuple[str, str], ...]:
        if self.resources is None:
            return ()
        if record.fault_class is FaultClass.WORKER_FAILURE and record.target in self.resources.workers:
            status = self.resources.workers[record.target].status
            return (("worker_status", status.value),)
        if record.fault_class in {
            FaultClass.REPLICA_LOSS,
            FaultClass.REPLICA_EVICTION,
        } and record.target in self.resources.replicas:
            status = self.resources.replicas[record.target].status
            return (("replica_status", status.value),)
        return ()