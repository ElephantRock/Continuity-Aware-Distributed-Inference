from continuity.entities import ContinuationLifecycle, ExecutionContext, Evidence, EvidenceAuthority, EvidenceStatus, ExecutionStatus, StateValidity
from continuity.errors import SemanticViolation
import pytest

def exact(core,id_,scope,t=10):
    core.record_evidence(Evidence(id_,'ok','test',EvidenceAuthority.EXACT_OBSERVATION,EvidenceStatus.VALID,t,frozenset(scope)))

def test_tool_wait_creates_descendant(core):
    core.create_program('p'); core.create_session('s','p'); core.create_continuation('c1','s')
    core.create_state('x',origin_type='continuation',origin_id='c1')
    core.set_continuation_lifecycle('c1',ContinuationLifecycle.WAITING)
    assert core.states['x'].lifecycle.name == 'WAITING'
    core.resume_after_wait('c1','c2')
    assert core.continuations['c1'].lifecycle is ContinuationLifecycle.TERMINAL
    assert core.continuations['c2'].lifecycle is ContinuationLifecycle.ACTIVE
    assert core.states['x'].lifecycle.name == 'ACTIVE'

def test_replica_evidence_scoped_to_exact_state_and_replica(core):
    core.create_program('p'); core.create_session('s','p'); core.create_continuation('c','s'); core.create_request('r','c'); core.start_attempt('a','r')
    core.create_state('x',origin_type='continuation',origin_id='c'); core.add_replica('rp','x','w')
    ctx=ExecutionContext('p','s','c','r','a')
    exact(core,'bad',{('state','x')})
    assert not core.can_consume_state('x','rp',ctx,['bad'],now=10)
    exact(core,'good',{('state','x'),('replica','rp')})
    assert core.can_consume_state('x','rp',ctx,['good'],now=10)
