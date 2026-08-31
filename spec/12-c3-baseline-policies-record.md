# 12 — C3 Baseline Policies
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C3 — Baseline Policies B0–B4  
**Prerequisite:** C2 CLOSED on `main` at `afcce955517b9cb063cc75c9d098d24a5171dbdb`  
**Tracking:** #32  
**Status:** IN PROGRESS — C3.1 CLOSED; C3.2 B1 Cache-Aware implementation candidate

---

# 1. Purpose

C3 introduces the comparison policies required by the normalized Experimental Plan:

```text
B0  Request-Centric
B1  Cache-Aware
B2  Session-Affinity
B3  State-Aware
B4  Continuity-Aware
```

All five policies execute through the same simulator-facing decision interface.

> **C1 remains semantic authority. C2 remains the policy-neutral time/resource/fault substrate. C3 may choose actions only from the information its baseline contract permits.**

---

# 2. Baseline Fairness Boundary

The Experimental Plan requires a machine-readable information contract before policy comparison begins. The purpose is to separate algorithmic advantage from information advantage and to prevent two invalid comparisons:

1. giving a weaker baseline privileged Continuity information that its abstraction does not contain;
2. making a weaker baseline artificially incompetent by withholding ordinary information its abstraction would normally possess.

The projection boundary is therefore part of the experimental method, not merely an implementation convenience.

The contract registry is immutable after import. Set-like observation fields are canonicalized at the interface boundary so incidental caller/container order cannot become a policy input.

---

# 3. Canonical Information Fields

`simulator/policies.py` defines:

```text
logical_request_id
attempt_id
attempt_authority
session_id
session_preferred_location
continuation_id
continuation_ancestry
state_candidate_key
exact_state_id
state_location
state_provenance
producer_attempt
binding_id
binding_epoch
evidence_authority
evidence_status
evidence_freshness
resource_load
```

Schema:

```text
cadi.policy-information-contract.v1
```

---

# 4. C3 Information Contracts

## B0 — Request-Centric

```text
logical_request_id
resource_load
```

B0 receives ordinary request identity, worker availability, capacity, active-task count, and queue depth. It does not receive Session, Continuation, State, Binding, Evidence, or Continuity Attempt-authority fields.

## B1 — Cache-Aware

B0 plus:

```text
state_candidate_key
state_location
```

These fields represent cache/prefix candidate locality or cache-presence information without granting exact State identity or causal State provenance.

## B2 — Session-Affinity

B1 plus:

```text
session_id
session_preferred_location
```

The explicit preferred-location field is required by the canonical Experimental Plan definition of B2. Sibling branches remain in the same Session; B2 does not receive Continuation ancestry or producer-Attempt authority.

## B3 — State-Aware

```text
logical_request_id
resource_load
state_candidate_key
exact_state_id
state_location
```

B3 may know the exact State identity being considered and its precise physical location. It does **not** thereby receive Continuation ancestry, State provenance, producer-Attempt authority, Binding generations, Evidence authority, or reconciliation semantics.

## B4 — Continuity-Aware

B4 receives the complete normalized field set. Its later implementation may invoke C1 public semantics for Attempt authority, State compatibility, Binding epoch validity, Evidence sufficiency, and reconciliation; C3 must not independently reimplement those semantics.

---

# 5. Common Observation and Projection Interface

The harness constructs one privileged immutable `PolicyObservation`. Policies never receive that object directly:

```text
PolicyObservation
      |
      v
project_observation(..., PolicyID)
      |
      v
PolicyView
```

`PolicyView` contains exactly the fields declared by `INFORMATION_CONTRACTS[PolicyID]`. Forbidden field access fails explicitly.

Workers are observed from the closed C2 `ResourceModel` as canonical tuples containing:

```text
worker_id
available
capacity
active_tasks
queued_tasks
```

No C1 semantic state is modified by this observation path.

---

# 6. C3.1 — B0 Request-Centric — CLOSED

