# 04 — Failure Model
## Fault, Adversary, and Recovery Semantics for Continuity-Aware Distributed Inference

**Working paper title:**  
**Continuity-Aware Distributed Inference: Causal Execution and State Lineage for Stateful Generative Workloads**

**Document role:** Canonical failure and adversary specification  
**Milestone:** C0 — Research Specification  
**Dependencies:** `01 — Research Thesis`, `02 — Continuity Model`, `03 — Invariants`  
**Status:** C0.1 normalized failure-model candidate

---

# 1. Purpose

This document defines the failures under which **Continuity-Aware Distributed Inference** is expected to preserve its safety properties.

It establishes:

- what components may fail;
- how communication may fail;
- how observations may become stale, delayed, duplicated, or contradictory;
- how execution attempts may overlap;
- how reusable state may be lost, moved, or partially materialized;
- which components remain trusted;
- which failures are recoverable;
- which failures may produce explicit non-success;
- which failures are outside the first paper's model.

The objective is not to prove that every inference request always completes.

The objective is to establish the conditions under which the Continuity Runtime must prevent a failure from becoming a **silent semantic misassociation**.

The governing distinction is:

```text
physical/system failure
        ≠
semantic correctness failure
```

A worker crash, lost cache replica, timeout, or delayed event may legitimately cause:

```text
WAIT
RETRY
RECOMPUTE
REPAIR
REJECT
FAIL
AMBIGUOUS
```

The Continuity correctness claim is violated only when such conditions cause the system to silently commit semantically incorrect authoritative state.

---

# 2. Failure-Model Objective

The failure model is constructed to test the central thesis:

> Stateful distributed inference should preserve explicit causal relationships between logical computation, execution attempts, reusable state, and physical placement even when observations and execution evolve asynchronously.

The failure model therefore concentrates on conditions where **physical reality and semantic authority diverge temporarily**.

Examples:

```text
old Attempt still running
        while
new Attempt is authoritative
```

```text
old binding still physically active
        while
new binding is committed
```

```text
state replica exists physically
        while
its semantic lineage is incompatible
```

```text
observation is exact
        while
it is stale relative to current authority
```

These divergence windows are the principal adversarial surface of the paper.

---

# 3. Terminology

The following terms are used consistently.

## Fault

An underlying abnormal condition.

Examples:

- worker process terminates;
- message delivery is delayed;
- state replica disappears;
- cache index is stale.

## Failure

Externally observable inability of a component to provide expected behavior.

Example:

```text
worker does not return result before timeout
```

## Observation fault

The physical system may be functioning correctly, but the Continuity Runtime receives incomplete, stale, reordered, contradictory, or delayed information.

## Semantic hazard

A condition capable of causing authoritative logical state to become associated with the wrong execution, state, branch, or owner.

## Semantic violation

A committed state in which one of the Continuity invariants is false.

## Silent incorrect success

The system reports or commits success despite semantic misassociation.

This is the primary correctness failure class.

## Explicit non-success

The system returns or enters an explicit outcome such as:

```text
WAIT
RETRY
RECOMPUTE
REPAIR
REJECT
FAIL
AMBIGUOUS
```

Explicit non-success may reduce availability or performance.

It is not by itself a Continuity correctness violation.

---

# 4. System Components

The failure model conceptually contains the following components:

```text
Application / Workload Generator
            │
            ▼
     Continuity Runtime
            │
       ┌────┼────┐
       ▼    ▼    ▼
 Scheduler State Observation
 Adapter    Adapter Adapter
       │       │       │
       └───────┼───────┘
               ▼
          Execution Plane
        ┌──────┼──────┐
        ▼      ▼      ▼
     Worker  Worker  Worker
        │      │      │
        └──────┼──────┘
               ▼
          Physical State
```

The model distinguishes:

### Semantic authority

The component responsible for committing Continuity logical state.

### Execution components

Workers and execution services that perform inference work.

### State components

Systems that store, transfer, materialize, or evict reusable inference state.

### Observation components

Systems that report execution, placement, state, or health information.

### Scheduling components

Systems that choose execution locations or performance policies.

Only semantic authority may convert observations into authoritative Continuity state.

---

# 5. Trusted Computing Boundary

Paper 1 assumes a trusted logical Continuity authority.

The Continuity authority is assumed to:

- execute its transition rules correctly;
- allocate Attempt generations correctly;
- allocate Binding epochs correctly;
- enforce semantic commit guards;
- not deliberately fabricate authoritative state;
- not become Byzantine.

External workers, schedulers, caches, and observation systems are not treated as semantic authorities merely because they report facts.

They provide evidence.

This separation is intentional.

---

# 6. Single Semantic Authority Assumption

Paper 1 assumes one logical authority for each semantic commit domain.

The model does not require that this authority be one operating-system process.

It requires that authoritative state behave as though there is one serialized logical commit authority for:

```text
CurrentAttempt
Binding epoch
LogicalRequest finalization
state invalidation
continuation terminal state
migration commit
```

Consensus among competing independent Continuity authorities is outside Paper 1.

Therefore the model does **not** claim tolerance of:

```text
two independent Continuity controllers
both believing themselves authoritative
without consensus/fencing
```

Such a scenario belongs to future work.

---

# 7. Authority Failure Boundary

The safety guarantees in Paper 1 are conditional on correct preservation of committed Continuity authority.

If the authority process crashes but its committed state is preserved correctly and restored before new commits, the model may continue safely.

If authoritative state is lost or rolled back without detection, the Paper 1 model does not claim safety.

For example:

```text
CurrentEpoch = 9
        ↓
authority crashes
        ↓
restores stale snapshot
        ↓
CurrentEpoch = 7
```

is outside the core guarantee unless an implementation provides durable monotonic recovery preventing that rollback.

The experimental prototype may study crash/restart behavior, but such experiments must identify whether durable authority state is assumed.

---

# 8. Non-Byzantine Failure Model

Components may:

- crash;
- stop responding;
- restart;
- return late;
- report stale observations;
- duplicate observations;
- lose physical state;
- expose temporary contradictory observations.

Components are not assumed to maliciously fabricate arbitrary authenticated evidence.

The model therefore excludes Byzantine behavior such as:

