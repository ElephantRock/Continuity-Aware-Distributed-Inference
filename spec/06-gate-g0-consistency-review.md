# 06 — Gate G0 Consistency Review
## C0 Coherence Audit and Required Amendments

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** Gate G0  
**Documents reviewed:** `01 — Research Thesis`, `02 — Continuity Model`, `03 — Invariants`, `04 — Failure Model`, `05 — Experimental Plan`  
**Original verdict:** **HOLD — amendments required before C1**  
**C0.1 re-run verdict:** **PASS — blockers resolved; C1 authorized**

---

# 1. Executive Verdict

This document preserves the original Gate G0 audit. The original C0 research specification was **conceptually coherent but not yet implementation-closed**.

The C0.1 normalization patch has now been applied to the canonical specifications. The re-run result is recorded in Sections 27–29.

The review finds no reason to reject the central Continuity thesis.

The following foundational choices are consistent across the specification:

- provider-, engine-, gateway-, and orchestrator-neutrality;
- explicit Program → Session → Continuation → LogicalRequest → Attempt → Phase identity;
- separation of logical State from physical replicas;
- causal State lineage;
- explicit execution authority;
- fail-closed correctness semantics;
- evidence-aware reconciliation;
- monotonic Binding generations;
- separation of safety from availability;
- CPU-first experimental validation;
- trace-driven plus synthetic workload evaluation;
- explicit falsifiability;
- four-policy Paper 1 scope.

However, several definitions remain insufficiently precise for two independent implementers to produce materially equivalent C1 semantics.

Therefore:

\[
Gate\ G0 = HOLD
\]

not:

\[
Gate\ G0 = FAIL
\]

The thesis remains viable.

The specification requires a **C0.1 normalization patch**.

---

# 2. Severity Classes

Issues are classified as:

## BLOCKER

C1 implementation could encode materially different semantics depending on interpretation.

Must be resolved before coding.

## REQUIRED NORMALIZATION

The intended semantics are inferable, but terminology or representation is inconsistent.

Should be corrected before C1.

## DEFERRED

Not required to implement the deterministic kernel but must be resolved before the relevant later milestone.

---

# 3. G0-B1 — Attempt Execution State and Attempt Authority Are Conflated

**Severity:** BLOCKER

The current Attempt state machine uses:

```text
CREATED
DISPATCHED
RUNNING
SUCCEEDED
FAILED
CANCELLED
SUPERSEDED
```

This combines two different dimensions:

### Physical execution state

What happened to the execution?

### Semantic authority

May this execution still affect the LogicalRequest?

These are not the same.

The specification itself requires the following state to be representable:

```text
A1:
    physically SUCCEEDED
    semantically SUPERSEDED
```

because a timed-out Attempt may finish successfully after A2 has become authoritative.

A single enum cannot represent both simultaneously.

## Required amendment

Replace the Attempt status with two orthogonal dimensions.

### ExecutionStatus

```text
CREATED
DISPATCHED
RUNNING
SUCCEEDED
FAILED
CANCELLED
```

### AttemptAuthority

```text
CURRENT
COMMITTED
SUPERSEDED
```

Potentially:

```text
NONE
```

before authority is assigned.

Example:

```text
A1:
    execution_status = SUCCEEDED
    authority = SUPERSEDED

A2:
    execution_status = RUNNING
    authority = CURRENT
```

After successful request finalization:

```text
A2:
    execution_status = SUCCEEDED
    authority = COMMITTED
```

This introduces an important historical distinction:

```text
CurrentAttempt(r)
```

versus:

```text
CommittedAttempt(r)
```

A completed request should retain the identity of the Attempt that authoritatively produced its result.

## Revised finalization transition

Before finalization:

```text
R17:
    status = RUNNING

A2:
    execution = SUCCEEDED
    authority = CURRENT
```

Atomic commit:

```text
R17.status → COMPLETED
CommittedAttempt(R17) → A2
CurrentAttempt(R17) → ⊥
A2.authority → COMMITTED
```

Any other Attempt belonging to R17 remains:

```text
SUPERSEDED
```

This distinction will also be needed for authoritative State provenance.

---

