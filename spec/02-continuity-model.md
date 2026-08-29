# 02 — Continuity Model
## Formal Semantic Model for Continuity-Aware Distributed Inference

**Working paper title:**  
**Continuity-Aware Distributed Inference: Causal Execution and State Lineage for Stateful Generative Workloads**

**Document role:** Canonical semantic specification  
**Milestone:** C0 — Research Specification  
**Dependency:** `01 — Research Thesis`  
**Status:** C0.1 normalized semantic model candidate

---

# 1. Purpose

This document defines the semantic model underlying **Continuity-Aware Distributed Inference**.

It converts the conceptual hierarchy introduced in the Research Thesis into explicit entities, relations, state transitions, validity rules, and reconciliation semantics suitable for:

- formal reasoning;
- deterministic implementation;
- property testing;
- discrete-event simulation;
- distributed CPU experiments;
- later integration adapters.

This document does not define:

- a wire protocol;
- a database schema;
- a Kubernetes API;
- an inference-engine API;
- a specific scheduler;
- a cloud-provider implementation.

The model is intentionally implementation-neutral.

---

# 2. Design Principles

The model follows seven principles.

## M1 — Identity levels are distinct

The following identities must not be collapsed:

```text
Program
Session
Continuation
LogicalRequest
Attempt
Phase
```

Each represents a different semantic scope.

---

## M2 — Logical identity is distinct from physical placement

A state object remains logically identical when copied or moved.

```text
State S17
├── replica on Worker A
├── replica on Worker B
└── replica in remote storage
```

These are three placements of one logical state object.

---

## M3 — Causal lineage is explicit

Relations such as:

```text
continues
forks-from
derived-from
supersedes
produces
consumes
```

must be represented rather than inferred solely from timestamps or physical locality.

---

## M4 — Execution authority is explicit

The most recently observed execution is not automatically authoritative.

A logical request has an explicitly identified active attempt.

---

## M5 — State compatibility is semantic

A physically local or content-similar state object is not automatically valid for a continuation.

Compatibility depends on provenance and state semantics.

---

## M6 — Evidence and truth are separate

An observation describes what the system has evidence to believe.

It does not by itself redefine authoritative logical state.

---

## M7 — Ambiguity remains explicit

The model must be able to represent:

```text
known valid
known invalid
not yet known
ambiguous
stale
failed observation
```

without forcing uncertain state into a Boolean valid/invalid result.

---

# 3. Universe of Entities

Let the system contain the following entity sets.

\[
\mathcal{P}
\]

Programs.

\[
\mathcal{S}
\]

Sessions.

\[
\mathcal{C}
\]

Continuations.

\[
\mathcal{R}
\]

Logical requests.

\[
\mathcal{A}
\]

Execution attempts.

\[
\mathcal{F}
\]

Execution phases.

\[
\mathcal{X}
\]

Reusable inference state objects.

\[
\mathcal{L}
\]

Physical execution/state locations.

\[
\mathcal{B}
\]

Bindings.

\[
\mathcal{E}
\]

Evidence objects.

\[
\mathcal{O}
\]

Observed outputs and terminal results.

Every entity has a globally unique logical identifier within the experimental system.

Identity uniqueness is a semantic requirement.

The representation of the identifier is not prescribed.

---

# 4. Program

A **Program** is the highest-level unit of logical computation modeled by Continuity.

Formally:

\[
p \in \mathcal{P}
\]

A Program may contain one or more Sessions.

Define:

\[
sessions(p) \subseteq \mathcal{S}
\]

A Program does not require a particular agent framework or workflow representation.

Examples include:

- a multi-turn assistant task;
- a coding workflow;
- a research task;
- a compound inference pipeline;
- a multi-agent computation.

Program identity exists so lower-level execution can be attributed to a larger logical computation.

For Paper 1, Program execution has a minimal lifecycle:

```text
CREATED
RUNNING
COMPLETED
FAILED
CANCELLED
```

The Continuity Runtime does **not** define the application's objective semantics. Instead, the application or workload supplies:

\[
ObjectiveSatisfied(p)
\]

and Program completion is defined as:

\[
ProgramComplete(p) \iff ObjectiveSatisfied(p)
\]

subject to a valid Program terminal transition.

This definition exists primarily to make Program Completion Time and related evaluation metrics semantically well-defined. It does not introduce Program-level scheduling into Paper 1.

# 5. Session

A **Session** is a long-lived stateful lineage within a Program.

For:

\[
s \in \mathcal{S}
\]

define:

\[
program(s) \in \mathcal{P}
\]

A Session contains a continuation graph:

\[
G_C(s)
\]

A Session does not imply one linear conversation.

It may contain:

- forks;
- speculative continuations;
- subagent branches;
- abandoned branches;
- joins.

Therefore:

```text
Session ≠ linear request history
```

---

# 6. Continuation

A **Continuation** represents one semantic position in a Session's evolving computation.

For:

\[
c \in \mathcal{C}
\]

define:

\[
session(c) \in \mathcal{S}
\]

and:

\[
program(c)=program(session(c))
\]

Each Continuation has zero or more causal parents:

\[
parents(c) \subseteq \mathcal{C}
\]

and zero or more children:

\[
children(c) \subseteq \mathcal{C}
\]

For ordinary linear continuation:

\[
|parents(c)|=1
\]

A root Continuation has:

\[
|parents(c)|=0
\]

A join may have:

\[
|parents(c)|>1
\]

---

# 7. Continuation Graph

For each Session \(s\), define:

\[
G_C(s)=(V_C,E_C)
\]

where:

\[
V_C=\{c \in \mathcal{C}\mid session(c)=s\}
\]

and:

\[
(c_i,c_j)\in E_C
\]

means:

> \(c_j\) causally continues from \(c_i\).

The graph must be acyclic.

Therefore:

\[
G_C(s)
\]

is a directed acyclic graph.

Define:

\[
Ancestor(c_i,c_j)
\]

iff there exists a directed path:

\[
c_i \leadsto c_j
\]

in \(G_C\).

Define:

\[
StrictAncestor(c_i,c_j)
\]

iff:

\[
Ancestor(c_i,c_j) \land c_i \neq c_j
\]

The reflexive interpretation:

\[
Ancestor(c,c)=true
\]

is permitted for compatibility calculations.

---

# 8. Continuation Lifecycle

Each Continuation has lifecycle state:

```text
CREATED
ACTIVE
WAITING
SPECULATIVE
JOINING
TERMINAL
ABANDONED
```

The minimum implementation for Paper 1 only requires:

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

but the semantic model reserves the additional states.