```text
worker knowingly claims State X
while storing unrelated State Y
```

or:

```text
attacker forges AUTHORITATIVE evidence
```

unless later work adds cryptographic or Byzantine fault-tolerance mechanisms.

---

# 9. Communication Model

Communication between components is asynchronous.

The system does not assume:

- bounded message delay;
- FIFO delivery across all channels;
- exactly-once delivery;
- global ordering;
- globally synchronized clocks.

Messages or observations may be:

```text
delayed
duplicated
reordered
temporarily omitted
```

A message may arrive after the logical state to which it refers has been superseded.

This is a normal modeled condition.

---

# 10. No Global Clock Assumption

The model does not use wall-clock ordering as causal authority.

Clock timestamps may be used for:

- timeout policy;
- evidence freshness;
- measurement;
- observability.

But:

```text
timestamp A > timestamp B
```

does not establish:

```text
A causally supersedes B
```

Causal authority instead comes from:

- explicit graph relationships;
- Attempt generation;
- Binding epoch;
- supersession;
- semantic commit state.

Clock skew is therefore not required to violate correctness.

---

# 11. Event Delay Fault

## Definition

An event generated at physical time \(t_0\) is observed by the Continuity Runtime substantially later.

Example:

```text
A1 completes
        │
        │ delayed
        ▼
A2 already became authoritative
        │
        ▼
A1 completion arrives
```

## Hazard

Temporal recency of delivery may differ from causal generation.

## Invariants challenged

```text
B4 Supersession Irreversibility
B5 Finalization Authority
B7 Superseded-Event Fencing
E7 Freshness Does Not Establish Causal Newness
F3 Reordered Observation Safety
```

## Required Continuity behavior

The delayed event may be recorded.

It may not regain semantic authority.

---

# 12. Event Duplication Fault

## Definition

One physical event is delivered more than once.

Examples:

- duplicate result;
- duplicate state-materialization event;
- duplicate migration acknowledgment;
- duplicate eviction notification.

## Hazard

Repeated event handling may produce repeated semantic commits.

## Invariants challenged

```text
F1 Semantic Commit Idempotence
F2 Duplicate Observation Safety
B6 Terminal Request Immutability
```

## Required behavior

Authoritative state after \(n\) duplicate deliveries must be equivalent to authoritative state after one delivery.

---

# 13. Event Reordering Fault

## Definition

Events generated in one physical order are observed in another.

Example:

```text
generated:
    migration-start
    migration-complete

observed:
    migration-complete
    migration-start
```

or:

```text
generated:
    A1 completion
    A2 completion

observed:
    A2 completion
    A1 completion
```

## Hazard

Observation order may be mistaken for semantic generation order.

## Invariants challenged

```text
B3 Attempt Generation Monotonicity
B4 Supersession Irreversibility
D2 Binding Epoch Monotonicity
D3 Stale Binding Fencing
F3 Reordered Observation Safety
```

---

# 14. Event Omission / Temporary Loss

## Definition

An expected observation does not arrive within the decision interval.

The model does not require permanent reliable delivery of every observation.

Example:

```text
destination replica materialized
```

but the corresponding acknowledgment is temporarily lost.

## Hazard

The runtime may not know whether the physical action completed.

## Expected reconciliation

Possible:

```text
WAIT
RETRY observation
REPAIR
RECOMPUTE
FAIL
AMBIGUOUS
```

depending on policy.

## Required safety property

Missing evidence must not be silently treated as successful evidence for correctness-sensitive commit.

---

# 15. Communication Partition

## Definition

Two or more components cannot communicate for a period.

Example:

```text
Continuity authority
        X
Worker W2
```

while W2 may continue physical execution.

## Hazard

Physical activity continues while semantic authority cannot observe or control it.

## Required behavior

A partitioned worker cannot independently redefine semantic authority.

When communication returns, delayed events are reconciled against current:

- Attempt generation;
- Binding epoch;
- request terminal state.

## Liveness

The model does not guarantee progress during indefinite partition.

---

# 16. Worker Crash-Stop

## Definition

A worker terminates and ceases execution.

Potential effects:

- current Attempt stops;
- local State replicas disappear;
- completion event never arrives;
- migration source becomes unavailable.

## Hazards

```text
Attempt may remain logically active
while physical execution no longer exists
```

and:

```text
state directory may still report replicas
that have disappeared
```

## Invariants challenged

Primarily:

```text
E1 Observation Does Not Equal Authority
E2 Evidence Status and Authority
C6 Lost Replica Does Not Erase Logical Provenance
D5 Migration Evidence Sufficiency
```

## Expected recovery

Possible:

```text
RETRY
RECOMPUTE
MIGRATE
REPAIR
FAIL
```

---

# 17. Worker Crash During Attempt

Representative trace:

```text
R1
└── A1
     │
     ├── execution RUNNING / authority CURRENT on W1
     │
     X worker crash
```

The LogicalRequest may:

1. wait for failure detection;
2. create a newer retry Attempt A2;
3. atomically set A1 authority to `SUPERSEDED` and A2 authority to `CURRENT`.

Once A2 becomes `CURRENT`, A1 cannot regain authority even if W1 restarts and emits buffered completion data.

A1 may still later be observed as physically `SUCCEEDED`; its semantic authority remains `SUPERSEDED`.

# 18. Worker Restart with Late State

## Definition

A worker restarts and exposes residual or restored physical State from a prior semantic generation or superseded execution.

Examples:

```text
W1 previously hosted:
State X
Binding B4 / epoch 4

system migrated to:
Binding B5 / W2 / epoch 5

W1 restarts
and discovers X locally
```

or:

```text
A1 produced X
A1 later became SUPERSEDED
W1 restarts with X still present
```

## Hazard

Physical persistence may be mistaken for current ownership or valid producer authority.

## Required behavior

The old Replica may be observed.

Epoch 4 cannot regain Binding authority.

State X may be reused only if:

```text
Continuation lineage valid
producer-Attempt authority valid where applicable
StateValidity = VALID
Replica valid
representation validity passes
sufficient Evidence exists
```

Residual State produced by a superseded Attempt is not reusable by default merely because it still exists physically.

# 19. Retry Timeout Fault

## Definition