# 4. G0-B2 — State Compatibility Is Too Coarse

**Severity:** BLOCKER

Current compatibility is approximately:

\[
Compatible(x,c)=
SameSession(x,c)
\land
Ancestor(originContinuation(x),c)
\land
SemanticValidity(x,c)
\]

This correctly handles sibling-branch incompatibility.

It does **not** fully handle State whose origin is:

```text
LogicalRequest
Attempt
Phase
```

Consider:

```text
Continuation C1

LogicalRequest R1
├── A1 — superseded
└── A2 — committed
```

Suppose State X1 originated from A1.

Then:

\[
originContinuation(X1)=C1
\]

and therefore continuation ancestry alone can accept X1 for later C1 descendants.

That would allow State produced by a superseded execution to pass the Continuity-level lineage check.

Relying on the engine adapter's `SemanticValidity` predicate to reject this would incorrectly move an execution-continuity responsibility into engine-specific semantics.

## Required amendment

Compatibility must be evaluated against a **consumer execution context**, not only a Continuation.

Define:

```text
ExecutionContext κ {
    program
    session
    continuation
    logical_request
    attempt
    phase?
}
```

Then:

\[
Compatible(x,\kappa)
=
ContinuationCompatible(x,\kappa)
\land
ProducerAuthorityValid(x,\kappa)
\land
StateValid(x)
\land
SemanticValidity(x,\kappa)
\]

### ContinuationCompatible

\[
SameSession(x,\kappa)
\land
Ancestor(originContinuation(x),continuation(\kappa))
\]

### ProducerAuthorityValid

For State originating from a completed LogicalRequest:

\[
producerAttempt(x)
=
CommittedAttempt(originRequest(x))
\]

For State created and consumed within the current Attempt:

\[
producerAttempt(x)
=
attempt(\kappa)
\]

subject to valid Phase ordering.

State from a superseded Attempt is not reusable by default.

An explicit future promotion/validation mechanism may relax this, but it is outside Paper 1.

## Result

Continuity itself now enforces:

```text
correct branch
+
correct producing execution
+
correct State validity
+
engine representation compatibility
```

rather than only:

```text
correct branch
+
adapter validity
```

---

# 5. G0-B3 — State Lifecycle and State Validity Are Conflated

**Severity:** BLOCKER

Current reusable-State lifecycle contains:

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
INVALID
```

`INVALID` is not a lifecycle state.

It is a validity state.

The same conceptual problem occurs as with Attempt authority.

A State may logically be:

```text
lifecycle = TERMINAL
validity = VALID
```

because it remains a valid artifact even though no current live continuation is expected to need it.

A State may also become:

```text
lifecycle = ACTIVE
validity = INVALID
```

during a detected semantic invalidation before cleanup.

## Required amendment

Split into:

### StateLifecycle

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

### StateValidity

```text
VALID
INVALID
```

Then:

\[
Compatible(x,\kappa)
\Rightarrow
StateValidity(x)=VALID
\]

Lifecycle influences retention.

Validity controls semantic reuse.

This produces the clean separation:

```text
Lifecycle
    → retention policy

Validity
    → correctness
```

---

# 6. G0-B4 — State Lifecycle Derivation Is Undefined

**Severity:** BLOCKER

Paper 1 relies on lifecycle-aware retention for H5.

However, the specification does not yet define how a State obtains:

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

A State's lifecycle cannot simply equal the lifecycle of its origin Continuation.

Example:

```text
C0 → State X0
      |
      └── C1 ACTIVE
