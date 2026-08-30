# 08 — C2 Discrete-Event Simulator
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C2 — Discrete-Event Simulator  
**Prerequisite:** C1 CLOSED on `main`  
**Status:** IN PROGRESS — C2.1 event kernel

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

A simulator event may eventually call a public C1 semantic transition, but C2 must not mutate C1 semantic maps directly.

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

C2.1 implements only the event substrate.

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

Scheduling an event in the simulated past is invalid.

Non-finite time values are invalid.

## Stable identity

EventID is unique for the lifetime of one simulator instance.

Automatic EventIDs are deterministically derived from insertion sequence.

Explicit reuse of an EventID is rejected even after the original event executed or was cancelled.

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

where the last condition becomes executable in C2.3.

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

C2.1 does not yet define the final persisted trace schema; that is deferred until the resource/fault metadata surfaces stabilize.

---

# 8. C2.1 Validation Obligations

The first slice must test:

```text
explicit required EventKind surface
same-time insertion ordering
same-time scheduling from handlers
monotonic logical time
run-until horizon behavior
max-event stopping behavior
cancellation
EventID uniqueness
seed reproducibility
finite time validation
immutable/canonical payload conversion
```

All existing C1 tests must continue passing unchanged.

---

# 9. C2.2 Resource Model

Planned next slice:

```text
Worker
Worker queue
NetworkLink
physical StateReplica timing/location
State materialization
State transfer
State eviction/loss
worker failure/recovery
```

The resource model must preserve the distinction:

```text
logical State identity
        !=
physical StateReplica placement
```

No resource event may implicitly rewrite C1 provenance.

---

# 10. C2.3 Semantic Adapter

C2.3 will attach the simulator to one C1 `ContinuityCore` instance.

Requirements:

```text
use public C1 transitions only
run the independent invariant oracle
never mutate semantic stores directly
record semantic success / explicit non-success / rejection
```

Cross-layer validation will replay correctness-equivalent traces through:

```text
C1 deterministic operation sequence
        vs
C2 timed event schedule
```

Authoritative semantic outcomes must agree even though physical timing differs.

Canonical first example:

```text
A1 active
A1 timeout
A2 becomes authoritative
A2 succeeds
A1 result arrives late
```

Both layers must reject A1 as authoritative.

---

# 11. C2.4 Fault Injection

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

Fault injectors themselves require validation before use in experiments.

---

# 12. C2.5 Representability and Closure

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

# 13. C2 Exit Criterion

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

# 14. Current Status

```text
C1   CLOSED
C2   IN PROGRESS
C2.1 event kernel       implementation candidate
C2.2 resource model     not started
C2.3 semantic adapter   not started
C2.4 fault injection    not started
C2.5 representability   not started
C3 baseline policies    not started
```

Tracking:

```text
#6  C2 umbrella
#7  C2.1 event kernel
#8  C2.2 resource model
#9  C2.3 semantic adapter
#10 C2.4 fault injection
#11 C2.5 representability / closure
```
