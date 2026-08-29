import pytest

from continuity.entities import (
    AttemptAuthority, BindingStatus, ContinuationLifecycle, Evidence, EvidenceAuthority,
    EvidenceStatus, ExecutionContext, ReconcileOutcome, ReplicaStatus, SemanticEvent,
    StateValidity,
)
from continuity.errors import InsufficientEvidence, InvalidTransition, SemanticViolation
from continuity.invariants import InvariantOracle


def exact(core, id_, scope, *, authority=EvidenceAuthority.EXACT_OBSERVATION,
          status=EvidenceStatus.VALID, observed_at=10, valid_until=None):
    evidence = Evidence(
        id_, 'ok', 'failure-trace', authority, status, observed_at,
        frozenset(scope), valid_until=valid_until,
    )
    core.record_evidence(evidence)
    return evidence


def simple_request(core):
    core.create_program('p')
    core.create_session('s', 'p')
    core.create_continuation('c', 's')
    core.create_request('r', 'c')


def finalize_attempt(core, attempt_id, output_id, evidence_id):
    core.complete_attempt(attempt_id)
    exact(core, evidence_id, {('attempt', attempt_id)})
    core.create_output(output_id, attempt_id, True, [evidence_id])
    return core.finalize_request('r', output_id, now=10)


def test_ftr1_late_stale_completion_cannot_regain_authority(core):
    simple_request(core)
    core.start_attempt('a1', 'r')
    core.start_attempt('a2', 'r')
    finalize_attempt(core, 'a2', 'o2', 'e2')
    core.complete_attempt('a1')
    assert core.attempts['a1'].authority_status is AttemptAuthority.SUPERSEDED
    assert core.requests['r'].committed_attempt_id == 'a2'
    InvariantOracle(core).assert_all()


def test_ftr2_duplicate_result_finalizes_once(core):
    simple_request(core)
    core.start_attempt('a1', 'r')
    first = finalize_attempt(core, 'a1', 'o1', 'e1')
    second = core.finalize_request('r', 'o1', now=10)
    assert first == second
    assert core.requests['r'].authoritative_output_id == 'o1'
    assert core.attempts['a1'].authority_status is AttemptAuthority.COMMITTED
    InvariantOracle(core).assert_all()


def test_ftr3_reordered_old_retry_event_cannot_override_committed_attempt(core):
    simple_request(core)
    core.start_attempt('a1', 'r')
    core.start_attempt('a2', 'r')
    finalize_attempt(core, 'a2', 'o2', 'e2')
    core.record_event(SemanticEvent('ev-a2', 'ATTEMPT_COMMITTED', 'attempt', 'a2'))
    core.complete_attempt('a1')
    core.record_event(SemanticEvent('ev-a1-late', 'ATTEMPT_COMPLETED', 'attempt', 'a1'))
    exact(core, 'e1', {('attempt', 'a1')})
    core.create_output('o1', 'a1', True, ['e1'])
    with pytest.raises(InvalidTransition):
        core.finalize_request('r', 'o1', now=10)
    assert core.event_order == ['ev-a2', 'ev-a1-late']
    assert core.requests['r'].committed_attempt_id == 'a2'


def test_ftr4_wrong_sibling_state_is_rejected(core):
    core.create_program('p'); core.create_session('s', 'p')
    core.create_continuation('c0', 's')
    core.create_continuation('c1', 's', ['c0'])
    core.create_continuation('c2', 's', ['c0'])
    core.create_state('x1', origin_type='continuation', origin_id='c1')
    core.create_request('r2', 'c2'); core.start_attempt('a2', 'r2')
    ctx = ExecutionContext('p', 's', 'c2', 'r2', 'a2')
    assert not core.state_compatible('x1', ctx)
    InvariantOracle(core).assert_all()


def test_ftr5_total_physical_state_loss_preserves_logical_provenance(core):
    simple_request(core)
    core.start_attempt('a1', 'r')
    core.create_state('x', origin_type='continuation', origin_id='c')
    core.add_replica('rp', 'x', 'w1')
    core.set_replica_status('rp', ReplicaStatus.LOST)
    ctx = ExecutionContext('p', 's', 'c', 'r', 'a1')
    assert core.states['x'].validity is StateValidity.VALID
    assert core.states['x'].origin_continuation_id == 'c'
    assert not core.can_consume_state('x', 'rp', ctx, [], now=10)
    InvariantOracle(core).assert_all()


def test_ftr6_partial_migration_does_not_commit_destination(core):
    core.activate_initial_binding('b1', 'subject', 'w1')
    candidate = core.propose_binding('b2', 'subject', 'w2')
    core.begin_migration('b2')
    with pytest.raises(InsufficientEvidence):
        core.commit_migration('b2', [], now=10)
    assert core.current_binding_by_subject['subject'] == 'b1'
    assert core.bindings['b1'].status is BindingStatus.ACTIVE
    assert core.bindings['b2'].status is BindingStatus.MIGRATING
    assert core.current_epoch_by_subject['subject'] == candidate.base_epoch
    InvariantOracle(core).assert_all()