```

C0 may itself be terminal while X0 remains valuable because active descendants reuse it.

## Required amendment

Define State lifecycle relative to **live dependent continuations**.

Conceptually:

```text
Dependents(x)
=
live Continuations for which x remains lineage-compatible
and potentially reusable
```

Then an initial Paper 1 policy may derive:

### ACTIVE

At least one active dependent Continuation is expected to reuse X.

### WAITING

No active dependent currently executes, but at least one live dependent is waiting on an external event.

### SPECULATIVE

Only speculative live dependents currently justify retention.

### TERMINAL

No known live dependent Continuation is expected to reuse X.

This need not be an optimal predictor.

It must merely define what lifecycle labels mean before H5 is evaluated.

---

# 7. G0-B5 — State Supersession Uses an Undefined “Logical State Role”

**Severity:** BLOCKER

The model introduces:

\[
StateSupersedes(x_j,x_i)
\]

when two State objects represent the same “logical state role.”

No formal `StateRole` identity exists.

That creates a hidden semantic entity, violating C1's own requirement that implementation introduce no semantic identity absent from the formal model.

Furthermore, State supersession is not currently included in the compatibility predicate, so a logically obsolete State may remain ancestry-compatible.

## Recommended Paper 1 resolution

Remove normative `StateSupersedes` semantics from Paper 1.

Use:

```text
StateID
provenance
validity
replicas
```

If old State becomes semantically unusable:

```text
StateValidity(old) → INVALID
```

If a semantically new State is produced:

```text
new StateID
```

If the same logical State is merely reconstructed physically:

```text
same StateID
new ReplicaID
```

This is sufficient for Paper 1 and avoids introducing an undefined `StateRoleID`.

State-version/supersession semantics can return in later work if required.

---

# 8. G0-B6 — Binding Epochs Do Not Fully Fence Concurrent Migration Candidates

**Severity:** BLOCKER

The specification correctly requires monotonic committed Binding epochs.

It also permits concurrent migration candidates.

The current rule:

\[
candidateEpoch=CurrentEpoch+1
\]

allows two candidates initiated from the same current Binding to receive the same prospective epoch unless proposals are serialized.

Example:

```text
Current:
W1 / epoch 7

Candidate B1:
W2 / epoch 8

Candidate B2:
W3 / epoch 8
```

Epoch alone can no longer distinguish the two candidates.

## Required amendment

Every ownership-sensitive migration message must be scoped by:

```text
BindingID
+
BindingEpoch
```

and each candidate Binding must be unique.

A robust candidate model is:

```text
Binding {
    binding_id
    subject
    location
    base_epoch
    epoch
    status
}
```

A candidate is created against:

```text
base_epoch = CurrentEpoch(subject)
```

Candidate epochs are uniquely and monotonically allocated per subject.

For example:

```text
Current:
B7 / W1 / epoch 7

Candidate:
B8 / W2 / base_epoch 7 / epoch 8

Concurrent candidate:
B9 / W3 / base_epoch 7 / epoch 9
```

Commit requires:

```text
candidate.base_epoch == CurrentEpoch(subject)
```

plus:

```text
candidate is still eligible
sufficient migration evidence exists
```

Once one candidate commits, all candidates based on the old epoch fail their commit precondition.

Every delayed migration event must carry at least:

```text
BindingID
BindingEpoch
```

This closes the concurrent-candidate fencing hole without adding a new `MigrationID` entity.

---

# 9. G0-B7 — Core Evidence Thresholds Are Not Yet Deterministic

**Severity:** BLOCKER

The model defines:

\[
Sufficient(E,a,t)
\]

using:

```text
requiredAuthority(a)
maxAge(a)
scope
status
```

but the core Paper 1 actions do not yet have canonical minimum evidence requirements.

Therefore two C1 implementations could both satisfy the prose while making different correctness decisions.

There is also a potential circularity if `AUTHORITATIVE` evidence is interpreted as something created by the semantic commit that it is simultaneously required to authorize.

## Required amendment

Separate:

### Authoritative semantic state

Examples:

```text
CurrentAttempt(R)
CommittedAttempt(R)
CurrentBinding(S)
StateValidity(X)
```

from:

### Evidence about external reality

Examples:

```text
worker observed output
destination observed State materialized
replica observed present
```

Semantic authority is not merely another external observation.

Then define a minimum Paper 1 action table.

| Action | Internal authority required | External evidence minimum |
|---|---|---|
| Finalize request | Attempt is CURRENT | exact terminal-output observation scoped to that Attempt |
| Consume local/remote State | State is causally compatible and VALID | evidence sufficient to establish selected replica is usable |
| Commit migration | candidate based on current Binding | exact/validated destination materialization evidence |
| Rank endpoint | none beyond valid candidate set | estimated evidence permitted |
| Estimate reuse benefit | none beyond valid candidate set | estimated/derived evidence permitted |
| Invalidate State | current semantic subject authority | evidence defined by invalidation cause |

The exact implementation can later refine thresholds upward.

It may not weaken the correctness minimum silently.

## Evidence hierarchy

Keep:

```text
ESTIMATED
DERIVED
EXACT_OBSERVATION
AUTHORITATIVE
```

if desired, but define `AUTHORITATIVE` as an attestation from a recognized authority—not as the commit being justified by itself.

---

# 10. G0-B8 — Evidence Taxonomy Is Inconsistent Across C0

**Severity:** REQUIRED BEFORE C1

The Thesis currently lists:

```text
AUTHORITATIVE
EXACT_OBSERVATION
DERIVED
ESTIMATED
STALE
UNKNOWN
FAILED
```

as one set of “evidence classes.”

The Continuity Model correctly separates them.

## Canonical taxonomy

### Authority

```text
ESTIMATED
DERIVED
EXACT_OBSERVATION
AUTHORITATIVE
```

### Status

```text
VALID
STALE
UNKNOWN
FAILED
AMBIGUOUS
```

### Freshness

Independent temporal predicate:

\[
age(e,t)\le maxAge(action)
\]

### Scope

Independent semantic relation.

### Confidence

Optional policy metadata.

It must not silently alter authority.

## Additional normalization

The model currently contains both:

```text
valid_until
```

and:

```text
maxAge(action)
```

Define them as:

```text
valid_until
    = source/evidence-specific absolute validity bound

