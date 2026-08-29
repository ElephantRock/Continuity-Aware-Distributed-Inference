from itertools import permutations

import pytest

from continuity.entities import (
    Evidence, EvidenceAuthority, EvidenceStatus, ExecutionContext, ReconcileOutcome,
    SemanticEvent,
)
from continuity.errors import SemanticViolation
from continuity.invariants import InvariantOracle


def exact(core, id_, scope, *, status=EvidenceStatus.VALID, t=10):
    evidence = Evidence(
        id_, 'ok', 'adversarial', EvidenceAuthority.EXACT_OBSERVATION,
        status, t, frozenset(scope),
    )
    core.record_evidence(evidence)
    return evidence


def committed_retry_core(core):
    core.create_program('p'); core.create_session('s', 'p'); core.create_continuation('c', 's')
    core.create_request('r', 'c'); core.start_attempt('a1', 'r'); core.start_attempt('a2', 'r')
    core.complete_attempt('a2'); exact(core, 'e2', {('attempt', 'a2')})
    core.create_output('o2', 'a2', True, ['e2']); core.finalize_request('r', 'o2', now=10)
    core.complete_attempt('a1')
    return core


def test_retry_event_permutations_cannot_change_committed_authority(core):
    committed_retry_core(core)
    old = SemanticEvent('old', 'ATTEMPT_COMPLETED', 'attempt', 'a1')
    new = SemanticEvent('new', 'ATTEMPT_COMMITTED', 'attempt', 'a2')
    sequences = set(permutations((old, new, old)))
    for sequence in sequences:
        candidate = committed_retry_core(type(core)())
        for event in sequence:
            candidate.record_event(event)
            assert candidate.requests['r'].committed_attempt_id == 'a2'
            assert candidate.requests['r'].authoritative_output_id == 'o2'
            InvariantOracle(candidate).assert_all()
        assert set(candidate.events) == {'old', 'new'}


@pytest.mark.parametrize('winner,loser', [('b2', 'b3'), ('b3', 'b2')])
def test_competing_migration_candidate_winner_permutations_fence_loser(core, winner, loser):
    core.activate_initial_binding('b1', 'subject', 'w1')
    b2 = core.propose_binding('b2', 'subject', 'w2'); core.begin_migration('b2')
    b3 = core.propose_binding('b3', 'subject', 'w3'); core.begin_migration('b3')
    exact(core, 'e2', {('binding', 'b2'), ('epoch', str(b2.epoch))})
    exact(core, 'e3', {('binding', 'b3'), ('epoch', str(b3.epoch))})
    core.commit_migration(winner, [f'e{winner[-1]}'], now=10)
    with pytest.raises(SemanticViolation):
        core.commit_migration(loser, [f'e{loser[-1]}'], now=10)
    assert core.current_binding_by_subject['subject'] == winner
    InvariantOracle(core).assert_all()


@pytest.mark.parametrize('authority', list(EvidenceAuthority))
@pytest.mark.parametrize('status', list(EvidenceStatus))
def test_evidence_authority_status_cross_product_fails_closed(core, authority, status):
    scope = frozenset({('attempt', 'a')})
    if authority is EvidenceAuthority.DERIVED:
        support = Evidence(
            'support', 'support', 'observer', EvidenceAuthority.EXACT_OBSERVATION,
            EvidenceStatus.VALID, 1.0, scope,
        )
        core.record_evidence(support)
        evidence = Evidence(
            'candidate', 'derived', 'rule', authority, status, 2.0, scope,
            derived_from=frozenset({'support'}), derivation_rule='identity',
        )
        if status is EvidenceStatus.VALID:
            core.record_evidence(evidence)
        else:
            # Non-VALID derived evidence may be based on valid support; it remains unusable.
            core.record_evidence(evidence)
    else:
        evidence = Evidence('candidate', 'claim', 'observer', authority, status, 2.0, scope)
        core.record_evidence(evidence)

    outcome = core.reconcile('finalize', ['candidate'], now=2, required_scope=set(scope))
    if status is EvidenceStatus.AMBIGUOUS:
        assert outcome is ReconcileOutcome.AMBIGUOUS
    elif status is not EvidenceStatus.VALID:
        assert outcome is ReconcileOutcome.WAIT
    elif authority in {EvidenceAuthority.EXACT_OBSERVATION, EvidenceAuthority.AUTHORITATIVE}:
        assert outcome is ReconcileOutcome.MATCHED
    else:
        assert outcome is ReconcileOutcome.WAIT
    InvariantOracle(core).assert_all()


def branch_matrix_core(core):
    core.create_program('p'); core.create_session('s', 'p')
    core.create_continuation('c0', 's')
    core.create_continuation('c1', 's', ['c0'])
    core.create_continuation('c2', 's', ['c0'])
    for continuation in ('c0', 'c1', 'c2'):
        core.create_state(f'x-{continuation}', origin_type='continuation', origin_id=continuation)
        core.create_request(f'r-{continuation}', continuation)
        core.start_attempt(f'a-{continuation}', f'r-{continuation}')
    return core


@pytest.mark.parametrize(
    'origin,consumer,expected',
    [
        ('c0', 'c0', True), ('c0', 'c1', True), ('c0', 'c2', True),
        ('c1', 'c0', False), ('c1', 'c1', True), ('c1', 'c2', False),
        ('c2', 'c0', False), ('c2', 'c1', False), ('c2', 'c2', True),
    ],
)
def test_branch_ancestry_compatibility_matrix(core, origin, consumer, expected):
    branch_matrix_core(core)
    ctx = ExecutionContext('p', 's', consumer, f'r-{consumer}', f'a-{consumer}')
    assert core.state_compatible(f'x-{origin}', ctx) is expected
    InvariantOracle(core).assert_all()


def test_delayed_evidence_arrival_changes_readiness_not_semantic_history(core):
    required = {('attempt', 'a')}
    assert core.reconcile('finalize', ['missing'], now=1, required_scope=required) is ReconcileOutcome.WAIT
    exact(core, 'exact', required, t=2)
    assert core.reconcile('finalize', ['exact'], now=2, required_scope=required) is ReconcileOutcome.MATCHED
    stale_newer = Evidence(
        'newer-but-stale', 'stale', 'observer', EvidenceAuthority.AUTHORITATIVE,
        EvidenceStatus.STALE, 3.0, frozenset(required),
    )
    core.record_evidence(stale_newer)
    assert core.reconcile('finalize', ['newer-but-stale'], now=3, required_scope=required) is ReconcileOutcome.WAIT
    assert core.reconcile('finalize', ['exact'], now=3, required_scope=required) is ReconcileOutcome.MATCHED
    InvariantOracle(core).assert_all()
