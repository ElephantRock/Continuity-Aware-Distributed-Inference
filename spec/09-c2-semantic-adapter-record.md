# 09 — C2.3 Semantic Adapter and Replay Equivalence
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C2.3 — C1 Semantic Adapter and Replay Equivalence  
**Prerequisite:** C2.2 merged to `main` as `9c2c3f0801bca9abf165bf626970c2e9d8fa7d5e`  
**Status:** IMPLEMENTATION CANDIDATE

---

# 1. Purpose

C2.3 attaches timed simulator delivery to one closed C1 `ContinuityCore` without reimplementing C1 semantics.

The governing rule is:

> **C2 decides when observations and physical events occur; C1 decides what those events are allowed to mean.**

The first C2.3 slice is intentionally restricted to request/Attempt retry races and authoritative finalization.

---

# 2. Semantic Boundary

`ContinuityAdapter` may inspect C1 state but may not directly assign into semantic entity stores.

Every semantic state change is performed through a public `ContinuityCore` transition.

After every successful or rejected semantic operation:

1. the independent `InvariantOracle` is executed;
2. a canonical C1 snapshot fingerprint is recorded;
3. rejected operations are required to leave the fingerprint unchanged.

This makes fail-closed behavior executable at the C1/C2 boundary.

---

# 3. Initial Event Mapping

The initial adapter surface maps:

```text
REQUEST_CREATED          -> create_request
ATTEMPT_STARTED          -> start_attempt + set_attempt_execution(RUNNING)
ATTEMPT_TIMEOUT          -> schedule RETRY_STARTED if the timeout is still current
RETRY_STARTED            -> start_attempt + set_attempt_execution(RUNNING)
ATTEMPT_COMPLETED        -> complete_attempt(SUCCEEDED)
LATE_RESULT              -> complete_attempt(SUCCEEDED)
ATTEMPT_FAILED           -> complete_attempt(FAILED)
OBSERVATION_CREATED      -> exact Evidence + terminal Output + finalize_request
OBSERVATION_DUPLICATED   -> idempotent Evidence/Output reuse + finalize_request
```

Timeouts are not themselves allowed to rewrite Attempt execution outcome. Starting the retry is the C1 operation that supersedes the prior current Attempt, preserving the possibility that the old physical Attempt may later succeed while remaining `SUPERSEDED`.

---

# 4. Semantic Action Record

Every adapter interaction records:

```text
sim_time
event_id
event_kind
C1 operation
outcome = APPLIED | IDEMPOTENT | REJECTED | IGNORED
result_id when applicable
error type/message for rejection
post-operation C1 snapshot fingerprint
```

Adapter bookkeeping is outside `ContinuityCore` and therefore does not become semantic authority.

---

# 5. Authoritative Equivalence Oracle

Cross-layer comparison uses an explicit `AuthoritativeOutcome` projection rather than comparing simulator event history.

The projection includes:

```text
Request status
CurrentAttempt
CommittedAttempt
authoritative Output
authoritative Output Evidence IDs
per-Request Attempt generation, execution status, and authority status
```

`assert_authoritative_equivalent(reference, candidate, request_id)` runs the independent invariant oracle on both cores and requires these authoritative projections to be equal.

Canonical snapshot fingerprints remain recorded for audit/replay evidence, but timing-dependent observational metadata is not incorrectly promoted into the authoritative-equivalence relation.

---

# 6. Canonical Retry Race

The first executable cross-layer trace is:

```text
R1 created
A1 starts and is CURRENT
A1 timeout delivered
A2 retry starts -> A1 SUPERSEDED, A2 CURRENT
A1 succeeds physically late
A2 succeeds
exact A2 terminal observation arrives
A2 finalizes R1 and becomes COMMITTED
optional later A1 observation is rejected as authoritative
```

Expected terminal authority:

```text
Request R1        COMPLETED
CurrentAttempt    none
CommittedAttempt  A2
A1 authority      SUPERSEDED
A1 execution      SUCCEEDED allowed
A2 authority      COMMITTED
A2 execution      SUCCEEDED
```

---

# 7. Schedule Variants

The initial validation corpus covers:

```text
late A1 success before A2 success
late A1 success after A2 success but before finalization
late A1 success after finalization
duplicate late A1 completion
delayed timeout after A1 physically succeeded but before authoritative observation
duplicate timeout delivery
stale timeout after finalization
duplicate authoritative observation with exact original Evidence timestamp
reordered duplicate-before-original observation delivery
conflicting Evidence identity rejection
terminal observation timestamp fenced after adapter-delivered Attempt success
simultaneous timeout/completion in both insertion orders
preplanned retry vs earlier timeout convergence without host-time reservation
late superseded-A1 terminal observation
malformed adapter event input
```

Correctness-equivalent variants must preserve the same authoritative winner.

---

# 8. Explicit Non-Scope

This first C2.3 slice does not yet adapt:

```text
ReusableState production/consumption
StateReplica Evidence reconciliation
Binding migration commit
Continuation fork/join/tool waits
resource events into semantic Evidence
probabilistic fault injection
routing/baseline policy decisions
```

Those surfaces are added only after the retry/finalization adapter path closes cleanly.

---

# 9. Closure Criterion

C2.3 retry/finalization adaptation may close only when:

```text
all pre-existing C1/C2 tests remain green
all new adapter tests are green on Python 3.11–3.13
canonical C1 vs timed C2 retry race has equal authoritative outcome
late superseded Attempt success cannot regain authority
duplicate/stale delivery is fail-closed or idempotent
rejected semantic calls are fingerprint-stable
bounded exact-delta review finds no unresolved semantic boundary defect
```

C2.4 fault injection must not begin until these conditions are met.
