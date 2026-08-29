# 03 — Invariants
## Safety, Consistency, and Enforcement Obligations for Continuity-Aware Distributed Inference

**Working paper title:**  
**Continuity-Aware Distributed Inference: Causal Execution and State Lineage for Stateful Generative Workloads**

**Document role:** Canonical invariant and enforcement specification  
**Milestone:** C0 — Research Specification  
**Dependencies:** `01 — Research Thesis`, `02 — Continuity Model`  
**Status:** C0.1 normalized invariant specification candidate

---

# 1. Purpose

This document defines the invariants that must hold for an implementation to claim conformance with the Continuity model.

The Continuity Model defines the entities, state machines, graph relations, authority semantics, and reconciliation operations of the system.

This document defines what must **never become false** while those entities evolve.

Each invariant is specified using:

1. semantic statement;
2. formal predicate;
3. scope;
4. assumptions;
5. prohibited state;
6. representative violation trace;
7. enforcement point;
8. deterministic test obligation;
9. property-based test obligation;
10. experimental relevance.

The invariants are not implementation suggestions.

They are correctness obligations.

An implementation that violates a core invariant is not a valid implementation of the Paper 1 Continuity semantics, even if it achieves better performance.

---

# 2. Invariant Classes

The invariant catalogue is divided into six classes.

## A — Identity invariants

Ensure logical entities cannot be confused across scopes.

## B — Execution invariants

Ensure only the authoritative execution can affect logical request state.

## C — State-lineage invariants

Ensure reusable state is consumed only by compatible computation.

## D — Binding and placement invariants

Ensure stale physical ownership cannot regain semantic authority.

## E — Evidence and reconciliation invariants

Ensure observations cannot silently become authoritative truth.

## F — Monotonicity and event-processing invariants

Ensure delayed, duplicated, and reordered events cannot reverse committed semantic state.

The Paper 1 correctness kernel consists primarily of B, C, D, and E.

---

# 3. System State

Let a system state at logical step \(t\) be:

\[
\Sigma_t
\]

containing at least:

\[
\Sigma_t =
(
G_E,
G_C,
G_X,
R,
A,
X,
B,
E,
O
)
\]

where:

- \(G_E\) is the execution graph;
- \(G_C\) is the set of continuation DAGs;
- \(G_X\) is the reusable-state provenance graph;
- \(R\) contains LogicalRequest state;
- \(A\) contains Attempt state;
- \(X\) contains reusable-state and replica state;
- \(B\) contains binding state;
- \(E\) contains evidence;
- \(O\) contains observed outputs.

A semantic transition is:

\[
\Sigma_t
\xrightarrow{op}
\Sigma_{t+1}
\]

An invariant \(I\) requires:

\[
I(\Sigma_t)=true
\]

for all reachable states:

\[
\Sigma_t
\]

under the stated failure assumptions.

---

# 4. Safety Versus Liveness

This document primarily specifies **safety properties**.

Safety asks:

> Can something semantically incorrect happen?

Examples:

- stale attempt finalizes a request;
- wrong-branch state is reused;
- old binding becomes authoritative;
- ambiguous evidence authorizes ownership.

The document does not generally require that progress always occur.

For example:

```text
insufficient evidence
        ↓
WAIT
```

may preserve safety while delaying progress.

Liveness properties such as:

```text
every valid request eventually completes
```

are outside the initial invariant kernel because they depend on:

- worker availability;
- retry policy;
- network recovery;
- inference-engine behavior;
- resource capacity.

Where safety intentionally permits blocking, the experimental plan must measure its availability/performance cost.

---

# 5. Assumption Boundary

The invariants apply under the Paper 1 assumptions established by the Continuity Model.

The system may experience:

- delayed events;
- duplicate events;
- reordered events;
- retries;
- worker crashes;
- stale observations;
- late results;
- partial state migration;
- state eviction;
- loss of physical state.

The invariant model does not attempt to protect against:

- Byzantine logical authorities;
- malicious evidence forgery;
- undetectable arbitrary memory corruption;
- conflicting independent Continuity authorities without consensus.

A failure outside this assumption boundary must not be cited as an invariant violation unless the model is later extended to cover it.

---

# 6. A1 — Entity Identity Uniqueness

## Statement

No two distinct logical entities of the same entity class may share the same identifier.

For each entity class:

\[
T \in
\{
\mathcal{P},
\mathcal{S},
\mathcal{C},
\mathcal{R},
\mathcal{A},
\mathcal{F},
\mathcal{X},
\mathcal{B},
\mathcal{E},
\mathcal{O}
\}
\]

require:

\[
id(x)=id(y)
\Rightarrow
x=y
\]

for:

\[
x,y\in T
\]

## Purpose

Prevents one event, output, state object, or binding from being interpreted as belonging to another logical entity.

## Prohibited state

```text
Attempt A1:
    id = "a-42"
    request = R1

Attempt A2:
    id = "a-42"
    request = R2
```

with:

\[
A1\neq A2
\]

## Enforcement

At entity creation.

