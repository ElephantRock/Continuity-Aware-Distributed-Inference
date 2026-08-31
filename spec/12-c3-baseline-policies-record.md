# 12 — C3 Baseline Policies
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C3 — Baseline Policies B0–B4  
**Prerequisite:** C2 CLOSED  
**Tracking:** #32  
**Status:** IN PROGRESS — C3.1–C3.4 CLOSED; C3.5 implementation/review candidate

---

# 1. Architectural boundary

C3 implements the five normalized Experimental Plan baselines through one policy-neutral simulator-facing placement interface:

```text
B0  Request-Centric
B1  Cache-Aware
B2  Session-Affinity
B3  State-Aware
B4  Continuity-Aware
```

The governing boundary remains:

> **C1 is semantic authority. C2 is the deterministic policy-neutral time/resource/fault substrate. C3 chooses policy actions only from each baseline's declared information and may delegate Continuity semantic judgments to C1 rather than reimplement them.**

No C3 policy mutates C1 during placement evaluation.

---

# 2. Information-contract schema

C3.1 introduced the machine-readable policy information contract. Before C3.5 implementation, bounded review found that schema v1 could not faithfully express the canonical B4 definition.

The Experimental Plan explicitly states that B4 receives/enforces:

```text
Program identity
Session identity
Continuation identity
LogicalRequest identity
Attempt identity
State lineage
Binding epoch
Evidence authority/status/freshness
Reconciliation
```

and implements:

```text
attempt fencing
compatible-state routing
lifecycle-aware retention
safe migration
```

Schema v1 omitted three B4 inputs required by that definition:

```text
program_id
state_lifecycle
reconciliation
```

C3.5 therefore bumps the schema to:

```text
cadi.policy-information-contract.v2
```

and adds exactly those fields.

B0–B3 privileges are unchanged. The new fields are B4-only because B0–B3 use explicit field sets while B4 receives the complete normalized vocabulary.

The v2 fields are appended to the end of `PolicyObservation`. Existing v1 positional constructor slots therefore retain their original meanings.

---

# 3. Closed baseline slices

## C3.1 — B0 Request-Centric — CLOSED

B0 ranks available workers by:

```text
normalized_load = (active_tasks + queued_tasks) / capacity
normalized_load
queued_tasks
active_tasks
worker_id
```

Bounded review fixed:

1. mutable contract registry;
2. non-canonical set-like observation ordering;
3. missing B2 `session_preferred_location`.

Provenance:

```text
implementation-bearing head  b0558644d15fd63fcc9aa200b634310fa6214333
final PR #34 head            9d77db3595982049cb6025443b497b934d87908e
suite                         288 passed on Python 3.11 / 3.12 / 3.13
squash merge                  b17e03867004ed0dc51aa821cbe10e98a888aebb
issue                         #33 CLOSED
```

## C3.2 — B1 Cache-Aware — CLOSED

B1 adds candidate-key-scoped State/cache locality. Candidate-local workers rank before non-local workers; the B0 load key orders workers within each class. Unscoped or unavailable locality degenerates exactly to B0.

Provenance:

```text
final PR #36 head  0c4028ca6d9a90260a88412a1c209fdfea924d49
suite              295 passed on Python 3.11 / 3.12 / 3.13
squash merge       d0742284de450b86ae98db4f52ea993e90d5f8e7
issue              #35 CLOSED
```

## C3.3 — B2 Session-Affinity — CLOSED

B2 adds SessionID-scoped preferred previous location. A usable preferred worker moves to the front while every remaining worker preserves exact B1 order. Missing, unscoped, or unavailable affinity degenerates exactly to B1.

Provenance:

```text
final PR #38 head  b75528f7c0bcb71cd82a6cadd2c7e0dad6cfd30a
suite              303 passed on Python 3.11 / 3.12 / 3.13
squash merge       b2a13e3bc372449fc5cd3bc8e5b925f7983d991e
issue              #37 CLOSED
```

## C3.4 — B3 State-Aware — CLOSED

B3 may use exact StateID and precise State location but remains causally blind. Exact-State-local workers form a preferred locality class. If exact-State locality is absent or unusable, B3 falls back to B1 candidate locality and then load.

The adversarial test obligation explicitly proves that hidden compatible-ancestor provenance and hidden wrong-sibling/superseded provenance cannot change a B3 decision when its visible exact StateID/location is unchanged.

Provenance:

```text
final PR #40 head  2d69a86fadd2424764415322746f310a7ad1568b
suite              311 passed on Python 3.11 / 3.12 / 3.13
squash merge       1ce103ab0805fa81a4da4c9c4cc708475a891bd6
issue              #39 CLOSED
```

---

# 4. C3.5 — B4 Continuity-Aware

Tracking: #41.

B4 receives the complete normalized v2 information contract and invokes the closed C1 semantic authority for Continuity judgments. C3.5 does **not** reconstruct C1 compatibility, evidence, or migration invariants in the policy layer.

## 4.1 Read-only C1 authority

`CoreContinuityAuthority` exposes two read-only placement-time semantic judgments over the closed C1 core:

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

`attempt_current` validates the observed Attempt against C1's authoritative current-Attempt state. `state_compatible` constructs the public C1 `ExecutionContext` and calls the public `ContinuityCore.state_compatible` method.

Neither query mutates C1.

## 4.2 Attempt fencing

Attempt fencing precedes physical worker availability. B4 permits placement only when:

```text
LogicalRequestID exists
AttemptID exists
observed Attempt authority == CURRENT
C1 attempt_current(LogicalRequestID, AttemptID) == true
```

Otherwise the policy returns `ATTEMPT_FENCED` with no worker ranking.

This prevents a stale observation that still claims `CURRENT` from overriding C1 authority after a retry has superseded that Attempt.

## 4.3 Compatible-State routing

B4 never treats candidate-only locality as verified reusable State.

Exact-State locality is used only when all of the following hold:

```text
exact StateID exists
precise State location exists
Reconciliation == MATCHED
C1 state_compatible(...) == true
at least one declared State location is available
```

Then B4 ranks compatible exact-State-local workers before remote workers, with the shared B0 load key inside each class.

If reconciliation is not matched or C1 reports incompatibility, B4 fails closed to load/recomputation routing rather than consuming the attractive State. This creates the intended executable B3↔B4 distinction for later H2 evaluation.

## 4.4 Lifecycle-aware retention

C3.5 exposes an ordinal lifecycle retention policy over the four canonical Paper 1 classes:

```text
ACTIVE       priority 3  PROTECT
WAITING      priority 2  RETAIN
SPECULATIVE  priority 1  BEST_EFFORT
TERMINAL     priority 0  RELEASE
```

The ordinal mapping is a deterministic C3 implementation choice over the canonical lifecycle classes, not a measured or calibrated cost model. C6 remains responsible for performance calibration and parameter sweeps.

## 4.5 Safe-migration policy surface

C3.5 exposes migration eligibility rather than mutating Binding state directly.

A migration-sensitive commit is policy-eligible only when:

```text
BindingID exists
Binding epoch exists
Reconciliation == MATCHED
```

The policy returns `ALLOW_COMMIT` only in that case; otherwise it returns `WAIT`.

Actual migration commit, epoch fencing, Evidence sufficiency, old-binding supersession, and atomic semantic transition remain C1 responsibilities.

## 4.6 Paired placement interface

`build_baseline_policies(...)` constructs exactly B0–B4.

`decide_paired_placements(...)` requires exactly those five keys, verifies that each mapped policy's own `policy_id` matches its key, and executes the policies in canonical B0→B4 order against the **same immutable `PolicyObservation`**. Each policy independently receives its own `PolicyView` projection.

This prevents duplicate or misregistered policy instances from silently invalidating a paired comparison.

---

# 5. C3.5 bounded review findings

Four substantive issues were found before finalizing C3.5:

1. **Incomplete B4 information contract.** Schema v1 omitted `program_id`, `state_lifecycle`, and `reconciliation`, despite those concepts being explicitly required by the canonical B4 definition. Schema v2 adds exactly those B4 inputs without expanding B0–B3 privileges.
2. **Attempt-fencing weakness and ordering.** The first B4 candidate trusted the observed `attempt_authority` and checked worker availability before fencing. B4 now cross-checks the request/Attempt against C1 `attempt_current`, and fencing precedes physical availability.
3. **PolicyObservation positional compatibility.** The first v2 draft inserted new fields into existing dataclass positions. The fields are now appended at the tail and a regression test proves v1 positional slots retain their original meaning.
4. **Paired-policy registration integrity.** The first paired harness checked only that mapping keys were B0–B4. It now also verifies every mapped policy exposes the matching `PolicyID`, preventing duplicate or wrong-policy registration from corrupting paired results.

The final behavior-bearing candidate before documentation synchronization is:

```text
50f5653e8a39549bbdac828b8677afea28356db7
```

The exact final PR head above this behavior-bearing candidate must pass the full Python 3.11 / 3.12 / 3.13 matrix before merge.

---

# 6. C3.5 test obligations

`tests/simulator/test_continuity_aware_policy.py` requires:

```text
schema v2 contains the three source-backed B4 contract repairs
B0–B3 privileges are unchanged
v1 PolicyObservation positional slots retain their original meanings
B4 can read Program/lifecycle/reconciliation while B3 cannot
compatible exact State locality is preferred only after C1 compatibility + MATCHED reconciliation
wrong-sibling State fails closed to recomputation/load routing
unmatched/ambiguous reconciliation blocks State locality
observed non-current Attempt is fenced
stale CURRENT observation is rejected when C1 says the Attempt is no longer current
Attempt fencing precedes worker availability
candidate-only locality is not promoted into verified reuse
lifecycle priority is deterministic across ACTIVE/WAITING/SPECULATIVE/TERMINAL
migration eligibility requires reconciled declared Binding context
B4 placement/retention/migration queries do not mutate C1
paired interface executes exactly B0–B4 over one observation
wrong-ID policy registration is rejected
identical paired observations produce identical paired decisions
```

Existing B0–B3 suites remain regression obligations.

---

# 7. C3 exit gate

C3 may close only when:

```text
B0 implemented and closed
B1 implemented and closed
B2 implemented and closed
B3 implemented and closed
B4 implemented and closed
machine-readable B0–B4 information contracts fixed before evaluation
B0–B3 privileges unchanged by C3.5 repair
C1 remains semantic authority
C2 remains policy-neutral
paired B0–B4 placement interface exists and validates policy identity
full repository tests pass on Python 3.11
full repository tests pass on Python 3.12
full repository tests pass on Python 3.13
bounded review findings are resolved
```

C4 correctness evaluation remains outside C3 until this gate closes.
