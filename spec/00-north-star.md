# Continuity-Aware Distributed Inference
## North Star Research & Engineering Roadmap

**Working paper title:**  
**Continuity-Aware Distributed Inference: Causal Execution and State Lineage for Stateful Generative Workloads**

**Project status:** C0.1 normalized research program definition  
**Primary orientation:** Distributed systems / inference serving / stateful generative workloads  
**Design constraint:** Provider-, engine-, gateway-, and orchestrator-neutral

---

# 1. Mission

Develop and rigorously evaluate a general systems abstraction for preserving **causal execution continuity** and **reusable inference-state continuity** across distributed generative workloads.

The project addresses workloads that can no longer be modeled adequately as isolated inference requests because they contain:

- long-lived sessions;
- retries;
- asynchronous execution;
- tool pauses;
- state reuse;
- state migration;
- prefill/decode separation;
- branching and fan-out;
- joins;
- subagents;
- failures and recovery;
- replicated inference state;
- heterogeneous execution locations.

The central premise is:

> Distributed inference systems increasingly know where computation and state are located, but lack a unified abstraction describing which logical computation that state belongs to, which execution attempt is authoritative, and whether an observed State or result is causally valid for the exact current execution context.

The project introduces **Continuity-Aware Distributed Inference** as that missing abstraction.

---

# 2. North Star

The project succeeds if it demonstrates the following proposition:

> **A distributed inference runtime that explicitly represents execution identity, causal lineage, reusable-state lineage, evidence authority, and execution lifecycle can prevent silent state/execution misassociation and enable more efficient state reuse than request-centric serving under stateful, asynchronous, branching, and failure-prone workloads.**

Every project activity must support one of two objectives:

### Correctness

Prevent distributed inference from silently associating:

- a result with the wrong attempt;
- state with the wrong continuation;
- ownership with a stale binding;
- ambiguous observations with authoritative state.

### Efficiency

Use continuity knowledge to reduce:

- unnecessary recomputation;
- unnecessary state migration;
- useless state retention;
- cold continuation execution;
- program critical-path delay.

If a proposed feature contributes to neither objective, it is outside the initial research scope.

---

# 3. What This Project Is Not

The work is **not**:

- a Kubernetes paper;
- a GKE, EKS, or AKS paper;
- a vLLM or SGLang optimization;
- a new GPU kernel;
- a KV-cache implementation;
- an agent framework;
- a new network protocol;
- a cloud cost-comparison paper;
- a replacement for distributed tracing;
- a replacement for Kubernetes or other resource orchestrators.

These technologies may be studied, compared, integrated, or used experimentally.

None defines the research contribution.

---

# 4. Research Thesis

Existing serving systems can be viewed as evolving through four stages:

```text
Generation 0
Load-aware serving

Request
   ↓
Least-loaded worker


Generation 1
Cache-aware serving

Request
   ↓
Load + reusable state
   ↓
Worker


Generation 2
Session-aware serving

Session
   ↓
Affinity + state locality
   ↓
Worker


Generation 3
Continuity-aware serving

Program
   ↓
Continuation
   ↓
Valid causal state
   ↓
Authoritative attempt
   ↓
Distributed execution
```

The research targets **Generation 3**.

The primary intellectual transition is:

> from scheduling individual requests to preserving the continuity of logical computation and its reusable state.

---

# 5. Core Research Questions

## RQ1 — Execution correctness

Can explicit logical-request and attempt identities prevent stale, duplicated, superseded, or delayed executions from incorrectly completing current requests?

## RQ2 — State correctness

Can causal state lineage prevent inference workers from reusing state that is locally attractive but logically incompatible with the active continuation?

## RQ3 — Evidence and reconciliation

Can explicit evidence provenance and authority prevent stale or approximate observations from being used incorrectly for correctness-sensitive decisions?

## RQ4 — State reuse

Does continuation-aware state management reduce recomputation compared with request-centric, cache-aware, and session-affinity approaches?

