# 10 — C2.4 Fault Injection
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C2.4 — Deterministic and Probabilistic Fault Injection  
**Prerequisite:** C2.3 CLOSED on `main` via PR #16 / `e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6`  
**Status:** IN PROGRESS — C2.4.1 implementation candidate

---

# 1. Purpose

C2.4 introduces policy-neutral fault ground truth around the closed C1 semantics and merged C2 event/resource substrate.

The governing separation is:

> **The fault injector decides what physical/observational disturbance occurs; policies decide how to respond; C1 decides what semantic result is valid.**

Fault injection therefore must not:

```text
choose routing
choose retry/recovery policy
rewrite Attempt authority
rewrite reusable-State lineage
rewrite Binding authority
promote Evidence authority
mutate ContinuityCore stores directly
```

---

# 2. C2.4 Slice Plan

Tracking:

```text
#10  C2.4 umbrella
#18  C2.4.1 fault metadata + delivery/resource transformation substrate
#19  C2.4.2 mandatory failure-class injectors + semantic outcome linkage
#20  C2.4.3 probabilistic campaign manifests + replay/reuse contract
#21  C2.4.4 injector trust oracle + closure review
#22  C2.4.5 post-merge documentation synchronization
```

C2.5 representability remains separate.

---

# 3. C2.4.1 Fault Record

Every injected fault is represented by immutable `FaultRecord` metadata containing:

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
produced_event_ids
cancelled_event_ids
optional probabilistic seed
optional probabilistic draw
```

The first fault classes are:

```text
DELIVERY_DELAY
DELIVERY_DROP
DELIVERY_DUPLICATE
DELIVERY_REORDER
WORKER_FAILURE
REPLICA_LOSS
```

These are simulator/physical facts, not semantic authority claims.

---

# 4. Delivery Transformations

C2.4.1 operates only on pending C2 events.

## Delay

```text
original pending delivery
  -> schedule equivalent replacement at later simulated time
  -> cancel original delivery
```

## Drop

```text
original pending delivery
  -> cancel with no replacement
```

## Duplicate

```text
original pending delivery remains
  + one additional delivery
```

For `OBSERVATION_CREATED`, the additional delivery uses `OBSERVATION_DUPLICATED`; for other events it preserves the original EventKind.

## Reorder

```text
target pending delivery
  -> schedule replacement at/after anchor time with later insertion sequence
  -> cancel original target delivery
```

This relies only on the deterministic C2 `(time, insertion sequence)` ordering contract.

---

# 5. Resource Faults

`WORKER_FAILURE` delegates to `ResourceModel.fail_worker`.

`REPLICA_LOSS` delegates to `ResourceModel.lose_replica`.

`FaultInjector` requires the `ResourceModel` to reference the same simulator instance.

Neither fault mutates C1 semantic State or StateReplica objects.

---

# 6. Probabilistic Generation

The injector owns a dedicated `random.Random(seed)` instance rather than consuming the simulator RNG.

This isolates fault-schedule randomness from unrelated simulator random draws.

The initial probabilistic delivery generator accepts probabilities for:

```text
DELIVERY_DROP
DELIVERY_DUPLICATE
DELIVERY_DELAY
NO_FAULT = remaining probability mass
```

Every probabilistic decision records:

```text
seed
ordinal
target
draw
selected FaultClass or none
FaultID when injected
```

Delay/duplicate secondary delay draws come from the same injector-local RNG and are therefore reproducible under equal seed/configuration/call order.

Reorder remains deterministic in C2.4.1 because it requires an explicit anchor event; campaign-level stochastic reorder generation is deferred to #20.

---

# 7. Ground-Truth Validation

`FaultInjector.assert_ground_truth()` checks the injector's own claims against simulator/resource state.

Current checks include:

```text
cancelled delivery did not execute or remain pending
fault-produced event remains pending or appears in trace
reordered replacement executes after its anchor when both execute
executed worker-failure event leaves Worker DOWN
executed replica-loss event leaves ReplicaRuntime LOST
```

The oracle deliberately validates injected physical truth only. Semantic outcome linkage is added in #19 so the injector does not silently become a second C1 oracle.

---

# 8. C2.4.1 Validation Obligations

The first slice must validate:

```text
delay cancellation/rescheduling
drop suppression
duplicate delivery
duplicate observation EventKind
same-time and different-time reorder
FaultID uniqueness
pending-event requirement
probabilistic no-fault decision
same-seed probabilistic reproducibility
probability validation
worker-failure delegation
replica-loss delegation
same-simulator ResourceModel requirement
ground-truth validation before and after delivery
all pre-existing C1/C2 tests remain green
Python 3.11–3.13 CI
```

---

# 9. Explicit Non-Scope

C2.4.1 does not yet claim complete support for:

```text
all mandatory Failure Model classes
Binding migration conflicts
State compatibility faults as semantic injections
semantic outcome classification per FaultID
persisted probabilistic campaign manifests
fault schedule replay across policies
W1–W10 / FTR1–FTR12 representability closure
baseline policies
performance/cost modeling
```

Those remain #19–#21 and C2.5.

---

# 10. Closure Criterion for C2.4.1

C2.4.1 may close only when:

```text
fault records are immutable and explicit
fault transformations are deterministic under equal inputs
probabilistic generation is same-seed reproducible
fault application is fail-closed / side-effect-safe on rejection
resource faults remain physical-only
injector ground truth validates against simulator/resource state
full repository tests pass on Python 3.11–3.13
bounded fault-boundary review has no unresolved blocker
```

C2.4 umbrella remains open after this first slice.