## Deterministic tests

Attempt to create duplicate:

- ProgramID;
- SessionID;
- ContinuationID;
- RequestID;
- AttemptID;
- StateID;
- BindingID;
- Event/Evidence identity where applicable.

Creation must fail or return the already-existing exact entity according to API semantics.

## Property-based test

Generate arbitrary entity-creation sequences with intentional ID collisions.

Property:

\[
UniqueIDs(\Sigma)
\]

must always hold.

---

# 7. A2 — Parent-Scope Consistency

## Statement

Every child entity must resolve to exactly one valid parent scope where the model defines a functional parent.

Examples:

\[
program(s)
\]

is singular.

\[
session(c)
\]

is singular.

\[
continuation(r)
\]

is singular.

\[
request(a)
\]

is singular.

\[
attempt(f)
\]

is singular.

## Formal obligations

For every Attempt:

\[
a\in\mathcal{A}
\Rightarrow
\exists! r\in\mathcal{R}:request(a)=r
\]

For every LogicalRequest:

\[
r\in\mathcal{R}
\Rightarrow
\exists! c\in\mathcal{C}:continuation(r)=c
\]

Equivalent uniqueness requirements hold for the other functional containment relations.

## Prohibited state

```text
Attempt A9
├── Request R2
└── Request R7
```

## Enforcement

At entity creation and graph mutation.

## Property-based test

Randomly construct containment graphs.

Reject all transitions that introduce:

- missing required parent;
- multiple functional parents;
- parent from invalid entity type.

---

# 8. A3 — Continuation Graph Acyclicity

## Statement

The continuation graph for every Session must remain a DAG.

For every Session \(s\):

\[
G_C(s)
\]

must contain no directed cycle.

Formally:

\[
\neg \exists c:
StrictAncestor(c,c)
\]

## Violation example

```text
C1 → C2 → C3
↑         │
└─────────┘
```

## Why it matters

Causal ancestry is used directly in state-compatibility decisions.

A cycle would make continuation precedence and state provenance ambiguous.

## Enforcement

At:

```text
create_continuation()
```

or any operation that adds a continuation parent edge.

## Deterministic test

Construct:

```text
C0 → C1 → C2
```

then attempt:

```text
C2 → C0
```

The mutation must fail.

## Property-based test

Generate random edge additions.

After every accepted mutation:

\[
Acyclic(G_C)=true
\]

must hold.

---

# 9. A4 — State Provenance Resolvability

## Statement

Every reusable inference state object must resolve to exactly one origin Continuation for Paper 1.

For:

\[
x\in\mathcal{X}
\]

require:

\[
\exists! c\in\mathcal{C}:
originContinuation(x)=c
\]

## Purpose

All lineage compatibility ultimately depends on the state object's causal position in the continuation graph.

## Prohibited state

State X has origin:

```text
Attempt A7
```

but A7 has no valid request/continuation parent.

Or X resolves to two different Continuations.

## Enforcement

At:

```text
create_state()
derive_state()
```

and during graph validation.

## Property test

Every reachable State must satisfy:

```text
state
→ origin
→ ...
→ exactly one Continuation
```

---

# 10. B1 — Single Current Attempt Authority

## Statement

A nonterminal LogicalRequest has at most one Attempt with semantic authority `CURRENT`.

For every:

\[
r\in\mathcal{R}
\]

there exists at most one:

\[
a\in\mathcal{A}
\]

such that:

\[
CurrentAttempt(r)=a
\]

## Strong form

\[
CurrentAttempt(r)=a
\Rightarrow
request(a)=r
\land
authorityStatus(a)=CURRENT
\]

Physical execution may overlap during retry races.

Semantic authority may not.

Permitted:

```text
A1 execution RUNNING / authority SUPERSEDED
A2 execution RUNNING / authority CURRENT
```

Prohibited:

```text
A1 authority CURRENT
A2 authority CURRENT
```

for the same LogicalRequest.

## Enforcement

At `start_attempt()` and `supersede_attempt()` using an atomic authority transition.

## Property

```text
count(CURRENT AttemptAuthority per LogicalRequest) ≤ 1
```

# 11. B2 — Attempt Ownership

## Statement

An Attempt may only become `CURRENT` or `COMMITTED` authority for the LogicalRequest to which it belongs.

\[
authorityStatus(a)\in\{CURRENT,COMMITTED\}
\Rightarrow
request(a)=r
\]

for the corresponding request authority relation.

Cross-request authority assignment is prohibited.

# 12. B3 — Attempt Generation Monotonicity

## Statement

New Attempts for a LogicalRequest have monotonically increasing generations.

If \(a_j\) is a later retry generation than \(a_i\) for the same LogicalRequest:

\[
generation(a_j)>generation(a_i)
\]

If:

\[
Supersedes(a_j,a_i)
\]

then:

\[
generation(a_j)>generation(a_i)
\]

Generation is allocated by semantic authority, not inferred from observation time.

# 13. B4 — Supersession Irreversibility

## Statement

Once:

\[
authorityStatus(a)=SUPERSEDED
\]