## RQ5 — Lifecycle-aware retention

Can knowledge of tool waits, active branches, completed branches, and expected continuation behavior improve reusable-state retention efficiency?

## RQ6 — Distributed failure recovery

Can binding epochs and reconciliation produce safer migration and failover under worker loss, delayed events, and state replication?

## RQ7 — Program-level performance

Under what workloads does continuity knowledge improve overall program completion time rather than merely individual-request latency?

## RQ8 — Portability

Can the model operate independently of a particular inference engine, scheduler, resource orchestrator, gateway, or cloud provider?

---

# 6. Core Hypotheses

### H1 — Attempt fencing

A continuity-aware runtime reduces stale-attempt acceptance to zero under the modeled failure assumptions.

### H2 — State compatibility

Explicit lineage prevents wrong-branch and incompatible-state reuse.

### H3 — Evidence authority

Authority-aware reconciliation trades some speculative availability for substantially lower silent semantic error.

### H4 — Stateful efficiency

Continuity-aware routing reduces unnecessary recomputation when workloads contain substantial continuation reuse.

### H5 — Lifecycle retention

Typed state lifecycle policies improve useful-state residency compared with plain LRU, fixed TTL, and session pinning.

### H6 — Failure recovery

Binding epochs and state provenance reduce unsafe or redundant state migration.

### H7 — Program optimization

The benefit of continuity increases with session depth, branching, tool gaps, retry frequency, and reusable-state size.

---

# 7. Canonical Execution Model

The research uses the following logical hierarchy:

```text
Program
   │
   └── Session
          │
          └── Continuation
                 │
                 └── Logical Request
                        │
                        └── Attempt
                               │
                               └── Phase
```

## Program

A complete higher-level unit of intent.

Examples:

- complete a coding task;
- conduct a research workflow;
- execute an agent plan;
- generate a multi-stage artifact.

## Session

A long-lived stateful lineage within a program.

## Continuation

A specific position or branch in session history.

Continuations may:

- extend another continuation;
- fork;
- suspend;
- resume;
- join;
- terminate.

## Logical Request

One inference operation requested by the application.

## Attempt

One execution attempt of a logical request.

Retries create new attempts.

## Phase

A sub-operation of an attempt.

Examples:

- prefill;
- decode;
- KV fetch;
- KV transfer;
- encoder execution;
- remote state restoration.

---

# 8. Reusable-State Model

The system treats KV cache as an important instance of a broader abstraction:

## `ReusableInferenceState`

A state object includes:

```text
StateID

Origin
    Program
    Session
    Continuation
    Request / Phase

Semantic class

Representation

Compatibility information

Physical replicas

Lifecycle

Retention intent

Evidence
```

Possible semantic classes include:

- system context;
- conversation state;
- tool catalogue;
- retrieved context;
- reasoning branch;
- vision encoder output;
- prefix representation;
- model-specific intermediate state.

Possible physical tiers include:

- accelerator memory;
- host memory;
- local storage;
- remote state service.

The model must therefore distinguish:

> **logical state identity**

from:

> **physical state placement**.

---

# 9. Three Graphs

The research models three independently evolving graphs.

## Execution graph

```text
Program
  ↓
Session
  ↓
Continuation DAG
  ↓
Request
  ↓
Attempt
  ↓
Phase
```

## State graph

```text
Logical state
   │
   ├── derived-from
   ├── shared-by
   └── supersedes
```

## Resource graph

```text
Workers
Accelerators
Memory tiers
Storage tiers
Network links
Failure domains
```

Continuity is the mechanism that keeps relationships between these graphs valid.

That relationship is the central systems abstraction.

---

# 10. Definition of Continuity

The project distinguishes four forms.

## Execution continuity

The system can identify which execution attempt is currently authoritative.

## State continuity

The system can determine whether reusable inference state is causally compatible with the active continuation.

## Placement continuity

The system can identify valid current placements and replicas of reusable state.