maxAge(action)
    = consumer/action-specific freshness bound
```

Evidence is fresh only when both applicable conditions pass.

Otherwise remove one of them.

The Research Thesis and North Star should be updated to match this taxonomy.

---

# 11. G0-B9 — Tool-Wait Semantics Exist at Two Different Levels

**Severity:** BLOCKER

The specification currently represents tool waiting as both:

```text
Continuation lifecycle = WAITING
```

and a possible:

```text
Phase type = TOOL_WAIT
```

The experimental workload also represents:

```text
C1 WAITING
    ↓ tool return
C2 resume
```

while the Continuity lifecycle permits:

```text
WAITING → ACTIVE
```

on the same Continuation.

These alternatives encode different lineage semantics.

## Required Paper 1 decision

Use **Continuation lifecycle** for external tool gaps.

Canonical sequence:

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

State retained while C1 is waiting remains ancestor-compatible with C2.

This makes tool-return a genuine continuation event and directly supports H5.

For Paper 1:

```text
TOOL_WAIT
```

should be removed from Attempt Phase types, or explicitly declared unused unless a particular adapter models tool execution as part of the same LogicalRequest.

This avoids two independent representations of the same semantic gap.

---

# 12. G0-B10 — Program Completion Is Measured but Not Formally Defined

**Severity:** BLOCKER FOR RQ7, not for initial C1 primitives

The Experimental Plan defines:

\[
PCT =
t_{program\ terminal}
-
t_{program\ start}
\]

but the Continuity Model has no Program lifecycle or `ProgramComplete` predicate.

Program identity exists only for attribution.

Likewise, fan-in metrics use join readiness while join completion semantics remain deliberately minimal.

## Required amendment

Add a minimal Program lifecycle:

```text
CREATED
RUNNING
COMPLETED
FAILED
CANCELLED
```

The Continuity Runtime does not need to schedule Programs globally.

The workload/application declares which terminal conditions satisfy a Program.

Conceptually:

\[
ProgramComplete(p)
\iff
ObjectiveSatisfied(p)
\]

where `ObjectiveSatisfied` is supplied by the workload/application.

For synthetic DAG workloads this may mean:

```text
all required terminal Continuations completed
```

For join workloads, the workload defines the required parent set.

This is sufficient to measure PCT without expanding Paper 1 into program-aware scheduling.

---

# 13. G0-B11 — B3 and the Wrong-State Experiment Need a Stronger Information Contract

**Severity:** BLOCKER FOR EXPERIMENTAL VALIDITY

B3 is defined as:

```text
precise State location
without causal Continuation lineage
```

This is insufficient to determine what B3 actually knows when selecting State.

Two interpretations produce radically different results.

### Interpretation A

B3 is told the exact desired `StateID`.

Then it can locate the correct State without Continuation lineage.

The wrong-branch experiment may disappear.

### Interpretation B

B3 only sees available State objects and locality.

Then deliberately allowing it to choose an obviously unrelated object may create an artificially weak baseline.

The specification's baseline-fairness rule correctly prohibits this.

## Required amendment

Before C3, every baseline needs an explicit **information contract**:

```text
identity fields available
State keys available
State-location information
causal information
attempt information
freshness information
ownership information
```

For B3 specifically, define the mechanism by which it decides that a State is a candidate for reuse.

The experiment must expose a realistic distinction between:

```text
physical/content State identification
```

and:

```text
causal validity for current continuation
```

rather than merely withholding an exact StateID.

## Consequence for H2

Until this contract is fixed and later grounded against real systems/prior work, the sibling-branch stress case demonstrates the **mechanism** but not yet external validity.

This does not invalidate H2.

It prevents overstating it.

---

# 14. G0-B12 — Evaluation Evidence and Runtime Evidence Use the Same “E” Vocabulary

**Severity:** REQUIRED NORMALIZATION

The runtime model uses:

```text
Evidence
\mathcal{E}
authority
status
freshness
```

The Experimental Plan separately uses:

```text
E0
E1
E2
E3
E4
```

for methodological evidence layers.

This is not formally contradictory but is unnecessarily confusing in a paper whose core contribution includes evidence semantics.

## Required amendment

Rename experimental evidence layers.

Recommended:

```text
EV0 — deterministic semantics
EV1 — measured CPU distributed
EV2 — trace-derived
EV3 — calibrated simulation
EV4 — optional accelerator measurement
```

or:

```text
VAL0–VAL4
```

Reserve:

```text
Evidence
E
\mathcal{E}
```

for runtime evidence semantics.

---

# 15. G0-R1 — Authority Ordering Terminology

**Severity:** REQUIRED NORMALIZATION

The model displays:

\[
ESTIMATED
<
DERIVED
<
EXACT\_OBSERVATION
<
AUTHORITATIVE
\]

and calls it a “default partial order.”

As written, this is a total linear ordering of the listed classes.

Either:

- call it a total order for Paper 1; or
- introduce incomparable authority dimensions if a partial order is genuinely intended.

Paper 1 does not currently require the added complexity of a partial order.

Recommendation:

> Use a total authority order for Paper 1.

---

# 16. G0-R2 — `confidence` Is Present but Semantically Unused

**Severity:** REQUIRED NORMALIZATION

`Evidence` contains:

```text
confidence
```

but `Sufficient(...)` does not use it.

Recommendation:

Declare:

> Confidence is policy metadata for ESTIMATED/DERIVED evidence and cannot raise evidence authority or authorize a correctness-sensitive action unless an explicit policy rule says so.

This preserves the field without adding hidden semantics.

---

# 17. G0-R3 — Reconciliation Vocabulary Should Be Canonical

**Severity:** REQUIRED NORMALIZATION

Across the documents, similar outcomes appear as:

```text
continue
reconstruct
restore
MATCHED
RECOMPUTE
```

Use one canonical set:

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

`RESTORE` may be an implementation action under `REPAIR` or `RECOMPUTE`, rather than a separate semantic reconciliation outcome unless formally introduced.

---

# 18. G0-R4 — H1 Must Not Be Presented as Standalone Novelty

**Severity:** REQUIRED RESEARCH POSITIONING

The Thesis already states that identifiers, generations, fencing tokens, and related individual mechanisms are not claimed as novel.

Preserve that discipline.

Attempt fencing should be positioned as:

```text
required component of the Continuity composition
```

rather than:

```text
independently novel retry mechanism
```

If competent serving baselines already provide equivalent retry fencing, that strengthens baseline quality rather than harming the overall composition thesis.

The main novelty burden should remain on:

```text
execution authority
+
causal State lineage
+
evidence semantics
+
reconciliation
```

as a unified inference abstraction.

---

# 19. Gate G0 Question Results

## 1. Provider-neutral definition

**PASS**

No core semantic primitive requires Kubernetes, a particular cloud, or a specific inference engine.

---

## 2. Identity hierarchy is non-overlapping

**PASS WITH NORMALIZATION**

Program, Session, Continuation, LogicalRequest, Attempt, and Phase are distinct.

Tool-wait representation must be normalized.

---

## 3. Logical State distinct from physical placement

**PASS**

This is one of the strongest and most consistent parts of C0.

---

## 4. State compatibility deterministic

**FAIL — BLOCKER**

Continuation ancestry is deterministic.

Full execution-origin compatibility is not yet defined.

Requires G0-B2.

---

## 5. Active Attempt unambiguous

**CONCEPTUALLY PASS / REPRESENTATION FAIL**

`CurrentAttempt` is semantically singular.

Attempt execution status and authority must be separated.

---

## 6. Superseded Attempt cannot regain authority

**CONCEPTUALLY PASS / REPRESENTATION FAIL**

The invariant is clear.

The Attempt enum cannot faithfully represent all required states.

---

## 7. Binding ownership generation monotonic

**CONDITIONAL**

Committed epoch monotonicity is clear.

Concurrent candidate fencing needs G0-B6.

---

## 8. Authority, freshness, status, scope are separate

**FAIL ACROSS DOCUMENT SET**

The Continuity Model separates them.

The Research Thesis and North Star still use the older combined taxonomy.

---

## 9. Ambiguous correctness states fail closed

**PASS**

This is consistently specified.

---

## 10. Kernel invariants CPU-testable

**PASS**

No GPU dependency exists for the semantic kernel.

---

## 11. Every RQ maps to experiments

**PASS WITH RQ7 AMENDMENT**

RQ1–RQ6 and RQ8 are mapped.

RQ7 requires formal Program completion semantics.

---

## 12. Baselines sufficiently strong and fair

**CONDITIONAL / BLOCKER FOR C3**

The fairness principle is strong.

B3's information contract remains underdefined.

---

## 13. Negative results can falsify/narrow thesis

**PASS**

Explicit falsification criteria exist.

---

## 14. Measured and simulated claims separated

**PASS**

The evidence discipline is strong.

Rename experimental E0–E4 only to avoid terminology collision.

---

## 15. CPU + public data + simulation sufficient

**PASS**

Nothing in the mandatory research claim requires accelerator ownership.

---

## 16. Paper 1 remains limited to four policies

**PASS**

Scope discipline is intact.

---

## 17. Broad autoscaling/global scheduling/cloud work excluded

**PASS**

No scope regression detected.

---

## 18. Same semantic core plausibly supports Gateway and another adapter

**PASS AT DESIGN LEVEL**

This remains an empirical portability question for G4.

No current core primitive prevents it.

---

# 20. Gate G0 Decision

The result is:

```text
THESIS                  PASS
PROVIDER NEUTRALITY     PASS
FAILURE BOUNDARY        PASS
EXPERIMENT PHILOSOPHY   PASS
FALSIFIABILITY          PASS
SCOPE CONTROL           PASS

