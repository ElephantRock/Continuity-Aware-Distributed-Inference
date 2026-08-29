# 05 — Experimental Plan
## Evaluation, Evidence, and Decision Protocol for Continuity-Aware Distributed Inference

**Working paper title:**  
**Continuity-Aware Distributed Inference: Causal Execution and State Lineage for Stateful Generative Workloads**

**Document role:** Canonical experimental specification  
**Milestone:** C0 — Research Specification  
**Dependencies:** `01 — Research Thesis`, `02 — Continuity Model`, `03 — Invariants`, `04 — Failure Model`  
**Status:** C0.1 normalized experimental-plan candidate

---

# 1. Purpose

This document defines how the claims of **Continuity-Aware Distributed Inference** will be tested.

It maps:

```text
Research Question
        ↓
Hypothesis
        ↓
Mechanism / Invariant
        ↓
Workload
        ↓
Fault / Experimental Condition
        ↓
Baseline
        ↓
Metric
        ↓
Evidence Class
        ↓
Statistical Analysis
        ↓
Decision Criterion
```

The purpose is to prevent implementation decisions, benchmark choices, or favorable observations from redefining the research questions after experiments begin.

The experimental plan is therefore a **pre-implementation evaluation contract**.

---

# 2. Experimental Objective

The project must establish two distinct propositions.

## Correctness proposition

Explicit execution identity, state lineage, binding generations, evidence authority, and reconciliation prevent defined classes of silent semantic misassociation under asynchronous and failure-prone execution.

## Efficiency proposition

The same Continuity information can improve selected state-management and scheduling outcomes under workloads containing meaningful reusable state.

These propositions must be evaluated separately.

A correctness improvement does not automatically imply a performance improvement.

A performance improvement does not compensate for a correctness violation.

---

# 3. Experimental Philosophy

The evaluation follows six principles.

## X1 — Ground truth is independent of policy

The experiment harness maintains authoritative experimental ground truth separately from the scheduler or policy being tested.

## X2 — Safety and availability are separate

The experiment distinguishes:

```text
silent incorrect success
```

from:

```text
explicit failure
WAIT
RETRY
RECOMPUTE
REPAIR
AMBIGUOUS
```

## X3 — Realistic workload characteristics and controlled semantic structure are combined

Use:

```text
public workload distributions
+
synthetic continuity structure
+
controlled fault injection
```

rather than relying exclusively on either real traces or synthetic workloads.

## X4 — Synthetic assumptions are swept

Important synthetic variables are evaluated across meaningful ranges.

No central claim may depend on one favorable hand-selected configuration.

## X5 — Validation evidence class is attached to every result

Every reported result is explicitly labeled:

```text
DETERMINISTIC
MEASURED_CPU
TRACE_DERIVED
SIMULATED
SYNTHETIC
ANALYTICALLY_DERIVED
OPTIONAL_GPU_MEASURED
```

## X6 — Negative results remain valid results

If an efficiency hypothesis fails, the paper reports that result and narrows the claim rather than modifying workloads until the hypothesis appears true.

---

# 4. Validation Evidence Layers

The research uses five **validation evidence layers**, named `EV0`–`EV4` to avoid collision with runtime `Evidence` objects.

## EV0 — Deterministic semantic evaluation

Environment:

```text
single-process deterministic Continuity Core
```

Purpose:

- state-machine correctness;
- invariant enforcement;
- exact counterexample traces;
- property-based testing;
- sequence fuzzing.

Claims supported:

```text
logical safety semantics
```

No timing-performance claims are made.

---

## EV1 — Real CPU distributed evaluation

Environment:

```text
multiple real processes/services
real IPC/network communication
CPU execution
real concurrency
```

Purpose:

- asynchronous behavior;
- process failure;
- actual message delay;
- duplicate delivery;
- event reordering;
- control-plane overhead;
- reconciliation latency.

Claims supported:

```text
distributed correctness
+
real control-plane performance
```

---

## EV2 — Trace-driven workload evaluation

Environment:

```text
publicly available workload traces
+
Continuity augmentation
```

Purpose:

- realistic arrival patterns;
- realistic request/output size distributions;
- realistic load burstiness;
- prefix/state-reuse distributions where available.

Claims supported:

```text
workload realism
```

EV2 does not independently establish physical GPU performance.

---

## EV3 — Calibrated inference simulation

Environment:

```text
CPU-based inference performance simulator
or calibrated analytical cost model
```

Purpose:

- prefill/decode cost;
- state recomputation;
- transfer cost;
- memory pressure;
- TTFT;
- throughput;
- program-level timing.

Claims supported:

```text
modeled accelerator-performance consequences
```

These results must be described as simulated or modeled.

---

## EV4 — Optional accelerator validation

Environment:

```text
real GPU/accelerator
```

Purpose:

- calibration validation;
- selected cold/warm continuation timings;
- state recomputation measurements;
- transfer/recomputation crossover.

EV4 strengthens EV3.

It is not required for the core correctness thesis.

---

# 5. Experimental Systems

All experiments compare conceptual serving systems through a common interface.

## B0 — Request-Centric

Information available:

```text
request identity
worker availability
queue/load information
```

No Continuation lineage.

No causal State lineage.

No explicit attempt authority beyond ordinary request execution semantics required by the implementation.

---

## B1 — Cache-Aware

B0 plus:

```text
prefix/cache locality
estimated or exact cache presence
```

Selection may optimize reuse.

It does not possess explicit Continuation-level causal lineage.

---

## B2 — Session-Affinity

B1 or load-aware scheduling plus:

```text
SessionID
preferred previous location
```

The system may attempt to keep a Session on one worker.

Sibling branches remain part of the same Session.

---

## B3 — State-Aware

Receives:

```text
logical/physical State identifiers
precise State locations
```

but does not receive the complete Continuity causal model:

```text
Continuation ancestry
Attempt authority
evidence authority
binding generations
reconciliation semantics
```

B3 is important because it distinguishes:

> knowing where State is

from:

> knowing whether State causally belongs to current work.

---

## B4 — Continuity-Aware

Receives and enforces:

```text
Program identity
Session identity
Continuation identity
LogicalRequest identity
Attempt identity
State lineage
Binding epoch
Evidence authority/status/freshness
Reconciliation
```

Implements Paper 1 policies:

```text
attempt fencing
compatible-state routing
lifecycle-aware retention
safe migration
```

---

# 6. Baseline Fairness Rule

Every baseline must be implemented competently according to its abstraction.

The experiment must not manufacture a Continuity advantage by deliberately removing normal correctness mechanisms from competing systems.

Examples:

B0 may use ordinary unique request IDs.

B1 may verify that a cache entry physically exists if that is part of its design.

B2 may detect worker failure and re-route.

B3 may maintain exact State identifiers and locations.

The research question is whether **causal Continuity adds information or safety beyond those abstractions**, not whether broken baselines can be defeated.

## 6.1 Baseline Information Contracts

Before C3, each baseline must have a machine-readable information contract declaring whether it receives:

```text
LogicalRequest identity
Attempt identity / authority
Session identity
Continuation identity / ancestry
State candidate key
exact StateID
State location
State provenance
producer Attempt
BindingID / epoch
Evidence authority / status / freshness
resource/load observations
```

The contract is part of the experiment manifest.

For B3 specifically, the State-candidate interface must be fixed and grounded against the real abstraction being modeled. B3 must not be made artificially weak by withholding an exact State selector that its design would ordinarily possess. Conversely, precise physical State location does not automatically grant Continuation ancestry or producer-Attempt authority.