B0 ranks available workers by:

```text
normalized_load = (active_tasks + queued_tasks) / capacity
```

with deterministic secondary ordering:

```text
normalized_load
queued_tasks
active_tasks
worker_id
```

A DOWN worker is never selected. No available worker produces:

```text
NO_AVAILABLE_WORKER
```

C3.1 bounded review found and fixed three substantive contract defects:

1. mutable information-contract registry;
2. non-canonical ordering of set-like observation fields;
3. missing explicit B2 `session_preferred_location` information.

Final C3.1 implementation-bearing head:

```text
b0558644d15fd63fcc9aa200b634310fa6214333
```

Final PR #34 head:

```text
9d77db3595982049cb6025443b497b934d87908e
```

Exact final-head validation:

```text
Python 3.11  PASS
Python 3.12  PASS
Python 3.13  PASS
288 passed
```

PR #34 squash merge:

```text
b17e03867004ed0dc51aa821cbe10e98a888aebb
```

Issue #33 closed through that merge.

---

# 7. C3.2 — B1 Cache-Aware

Tracking: #35.

B1 is implemented as a strict extension of the B0 load ordering. It receives only:

```text
logical_request_id
resource_load
state_candidate_key
state_location
```

It does not receive:

```text
exact StateID
Session identity or affinity
Continuation identity or ancestry
State provenance
producer Attempt
Binding generation
Evidence authority/status/freshness
```

## 7.1 Decision rule

Unavailable workers are removed first.

When a `state_candidate_key` exists and at least one candidate location corresponds to an available worker:

```text
candidate-local workers
        before
non-local workers
```

Within each locality class, B1 uses exactly the B0 load key:

```text
normalized_load
queued_tasks
active_tasks
worker_id
```

This deliberately avoids inventing a scalar conversion between cache-locality benefit and queue load before C6 introduces a calibrated cost model.

When no usable locality signal exists, B1 degenerates exactly to the B0 worker ranking and returns:

```text
CACHE_AWARE_LOAD_FALLBACK
```

A location tuple without a `state_candidate_key` is not treated as valid cache locality. This prevents unscoped or stale location data from being promoted into a meaningful candidate match.

When locality is usable, B1 returns:

```text
CACHE_LOCALITY_THEN_LOAD
```

No available worker returns:

```text
NO_AVAILABLE_WORKER
```

---

# 8. C3.2 Test Obligations

`tests/simulator/test_cache_aware_policy.py` requires:

```text
B1 PolicyView exposes only declared cache/load fields
exact StateID and causal/authority fields remain inaccessible
available candidate locality outranks lower remote load
multiple local candidates use B0 load ordering
unscoped location without candidate key is ignored
unavailable local candidate falls back to B0 load ordering
no-available-worker behavior remains explicit
B1 decisions are invariant to all hidden Continuity metadata
```

The full repository suite remains the closure gate.

---

# 9. Staged C3 Plan

```text
C3.1  information contracts + common interface + B0     CLOSED
C3.2  B1 cache-aware                                    ACTIVE
C3.3  B2 session-affinity                               PENDING
C3.4  B3 state-aware                                    PENDING
C3.5  B4 continuity-aware + paired-interface closure    PENDING
```

Every later slice must preserve the information contracts established in C3.1 unless a specification defect is documented before evaluation.

---

# 10. C3.2 Exit Gate

C3.2 may close only when:

```text
B1 uses only its declared PolicyView
B1 locality preference is deterministic
B1 load tie-breaking is exactly inherited from B0
absence/unavailability of locality falls back to B0 ranking
unscoped locations do not create false locality
B1 remains invariant to hidden Continuity metadata
full repository tests pass on Python 3.11
full repository tests pass on Python 3.12
full repository tests pass on Python 3.13
bounded review findings are resolved
```

B2–B4 ranking behavior remains outside C3.2. C4 correctness evaluation remains out of scope until C3 closes.