## Program continuity

The runtime preserves semantic relationships across retries, branches, tool waits, migration, fan-out, and joins.

---

# 11. Governing Safety Invariants

These invariants form the correctness kernel of the project.

## I1 — Attempt safety

A result can finalize a logical request only if it belongs to the currently valid attempt.

\[
Finalize(r,o)
\Rightarrow
Attempt(o)=ActiveAttempt(r)
\]

---

## I2 — State compatibility

Reusable state may be consumed only if it is compatible with the active continuation.

\[
Consume(s,c)
\Rightarrow
Compatible(s,c)
\]

---

## I3 — Binding validity

Ownership-changing operations must use the current binding generation.

\[
Use(b)
\Rightarrow
Epoch(b)=CurrentEpoch
\]

---

## I4 — Evidence sufficiency

Correctness-sensitive actions require evidence of sufficient authority.

\[
Authority(e)
\ge
RequiredAuthority(action)
\]

---

## I5 — No silent ambiguity

Ambiguous evidence cannot authorize a semantic commit.

\[
Ambiguous(x)
\Rightarrow
\neg Commit(x)
\]

---

## I6 — Superseded execution fencing

Once attempt \(A_n\) supersedes \(A_{n-1}\), subsequent events from \(A_{n-1}\) cannot mutate authoritative logical state.

---

# 12. Evidence Model

Every important observation may carry:

```text
Evidence<T> {
    value
    source
    authority
    freshness
    observed_at
    scope
    confidence
    status
}
```

Initial authority classes:

```text
AUTHORITATIVE
EXACT_OBSERVATION
DERIVED
ESTIMATED
STALE
UNKNOWN
FAILED
```

This distinguishes:

```text
"I estimate that state exists on W3"
```

from:

```text
"the runtime confirmed that state exists on W3"
```

These may lead to different actions.

---

# 13. Governing Policy Principle

## Performance decisions may degrade.

Example:

```text
exact cache information unavailable
        ↓
use approximate locality estimate
        ↓
possibly suffer recomputation
```

This is acceptable because the consequence is primarily performance.

## Correctness-sensitive decisions fail closed.

Example:

```text
authoritative session owner unknown
        ↓
do not silently transfer ownership
```

or:

```text
active attempt uncertain
        ↓
do not accept terminal result
```

This distinction is inherited from the project's original causal-correlation work and becomes a general distributed-inference principle.

---

# 14. Initial Continuity Runtime

The first implementation should contain only five fundamental services.

```text
                  Continuity Runtime
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
  Identity Store    Execution Graph    State Directory
        │                │                │
        └────────────────┼────────────────┘
                         ▼
                    Evidence Layer
                         │
                         ▼
                     Reconciler
```

## Identity Store

Creates and tracks:

```text
ProgramID
SessionID
ContinuationID
LogicalRequestID
AttemptID
PhaseID
BindingEpoch
```

## Execution Graph

Stores causal execution relationships.

## State Directory

Stores logical state provenance and known physical replicas.

## Evidence Layer

Associates state claims with provenance and authority.

## Reconciler

Compares:

```text
desired logical state
```

against:

```text
observed distributed state
```

and determines whether to:

```text
continue
wait
recompute
migrate
retry
reject
repair
```

---

# 15. Provider-Neutral Adapter Model

The runtime exposes generic interfaces.

## Execution Adapter

```text
submit()
cancel()
supersede()
complete()
```

## State Adapter

```text
locate()
materialize()
replicate()
transfer()
evict()
```

## Scheduler Adapter

```text
admit()
rank()
place()
migrate()
```

## Observation Adapter

```text
observe_execution()
observe_state()
observe_resources()
```

An implementation may map these onto:

- Kubernetes;
- Slurm;
- Ray;
- bare metal;
- VMs;
- custom cloud schedulers;
- existing inference gateways.

No implementation is normative.

---

# 16. Research Testbed Architecture