that Attempt can never become `CURRENT` or `COMMITTED` later.

## Physical outcome remains independent

A superseded Attempt may still transition physically:

```text
RUNNING → SUCCEEDED
```

without regaining semantic authority.

Thus this state is valid:

```text
execution_status = SUCCEEDED
authority_status = SUPERSEDED
```

Recovery after a newer Attempt fails requires a new Attempt generation rather than resurrection of an old one.

# 14. B5 — Finalization Authority and Committed Producer

## Statement

An output may finalize a LogicalRequest only when its Attempt is `CURRENT` at the commit point.

\[
Finalize(r,o)
\Rightarrow
attempt(o)=CurrentAttempt(r)
\]

and:

\[
authorityStatus(attempt(o))=CURRENT
\]

\[
executionStatus(attempt(o))=SUCCEEDED
\]

\[
terminal(o)=true
\]

and sufficient terminal-output Evidence exists.

Successful finalization atomically establishes:

\[
CommittedAttempt(r)=attempt(o)
\]

\[
CurrentAttempt(r)=\bot
\]

and:

\[
authorityStatus(attempt(o))=COMMITTED
\]

## Property-based test

For every accepted finalization, the Attempt must have been `CURRENT` at the exact semantic commit point; schedule-time checks are insufficient.

# 15. B6 — Terminal Request Immutability

## Statement

Once a LogicalRequest becomes terminal, delayed execution events cannot alter its authoritative outcome.

For `COMPLETED` request \(r\):

\[
CommittedAttempt(r)
\]

and:

\[
authoritativeOutput(r)
\]

are immutable under Paper 1 semantics.

A late result from any other Attempt may be recorded diagnostically but cannot replace either value.

# 16. B7 — Superseded-Event and State-Producer Fencing

## Statement

Events belonging to a `SUPERSEDED` Attempt may not perform correctness-sensitive semantic commits.

For event \(v\):

\[
attempt(v)=a
\land
authorityStatus(a)=SUPERSEDED
\Rightarrow
\neg AuthoritativeMutation(v)
\]

This includes:

- delayed final result;
- delayed Phase completion;
- ownership update;
- migration acknowledgment;
- State production marked authoritative;
- State promotion for later cross-request reuse.

Additionally, reusable State whose producer resolves to a `SUPERSEDED` Attempt is not compatible for later cross-request reuse by default.

The event or State may remain observable for diagnostics or cleanup.

# 17. C1 — State Reuse Requires Execution-Context Compatibility

## Statement

Reusable State may only be consumed when it is compatible with the exact consumer `ExecutionContext` \(\kappa\).

\[
Consume(x,\kappa)
\Rightarrow
Compatible(x,\kappa)
\]

Paper 1 requires:

\[
Compatible(x,\kappa)=
ContinuationCompatible(x,\kappa)
\land
ProducerAuthorityValid(x,\kappa)
\land
StateValidity(x)=VALID
\land
SemanticValidity(x,\kappa)
\]

Therefore compatibility requires all of:

```text
same Session
valid Continuation ancestry
valid producer Attempt where applicable
logical StateValidity = VALID
engine/representation validity
```

## Required counterexamples

1. sibling-branch State must be rejected;
2. State from a superseded Attempt on an otherwise valid ancestor must also be rejected.

# 18. C2 — Same Session Is Insufficient

## Statement

Session equality alone never establishes State compatibility.

There must be no rule equivalent to:

\[
SameSession(x,\kappa)
\Rightarrow
Compatible(x,\kappa)
\]

without ancestry, producer authority, logical State validity, and representation validity.

# 19. C3 — Physical Locality Is Insufficient

## Statement

Physical co-location cannot override semantic incompatibility.

Correctness filtering occurs before performance ranking:

```text
candidate State
    ↓
causal + producer + validity filter
    ↓
valid State candidates
    ↓
locality/performance ranking
```

Never:

```text
best local State
    ↓
assume valid
```

# 20. C4 — State Invalidity Dominates Locality

## Statement

If:

\[
StateValidity(x)=INVALID
\]

then:

\[
Compatible(x,\kappa)=false
\]

for every consumer context \(\kappa\).

`INVALID` is a validity state, not a lifecycle class.

Likewise, an `INVALID` physical Replica cannot be directly consumed.

# 21. C5 — Replica Identity Does Not Change Logical State Identity

## Statement

Creating, moving, or deleting physical replicas must not silently create a new logical State identity.

If:

\[
\rho_1,\rho_2\in replicas(x)
\]

then both refer to the same:

\[
StateID(x)
\]

## Prohibited behavior

```text
copy X from W1 to W2
        ↓
implicitly create logically unrelated state Y
```

unless the operation is semantically defined as creating new state.

## Purpose

Separates state lineage from placement.

---

# 22. C6 — Lost Replica Does Not Erase Logical Provenance

## Statement

Eviction or loss of all physical replicas does not remove the logical history of the reusable State.

If:

\[
validReplicas(x)=\varnothing
\]

it does not follow that:

\[
x\notin\mathcal{X}
\]

The system may know that X should exist semantically while having no usable physical copy.

## Required behavior

The reconciler may choose:

```text
RECOMPUTE
RESTORE
FAIL
```

according to policy.

It may not reinterpret another unrelated State as X merely because X is unavailable.

---

# 23. C7 — State Derivation Graph Acyclicity

## Statement

For Paper 1:

\[
G_X
\]

must remain acyclic.

There must be no State \(x\) such that:

\[
StrictStateAncestor(x,x)
\]

## Purpose

Preserves well-founded provenance.

## Enforcement

At `derive_state()`.

---

# 24. D1 — Single Current Binding

## Statement

For every Binding subject \(y\), semantic authority records exactly one current committed Binding or none:

\[
CurrentBinding(y)\in\mathcal{B}\cup\{\bot\}
\]

and one current committed epoch:

\[
CurrentEpoch(y)\in\mathbb{N}
\]

If `CurrentBinding(y)=b`, then:

\[
epoch(b)=CurrentEpoch(y)
\]

Multiple physical migration candidates may coexist, but only one committed Binding is current.

# 25. D2 — Binding Epoch Monotonicity and Unique Candidate Generations

## Statement

For every Binding subject \(y\):

\[
CurrentEpoch_{t+1}(y)\ge CurrentEpoch_t(y)
\]

Committed ownership changes strictly increase the epoch.

Candidate epochs are uniquely and monotonically allocated per subject.

A candidate records:

\[
baseEpoch(b)=CurrentEpoch(y)
\]

at proposal time.

# 26. D3 — Stale Binding and Candidate Fencing

## Statement

An ownership-sensitive action for Binding \(b\) may commit only if:

\[
baseEpoch(b)=CurrentEpoch(subject(b))
\]

and the event/action identifies the exact candidate:

```text
BindingID
BindingEpoch
```

A delayed event for an older committed epoch or a non-winning BindingID cannot alter current ownership.

# 27. D4 — Migration Has One Semantic Commit Point

## Statement

Physical transfer, destination materialization, and candidate creation do not independently change ownership.

There is one semantic commit that atomically changes:

\[
(CurrentBinding,CurrentEpoch)
\]

Before commit, the old Binding remains authoritative.

After commit, the winner becomes `ACTIVE` and the old Binding becomes `SUPERSEDED`.

Concurrent candidates based on the old epoch automatically lose commit eligibility after one candidate succeeds.

# 28. D5 — Migration Evidence Sufficiency

## Statement

Migration commit requires:

1. candidate `base_epoch` equals the current committed epoch;
2. event Evidence is scoped to the exact `BindingID` and epoch;
3. Evidence satisfies the minimum correctness threshold for destination materialization/validation.

\[
CommitMigration(y,b,E)
\Rightarrow
Sufficient(E,MigrationCommit(b),t)
\]

Insufficient or ambiguous Evidence yields an explicit safe outcome rather than ownership commit.

# 29. E1 — Observation Does Not Equal Authority

## Statement

Receiving an observation does not by itself change authoritative logical state.

Formally, for observation event \(v\):

\[
Observe(v)
\]

produces Evidence:

\[
e
\]

but does not imply:

\[
Commit(claim(e))
\]

## Example

```text
Worker W4 reports:
"A1 completed"
```

This can establish an exact observation that A1 produced a result.

It cannot establish:

```text
A1 is still authoritative
```

if A2 already superseded it.

## Purpose

Separates external facts from semantic interpretation.

---

# 30. E2 — Evidence Status and Authority Are Independent

## Statement

Evidence usability must depend on both status and authority.

A high-authority evidence object with unusable status cannot authorize a correctness-sensitive commit.

For evidence \(e\):

\[
authority(e)=AUTHORITATIVE
\]

does not imply:

\[
Sufficient(e,a,t)
\]

if:

```text
status = STALE
UNKNOWN
FAILED
AMBIGUOUS
```

or freshness/scope constraints fail.

Likewise, fresh evidence does not gain higher authority merely because it is recent.

## Purpose

Prevents conflating:

```text
freshness
```

with:

```text
authority
```

---

# 31. E3 — Evidence Sufficiency Before Correctness-Sensitive Commit

## Statement

Every correctness-sensitive semantic commit requiring external observation must satisfy:

\[
Sufficient(E,a,t)=true
\]

Minimum Paper 1 requirements are:

| Action | Internal authority | Minimum external Evidence |
|---|---|---|
| Finalize | Attempt `CURRENT` | exact terminal-output observation for that Attempt |
| State consumption | compatible, logical State `VALID` | Evidence that selected Replica is valid/usable |
| Migration commit | candidate based on current epoch | exact/validated destination materialization Evidence for exact BindingID |

Performance ranking may use weaker estimated/derived Evidence after correctness filtering.

## Property-based test

Generate random combinations of authority, status, age, scope, BindingID, and AttemptID. Correctness commit succeeds iff all normative requirements pass.

# 32. E4 — Ambiguity Cannot Become Semantic Success