An Attempt exceeds policy timeout, causing creation of a new Attempt while the original may still be physically running.

Representative trace:

```text
A1 execution RUNNING / authority CURRENT
   ↓
timeout
   ↓
A2 created
   ↓
A1 authority → SUPERSEDED
A2 authority → CURRENT
   ↓
A1 may still physically run
```

## Key semantic fact

Timeout does not prove A1 stopped.

Therefore execution outcome and semantic authority must be represented independently.

## Invariants challenged

```text
B1 Single Current Attempt Authority
B4 Supersession Irreversibility
B5 Finalization Authority
B7 Superseded-Event and State-Producer Fencing
```

# 20. Late Successful Attempt

## Definition

A superseded Attempt later produces a valid physical result.

Example:

```text
A1 timeout
A2 becomes CURRENT
A2 succeeds and commits
A1 physically succeeds late
```

The resulting A1 state is:

```text
execution_status = SUCCEEDED
authority_status = SUPERSEDED
```

The result may be valid relative to A1's physical execution while being semantically stale relative to the LogicalRequest.

## Required behavior

```text
recordable
diagnostic
non-authoritative
```

but never finalizing.

Any reusable State whose producer resolves to A1 is likewise non-reusable for later cross-request use by default.

This is the canonical execution-continuity failure case.

# 21. Concurrent Retry Race

## Definition

Two execution Attempts overlap substantially due to uncertain failure detection or retry policy.

Example:

```text
        ┌── A1 execution ─────────────┐
R1 ─────┤                             ├── O1
        └──── A2 execution ─────── O2
```

Both may physically succeed.

Only one may hold `CURRENT` semantic authority at a time.

## Continuity requirement

At the finalization commit point:

\[
attempt(o)=CurrentAttempt(R1)
\]

must hold.

After successful finalization:

\[
CommittedAttempt(R1)=attempt(o)
\]

Output quality or arrival order must not determine authority.

# 22. Duplicate Logical Submission

## Definition

The application or transport layer accidentally submits semantically duplicate work as either:

- duplicate delivery of the same LogicalRequest;
- or creation of two separate LogicalRequests.

These are distinct cases.

### Same LogicalRequest identity

Must be handled idempotently.

### Different LogicalRequest identities

They are semantically independent unless an application-level deduplication relation exists.

Paper 1 does not infer equality from payload similarity alone.

This maintains the principle:

```text
same text/content
≠
same logical request
```

---

# 23. State Replica Eviction

## Definition

A physical State replica is removed due to memory pressure or policy.

Example:

```text
State X
├── W1 VALID
└── W2 VALID

W1 evicts
```

## Expected semantic effect

```text
logical X remains
W1 replica → LOST/EVICTED
W2 replica remains
```

## Invariants challenged

```text
C5 Replica Identity Does Not Change Logical State Identity
C6 Lost Replica Does Not Erase Logical Provenance
```

---

# 24. Total Physical State Loss

## Definition

All physical replicas of logical State \(X\) disappear.

\[
validReplicas(X)=\varnothing
\]

## Hazard

The scheduler may be tempted to substitute a similar or nearby state object.

## Required behavior

Logical provenance remains known.

Possible reconciliation:

```text
RECOMPUTE
RESTORE
FAIL
```

The runtime must not relabel unrelated physical state as X.

---

# 25. Stale State-Location Observation

## Definition

The state directory or cache index reports a replica at a location where it no longer exists.

Example:

```text
directory:
X on W3

physical:
X evicted from W3
```

## Evidence class

Typically:

```text
STALE
```

once detected, or initially:

```text
EXACT_OBSERVATION
```

that has exceeded its freshness validity.

## Hazard

Performance policy may route toward W3 expecting reuse.

## Correctness effect

Routing to W3 may be permitted if the consequence is recomputation.

But direct correctness-sensitive consumption must require actual valid state evidence according to policy.

This distinction is central.

---

# 26. False-Negative State Observation

## Definition

A State replica exists physically but the observation system does not report it.

Example:

```text
physical:
X on W2

directory:
no X on W2
```

## Consequence

Primarily performance:

- unnecessary recomputation;
- unnecessary transfer;
- suboptimal routing.

Unless ownership semantics depend on the observation, it should not cause semantic misassociation.

This fault helps separate:

```text
performance degradation
```

from:

```text
correctness violation
```

---

# 27. Incompatible State Locality and Producer Authority

## Definition

A worker contains reusable State that is physically attractive but semantically invalid for the exact consumer execution context.

Two canonical cases are required.

### Wrong branch

```text
       C0
      /  \
    C1    C2
    |
    X1

Worker W2:
    executing C2
    contains X1
```

X1 is rejected because C1 is not an ancestor of C2.

### Correct branch, wrong producer Attempt

```text
C0 → C1
     │
     R1
     ├── A1 → X1
     └── A2

A1 authority = SUPERSEDED
A2 authority = COMMITTED
```

Even though X1 resolves to the correct Continuation ancestry, it is rejected for later reuse because its producer Attempt is superseded.

## Required Continuity behavior

State reuse requires:

```text
Continuation ancestry
+
producer-Attempt authority where applicable
+
StateValidity
+
Replica validity
+
representation validity
```

Physical locality cannot override any failed semantic check.

# 28. Similar-but-Different State

## Definition

Two reusable State objects appear similar under a performance-oriented key.

Examples:

- same prefix length;
- same hash bucket;
- same session;
- similar token prefix;
- same worker;
- same semantic type.

But their causal provenance differs.

## Hazard

Performance heuristics may conflate identity with similarity.

## Required rule

Similarity may influence candidate ranking.

It cannot establish logical State identity or compatibility by itself.

---

# 29. State Representation Mismatch

## Definition

A State object is lineage-compatible but physically/semantically incompatible with the current engine execution.

Examples may include mismatched:

```text
model identity
model revision
tokenization
representation version
execution semantics
```

## Model boundary

Continuity provides lineage compatibility.

The engine adapter provides:

\[
SemanticValidity(x,c)
\]

## Required result

If representation validation fails:

\[
Compatible(x,c)=false
\]

even if ancestry is valid.

---

# 30. Partial State Materialization

## Definition

A destination reports or exposes State before all required physical contents are usable.

Example:

