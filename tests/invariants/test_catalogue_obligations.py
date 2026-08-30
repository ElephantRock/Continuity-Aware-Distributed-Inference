from dataclasses import replace
import pytest

from continuity.entities import (
    AttemptAuthority, ContinuationLifecycle, StateValidity, ReusableState,
)
from continuity.errors import InvalidTransition, SemanticViolation
from continuity.invariants import InvariantOracle


def test_a1_entity_identity_uniqueness_is_enforced_globally(core):
    core.create_program('same')
    with pytest.raises(SemanticViolation):
        core.create_session('same', 'same')


def test_a2_parent_scope_consistency_rejects_cross_session_continuation_parent(core):
    core.create_program('p'); core.create_session('s1', 'p'); core.create_session('s2', 'p')
    core.create_continuation('c1', 's1')
    with pytest.raises(SemanticViolation):
        core.create_continuation('c2', 's2', ['c1'])


def test_a3_oracle_detects_continuation_cycle_corruption(core):
    core.create_program('p'); core.create_session('s', 'p')
    core.create_continuation('c0', 's'); core.create_continuation('c1', 's', ['c0'])
    core.continuations['c0'] = replace(core.continuations['c0'], parent_ids=frozenset({'c1'}))
    with pytest.raises(AssertionError):
        InvariantOracle(core).assert_all()


def test_a4_state_provenance_resolves_exactly_one_continuation(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    state = core.create_state('x', origin_type='continuation', origin_id='c')
    assert state.origin_continuation_id == 'c'
    InvariantOracle(core).assert_all()


def test_b2_oracle_detects_cross_request_attempt_authority_corruption(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_request('r1', 'c'); core.create_request('r2', 'c'); core.start_attempt('a1', 'r1')
    core.requests['r2'] = replace(core.requests['r2'], current_attempt_id='a1')
    with pytest.raises(AssertionError):
        InvariantOracle(core).assert_all()


def test_b3_attempt_generations_are_monotonic_and_oracle_checked(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's'); core.create_request('r', 'c')
    assert core.start_attempt('a1', 'r').generation == 1
    assert core.start_attempt('a2', 'r').generation == 2
    assert core.start_attempt('a3', 'r').generation == 3
    InvariantOracle(core).assert_all()
    core.attempts['a3'] = replace(core.attempts['a3'], generation=7)
    with pytest.raises(AssertionError):
        InvariantOracle(core).assert_all()


def test_c5_multiple_replicas_preserve_one_logical_state_identity(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_state('x', origin_type='continuation', origin_id='c')
    core.add_replica('r1', 'x', 'w1'); core.add_replica('r2', 'x', 'w2')
    assert {core.replicas['r1'].state_id, core.replicas['r2'].state_id} == {'x'}
    assert set(core.states) == {'x'}
    InvariantOracle(core).assert_all()


def test_c7_oracle_detects_state_derivation_cycle_corruption(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_state('x1', origin_type='continuation', origin_id='c')
    core.create_state('x2', origin_type='continuation', origin_id='c', derived_from=['x1'])
    core.states['x1'] = replace(core.states['x1'], derived_from=frozenset({'x2'}))
    with pytest.raises(AssertionError):
        InvariantOracle(core).assert_all()


def test_f4_terminal_continuation_cannot_reactivate(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.set_continuation_lifecycle('c', ContinuationLifecycle.TERMINAL)
    with pytest.raises(InvalidTransition):
        core.set_continuation_lifecycle('c', ContinuationLifecycle.ACTIVE)


def test_f6_invalid_state_cannot_be_resurrected(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_state('x', origin_type='continuation', origin_id='c')
    core.set_state_validity('x', StateValidity.INVALID)
    with pytest.raises(InvalidTransition):
        core.set_state_validity('x', StateValidity.VALID)


def test_oracle_detects_broken_replica_parent(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_state('x', origin_type='continuation', origin_id='c'); core.add_replica('rp', 'x', 'w')
    core.replicas['rp'] = replace(core.replicas['rp'], state_id='missing')
    with pytest.raises(AssertionError):
        InvariantOracle(core).assert_all()


def test_attempt_execution_status_cannot_move_backward(core):
    from continuity.entities import ExecutionStatus
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_request('r', 'c'); core.start_attempt('a', 'r')
    core.set_attempt_execution('a', ExecutionStatus.DISPATCHED)
    core.set_attempt_execution('a', ExecutionStatus.RUNNING)
    with pytest.raises(InvalidTransition):
        core.set_attempt_execution('a', ExecutionStatus.DISPATCHED)
    with pytest.raises(InvalidTransition):
        core.set_attempt_execution('a', ExecutionStatus.CREATED)


def test_phase_status_cannot_move_backward(core):
    from continuity.entities import PhaseStatus, PhaseType
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_request('r', 'c'); core.start_attempt('a', 'r')
    core.create_phase('phase', 'a', PhaseType.PREFILL)
    core.set_phase_status('phase', PhaseStatus.RUNNING)
    with pytest.raises(InvalidTransition):
        core.set_phase_status('phase', PhaseStatus.CREATED)


def test_output_rejects_unresolved_evidence_reference(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_request('r', 'c'); core.start_attempt('a', 'r')
    with pytest.raises(SemanticViolation):
        core.create_output('o', 'a', True, ['later'])
    assert 'o' not in core.outputs


def test_oracle_rejects_mismatched_declared_phase_origin(core):
    from continuity.entities import PhaseStatus, PhaseType
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_request('r', 'c'); core.start_attempt('a', 'r')
    core.create_phase('p1', 'a', PhaseType.PREFILL); core.set_phase_status('p1', PhaseStatus.RUNNING); core.complete_phase('p1')
    core.create_phase('p2', 'a', PhaseType.DECODE); core.set_phase_status('p2', PhaseStatus.RUNNING); core.complete_phase('p2')
    core.create_state('x', origin_type='phase', origin_id='p1')
    core.states['x'] = replace(core.states['x'], origin_id='p2')
    with pytest.raises(AssertionError):
        InvariantOracle(core).assert_all()
