import pytest
from continuity import ContinuityCore
from continuity.entities import Evidence, EvidenceAuthority, EvidenceStatus

@pytest.fixture
def core():
    return ContinuityCore()

@pytest.fixture
def exact_evidence():
    def make(core, id_, scope, t=10.0, authority=EvidenceAuthority.EXACT_OBSERVATION):
        e=Evidence(id_, "ok", "test", authority, EvidenceStatus.VALID, t, frozenset(scope))
        core.record_evidence(e)
        return e
    return make
