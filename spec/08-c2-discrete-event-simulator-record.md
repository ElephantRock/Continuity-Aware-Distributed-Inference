# 08 — C2 Discrete-Event Simulator
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C2 — Discrete-Event Simulator  
**Prerequisite:** C1 CLOSED on `main`  
**Status:** IN PROGRESS — C2.1, C2.2, and C2.3 CLOSED; C2.4 fault injection next

---

# 1. Purpose

C2 adds simulated time and physical/resource dynamics around the closed C1 semantic kernel.

The simulator exists to represent, schedule, and reproduce asynchronous distributed-inference conditions such as:

```text
queueing
message delay
message reordering
timeouts
retries
worker failure/recovery
State materialization/transfer/loss
observation delay/drop/duplication
tool waits
branch lifecycle
resource pressure
```

C2 does not redefine semantic authority.

The central implementation rule is:

> **C1 remains the semantic authority; C2 controls when physical and observational events occur.**

---

# 2. Non-Redefinition Contract

C2 must not introduce a second implementation of:

```text
Program
Session
Continuation
LogicalRequest
Attempt
Phase
ReusableState lineage
Attempt authority
Binding authority
Evidence authority
semantic finalization
```

Those remain defined by `continuity.ContinuityCore` and the closed C1 invariant oracle.

Simulator-only state may represent:

```text
logical simulation time
scheduled event delivery
worker availability
worker queues
network links
physical StateReplica placement/timing
fault ground truth
randomized but seeded schedules
performance/cost quantities introduced in later milestones
```

A simulator event may call a public C1 semantic transition, but C2 must not mutate C1 semantic maps directly.

---

# 3. C2 Evidence Boundary

C2 results are simulation evidence.

They may support:

```text
SIMULATED event/failure behavior
reproducibility
cross-layer semantic agreement
later modeled timing consequences
```

They do not independently establish real accelerator performance.

The Experimental Plan requirement remains:

```text
Continuity/event semantics
          !=
inference execution cost model
```

The C2 event engine is therefore implemented before C6 cost calibration.

---

# 4. Required Event Surface

C2 uses an explicit event vocabulary sufficient to cover the union of the roadmap and Failure Model.

## Request / Attempt

```text
REQUEST_CREATED
ATTEMPT_STARTED
ATTEMPT_TIMEOUT
ATTEMPT_COMPLETED
ATTEMPT_FAILED
RETRY_STARTED
LATE_RESULT
```

## State

```text
STATE_CREATED
STATE_MATERIALIZATION_STARTED
STATE_MATERIALIZED
STATE_TRANSFER_STARTED
STATE_TRANSFER_COMPLETED
STATE_TRANSFER_FAILED
STATE_MOVED
STATE_EVICTED
STATE_LOST
```

## Migration

```text
MIGRATION_STARTED
MIGRATION_COMMITTED
MIGRATION_FAILED
```

## Worker

```text
WORKER_FAILED
WORKER_RECOVERED
WORKER_TASK_ENQUEUED
WORKER_TASK_COMPLETED
WORKER_TASK_FAILED
```

## Observation

```text
OBSERVATION_CREATED
OBSERVATION_DELAYED
OBSERVATION_DROPPED
OBSERVATION_DUPLICATED
```

## Program / lifecycle

```text
TOOL_WAIT_STARTED
TOOL_RETURNED
CONTINUATION_FORKED
CONTINUATION_JOINED
CONTINUATION_ABANDONED
CONTINUATION_TERMINATED
```

Roadmap shorthand maps to the more explicit C2 names:

```text
RETRY      -> RETRY_STARTED
TOOL_WAIT  -> TOOL_WAIT_STARTED
TOOL_RETURN-> TOOL_RETURNED
FORK       -> CONTINUATION_FORKED
JOIN       -> CONTINUATION_JOINED
EVICTION   -> STATE_EVICTED
```

---

# 5. C2.1 Deterministic Event Kernel

C2.1 implements the event substrate.

Each scheduled event has:

```text
EventID
logical time
insertion sequence
EventKind
immutable canonical payload
```

Ordering is exactly:

```text
(time, sequence)
```

This gives deterministic ordering for simultaneous events without using wall-clock time.

## Logical clock

Simulation time:

```text
starts at 0
never moves backward
advances to each delivered event
may advance explicitly to a run horizon
```

Scheduling an event in the simulated past is invalid. Non-finite time values are invalid.

## Stable identity