def test_ftr7_destination_failure_before_commit_leaves_source_authoritative(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_state('x', origin_type='continuation', origin_id='c')
    core.add_replica('src', 'x', 'w1')
    core.add_replica('dst', 'x', 'w2', status=ReplicaStatus.MATERIALIZING)
    core.activate_initial_binding('b1', 'state:x', 'w1')
    core.propose_binding('b2', 'state:x', 'w2'); core.begin_migration('b2')
    core.set_replica_status('dst', ReplicaStatus.LOST)
    with pytest.raises(InsufficientEvidence):
        core.commit_migration('b2', [], now=10)
    assert core.current_binding_by_subject['state:x'] == 'b1'
    assert core.replicas['dst'].status is ReplicaStatus.LOST


def test_ftr8_late_old_binding_observation_cannot_restore_old_owner(core):
    old = core.activate_initial_binding('b1', 'subject', 'w1')
    new = core.propose_binding('b2', 'subject', 'w2'); core.begin_migration('b2')
    exact(core, 'e2', {('binding', 'b2'), ('epoch', str(new.epoch))})
    core.commit_migration('b2', ['e2'], now=10)
    core.record_event(SemanticEvent(
        'late-old-owner', 'BINDING_ACTIVE_OBSERVED', 'binding', 'b1',
        frozenset({('epoch', str(old.epoch)), ('location', 'w1')})
    ))
    assert core.current_binding_by_subject['subject'] == 'b2'
    assert core.bindings['b1'].status is BindingStatus.SUPERSEDED
    assert core.current_epoch_by_subject['subject'] == new.epoch
    InvariantOracle(core).assert_all()


def test_ftr9_ambiguous_ownership_fails_closed(core):
    core.activate_initial_binding('b1', 'subject', 'w1')
    candidate = core.propose_binding('b2', 'subject', 'w2')
    core.begin_migration('b2')
    exact(
        core, 'ambiguous', {('binding', 'b2'), ('epoch', str(candidate.epoch))},
        authority=EvidenceAuthority.AUTHORITATIVE,
        status=EvidenceStatus.AMBIGUOUS,
    )
    outcome = core.reconcile(
        'commit_migration', ['ambiguous'], now=10,
        required_scope={('binding', 'b2'), ('epoch', str(candidate.epoch))},
    )
    assert outcome is ReconcileOutcome.AMBIGUOUS
    assert core.current_binding_by_subject['subject'] == 'b1'


def test_ftr10_stale_high_authority_evidence_is_insufficient(core):
    exact(
        core, 'stale-authority', {('attempt', 'a')},
        authority=EvidenceAuthority.AUTHORITATIVE,
        observed_at=1,
        valid_until=5,
    )
    with pytest.raises(InsufficientEvidence):
        core.require_evidence(
            'finalize', ['stale-authority'], now=10,
            required_scope={('attempt', 'a')},
        )


def test_ftr11_tool_wait_eviction_preserves_lineage_but_forces_cold_resume(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c1', 's')
    core.create_state('x', origin_type='continuation', origin_id='c1')
    core.add_replica('rp', 'x', 'w1')
    core.set_continuation_lifecycle('c1', ContinuationLifecycle.WAITING)
    core.set_replica_status('rp', ReplicaStatus.LOST)
    core.resume_after_wait('c1', 'c2')
    core.create_request('r2', 'c2'); core.start_attempt('a2', 'r2')
    ctx = ExecutionContext('p', 's', 'c2', 'r2', 'a2')
    assert core.state_compatible('x', ctx)
    assert not core.can_consume_state('x', 'rp', ctx, [], now=10)
    assert core.states['x'].origin_continuation_id == 'c1'
    InvariantOracle(core).assert_all()


def test_ftr12_abandoned_branch_residual_state_cannot_reactivate_or_cross_branch(core):
    core.create_program('p'); core.create_session('s', 'p')
    core.create_continuation('c0', 's')
    core.create_continuation('c1', 's', ['c0'])
    core.create_continuation('c2', 's', ['c0'])
    core.create_state('x1', origin_type='continuation', origin_id='c1')
    core.add_replica('rp1', 'x1', 'w1')
    core.set_continuation_lifecycle('c1', ContinuationLifecycle.ABANDONED)
    core.create_request('r2', 'c2'); core.start_attempt('a2', 'r2')
    ctx = ExecutionContext('p', 's', 'c2', 'r2', 'a2')
    assert core.replicas['rp1'].status is ReplicaStatus.VALID
    assert core.continuations['c1'].lifecycle is ContinuationLifecycle.ABANDONED
    assert not core.state_compatible('x1', ctx)
    with pytest.raises(InvalidTransition):
        core.set_continuation_lifecycle('c1', ContinuationLifecycle.ACTIVE)
    InvariantOracle(core).assert_all()
