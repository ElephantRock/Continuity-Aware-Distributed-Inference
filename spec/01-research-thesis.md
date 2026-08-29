# 01 — Research Thesis
## Continuity-Aware Distributed Inference

**Working paper title:**  
**Continuity-Aware Distributed Inference: Causal Execution and State Lineage for Stateful Generative Workloads**

**Document role:** Canonical research contract  
**Milestone:** C0 — Research Specification  
**Status:** C0.1 normalized thesis candidate

---

# 1. Purpose

This document defines the scientific claim of **Continuity-Aware Distributed Inference** and establishes the boundaries within which the first research paper will operate.

It is intentionally narrower than the full project roadmap.

Its purpose is to ensure that subsequent architecture, implementation, simulation, experimentation, and integration work exists to test a clearly stated research proposition rather than to accumulate unrelated serving features.

The project may evolve, but changes to the thesis, core abstractions, safety properties, or required evidence should be treated as changes to the research program rather than ordinary implementation decisions.

---

# 2. Research Thesis

Modern distributed inference systems increasingly optimize requests using information about load, reusable state, cache locality, accelerator utilization, and session affinity.

These mechanisms remain incomplete for workloads whose logical execution persists across multiple requests, retries, asynchronous gaps, branches, state migrations, and distributed execution phases.

The central thesis of this work is:

> **Distributed inference should explicitly preserve the causal continuity between logical computation, execution attempts, reusable inference state, and physical execution. A runtime that represents this continuity can prevent classes of silent state and execution misassociation while enabling more effective state reuse under stateful, asynchronous, branching, and failure-prone generative workloads.**

The thesis has two distinct parts.

## 2.1 Correctness thesis

Distributed inference correctness requires more than identifying a request or locating reusable state.

The system must also establish:

- which logical computation is being executed;
- which execution attempt is currently authoritative;
- which reusable state is causally compatible with the active continuation;
- which state placement or ownership information is current;
- whether the evidence supporting a correctness-sensitive action is sufficiently authoritative.

Therefore:

> **Temporal proximity, physical locality, session equality, request equality, and cache similarity are insufficient substitutes for explicit causal identity when semantic correctness depends on the relationship between computation and state.**

## 2.2 Efficiency thesis

The same causal information used for correctness also exposes optimization opportunities unavailable to purely request-centric systems.

Specifically:

> **Knowledge of continuation lineage, reusable-state provenance, execution lifecycle, and state validity can reduce unnecessary recomputation, unnecessary migration, useless retention, and cold continuation execution in workloads with substantial state reuse.**

The project does not assume that Continuity improves every workload.

Independent stateless requests may receive little or no benefit and may incur small control-plane overhead.

The research question is therefore not whether Continuity always improves serving performance, but:

> **Under which workload, failure, and state-reuse conditions does explicit continuity provide measurable correctness or efficiency advantages over simpler serving abstractions?**

---

# 3. Problem Statement

Traditional inference serving can be approximated as:

```text
Request
   ↓
Schedule
   ↓
Execute
   ↓
Return result
```

This abstraction assumes that the request itself is the relevant unit of identity.

Emerging generative workloads instead exhibit longer-lived computation:

```text
Program
   │
   └── Session
          │
          ├── Continuation
          │      │
          │      ├── Request
          │      │     ├── Attempt 1
          │      │     └── Attempt 2
          │      │
          │      ├── Tool wait
          │      └── Resume
          │
          ├── Branch
          ├── Fan-out
          └── Join
```

At the same time, reusable inference state evolves independently:

```text
Reusable State
   │
   ├── accelerator-memory replica
   ├── host-memory replica
   ├── remote replica
   ├── migration
   ├── eviction
   └── reconstruction
```

A third structure describes execution resources:

```text
Workers
Accelerators
Memory tiers
Storage tiers
Networks
Failure domains
```

The research problem is the preservation of valid relationships between these independently evolving structures.

We define this problem as **distributed inference continuity**.

---

# 4. Definition of Continuity

Continuity is:

> **The preservation of valid causal relationships between logical inference computation, its execution instances, its reusable state, and its physical placement as those entities evolve independently in a distributed system.**