## CREATED

Continuation exists but has not begun active execution.

## ACTIVE

Continuation may currently generate execution work.

## WAITING

Continuation is logically live but external progress is required.

Examples:

- tool invocation;
- user interaction;
- external retrieval;
- asynchronous dependency.

## SPECULATIVE

Continuation is being explored but is not yet authoritative.

## JOINING

Continuation waits for required parent branches.

## TERMINAL

Continuation has no expected future execution.

## ABANDONED

Continuation is intentionally discarded.

---

# 9. Valid Continuation Transitions

At minimum:

```text
CREATED → ACTIVE

ACTIVE → WAITING
ACTIVE → SPECULATIVE
ACTIVE → TERMINAL
ACTIVE → ABANDONED

WAITING → TERMINAL
WAITING → ABANDONED

SPECULATIVE → ACTIVE
SPECULATIVE → TERMINAL
SPECULATIVE → ABANDONED

JOINING → ACTIVE
JOINING → TERMINAL
JOINING → ABANDONED
```

The model forbids transition from:

```text
TERMINAL → ACTIVE
```

and:

```text
ABANDONED → ACTIVE
```

For Paper 1, an external tool gap is represented at the **Continuation** level rather than as an Attempt Phase.

Canonical tool-return sequence:

```text
C1 ACTIVE
    ↓
external tool invoked
    ↓
C1 WAITING
    ↓
tool result arrives
    ↓
create child C2 ACTIVE
    ↓
C1 TERMINAL
```

Thus resumption after an external gap advances causal history by creating a descendant Continuation rather than reactivating the same waiting node. This rule makes tool-return lineage explicit and keeps retention semantics aligned with the Continuation DAG.

# 10. Logical Request

A **LogicalRequest** represents one requested inference operation.

For:

\[
r\in\mathcal{R}
\]

define:

\[
continuation(r)\in\mathcal{C}
\]

\[
session(r)=session(continuation(r))
\]

\[
program(r)=program(continuation(r))
\]

A LogicalRequest represents application intent, not a single transport submission.

One LogicalRequest may produce multiple execution attempts.

---

# 11. Logical Request Lifecycle

Initial lifecycle:

```text
CREATED
READY
RUNNING
COMPLETED
FAILED
CANCELLED
```

Semantics:

### CREATED

Request identity exists.

### READY

Request is eligible for execution.

### RUNNING

At least one current execution attempt is active.

### COMPLETED

One valid attempt has committed the terminal result.

### FAILED

The request cannot be completed under current execution/recovery policy.

### CANCELLED

The logical request is intentionally terminated.

The following are terminal:

```text
COMPLETED
FAILED
CANCELLED
```

---

# 12. Attempt

An **Attempt** is one concrete execution instance of a LogicalRequest.

For:

\[
a \in \mathcal{A}
\]

define:

\[
request(a)\in\mathcal{R}
\]

Every Attempt has:

```text
AttemptID
LogicalRequestID
generation
execution_status
authority_status
created_at
```

The generation is monotonically increasing within a LogicalRequest.

For two attempts:

\[
a_i,a_j
\]

where:

\[
request(a_i)=request(a_j)
\]

if:

\[
generation(a_i)<generation(a_j)
\]

then \(a_j\) is newer in the attempt sequence.

Generation is ordering metadata.

`AttemptID` remains the exact execution identity.

Crucially, **execution outcome and semantic authority are orthogonal**. An Attempt may physically succeed after it has already been superseded.

# 13. Attempt Execution and Authority State

Each Attempt has two independent state dimensions.

## 13.1 ExecutionStatus

```text
CREATED
DISPATCHED
RUNNING
SUCCEEDED
FAILED
CANCELLED
```

### CREATED

Attempt identity exists but execution has not been dispatched.

### DISPATCHED

Execution has been assigned to an execution location.

### RUNNING

Physical execution has begun.

### SUCCEEDED

The Attempt produced a terminal candidate result.

Important:

```text
SUCCEEDED ≠ LogicalRequest COMMITTED
```

### FAILED

The Attempt cannot produce a usable result.

### CANCELLED

Physical execution was intentionally stopped.

## 13.2 AttemptAuthority

```text
NONE
CURRENT
COMMITTED
SUPERSEDED
```

### NONE

The Attempt exists but has not been granted semantic authority.

### CURRENT

The Attempt is currently authorized to affect the LogicalRequest at correctness-sensitive commit points.

### COMMITTED

The Attempt is the historical authoritative producer of the completed LogicalRequest.

### SUPERSEDED

The Attempt has permanently lost semantic authority to a newer Attempt.

These dimensions permit states such as:

```text
A1:
    execution_status = SUCCEEDED
    authority_status = SUPERSEDED
```

which is required for late-success retry races.

# 14. Current and Committed Attempt

Each nonterminal LogicalRequest has at most one **current authoritative Attempt**.

Define:

\[
CurrentAttempt(r)\in \mathcal{A}\cup\{\bot\}
\]

where \(\bot\) means no current Attempt.

If:

\[
CurrentAttempt(r)=a
\]

then:

\[
request(a)=r
\]

and:

\[
authorityStatus(a)=CURRENT
\]

For a completed LogicalRequest, define:

\[
CommittedAttempt(r)\in \mathcal{A}\cup\{\bot\}
\]

A non-completed request has:

\[
CommittedAttempt(r)=\bot
\]

Starting a retry produces a new Attempt \(a_{n+1}\) and atomically changes:

\[
CurrentAttempt(r):a_n\rightarrow a_{n+1}
\]

with:

```text
old Attempt authority → SUPERSEDED
new Attempt authority → CURRENT
```

Finalization atomically performs:

```text
LogicalRequest.status → COMPLETED
CommittedAttempt(r) → current Attempt
CurrentAttempt(r) → ⊥
current Attempt.authority_status → COMMITTED
```

This preserves both current authority and the historical identity of the committed producer.

All accompanying C0.1 documents use `CurrentAttempt(r)` consistently for current semantic authority.

# 15. Supersession Relation

Define:

\[
Supersedes(a_j,a_i)
\]

iff:

\[
request(a_j)=request(a_i)
\]

and:

\[
generation(a_j)>generation(a_i)
\]

and \(a_j\) has become `CURRENT` authority while \(a_i\) is changed to `SUPERSEDED`.

Supersession is monotonic.

Once:

\[
authorityStatus(a_i)=SUPERSEDED
\]

it remains `SUPERSEDED` for the lifetime of that Attempt.

An old Attempt cannot regain authority. Recovery after a newer Attempt fails requires creation of another new Attempt; it does not resurrect an older one.