## Statement

For correctness-sensitive operation \(a\):

\[
Reconcile(...)=AMBIGUOUS
\Rightarrow
\neg Commit(a)
\]

## Permitted responses

```text
WAIT
RETRY
RECOMPUTE
REPAIR
FAIL
```

depending on policy.

## Prohibited response

```text
AMBIGUOUS
        ↓
choose most recent candidate
        ↓
commit as authoritative
```

unless the decision has been explicitly reclassified as performance-only and cannot alter semantic correctness.

## Experimental significance

This invariant directly supports the hypothesis that Continuity converts some silent incorrect successes into explicit non-success outcomes.

---

# 33. E5 — Evidence Scope Integrity

## Statement

Evidence may only authorize actions within its declared semantic scope.

If evidence concerns:

```text
State X
Attempt A
Binding epoch e
```

it cannot be reused to authorize an unrelated:

```text
State Y
Attempt B
Binding epoch f
```

unless an explicit derivation rule establishes that relationship.

## Formal form

\[
Sufficient(e,a,t)
\Rightarrow
ScopeCompatible(scope(e),subject(a))
\]

---

# 34. E6 — Derived Evidence Cannot Exceed Its Derivation Rule

## Statement

`DERIVED` evidence must have an explicit deterministic derivation from supporting evidence.

It must not silently be promoted to `AUTHORITATIVE` merely because multiple weak observations agree.

For Paper 1, authority escalation must be explicit in policy.

## Example

Two estimated cache-location hints do not automatically become authoritative ownership evidence.

---

# 35. E7 — Freshness Does Not Establish Causal Newness

## Statement

A more recently observed event is not necessarily causally newer.

For evidence \(e_1,e_2\):

\[
observedAt(e_2)>observedAt(e_1)
\]

does not imply:

\[
CausallyNewer(e_2,e_1)
\]

Causal ordering must instead come from:

- Attempt generation;
- Binding epoch;
- explicit supersession;
- graph ancestry;
- semantic commits.

## Purpose

Protects the system from reordered events and clock assumptions.

---

# 36. F1 — Semantic Commit Idempotence

## Statement

Reapplying the same semantic commit must not create a second logical effect.

For idempotent commit \(C\):

\[
C(C(\Sigma))=C(\Sigma)
\]

with respect to authoritative semantic state.

## Example

Receiving:

```text
Finalize R17 with O2
```

twice must still produce exactly one completed LogicalRequest with one authoritative output.

## Enforcement options

- EventID deduplication;
- state-transition guards;
- generation/epoch fencing;
- idempotent update semantics.

The model does not prescribe which.

---

# 37. F2 — Duplicate Observation Safety

## Statement

Duplicate external events cannot create additional authoritative transitions.

If event \(v\) is delivered \(n>1\) times, semantic result must be equivalent to one delivery, absent separate policy-visible side effects such as metrics.

## Tests

Duplicate:

- attempt completion;
- transfer completion;
- state materialization;
- migration acknowledgment;
- final result.

Authoritative system state must remain equivalent.

---

# 38. F3 — Reordered Observation Safety

## Statement

Reordering events must not allow an older semantic generation to supersede a newer committed generation.

Examples:

### Attempt

```text
A2 active
late A1 completion
```

A1 remains fenced.

### Binding

```text
epoch 9 active
late epoch 8 event
```

epoch 8 remains stale.

## Formal concept

For monotonic authority domains:

\[
generation_{committed}
\]

cannot decrease due to observation order.

---

# 39. F4 — Terminal Continuation Irreversibility

## Statement

Once:

\[
lifecycle(c)=TERMINAL
\]

the same Continuation cannot return to:

```text
ACTIVE
WAITING
SPECULATIVE
```

A resumed logical path requires a valid new or pre-existing descendant Continuation.

## Purpose

Prevents causal history from being rewritten.

---

# 40. F5 — Abandoned Continuation Irreversibility

## Statement

Once:

\[
lifecycle(c)=ABANDONED
\]

the Continuation cannot become active again.

State associated exclusively with an abandoned branch may still physically exist but must not regain live-branch semantics merely because it is available.

---

# 41. F6 — State Invalidation Monotonicity

## Statement

For Paper 1, once:

\[
StateValidity(x)=INVALID
\]

that same logical StateID cannot return to `VALID`.

If the logical State remains valid but one physical Replica is defective, repair creates or validates another Replica without invalidating the logical State.

If the logical State itself has become semantically invalid, later recovery requires a new logical StateID where appropriate.

# 42. F7 — Completed Request Output Immutability

## Statement

For completed LogicalRequest \(r\):

\[
authoritativeOutput_t(r)=o
\]