The project distinguishes four forms.

## 4.1 Execution continuity

The runtime can determine which execution attempt is currently authorized to affect a logical request.

## 4.2 State continuity

The runtime can determine whether a reusable-state object is causally compatible with the active continuation.

## 4.3 Placement continuity

The runtime can distinguish current valid state placements and ownership from stale or superseded placement information.

## 4.4 Program continuity

The runtime preserves logical relationships across request boundaries, including:

- continuation;
- suspension;
- retry;
- branch;
- fan-out;
- join;
- failure;
- migration;
- resumption.

These forms of continuity are related but not interchangeable.

A system may provide state locality without execution continuity, or session affinity without branch-aware state continuity.

---

# 5. Canonical Identity and Authority Model

The first paper adopts the following logical hierarchy:

```text
Program
   ↓
Session
   ↓
Continuation
   ↓
Logical Request
   ↓
Attempt
   ↓
Phase
```

Each level answers a different identity question.

## Program

What higher-level computation or intent is being completed?

## Session

Which long-lived stateful lineage does this work belong to?

## Continuation

Which exact position or branch within that lineage is active?

## Logical Request

Which inference operation did the application request?

## Attempt

Which concrete execution of that logical request is this?

## Phase

Which distributed sub-operation of the attempt is executing?

The paper explicitly rejects collapsing these identities into a single request identifier.

Attempt execution outcome and semantic authority are also distinct.

Conceptually:

```text
Attempt
├── ExecutionStatus
│   ├── CREATED
│   ├── DISPATCHED
│   ├── RUNNING
│   ├── SUCCEEDED
│   ├── FAILED
│   └── CANCELLED
│
└── AttemptAuthority
    ├── NONE
    ├── CURRENT
    ├── COMMITTED
    └── SUPERSEDED
```

Thus:

```text
Logical Request R17
├── A1 — execution SUCCEEDED / authority SUPERSEDED
└── A2 — execution SUCCEEDED / authority COMMITTED
```

is a valid representation of a retry race.

The runtime distinguishes:

```text
CurrentAttempt(R17)
```

from:

```text
CommittedAttempt(R17)
```

Likewise:

```text
Session S8
├── Continuation C11
└── Continuation C12
```

C11 and C12 share a Session but may represent incompatible branches.

# 6. Reusable Inference State

The paper treats KV cache as an important example of a more general abstraction:

## `ReusableInferenceState`

Each State object has at least:

```text
StateID
Origin
Semantic type
Representation
Producer Attempt where applicable
Compatibility relation
Physical replicas
Lifecycle
Validity
Evidence
```

A State object's **logical identity** is distinct from its **physical location**.

This distinction is necessary because:

```text
State S17
```

may simultaneously exist at:

```text
Worker A / accelerator memory
Worker A / host memory
Worker C / remote replica
```

while remaining one logical State object.

Conversely, two physically similar State objects may represent different Continuation lineages and therefore not be interchangeable.

The model also separates:

```text
StateLifecycle
    = expected future usefulness for retention
```

from:

```text
StateValidity
    = whether semantic reuse is permitted
```

The research contribution is not merely to discover where State exists.

It is to connect:

```text
State location
```

to:

```text
State provenance
+
Continuation ancestry
+
producer-Attempt authority
+
State validity
+
representation compatibility
```

# 7. Core Safety Properties

The correctness contribution is governed by the following properties.

## P1 — Attempt Safety

A result may finalize a LogicalRequest only if its Attempt is the current semantic authority at the commit point.

\[
Finalize(r,o)
\Rightarrow
attempt(o)=CurrentAttempt(r)
\]

Successful finalization records:

\[
CommittedAttempt(r)=attempt(o)
\]

A late result from a superseded Attempt must not finalize or overwrite the current LogicalRequest.

---

## P2 — State Compatibility

Reusable State may be consumed only if it is compatible with the exact consumer execution context \(\kappa\).

\[
Consume(x,\kappa)
\Rightarrow
Compatible(x,\kappa)
\]

Compatibility includes:

```text
Continuation ancestry
+
producer-Attempt authority where applicable
+
State validity
+
representation-specific validity
```

