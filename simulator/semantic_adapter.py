from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from continuity.core import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionStatus,
    Output,
    RequestStatus,
)
from continuity.errors import ContinuityError, SemanticViolation
from continuity.invariants import InvariantOracle
from continuity.serialization import snapshot_fingerprint

from .engine import DiscreteEventSimulator
from .events import EventKind, SimEvent


class AdapterOutcome(str, Enum):
    APPLIED = "APPLIED"
    IDEMPOTENT = "IDEMPOTENT"
    REJECTED = "REJECTED"
    IGNORED = "IGNORED"


@dataclass(frozen=True, slots=True)
class SemanticActionRecord:
    sim_time: float
    event_id: str
    event_kind: EventKind
    operation: str
    outcome: AdapterOutcome
    result_id: Optional[str]
    error_type: Optional[str]
    error_message: Optional[str]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class AttemptProjection:
    id: str
    generation: int
    execution_status: str
    authority_status: str


@dataclass(frozen=True, slots=True)
class AuthoritativeOutcome:
    request_id: str
    request_status: str
    current_attempt_id: Optional[str]
    committed_attempt_id: Optional[str]
    authoritative_output_id: Optional[str]
    authoritative_evidence_ids: tuple[str, ...]
    attempts: tuple[AttemptProjection, ...]


class _AdapterInputError(ValueError):
    pass