This allows the paper to distinguish:

```text
algorithmic advantage
from
information advantage
```

while treating richer causal information as part of the Continuity contribution.

---

# 7. Independent Ground Truth

The harness maintains an oracle containing at minimum:

```text
true Program
true Session
true Continuation graph
true CurrentAttempt and CommittedAttempt
true State origin
true State compatibility including producer-Attempt authority and StateValidity
true physical replicas
true CurrentBinding and Binding epoch
true injected fault
true semantic outcome
```

The policy under test does not automatically receive this information.

For every policy decision, the harness records:

```text
ground_truth
observed_evidence
policy_decision
semantic_result
```

This permits unambiguous classification of:

```text
correct decision
safe conservative decision
performance miss
silent semantic error
```

---

# 8. Workload Families

The benchmark contains ten primary workload families.

---

## W1 — Independent Requests

Structure:

```text
R1
R2
R3
...
```

No meaningful cross-request continuity.

Purpose:

- control workload;
- measure Continuity overhead;
- determine whether Continuity harms stateless serving.

Primary metrics:

```text
decision latency
metadata overhead
throughput
CPU overhead
```

Expected result:

B4 should provide little efficiency benefit.

This workload is important because the thesis does not predict universal superiority.

---

## W2 — Deep Stateful Sessions

Structure:

```text
C0 → C1 → C2 → C3 → ... → Cn
```

High reusable-state ancestry.

Parameters:

```text
session depth
state size
prefix-reuse fraction
request size
output size
```

Purpose:

test H4 and H7.

Primary metrics:

```text
Recomputation Ratio
State Reuse Ratio
TTFT
Program Completion Time
```

---

## W3 — Tool-Gap Sessions

Structure:

```text
C0
 ↓ inference
C1 ACTIVE
 ↓ external tool invoked
C1 WAITING
 ↓ tool result
C2 ACTIVE child
C1 TERMINAL
```

Parameters:

```text
tool-gap duration
state size
memory pressure
return probability
```

Purpose:

test lifecycle-aware retention.

Baselines:

```text
LRU
fixed TTL
session pinning
Continuity lifecycle retention
```

Primary metrics:

```text
Useful State Residency
Wasted State Residency
Tool-Return TTFT
Recomputation Ratio
```

---

## W4 — Retry Races

Structure:

```text
LogicalRequest R
├── A1 — may remain physically RUNNING/SUCCEEDED after authority becomes SUPERSEDED
└── A2 — CURRENT, then COMMITTED if it finalizes
```

with overlapping execution.

Fault conditions:

```text
timeout
late completion
duplicate completion
event reordering
```

Purpose:

test H1 and execution invariants.

Primary metric:

```text
Stale Attempt Acceptance Rate
```

Expected B4 target:

\[
0
\]

under modeled assumptions.

---

## W5 — Stateful Failover

Structure:

```text
C
 ↓
State X on W1
 ↓
W1 failure
 ↓
recovery / retry / transfer / recompute
```

Purpose:

test H6.

Primary metrics:

```text
Silent Binding Divergence Rate
Recovery Time
State Transfer Volume
Recomputation Ratio
```

---

## W6 — Branching Programs

Structure:

```text
        C0
      / | \
    C1 C2 C3
```

State may exist independently on multiple branches.

Purpose:

test H2.

Key experiments:

1. make incompatible sibling State physically attractive;
2. make State from a superseded producer Attempt physically attractive on an otherwise valid ancestor lineage.

Primary metrics:

```text
Wrong-Branch Reuse Rate
Wrong-State Consumption Rate
Recomputation Ratio
```

Expected B4 correctness target:

\[
0
\]

for lineage-detectable incompatibility.

---

## W7 — Fan-Out / Fan-In

Structure:

```text
          C0
      / / | \ \
    C1 C2 C3 C4 C5
          ...
           ↓
          join
```

Purpose:

evaluate shared-ancestor reuse and program-level impact.

Parameters:

```text
fan-out width
shared-prefix size
branch duration
cache capacity
```

Primary metrics:

```text
State Reuse Ratio
Fan-Out Completion Time
Branch Join Latency
State Transfer Volume
```

---

## W8 — Cache Pressure

Competing State classes:

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

under limited capacity.

Purpose:

test H5.

Compare:

```text
LRU
fixed TTL
session pinning
lifecycle-aware retention
```

Primary metrics:

```text
Useful State Residency
Wasted State Residency
Recomputation Ratio
Cold Continuation Rate
```

---

## W9 — Stale Evidence

Scheduling/ownership observations have controlled age.

Example sweep:

```text
0
10 ms
50 ms
100 ms
500 ms
1 s
5 s
```

or normalized relative to execution duration where absolute values are inappropriate.

Purpose:

test H3.

Primary metrics:

```text
Silent Semantic Error Rate
Ambiguous Commit Rate
Explicit Non-Success Rate
Recomputation Rate
Program Completion Time
```

---

## W10 — State Migration

State may:

```text
remain local
transfer
recompute
restore
```

under changing placement.

Purpose:

test H6 and efficiency consequences of safe migration.

Parameters:

```text
state size
transfer bandwidth
transfer latency
recompute cost
queue delay
source failure probability
destination failure probability
```

Primary outputs:

```text
safe migration rate
migration failure behavior
recompute-vs-transfer crossover
Recovery Time
Program Completion Time
```

---

# 9. Fault Families

Experiments draw from the Failure Model.

Core fault classes:

```text
F1 delayed event
F2 duplicate event
F3 reordered event
F4 omitted observation
F5 communication partition
F6 worker crash
F7 worker restart
F8 retry timeout
F9 late successful attempt
F10 state eviction
F11 total State loss
F12 stale State-location observation
F13 false-negative State observation
F14 incompatible local State
F15 partial State materialization
F16 migration source failure
F17 migration destination failure
F18 migration acknowledgment delay
F19 stale Binding event
F20 concurrent migration
F21 contradictory evidence
F22 stale evidence
F23 scope-mismatched evidence
F24 cache/resource saturation
F25 retry storm
F26 migration storm
```

Not every fault participates in every experiment.

---

# 10. Outcome Classes

Every faulted operation is classified as:

## O1 — Correct transparent recovery

No externally visible degradation.

## O2 — Correct degraded recovery

Recovery requires:

```text
retry
recompute
repair
migration
```

## O3 — Explicit non-success

Result:

```text
WAIT
REJECT
FAIL
AMBIGUOUS
```

## O4 — Silent semantic violation

System commits semantically incorrect success.

For safety claims, O4 is the critical failure.

---

# 11. Correctness Metrics

Define:

## Stale Attempt Acceptance Rate

\[
SAAR=
\frac{
\text{stale Attempt results accepted authoritatively}
}{
\text{stale Attempt results presented}
}
\]

---

## Wrong-State Consumption Rate

\[
WSCR=
\frac{
\text{incompatible State consumptions}
}{
\text{State consumptions}
}
\]

---

## Wrong-Branch Reuse Rate

\[
WBRR=
\frac{
\text{sibling/unrelated branch State reused}
}{
\text{opportunities for incompatible branch reuse}
}
\]

---

## Silent Binding Divergence Rate

\[
SBDR=
\frac{
\text{operations committed under stale/conflicting Binding authority}
}{
\text{Binding-sensitive operations}
}
\]