# 16. Phase

A **Phase** represents a distributed sub-operation of an Attempt.

For:

\[
f\in\mathcal{F}
\]

define:

\[
attempt(f)\in\mathcal{A}
\]

Possible Phase types include:

```text
PREFILL
DECODE
STATE_FETCH
STATE_TRANSFER
STATE_RESTORE
ENCODE
OTHER
```

The model does not require every inference engine to expose all phases.

External tool waits are represented through Continuation lifecycle and descendant creation for Paper 1, not as a normative Attempt Phase.

Phase identity exists primarily to associate:

- state production;
- state consumption;
- execution location;
- observations;
- delayed events;

with an exact Attempt component.

# 17. Execution Graph

Define the execution graph:

\[
G_E=(V_E,E_E)
\]

where:

\[
V_E=
\mathcal{P}\cup
\mathcal{S}\cup
\mathcal{C}\cup
\mathcal{R}\cup
\mathcal{A}\cup
\mathcal{F}
\]

Edges encode semantic containment or causality.

Examples:

```text
Program → Session
Session → Continuation
Continuation → LogicalRequest
LogicalRequest → Attempt
Attempt → Phase
Continuation → Continuation
```

The execution graph contains both:

### containment edges

Example:

\[
request(a)=r
\]

### causal edges

Example:

\[
c_1\rightarrow c_2
\]

The implementation may store these separately.

The semantic model treats both as explicit relationships.

---

# 18. ReusableInferenceState

A reusable-state object is:

\[
x\in\mathcal{X}
\]

Each state object contains:

```text
StateID
origin
semantic_type
representation
lineage
lifecycle
validity
retention_intent
replicas
```

`lifecycle` and `validity` are independent dimensions.

Lifecycle expresses **expected future usefulness** for retention policy.

Validity expresses **whether direct semantic reuse is permitted**.

# 19. State Origin and Producer Resolution

Every reusable State object has one exact provenance origin.

Define:

\[
origin(x)\in
\mathcal{C}\cup
\mathcal{R}\cup
\mathcal{A}\cup
\mathcal{F}
\]

The exact granularity may depend on the State representation.

Examples:

### Continuation-level

```text
conversation prefix state
```

may originate at Continuation \(c\).

### Phase-level

```text
prefill KV state
```

may originate from Phase \(f\).

When origin is below Continuation level, define:

\[
originContinuation(x)
\]

by following execution containment upward.

Thus every reusable State object resolves to exactly one origin Continuation.

When origin resolves through a LogicalRequest, Attempt, or Phase, also define where applicable:

\[
originRequest(x)
\]

and:

\[
producerAttempt(x)
\]

A Continuation-origin State may have:

\[
producerAttempt(x)=\bot
\]

if its semantics are not tied to a particular Attempt.

This distinction is necessary because two States may share the same origin Continuation while having different producer-Attempt authority.

# 20. State Semantic Type

A reusable state object has:

\[
semanticType(x)
\]

Examples include:

```text
PREFIX
CONVERSATION
SYSTEM_CONTEXT
TOOL_CONTEXT
RETRIEVED_CONTEXT
REASONING_BRANCH
ENCODER_OUTPUT
MODEL_INTERMEDIATE
OTHER
```

Semantic type affects compatibility rules.

The first prototype may use primarily:

```text
PREFIX
CONVERSATION
```

while retaining the generic model.

---

# 21. State Representation

Define:

\[
representation(x)
\]

Examples:

```text
KV_CACHE
EMBEDDING
ENCODER_STATE
PREFIX_REPRESENTATION
OPAQUE
```

The semantic model does not require that compatibility be derived from representation identity.

Two state objects may share representation but have incompatible provenance.

---

# 22. State Derivation

Define:

\[
DerivedFrom(x_j,x_i)
\]

when reusable state \(x_j\) was constructed using state \(x_i\) as an input or ancestor.

This forms a state provenance graph:

\[
G_X=(V_X,E_X)
\]

where:

\[
V_X=\mathcal{X}
\]

and:

\[
(x_i,x_j)\in E_X
\]

means:

\[
DerivedFrom(x_j,x_i)
\]

The state graph must be acyclic for Paper 1.

---

# 23. State Identity and Regeneration

Paper 1 does not define a separate normative `StateSupersedes` relation or hidden `StateRole` identity.

The rules are instead:

### Physical reconstruction of the same logical State

If reconstruction is semantically identical to an existing logical State:

```text
same StateID
new ReplicaID
```

### Semantically new State

If reconstruction or continued execution produces semantically new reusable State:

```text
new StateID
```

### Semantically obsolete State

If an existing State must no longer be consumed:

```text
StateValidity(old) → INVALID
```

This is sufficient for Paper 1 and avoids introducing an undefined notion of “same logical State role.”

# 24. State Lifecycle and State Validity

Reusable State has two independent semantic dimensions.

## 24.1 StateLifecycle

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

Lifecycle is used by retention policy.

Define the set of known potentially reusable live dependents:

\[
Dependents(x)=
\{c\in\mathcal{C}\mid
Ancestor(originContinuation(x),c)
\land PotentiallyReusable(x,c)\}
\]

where `PotentiallyReusable` is a policy-visible predicate that does not override correctness compatibility.

Paper 1 derives lifecycle as follows:

### ACTIVE

At least one live dependent Continuation is `ACTIVE` and may reuse the State.

### WAITING

No active dependent currently justifies `ACTIVE`, but at least one live dependent is `WAITING` on an external dependency and may resume through a descendant.

### SPECULATIVE

No active or waiting dependent justifies stronger retention, but at least one live dependent is `SPECULATIVE`.

### TERMINAL

No known live dependent Continuation is expected to reuse the State.

The lifecycle derivation is intentionally simple. H5 evaluates whether exposing this semantic information is useful; it does not claim that this derivation is an optimal predictor.

## 24.2 StateValidity

```text
VALID
INVALID
```

`VALID` means the logical State may participate in compatibility evaluation.

`INVALID` means the State must not be consumed directly by any execution context.

Validity controls correctness.

Lifecycle controls retention.

Therefore:

```text
lifecycle ≠ validity
```

# 25. State Replica

A **StateReplica** represents one physical realization of a logical reusable-state object.

For state:

\[
x\in\mathcal{X}
\]

a replica:

\[
\rho
\]

contains:

```text
StateID
ReplicaID
LocationID
tier
status
binding_epoch
evidence
```

A state may have zero or more replicas.

Define:

\[
replicas(x)
\]

---

# 26. Physical Location

A physical location:

\[
l\in\mathcal{L}
\]

represents a location capable of executing work or storing reusable state.

The model uses generic attributes:

```text
LocationID
ResourceDomain
FailureDomain
Capabilities
StateTiers
Availability
```

Examples may map to:

- process;
- worker;
- node;
- accelerator;
- host-memory tier;
- remote state service.

The model does not prescribe the granularity.

---

# 27. Replica Status

A replica may be:

```text
MATERIALIZING
VALID
STALE
TRANSFERRING
EVICTING
LOST
INVALID
```

Only `VALID` replicas are ordinarily eligible for direct reuse.

`STALE` may still be useful for recomputation or restoration policies but is not considered authoritative reusable state without additional validation.

---

# 28. Execution Context and State Compatibility

State compatibility is central to Continuity.

Compatibility is evaluated against an exact **consumer execution context** rather than only a Continuation.

Define:

```text
ExecutionContext κ {
    ProgramID
    SessionID
    ContinuationID
    LogicalRequestID
    AttemptID
    PhaseID?
}
```

For State \(x\) and consumer context \(\kappa\), define:

\[
Compatible(x,\kappa)
\]

to mean:

> reusable State \(x\) may be semantically consumed by the execution represented by \(\kappa\).

Compatibility is not equivalent to:

```text
same session
same worker
same prefix length
similar cache key
same Continuation alone
```

# 29. Base Compatibility Rule

For Paper 1:

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

## Continuation compatibility

\[
ContinuationCompatible(x,\kappa)=
SameSession(x,\kappa)
\land
Ancestor(originContinuation(x),continuation(\kappa))
\]

## Producer-Attempt authority

If State \(x\) resolves to producer Attempt \(a_p\), then one of the following must hold.

### State from a completed producer request

\[
a_p=CommittedAttempt(originRequest(x))
\]

### State produced and consumed within the same current Attempt

\[
a_p=attempt(\kappa)
\]

and:

\[
authorityStatus(a_p)=CURRENT
\]

with any required Phase-ordering constraints satisfied.

A State produced by a `SUPERSEDED` Attempt is not reusable by default, even if it shares the correct Continuation ancestry.

A future explicit promotion or revalidation mechanism could relax this rule, but such a mechanism is outside Paper 1.

If `producerAttempt(x)=⊥`, producer-Attempt authority does not add a constraint; Continuation lineage, State validity, and representation-specific semantics still apply.

# 30. Branch Incompatibility

Consider:

```text
        C0
       /  \
     C1    C2
     |
     C3
```

State produced at C1 may be continuation-compatible with C3 because:

\[
Ancestor(C1,C3)
\]

But State produced at C1 is not automatically compatible with C2 because:

\[
\neg Ancestor(C1,C2)
\]

even though:

\[
session(C1)=session(C2)
\]

This formalizes:

```text
same session ≠ compatible continuation
```

A second independent rejection may occur when a State has valid branch ancestry but was produced by a superseded Attempt.

Thus Paper 1 distinguishes:

```text
branch validity
+
producer-execution validity
```

# 31. Join Compatibility

For a join Continuation:

```text
C1 ──┐
     ├── C3
C2 ──┘
```

C3 may legitimately depend on state from both C1 and C2.

Therefore ancestry remains sufficient at graph level:

\[
Ancestor(C1,C3)
\]

and:

\[
Ancestor(C2,C3)
\]

However, semantic composition may require multiple state objects.

The first paper does not require automatic state merging.

It only requires that state provenance not reject valid ancestors or accept unrelated branches.

---

# 32. Representation-Specific Compatibility

Define:

\[
SemanticValidity(x,\kappa)
\]

as a representation-specific predicate supplied by an engine/state adapter.

For example, KV State may additionally require equality of:

```text
model identity
model revision
tokenization/version
relevant prefix identity
representation version
execution semantics
```

Adapters do **not** decide Continuation ancestry or Attempt authority.

Thus:

\[
Compatible(x,\kappa)
\]

combines:

```text
Continuity-level causal lineage
+
Continuity-level producer authority
+
Continuity-level State validity
+
representation-level validity
```

This preserves provider and engine neutrality without outsourcing Continuity correctness to the adapter.

# 33. Binding

A **Binding** associates a logical subject with a physical location under a unique Binding identity and monotonically allocated generation.

A Binding:

\[
b\in\mathcal{B}
\]

contains:

```text
BindingID
subject
location
base_epoch
epoch
status
created_at
evidence
```

The subject may be:

```text
Session
Continuation
ReusableInferenceState
```

depending on policy.

`BindingID` identifies an exact binding candidate or committed binding.

`epoch` establishes ordering within the subject's binding history.

`base_epoch` records the committed epoch against which a candidate was proposed.

The first paper primarily uses Binding for:

- state ownership;
- session/continuation affinity;
- migration authority.

# 34. Binding Epoch

For each Binding subject \(y\), define:

\[
CurrentEpoch(y)\in\mathbb{N}
\]

and:

\[
CurrentBinding(y)\in\mathcal{B}\cup\{\bot\}
\]

A committed Binding is current iff:

\[
epoch(b)=CurrentEpoch(subject(b))
\]

and:

\[
BindingID(b)=BindingID(CurrentBinding(subject(b)))
\]

Candidate epochs are uniquely and monotonically allocated per subject.

A candidate is proposed against:

\[
baseEpoch(b)=CurrentEpoch(subject(b))
\]

When ownership changes, commit atomically advances both:

\[
CurrentEpoch(y)
\]

and:

\[
CurrentBinding(y)
\]

All operations associated with older committed epochs or non-winning BindingIDs become stale for ownership-sensitive commits.

# 35. Binding Lifecycle

A binding may be:

```text
PROPOSED
ACTIVE
MIGRATING
SUPERSEDED
RELEASED
INVALID
```

Only:

```text
ACTIVE
```

can authorize ordinary ownership-sensitive operations.

During:

```text
MIGRATING
```

the system may maintain both old and candidate locations, but commit rules must define which location remains authoritative.

---

# 36. Migration Semantics

Suppose subject \(y\) initially has:

```text
B7 / Location A / epoch 7 / ACTIVE
```

with:

\[
CurrentEpoch(y)=7
\]

A migration candidate may be proposed:

```text
B8 / Location B / base_epoch 7 / epoch 8 / PROPOSED
```

A concurrent candidate may receive a different monotonically allocated epoch:

```text
B9 / Location C / base_epoch 7 / epoch 9 / PROPOSED
```

Physical work for multiple candidates may overlap.

Semantic commit is serialized.

A candidate \(b\) may commit only if:

\[
baseEpoch(b)=CurrentEpoch(subject(b))
\]

and it remains eligible and has sufficient migration evidence.

If B8 commits first:

```text
CurrentEpoch(y) → 8
CurrentBinding(y) → B8
B8 → ACTIVE
B7 → SUPERSEDED
```

B9 can no longer commit because:

\[
baseEpoch(B9)=7 \neq CurrentEpoch(y)=8
\]

Every migration-sensitive completion or acknowledgment must carry at least:

```text
BindingID
BindingEpoch
```

so delayed events can be fenced to the exact candidate they concern.

# 37. Evidence

An evidence object:

\[
e\in\mathcal{E}
\]

has:

```text
EvidenceID
claim
source
authority
observed_at
valid_until
scope
confidence
status
```

The claim may concern:

- attempt status;
- state existence;
- state location;
- binding ownership;
- worker health;
- transfer completion;
- output completion.

---

# 38. Evidence Status

Evidence status is one of:

```text
VALID
STALE
UNKNOWN
FAILED
AMBIGUOUS
```

These statuses are not authority levels.

They describe whether the evidence itself is currently usable.

---

# 39. Authority Classes

Paper 1 uses the following **total authority order**:

```text
ESTIMATED
DERIVED
EXACT_OBSERVATION
AUTHORITATIVE
```

with:

\[
ESTIMATED
<
DERIVED
<
EXACT\_OBSERVATION
<
AUTHORITATIVE
\]

Authority is independent of evidence status, freshness, scope, and confidence.

`STALE`, `UNKNOWN`, `FAILED`, and `AMBIGUOUS` are Evidence status values, not authority classes.

# 40. Authority Semantics

## AUTHORITATIVE

An attestation produced by a recognized logical authority for the claim's scope.

Examples may include:

- the Continuity authority attesting the current Binding;
- a recognized state authority attesting committed ownership;
- a semantic authority attesting a committed logical transition.

`AUTHORITATIVE` evidence must not be defined circularly as “the semantic commit that this same evidence is being used to justify.”

## EXACT_OBSERVATION

Direct observation of an external fact without authority to define its semantic interpretation.

Examples:

- worker reports State present;
- runtime reports Phase completed;
- network layer reports terminal result delivered.

## DERIVED

Computed from supporting evidence through an explicit deterministic derivation rule.

## ESTIMATED

Produced from probabilistic, approximate, sampled, or heuristic information.

`confidence` may accompany `DERIVED` or `ESTIMATED` evidence as policy metadata. Confidence cannot silently raise authority.

# 41. Freshness

Define:

\[
age(e,t)=t-observedAt(e)
\]

An action or policy may define:

\[
maxAge(action)
\]

Evidence may also contain:

```text
valid_until
```

with separate semantics:

```text
valid_until
    = source/evidence-specific absolute validity bound

maxAge(action)
    = consumer/action-specific freshness bound
```

Evidence is temporally acceptable only when all applicable freshness conditions pass.

Freshness and authority are independent.

An authoritative attestation may become stale.

A fresh estimate remains estimated.

# 42. Evidence Sufficiency

Define:

\[
Sufficient(e,a,t)
\]

iff:

\[
status(e)=VALID
\]

and:

\[
authority(e)\ge requiredAuthority(a)
\]

and:

\[
age(e,t)\le maxAge(a)
\]

and any applicable `valid_until` bound has not expired, and:

\[
ScopeCompatible(scope(e),subject(a))
\]

Multiple evidence objects may jointly satisfy an action.

Therefore generalized sufficiency:

\[
Sufficient(E,a,t)
\]

for:

\[
E\subseteq\mathcal{E}
\]

may be defined by explicit policy rules.

## Minimum Paper 1 correctness requirements

The following are normative lower bounds.

| Action | Internal semantic authority | Minimum external evidence |
|---|---|---|
| Finalize LogicalRequest | Attempt is `CURRENT` for the request | `EXACT_OBSERVATION` or stronger terminal-output evidence scoped to that Attempt |
| Consume reusable State | State is `VALID`; lineage and producer authority pass | evidence sufficient to establish the selected Replica is `VALID` and usable |
| Commit migration | candidate `base_epoch` equals current committed epoch | `EXACT_OBSERVATION` or stronger evidence that destination materialization/validation completed for that exact BindingID |
| Rank endpoint | candidate has already passed correctness filtering | `ESTIMATED` evidence permitted |
| Estimate reuse benefit | candidate has already passed correctness filtering | `ESTIMATED` or `DERIVED` evidence permitted |
| Invalidate logical State | current semantic subject authority | evidence required by the explicit invalidation cause |

Implementations may require stronger evidence.

They may not silently weaken these correctness minima.

# 43. Performance-Sensitive Action

A **performance-sensitive action** is one where an incorrect prediction primarily causes:

- additional latency;
- additional recomputation;
- additional transfer;
- reduced utilization.

Examples:

```text
rank worker
estimate cache locality
select likely warm replica
```

Such actions may accept weaker evidence.

---

# 44. Correctness-Sensitive Action

A **correctness-sensitive action** is one where an incorrect decision may change authoritative logical meaning.

Examples:

```text
finalize LogicalRequest
commit binding ownership
mark migration complete
reuse state from another branch
accept completion from retry attempt
```

These actions require stronger evidence.

This distinction is policy-visible and must be explicit.

---

# 45. Output

An output:

\[
o\in\mathcal{O}
\]

contains:

```text
OutputID
AttemptID
PhaseID?
payload/reference
terminal
observed_at
evidence
```

An output belongs to exactly one Attempt.

Define:

\[
attempt(o)\in\mathcal{A}
\]

---

# 46. Candidate Completion

When an Attempt produces a terminal result:

```text
Attempt A → SUCCEEDED
```

the output becomes a **candidate completion**.

It has not yet necessarily finalized the LogicalRequest.

The Continuity Runtime evaluates:

\[
CanFinalize(r,o)
\]

---

# 47. Finalization Rule

For LogicalRequest \(r\) and output \(o\), define:

\[
CanFinalize(r,o)
\]

which requires at minimum:

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

and sufficient terminal-output evidence.

Then `Finalize(r,o)` atomically performs:

```text
LogicalRequest.status → COMPLETED
CommittedAttempt(r) → attempt(o)
CurrentAttempt(r) → ⊥
attempt(o).authority_status → COMMITTED
authoritativeOutput(r) → o
```

A duplicate application of the same valid finalization must be idempotent.

# 48. Late Attempt Example

Consider:

```text
R17
│
├── A1
│    └── delayed O1
│
└── A2
     └── O2
```