implies for all later \(t'\):

\[
authoritativeOutput_{t'}(r)=o
\]

under Paper 1 semantics.

A correction requires a new higher-level logical operation, not mutation by a late Attempt.

---

# 43. F8 — Graph History Is Append/Annotate, Not Rewrite

## Statement

Committed causal relations should not be silently rewritten to make later observations fit.

Examples:

Prohibited:

```text
change State X origin from C1 to C2
because X is physically found near C2
```

Prohibited:

```text
change A1 request parent from R1 to R2
because its output arrived during R2
```

Corrections to erroneous metadata must be explicit repair operations with provenance, not implicit reinterpretation.

---

# 44. Composite Invariant — Safe Finalization

For:

\[
Finalize(r,o)
\]

require:

\[
request(attempt(o))=r
\]

\[
attempt(o)=CurrentAttempt(r)
\]

\[
authorityStatus(attempt(o))=CURRENT
\]

\[
executionStatus(attempt(o))=SUCCEEDED
\]

\[
terminal(o)=true
\]

\[
status(r)\notin\{COMPLETED,FAILED,CANCELLED\}
\]

and sufficient terminal-output Evidence.

Successful commit atomically records:

\[
CommittedAttempt(r)=attempt(o)
\]

and clears `CurrentAttempt(r)`.

# 45. Composite Invariant — Safe State Reuse

For:

\[
Consume(x,\rho,\kappa)
\]

require:

\[
Compatible(x,\kappa)
\]

which includes Continuation ancestry, producer-Attempt authority, logical State validity, and representation validity; additionally:

\[
\rho\in replicas(x)
\]

\[
status(\rho)=VALID
\]

and sufficient Replica Evidence where required.

A local Replica never overrides a failed causal or producer-authority check.

# 46. Composite Invariant — Safe Migration

For migration of subject \(y\) from committed Binding \(b_i\) to candidate \(b_j\):

require:

\[
baseEpoch(b_j)=CurrentEpoch(y)
\]

\[
epoch(b_j)>epoch(b_i)
\]

with a unique candidate generation and sufficient Evidence scoped to:

```text
BindingID(b_j)
epoch(b_j)
subject(y)
```

At commit, atomically:

```text
CurrentBinding(y) → b_j
CurrentEpoch(y) → epoch(b_j)
b_j.status → ACTIVE
b_i.status → SUPERSEDED
```

Any competing candidate based on the previous epoch loses commit eligibility.

# 47. Composite Invariant — Fail-Closed Reconciliation

For correctness-sensitive action \(a\):

if the Reconciler cannot establish:

\[
Sufficient(E,a,t)
\]

or returns:

```text
AMBIGUOUS
```

then:

\[
\neg Commit(a)
\]

The runtime may instead return:

```text
WAIT
RETRY
RECOMPUTE
REPAIR
FAIL
REJECT
```

This is the executable form of:

> uncertainty does not become semantic certainty.

---

# 48. Enforcement Architecture

The deterministic core should enforce invariants at three layers.

## Layer 1 — Construction-time guards

Prevent malformed entities and graphs.

Examples:

- duplicate identity;
- invalid parent scope;
- continuation cycle;
- state provenance cycle.

## Layer 2 — Transition guards

Prevent illegal state-machine changes.

Examples:

- SUPERSEDED → ACTIVE;
- TERMINAL Continuation → ACTIVE;
- decreasing Binding epoch;
- INVALID State → ACTIVE.

## Layer 3 — Semantic commit guards

Revalidate authority immediately before authoritative mutation.

Examples:

- finalization;
- migration commit;
- ownership change;
- state consumption authorization.

The third layer is essential because conditions may have changed since an earlier scheduling or observation decision.

---

# 49. No Trust in Prior Checks

## Statement

A prior successful check does not exempt a correctness-sensitive commit from validating current authority.

Example:

```text
t0 scheduler checks A1 is active
t1 retry creates A2
t2 A1 result arrives
t3 finalizer trusts t0 check
```

This violates attempt safety.

Therefore:

```text
check-at-schedule-time
```

cannot substitute for:

```text
check-at-commit-time
```

for mutable authority.

The same principle applies to Binding epochs.

---

# 50. Deterministic Test Suite Structure

C1 should contain invariant-oriented tests grouped by semantic property rather than only by implementation module.

Recommended layout:

```text
tests/
├── invariants/
│   ├── identity/
│   ├── attempts/
│   ├── state_lineage/
│   ├── bindings/
│   ├── evidence/
│   ├── reconciliation/
│   └── idempotence/
```

Every invariant in this document must map to at least one deterministic test.

Core invariants require multiple traces.

---

# 51. Property-Based Testing

Property-based tests should generate sequences of valid and adversarial semantic operations.

A generated operation set may include:

```text
create_program
create_session
create_continuation
create_request
start_attempt
supersede_attempt
complete_attempt
finalize_request
create_state
derive_state
add_replica
invalidate_replica
begin_migration
commit_migration
record_evidence
duplicate_event
delay_event
reorder_events
```

After every accepted operation:

```text
assert_all_invariants()
```

must hold.

If an operation is invalid, the test should verify:

1. it is rejected; and
2. the state remains invariant-valid.

---

# 52. Sequence Fuzzing

Beyond property-based entity generation, C1 should support sequence fuzzing.

Example generated trace:

```text
1 create R
2 create A1
3 dispatch A1
4 create state X
5 start migration X
6 timeout A1
7 create A2
8 complete migration from A1
9 complete A2
10 finalize A2
11 deliver duplicate migration event
12 deliver late A1 output
13 evict X
```

The objective is not merely to test individual API calls.

It is to discover invariant violations caused by unusual interleavings.

---

# 53. Model-Based Test Oracle

The invariant checker should act as an independent oracle over semantic state.

For example:

```text
implementation state
        ↓
normalize into semantic snapshot
        ↓
invariant checker
```

The checker should not simply call the same internal predicates used by the implementation wherever avoidable.

Otherwise a bug in:

```text
state_compatible()
```

could be duplicated in both implementation and test.

For critical invariants, use a structurally independent test oracle.

---

# 54. Minimal Required Counterexample Traces

Before C1 is considered complete, the test suite must include explicit traces for at least the following.

## T1 — Late attempt finalization

```text
A1 active
A1 timeout
A2 active
A2 succeeds
A1 succeeds late
```

Expected:

```text
A1 rejected as authoritative completion
```

---

## T2 — Duplicate finalization

```text
A1 output O
Finalize(O)
Finalize(O) again
```

Expected:

```text
one logical completion
```

---

## T3 — Wrong sibling-branch state

```text
C0
├── C1 → X1
└── C2
```

Attempt reuse X1 for C2.

Expected:

```text
rejected
```

---

## T4 — Valid ancestor state

```text
C0 → X0
├── C1
└── C2
```

Expected:

```text
X0 lineage-compatible with C1 and C2
```

subject to semantic validity.

---

## T5 — Stale binding event

```text
epoch 4 owner W1
epoch 5 owner W2 committed
late epoch 4 event arrives
```

Expected:

```text
epoch 5 remains authoritative
```

---

## T6 — Ambiguous ownership evidence

Two incompatible exact observations with insufficient authority.

Expected:

```text
AMBIGUOUS
no ownership commit
```

---

## T7 — Stale authoritative evidence

Evidence authority:

```text
AUTHORITATIVE
```

but status/freshness is unusable.

Expected:

```text
insufficient for action requiring current evidence
```

---

## T8 — Replica eviction

All replicas of X lost.

Expected:

```text
logical X remains known
validReplicas(X) = ∅
```

No unrelated state substituted.

---

## T9 — Terminal continuation resurrection

```text
C1 → TERMINAL
set C1 ACTIVE
```

Expected:

```text
rejected
```

---

## T10 — State invalidation resurrection

```text
X → INVALID
reuse X
```

Expected:

```text
rejected
```

---

# 55. Metamorphic Properties

Several Continuity properties are suitable for metamorphic testing.

## MTP1 — Duplicate insertion

Duplicating an already-delivered observation must not change authoritative semantic outcome.

## MTP2 — Delay

Delaying a stale Attempt completion until after a retry must not make it authoritative.

## MTP3 — Reordering

Reordering observations from different generations must not change generation authority.

## MTP4 — Replica permutation

Changing which physical replica is enumerated first must not change state compatibility.

## MTP5 — Location permutation

Renaming or permuting worker identities must not alter logical ancestry or attempt validity.

## MTP6 — Added irrelevant evidence

Adding evidence outside an action's scope must not change whether that action is authorized.

These properties are useful because they test architectural separation rather than specific constants.

---

# 56. Invariant-to-Research Mapping

| Invariant group | Research question | Hypothesis |
|---|---|---|
| B1–B7 Attempt authority | RQ1 | H1 |
| C1–C7 State lineage | RQ2 | H2 |
| E1–E7 Evidence | RQ3 | H3 |
| C1 + routing constraint | RQ4 | H4 |
| State lifecycle invariants | RQ5 | H5 |
| D1–D5 Binding/migration | RQ6 | H6 |
| Identity/graph attribution | RQ7 | H7 enabling mechanism |
| Generic invariant semantics | RQ8 | portability requirement |

Not every invariant directly produces a performance benefit.

The invariant layer defines correctness constraints within which performance policy operates.

---

# 57. Invariant-to-Metric Mapping

## Attempt invariants

Primary metric:

```text
Stale Attempt Acceptance Rate
```

Expected under conforming Continuity implementation:

\[
0
\]

under modeled failure assumptions.

Secondary:

```text
Duplicate Finalization Rate
```

Expected:

\[
0
\]

---

## State-lineage invariants

Primary:

```text
Wrong-State Consumption Rate
Wrong-Branch Reuse Rate
```

Expected:

\[
0
\]

for lineage-detectable incompatibility.

---

## Binding invariants

Primary:

```text
Silent Binding Divergence Rate
```

Expected:

\[
0
\]

for stale-epoch scenarios covered by the model.

---

## Evidence invariants

Primary:

```text
Ambiguous Commit Rate
```

Expected:

\[
0
\]

for actions classified correctness-sensitive.

Important:

This does not imply:

```text
Ambiguous Outcome Rate = 0
```

Ambiguity may legitimately remain visible.

---

# 58. Explicit Failure Versus Silent Error

A central evaluation distinction is:

```text
explicit non-success
```

versus:

```text
silent incorrect success
```

For example:

### Baseline

```text
ambiguous state
      ↓
guess W2
      ↓
wrong semantic result
      ↓
reported success
```

### Continuity

```text
ambiguous state
      ↓
AMBIGUOUS
      ↓
recompute / wait / fail
```

The latter may be slower.

It is not a correctness failure under the invariant model.

Experiments must therefore separately measure:

```text
silent semantic error rate
explicit failure rate
defer/wait rate
recompute rate
```

---

# 59. Invariant Failure Classification

When a test detects a violation, classify it as one of:

```text
IDENTITY_CORRUPTION
EXECUTION_AUTHORITY_VIOLATION
STATE_LINEAGE_VIOLATION
BINDING_EPOCH_VIOLATION
EVIDENCE_AUTHORITY_VIOLATION
MONOTONICITY_VIOLATION
IDEMPOTENCE_VIOLATION
GRAPH_INTEGRITY_VIOLATION
```

This classification should be emitted by the simulator and CPU prototype.

It will simplify correctness evaluation and fault analysis.

---

# 60. Runtime Assertions

The C1 implementation should support optional invariant assertions in development/test mode.

Examples:

```text
assert request(active_attempt(r)) == r

assert current_epoch(subject) >= previous_epoch

assert not has_cycle(continuation_graph)

assert not has_cycle(state_graph)

assert completed_request_output_is_immutable(r)
```

Expensive full-graph assertions need not run in production mode.

Critical commit-time guards must not be compiled away.

---

# 61. Invariant Coverage

Each invariant must have recorded coverage metadata.

Suggested format:

```text
Invariant:
    B5 Finalization Authority

Unit tests:
    test_finalize_active_attempt
    test_reject_superseded_attempt
    test_reject_cross_request_output

Property tests:
    prop_finalization_always_from_active_attempt

Distributed test:
    retry_race_late_completion

Metric:
    stale_attempt_acceptance_rate
```

This traceability should later connect specification to experiment artifact.

---

# 62. C1 Exit Criteria

C1 — Deterministic Continuity Core is complete only when:

1. every core entity/state machine from `02 — Continuity Model` is implemented;
2. every core invariant has an enforcement point;
3. every invariant has at least one deterministic test;
4. critical invariants have property-based tests;
5. sequence fuzzing can execute adversarial event orderings;
6. duplicate and delayed-event tests pass;
7. all mandatory counterexample traces T1–T10 pass;
8. invariant checking reports zero violations over the defined deterministic test corpus;
9. rejected operations leave semantic state unchanged;
10. test output identifies invariant violations by category;
11. every authoritative mutation passes through a guarded semantic-commit path;
12. no implementation mechanism introduces a hidden semantic identity not represented in `02 — Continuity Model`.

---

# 63. Gate G0 Implication

This invariant specification is one of the documents required to determine whether the C0 abstraction is coherent.

Gate G0 should fail if any of the following remain unresolved after `04 — Failure Model` and `05 — Experimental Plan` are completed:

- two invariants contradict each other;
- an invariant cannot be expressed using the entities in the Continuity Model;
- a core semantic commit lacks an authority rule;
- state compatibility cannot be evaluated deterministically at the Continuity layer plus adapter predicate;
- migration authority is undefined during a modeled state;
- ambiguity has no safe reconciliation outcome;
- an invariant depends on globally synchronized clocks;
- an invariant requires a provider-specific primitive.

---

# 64. Canonical Safety Kernel

The core Paper 1 safety kernel can be reduced to five obligations.

## K1 — Correct execution

\[
Finalize(r,o)
\Rightarrow
attempt(o)=CurrentAttempt(r)
\]

## K2 — Correct state

\[
Consume(x,c)
\Rightarrow
Compatible(x,c)
\]

## K3 — Correct ownership generation

\[
OwnershipCommit(b)
\Rightarrow
epoch(b)=CurrentEpoch(subject(b))
\]

## K4 — Sufficient evidence

\[
CorrectnessCommit(a)
\Rightarrow
Sufficient(E,a,t)
\]

## K5 — No semantic guessing

\[
Ambiguous(E,a)
\Rightarrow
\neg CorrectnessCommit(a)
\]

Everything else in the invariant catalogue supports the integrity of these five obligations.

---

# 65. Final Invariant Principle

The Continuity Runtime must preserve the following rule across retries, failures, state movement, asynchronous observations, and event reordering:

> **No physical observation, cached artifact, delayed result, or historical binding may change authoritative inference semantics unless it can be causally attributed to the current logical computation and satisfies the authority requirements of the action being committed.**

Operationally:

```text
exact identity
      +
valid lineage
      +
current generation
      +
sufficient evidence
      ↓
semantic commit
```

Otherwise:

```text
WAIT
RETRY
RECOMPUTE
REPAIR
REJECT
FAIL
AMBIGUOUS
```

but never silent semantic guessing.