The first experimental system should deliberately be minimal:

```text
              Workload Generator
                     │
                     ▼
             Continuity Router
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
       Worker A   Worker B   Worker C
          │          │          │
          └──────────┼──────────┘
                     ▼
                 State Layer
```

Avoid Kubernetes initially.

This isolates the contribution from platform behavior.

---

# 17. Experimental Evidence Strategy

The project will use four complementary evidence classes.

## E0 — Formal / deterministic correctness

Used for:

- invariants;
- attempt fencing;
- state compatibility;
- binding epochs;
- reconciliation state machines.

No GPU required.

## E1 — Real CPU distributed execution

Independent processes and real asynchronous communication.

Used for:

- concurrency;
- late responses;
- crashes;
- duplicate delivery;
- event reordering;
- queue behavior;
- metadata/control-plane overhead.

## E2 — Trace-driven simulation

Uses public workload traces for realistic:

- arrival distributions;
- request sizes;
- output sizes;
- prefix/cache patterns.

## E3 — Calibrated inference simulation

Uses validated performance models or published measurements to estimate:

- prefill latency;
- decode latency;
- recomputation;
- transfer;
- TTFT;
- throughput.

## E4 — Optional physical GPU validation

Used only if available.

It strengthens physical-performance claims but is **not a prerequisite for validating the core Continuity contribution**.

---

# 18. Data Strategy

Avoid purely synthetic evaluation.

Use:

```text
Real workload distributions
        +
Synthetic continuity structure
        +
Controlled fault injection
```

Public data may provide:

- production request arrivals;
- token lengths;
- load bursts;
- prefix-overlap patterns;
- cache-sharing patterns.

Synthetic augmentation supplies otherwise unavailable information:

- session structure;
- tool gaps;
- retries;
- failure races;
- fan-out;
- joins;
- continuation DAGs;
- state migration.

Every synthetic parameter must be sensitivity-tested across a range rather than fixed at one convenient value.

---

# 19. Workload Families

The benchmark suite should contain at least the following.

## W1 — Independent requests

Control workload.

Tests whether Continuity imposes unnecessary overhead when continuity is irrelevant.

## W2 — Deep sessions

Long chains of high prefix reuse.

## W3 — Tool-gap sessions

```text
Inference
  ↓
Tool wait
  ↓
Continuation
```

## W4 — Retry races

Late results from superseded attempts.

## W5 — Stateful failover

Worker failure while reusable state exists locally or remotely.

## W6 — Branching programs

```text
      C0
    / | \
   C1 C2 C3
```

## W7 — Fan-out / fan-in

Large common prefix followed by parallel branches and join.

## W8 — Cache pressure

Active, waiting, speculative, and terminal states competing for limited capacity.

## W9 — Stale observation

Scheduler operates under increasing evidence delay.

## W10 — Migration

Trade state transfer against recomputation.

---

# 20. Baselines

At minimum compare:

## B0 — Request-centric

Load-based worker selection.

## B1 — Cache-aware

Load + reusable-prefix locality.

## B2 — Session affinity

Session prefers previous worker.

## B3 — State-aware

Precise state location without causal lineage.

## B4 — Continuity-aware

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

---

# 21. Correctness Metrics

Primary:

```text
Stale Attempt Acceptance Rate

Wrong-State Consumption Rate

Wrong-Branch Reuse Rate

Silent Binding Divergence Rate

Ambiguous Commit Rate

Duplicate Finalization Rate
```

For the proposed system, the ideal result for the modeled threat classes is:

```text
0
```

or a formally explained residual.

---

# 22. Efficiency Metrics

```text
Recomputation Ratio

State Reuse Ratio

State Transfer Volume

Useful State Residency

Wasted State Residency

Migration Cost

Cold Continuation Rate
```

---

# 23. Program-Level Metrics

```text
Program Completion Time

Critical Path Delay

Tool-Return TTFT

Branch Join Latency

Fan-Out Completion Time

Recovery Time
```

