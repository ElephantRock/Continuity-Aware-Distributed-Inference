# 12 — C3 Baseline Policies
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C3 — Baseline Policies B0–B4  
**Prerequisite:** C2 CLOSED on `main` at `afcce955517b9cb063cc75c9d098d24a5171dbdb`  
**Tracking:** #32  
**Status:** IN PROGRESS — C3.1–C3.3 CLOSED; C3.4 B3 State-Aware implementation candidate

---

# 1. Purpose and fairness boundary

C3 implements the normalized Experimental Plan baselines through one common simulator-facing interface:

```text
B0  Request-Centric
B1  Cache-Aware
B2  Session-Affinity
B3  State-Aware
B4  Continuity-Aware
```

> **C1 remains semantic authority. C2 remains the policy-neutral time/resource/fault substrate. C3 may choose actions only from the information its baseline contract permits.**

The machine-readable information boundary is part of the experimental method. The contract registry is immutable after import and set-like observations are canonicalized so neither privilege mutation nor incidental caller ordering can alter a policy's information model.

---

# 2. Information contracts

Schema:

```text
cadi.policy-information-contract.v1
```

Normalized fields:

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

Baseline access:

```text
B0  logical_request_id + resource_load
B1  B0 + state_candidate_key + state_location
B2  B1 + session_id + session_preferred_location
B3  logical_request_id + resource_load + state_candidate_key + exact_state_id + state_location
B4  complete normalized field set
```

B3 receives exact logical/physical State identity and precise State location, as required by the Experimental Plan, but does **not** receive Continuation ancestry, Attempt authority, Evidence authority, Binding generations, reconciliation semantics, State provenance, or producer-Attempt authority.

---

# 3. Common interface

The harness may collect a privileged immutable `PolicyObservation`, but each policy receives only:

```text
PolicyObservation
      |
      v
project_observation(..., PolicyID)
      |
      v
PolicyView
```

`PolicyView` contains exactly the declared fields. Forbidden access fails explicitly.

C2 worker observations are canonical tuples of:

```text
worker_id
available
capacity
active_tasks
queued_tasks
```

All ranking layers exclude unavailable workers before applying policy preference.

---

# 4. Closed slices

## C3.1 — B0 Request-Centric — CLOSED

B0 ranks by:

```text
normalized_load = (active_tasks + queued_tasks) / capacity
normalized_load
queued_tasks
active_tasks
worker_id
```

C3.1 bounded review fixed the mutable contract registry, non-canonical set-like observation ordering, and the missing B2 preferred-location field.

```text
implementation-bearing head  b0558644d15fd63fcc9aa200b634310fa6214333
final PR #34 head            9d77db3595982049cb6025443b497b934d87908e
suite                         288 passed on Python 3.11 / 3.12 / 3.13
squash merge                  b17e03867004ed0dc51aa821cbe10e98a888aebb
issue                         #33 CLOSED
```

## C3.2 — B1 Cache-Aware — CLOSED

B1 treats a candidate-key-scoped location as a deterministic locality preference class, then uses exact B0 load ordering within locality classes. No usable locality degenerates exactly to B0. An unscoped location without a candidate key is ignored.

```text
final PR #36 head  0c4028ca6d9a90260a88412a1c209fdfea924d49
suite              295 passed on Python 3.11 / 3.12 / 3.13
squash merge       d0742284de450b86ae98db4f52ea993e90d5f8e7
issue              #35 CLOSED
```

## C3.3 — B2 Session-Affinity — CLOSED

B2 treats a SessionID-scoped preferred previous location as a deterministic preference. If usable, that worker is moved to the front and every remaining worker preserves exact B1 order. Absent, unscoped, or unavailable affinity degenerates exactly to B1. B2 deliberately cannot distinguish sibling branches because it receives no Continuation ancestry.

```text
final PR #38 head  b75528f7c0bcb71cd82a6cadd2c7e0dad6cfd30a
suite              303 passed on Python 3.11 / 3.12 / 3.13
squash merge       b2a13e3bc372449fc5cd3bc8e5b925f7983d991e
issue              #37 CLOSED
```

---

# 5. C3.4 — B3 State-Aware

Tracking: #39.

The canonical Experimental Plan distinction is:

> knowing where State is

versus:

> knowing whether State causally belongs to current work.

B3 is therefore intentionally **exact-State-aware but causally blind**.

## 5.1 Decision rule

Unavailable workers are removed first.

When `exact_state_id` is present, `state_location` is interpreted as the precise location set for that exact State. If one or more of those locations are available:

```text
exact-State-local workers
        before
remote workers
```

Within locality classes B3 uses the shared B0 load key.

The exact StateID is a selector only. It does not imply:

```text
Continuation compatibility
producer-Attempt authority
branch membership
Binding freshness
Evidence sufficiency
```

When exact-State locality is absent or unusable, B3 falls back to the competent B1 candidate-key locality rule. This ensures B3 is not artificially weakened when only ordinary cache/prefix candidate information is available.

When neither exact StateID nor candidate key scopes `state_location`, B3 falls back to load.

Reasons:

```text
EXACT_STATE_LOCALITY_THEN_LOAD
STATE_AWARE_CANDIDATE_FALLBACK
STATE_AWARE_LOAD_FALLBACK
NO_AVAILABLE_WORKER
```

The policy deliberately does not call C1 `state_compatible` or inspect provenance. Those semantics belong to B4.

---

# 6. C3.4 test obligations

`tests/simulator/test_state_aware_policy.py` requires:

```text
B3 PolicyView exposes exact State identity/location but no causal authority fields
exact-State locality works without a cache candidate key
multiple exact-State locations use shared B0 load ordering
absence of exact StateID falls back exactly to B1 candidate locality
unscoped locations fall back to load
unavailable exact-State locality cannot override available workers
no-available-worker behavior remains explicit
compatible and wrong-branch hidden provenance produce the same B3 decision
```

The last obligation is essential: it makes B3's intended inability to distinguish causal compatibility executable rather than merely descriptive.

---

# 7. Staged C3 plan

```text
C3.1  information contracts + common interface + B0     CLOSED
C3.2  B1 cache-aware                                    CLOSED
C3.3  B2 session-affinity                               CLOSED
C3.4  B3 state-aware                                    ACTIVE
C3.5  B4 continuity-aware + paired-interface closure    PENDING
```

---

# 8. C3.4 exit gate

C3.4 may close only when:

```text
B3 uses only its declared PolicyView
exact StateID scopes physical locality without implying causal validity
exact-State locality is deterministic
absence/unavailability of exact locality falls back to competent B1 behavior
unscoped location falls back safely
B3 remains invariant to hidden Continuation/provenance/authority metadata
full repository tests pass on Python 3.11
full repository tests pass on Python 3.12
full repository tests pass on Python 3.13
bounded review findings are resolved
```

B4 and C4 remain out of scope until this gate closes.