Physical locality alone is insufficient.

---

## P3 — Binding Validity

An ownership-sensitive operation may commit only against the current Binding generation and exact Binding candidate.

\[
OwnershipCommit(b)
\Rightarrow
baseEpoch(b)=CurrentEpoch(subject(b))
\]

and migration-sensitive events must match the relevant `BindingID`.

This provides fencing against stale ownership and losing concurrent migration candidates.

---

## P4 — Evidence Sufficiency

An action requiring a particular level of semantic authority may execute only when available evidence satisfies that requirement.

\[
Authority(e)
\ge
RequiredAuthority(a)
\]

Approximate evidence may be sufficient for an optimization decision while remaining insufficient for an ownership-changing decision.

---

## P5 — No Silent Ambiguity

When the runtime cannot establish sufficient evidence for a correctness-sensitive operation, uncertainty must remain explicit.

\[
Ambiguous(x)
\Rightarrow
\neg Commit(x)
\]

The runtime may:

- wait;
- retry;
- reconstruct;
- recompute;
- request stronger evidence;
- fail explicitly.

It may not silently convert ambiguity into semantic certainty.

---

## P6 — Supersession Fencing

Once attempt \(A_n\) supersedes \(A_{n-1}\), later events from \(A_{n-1}\) cannot mutate authoritative state belonging to the logical request.

This applies not only to final results but also to delayed state-transfer, ownership, completion, or reconciliation events.

# 8. Evidence Model

Distributed inference decisions frequently depend on observations of different quality.

The research therefore treats Evidence as a first-class object while keeping it distinct from authoritative semantic state.

Conceptually:

```text
Evidence<T> {
    value
    source
    authority
    status
    observed_at
    valid_until
    scope
    confidence
}
```

The canonical Evidence dimensions are separate.

## Authority

```text
ESTIMATED
DERIVED
EXACT_OBSERVATION
AUTHORITATIVE
```

For Paper 1 these form the total order:

\[
ESTIMATED < DERIVED < EXACT\_OBSERVATION < AUTHORITATIVE
\]

## Status

```text
VALID
STALE
UNKNOWN
FAILED
AMBIGUOUS
```

## Freshness

Freshness is action-relative.

Evidence may define a source-specific absolute validity bound:

```text
valid_until
```

while an action may additionally define:

```text
maxAge(action)
```

Evidence must satisfy both when applicable.

## Scope

Evidence may authorize actions only within the logical entities to which the Evidence refers.

## Confidence

Confidence is optional policy metadata for `ESTIMATED` or `DERIVED` Evidence.

Confidence cannot silently raise authority and cannot authorize a correctness-sensitive action unless an explicit action rule permits it.

The model also distinguishes Evidence from authoritative internal semantic state such as:

```text
CurrentAttempt(R)
CommittedAttempt(R)
CurrentBinding(S)
StateValidity(X)
```

An external observation does not redefine these merely by arriving.

---

# 9. Primary Research Questions

The first paper will answer the following questions.

### RQ1 — Execution correctness

Can explicit logical-request and attempt identities prevent stale, duplicated, superseded, or delayed executions from incorrectly completing current requests?

### RQ2 — State correctness

Can causal state lineage prevent reuse of locally attractive but logically incompatible inference state?

### RQ3 — Evidence and reconciliation

Can explicit evidence authority reduce silent incorrect decisions under stale, delayed, incomplete, or approximate observations?

### RQ4 — State reuse

Does continuation-aware state management reduce unnecessary recomputation compared with request-centric, cache-aware, and session-affinity approaches?

### RQ5 — Lifecycle-aware retention

Can continuation lifecycle improve useful-state retention under tool gaps, active branches, speculative branches, and completed computation?

### RQ6 — Failure recovery

Do binding epochs and state provenance improve safety and efficiency during worker failure, migration, and recovery?

### RQ7 — Program-level performance

Under which workload conditions does Continuity improve program completion time?

### RQ8 — Portability

Can the semantic model operate independently of a specific engine, gateway, resource orchestrator, or provider?

---

# 10. Primary Hypotheses

### H1