---

## Ambiguous Commit Rate

\[
ACR=
\frac{
\text{correctness commits made while evidence is ambiguous}
}{
\text{ambiguous correctness-sensitive decisions}
}
\]

---

## Duplicate Finalization Rate

\[
DFR=
\frac{
\text{LogicalRequests finalized more than once semantically}
}{
\text{completed LogicalRequests}
}
\]

---

## Silent Semantic Error Rate

\[
SSER=
\frac{O4}{O1+O2+O3+O4}
\]

This is the broadest correctness metric.

---

# 12. Availability / Safety-Cost Metrics

Because fail-closed behavior may trade availability for safety, measure:

```text
Explicit Non-Success Rate
WAIT Rate
RETRY Rate
RECOMPUTE Rate
REPAIR Rate
AMBIGUOUS Outcome Rate
```

These metrics must never be merged with silent correctness errors.

---

# 13. Efficiency Metrics

## Recomputation Ratio

Possible token-weighted definition:

\[
RR=
\frac{
\text{tokens unnecessarily recomputed}
}{
\text{total prefill-equivalent tokens processed}
}
\]

The implementation must specify the exact denominator.

---

## State Reuse Ratio

\[
SRR=
\frac{
\text{reusable State actually consumed}
}{
\text{eligible State reuse opportunities}
}
\]

---

## State Transfer Volume

Measured in:

```text
bytes
or normalized State units
```

---

## Useful State Residency

State-time that is later used before eviction.

Conceptually:

\[
USR=
\frac{
\text{resident State-time that leads to reuse}
}{
\text{total resident State-time}
}
\]

A second token- or byte-weighted version may also be reported.

---

## Wasted State Residency

State-time retained without later reuse.

---

## Cold Continuation Rate

\[
CCR=
\frac{
\text{continuations requiring full reconstruction}
}{
\text{continuation executions}
}
\]

---

# 14. Program-Level Metrics

## Program Completion Time

\[
PCT =
t_{program\ terminal}
-
t_{program\ start}
\]

`program terminal` is defined by the workload/application's declared `ObjectiveSatisfied(program)` predicate and the Program lifecycle in the Continuity Model. The experiment manifest must state the objective condition.

---

## Critical Path Delay

Delay attributable to serving/state decisions along the actual logical critical path.

Used cautiously because Paper 1 does not implement full critical-path scheduling.

---

## Tool-Return TTFT

Time from tool-return/resume eligibility to first generated token or simulated equivalent.

---

## Fan-Out Completion Time

Time from fan-out initiation to all required branches becoming complete.

---

## Branch Join Latency

Time between final required branch completion and join readiness/completion.

---

## Recovery Time

Time between injected failure and restoration of useful logical progress.

---

# 15. Runtime Overhead Metrics

Measured on real CPU implementation where possible:

```text
median decision latency
p95 decision latency
p99 decision latency

median reconciliation latency
p95 reconciliation latency
p99 reconciliation latency

events processed / second

CPU utilization

resident memory

execution-graph memory

State-directory memory

Evidence-store memory

metadata bytes / LogicalRequest

metadata bytes / event

control-plane network volume
```

Scaling variables:

```text
number of active Sessions
number of Continuations
number of State objects
event rate
worker count
```

---

# 16. Traditional Serving Metrics

Where supported by the inference cost model:

```text
TTFT
TPOT
ITL
throughput
queue delay
resource utilization
```

These metrics remain secondary to:

```text
semantic correctness
state efficiency
program completion
```

---

# 17. RQ1 — Execution Correctness

## Question

Can explicit LogicalRequest and Attempt identities prevent stale, duplicated, superseded, or delayed executions from incorrectly completing current requests?

## Hypothesis

H1.

## Mechanisms

```text
AttemptID
generation
ExecutionStatus
AttemptAuthority
CurrentAttempt
CommittedAttempt
supersession
semantic commit guard
idempotence
```

## Invariants

Primarily:

```text
B1–B7
F1–F3
F7
```

## Workloads

```text
W4 Retry Races
W1 Independent Requests as control
```

## Faults

```text
timeout
late completion
duplicate result
event delay
event reordering
retry storm
```

## Baselines

```text
B0
B1
B2
B3
B4
```

## Evidence

```text
EV0 deterministic
EV1 distributed CPU
```

## Primary metrics

```text
SAAR
DFR
SSER
```

## Secondary metrics

```text
retry overhead
explicit failure rate
recovery latency
control-plane decision latency
```

## Decision criterion

H1 is supported if:

1. B4 produces **zero** stale-attempt authoritative acceptance across all mandatory deterministic traces;
2. B4 produces **zero** stale-attempt acceptance across the covered adversarial sequence-fuzzing corpus;
3. EV1 real distributed retry races produce no B4 semantic violations;
4. at least one weaker abstraction demonstrates a materially different failure or requires an equivalent mechanism that effectively reproduces Continuity attempt authority.

If all competent baselines already provide equivalent attempt fencing independently of Continuity, H1 may remain an implementation property but loses strength as an independent novelty claim.

---

# 18. RQ2 — State Correctness

## Question

Can causal State lineage prevent reuse of locally attractive but logically incompatible State?

## Hypothesis

H2.

## Mechanisms

```text
ExecutionContext
Continuation DAG
State origin
producer Attempt
AttemptAuthority
StateValidity
Ancestor relation
SemanticValidity
compatibility filter
```

## Invariants

```text
A4
C1–C7
```

## Workloads

```text
W6 Branching
W7 Fan-Out
```

## Faults

```text
incompatible local State
superseded-producer State
similar-but-different State
residual abandoned-branch State
stale cache information
```

## Baselines

Most important comparison:

```text
B1 Cache-Aware
B2 Session-Affinity
B3 State-Aware
B4 Continuity-Aware
```

## Primary metrics

```text
WBRR
WSCR
SSER
```

## Efficiency cost

```text
Recomputation Ratio
State Transfer Volume
PCT
```

## Decision criterion

H2 is supported if:

1. B4 rejects every lineage-detectable sibling/unrelated State in deterministic tests;
2. B4 rejects State from a superseded producer Attempt by default even when Continuation ancestry matches;
3. B4 has zero wrong-branch or superseded-producer reuse under covered adversarial simulations;
4. at least one non-lineage baseline admits incompatible reuse or must conservatively discard reusable State in cases B4 can distinguish safely;
5. safety is not obtained merely by disabling all State reuse.

The fourth condition is important.

A system that never reuses State trivially avoids wrong-State reuse but does not solve the stateful serving problem efficiently.

---

# 19. RQ3 — Evidence and Reconciliation

## Question

Can explicit evidence authority reduce silent incorrect decisions under stale, incomplete, delayed, or contradictory observations?

## Hypothesis

H3.

## Mechanisms

```text
Evidence authority
status
freshness
scope
Sufficient(...)
AMBIGUOUS
fail-closed reconciliation
```

## Invariants

```text
E1–E7
Composite Fail-Closed Reconciliation
```

## Workloads

```text
W9 Stale Evidence
W5 Stateful Failover
W10 Migration
```

## Faults

```text
observation delay
contradictory evidence
stale authoritative evidence
approximate evidence error
scope mismatch
omitted observation
```

## Evidence sweep

Vary evidence age and authority independently.

Example matrix:

```text
Authority:
ESTIMATED
DERIVED
EXACT_OBSERVATION
AUTHORITATIVE

Status:
VALID
STALE
AMBIGUOUS
UNKNOWN
FAILED
```

