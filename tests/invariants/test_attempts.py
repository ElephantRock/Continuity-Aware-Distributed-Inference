import pytest
from continuity.entities import AttemptAuthority, ExecutionStatus
from continuity.errors import SemanticViolation, InvalidTransition
from continuity.invariants import InvariantOracle

def test_late_success_can_be_superseded(core, exact_evidence):
    core.create_program('p'); core.create_session('s','p'); core.create_continuation('c','s')
    core.create_request('r','c')
    a1=core.start_attempt('a1','r'); core.set_attempt_execution('a1',ExecutionStatus.RUNNING)
    a2=core.start_attempt('a2','r'); core.set_attempt_execution('a2',ExecutionStatus.RUNNING)
    core.complete_attempt('a2',True)
    exact_evidence(core,'e2',{('attempt','a2')})
    core.create_output('o2','a2',True,['e2']); core.finalize_request('r','o2',now=10)
    core.complete_attempt('a1',True)
    assert core.attempts['a1'].execution_status is ExecutionStatus.SUCCEEDED
    assert core.attempts['a1'].authority_status is AttemptAuthority.SUPERSEDED
    assert core.requests['r'].committed_attempt_id == 'a2'
    InvariantOracle(core).assert_all()

def test_superseded_output_cannot_finalize(core, exact_evidence):
    core.create_program('p'); core.create_session('s','p'); core.create_continuation('c','s'); core.create_request('r','c')
    core.start_attempt('a1','r'); core.start_attempt('a2','r'); core.complete_attempt('a1',True)
    exact_evidence(core,'e1',{('attempt','a1')}); core.create_output('o1','a1',True,['e1'])
    with pytest.raises(SemanticViolation): core.finalize_request('r','o1',now=10)

def test_completed_output_immutable(core, exact_evidence):
    core.create_program('p'); core.create_session('s','p'); core.create_continuation('c','s'); core.create_request('r','c')
    core.start_attempt('a','r'); core.complete_attempt('a',True)
    exact_evidence(core,'e',{('attempt','a')}); core.create_output('o','a',True,['e']); core.finalize_request('r','o',now=10)
    assert core.finalize_request('r','o',now=10).authoritative_output_id == 'o'
    with pytest.raises(InvalidTransition):
        core.create_output('o2','a',True,['e']); core.finalize_request('r','o2',now=10)
