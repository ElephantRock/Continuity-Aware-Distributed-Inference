# 12 — C3 Baseline Policies
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C3 — Baseline Policies B0–B4  
**Prerequisite:** C2 CLOSED on `main` at `afcce955517b9cb063cc75c9d098d24a5171dbdb`  
**Tracking:** #32  
**Status:** IN PROGRESS — C3.1 information contracts/common interface/B0 implementation candidate

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

All five policies must execute through the same simulator-facing decision interface.

C3 must preserve the closed architectural boundaries:

> **C1 remains semantic authority. C2 remains the policy-neutral time/resource/fault substrate. C3 may choose actions only from the information its baseline contract permits.**

---

# 2. Baseline Fairness Boundary

The Experimental Plan requires a machine-readable information contract before policy comparison begins.

The purpose is to separate:

```text
algorithmic advantage
```

from:

```text
information advantage
```

and to prevent two invalid comparisons:

1. giving a weaker baseline privileged Continuity information that its abstraction does not contain;
2. making a weaker baseline artificially incompetent by withholding ordinary information its abstraction would normally possess.

The projection boundary is therefore part of the experimental method, not merely an implementation convenience.

---

# 3. Canonical Information Fields

`simulator/policies.py` defines the normalized machine-readable field vocabulary:

```text
logical_request_id
attempt_id
attempt_authority
session_id
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

The schema identifier is:

```text
cadi.policy-information-contract.v1
```

---

# 4. C3 Information Contracts

## B0 — Request-Centric

Allowed:

```text
logical_request_id
resource_load
```

B0 receives ordinary request identity, worker availability, capacity, active-task count, and queue depth.

It does not receive Session, Continuation, State, Binding, Evidence, or Continuity Attempt-authority fields.

## B1 — Cache-Aware

B0 plus:

```text
state_candidate_key
state_location
```

These fields represent cache/prefix candidate locality or cache-presence information without granting causal State provenance.

## B2 — Session-Affinity

B1 plus:

```text
session_id
```

Session identity may influence preferred placement. Sibling branches remain in the same Session and B2 does not receive Continuation ancestry.

## B3 — State-Aware

Allowed:

```text
logical_request_id
resource_load
state_candidate_key
exact_state_id
state_location
```

This is the explicit C3 interpretation of the normalized B3 contract.

B3 may know the exact logical/physical State identity being considered and its precise physical location. It does **not** thereby receive:

```text
Continuation ancestry
State provenance
producer-Attempt authority
Binding generations
Evidence authority
reconciliation semantics
```

This preserves the intended distinction:

> knowing where a precisely identified State is

is not equivalent to:

> knowing whether that State is causally valid for the current Continuation and authoritative Attempt.

This contract must remain fixed during later H2 experiments unless the research specification is explicitly revised and the comparison rerun.

## B4 — Continuity-Aware

B4 receives the complete normalized field set.

Its later implementation may use C1 public semantics for:

```text
Attempt authority
State compatibility
Binding epoch validity
Evidence sufficiency
reconciliation
```

C3 must not reimplement those semantics independently.

---

# 5. Common Observation and Projection Interface

The harness constructs one privileged immutable `PolicyObservation` containing the observations available to the experiment.

Policies never receive that object directly.

Instead:

```text
PolicyObservation
      |
      v
project_observation(..., PolicyID)
      |
      v
PolicyView
```

`PolicyView` contains exactly the fields declared by `INFORMATION_CONTRACTS[PolicyID]`.

Attempting to request a field outside the contract fails explicitly.

The individual policy implementation therefore cannot accidentally read a hidden field merely because the experiment harness happened to collect it.

---

# 6. C2 Resource Observation

`observe_resources(ResourceModel)` projects the closed C2 physical model into deterministic worker observations:

```text
worker_id
available
capacity
active_tasks
queued_tasks
```

Workers are emitted in canonical `worker_id` order.

No C1 semantic state is read or modified by this observation path.

---

# 7. B0 Request-Centric Policy

C3.1 implements B0 only.

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

A DOWN worker is never selected even when its numeric load is zero.

If no worker is available, B0 returns the explicit non-placement result:

```text
NO_AVAILABLE_WORKER
```

This behavior is intentionally simple but competent within the B0 abstraction.

---

# 8. C3.1 Test Obligations

`tests/simulator/test_policies.py` requires:

```text
exact B0–B4 contract registry
B0 contract contains request identity + resource/load only
B2 receives Session identity
B3 receives exact StateID/location but not Continuation ancestry or producer Attempt
B4 receives the complete field vocabulary
B0 PolicyView structurally omits privileged attributes
forbidden field access fails explicitly
ResourceModel observations are canonical and reflect queue state
B0 chooses least normalized load deterministically
unavailable workers are excluded
no-available-worker behavior is explicit
B0 decisions are invariant to changes in all hidden Continuity metadata
```

---

# 9. Staged C3 Plan

```text
C3.1  information contracts + common interface + B0
C3.2  B1 cache-aware
C3.3  B2 session-affinity
C3.4  B3 state-aware
C3.5  B4 continuity-aware + paired-interface closure
```

Every later slice must preserve the information contracts established here unless a specification defect is found and documented before evaluation.

---

# 10. C3.1 Exit Gate

C3.1 may close only when:

```text
machine-readable B0–B4 contracts exist
projection boundary is enforced
B0 is deterministic and load-aware
B0 cannot observe privileged Continuity fields
full repository tests pass on Python 3.11
full repository tests pass on Python 3.12
full repository tests pass on Python 3.13
review findings are resolved
```

B1–B4 ranking behavior remains outside C3.1.

C4 correctness evaluation remains outside C3 entirely until all five policies are implemented against the identical closed interface.
