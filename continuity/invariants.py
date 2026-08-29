from __future__ import annotations
from .core import ContinuityCore
from .entities import AttemptAuthority, BindingStatus, EvidenceAuthority, EvidenceStatus, RequestStatus, StateValidity

class InvariantOracle:
    """Independent structural checks over normalized core state."""

    def __init__(self, core: ContinuityCore):
        self.c = core

    def assert_all(self) -> None:
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

    def _parent_scopes(self):
        for session in self.c.sessions.values():
            assert session.program_id in self.c.programs
        for continuation in self.c.continuations.values():
            assert continuation.session_id in self.c.sessions
            for parent_id in continuation.parent_ids:
                parent = self.c.continuations[parent_id]
                assert parent.session_id == continuation.session_id
        for request in self.c.requests.values():
            assert request.continuation_id in self.c.continuations
        for attempt in self.c.attempts.values():
            assert attempt.request_id in self.c.requests
        for phase in self.c.phases.values():
            assert phase.attempt_id in self.c.attempts

    def _attempt_authority(self):
        for rid, r in self.c.requests.items():
            current=[a for a in self.c.attempts.values() if a.request_id==rid and a.authority_status==AttemptAuthority.CURRENT]
            committed=[a for a in self.c.attempts.values() if a.request_id==rid and a.authority_status==AttemptAuthority.COMMITTED]
            assert len(current) <= 1
            assert len(committed) <= 1
            if r.current_attempt_id is None: assert not current
            else: assert len(current)==1 and current[0].id==r.current_attempt_id
            if r.committed_attempt_id is None: assert not committed
            else: assert len(committed)==1 and committed[0].id==r.committed_attempt_id
            for a in self.c.attempts.values():
                if a.request_id==rid and a.authority_status==AttemptAuthority.SUPERSEDED:
                    assert a.id != r.current_attempt_id and a.id != r.committed_attempt_id

    def _attempt_generations(self):
        for request_id in self.c.requests:
            attempts = [a for a in self.c.attempts.values() if a.request_id == request_id]
            generations = sorted(a.generation for a in attempts)
            assert generations == list(range(1, len(attempts) + 1))

    def _request_commit_consistency(self):
        for r in self.c.requests.values():
            if r.status == RequestStatus.COMPLETED:
                assert r.current_attempt_id is None
                assert r.committed_attempt_id is not None
                assert r.authoritative_output_id is not None
                assert self.c.outputs[r.authoritative_output_id].attempt_id == r.committed_attempt_id

    def _continuation_dags(self):
        for sid in self.c.sessions:
            assert not self.c._has_cycle(sid)

    def _phase_ordering(self):
        for attempt_id in self.c.attempts:
            phases = sorted((p for p in self.c.phases.values() if p.attempt_id == attempt_id), key=lambda p: p.ordinal)
            assert [p.ordinal for p in phases] == list(range(1, len(phases) + 1))
            assert all(p.attempt_id == attempt_id for p in phases)

    def _state_provenance(self):
        assert not self.c._state_cycle()
        for x in self.c.states.values():
            assert x.origin_continuation_id in self.c.continuations
            for dependency_id in x.derived_from:
                dependency = self.c.states[dependency_id]
                assert self.c.is_ancestor(dependency.origin_continuation_id, x.origin_continuation_id)
            if x.producer_attempt_id:
                a=self.c.attempts[x.producer_attempt_id]
                if x.origin_request_id:
                    assert a.request_id == x.origin_request_id
            if x.producer_phase_id:
                phase = self.c.phases[x.producer_phase_id]
                assert phase.attempt_id == x.producer_attempt_id
                assert phase.status.name == "COMPLETED"

    def _replica_integrity(self):
        for replica in self.c.replicas.values():
            assert replica.state_id in self.c.states
            if replica.binding_id is not None:
                binding = self.c.bindings[replica.binding_id]
                assert replica.binding_epoch == binding.epoch

    def _binding_authority(self):
        for subj,bid in self.c.current_binding_by_subject.items():
            b=self.c.bindings[bid]
            assert b.subject_id==subj
            assert b.status==BindingStatus.ACTIVE
            assert self.c.current_epoch_by_subject[subj]==b.epoch
        for b in self.c.bindings.values():
            if b.status==BindingStatus.ACTIVE:
                assert self.c.current_binding_by_subject.get(b.subject_id)==b.id

    def _binding_generations(self):
        by_subject = {}
        for binding in self.c.bindings.values():
            by_subject.setdefault(binding.subject_id, []).append(binding)
            assert binding.epoch > binding.base_epoch
        for subject, bindings in by_subject.items():
            epochs = [b.epoch for b in bindings]
            assert len(epochs) == len(set(epochs))
            assert self.c.last_allocated_epoch_by_subject[subject] == max(epochs)
            current_epoch = self.c.current_epoch_by_subject.get(subject)
            if current_epoch is not None:
                assert current_epoch in epochs

    def _evidence_derivation(self):
        for evidence in self.c.evidence.values():
            has_derivation = bool(evidence.derived_from or evidence.derivation_rule)
            if evidence.authority == EvidenceAuthority.DERIVED:
                assert evidence.derived_from
                assert evidence.derivation_rule
            else:
                assert not has_derivation
            if evidence.derived_from:
                supports = [self.c.evidence[eid] for eid in evidence.derived_from]
                union_scope = set().union(*(set(s.scope) for s in supports)) if supports else set()
                assert set(evidence.scope).issubset(union_scope)
                if evidence.status == EvidenceStatus.VALID:
                    assert all(s.status == EvidenceStatus.VALID for s in supports)

    def _event_identity(self):
        assert len(self.c.event_order) == len(set(self.c.event_order))
        assert set(self.c.event_order) == set(self.c.events)
        for event_id in self.c.event_order:
            assert self.c.events[event_id].id == event_id

    def _output_integrity(self):
        for output in self.c.outputs.values():
            assert output.attempt_id in self.c.attempts
            for evidence_id in output.evidence_ids:
                assert evidence_id in self.c.evidence

    def _state_validity(self):
        for x in self.c.states.values():
            if x.validity == StateValidity.INVALID:
                # Oracle checks that invalid logical state is never claimed as compatible with any known context.
                for r in self.c.requests.values():
                    if r.current_attempt_id:
                        c=self.c.continuations[r.continuation_id]; s=self.c.sessions[c.session_id]
                        ctx=__import__('continuity.entities',fromlist=['ExecutionContext']).ExecutionContext(
                            s.program_id,s.id,c.id,r.id,r.current_attempt_id)
                        assert not self.c.state_compatible(x.id,ctx)