Timeline:

```text
t0 A1 execution RUNNING / authority CURRENT
t1 timeout
t2 A2 created
t3 A1 authority → SUPERSEDED; A2 authority → CURRENT
t4 A2 execution → SUCCEEDED; O2 arrives
t5 R17 finalizes with O2; A2 authority → COMMITTED
t6 A1 execution → SUCCEEDED; O1 arrives late
```

At \(t5\), A2 may finalize because it is `CURRENT` at the commit point.

At \(t6\):

```text
A1 execution_status = SUCCEEDED
A1 authority_status = SUPERSEDED
```

so:

\[
CanFinalize(R17,O1)=false
\]

O1 may be retained diagnostically but cannot mutate the completed request or become an authoritative State producer for later reuse.

# 49. Desired State

The Continuity Runtime maintains **desired semantic state**.

Examples:

```text
CurrentAttempt(R17)=A2

CommittedAttempt(R16)=A7

CurrentBinding(Session S8)=B9 / Worker W3 / epoch 9

State X17 lifecycle=WAITING
State X17 validity=VALID

Continuation C12 lifecycle=ACTIVE
```

Desired state represents committed logical truth.

# 50. Observed State

External execution produces observations.

Examples:

```text
Worker W3 reports X17 present

Worker W4 reports attempt A1 completed

state-transfer service reports X17 copied

scheduler reports W3 unhealthy
```

Observed state does not automatically overwrite desired semantic state.

It creates Evidence.

---

# 51. Reconciliation

The **Reconciler** compares:

```text
desired semantic state
```

with:

```text
evidence about observed state
```

and chooses a reconciliation outcome.

Define:

\[
Reconcile(D,E)\rightarrow Q
\]

where \(D\) is desired state, \(E\) available evidence, and \(Q\) an outcome.

---

# 52. Reconciliation Outcomes

Initial outcomes:

```text
MATCHED
WAIT
RETRY
RECOMPUTE
MIGRATE
REJECT
REPAIR
FAIL
AMBIGUOUS
```

## MATCHED

Observed evidence sufficiently confirms desired state.

## WAIT

Evidence may become sufficient through normal propagation.

## RETRY

The logical operation should generate a new execution attempt.

## RECOMPUTE

Reusable state cannot safely be used and should be regenerated.

## MIGRATE

State or execution ownership should move.

## REJECT

The observed event is incompatible with authoritative state.

Example:

```text
late completion from superseded attempt
```

## REPAIR

Control metadata should be restored or reconciled.

## FAIL

No acceptable recovery exists under current policy.

## AMBIGUOUS

Available evidence supports multiple incompatible semantic interpretations.

For correctness-sensitive actions:

```text
AMBIGUOUS
```

cannot be treated as:

```text
MATCHED
```

---

# 53. Reconciliation Monotonicity

Where required by the semantics, authoritative knowledge evolves monotonically.

Examples:

```text
Attempt authority SUPERSEDED
```

must never become:

```text
Attempt authority CURRENT
```

and:

```text
Binding epoch 7 superseded
```

must never become current again.

Likewise:

```text
LogicalRequest COMPLETED
```

must not be replaced by a late result, and:

```text
StateValidity = INVALID
```

must not return to `VALID` for the same logical StateID under Paper 1.

This monotonicity reduces distributed ambiguity without requiring global event ordering.

# 54. Semantic Commit

A **semantic commit** is any operation that changes authoritative logical state.

Examples:

```text
set CurrentAttempt
finalize request / set CommittedAttempt
advance CurrentBinding and CurrentEpoch
mark State INVALID
commit migration
mark Continuation terminal
mark Program terminal
```

Every semantic commit must be:

1. attributable to an explicit logical entity;
2. authorized by current Attempt/Binding/subject state;
3. supported by sufficient evidence where external observation is required;
4. idempotent or protected against duplicate application;
5. revalidated at the commit point rather than trusting stale prior checks.

# 55. Event Identity

Every externally produced event relevant to semantic state must carry enough identity to resolve its exact semantic scope.

Conceptually:

```text
EventID
ProgramID?
SessionID?
ContinuationID?
LogicalRequestID?
AttemptID?
PhaseID?
StateID?
BindingID?
BindingEpoch?
```

Not every event requires every identifier.

The rule is:

> An event must contain sufficient identity to prevent it from being confused with an event from another logical scope or another concurrent Binding candidate.

Migration-sensitive events must carry both `BindingID` and `BindingEpoch`.

# 56. Event Idempotence

Duplicate delivery is assumed possible.

Therefore a semantic event must be:

- idempotently applicable;
- or deduplicated by EventID;
- or protected by monotonic generation state.

Example:

```text
Finalize R17 with O2
```

received twice must not cause two logical completions.

---

# 57. Temporal Ordering

The model does not assume globally synchronized clocks.

Timestamps may be used for:

- observability;
- timeout policy;
- freshness estimation.

They must not be the sole source of causal identity.

Therefore:

```text
event observed later
```

does not imply:

```text
event causally newer
```

Authoritative ordering should use:

- graph relations;
- Attempt generation;
- Binding epoch;
- explicit supersession;
- semantic commits.

---

# 58. Failure Assumptions

The Continuity Model is designed to tolerate:

- delayed events;
- duplicate events;
- reordered events;
- worker crashes;
- stale observations;
- late results;
- retries;
- partial state migration;
- cache eviction;
- lost physical state.

The model does not initially attempt to tolerate:

- Byzantine workers;
- malicious falsification of authoritative evidence;
- arbitrary data corruption that cannot be detected;
- consensus failure in the Continuity authority itself.

These assumptions will be specified more completely in `04-failure-model.md`.

---

# 59. State Reuse Decision

For consumer execution context \(\kappa\), candidate State \(x\), and Replica \(\rho\), define:

\[
Reusable(x,\rho,\kappa,t)
\]

iff all of the following hold:

\[
Compatible(x,\kappa)
\]

\[
\rho\in replicas(x)
\]

\[
status(\rho)=VALID
\]

and available evidence for the Replica satisfies the action's required authority, status, freshness, and scope.

A scheduler may then estimate:

\[
ReuseBenefit(x,\rho,\kappa)
\]

for performance ranking.

Correctness decides **whether reuse is allowed**.

Performance policy decides **whether reuse is worthwhile**.

These remain separate.

# 60. Routing Decision Model

Let:

\[
L_\kappa
\]

be candidate execution locations for consumer execution context \(\kappa\).

For each location \(l\), define performance cost:

\[
J(\kappa,l)
\]