```text
migration begins
metadata appears on W2
only part of State X copied
```

## Hazard

Presence may be mistaken for usability.

## Required behavior

Replica remains:

```text
MATERIALIZING
TRANSFERRING
```

rather than:

```text
VALID
```

until sufficient completion evidence exists.

---

# 31. Migration Source Failure

Representative trace:

```text
W1 authoritative
     │
migration X → W2
     │
W1 crashes mid-transfer
```

Potential physical states:

```text
W1 lost
W2 partial replica
remote replica maybe present
```

## Required reconciliation

Possible:

```text
WAIT
RESTORE
RECOMPUTE
FAIL
```

The destination must not become authoritative merely because it has partial state.

---

# 32. Migration Destination Failure

Representative trace:

```text
W1 authoritative
     │
migration → W2
     │
W2 crashes
```

## Required behavior

Before semantic migration commit:

```text
W1 remains authoritative
```

if still available.

The candidate binding may be invalidated.

A new migration may target W3 with a newer candidate generation according to policy.

---

# 33. Migration Completion Event Delay

Representative trace:

```text
W2 completes physical transfer
        │
ack delayed
        │
runtime still sees W1 authoritative
```

This is safe.

It may reduce efficiency.

The runtime must not infer migration completion without sufficient evidence.

---

# 34. Late Old-Binding Event

Representative trace:

```text
epoch 7 / W1 authoritative
migration
epoch 8 / W2 committed
late event from W1 / epoch 7
```

## Required behavior

The event is fenced by:

\[
7 < CurrentEpoch=8
\]

This directly tests:

```text
D2 Binding Epoch Monotonicity
D3 Stale Binding Fencing
F3 Reordered Observation Safety
```

---

# 35. Concurrent Migration Race

## Definition

Multiple migration candidates are initiated before an earlier candidate has committed.

Example:

```text
Current:
B7 / W1 / epoch 7

Candidate B8:
W2 / base_epoch 7 / epoch 8

Candidate B9:
W3 / base_epoch 7 / epoch 9
```

Physical transfer work may overlap.

Semantic commit is serialized.

## Safety requirement

A candidate may commit only if:

\[
baseEpoch(candidate)=CurrentEpoch(subject)
\]

and sufficient Evidence is scoped to the exact:

```text
BindingID
BindingEpoch
```

If B8 commits first, CurrentEpoch becomes 8. B9 can no longer commit because its base epoch is 7.

Late completion for a losing candidate is fenced by both candidate identity and generation.

Physical concurrency cannot produce multiple authoritative owners.

# 36. Ambiguous Ownership Observation

## Definition

Available observations support multiple incompatible ownership interpretations.

Example:

```text
E1:
claim = W1 has relevant State
status = VALID

E2:
claim = W2 has relevant State
status = VALID
```

Neither observation is sufficient to establish which exact BindingID is semantically current.

## Required reconciliation

```text
AMBIGUOUS
```

or another explicit safe outcome.

## Prohibited behavior

```text
choose whichever observation arrived last
```

for a correctness-sensitive ownership commit.

Physical State presence is not equivalent to committed Binding authority.

# 37. Stale High-Authority Evidence

## Definition

Evidence has high authority but is no longer usable for the current action because freshness, status, scope, Binding generation, or Attempt authority has changed.

Example:

```text
E:
claim = B7 is current owner
authority = AUTHORITATIVE
status = VALID
observed earlier

CurrentBinding = B8
CurrentEpoch = 8
```

## Hazard

Authority may be mistaken for perpetual validity.

## Required behavior

Evidence sufficiency considers independently:

- authority;
- status;
- freshness;
- scope;
- exact Attempt/Binding identity where applicable.

A high-authority stale claim cannot override current semantic state.

# 38. Contradictory Evidence

## Definition

Two valid observations make incompatible claims.

Contradiction does not automatically imply Byzantine behavior.

It may arise from:

- observation delay;
- transitional migration state;
- stale cache;
- asynchronous state propagation.

## Example

```text
E1:
X exists on W1

E2:
X absent on W1
```

with different observation times.

## Required behavior

The Reconciler evaluates:

- authority;
- generation;
- freshness;
- scope.

If conflict cannot be resolved:

```text
AMBIGUOUS
```

remains explicit.

---

# 39. Approximate Evidence Error

## Definition

A performance-oriented estimator produces an incorrect prediction.

Examples:

```text
cache locality prediction wrong
reuse probability wrong
queue estimate wrong
transfer-time estimate wrong
```

## Expected consequence

Primarily:

- slower execution;
- recomputation;
- poor placement.

Approximate evidence may not authorize correctness-sensitive semantic commits when stronger evidence is required.

This is how the model contains estimator error.

---

# 40. Evidence Freshness Expiry

## Definition

Evidence remains internally consistent but exceeds the action's maximum acceptable age.

If:

\[
age(e,t)>maxAge(a)
\]

then:

\[
Sufficient(e,a,t)=false
\]

for that action.

## Consequence

A system may need:

```text
refresh
WAIT
re-observe
recompute
```

The freshness threshold is policy-specific.

---

# 41. Evidence Scope Mismatch

## Definition

Evidence is valid but refers to a different semantic subject.

Example:

```text
evidence:
State X exists on W3

action:
commit ownership for State Y
```

## Hazard

Generic location evidence may be reused outside its proper identity scope.

## Required behavior

Reject as insufficient due to scope mismatch.

---

# 42. Tool-Wait State Eviction

Representative trace:

```text
C1 ACTIVE
  ↓
external tool invoked
  ↓
C1 WAITING
  ↓
State X retained
  ↓
memory pressure
  ↓
X replica evicted
  ↓
tool result arrives
  ↓
create child C2 ACTIVE
C1 → TERMINAL
```

## Correctness

No semantic violation occurs merely because X was evicted.

C2 preserves causal ancestry from C1.

If X is unavailable, the runtime may recompute or restore valid ancestor State.

It must not substitute incompatible State.

## Efficiency consequence

Tool-return execution may become cold.

## Research relevance

This fault drives lifecycle-aware retention experiments.

For Paper 1, external tool waits are Continuation lifecycle events rather than normative Attempt Phases.

# 43. Speculative Branch Eviction

Representative structure:

```text
C0
├── C1 ACTIVE
├── C2 SPECULATIVE
└── C3 SPECULATIVE
```

Under pressure, speculative state may be evicted preferentially.

The failure model permits speculative branch state to disappear.

The scheduler must not:

- treat eviction as branch termination unless explicitly committed;
- reuse state from another branch as substitute.

---

# 44. Branch Abandonment with Residual State

## Definition

A Continuation becomes:

```text
ABANDONED
```

while its State replicas remain physically present.

## Hazard

Residual cache may later appear attractive.

## Required behavior

Physical presence does not reactivate the branch.

State associated exclusively with abandoned semantics must not regain live-branch authority.

---

# 45. Terminal State Residue

A terminal Continuation may leave physical reusable state in cache.

This is permitted.

Its lifecycle may make the state:

```text
TERMINAL
```

and retention policy may later evict it.

Physical persistence does not imply the Continuation remains active.

This separates:

```text
logical lifecycle
```

from:

```text
physical lifetime
```

---

# 46. Fan-Out Pressure

## Definition

A single ancestor Continuation creates many descendants concurrently.

Example:

```text
          C0
      / / | \ \
    C1 C2 C3 C4 C5
```

## Hazards

- high state demand;
- eviction pressure;
- replica-placement contention;
- stale observations;
- queue imbalance.

## Correctness requirement

Every branch must preserve its own lineage.

## Efficiency relevance

This workload tests whether preserving common valid ancestor state provides measurable reuse advantage.

---

# 47. Join Delay

## Definition

A join Continuation waits for multiple parent branches, one or more of which is delayed or fails.

Example:

```text
C1 ─────┐
        ├── C3
C2 ─X───┘
```

## Paper 1 boundary

The model does not require automatic semantic merging of arbitrary branch state.

The failure model may use joins for lifecycle/program timing experiments.

Correctness claims remain limited to lineage/provenance validity.

---

# 48. Scheduler Staleness

## Definition

The scheduler makes a placement decision using a snapshot of resource/state information that becomes stale before dispatch.

Example:

```text
t0:
W1 queue = low
X on W1

t1:
W1 queue increases
X evicted

t2:
request dispatched based on t0
```

## Expected consequence

Performance degradation.

If state validity is checked before actual state consumption, semantic correctness remains intact.

This directly motivates:

```text
schedule-time optimization
≠
commit-time authority
```

---

# 49. Check-Then-Commit Race

## Definition

A correctness predicate is true during an early check but false at commit time.

Canonical Attempt example:

```text
t0:
A1 authority = CURRENT

t1:
scheduler validates A1

t2:
A2 supersedes A1

t3:
A1 result arrives

t4:
commit based only on t1 check
```

Canonical Binding example:

```text
t0 candidate B8 based on epoch 7

t1 validation succeeds

t2 competing B9 commits and changes CurrentEpoch

t3 B8 completion arrives

t4 commit based only on t1 validation
```

## Required behavior

Re-check mutable semantic authority at the commit point.

This includes:

```text
CurrentAttempt
CurrentBinding
CurrentEpoch
BindingID
StateValidity
```

as applicable.

# 50. Resource Saturation

## Definition

Workers become heavily queued or unavailable due to workload pressure.

This may produce:

- increased timeout;
- retries;
- longer evidence age;
- state eviction;
- migration.

Resource saturation is not itself a semantic fault.

It is important because it increases the frequency of Continuity hazards.

The simulator should therefore combine high load with retry/state faults rather than evaluating them only in isolation.

---

# 51. Retry Storm

## Definition

Many LogicalRequests time out and generate new Attempts during a short interval.

Potential effects:

- large number of overlapping superseded Attempts;
- delayed completion flood;
- high control-plane event rate;
- increased stale evidence.

## Research relevance

Tests whether attempt fencing remains safe and whether Continuity metadata/control overhead remains practical under adversarial concurrency.

---

# 52. State-Migration Storm

## Definition

Many state objects migrate concurrently due to failures or placement policy.

Potential effects:

- competing transfers;
- delayed acknowledgments;
- replica-state churn;
- evidence staleness;
- migration retries.

## Research relevance

Tests:

```text
Binding epoch safety
migration commit semantics
control-plane scalability
```

---

# 53. Observation Lag Sweep

The experimental failure model should expose observation delay as a controllable parameter.

Example sweep:

```text
0 ms
10 ms
50 ms
100 ms
500 ms
1 s
5 s
```

The objective is to measure the transition from:

```text
fresh exact observation
```

to:

```text
operationally stale evidence
```

and compare baseline behavior against authority-aware reconciliation.

---

# 54. Failure Injection Dimensions

The simulator should expose at least the following tunable dimensions:

```text
event delay distribution
event duplication probability
event omission probability
event reordering window
worker failure probability
worker restart delay
retry timeout
attempt completion distribution
replica eviction probability
state-loss probability
observation lag
migration failure probability
migration transfer duration
branch factor
tool-wait duration
cache pressure
```

Not every experiment varies every parameter.

The full parameter space enables systematic sensitivity analysis.

---

# 55. Failure Correlation

Failures should be tested both independently and in correlated combinations.

Independent-only testing is insufficient because the most important Continuity hazards emerge from interactions.

Examples:

```text
worker overload
    +
timeout
    +
retry
    +
late old completion
```

```text
migration
    +
destination crash
    +
stale directory
```

```text
tool wait
    +
state eviction
    +
branch resume
```

```text
high load
    +
evidence lag
    +
duplicate events
```

The simulator must support such composed traces.

---

# 56. Deterministic Adversary Model

For correctness testing, the adversary may control:

- event delivery order;
- event delivery delay;
- duplicate count;
- omission of non-authoritative observations;
- worker crash timing;
- state eviction timing;
- retry timing;
- migration interruption timing;
- observation freshness.

The adversary may **not**:

- forge entity identities;
- alter committed Continuity state directly;
- invent valid authoritative credentials;
- violate the trusted authority implementation;
- create cycles through APIs that correctly enforce graph invariants.

The objective is to maximize difficult interleavings while remaining within the failure assumptions.

---

# 57. Safety Expectation Under Adversarial Scheduling

For every adversarial execution trace permitted by this failure model:

\[
\tau
\]

the Continuity implementation should maintain the normalized safety kernel:

```text
K1 Correct current/committed Attempt authority
K2 Correct Continuation and producer-State causality
K3 Correct BindingID + generation authority
K4 Sufficient scoped Evidence
K5 No semantic guessing
```

The system may fail to make progress.

It may not violate these safety obligations.

# 58. Liveness Boundary

The model intentionally does not guarantee progress under:

- indefinite network partition;
- permanent loss of all execution capacity;
- permanent loss of all reconstructible State;
- unavailable semantic authority;
- perpetual ambiguity;
- infinite retry/failure loops.

A valid safe result may therefore be:

```text
FAIL
```

The paper must not equate:

```text
safe
```

with:

```text
always available
```

This distinction is central to fail-closed semantics.

---

# 59. Failure Outcome Classes

Every injected fault should resolve into one of four outcome classes.

## O1 — Correct transparent recovery

The system recovers and produces the intended result without externally visible failure.

## O2 — Correct degraded recovery

The system recovers through:

- retry;
- recomputation;
- migration;
- repair;

with measurable overhead.

## O3 — Explicit non-success

The system returns or remains in:

```text
WAIT
FAIL
AMBIGUOUS
REJECT
```

according to policy.

## O4 — Silent semantic violation

The system reports success while committing incorrect semantic state.

For Continuity under the modeled safety claims:

```text
O4 should be zero
```

for covered failure classes.

---

# 60. Correctness Metrics Derived from Failure Outcomes

The failure experiments must record at least:

\[
SilentSemanticErrorRate
=
\frac{O4}{TotalFaultedOperations}
\]

\[
ExplicitNonSuccessRate
=
\frac{O3}{TotalFaultedOperations}
\]

\[
RecoveryRate
=
\frac{O1+O2}{TotalFaultedOperations}
\]

and where appropriate:

```text
Stale Attempt Acceptance Rate
Wrong-State Consumption Rate
Wrong-Branch Reuse Rate
Silent Binding Divergence Rate
Ambiguous Commit Rate
Duplicate Finalization Rate
```

This prevents availability cost from being hidden inside a single "success rate."

---

# 61. Failure-to-Invariant Matrix

| Failure class | Primary invariants challenged |
|---|---|
| Delayed Attempt result | B4, B5, B7, F3 |
| Duplicate result | B6, F1, F2 |
| Retry race | B1, B4, B5, B7 |
| Event reordering | B3, D2, D3, F3 |
| Stale state observation | C1, E2, E3, E7 |
| Wrong sibling state | C1, C2, C3 |
| Replica eviction | C5, C6 |
| Total state loss | C6 |
| Partial materialization | C4, E3 |
| Source crash during migration | D4, D5 |
| Destination crash during migration | D4, D5 |
| Late old-binding event | D2, D3, F3 |
| Concurrent migration | D1, D2, D4 |
| Ambiguous ownership | E3, E4 |
| Stale authoritative evidence | E2, E3 |
| Scope mismatch | E5 |
| Duplicate observation | F1, F2 |
| Terminal branch residue | F4, F5 |
| Invalid state resurrection | C4, F6 |
| Check-then-commit race | B5, D3, E3 |

---

# 62. Failure-to-Research Mapping

## RQ1 / H1

Failures:

```text
timeout
retry race
late completion
duplicate completion
event reorder
```

Primary objective:

```text
Stale Attempt Acceptance Rate = 0
```

---

## RQ2 / H2

Failures:

```text
wrong sibling branch
similar-but-different state
stale cache locality
residual abandoned state
```

Primary objective:

```text
Wrong-State Consumption Rate = 0
```

for lineage-detectable incompatibility.

---

## RQ3 / H3

Failures:

```text
stale evidence
contradictory evidence
scope mismatch
approximate prediction
observation delay
```

Primary objective:

```text
Ambiguous Commit Rate = 0
```

while measuring:

```text
WAIT
RECOMPUTE
FAIL
```

cost.

---

## RQ5 / H5

Failures/pressure:

```text
tool wait
cache pressure
speculative branches
state eviction
```

Objective:

improve useful state retention without violating lineage semantics.

---

## RQ6 / H6

Failures:

```text
worker loss
partial migration
migration race
late epoch event
destination failure
```

Objective:

```text
Silent Binding Divergence Rate = 0
```

and reduced redundant recovery where possible.

---

# 63. Required Deterministic Failure Traces

The following traces are mandatory before Gate G1 experiments.

## FTR1 — Late stale completion

```text
A1 CURRENT / RUNNING
A1 timeout
A2 CURRENT
A1 SUPERSEDED
A2 succeeds and commits
A1 succeeds late
```

Expected:

```text
A1 result rejected as authoritative
CommittedAttempt remains A2
```

---

## FTR2 — Duplicate result

Expected:

```text
one logical finalization
```

---

## FTR3 — Reordered retry events

Expected:

```text
newer Attempt authority remains current/committed
```

---

## FTR4 — Wrong sibling State

Expected:

```text
rejected by Continuation ancestry
```

---

## FTR5 — Superseded-producer State

```text
C1 / R1
├── A1 → X1
└── A2 commits
A1 = SUPERSEDED
```

Expected:

```text
X1 rejected for later cross-request reuse
```

---

## FTR6 — Lost valid State

Expected:

```text
recompute/repair/fail
not substitute unrelated State
```

---

## FTR7 — Partial migration

Expected:

```text
old committed Binding remains authoritative
```

---

## FTR8 — Destination crash before commit

Expected:

```text
no new authoritative Binding
```

---

## FTR9 — Late old-Binding event

Expected:

```text
current Binding unchanged
```

---

## FTR10 — Losing concurrent migration candidate completes late

Expected:

```text
rejected because candidate base_epoch is stale and/or BindingID is non-current
```

---

## FTR11 — Ambiguous ownership

Expected:

```text
AMBIGUOUS
no semantic ownership commit
```

---

## FTR12 — Stale high-authority Evidence

Expected:

```text
insufficient for current correctness-sensitive action
```

---

## FTR13 — Tool-wait eviction

Expected:

```text
child Continuation preserves lineage
State may be recomputed
```

---

## FTR14 — Abandoned branch residual State

