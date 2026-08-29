from __future__ import annotations
from dataclasses import replace
from typing import Callable, Iterable, Optional
import time

from .entities import (
    Attempt, AttemptAuthority, Binding, BindingStatus, Continuation, ContinuationLifecycle,
    Evidence, EvidenceAuthority, EvidenceStatus, ExecutionContext, ExecutionStatus,
    LogicalRequest, Output, Program, ProgramStatus, ReconcileOutcome, ReplicaStatus,
    RequestStatus, ReusableState, Session, StateLifecycle, StateReplica, StateValidity,
)
from .errors import InvalidTransition, InsufficientEvidence, SemanticViolation

class ContinuityCore:
    """Single-authority deterministic semantic core for Paper 1 C1."""

    REQUIRED_AUTHORITY = {
        "finalize": EvidenceAuthority.EXACT_OBSERVATION,
        "consume_state": EvidenceAuthority.EXACT_OBSERVATION,
        "commit_migration": EvidenceAuthority.EXACT_OBSERVATION,
        "rank_endpoint": EvidenceAuthority.ESTIMATED,
        "estimate_reuse": EvidenceAuthority.ESTIMATED,
    }

    def __init__(self, semantic_validity: Optional[Callable[[ReusableState, ExecutionContext], bool]] = None):
        self.programs: dict[str, Program] = {}
        self.sessions: dict[str, Session] = {}
        self.continuations: dict[str, Continuation] = {}
        self.requests: dict[str, LogicalRequest] = {}
        self.attempts: dict[str, Attempt] = {}
        self.states: dict[str, ReusableState] = {}
        self.replicas: dict[str, StateReplica] = {}
        self.bindings: dict[str, Binding] = {}
        self.evidence: dict[str, Evidence] = {}
        self.outputs: dict[str, Output] = {}
        self.current_binding_by_subject: dict[str, str] = {}
        self.current_epoch_by_subject: dict[str, int] = {}
        self.last_allocated_epoch_by_subject: dict[str, int] = {}
        self.semantic_validity = semantic_validity or (lambda state, ctx: True)

    # ---------- identity / graph ----------
    def _unique(self, id_: str) -> None:
        collections = (self.programs, self.sessions, self.continuations, self.requests,
                       self.attempts, self.states, self.replicas, self.bindings,
                       self.evidence, self.outputs)
        if any(id_ in c for c in collections):
            raise SemanticViolation(f"duplicate logical identifier: {id_}")

    def create_program(self, id_: str) -> Program:
        self._unique(id_); p = Program(id_); self.programs[id_] = p; return p

    def set_program_status(self, program_id: str, status: ProgramStatus) -> Program:
        p = self.programs[program_id]
        if p.status in {ProgramStatus.COMPLETED, ProgramStatus.FAILED, ProgramStatus.CANCELLED} and status != p.status:
            raise InvalidTransition("terminal Program is immutable")
        p = replace(p, status=status); self.programs[program_id] = p; return p

    def create_session(self, id_: str, program_id: str) -> Session:
        self._unique(id_); self.programs[program_id]
        s = Session(id_, program_id); self.sessions[id_] = s; return s

    def create_continuation(self, id_: str, session_id: str, parent_ids: Iterable[str] = (), lifecycle: ContinuationLifecycle = ContinuationLifecycle.ACTIVE) -> Continuation:
        self._unique(id_); self.sessions[session_id]
        parents = frozenset(parent_ids)
        for pid in parents:
            p = self.continuations[pid]
            if p.session_id != session_id:
                raise SemanticViolation("Continuation parents must be in same Session")
        c = Continuation(id_, session_id, parents, lifecycle)
        self.continuations[id_] = c
        if self._has_cycle(session_id):
            del self.continuations[id_]
            raise SemanticViolation("Continuation DAG cycle")
        return c

    def _has_cycle(self, session_id: str) -> bool:
        nodes = {cid:c for cid,c in self.continuations.items() if c.session_id == session_id}
        visiting, visited = set(), set()
        def dfs(n):
            if n in visiting: return True
            if n in visited: return False
            visiting.add(n)
            for p in nodes[n].parent_ids:
                if p in nodes and dfs(p): return True
            visiting.remove(n); visited.add(n); return False
        return any(dfs(n) for n in nodes if n not in visited)

    def is_ancestor(self, ancestor_id: str, descendant_id: str) -> bool:
        if ancestor_id == descendant_id: return True
        target = self.continuations[descendant_id]
        todo = list(target.parent_ids); seen = set()
        while todo:
            x = todo.pop()
            if x == ancestor_id: return True
            if x in seen: continue
            seen.add(x); todo.extend(self.continuations[x].parent_ids)
        return False

    def set_continuation_lifecycle(self, continuation_id: str, lifecycle: ContinuationLifecycle) -> Continuation:
        c = self.continuations[continuation_id]
        if c.lifecycle in {ContinuationLifecycle.TERMINAL, ContinuationLifecycle.ABANDONED} and lifecycle != c.lifecycle:
            raise InvalidTransition("terminal/abandoned Continuation cannot reactivate")
        c = replace(c, lifecycle=lifecycle); self.continuations[continuation_id] = c
        self._refresh_state_lifecycles(); return c

    def resume_after_wait(self, waiting_id: str, child_id: str) -> Continuation:
        c = self.continuations[waiting_id]
        if c.lifecycle != ContinuationLifecycle.WAITING:
            raise InvalidTransition("Continuation is not WAITING")
        child = self.create_continuation(child_id, c.session_id, [waiting_id], ContinuationLifecycle.ACTIVE)
        self.continuations[waiting_id] = replace(c, lifecycle=ContinuationLifecycle.TERMINAL)
        self._refresh_state_lifecycles(); return child

    # ---------- requests / attempts ----------
    def create_request(self, id_: str, continuation_id: str) -> LogicalRequest:
        self._unique(id_); self.continuations[continuation_id]
        r = LogicalRequest(id_, continuation_id, RequestStatus.READY); self.requests[id_] = r; return r

    def start_attempt(self, id_: str, request_id: str) -> Attempt:
        self._unique(id_); r = self.requests[request_id]
        if r.status in {RequestStatus.COMPLETED, RequestStatus.FAILED, RequestStatus.CANCELLED}:
            raise InvalidTransition("cannot start Attempt for terminal request")
        existing = [a for a in self.attempts.values() if a.request_id == request_id]
        generation = max([a.generation for a in existing], default=0) + 1
        if r.current_attempt_id:
            old = self.attempts[r.current_attempt_id]
            if old.authority_status != AttemptAuthority.CURRENT:
                raise SemanticViolation("CurrentAttempt pointer is not CURRENT")
            self.attempts[old.id] = replace(old, authority_status=AttemptAuthority.SUPERSEDED)
        a = Attempt(id_, request_id, generation, ExecutionStatus.CREATED, AttemptAuthority.CURRENT)
        self.attempts[id_] = a
        self.requests[request_id] = replace(r, status=RequestStatus.RUNNING, current_attempt_id=id_)
        return a

    def set_attempt_execution(self, attempt_id: str, status: ExecutionStatus) -> Attempt:
        a = self.attempts[attempt_id]
        terminal = {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED}
        if a.execution_status in terminal and status != a.execution_status:
            raise InvalidTransition("terminal execution outcome is immutable")
        a = replace(a, execution_status=status); self.attempts[attempt_id] = a; return a

    def complete_attempt(self, attempt_id: str, succeeded: bool = True) -> Attempt:
        return self.set_attempt_execution(attempt_id, ExecutionStatus.SUCCEEDED if succeeded else ExecutionStatus.FAILED)

    def create_output(self, id_: str, attempt_id: str, terminal: bool, evidence_ids: Iterable[str] = ()) -> Output:
        self._unique(id_); self.attempts[attempt_id]
        o = Output(id_, attempt_id, terminal, frozenset(evidence_ids)); self.outputs[id_] = o; return o

    def finalize_request(self, request_id: str, output_id: str, now: Optional[float] = None) -> LogicalRequest:
        r, o = self.requests[request_id], self.outputs[output_id]
        a = self.attempts[o.attempt_id]
        if r.status == RequestStatus.COMPLETED:
            if r.authoritative_output_id == output_id: return r
            raise InvalidTransition("completed request output is immutable")
        if o.attempt_id != r.current_attempt_id or a.authority_status != AttemptAuthority.CURRENT:
            raise SemanticViolation("only CURRENT Attempt may finalize")
        if a.execution_status != ExecutionStatus.SUCCEEDED or not o.terminal:
            raise SemanticViolation("Attempt must have terminal successful output")
        self.require_evidence("finalize", o.evidence_ids, now=now, required_scope={("attempt", a.id)})
        a = replace(a, authority_status=AttemptAuthority.COMMITTED); self.attempts[a.id] = a
        r = replace(r, status=RequestStatus.COMPLETED, current_attempt_id=None,
                    committed_attempt_id=a.id, authoritative_output_id=o.id)
        self.requests[request_id] = r; return r

    # ---------- State ----------
    def create_state(self, id_: str, *, origin_type: str, origin_id: str,
                     semantic_type: str = "PREFIX", representation: str = "OPAQUE",
                     derived_from: Iterable[str] = ()) -> ReusableState:
        self._unique(id_)
        oc, orq, pa = self._resolve_origin(origin_type, origin_id)
        for sid in derived_from: self.states[sid]
        x = ReusableState(id_, origin_type, origin_id, oc, orq, pa, semantic_type,
                          representation, StateLifecycle.TERMINAL, StateValidity.VALID,
                          frozenset(derived_from))
        self.states[id_] = x
        if self._state_cycle():
            del self.states[id_]; raise SemanticViolation("State provenance cycle")
        self._refresh_state_lifecycles(); return self.states[id_]

    def _resolve_origin(self, origin_type: str, origin_id: str):
        if origin_type == "continuation":
            c = self.continuations[origin_id]; return c.id, None, None
        if origin_type == "request":
            r = self.requests[origin_id]; return r.continuation_id, r.id, r.committed_attempt_id
        if origin_type == "attempt":
            a = self.attempts[origin_id]; r = self.requests[a.request_id]; return r.continuation_id, r.id, a.id
        raise SemanticViolation(f"unsupported origin_type: {origin_type}")

    def _state_cycle(self) -> bool:
        visiting, visited = set(), set()
        def dfs(s):
            if s in visiting: return True
            if s in visited: return False
            visiting.add(s)
            for p in self.states[s].derived_from:
                if dfs(p): return True
            visiting.remove(s); visited.add(s); return False
        return any(dfs(s) for s in self.states if s not in visited)

    def set_state_validity(self, state_id: str, validity: StateValidity) -> ReusableState:
        x = self.states[state_id]
        if x.validity == StateValidity.INVALID and validity != StateValidity.INVALID:
            raise InvalidTransition("INVALID logical State cannot be resurrected")
        x = replace(x, validity=validity); self.states[state_id] = x; return x

    def _refresh_state_lifecycles(self) -> None:
        rank = {ContinuationLifecycle.ACTIVE: StateLifecycle.ACTIVE,
                ContinuationLifecycle.WAITING: StateLifecycle.WAITING,
                ContinuationLifecycle.SPECULATIVE: StateLifecycle.SPECULATIVE}
        for sid, x in list(self.states.items()):
            candidates=[]
            for c in self.continuations.values():
                if self.is_ancestor(x.origin_continuation_id, c.id) and c.lifecycle in rank:
                    candidates.append(rank[c.lifecycle])
            life = StateLifecycle.TERMINAL
            if StateLifecycle.ACTIVE in candidates: life = StateLifecycle.ACTIVE
            elif StateLifecycle.WAITING in candidates: life = StateLifecycle.WAITING
            elif StateLifecycle.SPECULATIVE in candidates: life = StateLifecycle.SPECULATIVE
            self.states[sid] = replace(x, lifecycle=life)

    def state_compatible(self, state_id: str, ctx: ExecutionContext) -> bool:
        x = self.states[state_id]
        if x.validity != StateValidity.VALID: return False
        c = self.continuations[ctx.continuation_id]
        s = self.sessions[c.session_id]
        if s.id != ctx.session_id or s.program_id != ctx.program_id: return False
        if not self.is_ancestor(x.origin_continuation_id, ctx.continuation_id): return False
        if x.producer_attempt_id:
            pa = self.attempts[x.producer_attempt_id]
            if pa.authority_status == AttemptAuthority.SUPERSEDED: return False
            if x.origin_request_id:
                pr = self.requests[x.origin_request_id]
                if pr.status == RequestStatus.COMPLETED:
                    if pr.committed_attempt_id != pa.id: return False
                else:
                    if pa.id != ctx.attempt_id or pa.authority_status != AttemptAuthority.CURRENT: return False
            elif pa.id != ctx.attempt_id or pa.authority_status != AttemptAuthority.CURRENT:
                return False
        return bool(self.semantic_validity(x, ctx))

    def add_replica(self, id_: str, state_id: str, location_id: str, status: ReplicaStatus = ReplicaStatus.VALID) -> StateReplica:
        self._unique(id_); self.states[state_id]
        r = StateReplica(id_, state_id, location_id, status); self.replicas[id_] = r; return r

    def set_replica_status(self, replica_id: str, status: ReplicaStatus) -> StateReplica:
        r = replace(self.replicas[replica_id], status=status); self.replicas[replica_id] = r; return r

    def can_consume_state(self, state_id: str, replica_id: str, ctx: ExecutionContext,
                          evidence_ids: Iterable[str], now: Optional[float] = None) -> bool:
        r = self.replicas[replica_id]
        if r.state_id != state_id or r.status != ReplicaStatus.VALID: return False
        if not self.state_compatible(state_id, ctx): return False
        try:
            self.require_evidence("consume_state", evidence_ids, now=now,
                                  required_scope={("state", state_id), ("replica", replica_id)})
        except InsufficientEvidence:
            return False
        return True

    # ---------- binding / migration ----------
    def propose_binding(self, id_: str, subject_id: str, location_id: str) -> Binding:
        self._unique(id_)
        base = self.current_epoch_by_subject.get(subject_id, 0)
        last = self.last_allocated_epoch_by_subject.get(subject_id, base)
        epoch = last + 1
        self.last_allocated_epoch_by_subject[subject_id] = epoch
        b = Binding(id_, subject_id, location_id, base, epoch, BindingStatus.PROPOSED)
        self.bindings[id_] = b; return b

    def activate_initial_binding(self, id_: str, subject_id: str, location_id: str) -> Binding:
        if subject_id in self.current_binding_by_subject:
            raise InvalidTransition("subject already has current Binding")
        b = self.propose_binding(id_, subject_id, location_id)
        b = replace(b, status=BindingStatus.ACTIVE)
        self.bindings[id_] = b
        self.current_binding_by_subject[subject_id] = id_
        self.current_epoch_by_subject[subject_id] = b.epoch
        return b

    def begin_migration(self, binding_id: str) -> Binding:
        b = self.bindings[binding_id]
        if b.status != BindingStatus.PROPOSED: raise InvalidTransition("candidate not PROPOSED")
        b = replace(b, status=BindingStatus.MIGRATING); self.bindings[b.id] = b; return b

    def commit_migration(self, binding_id: str, evidence_ids: Iterable[str], now: Optional[float] = None) -> Binding:
        b = self.bindings[binding_id]
        current_epoch = self.current_epoch_by_subject.get(b.subject_id, 0)
        if b.base_epoch != current_epoch:
            raise SemanticViolation("migration candidate base_epoch is stale")
        if b.status not in {BindingStatus.PROPOSED, BindingStatus.MIGRATING}:
            raise InvalidTransition("candidate not eligible")
        self.require_evidence("commit_migration", evidence_ids, now=now,
                              required_scope={("binding", b.id), ("epoch", str(b.epoch))})
        old_id = self.current_binding_by_subject.get(b.subject_id)
        if old_id:
            old = self.bindings[old_id]
            self.bindings[old_id] = replace(old, status=BindingStatus.SUPERSEDED)
        b = replace(b, status=BindingStatus.ACTIVE); self.bindings[b.id] = b
        self.current_binding_by_subject[b.subject_id] = b.id
        self.current_epoch_by_subject[b.subject_id] = b.epoch
        return b

    # ---------- evidence / reconcile ----------
    def record_evidence(self, e: Evidence) -> Evidence:
        self._unique(e.id); self.evidence[e.id] = e; return e

    def require_evidence(self, action: str, evidence_ids: Iterable[str], *, now: Optional[float] = None,
                         required_scope: set[tuple[str,str]] | None = None, max_age: Optional[float] = None) -> list[Evidence]:
        now = time.time() if now is None else now
        req = self.REQUIRED_AUTHORITY[action]
        evs = [self.evidence[eid] for eid in evidence_ids]
        good=[]
        for e in evs:
            if e.status != EvidenceStatus.VALID: continue
            if e.authority < req: continue
            if e.valid_until is not None and now > e.valid_until: continue
            if max_age is not None and now - e.observed_at > max_age: continue
            if required_scope and not required_scope.issubset(set(e.scope)): continue
            good.append(e)
        if not good:
            raise InsufficientEvidence(f"insufficient Evidence for {action}")
        return good

    def reconcile(self, action: str, evidence_ids: Iterable[str], *, now: Optional[float] = None,
                  required_scope: set[tuple[str,str]] | None = None) -> ReconcileOutcome:
        evs=[self.evidence[eid] for eid in evidence_ids]
        if any(e.status == EvidenceStatus.AMBIGUOUS for e in evs): return ReconcileOutcome.AMBIGUOUS
        try:
            self.require_evidence(action, evidence_ids, now=now, required_scope=required_scope)
            return ReconcileOutcome.MATCHED
        except InsufficientEvidence:
            return ReconcileOutcome.WAIT