SEMANTIC CLOSURE        HOLD
BASELINE CLOSURE        HOLD
```

Therefore:

\[
\boxed{Gate\ G0 = HOLD}
\]

C1 should not begin until the blocker patch is incorporated.

---

# 21. C0.1 Required Patch Set

Before implementation, revise the specifications to establish these canonical decisions:

1. split Attempt execution status from Attempt authority;
2. add `CommittedAttempt`;
3. make State compatibility execution-context-aware;
4. reject State produced by superseded/non-authoritative Attempts by default;
5. split State lifecycle from State validity;
6. define lifecycle derivation from live dependent Continuations;
7. remove or formally define State supersession;
8. fence migration candidates using BindingID plus monotonic epoch/base epoch;
9. define minimum evidence requirements for core semantic commits;
10. normalize authority/status/freshness/scope across all documents;
11. canonicalize tool waits as Continuation lifecycle events for Paper 1;
12. define minimal Program completion semantics;
13. write explicit baseline information contracts, especially B3;
14. rename experimental E0–E4 to avoid collision with runtime Evidence;
15. normalize reconciliation vocabulary.

---

# 22. Recommended Patch Order

Apply changes in this order because later changes depend on earlier semantics:

```text
1 Attempt authority model
        ↓
2 authoritative State producer model
        ↓
