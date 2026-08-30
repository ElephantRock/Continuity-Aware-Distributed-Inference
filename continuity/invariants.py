from __future__ import annotations

from .core import ContinuityCore
from .entities import (
    AttemptAuthority,
    BindingStatus,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionStatus,
    RequestStatus,
    StateValidity,
)


def _require(condition, message=None):
    """Non-strippable invariant check used by the independent oracle."""
    if not condition:
        raise AssertionError(message or "Continuity invariant violation")


class InvariantOracle:
    """Independent structural checks over normalized core state."""

    def __init__(self, core: ContinuityCore):
        self.c = core

    def assert_all(self) -> None:
        self._global_identity_uniqueness()
        self._parent_scopes()
        self._attempt_authority()
        self._attempt_generations()
        self._request_commit_consistency()
        self._continuation_dags()
        self._phase_ordering()
        self._state_provenance()
        self._replica_integrity()
        self._binding_authority()
        self._binding_generations()
        self._evidence_derivation()
        self._event_identity()
        self._output_integrity()
        self._state_validity()

    def _global_identity_uniqueness(self):
        collections = (
            self.c.programs,
            self.c.sessions,
            self.c.continuations,
            self.c.requests,
            self.c.attempts,
            self.c.phases,
            self.c.states,
            self.c.replicas,
            self.c.bindings,
            self.c.evidence,
            self.c.outputs,
            self.c.events,
        )
        seen = set()
        for collection in collections:
            for key, entity in collection.items():
                _require(entity.id == key)
                _require(key not in seen)
                seen.add(key)

    def _parent_scopes(self):
        for session in self.c.sessions.values():
            _require(session.program_id in self.c.programs)
        for continuation in self.c.continuations.values():
            _require(continuation.session_id in self.c.sessions)
            for parent_id in continuation.parent_ids:
                parent = self.c.continuations[parent_id]
                _require(parent.session_id == continuation.session_id)
        for request in self.c.requests.values():
            _require(request.continuation_id in self.c.continuations)
        for attempt in self.c.attempts.values():
            _require(attempt.request_id in self.c.requests)
        for phase in self.c.phases.values():
            _require(phase.attempt_id in self.c.attempts)

    def _attempt_authority(self):
        for rid, request in self.c.requests.items():
            current = [
                attempt for attempt in self.c.attempts.values()
                if attempt.request_id == rid and attempt.authority_status == AttemptAuthority.CURRENT
            ]
            committed = [
                attempt for attempt in self.c.attempts.values()
                if attempt.request_id == rid and attempt.authority_status == AttemptAuthority.COMMITTED
            ]
            _require(len(current) <= 1)
            _require(len(committed) <= 1)
            if request.current_attempt_id is None:
                _require(not current)
            else:
                _require(len(current) == 1 and current[0].id == request.current_attempt_id)
            if request.committed_attempt_id is None:
                _require(not committed)
            else:
                _require(len(committed) == 1 and committed[0].id == request.committed_attempt_id)
            for attempt in self.c.attempts.values():
                if attempt.request_id == rid and attempt.authority_status == AttemptAuthority.SUPERSEDED:
                    _require(attempt.id != request.current_attempt_id)
                    _require(attempt.id != request.committed_attempt_id)

    def _attempt_generations(self):
        for request_id in self.c.requests:
            attempts = [a for a in self.c.attempts.values() if a.request_id == request_id]
            generations = sorted(a.generation for a in attempts)
            _require(generations == list(range(1, len(attempts) + 1)))

    def _request_commit_consistency(self):
        for request in self.c.requests.values():
            if request.status == RequestStatus.COMPLETED:
                _require(request.current_attempt_id is None)
                _require(request.committed_attempt_id is not None)
                _require(request.authoritative_output_id is not None)
                attempt = self.c.attempts[request.committed_attempt_id]
                output = self.c.outputs[request.authoritative_output_id]
                _require(attempt.request_id == request.id)
                _require(attempt.authority_status == AttemptAuthority.COMMITTED)
                _require(attempt.execution_status == ExecutionStatus.SUCCEEDED)
                _require(output.attempt_id == request.committed_attempt_id)
                _require(output.terminal)
            elif request.status in {RequestStatus.FAILED, RequestStatus.CANCELLED}:
                _require(request.current_attempt_id is None)
                _require(request.committed_attempt_id is None)
                _require(request.authoritative_output_id is None)

    def _continuation_dags(self):
        for session_id in self.c.sessions:
            _require(not self.c._has_cycle(session_id))

    def _phase_ordering(self):
        for attempt_id in self.c.attempts:
            phases = sorted(
                (p for p in self.c.phases.values() if p.attempt_id == attempt_id),
                key=lambda p: p.ordinal,
            )
            _require([p.ordinal for p in phases] == list(range(1, len(phases) + 1)))
            _require(all(p.attempt_id == attempt_id for p in phases))

    def _state_provenance(self):
        _require(not self.c._state_cycle())
        for state in self.c.states.values():
            _require(state.origin_continuation_id in self.c.continuations)
            self._declared_state_origin(state)
            for dependency_id in state.derived_from:
                dependency = self.c.states[dependency_id]
                _require(
                    self.c.is_ancestor(
                        dependency.origin_continuation_id,
                        state.origin_continuation_id,
                    )
                )
                if (
                    state.producer_phase_id
                    and dependency.producer_phase_id
                    and dependency.producer_attempt_id == state.producer_attempt_id
                ):
                    producer_phase = self.c.phases[state.producer_phase_id]
                    dependency_phase = self.c.phases[dependency.producer_phase_id]
                    _require(dependency_phase.ordinal < producer_phase.ordinal)
            if state.producer_attempt_id:
                attempt = self.c.attempts[state.producer_attempt_id]
                if state.origin_request_id:
                    _require(attempt.request_id == state.origin_request_id)
            if state.producer_phase_id:
                phase = self.c.phases[state.producer_phase_id]
                _require(phase.attempt_id == state.producer_attempt_id)
                _require(phase.status.name == "COMPLETED")

    def _declared_state_origin(self, state):
        if state.origin_type == "continuation":
            continuation = self.c.continuations[state.origin_id]
            _require(state.origin_continuation_id == continuation.id)
            _require(state.origin_request_id is None)
            _require(state.producer_attempt_id is None)
            _require(state.producer_phase_id is None)
            return
        if state.origin_type == "request":
            request = self.c.requests[state.origin_id]
            _require(request.status == RequestStatus.COMPLETED)
            _require(request.committed_attempt_id is not None)
            _require(state.origin_request_id == request.id)
            _require(state.origin_continuation_id == request.continuation_id)
            _require(state.producer_attempt_id == request.committed_attempt_id)
            _require(state.producer_phase_id is None)
            return
        if state.origin_type == "attempt":
            attempt = self.c.attempts[state.origin_id]
            request = self.c.requests[attempt.request_id]
            _require(state.producer_attempt_id == attempt.id)
            _require(state.origin_request_id == request.id)
            _require(state.origin_continuation_id == request.continuation_id)
            _require(state.producer_phase_id is None)
            return
        if state.origin_type == "phase":
            phase = self.c.phases[state.origin_id]
            attempt = self.c.attempts[phase.attempt_id]
            request = self.c.requests[attempt.request_id]
            _require(state.producer_phase_id == phase.id)
            _require(state.producer_attempt_id == attempt.id)
            _require(state.origin_request_id == request.id)
            _require(state.origin_continuation_id == request.continuation_id)
            return
        raise AssertionError(f"unsupported State origin_type: {state.origin_type}")

    def _replica_integrity(self):
        for replica in self.c.replicas.values():
            _require(replica.state_id in self.c.states)
            if replica.binding_id is not None:
                binding = self.c.bindings[replica.binding_id]
                _require(replica.binding_epoch == binding.epoch)

    def _binding_authority(self):
        for subject, binding_id in self.c.current_binding_by_subject.items():
            binding = self.c.bindings[binding_id]
            _require(binding.subject_id == subject)
            _require(binding.status == BindingStatus.ACTIVE)
            _require(self.c.current_epoch_by_subject[subject] == binding.epoch)
        for binding in self.c.bindings.values():
            if binding.status == BindingStatus.ACTIVE:
                _require(self.c.current_binding_by_subject.get(binding.subject_id) == binding.id)

    def _binding_generations(self):
        by_subject = {}
        for binding in self.c.bindings.values():
            by_subject.setdefault(binding.subject_id, []).append(binding)
            _require(binding.epoch > binding.base_epoch)
        for subject, bindings in by_subject.items():
            epochs = [binding.epoch for binding in bindings]
            _require(len(epochs) == len(set(epochs)))
            _require(self.c.last_allocated_epoch_by_subject[subject] == max(epochs))
            current_epoch = self.c.current_epoch_by_subject.get(subject)
            if current_epoch is not None:
                _require(current_epoch in epochs)

    def _evidence_derivation(self):
        for evidence in self.c.evidence.values():
            has_derivation = bool(evidence.derived_from or evidence.derivation_rule)
            if evidence.authority == EvidenceAuthority.DERIVED:
                _require(evidence.derived_from)
                _require(evidence.derivation_rule)
            else:
                _require(not has_derivation)
            if evidence.derived_from:
                supports = [self.c.evidence[eid] for eid in evidence.derived_from]
                union_scope = set().union(*(set(s.scope) for s in supports)) if supports else set()
                _require(set(evidence.scope).issubset(union_scope))
                if evidence.status == EvidenceStatus.VALID:
                    _require(all(s.status == EvidenceStatus.VALID for s in supports))

    def _event_identity(self):
        _require(len(self.c.event_order) == len(set(self.c.event_order)))
        _require(set(self.c.event_order) == set(self.c.events))
        for event_id in self.c.event_order:
            _require(self.c.events[event_id].id == event_id)

    def _output_integrity(self):
        for output in self.c.outputs.values():
            _require(output.attempt_id in self.c.attempts)
            for evidence_id in output.evidence_ids:
                _require(evidence_id in self.c.evidence)

    def _state_validity(self):
        for state in self.c.states.values():
            if state.validity == StateValidity.INVALID:
                for request in self.c.requests.values():
                    if request.current_attempt_id:
                        continuation = self.c.continuations[request.continuation_id]
                        session = self.c.sessions[continuation.session_id]
                        context = __import__(
                            "continuity.entities",
                            fromlist=["ExecutionContext"],
                        ).ExecutionContext(
                            session.program_id,
                            session.id,
                            continuation.id,
                            request.id,
                            request.current_attempt_id,
                        )
                        _require(not self.c.state_compatible(state.id, context))
