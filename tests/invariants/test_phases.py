import pytest

from continuity.entities import ExecutionContext, PhaseStatus, PhaseType
from continuity.errors import SemanticViolation
from continuity.invariants import InvariantOracle


def setup_attempt(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_request('r', 'c'); core.start_attempt('a', 'r')


def test_phase_ordinals_are_contiguous_and_monotonic(core):
    setup_attempt(core)
    p1 = core.create_phase('p1', 'a', PhaseType.PREFILL)
    p2 = core.create_phase('p2', 'a', PhaseType.DECODE)
    assert (p1.ordinal, p2.ordinal) == (1, 2)
    with pytest.raises(SemanticViolation):
        core.create_phase('p4', 'a', PhaseType.OTHER, ordinal=4)
    InvariantOracle(core).assert_all()


def test_phase_origin_state_requires_completed_phase(core):
    setup_attempt(core)
    core.create_phase('p1', 'a', PhaseType.PREFILL)
    core.set_phase_status('p1', PhaseStatus.RUNNING)
    with pytest.raises(SemanticViolation):
        core.create_state('x', origin_type='phase', origin_id='p1')
    core.complete_phase('p1')
    x = core.create_state('x', origin_type='phase', origin_id='p1')
    assert x.producer_phase_id == 'p1'
    assert x.producer_attempt_id == 'a'


def test_phase_context_must_belong_to_same_attempt(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_request('r1', 'c'); core.start_attempt('a1', 'r1')
    core.create_phase('a1p1', 'a1', PhaseType.PREFILL); core.set_phase_status('a1p1', PhaseStatus.RUNNING)
    core.create_request('r2', 'c'); core.start_attempt('a2', 'r2')
    core.create_phase('a2p1', 'a2', PhaseType.PREFILL); core.set_phase_status('a2p1', PhaseStatus.RUNNING)
    core.create_state('x', origin_type='continuation', origin_id='c')
    bad = ExecutionContext('p', 's', 'c', 'r2', 'a2', 'a1p1')
    good = ExecutionContext('p', 's', 'c', 'r2', 'a2', 'a2p1')
    assert not core.state_compatible('x', bad)
    assert core.state_compatible('x', good)


def test_same_attempt_phase_state_requires_later_consumer_phase(core):
    setup_attempt(core)
    core.create_phase('prefill', 'a', PhaseType.PREFILL); core.set_phase_status('prefill', PhaseStatus.RUNNING); core.complete_phase('prefill')
    core.create_state('kv', origin_type='phase', origin_id='prefill')
    core.create_phase('decode', 'a', PhaseType.DECODE); core.set_phase_status('decode', PhaseStatus.RUNNING)
    same_phase = ExecutionContext('p', 's', 'c', 'r', 'a', 'prefill')
    later_phase = ExecutionContext('p', 's', 'c', 'r', 'a', 'decode')
    no_phase = ExecutionContext('p', 's', 'c', 'r', 'a')
    assert not core.state_compatible('kv', same_phase)
    assert not core.state_compatible('kv', no_phase)
    assert core.state_compatible('kv', later_phase)
    InvariantOracle(core).assert_all()
