# 12 — C3 Baseline Policies
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C3 — Baseline Policies B0–B4  
**Prerequisite:** C2 CLOSED on `main` at `afcce955517b9cb063cc75c9d098d24a5171dbdb`  
**Tracking:** #32  
**Status:** IN PROGRESS — C3.1 and C3.2 CLOSED; C3.3 B2 Session-Affinity implementation candidate

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

The Experimental Plan requires a machine-readable information contract before policy comparison begins. The projection boundary prevents privileged Continuity information from leaking into weaker baselines and prevents weaker baselines from being made artificially incompetent by withholding ordinary information their abstraction requires.

The contract registry is immutable after import. Set-like observation fields are canonicalized at the interface boundary so incidental caller/container order cannot become a policy input.

---

# 3. Canonical Information Contracts

The normalized field vocabulary is:

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

Baseline access:

```text
B0  logical_request_id + resource_load
B1  B0 + state_candidate_key + state_location
B2  B1 + session_id + session_preferred_location
B3  logical_request_id + resource_load + state_candidate_key + exact_state_id + state_location
B4  complete normalized field set
```

B2 still does not receive Continuation identity/ancestry, exact State identity, causal State provenance, producer-Attempt authority, Binding generations, or Evidence authority.

B3 may know exact State identity/location but does not thereby learn whether that State is causally valid for the current Continuation and authoritative Attempt.

B4 may later invoke C1 public semantics; C3 must not independently reimplement those semantics.

---

# 4. Common Interface

The harness constructs a privileged immutable `PolicyObservation`, but policies receive only a projected `PolicyView`:

```text
PolicyObservation
      |
      v
project_observation(..., PolicyID)
      |
      v
PolicyView
```

`PolicyView` contains exactly the declared contract fields. Forbidden field access fails explicitly.

C2 workers are observed canonically as:

```text
worker_id
available
capacity
active_tasks
queued_tasks
```

---

# 5. C3.1 — B0 Request-Centric — CLOSED

B0 ranks available workers by:

```text
normalized_load = (active_tasks + queued_tasks) / capacity
normalized_load
queued_tasks
active_tasks
worker_id
```

No available worker returns `NO_AVAILABLE_WORKER`.

C3.1 bounded review fixed:

1. mutable information-contract registry;
2. non-canonical set-like observation ordering;
3. missing explicit B2 `session_preferred_location`.

Provenance:

```text
implementation-bearing head  b0558644d15fd63fcc9aa200b634310fa6214333
final PR #34 head            9d77db3595982049cb6025443b497b934d87908e
suite                         288 passed on Python 3.11 / 3.12 / 3.13
squash merge                  b17e03867004ed0dc51aa821cbe10e98a888aebb
issue                         #33 CLOSED
```

---

# 6. C3.2 — B1 Cache-Aware — CLOSED

B1 receives only request/load plus candidate key/location. Unavailable workers are removed first.

If a candidate key exists and one or more candidate locations are available, candidate-local workers rank before non-local workers. Within each class B1 uses exactly B0's load ordering.

If locality is absent or unusable, B1 degenerates exactly to B0 worker ranking. A location tuple without a candidate key is ignored as unscoped locality.

This avoids inventing a scalar cache-benefit-vs-load conversion before C6 calibration.

C3.2 bounded review found no additional semantic-information leak or ordering defect after the C3.1 contract hardening.

Provenance:

```text
final PR #36 head  0c4028ca6d9a90260a88412a1c209fdfea924d49
suite              295 passed on Python 3.11 / 3.12 / 3.13
squash merge       d0742284de450b86ae98db4f52ea993e90d5f8e7
issue              #35 CLOSED
```

---

# 7. C3.3 — B2 Session-Affinity

Tracking: #37.

B2 receives the B1 contract plus:

```text
session_id
session_preferred_location
```

## 7.1 Decision rule

Unavailable workers are removed first.

`session_preferred_location` is meaningful only when a `session_id` is present. A bare preferred location without Session identity is ignored as an unscoped affinity signal.

When the scoped preferred location names an available worker:

```text
session-preferred worker
        before
all remaining workers in exact B1 order
```

The remaining workers are not re-ranked by a new B2-specific rule. This guarantees that B2 is a strict, inspectable extension of B1.

When Session affinity is absent or unusable, B2 degenerates exactly to B1 ordering and returns:

```text
SESSION_AFFINITY_B1_FALLBACK
```

When affinity is used:

```text
SESSION_AFFINITY_THEN_CACHE_LOAD
```

No available worker returns:

```text
NO_AVAILABLE_WORKER
```

This intentionally models the known limitation that sibling branches share a Session: B2 has no Continuation ancestry and therefore cannot distinguish branch causality.

---

# 8. C3.3 Test Obligations

`tests/simulator/test_session_affinity_policy.py` requires:

```text
B2 PolicyView exposes Session affinity but no causal Continuity fields
scoped available Session preference outranks cache locality
workers after the preferred location preserve exact B1 ordering
unscoped preferred location is ignored
unavailable preferred location falls back exactly to B1
missing preferred location falls back exactly to B1
no-available-worker behavior remains explicit
B2 decisions are invariant to hidden branch/Continuity metadata
```

---

# 9. Staged C3 Plan

```text
C3.1  information contracts + common interface + B0     CLOSED
C3.2  B1 cache-aware                                    CLOSED
C3.3  B2 session-affinity                               ACTIVE
C3.4  B3 state-aware                                    PENDING
C3.5  B4 continuity-aware + paired-interface closure    PENDING
```

---

# 10. C3.3 Exit Gate

C3.3 may close only when:

```text
B2 uses only its declared PolicyView
Session preferred location requires Session scope
available Session preference is deterministic
remaining workers preserve exact B1 ordering
absence/unavailability of affinity falls back exactly to B1
B2 remains invariant to hidden Continuation/causal metadata
full repository tests pass on Python 3.11
full repository tests pass on Python 3.12
full repository tests pass on Python 3.13
bounded review findings are resolved
```

B3/B4 behavior and C4 evaluation remain out of scope.
