from continuity.entities import ExecutionContext, StateValidity, ExecutionStatus, AttemptAuthority, ContinuationLifecycle
from continuity.invariants import InvariantOracle

def setup_branch(core):
    core.create_program('p'); core.create_session('s','p'); core.create_continuation('c0','s')
    core.create_continuation('c1','s',['c0']); core.create_continuation('c2','s',['c0'])

def test_wrong_sibling_state_rejected(core):
    setup_branch(core)
    core.create_state('x',origin_type='continuation',origin_id='c1')
    core.create_request('r2','c2'); core.start_attempt('a2','r2')
    ctx=ExecutionContext('p','s','c2','r2','a2')
    assert not core.state_compatible('x',ctx)

def test_valid_ancestor_state_accepted(core):
    setup_branch(core)
    core.create_state('x0',origin_type='continuation',origin_id='c0')
    core.create_request('r2','c2'); core.start_attempt('a2','r2')
    ctx=ExecutionContext('p','s','c2','r2','a2')
    assert core.state_compatible('x0',ctx)

def test_superseded_producer_state_rejected(core):
    core.create_program('p'); core.create_session('s','p'); core.create_continuation('c','s')
    core.create_request('r','c'); core.start_attempt('a1','r'); core.create_state('x1',origin_type='attempt',origin_id='a1')
    core.start_attempt('a2','r')
    ctx=ExecutionContext('p','s','c','r','a2')
    assert core.attempts['a1'].authority_status is AttemptAuthority.SUPERSEDED
    assert not core.state_compatible('x1',ctx)

def test_invalid_state_never_compatible(core):
    core.create_program('p'); core.create_session('s','p'); core.create_continuation('c','s'); core.create_request('r','c'); core.start_attempt('a','r')
    core.create_state('x',origin_type='continuation',origin_id='c'); core.set_state_validity('x',StateValidity.INVALID)
    ctx=ExecutionContext('p','s','c','r','a')
    assert not core.state_compatible('x',ctx)
    InvariantOracle(core).assert_all()

def test_derived_state_cannot_launder_sibling_provenance(core):
    setup_branch(core)
    core.create_state('x1', origin_type='continuation', origin_id='c1')
    import pytest
    from continuity.errors import SemanticViolation
    with pytest.raises(SemanticViolation):
        core.create_state('laundered', origin_type='continuation', origin_id='c0', derived_from=['x1'])


def test_state_compatibility_validates_entire_execution_context(core):
    setup_branch(core)
    core.create_state('x0', origin_type='continuation', origin_id='c0')
    core.create_request('r2', 'c2'); core.start_attempt('a2', 'r2')
    core.create_request('r1', 'c1'); core.start_attempt('a1', 'r1')

    assert not core.state_compatible('x0', ExecutionContext('p', 's', 'c2', 'missing', 'a2'))
    assert not core.state_compatible('x0', ExecutionContext('p', 's', 'c2', 'r1', 'a1'))
    assert not core.state_compatible('x0', ExecutionContext('p', 's', 'c2', 'r2', 'a1'))
    assert not core.state_compatible('x0', ExecutionContext('p', 's', 'c2', 'r2', 'a2', phase_id='unmodeled'))


def test_new_active_descendant_refreshes_ancestor_state_lifecycle(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c0', 's')
    core.create_state('x', origin_type='continuation', origin_id='c0')
    core.set_continuation_lifecycle('c0', ContinuationLifecycle.TERMINAL)
    assert core.states['x'].lifecycle.name == 'TERMINAL'
    core.create_continuation('c1', 's', ['c0'], ContinuationLifecycle.ACTIVE)
    assert core.states['x'].lifecycle.name == 'ACTIVE'


def test_derived_state_recursively_inherits_superseded_producer_invalidity(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_request('r', 'c'); core.start_attempt('a1', 'r')
    core.create_state('producer', origin_type='attempt', origin_id='a1')
    core.create_state('derived', origin_type='continuation', origin_id='c', derived_from=['producer'])
    core.start_attempt('a2', 'r')
    ctx = ExecutionContext('p', 's', 'c', 'r', 'a2')
    assert core.attempts['a1'].authority_status is AttemptAuthority.SUPERSEDED
    assert not core.state_compatible('derived', ctx)