## Primary metrics

```text
SSER
ACR
Explicit Non-Success Rate
RECOMPUTE Rate
WAIT Rate
```

## Decision criterion

H3 is supported if:

1. B4 produces zero ambiguous correctness commits under covered cases;
2. weaker evidence-handling policies show a measurable semantic-error region as evidence becomes stale/contradictory, or require equally conservative behavior;
3. B4 exposes an observable safety/availability frontier rather than silently guessing;
4. the cost of conservatism is quantified.

---

# 20. RQ4 — State Reuse

## Question

Does Continuation-aware State management reduce unnecessary recomputation?

## Hypothesis

H4.

## Workloads

```text
W2 Deep Sessions
W3 Tool Gaps
W7 Fan-Out
W8 Cache Pressure
```

## Baselines

```text
B0
B1
B2
B3
B4
```

## Evidence

```text
EV2 trace-driven
EV3 calibrated simulation
```

## Primary metrics

```text
Recomputation Ratio
State Reuse Ratio
Cold Continuation Rate
PCT
TTFT
```

## Independent variables

```text
session depth
state-reuse fraction
State size
cache capacity
queue load
worker count
```

## Decision criterion

H4 is supported only over identified workload regions.

It is not required that B4 dominate every configuration.

Support requires:

1. a positive reduction in Recomputation Ratio versus relevant baselines;
2. uncertainty analysis showing the observed improvement is not explained by run-to-run variation;
3. the benefit persists over more than one isolated parameter point;
4. no accompanying semantic correctness regression.

The paper must explicitly report the region where B4 provides no benefit.

---

# 21. RQ5 — Lifecycle-Aware Retention

## Question

Can Continuation lifecycle improve useful-State retention under tool gaps, branches, and cache pressure?

## Hypothesis

H5.

## Policies compared

```text
LRU
fixed TTL
session pinning
Continuity lifecycle-aware retention
```

## Workloads

```text
W3 Tool Gaps
W8 Cache Pressure
W6/W7 Branching and Fan-Out
```

## Lifecycle classes

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

## Independent variables

```text
tool-gap duration
return probability
cache capacity
memory pressure
branch width
State size
ratio of waiting to active State
```

## Primary metrics

```text
Useful State Residency
Wasted State Residency
Recomputation Ratio
Tool-Return TTFT
Cold Continuation Rate
```

## Decision criterion

H5 is supported if lifecycle information produces a statistically supported improvement in useful residency or recomputation/return latency over at least one meaningful range of cache-pressure/tool-gap conditions, while avoiding pathological retention overhead.

If ordinary LRU/TTL performs equivalently across plausible parameter ranges, H5 is not supported.

---

# 22. RQ6 — Failure Recovery and Migration

## Question

Do Binding epochs and State provenance improve safety and efficiency during failover and migration?

## Hypothesis

H6.

## Mechanisms

```text
BindingID
base_epoch
CurrentBinding
CurrentEpoch
unique candidate generation
migration commit
State provenance
Evidence sufficiency
stale-event fencing
```

## Invariants

```text
D1–D5
F3
Composite Safe Migration
```

## Workloads

```text
W5 Stateful Failover
W10 Migration
```

## Faults

```text
source failure
destination failure
partial materialization
late migration acknowledgment
stale old-Binding event
concurrent migration
worker restart with residual State
```

## Primary metrics

```text
SBDR
SSER
Recovery Time
State Transfer Volume
Recomputation Ratio
```

## Decision criterion

Correctness component:

```text
B4 SBDR = 0
```

for covered stale-epoch and migration-race scenarios.

Efficiency component:

H6 is strengthened if B4 also reduces unnecessary migration or recomputation relative to conservative alternatives.

Safe migration alone is sufficient for the correctness claim.

Efficiency is separately reported.

---

# 23. RQ7 — Program-Level Performance

## Question

Under which workload conditions does Continuity improve total Program completion?

## Hypothesis

H7.

## Workloads

```text
W2 Deep Sessions
W3 Tool Gaps
W5 Failover
W6 Branching
W7 Fan-Out
W10 Migration
```

## Independent variables

Primary:

```text
session depth
branch factor
tool-gap duration
retry frequency
migration frequency
State size
reusable-prefix fraction
```

## Evidence

Primarily:

```text
EV2
EV3
```

with EV1 for control-plane contributions.

## Metrics

```text
PCT
Tool-Return TTFT
Fan-Out Completion Time
Recovery Time
Critical Path Delay
```

## Analysis objective

Do not merely ask:

```text
Does B4 win?
```

Instead estimate:

\[
Benefit =
f(
sessionDepth,
branching,
toolGap,
retryRate,
stateSize,
cachePressure
)
\]

The desired result is a **phase diagram** or response surface identifying when Continuity matters.

## Decision criterion

H7 is supported if increasing one or more statefulness dimensions produces a systematic increase in B4's relative benefit over simpler abstractions across a defensible parameter region.

A monotonic increase is not required for every variable because interactions may be nonlinear.

The analysis must report those interactions.

---

# 24. RQ8 — Portability

## Question

Can the semantic model operate independently of engine, gateway, resource orchestrator, or provider?

## Hypothesis

No separate numerical H8 is currently defined.

RQ8 tests a design property.

## Environments

At minimum:

### Environment A

Minimal provider-neutral scheduler/runtime.

### Environment B

Standards-based Kubernetes Gateway API Inference Extension adapter.

Preferably:

### Environment C

A second materially different integration category.

Example:

```text
non-Kubernetes custom runtime
or
alternative scheduler
```

## Portability requirement

The semantic core must remain unchanged.

Adapters may translate:

```text
execution
state
observation
scheduling
```

operations.

They must not introduce new Continuity semantics required for correctness.

## Primary metrics

```text
semantic test equivalence
adapter-specific code size
adapter translation overhead
invariant pass rate
```

## Decision criterion

RQ8 is supported if equivalent Continuity traces produce the same authoritative semantic outcome across at least two materially different integration environments without modifying the core semantic rules.

---

# 25. Master Traceability Matrix

| RQ | Hypothesis | Main mechanism | Workload | Main failure/condition | Primary metrics | Validation evidence |
|---|---|---|---|---|---|---|
| RQ1 | H1 | Attempt fencing | W4 | retry/late result | SAAR, DFR | EV0, EV1 |
| RQ2 | H2 | State lineage | W6 | sibling State | WBRR, WSCR | EV0, EV1, EV3 |
| RQ3 | H3 | Evidence authority | W9 | stale/ambiguous evidence | SSER, ACR | EV0, EV1, EV3 |
| RQ4 | H4 | Compatible routing | W2/W3/W7 | reuse opportunity | RR, SRR, PCT | EV2, EV3 |
| RQ5 | H5 | Lifecycle retention | W3/W8 | cache pressure/tool gap | USR, WSR, RR | EV2, EV3 |
| RQ6 | H6 | Binding epoch | W5/W10 | migration/failover | SBDR, recovery | EV0, EV1, EV3 |
| RQ7 | H7 | Full Continuity context | multiple | increasing statefulness | PCT | EV2, EV3 |
| RQ8 | — | Adapter contract | portability traces | environment change | semantic equivalence | EV1/integration |

---

# 26. Safety Experiment Series S

Correctness experiments are grouped separately from efficiency experiments.

## S1 — Attempt Fencing

Faults:

```text
late completion
duplicate result
retry race
event reorder
```

Metrics:

```text
SAAR
DFR
```

---

## S2 — State-Lineage Safety

Faults:

```text
sibling State
abandoned branch State
similar-but-different State
```

Metrics:

```text
WBRR
WSCR
```

---

## S3 — Binding Safety

Faults:

```text
stale epoch
partial migration
late old-owner message
concurrent migration
```

Metric:

```text
SBDR
```

---

## S4 — Evidence Safety

Faults:

```text
stale
contradictory
ambiguous
scope mismatch
```

Metrics:

```text
ACR
SSER
Explicit Non-Success Rate
```

---

## S5 — Idempotence and Ordering

Faults:

```text
duplicate observations
arbitrary permitted event permutations
```

Metrics:

```text
DFR
invariant violations
semantic-state equivalence
```

---

# 27. Efficiency Experiment Series P

## P1 — Deep Continuation Reuse

Compare routing policies under increasing session depth.

## P2 — Tool-Gap Retention

Compare lifecycle policy with LRU/TTL/session pinning.

## P3 — Branching State Pressure

Measure cost of keeping only causally useful branch State.

## P4 — Fan-Out Shared Prefix

Measure common ancestor State reuse.

## P5 — Migration Versus Recompute

Map crossover boundaries.

## P6 — Evidence Conservatism

Measure performance cost of stronger authority requirements.

## P7 — Stateless Overhead

Measure B4 overhead where Continuity provides no expected benefit.

---

# 28. Required Ablations

Evaluate full B4 against:

```text
B4 − AttemptID/Attempt authority

B4 − State lineage

B4 − BindingEpoch

B4 − Evidence authority

B4 − Reconciliation

B4 − Lifecycle information
```

Ablations must not be used where removing a mechanism makes execution structurally impossible.

Instead, replace it with the nearest weaker behavior.

Examples:

### − Attempt authority

Use most-recently-observed completion or equivalent request-level behavior.

### − State lineage

Use State locality/session relation without Continuation ancestry.

### − Binding epoch

Use current reported owner without generation fencing.

### − Evidence authority

Use freshness/confidence only.

### − Reconciliation

Apply observations directly according to policy.

### − Lifecycle

Use ordinary retention policy without Continuation lifecycle.

---

# 29. Ablation Questions

For every removed component ask:

```text
Which correctness failure reappears?

Which efficiency benefit disappears?

Which runtime overhead disappears?

Does another component compensate?
```

The desired ablation result is causal attribution of mechanisms to observed outcomes.

---

# 30. Sensitivity Variables

At minimum sweep:

```text
session depth
branch factor
fan-out width
tool-gap duration
tool-return probability
retry timeout
retry frequency
worker failure rate
migration frequency
State size
prefix-reuse fraction
cache capacity
cache pressure
worker count
arrival intensity
observation lag
event reordering window
duplication probability
transfer bandwidth
transfer latency
recompute cost
```

---

# 31. Parameter Selection Rule

Parameters are selected from one of four sources.

## P-SRC1 — Public traces

Preferred where available.

## P-SRC2 — Published or validated simulator profiles

Used for infrastructure/performance characteristics.

## P-SRC3 — Direct CPU measurements

Used for Continuity control-plane overhead.

## P-SRC4 — Explicit synthetic ranges

Used when no empirical source exposes the required semantic variable.

Each experiment manifest records the source class of every parameter.

---

# 32. Synthetic Parameter Policy

A synthetic parameter must never appear as an unexplained magic constant.

Example:

```text
tool_gap = 30 seconds
```

is insufficient by itself.

Instead:

```text
tool_gap distribution:
1 s
5 s
10 s
30 s
60 s
300 s
```

or a distribution derived from defensible evidence.

The experiment report must indicate which values are synthetic.

---

# 33. Trace Augmentation

Public traces may contain:

```text
arrival timestamp
input length
output length
prefix information
```

while lacking:

```text
Session
Continuation
tool wait
branch
retry
failure
migration
```

Trace augmentation adds those semantic structures without modifying the original trace characteristics unnecessarily.

Conceptually:

```text
Original request trace
        │
        ├── preserve arrivals
        ├── preserve sizes
        └── preserve prefix reuse where available
                │
                ▼
        Continuity augmentation
        ├── Session assignment
        ├── Continuation DAG
        ├── tool gaps
        ├── retries
        └── failures
```

Original and augmented fields must remain separately identifiable.

---

# 34. Workload Classes by Realism

Every benchmark run is labeled:

## REAL-TRACE

No synthetic Continuity semantics beyond what source data genuinely provides.

## TRACE-AUGMENTED

Real trace characteristics plus synthetic Continuity structure.

## SYNTHETIC-STRESS

Fully controlled workload for corner/adversarial behavior.

These classes must not be conflated.

---

# 35. Calibration Strategy

EV3 performance modeling must expose all calibration assumptions.

For every modeled hardware profile record:

```text
model
hardware profile
prefill cost function
decode cost function
memory capacity
State size model
transfer bandwidth
transfer latency
source of calibration
validation error if known
```

If a parameter is not empirically calibrated:

```text
SYNTHETIC
```

must be attached to it.

---

# 36. Simulation Validation

Before using EV3 for headline efficiency results:

1. reproduce a set of known simulator/reference configurations;
2. verify internal consistency;
3. compare modeled quantities with any available published or locally measurable references;
4. document known error bounds or limitations;
5. perform sensitivity analysis around uncertain calibration values.

If simulation error is large enough to reverse policy ranking, the paper must report the result as inconclusive.

---

# 37. Statistical Unit

The statistical unit depends on the experiment.

Examples:

```text
LogicalRequest
Session
Program
faulted operation
simulation run
trace segment
```

The analysis must not incorrectly treat highly correlated requests from the same Program as fully independent samples.

Where appropriate, bootstrap or aggregate at:

```text
Session
or
Program
```

level.

---

# 38. Repetition

Deterministic experiments require no stochastic replication once the exact trace is fixed, but must cover all defined counterexample/interleaving classes.

Stochastic experiments require:

- multiple fixed random seeds;
- repeated runs;
- reported run count;
- stable confidence intervals.

The run count should be determined by convergence of the target metric rather than a decorative fixed number.

---

# 39. Statistical Reporting

For continuous metrics, normally report:

```text
median
mean where meaningful
p95
p99 where meaningful
95% confidence interval
```

For proportions:

```text
point estimate
95% interval
numerator
denominator
```

For policy differences report:

```text
absolute difference
relative difference
confidence interval
```

Do not report only percentage improvement.

---

# 40. Bootstrap Policy

For non-normal latency/program-completion distributions, prefer nonparametric bootstrap intervals.

Where observations are nested:

```text
requests inside Programs
```

bootstrap at the appropriate higher-level independent unit.

The exact method and bootstrap count will be fixed in analysis code before headline results are produced.

---

# 41. Significance Versus Practical Importance

A statistically distinguishable result is not automatically important.

Every performance result should report:

```text
effect size
uncertainty
absolute cost/benefit
operating region
```

The paper's goal is not to accumulate statistically significant micro-differences.

It is to identify systems-relevant tradeoffs.

---

# 42. Correctness Statistics

Safety results are treated differently from ordinary performance metrics.

For deterministic covered traces, the requirement is exact:

```text
violations = 0
```

For stochastic fault campaigns:

```text
observed violations
/
opportunities
```

must be reported explicitly.

