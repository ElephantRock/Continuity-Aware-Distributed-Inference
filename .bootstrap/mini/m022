import pytest
from continuity.entities import BindingStatus, Evidence, EvidenceAuthority, EvidenceStatus, ReconcileOutcome
from continuity.errors import SemanticViolation, InsufficientEvidence
from continuity.invariants import InvariantOracle

def ev(core,id_,scope,authority=EvidenceAuthority.EXACT_OBSERVATION,status=EvidenceStatus.VALID,t=10):
    e=Evidence(id_,'ok','test',authority,status,t,frozenset(scope)); core.record_evidence(e); return e

def test_concurrent_migration_candidate_fencing(core):
    b1=core.activate_initial_binding('b1','subject','w1')
    b2=core.propose_binding('b2','subject','w2'); core.begin_migration('b2')
    b3=core.propose_binding('b3','subject','w3'); core.begin_migration('b3')
    assert b2.base_epoch == b3.base_epoch == b1.epoch
    assert b2.epoch != b3.epoch
    ev(core,'e2',{('binding','b2'),('epoch',str(b2.epoch))})
    core.commit_migration('b2',['e2'],now=10)
    ev(core,'e3',{('binding','b3'),('epoch',str(b3.epoch))})
    with pytest.raises(SemanticViolation): core.commit_migration('b3',['e3'],now=10)
    assert core.current_binding_by_subject['subject']=='b2'
    InvariantOracle(core).assert_all()

def test_ambiguous_evidence_fails_closed(core):
    e=Evidence('e','ambiguous','test',EvidenceAuthority.AUTHORITATIVE,EvidenceStatus.AMBIGUOUS,10,frozenset({('attempt','a')}))
    core.record_evidence(e)
    assert core.reconcile('finalize',['e'],now=10,required_scope={('attempt','a')}) is ReconcileOutcome.AMBIGUOUS

def test_estimated_evidence_insufficient_for_finalize(core):
    ev(core,'e',{('attempt','a')},authority=EvidenceAuthority.ESTIMATED)
    with pytest.raises(InsufficientEvidence): core.require_evidence('finalize',['e'],now=10,required_scope={('attempt','a')})