which may contain:

\[
QueueCost
+
RecomputeCost
+
TransferCost
+
MigrationCost
+
ExecutionCost
\]

Continuity constrains candidates before optimization.

Define:

\[
ValidLocations(\kappa)
\subseteq
L_\kappa
\]

based on correctness requirements, including any State selected for reuse.

Then the scheduler chooses:

\[
l^*
=
\arg\min_{l\in ValidLocations(\kappa)}
J(\kappa,l)
\]

Thus Continuity does not replace scheduling.

It constrains and enriches scheduling.

# 61. Retention Model

Each State object exposes its derived:

```text
StateLifecycle
```

and may additionally carry policy-specific:

\[
retentionIntent(x)
\]

Policy may assign retention priority:

\[
priority(x)
=
f(
lifecycle,
reuseProbability,
recomputeCost,
stateSize,
memoryPressure
)
\]

`StateValidity` is not a retention preference. An `INVALID` State is excluded from direct reuse regardless of retention score.

The Continuity Model requires lifecycle information to be available and deterministically derivable from known dependents. The exact priority function belongs to policy.

# 62. State Eviction

Eviction changes physical availability, not logical history.

If replica \(\rho\) is evicted:

```text
Replica ρ → EVICTING → LOST
```

The logical State \(x\) may remain known.

If all replicas disappear:

\[
|validReplicas(x)|=0
\]

the state remains logically known but physically unavailable.

It may later be:

```text
RECOMPUTED
RESTORED
```

as a new physical replica.

---

# 63. Recomputed State

A recomputation of logical state may produce a new state object or a new replica depending on semantic equivalence.

Paper 1 will use the following rule:

If reconstruction is semantically identical to existing logical state:

```text
same StateID
new ReplicaID
```

If reconstruction produces a semantically new continuation state:

```text
new StateID
```

This distinction must be deterministic in adapters.

---

# 64. Program-Level Attribution and Completion

Every Attempt and reusable State must be attributable upward to:

```text
Continuation
Session
Program
```

This enables metrics such as:

```text
Program Completion Time
State reuse per Program
recomputation per Program
```

Program lifecycle is:

```text
CREATED
RUNNING
COMPLETED
FAILED
CANCELLED
```

The application/workload supplies `ObjectiveSatisfied(p)`.

The Continuity Runtime records the resulting Program terminal transition but does not perform global Program scheduling in Paper 1.

For synthetic DAG workloads, the manifest must declare the objective condition, such as:

```text
all required terminal Continuations completed
```

or a declared join objective.

# 65. Continuity Context

For integration purposes, define a conceptual:

```text
ContinuityContext
```

containing the minimum identity necessary for an inference operation.

Conceptually:

```text
ProgramID
SessionID
ContinuationID
LogicalRequestID
AttemptID
BindingID?
BindingEpoch?
```

Optional:

```text
PhaseID
ContinuationLifecycle
Objective
```

The corresponding internal consumer `ExecutionContext` used for compatibility contains the resolved entities represented by these identifiers.

This is a semantic object, not a prescribed protocol header.

An adapter may encode it using:

- RPC metadata;
- message attributes;
- structured request fields;
- tracing context;
- local shared state.

# 66. Minimal C1 Data Model

The deterministic Continuity Core must at minimum implement:

```text
Program
Session
Continuation
LogicalRequest
Attempt
ReusableInferenceState
StateReplica
Binding
Evidence
Output
ExecutionContext
```

and relations/attributes:

```text
contains
parent-of
originates-from
derived-from
supersedes
current-attempt
committed-attempt
producer-attempt
located-at
bound-to
supported-by
StateLifecycle
StateValidity
AttemptAuthority
AttemptExecutionStatus
```

Phases may initially be implemented minimally but must remain representable.

# 67. Minimal C1 State Machines

C1 must implement state machines or monotonic state dimensions for:

## Program

```text
CREATED
RUNNING
COMPLETED
FAILED
CANCELLED
```

## LogicalRequest

```text
CREATED
READY
RUNNING
COMPLETED
FAILED
CANCELLED
```

## Attempt ExecutionStatus

```text
CREATED
DISPATCHED
RUNNING
SUCCEEDED
FAILED
CANCELLED
```

## AttemptAuthority

```text
NONE
CURRENT
COMMITTED
SUPERSEDED
```

## Continuation

Minimum:

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

## StateLifecycle

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

## StateValidity

```text
VALID
INVALID
```

## Replica

```text
MATERIALIZING
VALID
STALE
TRANSFERRING
EVICTING
LOST
INVALID
```

## Binding

```text
PROPOSED
ACTIVE
MIGRATING
SUPERSEDED
RELEASED
INVALID
```

# 68. Required C1 Queries

The deterministic core must answer at least:

```text
current_attempt(request_id)
committed_attempt(request_id)

attempt_execution_status(attempt_id)
attempt_authority(attempt_id)
is_attempt_current(attempt_id)

ancestors(continuation_id)
is_ancestor(a, b)

state_origin(state_id)
state_producer_attempt(state_id)
state_lifecycle(state_id)
state_validity(state_id)
state_compatible(state_id, execution_context)

valid_replicas(state_id)

current_binding(subject_id)
current_epoch(subject_id)

evidence_sufficient(action, evidence_set)

can_finalize(request_id, output_id)
program_complete(program_id)
reconcile(subject_id)
```

These queries form the first executable expression of the normalized model.

# 69. Required C1 Mutations

The deterministic core must support:

```text
create_program()
set_program_status()

create_session()
create_continuation()
create_request()

start_attempt()
supersede_attempt()
complete_attempt()
finalize_request()

create_state()
derive_state()
set_state_validity()
recompute_state_replica()

add_replica()
invalidate_replica()
evict_replica()

propose_binding()
commit_binding()
begin_migration()
commit_migration()

record_evidence()
set_continuation_lifecycle()
```

All mutations affecting authoritative semantic state must enforce the model's invariants.

# 70. Invalid Operations

The core must reject at least:

```text
finalize with a SUPERSEDED Attempt

reuse State from an unrelated branch

reuse State produced by a SUPERSEDED Attempt

consume logical State with StateValidity=INVALID

activate an older Binding epoch

commit a migration candidate whose base_epoch is stale

apply a migration event for the wrong BindingID

mutate a COMPLETED request from a late result

reactivate a TERMINAL Continuation

use ambiguous or insufficient evidence for a correctness-sensitive commit
```

These become direct C1 property tests.

# 71. Reference Example

Consider:

```text
Program P1
└── Session S1
    └── C0
         │
         ├── R1
         │    └── A1
         │         └── produces X0
         │
         ├── C1
         │    ├── R2
         │    │    ├── A2
         │    │    └── A3
         │    │
         │    └── produces X1
         │
         └── C2
```

Relations:

\[
Ancestor(C0,C1)
\]

\[
Ancestor(C0,C2)
\]

but:

\[
\neg Ancestor(C1,C2)
\]

Therefore X0 from C0 may be continuation-compatible with C1 and C2, subject to producer authority, State validity, and representation validity.

X1 from C1 is not compatible with C2.

Now suppose:

```text
A2 execution RUNNING / authority CURRENT
A3 starts
A2 authority → SUPERSEDED
A3 authority → CURRENT
A2 later execution → SUCCEEDED
```

An output from A2 cannot finalize R2.

Any State whose producer resolves to A2 is also rejected for later cross-request reuse by default because its producer Attempt is `SUPERSEDED`.

This single example exercises:

- Continuation ancestry;
- State compatibility;
- producer-Attempt authority;
- retry supersession;
- result fencing.

# 72. Reference Migration Example

Assume subject S1 has initial committed Binding:

```text
B4 / Worker W1 / epoch 4 / ACTIVE
```

A migration candidate is proposed:

```text
B5 / Worker W2 / base_epoch 4 / epoch 5
```

Evidence:

```text
E1:
claim = destination replica materialized for B5
source = W2/state runtime
authority = EXACT_OBSERVATION
status = VALID
scope = {BindingID=B5, epoch=5, StateID=X0}
```

Only after all migration requirements are satisfied may the semantic authority atomically commit:

```text
CurrentBinding(S1) = B5
CurrentEpoch(S1) = 5
B5 = ACTIVE
B4 = SUPERSEDED
```

A delayed message for:

```text
BindingID = B4
epoch = 4
```

cannot restore ownership.

A different uncommitted candidate based on epoch 4 also cannot commit after B5 because its `base_epoch` is stale.

# 73. Reference Ambiguity Example

Suppose two observations claim:

```text
E1:
State X on W1
epoch 8

E2:
State X on W2
epoch 8
```

and neither source is authoritative enough to establish ownership.

The runtime may know:

```text
X probably exists
```

while still not knowing:

```text
which location owns X
```

Therefore reconciliation result:

```text
AMBIGUOUS
```

A performance-sensitive operation might choose recomputation.

A correctness-sensitive ownership operation must not arbitrarily choose W1 or W2.

This distinction is essential.

---

# 74. Semantic Guarantees of the Model

If implemented correctly under the stated failure assumptions, the normalized model is designed to provide:

## G1

No `SUPERSEDED` Attempt can finalize a LogicalRequest.

## G2

A completed LogicalRequest records exactly one `CommittedAttempt` as its authoritative producer.

## G3

No unrelated-branch State can be accepted by Continuation lineage.

## G4

No State produced by a `SUPERSEDED` Attempt can be reused by default merely because Continuation ancestry matches.

## G5

No `INVALID` logical State can be directly reused.

## G6

No stale Binding epoch or non-winning BindingID can regain authoritative ownership.

## G7

No correctness-sensitive semantic commit can proceed from explicitly insufficient or ambiguous evidence.

## G8

Duplicate or reordered observations cannot reverse monotonic authoritative transitions.

## G9

Logical State remains distinguishable from physical replicas and their lifetimes.

These are design guarantees.

`03 — Invariants.md` states them as explicit invariant obligations and test requirements.

# 75. Deliberately Unresolved Items

The following remain outside this document and must not be silently assumed.

## U1 — Exact representation compatibility for each inference engine

Continuity defines causal/authority compatibility.

Engine adapters define model/representation validity.

## U2 — Global ordering across independent Sessions

Not required.

## U3 — Consensus among multiple Continuity authorities

Paper 1 assumes one logical authority for semantic commits.

## U4 — Byzantine behavior

Out of scope.

## U5 — Exact physical State-transfer protocol

Adapter-specific.

## U6 — Automatic join-State composition

Not required for Paper 1.

## U7 — Program critical-path scheduling

Future work.

## U8 — Optimal retention policy or optimal lifecycle prediction

Experimental policy, not semantic requirement.

## U9 — Promotion of State produced by a superseded Attempt

Paper 1 rejects such State by default. A future authority-transfer/revalidation protocol could study safe promotion.

# 76. C0 Acceptance Criteria for This Model

This Continuity Model is stable enough for C1 when:

1. Every canonical entity has an unambiguous semantic role.
2. Identity scopes do not overlap ambiguously.
3. Program completion is minimally well-defined for evaluation.
4. Continuation ancestry is formally defined.
5. State origin resolves to a Continuation and, where applicable, a producer Attempt.
6. Compatibility separates Continuation lineage, producer authority, State validity, and engine-specific representation validity.
7. Attempt execution outcome is distinct from Attempt semantic authority.
8. `CurrentAttempt` and `CommittedAttempt` are deterministic.
9. Binding epochs and Binding candidate identities are monotonic and fence concurrent candidates.
10. Evidence authority is distinguishable from evidence status, freshness, scope, and confidence.
11. Minimum correctness-sensitive evidence requirements are explicit.
12. Tool waits have one canonical Paper 1 semantic representation.
13. Reconciliation has explicit non-success outcomes.
14. Authoritative state transitions are monotonic where required.
15. Every Paper 1 safety property can be expressed against this model.
16. C1 can be implemented without introducing additional hidden semantic entities.

# 77. Canonical Summary

Continuity-Aware Distributed Inference models distributed serving as the interaction of three evolving structures:

```text
Execution Graph
      ↕
State Graph
      ↕
Resource Graph
```

The semantic chain is:

```text
Program
  ↓
Session
  ↓
Continuation
  ↓
LogicalRequest
  ↓
Attempt
  ↓
Phase
```

The normalized Attempt model separates:

```text
physical execution outcome
        ≠
semantic execution authority
```

Reusable State is attached to execution through explicit provenance and separates:

```text
State lifecycle
        ≠
State validity
```

Physical replicas are attached separately through location relationships.

Correctness requires agreement among:

```text
current/committed execution authority
+
compatible causal State lineage
+
valid producer Attempt
+
valid logical State
+
current Binding candidate/generation
+
sufficient scoped evidence
```

before a correctness-sensitive semantic commit is accepted.

Therefore the core Continuity principle becomes:

> **Distributed inference may optimize over physical placement and approximate observations, but semantic commits must remain anchored to explicit logical identity, causal lineage, producer authority, current ownership generation, State validity, and sufficient evidence.**

