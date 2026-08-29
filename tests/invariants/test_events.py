import pytest

from continuity.entities import Evidence, EvidenceAuthority, EvidenceStatus, SemanticEvent
from continuity.errors import SemanticViolation
from continuity.invariants import InvariantOracle


def test_duplicate_semantic_event_delivery_is_idempotent(core):
    event = SemanticEvent('ev1', 'ATTEMPT_COMPLETED', 'attempt', 'a1', frozenset({('result', 'ok')}))
    first = core.record_event(event)
    second = core.record_event(event)
    assert first == second == event
    assert core.event_order == ['ev1']
    assert len(core.events) == 1
    InvariantOracle(core).assert_all()


def test_conflicting_duplicate_semantic_event_is_rejected(core):
    core.record_event(SemanticEvent('ev1', 'STATE_EVICTED', 'state', 'x'))
    with pytest.raises(SemanticViolation):
        core.record_event(SemanticEvent('ev1', 'STATE_MATERIALIZED', 'state', 'x'))


def test_event_log_preserves_first_delivery_order(core):
    core.record_event(SemanticEvent('ev2', 'SECOND', 'system', 'runtime'))
    core.record_event(SemanticEvent('ev1', 'FIRST', 'system', 'runtime'))
    core.record_event(SemanticEvent('ev2', 'SECOND', 'system', 'runtime'))
    assert core.event_order == ['ev2', 'ev1']


def test_event_identity_cannot_collide_with_logical_entity(core):
    core.create_program('shared-id')
    with pytest.raises(SemanticViolation):
        core.record_event(SemanticEvent('shared-id', 'PROGRAM_CREATED', 'program', 'shared-id'))


def test_duplicate_evidence_delivery_is_idempotent(core):
    evidence = Evidence(
        'e1', 'replica present', 'observer', EvidenceAuthority.EXACT_OBSERVATION,
        EvidenceStatus.VALID, 1.0, frozenset({('state', 'x'), ('replica', 'r')})
    )
    first = core.record_evidence(evidence)
    second = core.record_evidence(evidence)
    assert first == second == evidence
    assert len(core.evidence) == 1


def test_conflicting_duplicate_evidence_is_rejected(core):
    first = Evidence('e1', 'owner w1', 'observer', EvidenceAuthority.EXACT_OBSERVATION, EvidenceStatus.VALID, 1.0)
    second = Evidence('e1', 'owner w2', 'observer', EvidenceAuthority.EXACT_OBSERVATION, EvidenceStatus.VALID, 1.0)
    core.record_evidence(first)
    with pytest.raises(SemanticViolation):
        core.record_evidence(second)