3 State compatibility
        ↓
4 State lifecycle / validity
        ↓
5 Binding candidate semantics
        ↓
6 Evidence action requirements
        ↓
7 Tool-wait semantics
        ↓
8 Program completion
        ↓
9 Baseline information contracts
        ↓
10 terminology normalization
```

---

# 23. Revised Canonical Safety Kernel

After C0.1, the kernel should become:

## K1 — Execution authority

\[
Finalize(r,o)
\Rightarrow
authority(attempt(o))=CURRENT
\]

and finalization atomically establishes:

\[
CommittedAttempt(r)=attempt(o)
\]

---

## K2 — State causal validity

\[
Consume(x,\kappa)
\Rightarrow
Compatible(x,\kappa)
\]

where compatibility includes:

```text
Continuation ancestry
+
producer Attempt authority
+
State validity
+
representation-specific validity
```

---

## K3 — Binding authority

\[
OwnershipCommit(b)
\Rightarrow
baseEpoch(b)=CurrentEpoch(subject(b))
\]

and:

\[
BindingID(event)=BindingID(b)
\]

for candidate-specific completion.

---

## K4 — Evidence sufficiency

\[
CorrectnessCommit(a)
\Rightarrow
Sufficient(E,a,t)
\]

with explicit minimum evidence rules for each core action.

---

## K5 — No semantic guessing

\[
Ambiguous(E,a)
\Rightarrow
\neg CorrectnessCommit(a)
\]

---

# 24. Revised Conceptual Architecture

The normalization produces a cleaner separation:

```text
LOGICAL EXECUTION
Program
Session
Continuation
LogicalRequest

        │