These should eventually matter more than isolated request latency for agentic workloads.

---

# 24. Traditional Serving Metrics

Retain:

```text
TTFT
TPOT
ITL
throughput
queue latency
resource utilization
```

but do not allow them to dominate the research narrative.

---

# 25. Control-Plane Metrics

Measure the cost of Continuity itself:

```text
decision latency
reconciliation latency
events processed/sec
graph memory footprint
state-directory memory
CPU overhead
metadata bytes/request
network event volume
```

A correctness mechanism that requires unreasonable control overhead would not be useful.

---

# 26. Initial Policies to Implement

Keep the first paper narrow.

Implement only:

### P1 — Attempt fencing

Reject results from superseded attempts.

### P2 — Compatible-state routing

Use reusable state only when lineage-compatible.

### P3 — Lifecycle-aware retention

Initial lifecycle:

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

### P4 — Safe migration

Require appropriate state/ownership evidence before committing migration.

Do **not** initially implement:

- full autoscaling;
- global program scheduling;
- predictive fan-out;
- multi-cloud placement;
- sophisticated cost optimization;
- energy-aware scheduling.

Those are future extensions.

---

# 27. Implementation Roadmap

## C0 — Research Specification

### Goal

Freeze the intellectual core.

### Deliverables

```text
problem statement
terminology
execution model
state model
evidence model
failure model
invariants
research questions
hypotheses
```

### Exit criteria

Every proposed mechanism maps to a hypothesis.

No unresolved ambiguity remains around what “continuity” means.

---

## C1 — Deterministic Continuity Core

### Implement

```text
identity hierarchy
execution DAG
state lineage
attempt state machine
binding epochs
evidence objects
reconciliation rules
```

### Tests

Exhaustive unit/property tests for:

```text
retry
supersession
branching
state compatibility
stale epochs
ambiguous evidence
```

### Exit criterion

Core safety invariants pass deterministic adversarial tests.

---

## C2 — Discrete-Event Simulator

### Implement

Entities:

```text
Program
Session
Continuation
Request
Attempt
Worker
StateBlock
StateReplica
NetworkLink
Evidence
```

Events:

```text
REQUEST_CREATED
ATTEMPT_STARTED
STATE_CREATED
STATE_MOVED
ATTEMPT_TIMEOUT
RETRY
WORKER_FAILED
LATE_RESULT
TOOL_WAIT
TOOL_RETURN
FORK
JOIN
EVICTION
```

### Exit criterion

All workload/failure classes can be represented deterministically with reproducible random seeds.

---

## C3 — Baseline Policies

Implement:

```text
request-centric
cache-aware
session-affinity
state-aware
continuity-aware
```

### Exit criterion

All policies execute against identical simulator interfaces and workloads.

---

## C4 — Correctness Evaluation

Run systematic fault injection:

```text
late result
duplicate result
event reordering
worker crash
stale state index
migration race
concurrent retry
wrong branch
binding replacement
```

### Primary result

Show whether Continuity eliminates silent incorrect outcomes.

### Decision Gate G1

If Continuity cannot demonstrate a clear correctness distinction from simpler baselines, reconsider the research thesis before proceeding.

---

## C5 — Public Trace Integration

Ingest real inference traces.

Extract:

```text
arrival distribution
request length
output length
load burstiness
prefix reuse where available
```

Implement trace augmentation for:

```text
sessions
tool waits
branches
failures
```

### Exit criterion

The experiment framework supports:

```text
real
trace-augmented
fully synthetic
```

workload classes.

---

## C6 — Inference Cost Model

Integrate or implement a calibrated serving-performance model.

Required outputs:

```text
prefill cost
decode cost
recompute cost
transfer cost
memory footprint
```

Potentially use an existing validated CPU simulator where appropriate.

### Exit criterion

Every physical-performance parameter is sourced from:

```text
published measurement
validated simulator
or explicitly synthetic sensitivity range
```