class ContinuityAdapter:
    """Timed C2 delivery adapter around exactly one closed C1 ContinuityCore.

    The adapter may inspect C1 state but never mutates semantic stores directly.
    All semantic changes are performed through public ContinuityCore methods and
    validated by the independent C1 invariant oracle.
    """

    def __init__(self, simulator: DiscreteEventSimulator, core: ContinuityCore) -> None:
        if not isinstance(simulator, DiscreteEventSimulator):
            raise TypeError("simulator must be DiscreteEventSimulator")
        if not isinstance(core, ContinuityCore):
            raise TypeError("core must be ContinuityCore")
        self.simulator = simulator
        self.core = core
        self.records: list[SemanticActionRecord] = []
        self._scheduled_retry_attempts: set[str] = set()
        self._attempt_success_times: dict[str, float] = {}

        self._register(EventKind.REQUEST_CREATED, self._on_request_created)
        self._register(EventKind.ATTEMPT_STARTED, self._on_attempt_started)
        self._register(EventKind.ATTEMPT_TIMEOUT, self._on_attempt_timeout)
        self._register(EventKind.RETRY_STARTED, self._on_retry_started)
        self._register(EventKind.ATTEMPT_COMPLETED, self._on_attempt_completed)
        self._register(EventKind.LATE_RESULT, self._on_attempt_completed)
        self._register(EventKind.ATTEMPT_FAILED, self._on_attempt_failed)
        self._register(EventKind.OBSERVATION_CREATED, self._on_observation)
        self._register(EventKind.OBSERVATION_DUPLICATED, self._on_observation)

        InvariantOracle(self.core).assert_all()

    @property
    def semantic_fingerprint(self) -> str:
        return snapshot_fingerprint(self.core)

    def schedule_request(
        self,
        request_id: str,
        continuation_id: str,
        *,
        at: float,
        event_id: Optional[str] = None,
    ) -> SimEvent:
        self._require_id(request_id, "request_id")
        self._require_id(continuation_id, "continuation_id")
        return self.simulator.schedule(
            EventKind.REQUEST_CREATED,
            at=at,
            event_id=event_id or f"request:{request_id}",
            payload={"request_id": request_id, "continuation_id": continuation_id},
        )

    def schedule_attempt_start(
        self,
        request_id: str,
        attempt_id: str,
        *,
        at: float,
        event_id: Optional[str] = None,
    ) -> SimEvent:
        self._require_id(request_id, "request_id")
        self._require_id(attempt_id, "attempt_id")
        return self.simulator.schedule(
            EventKind.ATTEMPT_STARTED,
            at=at,
            event_id=event_id or f"attempt-start:{attempt_id}",
            payload={"request_id": request_id, "attempt_id": attempt_id},
        )

    def schedule_timeout(
        self,
        request_id: str,
        timed_out_attempt_id: str,
        retry_attempt_id: str,
        *,
        at: float,
        event_id: Optional[str] = None,
    ) -> SimEvent:
        for value, name in (
            (request_id, "request_id"),
            (timed_out_attempt_id, "timed_out_attempt_id"),
            (retry_attempt_id, "retry_attempt_id"),
        ):
            self._require_id(value, name)
        if retry_attempt_id == timed_out_attempt_id:
            raise ValueError("retry_attempt_id must differ from timed_out_attempt_id")
        return self.simulator.schedule(
            EventKind.ATTEMPT_TIMEOUT,
            at=at,
            event_id=event_id or f"attempt-timeout:{timed_out_attempt_id}:{retry_attempt_id}",
            payload={
                "request_id": request_id,
                "timed_out_attempt_id": timed_out_attempt_id,
                "retry_attempt_id": retry_attempt_id,
            },
        )

    def schedule_retry_start(
        self,
        request_id: str,
        superseded_attempt_id: str,
        retry_attempt_id: str,
        *,
        at: float,
        event_id: Optional[str] = None,
    ) -> SimEvent:
        for value, name in (
            (request_id, "request_id"),
            (superseded_attempt_id, "superseded_attempt_id"),
            (retry_attempt_id, "retry_attempt_id"),
        ):
            self._require_id(value, name)
        if retry_attempt_id == superseded_attempt_id:
            raise ValueError("retry_attempt_id must differ from superseded_attempt_id")
        return self.simulator.schedule(
            EventKind.RETRY_STARTED,
            at=at,
            event_id=event_id or f"retry-start:{retry_attempt_id}",
            payload={
                "request_id": request_id,
                "superseded_attempt_id": superseded_attempt_id,
                "retry_attempt_id": retry_attempt_id,
            },
        )

    def schedule_attempt_completion(
        self,
        attempt_id: str,
        *,
        at: float,
        late: bool = False,
        event_id: Optional[str] = None,
    ) -> SimEvent:
        self._require_id(attempt_id, "attempt_id")
        kind = EventKind.LATE_RESULT if late else EventKind.ATTEMPT_COMPLETED
        prefix = "late-result" if late else "attempt-complete"
        return self.simulator.schedule(
            kind,
            at=at,
            event_id=event_id or f"{prefix}:{attempt_id}",
            payload={"attempt_id": attempt_id},
        )

    def schedule_attempt_failure(
        self,
        attempt_id: str,
        *,
        at: float,
        event_id: Optional[str] = None,
    ) -> SimEvent:
        self._require_id(attempt_id, "attempt_id")
        return self.simulator.schedule(
            EventKind.ATTEMPT_FAILED,
            at=at,
            event_id=event_id or f"attempt-failed:{attempt_id}",
            payload={"attempt_id": attempt_id},
        )

    def schedule_observation(
        self,
        request_id: str,
        attempt_id: str,
        evidence_id: str,
        output_id: str,
        *,
        at: float,
        observed_at: Optional[float] = None,
        duplicated: bool = False,
        event_id: Optional[str] = None,
    ) -> SimEvent:
        for value, name in (
            (request_id, "request_id"),
            (attempt_id, "attempt_id"),
            (evidence_id, "evidence_id"),
            (output_id, "output_id"),
        ):
            self._require_id(value, name)
        delivery_time = self._finite_nonnegative(at, "at")
        observation_time = (
            delivery_time
            if observed_at is None
            else self._finite_nonnegative(observed_at, "observed_at")
        )
        if observation_time > delivery_time:
            raise ValueError("observed_at cannot be later than observation delivery")
        kind = EventKind.OBSERVATION_DUPLICATED if duplicated else EventKind.OBSERVATION_CREATED
        prefix = "observation-duplicate" if duplicated else "observation"
        return self.simulator.schedule(
            kind,
            at=delivery_time,
            event_id=event_id or f"{prefix}:{evidence_id}:{output_id}",
            payload={
                "request_id": request_id,
                "attempt_id": attempt_id,
                "evidence_id": evidence_id,
                "output_id": output_id,
                "observed_at": observation_time,
            },
        )

    def _register(self, kind: EventKind, handler: Callable[[SimEvent], None]) -> None:
        def deliver(_sim: DiscreteEventSimulator, event: SimEvent) -> None:
            try:
                handler(event)
            except _AdapterInputError as exc:
                self._note(event, "event_validation", AdapterOutcome.REJECTED, error=exc)

        self.simulator.register_handler(kind, deliver)

    def _on_request_created(self, event: SimEvent) -> None:
        payload = self._payload(event, "request_id", "continuation_id")
        self._apply(
            event,
            "create_request",
            lambda: self.core.create_request(payload["request_id"], payload["continuation_id"]),
        )

    def _on_attempt_started(self, event: SimEvent) -> None:
        payload = self._payload(event, "request_id", "attempt_id")
        attempt = self._apply(
            event,
            "start_attempt",
            lambda: self.core.start_attempt(payload["attempt_id"], payload["request_id"]),
        )
        if attempt is not None:
            self._apply(
                event,
                "set_attempt_execution:RUNNING",
                lambda: self.core.set_attempt_execution(payload["attempt_id"], ExecutionStatus.RUNNING),
            )

    def _on_attempt_timeout(self, event: SimEvent) -> None:
        payload = self._payload(
            event, "request_id", "timed_out_attempt_id", "retry_attempt_id"
        )
        request = self.core.requests.get(payload["request_id"])
        if request is None:
            self._note(
                event,
                "timeout_retry_eligibility",
                AdapterOutcome.REJECTED,
                error=_AdapterInputError("timeout references unknown request"),
            )
            return
        if request.status in {RequestStatus.COMPLETED, RequestStatus.FAILED, RequestStatus.CANCELLED}:
            self._note(event, "timeout_retry_eligibility", AdapterOutcome.IGNORED)
            return
        if request.current_attempt_id != payload["timed_out_attempt_id"]:
            self._note(event, "timeout_retry_eligibility", AdapterOutcome.IGNORED)
            return
        retry_attempt_id = payload["retry_attempt_id"]
        if retry_attempt_id in self._scheduled_retry_attempts:
            self._note(event, "timeout_retry_eligibility", AdapterOutcome.IGNORED)
            return
        self._scheduled_retry_attempts.add(retry_attempt_id)
        try:
            self.simulator.schedule(
                EventKind.RETRY_STARTED,
                delay=0,
                event_id=f"retry-start:{retry_attempt_id}:from:{event.event_id}",
                payload={
                    "request_id": payload["request_id"],
                    "superseded_attempt_id": payload["timed_out_attempt_id"],
                    "retry_attempt_id": retry_attempt_id,
                },
            )
        except Exception:
            self._scheduled_retry_attempts.remove(retry_attempt_id)
            raise
        self._note(event, "schedule_retry", AdapterOutcome.APPLIED)

    def _on_retry_started(self, event: SimEvent) -> None:
        payload = self._payload(
            event, "request_id", "superseded_attempt_id", "retry_attempt_id"
        )
        request = self.core.requests.get(payload["request_id"])
        if request is None:
            self._note(
                event,
                "retry_eligibility",
                AdapterOutcome.REJECTED,
                error=_AdapterInputError("retry references unknown request"),
            )
            return
        if request.status in {RequestStatus.COMPLETED, RequestStatus.FAILED, RequestStatus.CANCELLED}:
            self._note(event, "retry_eligibility", AdapterOutcome.IGNORED)
            return
        if request.current_attempt_id != payload["superseded_attempt_id"]:
            self._note(event, "retry_eligibility", AdapterOutcome.IGNORED)
            return
        attempt = self._apply(
            event,
            "start_attempt",
            lambda: self.core.start_attempt(payload["retry_attempt_id"], payload["request_id"]),
        )
        if attempt is not None:
            self._apply(
                event,
                "set_attempt_execution:RUNNING",
                lambda: self.core.set_attempt_execution(
                    payload["retry_attempt_id"], ExecutionStatus.RUNNING
                ),
            )

    def _on_attempt_completed(self, event: SimEvent) -> None:
        payload = self._payload(event, "attempt_id")
        attempt = self.core.attempts.get(payload["attempt_id"])
        already_succeeded = (
            attempt is not None and attempt.execution_status is ExecutionStatus.SUCCEEDED
        )
        result = self._apply(
            event,
            "complete_attempt:SUCCEEDED",
            lambda: self.core.complete_attempt(payload["attempt_id"], succeeded=True),
        )
        if result is not None and not already_succeeded:
            self._attempt_success_times.setdefault(payload["attempt_id"], self.simulator.now)

    def _on_attempt_failed(self, event: SimEvent) -> None:
        payload = self._payload(event, "attempt_id")
        self._apply(
            event,
            "complete_attempt:FAILED",
            lambda: self.core.complete_attempt(payload["attempt_id"], succeeded=False),
        )

    def _on_observation(self, event: SimEvent) -> None:
        payload = self._payload(
            event, "request_id", "attempt_id", "evidence_id", "output_id"
        )
        observed_at = self._event_nonnegative_float(event, "observed_at")
        if observed_at > self.simulator.now:
            self._note(
                event,
                "observation_timestamp",
                AdapterOutcome.REJECTED,
                error=_AdapterInputError("observation timestamp is later than delivery"),
            )
            return
        attempt = self.core.attempts.get(payload["attempt_id"])
        if attempt is None:
            self._note(
                event,
                "observe_terminal_attempt",
                AdapterOutcome.REJECTED,
                error=_AdapterInputError("observation references unknown Attempt"),
            )
            return
        if attempt.request_id != payload["request_id"]:
            self._note(
                event,
                "observe_terminal_attempt",
                AdapterOutcome.REJECTED,
                error=_AdapterInputError("observation Attempt/Request identity mismatch"),
            )
            return
        if attempt.execution_status is not ExecutionStatus.SUCCEEDED:
            self._note(
                event,
                "observe_terminal_attempt",
                AdapterOutcome.REJECTED,
                error=SemanticViolation("terminal success observation requires SUCCEEDED Attempt"),
            )
            return
        success_time = self._attempt_success_times.get(payload["attempt_id"])
        if success_time is not None and observed_at < success_time:
            self._note(
                event,
                "observation_timestamp",
                AdapterOutcome.REJECTED,
                error=_AdapterInputError(
                    "terminal observation cannot predate delivered Attempt success"
                ),
            )
            return

        try:
            self._ensure_terminal_evidence(
                event, payload["attempt_id"], payload["evidence_id"], observed_at
            )
            self._ensure_terminal_output(
                event,
                payload["attempt_id"],
                payload["evidence_id"],
                payload["output_id"],
            )
        except ContinuityError as exc:
            self._note(event, "observation_identity", AdapterOutcome.REJECTED, error=exc)
            return

        self._apply(
            event,
            "finalize_request",
            lambda: self.core.finalize_request(
                payload["request_id"], payload["output_id"], now=self.simulator.now
            ),
        )

    def _ensure_terminal_evidence(
        self, event: SimEvent, attempt_id: str, evidence_id: str, observed_at: float
    ) -> None:
        expected = Evidence(
            id=evidence_id,
            claim="terminal_attempt_success",
            source="c2.semantic_adapter",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=observed_at,
            scope=frozenset({("attempt", attempt_id)}),
        )
        existing = self.core.evidence.get(evidence_id)
        if existing is not None:
            if existing != expected:
                raise SemanticViolation("conflicting terminal Evidence identity in semantic adapter")
            self._note(event, "record_evidence", AdapterOutcome.IDEMPOTENT, result_id=evidence_id)
            return
        self._apply(event, "record_evidence", lambda: self.core.record_evidence(expected))

    def _ensure_terminal_output(
        self,
        event: SimEvent,
        attempt_id: str,
        evidence_id: str,
        output_id: str,
    ) -> None:
        expected = Output(
            id=output_id,
            attempt_id=attempt_id,
            terminal=True,
            evidence_ids=frozenset({evidence_id}),
        )
        existing = self.core.outputs.get(output_id)
        if existing is not None:
            if existing != expected:
                raise SemanticViolation("conflicting terminal Output identity in semantic adapter")
            self._note(event, "create_output", AdapterOutcome.IDEMPOTENT, result_id=output_id)
            return
        self._apply(
            event,
            "create_output",
            lambda: self.core.create_output(
                output_id, attempt_id, True, evidence_ids=[evidence_id]
            ),
        )

    def _apply(self, event: SimEvent, operation: str, call: Callable[[], object]) -> object | None:
        before = snapshot_fingerprint(self.core)
        try:
            result = call()
        except (ContinuityError, KeyError) as exc:
            InvariantOracle(self.core).assert_all()
            after = snapshot_fingerprint(self.core)
            if after != before:
                raise AssertionError(
                    f"rejected C1 operation {operation!r} mutated semantic state"
                ) from exc
            self._append_record(
                event,
                operation,
                AdapterOutcome.REJECTED,
                error=exc,
                fingerprint=after,
            )
            return None

        InvariantOracle(self.core).assert_all()
        after = snapshot_fingerprint(self.core)
        outcome = AdapterOutcome.IDEMPOTENT if after == before else AdapterOutcome.APPLIED
        result_id = getattr(result, "id", None)
        self._append_record(
            event,
            operation,
            outcome,
            result_id=result_id,
            fingerprint=after,
        )
        return result

    def _note(
        self,
        event: SimEvent,
        operation: str,
        outcome: AdapterOutcome,
        *,
        result_id: Optional[str] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        InvariantOracle(self.core).assert_all()
        self._append_record(
            event,
            operation,
            outcome,
            result_id=result_id,
            error=error,
            fingerprint=snapshot_fingerprint(self.core),
        )

    def _append_record(
        self,
        event: SimEvent,
        operation: str,
        outcome: AdapterOutcome,
        *,
        result_id: Optional[str] = None,
        error: Optional[BaseException] = None,
        fingerprint: str,
    ) -> None:
        self.records.append(
            SemanticActionRecord(
                sim_time=self.simulator.now,
                event_id=event.event_id,
                event_kind=event.kind,
                operation=operation,
                outcome=outcome,
                result_id=result_id,
                error_type=type(error).__name__ if error is not None else None,
                error_message=str(error) if error is not None else None,
                fingerprint=fingerprint,
            )
        )

    @staticmethod
    def _finite_nonnegative(value: float, name: str) -> float:
        import math
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{name} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"{name} must be finite and non-negative")
        return numeric

    def _event_nonnegative_float(self, event: SimEvent, name: str) -> float:
        payload = dict(event.payload)
        if name not in payload:
            raise _AdapterInputError(f"event payload missing field: {name}")
        try:
            return self._finite_nonnegative(payload[name], name)
        except (TypeError, ValueError) as exc:
            raise _AdapterInputError(str(exc)) from exc

    @staticmethod
    def _require_id(value: str, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")

    def _payload(self, event: SimEvent, *required: str) -> dict[str, str]:
        payload = dict(event.payload)
        missing = [name for name in required if name not in payload]
        if missing:
            raise _AdapterInputError(f"event payload missing fields: {missing}")
        for name in required:
            if not isinstance(payload[name], str) or not payload[name]:
                raise _AdapterInputError(f"event payload field {name!r} must be a non-empty string")
        return payload


def authoritative_outcome(core: ContinuityCore, request_id: str) -> AuthoritativeOutcome:
    InvariantOracle(core).assert_all()
    request = core.requests[request_id]
    attempts = tuple(
        AttemptProjection(
            id=attempt.id,
            generation=attempt.generation,
            execution_status=attempt.execution_status.name,
            authority_status=attempt.authority_status.name,
        )
        for attempt in sorted(
            (attempt for attempt in core.attempts.values() if attempt.request_id == request_id),
            key=lambda attempt: attempt.generation,
        )
    )
    evidence_ids: tuple[str, ...] = ()
    if request.authoritative_output_id is not None:
        output = core.outputs[request.authoritative_output_id]
        evidence_ids = tuple(sorted(output.evidence_ids))
    return AuthoritativeOutcome(
        request_id=request.id,
        request_status=request.status.name,
        current_attempt_id=request.current_attempt_id,
        committed_attempt_id=request.committed_attempt_id,
        authoritative_output_id=request.authoritative_output_id,
        authoritative_evidence_ids=evidence_ids,
        attempts=attempts,
    )


def assert_authoritative_equivalent(
    reference: ContinuityCore,
    candidate: ContinuityCore,
    request_id: str,
) -> AuthoritativeOutcome:
    expected = authoritative_outcome(reference, request_id)
    actual = authoritative_outcome(candidate, request_id)
    if actual != expected:
        raise AssertionError(
            f"authoritative outcome mismatch for {request_id}: expected {expected!r}, got {actual!r}"
        )
    return actual