EventID is unique for the lifetime of one simulator instance. Automatic EventIDs are deterministically derived from insertion sequence. Explicit reuse of an EventID is rejected even after the original event executed or was cancelled.

## Cancellation

Cancellation is delivery cancellation, not historical deletion.

A cancelled pending event:

```text
is not delivered
is not added to the executed trace
cannot be cancelled twice
```

Later C2 fault metadata may separately record why cancellation occurred.

## Handler ordering

Handlers are invoked in registration order.

A handler may schedule another event at the current logical time. Such an event receives a later insertion sequence and therefore executes after the currently delivering event and after any already-scheduled same-time events with lower sequence.

## C2.1 closure result

C2.1 closed on 2026-08-30 when PR #14 was squash-merged to `main` as `f4e854fa930b09c27d2ea2bea9ecbca04b7ff00d`. The final PR head `d2a3ebba8e119331f21958b828a2d08605a4c6a4` passed the full repository suite on Python 3.11, 3.12, and 3.13 with:

```text
169 passed
```

A bounded exact-delta review found and fixed three event-kernel defects before merge:

1. rejected scheduling consumed sequence numbers and shifted later automatic EventIDs;
2. `max_events` could fail to remain the actual stop condition when queue exhaustion coincided with an `until` horizon;
3. top-level payload validation did not enforce the documented string-keyed mapping boundary.

Rejected scheduling is now side-effect-free, `max_events` is a strict stop condition, and top-level payloads must be mappings. All three are regression-tested.

---

# 6. Reproducibility Contract

Every simulator instance has an explicit integer seed.

The simulator owns a dedicated pseudorandom generator rather than using process-global randomness.

For equal:

```text
initial C1 state
simulator configuration
seed
scheduled input events
handler registration order
```

C2 must produce equal:

```text
random draws
scheduled event order
executed event trace
authoritative C1 semantic result
```

The final condition is now executable for the C2.3 retry/finalization surface.

Headline paired-policy evaluation in C3/C4 will therefore be able to reuse the same seed and workload/fault schedule.

---

# 7. Event Payload Contract

C2.1 payloads are immutable canonical data composed from:

```text
None
bool
int
finite float
str
list/tuple -> immutable tuple
string-keyed mapping -> sorted immutable tuple of pairs
```

Unsupported mutable/opaque values are rejected at scheduling time.

This keeps the executed event trace deterministic and suitable for later canonical serialization.

C2.1 does not yet define the final persisted trace schema; that remains deferred until the resource/fault metadata surfaces stabilize.

---

# 8. C2.2 Resource Model

C2.2 adds deterministic physical-resource state without changing C1 semantics.

Merged resource surfaces include:

```text
Worker and WorkerStatus
worker queue and capacity
ResourceTask lifecycle
NetworkLink latency/bandwidth timing
ReplicaRuntime physical shadow
State materialization
State transfer
State eviction/loss
worker failure/recovery
transfer failure
```

The model preserves the distinction:

```text
logical State identity
        !=
physical StateReplica runtime placement
```

`ReplicaRuntime` records reference C1 State/Replica identities but are explicitly non-authoritative physical facts. Resource events do not rewrite C1 provenance, Attempt authority, Binding authority, or Evidence authority.

## C2.2 closure result

C2.2 closed on 2026-08-30 when PR #15 was squash-merged to `main` as `9c2c3f0801bca9abf165bf626970c2e9d8fa7d5e`.

The exact final head `d3059c883a859517d7ecadb71504e7ae7b44f3ea` passed the full repository suite on Python 3.11, 3.12, and 3.13 with:

```text
183 passed
```

The merged C2.2 contract provides deterministic worker queues, synthetic network timing, non-authoritative replica runtime state, materialization/transfer/failure handling, and bounded lifecycle hardening.

---

# 9. C2.3 Semantic Adapter and Replay Equivalence

C2.3 attaches timed simulator delivery to exactly one closed C1 `ContinuityCore`.

The adapter contract is:

```text
use public C1 transitions only
run the independent invariant oracle
never mutate semantic stores directly
record applied / idempotent / rejected / ignored interactions
record post-operation canonical C1 fingerprints
require rejected semantic operations to be fingerprint-stable
```

Cross-layer validation compares:

```text
C1 deterministic semantic reference
        vs
C2 timed event schedule
```

using an explicit authoritative projection rather than event-history equality.

The projection includes:

```text
Request status
CurrentAttempt
CommittedAttempt
authoritative Output/Evidence IDs
per-Request Attempt generation
Attempt execution status
Attempt authority status
```

## Canonical retry race

```text
A1 CURRENT
A1 timeout
A2 starts -> A1 SUPERSEDED, A2 CURRENT
A1 may later succeed physically
A2 succeeds
exact A2 terminal observation arrives
A2 COMMITTED
later A1 terminal observation cannot regain authority
```

The allowed late physical outcome remains:

```text
A1 execution = SUCCEEDED
A1 authority = SUPERSEDED
```

## C2.3 bounded-review corrections

Three correctness defects were found and fixed before closure:

1. duplicate/reordered observations initially matched Evidence identity only partially; the adapter now carries the original `observed_at` and requires exact immutable C1 Evidence equality;
2. a terminal exact observation could claim an observation time earlier than the adapter-delivered Attempt success; C2 now fences that causal inversion;
3. an intermediate retry-dedup approach allowed host/Python scheduling-call order to influence earlier simulated time; timeout-generated retry EventIDs are now parent-derived and semantic convergence depends on delivered C2 history/current-Attempt fencing rather than setup order.

Regression coverage includes:

```text
late success before/after retry success
late success after finalization
duplicate late completion
duplicate timeout
stale timeout after finalization
duplicate authoritative observation
reordered duplicate-before-original observation
conflicting Evidence identity
observation timestamp before delivered success
simultaneous timeout/completion in both insertion orders
preplanned retry vs earlier timeout under both setup orders
late superseded observation rejection
malformed adapter events
fingerprint-stable rejected semantic operations
```

## C2.3 closure result

C2.3 closed on 2026-08-30 when PR #16 was squash-merged to `main` as `e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6`.

The exact user-authored closure head `c0125d84b20f5d58553ce241fd254169534783e4` passed the full repository suite on Python 3.11, 3.12, and 3.13 with:

```text
203 passed
```

Issue #9 is closed as completed.

---

# 10. C2.4 Fault Injection

C2.4 is the next implementation slice and is tracked by issue #10.

C2 will support both deterministic and probabilistic fault injection.

Every injected fault must eventually record:

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

Every observed semantic result must record:

```text
FaultID
policy
outcome_class
invariant_violations
recovery_action
recovery_latency
semantic_error
```

Probabilistic fault generation must record its seed.

C2.4 specifically requires:

```text
explicit fault metadata
deterministic injectors for mandatory failure classes
seeded probabilistic injectors
delay/drop/duplicate/reorder delivery support
validated injector ground truth before campaign use
```

Fault injectors themselves require validation before use in experiments.

C2.4 has not started as of the C2.3 bookkeeping checkpoint.

---

# 11. C2.5 Representability and Closure

C2 closes only when the simulator can deterministically represent:

```text
W1–W10 workload families
FTR1–FTR12 mandatory failure traces
```

and supports the Failure Model event requirements including delayed, duplicated, reordered, and dropped observations.

Required simulator trust tests include:

```text
event-order tests
queue tests
State-transfer tests
fault-injector tests
ground-truth oracle tests
semantic replay against C1
same-seed reproducibility
```

---

# 12. C2 Exit Criterion

The roadmap exit criterion is interpreted operationally as:

> **All required workload and failure classes can be represented deterministically with reproducible random seeds, and correctness-equivalent timed C2 traces agree with the closed C1 semantic reference.**

C2 does not require:

```text
baseline policy comparison
Gate G1 correctness results
public trace ingestion
calibrated accelerator timing
broad efficiency sweeps
```

Those remain C3–C7.

---

# 13. Current Status

```text
C1   CLOSED
C2   IN PROGRESS
C2.1 event kernel       CLOSED — PR #14 / f4e854fa930b09c27d2ea2bea9ecbca04b7ff00d / 169 tests
C2.2 resource model     CLOSED — PR #15 / 9c2c3f0801bca9abf165bf626970c2e9d8fa7d5e / 183 tests
C2.3 semantic adapter   CLOSED — PR #16 / e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6 / 203 tests
C2.4 fault injection    NEXT — issue #10; not started
C2.5 representability   not started
C3 baseline policies    not started
```

Tracking:

```text
#6  C2 umbrella
#7  C2.1 event kernel
#8  C2.2 resource model
#9  C2.3 semantic adapter — closed
#10 C2.4 fault injection — open / next
#11 C2.5 representability / closure
```