No unexplained constants.

---

## C7 — Efficiency Evaluation

Evaluate:

```text
routing
state retention
state reuse
migration
tool gaps
branching
```

across broad parameter sweeps.

### Decision Gate G2

Continuity should demonstrate either:

- meaningful efficiency improvement in identifiable workload regions; or
- a clear correctness/efficiency frontier unavailable to baselines.

If not, the contribution remains primarily correctness-oriented and the paper must say so.

---

## C8 — Real CPU Distributed Prototype

Replace simulated workers with independent processes/services.

Inject:

```text
real process termination
network delay
duplicate messages
message reordering
late completion
queue failure
```

Measure:

```text
control overhead
decision latency
reconciliation latency
failure behavior
```

### Exit criterion

Core safety behavior survives real asynchronous execution.

---

## C9 — Standards-Based Reference Integration

Use the **Kubernetes Gateway API Inference Extension** as one independent application example.

Research remains independent.

Reference artifact may be called:

```text
Continuity-GIE
```

The integration demonstrates how:

```text
ContinuityContext
State Compatibility
Evidence
Lifecycle
```

can participate in endpoint selection without modifying the underlying research model.

### Exit criterion

The same Continuity Core operates unchanged with the Gateway adapter.

---

## C10 — Portability Demonstration

Add at least one second adapter or minimal alternative environment.

Possible categories:

```text
custom scheduler
non-Kubernetes runtime
alternative inference router
```

### Goal

Demonstrate:

> continuity semantics are not Gateway- or Kubernetes-specific.

---

## C11 — Optional GPU Validation

If accelerator resources become available, conduct a focused validation rather than redesigning the paper around GPUs.

Validate:

```text
cold vs warm continuation TTFT
recompute cost
state retention effect
transfer/recompute crossover
simulator error
```

### Rule

Do not expand into hardware optimization research.

---

## C12 — Artifact Hardening

Produce:

```text
reproducible experiment scripts
fixed random seeds
experiment manifests
public datasets/download instructions
analysis notebooks/scripts
tables
figures
CI
artifact documentation
```

Every paper result must be reproducible from a declared experiment configuration.

---

# 28. Proposed Repository Structure

```text
continuity-aware-inference/
│
├── README.md
├── LICENSE
│
├── spec/
│   ├── research-thesis.md
│   ├── terminology.md
│   ├── execution-model.md
│   ├── state-model.md
│   ├── evidence-model.md
│   ├── invariants.md
│   └── failure-model.md
│
├── continuity/
│   ├── identity/
│   ├── execution/
│   ├── state/
│   ├── evidence/
│   ├── reconciliation/
│   └── policy/
│
├── simulator/
│   ├── engine/
│   ├── events/
│   ├── workers/
│   ├── network/
│   ├── state/
│   └── faults/
│
├── baselines/
│   ├── request_centric/
│   ├── cache_aware/
│   ├── session_affinity/
│   └── state_aware/
│
├── workloads/
│   ├── synthetic/
│   ├── trace_driven/
│   └── augmentation/
│
├── experiments/
│   ├── attempt_fencing/
│   ├── wrong_state/
│   ├── evidence_staleness/
│   ├── retention/
│   ├── migration/
│   ├── tool_gap/
│   ├── branching/
│   └── fanout/
│
├── adapters/
│   ├── reference/
│   └── gateway_api/
│
├── prototype/
│   ├── router/
│   ├── worker/
│   └── state_service/
│
├── analysis/
│   ├── metrics/
│   ├── statistics/
│   ├── plots/
│   └── tables/
│
└── paper/
```

---

# 29. Paper Structure

## 1. Introduction

Establish the transition from independent requests to stateful generative programs.

Introduce continuity as the missing abstraction.

## 2. Motivation and Failure Model

Present concrete failures:

```text
stale retry completion
wrong state reuse
stale ownership
tool-gap state loss
branch-state confusion
```

