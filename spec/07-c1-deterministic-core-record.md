# 07 — C1 Deterministic Continuity Core
## Initial Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C1 — Deterministic Continuity Core  
**Prerequisite:** Gate G0 PASS after C0.1 normalization  
**Status:** Initial kernel implemented and deterministic test suite passing

---

# 1. Implemented Semantic Surface

The initial C1 kernel implements:

```text
Program
Session
Continuation DAG
LogicalRequest
Attempt
ExecutionContext
ReusableInferenceState
StateReplica
Binding
Evidence
Output
```

with the normalized semantic dimensions:

```text
Attempt.ExecutionStatus
Attempt.AttemptAuthority
CurrentAttempt
CommittedAttempt

StateLifecycle
StateValidity

BindingID
base_epoch
epoch

EvidenceAuthority
EvidenceStatus
scope
freshness bounds
```

---

# 2. Implemented Safety Mechanisms

## Attempt fencing

- only one `CURRENT` Attempt authority per nonterminal LogicalRequest;
- retries make older current Attempts `SUPERSEDED`;
- physical success is independent of semantic authority;
- finalization requires the exact `CURRENT` Attempt at commit time;
- successful finalization records `CommittedAttempt` and clears `CurrentAttempt`;
- completed request output is immutable.

## State compatibility

State compatibility is evaluated against `ExecutionContext` and requires:

```text
Continuation ancestry
+
producer-Attempt authority where applicable
+
StateValidity = VALID
+
representation-specific SemanticValidity
```

State produced by a superseded Attempt is rejected for later cross-request reuse by default.

## State lifecycle

State lifecycle is derived from known reusable descendant Continuations:

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

Validity is independent:

```text
VALID
INVALID
```

## Binding fencing

Migration candidates carry:

```text
BindingID
base_epoch
epoch
```

Candidate epochs are uniquely and monotonically allocated per subject.

Commit requires the candidate's `base_epoch` to remain equal to the current committed epoch and requires Evidence scoped to the exact BindingID and candidate epoch.

## Evidence

The runtime implements:

```text
ESTIMATED
DERIVED
EXACT_OBSERVATION
AUTHORITATIVE
```

as the authority order and:

```text
VALID
STALE
UNKNOWN
FAILED
AMBIGUOUS
```

as independent status.

Correctness-sensitive operations enforce minimum Evidence requirements.

---

# 3. Implemented Tool-Wait Semantics

External tool waits are represented at Continuation scope:

```text
C1 ACTIVE
    ↓
C1 WAITING
    ↓ tool result
create C2 ACTIVE child
    ↓
C1 TERMINAL
```

No normative `TOOL_WAIT` Attempt Phase is implemented.

---

# 4. Independent Invariant Oracle

The test artifact includes an independent structural invariant checker over normalized core state.

It checks at least:

```text
single CurrentAttempt authority
single CommittedAttempt
completed-request consistency
Continuation DAG acyclicity
State provenance consistency
Binding current-epoch consistency
invalid-State incompatibility
```

---

# 5. Initial Test Result

Command:

```text
python -m pytest
```

Result:

```text
13 passed
```

Covered cases include:

```text
late successful superseded Attempt
tainted/stale Attempt finalization rejection
completed output immutability
wrong sibling State
valid ancestor State
superseded-producer State
invalid logical State
concurrent migration candidate fencing
ambiguous Evidence fail-closed behavior
insufficient estimated finalization Evidence
tool-wait descendant creation
exact State/Replica Evidence scope
seeded operation-sequence fuzzing
```

---

# 6. Deliberately Not Implemented in C1

The initial kernel does not implement:

```text
queueing simulation
network timing
worker performance
accelerator cost models
public trace ingestion
routing optimization
retention optimization
migration optimization
Gateway API integration
real distributed process execution
```

Those belong to C2 and later milestones.

---

# 7. C1 Next Steps

Before C1 is considered complete, extend the deterministic artifact with:

1. broader mandatory Failure Model trace coverage;
2. larger adversarial sequence corpus;
3. explicit event identity/idempotence model;
4. Phase entities where needed for State production ordering;
5. more independent oracle checks rather than implementation-self-checks;
6. deterministic snapshots/replay;
7. machine-readable semantic trace format;
8. coverage mapping from every core invariant to at least one named test.

Once these are complete, C1 can close and C2 discrete-event simulation can begin.