Expected:

```text
residual State cannot reactivate abandoned semantics or bypass compatibility
```

# 64. Probabilistic Failure Workloads

After deterministic traces pass, the simulator should generate statistically controlled failures.

Example parameters:

```text
worker_failure_rate
event_delay_mean
event_delay_tail
duplication_probability
observation_lag
cache_eviction_rate
retry_probability
migration_failure_rate
```

For each parameter configuration:

- use fixed random seeds;
- record seed;
- repeat enough trials for stable estimates;
- report confidence intervals where appropriate.

The experimental plan will define the exact statistical methodology.

---

# 65. Adversarial Versus Representative Workloads

The project must keep two failure-evaluation modes separate.

## Adversarial correctness mode

Purpose:

> find invariant violations.

Characteristics:

- intentionally pathological timing;
- worst-case event ordering;
- high retry overlap;
- forced migration races;
- forced contradictory evidence.

## Representative systems mode

Purpose:

> estimate practical behavior.

Characteristics:

- trace-driven arrival rates;
- realistic timeout/failure parameters;
- calibrated inference timing;
- realistic state pressure.

A system passing representative workloads but failing adversarial invariant tests is not Continuity-correct.

---

# 66. Fault Injection Must Preserve Ground Truth

Every simulated or distributed fault experiment must maintain an independent ground-truth record.

The experiment harness must know, independently of the policy under test:

```text
true current Attempt
true continuation ancestry
true State origin
true physical replica state
true committed Binding epoch
```

This ground truth is required to distinguish:

```text
incorrect policy decision
```

from:

```text
uncertain observation
```

The scheduler or baseline must not be given privileged access to ground truth unless that baseline explicitly assumes perfect information.

---

# 67. Baseline Information Models

Baseline comparisons must define an explicit information contract.

For every baseline record whether it receives:

```text
LogicalRequest identity
Attempt identity/authority
Session identity
Continuation identity/ancestry
State candidate key
exact StateID
State location
State provenance
producer Attempt
BindingID / epoch
Evidence authority/status/freshness
resource/load observations
```

Illustrative Paper 1 contracts:

## B0 — Request-centric

May receive:

```text
LogicalRequest identity
ordinary transport/request correlation
worker load
queue state
```

It may implement normal retry correctness mechanisms that are standard for its abstraction.

## B1 — Cache-aware

B0 plus cache/prefix locality and cache-presence information according to the modeled cache API.

## B2 — Session-affinity

B1 plus `SessionID` and prior preferred location.

It does not receive Continuation ancestry solely by virtue of Session identity.

## B3 — State-aware

Receives precise physical State identities/keys and locations **according to an explicitly modeled State-candidate interface**, but does not receive Continuation ancestry, producer-Attempt authority, Binding-generation semantics, or Continuity Evidence authority unless that information is inherently part of the chosen real baseline.

The exact candidate interface must be fixed before C3 and grounded against the systems being represented.

The experiment must not make B3 fail by arbitrarily withholding the exact State selector that its abstraction would normally possess.

## B4 — Continuity-aware

Receives the normalized Continuity context:

```text
Program
Session
Continuation
LogicalRequest
Attempt authority
State provenance / producer Attempt
State validity / lifecycle
BindingID / epoch
Evidence semantics
```

This makes the source of each information advantage explicit.

# 68. No Artificial Baseline Corruption

The failure model must not deliberately make baselines incorrect by removing mechanisms they ordinarily require for basic implementation correctness.

For example:

- a request-centric system may still have normal request identifiers;
- a cache-aware system may still validate physical cache existence according to its design;
- a session-affinity system may still detect worker failure.

The experimental distinction must arise from the **semantic abstraction under study**, not from intentionally broken baseline implementations.

This requirement is important for falsifiability.

---

# 69. Failure-Model Falsifiability

The Continuity correctness thesis is weakened if:

- baseline systems prevent the defined silent misassociations without Continuity lineage;
- wrong-branch reuse cannot arise under realistic state abstractions;
- retry-result authority is already fully solved by lower-level execution protocols in all relevant settings;
- binding epochs add no protection beyond ordinary state identifiers;
- ambiguous evidence never affects correctness-sensitive actions in plausible inference systems.

The experiments must therefore establish not merely that Continuity handles the faults, but that the fault classes expose a real semantic distinction from the baselines.

---

# 70. Out-of-Scope Failures

Paper 1 does not claim protection against the following.

## Byzantine components

A worker deliberately fabricates false state or completion evidence.

## Malicious identity forgery

An attacker successfully impersonates another Attempt, State, or binding authority.

## Undetectable data corruption

Physical State contents are corrupted while all metadata incorrectly indicates validity.

## Split-brain semantic authority

Two independent Continuity authorities simultaneously commit conflicting semantic state without a consensus or fencing mechanism.

## Durable-authority rollback

Committed authority state is lost and restored to an older generation without detection.

## Arbitrary application semantic errors

The application labels unrelated work as the same Continuation or supplies incorrect lineage.

## Incorrect engine adapter semantics

An adapter falsely declares incompatible engine state semantically valid.

The paper may discuss these as future extensions or limitations.

---

# 71. Adapter Trust Boundary

The Continuity Runtime relies on adapters for certain representation-specific facts.

Most importantly:

\[
SemanticValidity(x,c)
\]

may depend on engine-specific state compatibility.

If an adapter incorrectly reports:

```text
semantic validity = true
```

for physically incompatible State, Continuity cannot detect that incompatibility solely from lineage.

Therefore Paper 1 separates:

```text
continuity-level correctness
```

from:

```text
engine-adapter correctness
```

The experimental adapter must itself be tested against its declared semantics.

---

# 72. Failure Detection Is Not Instantaneous

A worker may have failed physically while the system still considers it available.

A State replica may have disappeared while its last observation remains valid-looking.

A network may be partitioned without immediate detection.

Continuity therefore must not require perfect instantaneous failure detection.

Instead, it relies on:

```text
evidence status
freshness
authority
generation
reconciliation
```

to bound what may be committed under uncertainty.

---

# 73. Unknown Versus Failed

The failure model distinguishes:

```text
FAILED
```

from:

```text
UNKNOWN
```

### FAILED