## 3. Continuity Model

Define:

```text
Program
Session
Continuation
Request
Attempt
Phase
ReusableInferenceState
Evidence
```

## 4. Safety Properties

Present the governing invariants.

## 5. Architecture

Describe:

```text
execution graph
state directory
evidence layer
reconciler
```

## 6. Policies

Describe:

```text
attempt fencing
compatible-state routing
lifecycle retention
safe migration
```

## 7. Implementation

Provider-neutral Continuity prototype.

## 8. Experimental Methodology

Explain:

```text
CPU distributed tests
public traces
trace augmentation
simulation
fault injection
```

## 9. Evaluation

Answer RQ1–RQ8.

## 10. Standards-Based Integration Case Study

Kubernetes Gateway API Inference Extension.

Explicitly state it is an **example integration**, not a dependency.

## 11. Generality and Portability

Discuss other serving/orchestration architectures.

## 12. Related Work

Organize by concept:

```text
distributed inference
cache-aware routing
stateful scheduling
distributed tracing
workflow engines
actors
event sourcing
agent serving
```

## 13. Limitations

Be explicit about simulated versus physical measurements.

## 14. Conclusion

Reassert continuity as a distributed inference abstraction.

---

# 30. Evidence Discipline

Every paper result must be classified.

Example:

```text
Claim:
Attempt fencing eliminates stale finalization.

Evidence:
real CPU distributed experiment.


Claim:
Continuity reduces program completion time by X%.

Evidence:
trace-driven calibrated simulation.


Claim:
Policy works with standardized inference routing.

Evidence:
real Gateway API integration.


Claim:
Would improve H100 throughput.

Evidence:
not permitted unless directly measured or explicitly modeled.
```

The project must never blur:

```text
measured
simulated
derived
synthetic
estimated
```

results.

This should itself become part of the artifact metadata.

---

# 31. Sensitivity Analysis Requirement

Every important synthetic variable must be swept across a meaningful range.

Examples:

```text
branch factor
tool wait
failure rate
state size
cache capacity
evidence age
network bandwidth
transfer latency
session depth
retry timeout
worker count
arrival intensity
```

Avoid conclusions that depend on one hand-picked configuration.

The desired result is a **phase diagram** answering:

> Under what workload and infrastructure conditions does Continuity matter?

That is scientifically more valuable than a single percentage improvement.

---

# 32. Ablation Plan

Evaluate:

```text
Full Continuity

− AttemptID

− BindingEpoch

− State lineage

− Evidence authority

− Reconciliation

− Lifecycle information
```

For each removal, identify:

```text
which correctness failure returns
which efficiency benefit disappears
what overhead is saved
```

This demonstrates causality between mechanisms and observed benefits.

---

# 33. Scope Control

The initial paper must not absorb every possible future capability.

The following belong to **future work unless required by evaluation**:

```text
continuity-aware autoscaling
program critical-path scheduling
predictive fan-out replication
multi-region optimization
energy-aware scheduling
full multi-agent orchestration
RL rollout scheduling
cross-provider state placement
state markets
global planner integration
```

These can become later research.

---

# 34. Future Research Program

If Paper 1 validates the abstraction, the project can branch into several papers.

## Paper A — Current

### Continuity-Aware Distributed Inference

Focus:

```text
identity
lineage
evidence
reconciliation
state correctness
```

---

## Paper B — Program-Aware Inference Scheduling

Focus:

```text
critical paths
fork/join
fan-out
program completion time
```

---

## Paper C — Lifecycle-Aware Model State Management

Focus:

```text
tool gaps
typed retention
proactive replication
state survival
```

---

## Paper D — Continuity-Aware Elasticity

Focus:

```text
future demand
suspended workload
fan-out forecasting
autoscaling
```

---

## Paper E — Continuity Benchmark

A standardized benchmark for:

```text
stale attempts
wrong-state reuse
migration races
stateful failover
program DAGs
```