Attempt fencing prevents stale attempts from finalizing current logical requests under the modeled failure assumptions.

### H2

Explicit state lineage prevents wrong-branch and incompatible-state reuse.

### H3

Evidence-aware reconciliation reduces silent semantic errors compared with policies that treat the most recent or most likely observation as authoritative.

### H4

Continuation-aware routing reduces recomputation when substantial reusable state exists across logical continuations.

### H5

Lifecycle-aware retention improves useful-state residency relative to LRU, fixed TTL, and session-wide pinning in workloads containing tool gaps and branching.

### H6

Binding generations and state provenance reduce unsafe or redundant state migration under failover.

### H7

The performance benefit of Continuity increases as workloads become more stateful, including increases in:

- session depth;
- reusable-state size;
- branching;
- tool gaps;
- retry frequency;
- migration frequency.

H7 deliberately predicts **conditional**, not universal, superiority.

---

# 11. Claimed Novelty

The first paper does **not** claim novelty for individual mechanisms such as:

- request identifiers;
- generation counters;
- fencing tokens;
- cache-aware routing;
- session affinity;
- event sourcing;
- distributed tracing;
- DAG scheduling;
- state replication.

The novelty claim is the systematic composition of these principles into a distributed-inference abstraction that explicitly connects:

1. **hierarchical logical execution identity;**
2. **execution-attempt authority;**
3. **continuation-level causal lineage;**
4. **reusable inference-state provenance;**
5. **state compatibility;**
6. **evidence authority;**
7. **reconciliation of intended and observed inference state.**

The central novel proposition is:

> **Reusable inference state and distributed execution should be managed relative to causal logical lineage rather than only request identity, session affinity, physical locality, or observed cache similarity.**

Attempt fencing is treated as a required component of this composition, not as a standalone novelty claim.

The paper must demonstrate that this composition enables capabilities or safety properties not obtained merely by combining ordinary load balancing and cache locality.

# 12. Non-Claims

The first paper explicitly does **not** claim:

### Universal performance superiority

Continuity may impose overhead for independent or low-reuse workloads.

### New inference-engine mechanics

The work does not propose a new attention implementation, KV representation, model architecture, GPU kernel, or decoding algorithm.

### Exact hardware-performance predictions without appropriate evidence

Simulated H100, A100, TPU, or other accelerator results must be identified as simulated or derived unless directly measured.

### Replacement of infrastructure orchestration

Continuity does not replace Kubernetes, Slurm, cloud control planes, or equivalent systems.

### Replacement of inference gateways

Continuity supplies semantic execution and state information that gateways and schedulers may consume.

### Replacement of tracing

Tracing records execution observations.

Continuity represents authoritative logical relationships and uses observations as evidence.

### Complete program-aware scheduling

Critical-path scheduling, predictive fan-out, global autoscaling, energy optimization, and complete multi-agent orchestration remain future research unless required to validate the first paper.

---

# 13. Provider and Platform Neutrality

The research model must not require any specific:

- cloud provider;
- Kubernetes distribution;
- gateway;
- model server;
- inference engine;
- accelerator;
- scheduler;
- state-storage technology.

The formal model uses generic concepts such as:

```text
Worker
ExecutionLocation
ResourceDomain
StateLocation
StateTier
Scheduler
```

rather than:

```text
Pod
EC2 instance
GKE node
Azure VM
```

Specific platforms may be examined as:

- design references;
- comparison points;
- case studies;
- integration targets;
- portability demonstrations.

The Kubernetes Gateway API Inference Extension is intended as one **standards-based reference integration**, not as part of the core research definition.

---

# 14. Experimental Evidence Contract

The paper will distinguish explicitly between:

```text
measured
simulated
trace-derived
synthetically generated
analytically derived
estimated
```

evidence.

No result may be presented with stronger evidentiary language than the experiment supports.

To avoid collision with runtime `Evidence`, experimental validation layers use the `EV` prefix.

## EV0 — Deterministic correctness

Used to validate state machines and invariants.

## EV1 — Real CPU distributed execution

Used for concurrency, failure, event ordering, fencing, reconciliation, and control-plane overhead.