If zero violations are observed, report the number of trials rather than claiming mathematical impossibility from finite testing.

Formal invariants support the stronger semantic argument.

Experiments validate implementation conformance.

---

# 43. No “Zero Means Proven” Error

The paper must distinguish:

```text
Invariant states:
operation is prohibited by the model
```

from:

```text
Experiment observes:
0 violations in N trials
```

Testing alone does not prove absence under all possible executions.

The correctness argument combines:

```text
formal invariant
+
implementation enforcement
+
property testing
+
adversarial testing
+
distributed experiments
```

---

# 44. Experiment Manifest

Every run must have a machine-readable manifest.

Conceptually:

```text
experiment_id
git_commit
policy
workload
trace_source
random_seed
worker_count
fault_configuration
State_configuration
evidence_configuration
cost_model
parameter_sources
baseline_information_contract
program_objective_definition
start_time
software_versions
```

All generated result files reference this manifest.

---

# 45. Result Provenance

Every reported figure/table cell must be traceable to:

```text
paper result
    ↓
analysis output
    ↓
raw result file
    ↓
experiment manifest
    ↓
git commit
```

This is mandatory for artifact reproducibility.

---

# 46. Determinism

Where deterministic behavior is intended:

```text
same manifest
+
same seed
+
same software revision
```

should reproduce the same semantic event trace.

Performance measurements from real CPU environments may naturally vary.

Semantic outcome should remain invariant.

---

# 47. Cross-Layer Replay

Important fault scenarios must be represented at multiple layers.

Example:

## Retry race

### EV0

Logical event sequence.

### EV1

Real worker process deliberately delayed.

### EV3

Simulated long-tail inference completion.

Expected semantic outcome:

```text
same
```

Expected timing:

```text
different
```

This tests whether the model remains stable across implementation environments.

---

# 48. Required Cross-Layer Traces

At minimum replay:

```text
late superseded Attempt

duplicate completion

wrong sibling State

superseded-producer State

stale Binding event

ambiguous ownership

State eviction during tool wait

partial migration
```

across all applicable layers.

---

# 49. Correctness Gate G1

After C4, proceed only if:

1. all mandatory deterministic invariant traces pass;
2. property/sequence testing reveals no unresolved kernel violation;
3. B4 has zero observed violations of covered safety metrics in adversarial evaluation;
4. EV1 distributed tests reproduce the same safety outcomes;
5. at least one baseline comparison establishes that Continuity provides a meaningful semantic distinction rather than merely reimplementing universal existing behavior.

If criterion 5 fails, novelty must be reconsidered even if the implementation itself is correct.

---

# 50. Efficiency Gate G2

After C7, classify the result into one of three outcomes.

## G2-A — Strong support

Continuity demonstrates meaningful efficiency improvement across defensible stateful workload regions while preserving safety.

## G2-B — Correctness/efficiency frontier

Continuity does not universally improve performance but establishes a clear tradeoff:

```text
lower semantic risk
for
bounded additional cost
```

and/or provides efficiency benefit only above identifiable statefulness thresholds.

This remains publishable if the frontier is scientifically meaningful.

## G2-C — No useful leverage

Continuity provides no meaningful efficiency benefit and its correctness benefit is not sufficient to justify overhead.

The efficiency thesis must be rejected or substantially narrowed.

---

# 51. Practicality Gate G3

After C8 measure real control-plane overhead.

Gate G3 asks:

> Does Continuity overhead remain small relative to the inference operations it governs over the intended workload scale?

Do not preselect an arbitrary universal percentage threshold before implementation.

Instead report:

```text
absolute decision latency
relative latency contribution
CPU cost
memory cost
event throughput
scaling behavior
```

and compare these with modeled/observed inference times.

G3 fails if Continuity becomes the dominant serving bottleneck over workloads where it is intended to provide value.

---

# 52. Portability Gate G4

After C10 require:

1. same semantic entities;
2. same core invariant logic;
3. same reconciliation semantics;
4. adapter-specific translation only;
5. equivalent semantic outcomes under reference traces.

If a new integration requires changing the definition of:

```text
Attempt
Continuation
State compatibility
Binding epoch
Evidence authority
```

the portability claim requires review.

---

# 53. Publication Gate G5

Before submission every major claim must have:

```text
ClaimID
research question
evidence class
experiment IDs
analysis artifact
limitations
```

No claim enters the manuscript without traceable evidence.

---

# 54. Experiment-to-Claim Registry

Suggested form:

```text
Claim:
C-RQ1-01

Statement:
Continuity prevents stale superseded Attempts from finalizing a LogicalRequest under modeled asynchronous retry races.

Validation evidence:
EV0 + EV1

Experiments:
S1-A
S1-B
S1-C

Metrics:
SAAR
DFR

Limitations:
non-Byzantine
single semantic authority
```

This registry should ultimately be version controlled.

---

# 55. Planned Headline Figures

The final paper should target a small number of high-information figures.

## Figure 1 — Failure semantics

Illustrate retry/state/migration semantic hazards.

## Figure 2 — Correctness comparison

Example:

```text
Silent Semantic Error Rate
or individual error metrics
across B0–B4
```

under adversarial conditions.

## Figure 3 — Evidence staleness frontier

Axes:

```text
evidence age
vs
semantic error / explicit deferral
```

## Figure 4 — State-reuse phase diagram

Axes may include:

```text
state reuse
cache pressure
tool-gap duration
```

with Continuity benefit.

## Figure 5 — Migration/recompute phase diagram

Show where:

```text
reuse local
transfer
recompute
```

is preferable.

## Figure 6 — Control-plane overhead/scaling

Real CPU measurements.

## Figure 7 — Ablation

Show which mechanism eliminates which failure/benefit.

The exact figure count may change.

---

# 56. Planned Headline Tables

## Table A — Semantic capabilities

Compare B0–B4:

```text
request identity
cache locality
session affinity
State location
Continuation lineage
Attempt authority
Binding generation
Evidence authority
reconciliation
```

## Table B — Correctness metrics

Across core adversarial scenarios.

## Table C — Evidence provenance

For every experiment family:

```text
real
trace-derived
synthetic
simulated
measured
```

## Table D — Portability mapping

Show how generic interfaces map onto each reference environment.

---

# 57. Ablation Figure Requirement

A particularly important figure should map mechanisms to failures:

```text
Full Continuity          safe
− Attempt authority      stale result returns
− State lineage          wrong branch returns
− Binding epoch          stale ownership returns
− Evidence authority     ambiguous commit returns
− Reconciliation         observation/authority conflation returns
− Lifecycle              retention benefit disappears
```

This is central to demonstrating that the contribution is a coherent composition rather than a collection of unrelated IDs.

---

# 58. Null / Alternative Statements

Where useful, formalize hypotheses against explicit nulls.

Example H4:

\[
H_0:
RR_{B4} \ge RR_{best\ baseline}
\]

\[
H_A:
RR_{B4} < RR_{best\ baseline}
\]

within specified workload regions.

For correctness hypotheses, the stronger model obligation remains invariant-based rather than purely statistical.

---

# 59. Multiple Comparisons

Large parameter sweeps can produce accidental favorable regions.

Therefore:

- distinguish exploratory sweeps from confirmatory comparisons;
- do not highlight isolated wins without neighboring support;
- report full response surfaces where feasible;
- use held-out confirmation configurations for major performance claims where practical.

The objective is robustness, not benchmark selection.