EXECUTION INSTANCE
Attempt
├── ExecutionStatus
└── AuthorityStatus

        │

REUSABLE STATE
State
├── Provenance
├── Lifecycle
└── Validity

        │

PHYSICAL REALITY
Replica
Binding
Location

        │

OBSERVATION
Evidence
├── Authority
├── Status
├── Freshness
└── Scope

        │

SEMANTIC COMMIT
Reconciler
```

This is stronger than the current formulation because each dimension answers exactly one question.

---

# 25. Gate G0 Exit Condition

After the C0.1 patch, rerun Gate G0 against the same 18 questions.

Proceed to C1 only when:

```text
no BLOCKER remains
```

and every correctness-sensitive operation has a deterministic answer to:

```text
Who is authoritative?

Which causal lineage does this belong to?

Is the State valid for this exact execution context?

Which Binding generation is current?

What evidence is sufficient?

What happens under ambiguity?
```

At that point the specification will be implementation-closed enough for two independent C1 implementations to converge on equivalent semantics.

---

# 26. Final Gate G0 Finding

The audit strengthens rather than weakens the project thesis.

The principal issues found are exactly the kinds of distinctions the Continuity research itself argues must be made explicit:

```text
execution outcome
≠
execution authority

State lifecycle
≠
State validity

Continuation ancestry
≠
complete execution provenance

physical migration candidate
≠
committed ownership

observation
≠
authoritative truth