## EV2 — Public trace-driven workloads

Used to establish realistic arrival, token-length, burstiness, and prefix-reuse characteristics where the selected datasets support them.

## EV3 — Calibrated inference simulation

Used for modeled inference latency, recomputation, transfer, and program-level efficiency.

## EV4 — Physical accelerator validation

Optional.

Used only if available and required for direct physical-performance claims.

The paper remains valid without EV4 provided that accelerator-dependent claims are explicitly presented as simulated or modeled.

---

# 15. Synthetic Data Contract

Synthetic data is permitted where real datasets do not expose the semantic structures necessary to study Continuity.

Examples include:

- retry races;
- tool gaps;
- branch structure;
- continuation DAGs;
- worker failures;
- migrations;
- stale observations;
- ownership races.

Synthetic variables must not be represented by a single convenient configuration.

They must be evaluated through sensitivity analysis across meaningful ranges.

The objective is to identify:

> **regions of the workload and infrastructure parameter space in which Continuity matters.**

The desired output is therefore a trade-space or phase diagram rather than a single favorable benchmark result.

---

# 16. Required Baselines

At minimum, all central evaluations must compare against:

### B0 — Request-centric

Worker choice based primarily on current resource/load information.

### B1 — Cache-aware

Request-centric scheduling plus reusable-state locality.

### B2 — Session-affinity

Scheduling that prefers the location previously associated with the session.

### B3 — State-aware

Scheduling using accurate physical state location but without explicit causal continuation lineage.

### B4 — Continuity-aware

The proposed architecture:

```text
execution lineage
+
state lineage
+
attempt fencing
+
evidence authority
+
reconciliation
```

These baselines isolate what Continuity contributes beyond increasingly sophisticated locality mechanisms.

---

# 17. Required Failure Cases

The correctness claim must be evaluated against at least:

- late completion from a superseded attempt;
- duplicate completion;
- retry race;
- worker crash;
- event reordering;
- stale state-location observation;
- binding replacement;
- wrong-branch reusable state;
- partial state migration;
- delayed migration completion;
- ambiguous ownership;
- state eviction during suspension.

The benchmark must distinguish:

```text
explicit failure
```

from:

```text
silent incorrect success
```

because one of the core claims is that Continuity converts certain ambiguous outcomes into observable wait, recovery, recomputation, or failure rather than plausible but semantically incorrect success.

---

# 18. Primary Metrics

## Correctness

- Stale Attempt Acceptance Rate
- Wrong-State Consumption Rate
- Wrong-Branch Reuse Rate
- Silent Binding Divergence Rate
- Ambiguous Commit Rate
- Duplicate Finalization Rate

## Efficiency

- Recomputation Ratio
- State Reuse Ratio
- State Transfer Volume
- Useful State Residency
- Wasted State Residency
- Migration Cost
- Cold Continuation Rate

## Program-level performance

- Program Completion Time
- Critical Path Delay
- Tool-Return TTFT
- Branch Join Latency
- Fan-Out Completion Time
- Recovery Time

## Runtime overhead

- scheduling-decision latency;
- reconciliation latency;
- event throughput;
- graph-memory footprint;
- state-directory memory;
- CPU overhead;
- metadata bytes per request;
- control-plane network volume.

---

# 19. Falsifiability

The thesis must be allowed to fail.

The initial research proposition should be reconsidered if one or more of the following occur.

## F1 — No meaningful correctness distinction

If request-, state-, or session-aware baselines already eliminate the defined stale/wrong-state failures without requiring causal Continuity, then the correctness novelty is substantially weakened.

## F2 — State lineage adds no useful discrimination

If physical state identity and session affinity are sufficient to establish safe state reuse under the tested workload classes, continuation-level lineage may be unnecessary.

## F3 — Evidence semantics provide no meaningful benefit

If authority-aware reconciliation produces no important safety distinction over ordinary freshness/confidence policies, the evidence model should be simplified.

## F4 — Continuity overhead dominates

If metadata, graph maintenance, evidence processing, or reconciliation adds unacceptable control-plane cost relative to inference operations, the architecture may be impractical.

## F5 — Efficiency benefits occur only under unrealistic workloads