---

# 60. Pilot Experiments

Small pilot experiments may be used to:

```text
debug infrastructure
estimate runtime
identify metric variance
set plotting ranges
identify obviously irrelevant parameter regions
```

Pilot results must not silently become confirmatory headline results.

Any parameter choice influenced by pilot results must be recorded.

---

# 61. Reproducibility Modes

The artifact should eventually expose:

## Quick mode

Small deterministic correctness subset.

## Standard mode

Core paper correctness and representative efficiency experiments.

## Full mode

Large parameter sweeps and trace-driven analysis.

This allows reviewers to reproduce core claims without requiring the complete computational budget.

---

# 62. CPU-Only Requirement

All mandatory Paper 1 experiments must be executable without proprietary GPU access except optional EV4 validation.

Mandatory:

```text
EV0
EV1
EV2
EV3
```

must be CPU-executable.

This is a project constraint.

If an EV3 component requires GPU execution merely to operate rather than to calibrate optionally, it does not satisfy the intended research workflow.

---

# 63. Experiment Budget Strategy

Use staged evaluation.

```text
C1 correctness
    ↓
C2 simulator
    ↓
C4 correctness gate
    ↓
only if G1 passes
    ↓
C5 traces
    ↓
C6 calibration
    ↓
C7 broad efficiency sweeps
```

Do not spend large simulation budgets before the correctness thesis passes G1.

---

# 64. Data Integrity

Downloaded trace data should be:

```text
versioned by source/version
checksummed
stored or referenced reproducibly
never silently modified
```

Augmented derivatives must retain a link to the original source record where feasible.

---

# 65. Trace Splitting

Where enough trace duration/data exists, separate:

```text
calibration/exploration
confirmation
```

or use multiple independent trace segments.

This reduces overfitting policy parameters to one trace slice.

---

# 66. Policy Tuning Fairness

If B4 receives parameter tuning:

```text
retention weights
routing cost coefficients
evidence thresholds
```

comparable baselines should receive reasonable tuning effort.

Policy parameters should be tuned on separate workloads/configurations from final headline evaluation where feasible.

---

# 67. Information Advantage Reporting

Every experiment must report what information each policy receives.

This is essential because B4 intentionally has richer semantic information.

The paper must distinguish:

> advantage from better algorithm

from:

> advantage from additional information.

In this research, the latter is itself part of the abstraction contribution, but it must remain explicit.

---

# 68. Oracle Baseline

Where useful, include an optional **Oracle** policy with ground-truth future/State knowledge.

Purpose:

```text
upper bound
```

not realistic competitor.

Examples:

- perfect State-location knowledge;
- perfect future tool-return knowledge;
- perfect failure knowledge.

Oracle results answer:

> How much optimization headroom exists beyond B4?

Oracle must never be presented as a deployable baseline.

---

# 69. Conservative Safety Baseline

Where useful, include a trivial safe baseline:

```text
never reuse uncertain State
always recompute after uncertainty
```

Purpose:

demonstrate the distinction between:

```text
safe by refusing optimization
```

and:

```text
safe while preserving useful reuse
```

This is particularly relevant to RQ2 and RQ3.

---

# 70. Failure Injection Validation

Before using fault campaigns, validate that each injector actually produces the intended ground-truth condition.

Example:

For:

```text
LATE_SUPERSEDED_ATTEMPT
```

verify:

1. A1 physically remains active/unfinished at supersession;
2. A2 becomes authoritative;
3. A1 completion is delivered after supersession;
4. ground truth labels A1 stale at delivery.

Fault injection itself must be tested.

---

# 71. Simulator Correctness Validation

The simulator must not be considered trustworthy merely because it executes.

Required:

```text
event-order unit tests
queue tests
State-transfer tests
fault-injector tests
ground-truth oracle tests
semantic replay against C1
```

The C1 deterministic core should serve as a reference for equivalent semantic transitions.

---

# 72. Performance Model Separation

The simulator should conceptually separate:

```text
Continuity/event semantics
```

from:

```text
inference execution cost
```

Architecture:

```text
Continuity discrete-event engine
          │
          ├── semantic events
          └── policy decisions
                   │
                   ▼
             Cost Model
                   │
       prefill/decode/transfer
```

This allows replacement of the physical cost model without altering Continuity semantics.

---

# 73. Measurement Perturbation

EV1 overhead experiments must account for measurement overhead.

Avoid expensive invariant scans in production-timing measurements if they are test-only assertions.

Report clearly whether:

```text
debug invariant checks
```

are enabled.

Correctness guards required by the actual architecture remain enabled.

---

# 74. Warm-Up and Steady State

For throughput/latency experiments:

- define warm-up period where appropriate;
- report whether caches begin cold or warm;
- define initial State placement;
- keep identical initial conditions across policies.

Cold/warm ambiguity can otherwise dominate results.

---

# 75. Random Seed Policy

Every stochastic run has an explicit seed.

Headline comparisons should use paired seeds where possible:

```text
same workload
same failures
same arrivals
different policy
```

This reduces variance and makes comparisons more interpretable.

---

# 76. Paired Policy Evaluation

Whenever feasible, generate one ground-truth event workload and replay it against multiple policies.

For example:

```text
Trace T / Seed 42
├── B0
├── B1
├── B2
├── B3
└── B4
```

Differences then arise from policy behavior rather than different random workloads.

---

# 77. Handling Policy-Caused Divergence

Policies may change future execution timing and thereby alter later system state.

Therefore paired replay has two modes.

## Open-loop

Fault/workload schedule remains fixed independently of policy.

Useful for causal comparison of decisions.

## Closed-loop

Future events depend on policy outcomes.

Useful for realistic system evolution.

Both may be required.

They must be labeled distinctly.

---

# 78. Counterfactual Analysis

Where feasible, record enough state to ask:

```text
What would the alternative policy have done
at the same decision point?
```

This is particularly useful for:

```text
routing
State retention
migration
```

It can help explain why policies diverge.

Counterfactual results must not replace full closed-loop evaluation.

---

# 79. Error Analysis

Every observed semantic error from any policy should be classified.

Categories:

```text
STALE_ATTEMPT
WRONG_STATE
WRONG_BRANCH
STALE_BINDING
AMBIGUOUS_COMMIT
DUPLICATE_FINALIZATION
OTHER
```

For B4, any nonzero core category triggers investigation before aggregation.

Do not hide invariant failures inside averages.

---

# 80. Overhead Decomposition

Continuity overhead should be separated into:

```text
identity creation
graph lookup
ancestry check
evidence evaluation
Binding validation
reconciliation
metadata serialization
network transport
```

This supports future optimization and helps determine whether one mechanism dominates cost.

---

# 81. Scalability Sweeps

EV1/C8 should scale at least conceptually over:

```text
active Sessions
Continuation count
State count
replica count
event rate
worker count
```

Desired outputs:

```text
decision latency vs graph size
memory vs State count
event throughput vs concurrency
reconciliation latency vs evidence count
```

Exact maximum scale depends on available CPU resources.

---

# 82. Performance-Correctness Frontier

For H3/H6 especially, plot:

```text
x-axis:
strength/freshness of evidence requirement

y-axis 1:
semantic error

y-axis 2:
latency / recomputation / explicit deferral
```

This may become one of the most important scientific results.

The contribution is potentially not:

> maximum performance

but:

> explicit control over correctness versus speculation.

---

# 83. Phase Diagram Requirement