workload completion
≠
request completion
```

Resolving these distinctions before implementation is therefore not administrative cleanup.

It is part of establishing the semantic precision that the research claims existing distributed inference abstractions lack.

**Historical result:** Gate G0 was placed on HOLD pending C0.1. The hold is superseded by the successful re-run recorded below.

---

# 27. C0.1 Amendment Resolution Record

The C0.1 patch resolves the original blocker set as follows.

| Original issue | Resolution |
|---|---|
| G0-B1 Attempt execution vs authority | Split into `ExecutionStatus` and `AttemptAuthority`; added `CurrentAttempt` and `CommittedAttempt` |
| G0-B2 State compatibility too coarse | Compatibility now accepts `ExecutionContext` and includes producer-Attempt authority |
| G0-B3 State lifecycle vs validity | Split into `StateLifecycle` and `StateValidity` |
| G0-B4 lifecycle derivation undefined | Added derivation from known potentially reusable live dependent Continuations |
| G0-B5 undefined State supersession role | Removed normative `StateSupersedes` from Paper 1; use validity + StateID/ReplicaID rules |
| G0-B6 concurrent migration candidates | Added unique `BindingID`, `base_epoch`, unique monotonic candidate epoch, exact candidate fencing |
| G0-B7 Evidence thresholds underdefined | Added minimum Paper 1 evidence requirements for finalization, State consumption, and migration commit |
| G0-B8 Evidence taxonomy inconsistent | Canonical authority/status/freshness/scope separation propagated across C0 |
| G0-B9 tool wait represented twice | External tool gaps canonicalized as Continuation lifecycle + descendant creation; removed normative `TOOL_WAIT` Phase |
| G0-B10 Program completion undefined | Added minimal Program lifecycle and application/workload `ObjectiveSatisfied` predicate |
| G0-B11 B3 information contract weak | Added explicit baseline information-contract requirement and B3 candidate-interface constraint |
| G0-B12 Evidence terminology collision | Experimental validation layers renamed `EV0`–`EV4` |
| G0-R1 authority order wording | Paper 1 now uses an explicit total authority order |
| G0-R2 confidence undefined | Confidence declared optional policy metadata that cannot raise authority |
| G0-R3 reconciliation vocabulary | Canonical outcome set retained: `MATCHED`, `WAIT`, `RETRY`, `RECOMPUTE`, `MIGRATE`, `REJECT`, `REPAIR`, `FAIL`, `AMBIGUOUS` |
| G0-R4 H1 novelty positioning | Attempt fencing remains a required composition mechanism, not a standalone novelty claim |

---

# 28. Gate G0 Re-Run

The amended specifications were checked mechanically for the semantic conditions that motivated the original HOLD.

The re-run verified:

```text
Attempt execution outcome and authority are separate

CurrentAttempt and CommittedAttempt both exist

State compatibility is ExecutionContext-aware

producer-Attempt authority participates in State reuse

State lifecycle and State validity are separate

normative StateSupersedes is removed from Paper 1

Binding candidates carry BindingID + base_epoch + epoch

minimum correctness Evidence requirements are explicit

TOOL_WAIT is absent as a normative Attempt Phase

Program completion is defined for evaluation

superseded-producer State is covered by invariants and failure tests

baseline information contracts are required

experimental validation layers use EV0–EV4
```

A consistency scan across the North Star and canonical C0 documents found no remaining obsolete `ActiveAttempt` or normative `TOOL_WAIT` usage and no unresolved blocker from the original G0 list.

---

# 29. Final Gate G0 Verdict

The re-run result is:

```text
THESIS                  PASS
PROVIDER NEUTRALITY     PASS
IDENTITY MODEL          PASS
ATTEMPT AUTHORITY       PASS
STATE COMPATIBILITY     PASS
STATE LIFECYCLE/VALIDITY PASS
BINDING FENCING         PASS
EVIDENCE SEMANTICS      PASS
FAILURE BOUNDARY        PASS
EXPERIMENT TRACEABILITY PASS
BASELINE FAIRNESS       PASS WITH C3 CONTRACT REQUIREMENT
PROGRAM METRICS         PASS
SCOPE CONTROL           PASS
C1 IMPLEMENTATION CLOSURE PASS
```

Therefore:

\[
\boxed{Gate\ G0 = PASS}
\]

The project is authorized to begin:

> **C1 — Deterministic Continuity Core**

The first C1 implementation order remains:

```text
C1.1 canonical entity/identity types
C1.2 Continuation DAG
C1.3 Attempt execution + authority state
C1.4 CurrentAttempt / CommittedAttempt finalization
C1.5 State provenance + producer resolution
C1.6 ExecutionContext compatibility
C1.7 State lifecycle + validity
C1.8 Binding candidate / epoch model
C1.9 Evidence model + sufficiency
C1.10 semantic commit API + Reconciler
C1.11 independent invariant oracle
C1.12 deterministic counterexample tests
C1.13 property/sequence testing
```

No performance policy optimization should precede conformance of the semantic kernel to the invariant suite.

