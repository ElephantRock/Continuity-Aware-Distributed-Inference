# 09 — C2.3 Semantic Adapter and Replay Equivalence
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C2.3 — C1 Semantic Adapter and Replay Equivalence  
**Prerequisite:** C2.2 merged to `main` as `9c2c3f0801bca9abf165bf626970c2e9d8fa7d5e`  
**Status:** CLOSED — PR #16 squash-merged to `main` as `e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6`

---

# 1. Purpose

C2.3 attaches timed simulator delivery to one closed C1 `ContinuityCore` without reimplementing C1 semantics.

The governing rule is:

> **C2 decides when observations and physical events occur; C1 decides what those events are allowed to mean.**

The C2.3 slice is intentionally restricted to request/Attempt retry races and authoritative finalization.

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

# 3. Implemented Event Mapping

The adapter surface maps:

```text
REQUEST_CREATED          -> create_request
ATTEMPT_STARTED          -> start_attempt + set_attempt_execution(RUNNING)
ATTEMPT_TIMEOUT          -> schedule RETRY_STARTED if the timeout is still current
RETRY_STARTED            -> start_attempt + set_attempt_execution(RUNNING)
ATTEMPT_COMPLETED        -> complete_attempt(SUCCEEDED)
LATE_RESULT              -> complete_attempt(SUCCEEDED)
ATTEMPT_FAILED           -> complete_attempt(FAILED)
OBSERVATION_CREATED      -> exact Evidence + terminal Output + finalize_request
OBSERVATION_DUPLICATED   -> exact Evidence/Output identity reuse + finalize_request
```

Timeouts are not themselves allowed to rewrite Attempt execution outcome. Starting the retry is the C1 operation that supersedes the prior current Attempt, preserving the possibility that the old physical Attempt may later succeed while remaining `SUPERSEDED`.

Timeout-generated retry events use parent-derived EventIDs so retry delivery remains deterministic without making semantic outcome depend on host/Python scheduling-call order.

---

# 4. Semantic Action Record

Every adapter interaction records:

```text
sim_time
event_id
event_kind
operation or adapter decision
outcome = APPLIED | IDEMPOTENT | REJECTED | IGNORED
result_id when applicable
error type/message for rejection
post-operation C1 snapshot fingerprint
```

Adapter bookkeeping is outside `ContinuityCore` and therefore does not become semantic authority.

Rejected C1 operations are checked for semantic atomicity by comparing the pre/post canonical C1 fingerprint. A rejected semantic operation that mutates C1 state is treated as an adapter correctness failure.

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

The executable cross-layer trace is:

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

The key executable property is that physical success and semantic authority remain separate: `SUCCEEDED + SUPERSEDED` is valid for A1, but A1 can never regain authority or finalize R1.

---

# 7. Schedule and Identity Variants

The validation corpus covers:

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
retry setup-call order invariance for different simulated delivery times
late superseded-A1 terminal observation
malformed adapter event input
fingerprint-stable rejected finalization/semantic operations
```

Correctness-equivalent variants preserve the same authoritative winner.

---

# 8. Bounded Semantic-Boundary Review

The closure review found three substantive defects and fixed them before merge.

## 8.1 Exact immutable Evidence identity

Initial duplicate handling recognized Evidence using selected fields while C1 Evidence identity also contains `observed_at`.

That was insufficient for reordered delivery: a duplicate arriving before the nominal original could otherwise materialize a physically different immutable Evidence object under the same EvidenceID.

The adapter now carries the original observation timestamp explicitly and requires exact C1 `Evidence` equality before treating an existing EvidenceID as idempotent.

Conflicting Evidence identity is rejected before Output creation/finalization.

## 8.2 Observation-time causality

C1 intentionally does not own physical timestamps. Therefore C2 must prevent a physically impossible observation record from being injected into otherwise semantically valid C1 state.

The adapter now records the simulated time at which it first delivers Attempt success and rejects a terminal observation whose `observed_at` predates that delivered success.

This is a C2 physical-causality fence, not a new C1 semantic rule.

## 8.3 Host-order-independent retry scheduling

An intermediate retry-dedup implementation reserved retry identity when Python code scheduled a future event. That allowed future host-side setup to suppress an earlier simulated timeout.

The final implementation removes that host-time reservation.

Timeout-generated retry events derive their EventID from both retry identity and parent timeout EventID, while later redundant retry delivery is fenced by current-Attempt identity and delivered simulator history.

Regression tests verify that changing host setup-call order for events with different simulated times does not change the authoritative outcome.

---

# 9. Closure Criterion and Result

C2.3 required:

```text
all pre-existing C1/C2 tests green
all new adapter tests green on Python 3.11–3.13
canonical C1 vs timed C2 retry race has equal authoritative outcome
late superseded Attempt success cannot regain authority
duplicate/stale delivery is fail-closed, ignored, or idempotent
rejected semantic calls are fingerprint-stable
exact immutable Evidence identity across reordered delivery
observation time cannot predate adapter-delivered Attempt success
host setup order cannot alter different-time retry outcome
bounded semantic-boundary review has no unresolved blocker
```

All conditions passed.

Final user-authored closure head:

```text
c0125d84b20f5d58553ce241fd254169534783e4
```

Permanent GitHub Actions CI on that exact head:

```text
Python 3.11  PASS
Python 3.12  PASS
Python 3.13  PASS
203 passed
```

PR #16 was squash-merged on 2026-08-30 as:

```text
e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6
```

Issue #9 is closed as completed.

**C2.3 is CLOSED.**

---

# 10. Explicit Non-Scope / Next Boundary

C2.3 did not adapt:

```text
ReusableState production/consumption
StateReplica Evidence reconciliation
Binding migration commit
Continuation fork/join/tool waits
resource events into semantic Evidence
probabilistic fault injection
routing/baseline policy decisions
```

These surfaces must not be silently folded back into C2.3 after closure.

The immediate next slice is **C2.4 — deterministic and probabilistic fault injection**, tracked by issue #10. Its contract is to add explicit fault metadata, deterministic injectors for required failure classes, seeded probabilistic injectors, and delay/drop/duplicate/reorder delivery support with validated fault ground truth before campaign use.

C2.4 was not started during the C2.3 closure/bookkeeping checkpoint.