At least one major efficiency result should be represented as a phase diagram or response surface.

Candidate axes:

### Retention

```text
tool-gap duration
×
cache pressure
```

### Routing

```text
State reuse fraction
×
queue imbalance
```

### Migration

```text
State size
×
transfer bandwidth
```

### Program behavior

```text
branch factor
×
shared-prefix size
```

This better supports the conditional nature of H4–H7.

---

# 84. Required Negative Controls

At minimum:

## NC1 — Stateless workload

Continuity should not manufacture large benefits.

## NC2 — Zero reusable State

State-aware policies should converge toward ordinary scheduling.

## NC3 — Zero faults

Safety mechanisms should impose only overhead, not correctness advantage.

## NC4 — Perfect fresh evidence

Evidence-authority differences should narrow where all evidence is reliable.

## NC5 — Infinite cache

Retention-policy differences should diminish.

These controls validate causal interpretations.

---

# 85. Required Positive Stress Cases

At minimum:

## PC1 — High retry overlap

Challenges Attempt fencing.

## PC2 — Sibling branches with attractive incompatible State

Challenges lineage.

## PC3 — Stale conflicting Binding observations

Challenges epochs/evidence.

## PC4 — Long tool gaps under pressure

Challenges lifecycle retention.

## PC5 — Large shared ancestor with fan-out

Tests reuse opportunity.

These are deliberately adversarial and should be labeled as such.

---

# 86. Evidence Ledger

Every result in the analysis output should carry something equivalent to:

```text
evidence_class:
    E1_MEASURED_CPU

workload_class:
    TRACE_AUGMENTED

synthetic_fields:
    branch_factor
    tool_gap

trace_fields:
    arrival_time
    input_tokens

modeled_fields:
    prefill_latency
```

This mirrors the paper's own evidence-authority philosophy.

---

# 87. Claim Language Rules

Examples of permitted wording:

### EV1

> “In the CPU distributed prototype, Continuity added X µs median scheduling overhead.”

### EV3

> “Under the calibrated simulation model, Continuity reduced modeled Program Completion Time by X%.”

### Synthetic sensitivity

> “Across the evaluated synthetic branch-factor range, benefit increased with branching.”

### Forbidden without direct evidence

> “Continuity reduces H100 latency by X%.”

unless measured or explicitly qualified as modeled.

---

# 88. Publication Success Criteria

The experimental program supports Paper 1 when all of the following hold.

## Correctness

At least one material failure class demonstrates a semantic distinction between B4 and simpler abstractions, with B4 preserving its kernel invariants.

## Efficiency

At least one meaningful workload region demonstrates useful state-management benefit, or a meaningful safety/performance frontier is established.

## Practicality

Real CPU measurements show the control plane is not itself the dominant bottleneck for target workloads.

## Realism

Public trace-derived characteristics contribute to the evaluation.

## Robustness

Synthetic parameters are sensitivity-tested.

## Causality

Ablations connect mechanisms to outcomes.

## Portability

Equivalent semantics are demonstrated in multiple integration environments.

## Reproducibility

Results trace back to manifests, seeds, source data, and Git revisions.

---

# 89. Conditions Requiring Thesis Revision

The experimental program must trigger thesis review if:

```text
all competent baselines already preserve the same execution/state semantics

State lineage provides no safety distinction

evidence authority provides no useful distinction

Continuity's overhead dominates intended workloads

benefits occur only in implausible parameter regions

portability requires changing core semantics
```

A thesis review may result in:

```text
narrowed claim
revised mechanism
negative-result paper direction
or project stop
```

---

# 90. C0 Traceability Contract

Every implemented Paper 1 mechanism must map into this table:

```text
Mechanism
    ↓
RQ
    ↓
Hypothesis
    ↓
Invariant
    ↓
Experiment
    ↓
Metric
    ↓
Evidence
    ↓
Decision Gate
```

No mechanism should enter Paper 1 merely because it appears architecturally attractive.

---

# 91. C0 Completion Checklist

C0 is ready for Gate G0 review when the following five documents are mutually consistent:

```text
01 — Research Thesis
02 — Continuity Model
03 — Invariants
04 — Failure Model
05 — Experimental Plan
```

Specifically verify:

### Thesis → Model

Every claimed concept exists formally.

### Model → Invariants

Every correctness-sensitive semantic transition has protection.

### Invariants → Failure Model

Every central invariant is challenged by a modeled fault.

### Failure Model → Experimental Plan

Every claimed failure class has an experiment or explicit out-of-scope designation.

### Experimental Plan → Thesis

Every RQ/Hypothesis has evidence capable of supporting or falsifying it.

---

# 92. Gate G0 Review Questions

Before implementation begins, answer:

1. Is Continuity defined without reference to a provider-specific primitive?
2. Are Program, Session, Continuation, LogicalRequest, Attempt, and Phase non-overlapping semantic identities?
3. Is reusable State logically distinct from physical replica placement?
4. Is State compatibility deterministic given lineage plus adapter validity?
5. Are Attempt execution outcome and semantic authority orthogonal?
6. Are `CurrentAttempt` and `CommittedAttempt` unambiguous, and can a superseded Attempt ever regain authority?
7. Does State compatibility include producer-Attempt authority and logical StateValidity where applicable?
8. Are BindingID, base epoch, candidate epoch, and committed Binding authority sufficient to fence concurrent migration candidates?
9. Are authority, freshness, status, scope, and confidence separate runtime Evidence dimensions?
9. Does every ambiguous correctness state have a fail-closed outcome?
10. Can all kernel invariants be tested without GPU access?
11. Does every RQ map to a concrete experiment?
12. Are the baselines sufficiently strong and fair?
13. Can negative results falsify or narrow the thesis?
14. Are measured and simulated claims cleanly separated?
15. Can the required experiment program be executed using CPU resources plus public data and simulation?
16. Is Paper 1 still limited to attempt fencing, compatible-State routing, lifecycle retention, and safe migration?
17. Does the project avoid requiring full autoscaling, global program scheduling, or cloud-specific infrastructure?
18. Can the same semantic core plausibly support the intended Gateway API case study and a second environment?

Any unresolved “no” blocks C1 unless explicitly accepted as a scoped limitation.

---

# 93. Immediate Post-G0 Implementation Order

If Gate G0 passes:

```text
C1.1 Identity and entity types

C1.2 Continuation DAG

C1.3 LogicalRequest / Attempt authority

C1.4 reusable-State provenance

C1.5 compatibility

C1.6 Binding epochs

C1.7 Evidence model

C1.8 semantic commit API

C1.9 Reconciler

C1.10 invariant oracle

C1.11 deterministic counterexample tests

C1.12 property/sequence testing
```

No performance policy should be optimized before the semantic core passes the invariant suite.

---

# 94. Canonical Experimental Principle

The experimental program is governed by one final rule:

> **Correctness claims must be established against explicit semantic ground truth; performance claims must be established against realistic or transparently modeled cost; and neither may be strengthened beyond the evidence class that produced it.**

Operationally:

```text
formal model
   +
adversarial correctness
   +
real CPU execution
   +
public traces
   +
synthetic sensitivity
   +
calibrated simulation
   +
ablation
   +
portability
        ↓
defensible systems evidence
```

The purpose of the evaluation is not to prove that Continuity wins every benchmark.

It is to determine precisely:

> **what Continuity prevents, what it costs, what it enables, and under which stateful distributed-inference conditions it becomes valuable.**