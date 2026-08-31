# 12 — C3 Baseline Policies
## Closure Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C3 — Baseline Policies B0–B4  
**Prerequisite:** C2 CLOSED  
**Tracking:** #32  
**Status:** CLOSED

---

# 1. Closure statement

C3 implements the five normalized Experimental Plan baselines through one deterministic simulator-facing policy boundary:

```text
B0  Request-Centric
B1  Cache-Aware
B2  Session-Affinity
B3  State-Aware
B4  Continuity-Aware
```

The governing architectural boundary remains:

> **C1 is semantic authority. C2 is the deterministic policy-neutral time/resource/fault substrate. C3 chooses policy actions only from each baseline's declared information and delegates Continuity semantic judgments to C1 rather than reimplementing them.**

No C3 placement, retention, or migration-eligibility query mutates C1 semantic state.

C3 closed after the final B4 implementation and paired-interface slice was squash-merged as:

```text
PR #42 reviewed head  5d42dcd93842664034698180854ccd5b6877d0a6
C3.5 squash merge     ffa1ebb8a2aec63221252152c65069eef653259c
suite                  327 passed on Python 3.11 / 3.12 / 3.13
```

The exact final PR head received a clean automated re-review with no major issues after all bounded-review findings were resolved.

---

# 2. Information-contract schema

C3.1 introduced machine-readable policy information contracts. C3.5 bounded review found that schema v1 could not faithfully express the canonical B4 definition because it omitted three source-required B4 inputs:

```text
program_id
state_lifecycle
reconciliation
```

C3 therefore closes on:

```text
cadi.policy-information-contract.v2
```

B0–B3 information privileges remain unchanged. The three v2 fields are B4-only additions because B0–B3 use explicit field sets while B4 receives the complete normalized vocabulary.

The v2 fields are appended to the end of `PolicyObservation`; all v1 positional constructor slots retain their original meanings.

The normalized vocabulary now covers:

```text
Program identity
LogicalRequest identity
Attempt identity / authority
Session identity / preferred location
Continuation identity / ancestry
State candidate key
exact StateID
State location
State provenance
State lifecycle
producer Attempt
BindingID / epoch
Evidence authority / status / freshness
Reconciliation
resource / load observations
```

---

# 3. Baseline definitions and provenance

## C3.1 — B0 Request-Centric — CLOSED

B0 uses request identity plus worker availability/load and ranks available workers by deterministic normalized load:

```text
normalized_load = (active_tasks + queued_tasks) / capacity
normalized_load
queued_tasks
active_tasks
worker_id
```

Bounded review fixed the mutable contract registry, non-canonical set-like observation ordering, and the missing B2 `session_preferred_location` field.

```text
implementation-bearing head  b0558644d15fd63fcc9aa200b634310fa6214333
final PR #34 head            9d77db3595982049cb6025443b497b934d87908e
suite                         288 passed on Python 3.11 / 3.12 / 3.13
squash merge                  b17e03867004ed0dc51aa821cbe10e98a888aebb
issue                         #33 CLOSED
```

## C3.2 — B1 Cache-Aware — CLOSED

B1 adds candidate-key-scoped cache/State locality. Candidate-local workers rank before non-local workers, with the exact B0 load key inside each locality class. Missing, unscoped, unknown, or unavailable locality degenerates to B0 ordering.

```text
final PR #36 head  0c4028ca6d9a90260a88412a1c209fdfea924d49
suite              295 passed on Python 3.11 / 3.12 / 3.13
squash merge       d0742284de450b86ae98db4f52ea993e90d5f8e7
issue              #35 CLOSED
```

## C3.3 — B2 Session-Affinity — CLOSED

B2 adds SessionID-scoped preferred previous location. A usable preferred worker moves to the front while every remaining worker preserves exact B1 order. Missing, unscoped, or unavailable affinity degenerates exactly to B1.

```text
final PR #38 head  b75528f7c0bcb71cd82a6cadd2c7e0dad6cfd30a
suite              303 passed on Python 3.11 / 3.12 / 3.13
squash merge       b2a13e3bc372449fc5cd3bc8e5b925f7983d991e
issue              #37 CLOSED
```

## C3.4 — B3 State-Aware — CLOSED

B3 receives exact StateID and precise State location but remains causally blind. Exact-State-local workers form a preferred locality class; if exact locality is absent or unusable, B3 falls back to B1 candidate locality and then load.

Adversarial regression coverage proves that hidden compatible-ancestor provenance and hidden wrong-sibling/superseded provenance cannot change a B3 decision when its visible StateID/location is unchanged.

```text
final PR #40 head  2d69a86fadd2424764415322746f310a7ad1568b
suite              311 passed on Python 3.11 / 3.12 / 3.13
squash merge       1ce103ab0805fa81a4da4c9c4cc708475a891bd6
issue              #39 CLOSED
```

## C3.5 — B4 Continuity-Aware — CLOSED

B4 receives the complete v2 information contract and enforces the Paper 1 Continuity policy surface while preserving C1 as semantic authority.

```text
final behavior-bearing head  50f5653e8a39549bbdac828b8677afea28356db7
final reviewed PR #42 head   5d42dcd93842664034698180854ccd5b6877d0a6
suite                         327 passed on Python 3.11 / 3.12 / 3.13
squash merge                  ffa1ebb8a2aec63221252152c65069eef653259c
issue                         #41 CLOSED by PR #42 merge
```

---

# 4. B4 semantic boundary

## 4.1 Read-only C1 authority

`CoreContinuityAuthority` exposes two read-only judgments over the closed C1 core:

```text
attempt_current(LogicalRequestID, AttemptID)
state_compatible(
    exact StateID,
    ProgramID,
    SessionID,
    ContinuationID,
    LogicalRequestID,
    AttemptID
)
```

`attempt_current` cross-checks the observation against C1's authoritative current-Attempt state. `state_compatible` constructs the public C1 `ExecutionContext` and delegates to `ContinuityCore.state_compatible`.

C3 does not reimplement C1 Attempt, State-lineage, producer-authority, Evidence, Binding, or reconciliation semantics.

## 4.2 Attempt fencing

Attempt fencing precedes physical worker availability. B4 permits placement only when:

```text
LogicalRequestID exists
AttemptID exists
observed Attempt authority == CURRENT
C1 attempt_current(LogicalRequestID, AttemptID) == true
```

Otherwise B4 returns `ATTEMPT_FENCED` with no worker ranking.

This rejects both explicitly superseded Attempts and stale observations that still claim `CURRENT` after C1 has advanced the request to a newer Attempt.

## 4.3 Compatible-State routing

B4 does not promote candidate-only cache locality into verified reusable State.

Exact-State locality is preferred only when:

```text
exact StateID exists
precise State location exists
Reconciliation == MATCHED
C1 state_compatible(...) == true
at least one declared State location is available
```

Compatible local workers rank before remote workers, with the shared B0 load key inside each class.

If reconciliation is not matched, C1 reports incompatibility, or compatible locality is unavailable, B4 fails closed to recomputation/load routing rather than consuming attractive but unverified State.

This is the executable B3↔B4 causal-compatibility distinction required for later correctness evaluation.

## 4.4 Lifecycle-aware retention

B4 exposes deterministic ordinal retention classes over the canonical Paper 1 lifecycle states:

```text
ACTIVE       priority 3  PROTECT
WAITING      priority 2  RETAIN
SPECULATIVE  priority 1  BEST_EFFORT
TERMINAL     priority 0  RELEASE
```

These are policy preference classes, not calibrated cost weights. Performance calibration remains a later milestone.

## 4.5 Safe-migration eligibility

B4 exposes migration eligibility rather than mutating Binding state directly.

A migration-sensitive commit is policy-eligible only when:

```text
BindingID exists
Binding epoch exists
Reconciliation == MATCHED
```

B4 returns `ALLOW_COMMIT` only in that case; otherwise it returns `WAIT`.

Actual migration commit, epoch fencing, Evidence sufficiency, old-binding supersession, and atomic semantic transition remain C1 responsibilities.

---

# 5. Paired-interface closure

`build_baseline_policies(...)` constructs exactly B0–B4.

`decide_paired_placements(...)`:

1. requires exactly the B0–B4 mapping keys;
2. verifies each mapped policy's own `policy_id` matches its key;
3. executes policies in canonical B0→B4 order;
4. supplies every policy the same immutable `PolicyObservation`;
5. independently projects that observation through each policy's declared `PolicyView` contract.

This makes the C3 exit criterion executable: the five baselines run through an identical simulator-facing interface while receiving only information permitted by their abstraction.

---

# 6. C3.5 bounded-review findings resolved

Four substantive findings were resolved before merge:

1. **Incomplete B4 information contract.** Schema v1 omitted `program_id`, `state_lifecycle`, and `reconciliation`; v2 adds exactly those B4 inputs without expanding B0–B3 privileges.
2. **Attempt-fencing weakness and ordering.** The first candidate trusted observed authority and checked worker availability before fencing; B4 now cross-checks C1 `attempt_current`, and fencing precedes availability.
3. **PolicyObservation positional compatibility.** The first v2 draft inserted new fields into existing dataclass slots; v2 fields now append at the tail and regression coverage preserves v1 positional meanings.
4. **Paired-policy registration integrity.** The first paired harness validated only the keys; it now rejects any mapped policy whose `policy_id` does not match its B0–B4 key.

Both original automated review threads were resolved. A fresh automated review explicitly reviewed final head `5d42dcd938` and reported no major issues.

---

# 7. Final validation

The exact final C3.5 reviewed head passed the complete repository suite on every supported runtime:

```text
Python 3.11  PASS — 327 tests
Python 3.12  PASS — 327 tests
Python 3.13  PASS — 327 tests
```

The C3.5 change boundary was limited to:

```text
simulator/policies.py
simulator/continuity_policy.py
simulator/__init__.py
tests/simulator/test_continuity_aware_policy.py
spec/12-c3-baseline-policies-record.md
```

No C4 experiment result, C6 calibrated cost model, or policy-specific C2 scenario fork was introduced.

---

# 8. C3 exit gate — SATISFIED

```text
B0 implemented and closed                           PASS
B1 implemented and closed                           PASS
B2 implemented and closed                           PASS
B3 implemented and closed                           PASS
B4 implemented and closed                           PASS
machine-readable information contracts fixed        PASS
B0–B3 privileges unchanged by v2 repair             PASS
C1 remains semantic authority                       PASS
C2 remains policy-neutral                           PASS
paired B0–B4 placement interface exists             PASS
paired policy identity is validated                 PASS
full suite Python 3.11                              PASS
full suite Python 3.12                              PASS
full suite Python 3.13                              PASS
bounded review findings resolved                    PASS
```

**C3 is CLOSED.**

The next implementation milestone is **C4 — Correctness Evaluation**, which will apply the normalized fault/workload catalogue to B0–B4 and measure the correctness hypotheses without changing the closed C1/C2/C3 semantic and policy boundaries.