There is sufficient evidence that the operation/component/state is unavailable or unsuccessful.

### UNKNOWN

Available evidence is insufficient to establish either success or failure.

This distinction matters because:

```text
unknown execution
```

may still produce a late event.

Retrying unknown work therefore requires attempt fencing.

---

# 74. Failure Versus Supersession

An Attempt can be:

```text
SUPERSEDED
```

without being known physically failed.

This is intentional.

Example:

```text
A1 status physically unknown
        ↓
timeout
        ↓
A2 becomes authoritative
        ↓
A1 SUPERSEDED semantically
```

Supersession is about **authority**, not necessarily physical termination.

This distinction is fundamental to the model.

---

# 75. State Absence Versus State Invalidity

The failure model distinguishes:

### Absent

No usable physical replica is currently known.

### Invalid

The logical or physical state is prohibited from reuse.

A valid logical State with zero replicas may be recomputed.

An INVALID logical State must not be resurrected under the same semantic identity.

---

# 76. Migration Failure Versus Migration Ambiguity

### Migration failure

Evidence establishes that candidate migration cannot complete.

### Migration ambiguity

Available evidence cannot determine whether migration completed safely.

The second case is more dangerous because guessing may produce split authority.

Therefore ambiguous migration must fail closed.

---

# 77. Required Failure Metadata

Every injected fault should record:

```text
FaultID
fault_class
target
injection_time
duration
parameters
ground_truth_effect
expected_invariant_pressure
expected_safe_outcomes
```

Every observed semantic result should record:

```text
FaultID
policy
outcome_class
invariant_violations
recovery_action
recovery_latency
semantic_error
```

This structure will support reproducible experiment analysis.

---

# 78. Simulator Event Requirements

C2 must support events sufficient to instantiate this failure model.

At minimum:

```text
REQUEST_CREATED
ATTEMPT_STARTED
ATTEMPT_TIMEOUT
ATTEMPT_COMPLETED
ATTEMPT_FAILED
RETRY_STARTED

STATE_CREATED
STATE_MATERIALIZATION_STARTED
STATE_MATERIALIZED
STATE_TRANSFER_STARTED
STATE_TRANSFER_COMPLETED
STATE_EVICTED
STATE_LOST

MIGRATION_STARTED
MIGRATION_COMMITTED
MIGRATION_FAILED

WORKER_FAILED
WORKER_RECOVERED

OBSERVATION_CREATED
OBSERVATION_DELAYED
OBSERVATION_DROPPED
OBSERVATION_DUPLICATED

CONTINUATION_WAIT_STARTED
TOOL_RETURNED

CONTINUATION_FORKED
CONTINUATION_ABANDONED
CONTINUATION_TERMINATED
```

Events may be internally implemented differently.

The simulator must preserve these semantics.

---

# 79. C1 Versus C2 Failure Coverage

## C1 deterministic core

Must test logical failures without requiring real time or network.

Examples:

```text
late-event sequence
stale epoch
wrong branch
duplicate finalization
ambiguous evidence
```

## C2 simulator

Adds:

```text
time
queues
probabilities
message scheduling
failure distributions
resource pressure
```

## C8 distributed CPU prototype

Adds actual:

```text
process crashes
IPC/network delay
concurrency
real queueing
real serialization
```

The same semantic failures should exist across all three levels.

---

# 80. Cross-Layer Validation

A key methodology requirement is to replay equivalent fault scenarios across:

```text
C1 deterministic model
        ↓
C2 simulator
        ↓
C8 real CPU prototype
```

For correctness-equivalent traces, authoritative semantic outcomes should agree.

Example:

```text
late A1 after A2
```

must be rejected at every layer.

Performance timings may differ.

Semantic outcome should not.

---

# 81. Failure Coverage Matrix

The project should maintain a machine-readable coverage matrix:

```text
Failure:
    LATE_SUPERSEDED_ATTEMPT

Challenges:
    B4
    B5
    B7
    F3

C1:
    deterministic test

C2:
    adversarial event schedule

C8:
    real delayed worker response

Metric:
    stale_attempt_acceptance_rate
```

Every core Paper 1 failure class must eventually have equivalent metadata.

---

# 82. Gate G0 Failure-Model Acceptance Criteria

The failure model is sufficiently precise for C1/C2 only when:

1. trusted and untrusted components are explicit;
2. semantic authority assumptions are explicit;
3. asynchronous communication assumptions are explicit;
4. every required correctness failure maps to one or more invariants;
5. each fault has at least one defined safe outcome;
6. silent semantic violation is clearly distinguishable from explicit failure;
7. no safety claim depends on instantaneous failure detection;
8. no safety claim depends on globally synchronized clocks;
9. migration ambiguity has a fail-closed outcome;
10. state loss cannot implicitly rewrite state provenance;
11. retry semantics distinguish physical execution from logical authority;
12. out-of-scope Byzantine and split-authority cases are explicit;
13. baseline comparisons can be subjected to the same ground-truth fault traces;
14. deterministic and probabilistic fault-injection modes are both representable.

---

# 83. Gate G1 Correctness Requirement

At C4, Continuity must be evaluated against the core adversarial failure corpus.

For the failure classes claimed to be handled, the desired result is:

```text
Stale Attempt Acceptance Rate      = 0
Wrong-Branch Reuse Rate            = 0
Wrong-State Consumption Rate       = 0
Silent Binding Divergence Rate     = 0
Ambiguous Commit Rate              = 0
Duplicate Finalization Rate        = 0
```

under the model assumptions.

This does not require:

```text
Explicit Failure Rate = 0
```

or:

```text
Recomputation Rate = 0
```

Safety and availability are measured separately.

---

# 84. Canonical Failure Principle

The Continuity failure model can be summarized as:

> **Physical execution and physical state are allowed to become stale, duplicated, delayed, unavailable, or temporarily contradictory. Authoritative semantic state is not.**

Therefore:

```text
old execution may still run
but may not regain authority

old state may still exist
but may not become compatible by proximity

old binding may still emit events
but may not regain ownership

stale evidence may still be observed
but may not authorize current semantic commit

ambiguous reality may delay progress
but may not be silently guessed
```

This is the fundamental fault-containment boundary of Continuity-Aware Distributed Inference.