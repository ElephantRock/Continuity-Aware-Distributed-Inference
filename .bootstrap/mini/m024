import random
from continuity import ContinuityCore
from continuity.entities import ContinuationLifecycle, Evidence, EvidenceAuthority, EvidenceStatus, ExecutionStatus
from continuity.invariants import InvariantOracle
from continuity.errors import ContinuityError

def test_seeded_sequence_fuzz_keeps_invariants():
    for seed in range(50):
        rnd=random.Random(seed); c=ContinuityCore(); oracle=InvariantOracle(c)
        c.create_program('p'); c.create_session('s','p'); c.create_continuation('c0','s')
        reqs=[]; attempts=[]
        for step in range(100):
            try:
                op=rnd.randrange(6)
                if op==0:
                    cid=f'c{len(c.continuations)}'; parent=rnd.choice(list(c.continuations)); c.create_continuation(cid,'s',[parent])
                elif op==1:
                    rid=f'r{len(reqs)}'; cid=rnd.choice(list(c.continuations)); c.create_request(rid,cid); reqs.append(rid)
                elif op==2 and reqs:
                    rid=rnd.choice(reqs)
                    if c.requests[rid].status.name not in {'COMPLETED','FAILED','CANCELLED'}:
                        aid=f'a{len(attempts)}'; c.start_attempt(aid,rid); attempts.append(aid)
                elif op==3 and attempts:
                    aid=rnd.choice(attempts); c.complete_attempt(aid,True)
                elif op==4 and c.continuations:
                    cid=rnd.choice(list(c.continuations)); cur=c.continuations[cid].lifecycle
                    if cur not in {ContinuationLifecycle.TERMINAL,ContinuationLifecycle.ABANDONED}:
                        c.set_continuation_lifecycle(cid,rnd.choice([ContinuationLifecycle.ACTIVE,ContinuationLifecycle.WAITING,ContinuationLifecycle.SPECULATIVE,ContinuationLifecycle.TERMINAL]))
                elif op==5 and c.continuations:
                    sid=f'x{len(c.states)}'; cid=rnd.choice(list(c.continuations)); c.create_state(sid,origin_type='continuation',origin_id=cid)
            except (ContinuityError, KeyError):
                pass
            oracle.assert_all()
