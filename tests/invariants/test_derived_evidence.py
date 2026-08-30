import pytest

from continuity.entities import Evidence, EvidenceAuthority, EvidenceStatus
from continuity.errors import SemanticViolation
from continuity.invariants import InvariantOracle


def support(id_, *, status=EvidenceStatus.VALID, scope=frozenset({('state', 'x')})):
    return Evidence(id_, 'support', 'observer', EvidenceAuthority.EXACT_OBSERVATION, status, 1.0, scope)


def test_derived_evidence_requires_support_ids_and_rule(core):
    with pytest.raises(SemanticViolation):
        core.record_evidence(Evidence('d', 'derived', 'rule-engine', EvidenceAuthority.DERIVED, EvidenceStatus.VALID, 2.0))


def test_derived_evidence_requires_known_support(core):
    derived = Evidence(
        'd', 'derived', 'rule-engine', EvidenceAuthority.DERIVED, EvidenceStatus.VALID, 2.0,
        frozenset({('state', 'x')}), derived_from=frozenset({'missing'}), derivation_rule='copy-state-presence'
    )
    with pytest.raises(SemanticViolation):
        core.record_evidence(derived)


def test_valid_derived_evidence_records_explicit_provenance(core):
    core.record_evidence(support('s1'))
    derived = Evidence(
        'd1', 'derived', 'rule-engine', EvidenceAuthority.DERIVED, EvidenceStatus.VALID, 2.0,
        frozenset({('state', 'x')}), derived_from=frozenset({'s1'}), derivation_rule='copy-state-presence'
    )
    assert core.record_evidence(derived) == derived
    assert core.evidence['d1'].derived_from == frozenset({'s1'})
    InvariantOracle(core).assert_all()


def test_derived_provenance_cannot_silently_promote_to_authoritative(core):
    core.record_evidence(support('s1'))
    promoted = Evidence(
        'bad', 'promoted', 'rule-engine', EvidenceAuthority.AUTHORITATIVE, EvidenceStatus.VALID, 2.0,
        frozenset({('state', 'x')}), derived_from=frozenset({'s1'}), derivation_rule='vote'
    )
    with pytest.raises(SemanticViolation):
        core.record_evidence(promoted)


def test_derived_evidence_scope_cannot_exceed_support_scope(core):
    core.record_evidence(support('s1'))
    derived = Evidence(
        'bad', 'derived', 'rule-engine', EvidenceAuthority.DERIVED, EvidenceStatus.VALID, 2.0,
        frozenset({('state', 'x'), ('binding', 'b')}), derived_from=frozenset({'s1'}), derivation_rule='expand'
    )
    with pytest.raises(SemanticViolation):
        core.record_evidence(derived)


def test_valid_derived_evidence_cannot_depend_on_nonvalid_support(core):
    core.record_evidence(support('s1', status=EvidenceStatus.STALE))
    derived = Evidence(
        'bad', 'derived', 'rule-engine', EvidenceAuthority.DERIVED, EvidenceStatus.VALID, 2.0,
        frozenset({('state', 'x')}), derived_from=frozenset({'s1'}), derivation_rule='copy'
    )
    with pytest.raises(SemanticViolation):
        core.record_evidence(derived)