This could become an independent artifact contribution.

---

# 35. Decision Gates

The project should include explicit stop/go decisions.

## Gate G0 — Is the abstraction coherent?

After C0.

Proceed only if definitions and invariants are precise.

---

## Gate G1 — Does Continuity solve a real correctness problem?

After C4.

Require clear failure cases where simpler baselines silently misassociate execution/state and Continuity prevents it.

---

## Gate G2 — Does Continuity provide useful systems leverage?

After C7.

At least one of:

```text
lower recomputation
better useful-state residency
safer migration
lower program completion time
```

must emerge over meaningful parameter regions.

---

## Gate G3 — Is the system practical?

After C8.

Control-plane overhead must remain acceptably small relative to inference operations.

---

## Gate G4 — Is the abstraction portable?

After C10.

At least two substantially different integration environments must use the same semantic core.

---

## Gate G5 — Is the paper claim adequately supported?

Before submission.

Every major claim must have an appropriate evidence class.

---

# 36. Definition of Research Success

The project is ready for publication when we can demonstrate all of the following:

### Conceptual

A precise definition of Continuity exists.

### Formal

Core safety invariants are specified.

### Correctness

Adversarial distributed tests demonstrate that continuity prevents defined stale/wrong-state outcomes.

### Experimental

The system has been evaluated using public traces plus controlled synthetic continuity workloads.

### Performance

Trace-driven/calibrated simulation establishes the efficiency trade-space.

### Practicality

A real CPU-based distributed implementation measures runtime overhead.

### Portability

At least one standards-based integration plus one independent environment demonstrates architectural neutrality.

### Reproducibility

All major experiments can be reproduced from the repository.

### Scientific honesty

Simulated and measured claims are clearly distinguished.

---

# 37. The North Star Figure

The final project should ultimately be reducible to one diagram:

```text
                         GENERATIVE PROGRAM
                                │
                                ▼
                         Program / Session
                                │
                         Continuation DAG
                                │
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
            Execution         State          Objective
             Lineage          Lineage
                │               │
                └───────┬───────┘
                        ▼
                ┌─────────────────┐
                │ CONTINUITY PLANE│
                │                 │
                │ Identity        │
                │ Causality       │
                │ Evidence        │
                │ Lifecycle       │
                │ Reconciliation  │
                └────────┬────────┘
                         │
                  Scheduling Policy
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
       Compute         Memory         Storage
       Worker A        / Cache        / Replica
          │
          ▼
                    Execution
                         │
                         ▼
                     Evidence
                         │
                         └───────────────► Reconcile
```

The key relationship is:

```text
Logical computation
        ↕
Reusable inference state
        ↕
Physical execution
```

Continuity keeps those three aligned.

---

# 38. Final Governing Principle

The project should continually test itself against one statement:

> **An inference system should never infer semantic continuity solely from temporal proximity, physical locality, or apparent state similarity when causal identity can be represented explicitly.**

From that follows:

```text
latest result ≠ necessarily correct result

nearby state ≠ necessarily valid state

same session ≠ necessarily same continuation

same request ≠ necessarily same attempt

cached state ≠ necessarily authoritative state

observed state ≠ necessarily proven state
```

Continuity-Aware Distributed Inference exists to make those distinctions explicit, enforceable, observable, and useful to scheduling.

---

# 39. Immediate Next Milestone

The next project milestone is **C0 — Research Specification**.

No simulator, Gateway integration, cloud experiment, or GPU work should precede it.

C0 should produce five canonical documents:

```text
01-research-thesis.md
02-continuity-model.md
03-invariants.md
04-failure-model.md
05-experimental-plan.md
```

Once these documents are stable, implementation begins with **C1 — Deterministic Continuity Core**.

From this point onward, all design proposals should answer:

> Which research question does this address?

> Which invariant does it support?

> Which hypothesis will test it?

> What evidence will establish the claim?

If those questions cannot be answered, the proposal should not enter the critical path.