If recomputation, retention, or completion-time improvements require implausible branch factors, failure rates, tool gaps, or state reuse, the efficiency thesis must be narrowed.

## F6 — The abstraction cannot remain portable

If the semantic model fundamentally depends on one engine, scheduler, state representation, gateway, or orchestrator, the provider-neutrality claim fails.

A negative result on an efficiency hypothesis does not automatically invalidate the correctness contribution.

The paper must report such outcomes rather than modifying workloads until favorable results appear.

---

# 20. Minimum Publication Bar

The first paper should not be considered ready unless all of the following are satisfied.

## Conceptual

Continuity has a precise definition distinct from cache locality and session affinity.

## Formal

The governing safety properties have explicit semantics.

## Correctness

At least one significant failure class produces silent semantic misassociation in a baseline while Continuity prevents it or converts it into explicit recovery/failure.

## Experimental

Evaluation combines controlled adversarial workloads with public trace-derived workload characteristics where applicable.

## Efficiency

Either:

- Continuity demonstrates measurable efficiency improvement over a meaningful region of the parameter space;

or:

- the paper establishes a meaningful correctness-versus-efficiency frontier unavailable to simpler baselines.

## Practicality

A real CPU-based distributed implementation demonstrates that the Continuity mechanisms can operate under actual asynchronous execution with measurable and acceptable overhead.

## Portability

The same semantic core is demonstrated through at least two materially different integration environments.

One may be the Kubernetes Gateway API Inference Extension reference integration.

## Reproducibility

The experiment artifact reproduces every reported central result from declared configurations and random seeds.

---

# 21. Research Scope for Paper 1

The implementation scope is deliberately restricted to four policies:

## 1. Attempt fencing

Reject authoritative effects from superseded attempts.

## 2. Compatible-state routing

Permit reuse only when state is compatible with the active continuation.

## 3. Lifecycle-aware retention

Initial lifecycle classes:

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

## 4. Safe migration

Require valid ownership/state evidence before committing migration-sensitive operations.

The paper may discuss broader consequences but should not require implementation of:

- full continuity-aware autoscaling;
- predictive fan-out replication;
- complete program critical-path scheduling;
- multi-region optimization;
- cross-cloud placement;
- energy-aware scheduling;
- global planning;
- complete multi-agent orchestration.

---

# 22. Relationship to Prior Project Work

The Continuity research program originates from a broader causal-correlation principle:

> An observed result should not be associated with an action merely because it appeared afterward; the system should establish a durable identity and verify the causal relationship where correctness depends on that association.

In this research, that principle is generalized from individual interaction correlation into distributed inference:

```text
latest result
    ≠ necessarily active-attempt result

same request
    ≠ necessarily same execution

same session
    ≠ necessarily same continuation

local state
    ≠ necessarily compatible state

cached state
    ≠ necessarily authoritative state

observed placement
    ≠ necessarily current placement
```

The research contribution is the general distributed-inference model derived from that principle, not any browser-specific mechanism that motivated it.

---

# 23. Research Decision Rule

From C0 onward, every proposed addition must answer four questions:

### 1. Which research question does this address?

### 2. Which hypothesis or safety property does it support?

### 3. Which experiment requires it?

### 4. What evidence will determine whether it works?

If an addition cannot answer these questions, it does not belong on the critical path for Paper 1.

---

# 24. Final Thesis Statement

The project will proceed under the following canonical thesis:

> **Stateful generative workloads transform distributed inference from a sequence of independent requests into an evolving causal computation whose execution and reusable state may be distributed, replicated, retried, suspended, migrated, and branched. Existing request-, cache-, and session-oriented abstractions do not fully represent these relationships. Continuity-Aware Distributed Inference introduces explicit execution lineage, reusable-state lineage, attempt authority, evidence semantics, and reconciliation so that distributed serving decisions can preserve causal correctness while exploiting reusable state.**

The first paper will determine whether this abstraction provides a meaningful systems advantage over simpler request-, cache-, session-, and state-aware alternatives.

If the experiments do not support that proposition, the project will revise or reject the thesis rather than redefine success after implementation.
