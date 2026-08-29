from __future__ import annotations
from .core import ContinuityCore
from .entities import AttemptAuthority, BindingStatus, RequestStatus, StateValidity

class InvariantOracle:
    """Independent structural checks over normalized core state."""

    def __init__(self, core: ContinuityCore):
        self.c = core

    def assert_all(self) -> None:
        self._attempt_authority()
        self._request_commit_consistency()
        self._continuation_dags()
        self._state_provenance()
        self._binding_authority()
        self._state_validity()

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

    def _binding_authority(self):
        for subj,bid in self.c.current_binding_by_subject.items():
            b=self.c.bindings[bid]
            assert b.subject_id==subj
            assert b.status==BindingStatus.ACTIVE
            assert self.c.current_epoch_by_subject[subj]==b.epoch
        for b in self.c.bindings.values():
            if b.status==BindingStatus.ACTIVE:
                assert self.c.current_binding_by_subject.get(b.subject_id)==b.id

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
