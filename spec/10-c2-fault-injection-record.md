# 10 — C2.4 Fault Injection
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C2.4 — Deterministic and Probabilistic Fault Injection  
**Prerequisite:** C2.3 CLOSED on `main` via PR #16 / `e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6`  
**Status:** IN PROGRESS — C2.4.1 CLOSED; C2.4.2 implementation candidate

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
#18  C2.4.1 fault metadata + delivery/resource transformation substrate — CLOSED
#19  C2.4.2 mandatory failure-class injectors + semantic outcome linkage — ACTIVE
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

C2.4.1 fault classes are:

```text
DELIVERY_DELAY
DELIVERY_DROP
DELIVERY_DUPLICATE
DELIVERY_REORDER
WORKER_FAILURE
REPLICA_LOSS
```

These are simulator/physical facts, not semantic authority claims.

`expected_safe_outcomes` are simulator-level analysis labels. They are not asserted to be members of the C1 `ReconcileOutcome` enum.

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
fault-produced event remains pending, appears in trace, or is explicitly cancelled by a later fault
reordered replacement executes after its anchor when both execute
executed worker-failure event leaves Worker DOWN unless a later recovery occurs
executed replica-loss event leaves ReplicaRuntime LOST
executed replica-eviction event leaves ReplicaRuntime EVICTED
```

The oracle validates injected physical truth only. It does not become a second C1 semantic oracle.

---

# 8. C2.4.1 Closure

C2.4.1 closed through PR #25.

Final validated PR head:

```text
924e57b2ec4ca0a75a0cd3b6af35fc2bfe703627
```

Exact-head validation:

```text
Python 3.11  PASS
Python 3.12  PASS
Python 3.13  PASS
222 passed
```

Squash merge on `main`:

```text
a469b3eae80e258b3580e20526099dd0ef6e278c
```

A bounded fault-boundary review found and fixed four defects before merge:

1. rejected transformations could consume a FaultID;
2. rejected probabilistic transformation could advance injector RNG state;
3. composed fault chains could falsely fail ground-truth validation;
4. worker failure followed by valid later recovery could be misclassified by final-state-only validation.

The fixes make rejected application FaultID/RNG side-effect-safe and ground-truth validation trace/composition aware.

---

# 9. C2.4.2 Cross-Layer Fault Vocabulary

C2.4.2 adds explicit cross-layer classes where the disturbance itself must be represented through existing C2.3/C2.2 public surfaces:

```text
ATTEMPT_TIMEOUT
LATE_ATTEMPT_RESULT
STALE_ATTEMPT_OBSERVATION
REPLICA_EVICTION
```

`CrossLayerFaultInjector` is an internal extension of the C2.4.1 injector and therefore shares the same FaultID namespace.

It may:

```text
schedule ATTEMPT_TIMEOUT through ContinuityAdapter
schedule a LATE_RESULT through ContinuityAdapter
schedule a stale terminal observation for a SUCCEEDED + SUPERSEDED Attempt through ContinuityAdapter
delegate physical replica eviction to ResourceModel
```

It may not directly assign into `ContinuityCore` stores.

Precondition checks establish fault ground truth at injection time. For example:

```text
late-result fault requires AttemptAuthority.SUPERSEDED
stale terminal observation requires SUCCEEDED + SUPERSEDED
retry-timeout fault targets the current Attempt at injection time
```

If a future scheduled fault becomes stale before delivery, the existing C2.3 adapter fences it according to current C1 authority.

---

# 10. Mandatory Failure-Class Composition Boundary

C2.4 does not require a bespoke mutator for every FTR trace.

The Failure Model classes are represented by composition when the underlying fault is already expressible:

```text
partial migration acknowledgment loss
    -> DELIVERY_DROP / DELIVERY_DELAY on migration/transfer completion

migration destination failure
    -> WORKER_FAILURE on destination during transfer

late old-binding observation
    -> DELIVERY_DELAY / DELIVERY_REORDER

ambiguous ownership
    -> multiple valid scenario observations + delivery/reordering fault pattern

stale high-authority Evidence
    -> scenario Evidence validity/freshness + delayed delivery

total physical State loss
    -> REPLICA_LOSS across all physical replicas

tool-wait eviction
    -> REPLICA_EVICTION during waiting interval
```

C2.5 will prove W1–W10/FTR1–FTR12 end-to-end representability. C2.4.2 only establishes the reusable fault primitives and linkage needed by that later campaign.

---

# 11. Fault-to-Outcome Linkage

`FaultOutcomeLinker` correlates one committed `FaultRecord` with observed C2/C1 consequences without authorizing recovery.

`FaultOutcomeRecord` contains:

```text
FaultID
FaultClass
observation time
outcome class
related event IDs
matching SemanticActionRecord entries
invariant violations, if any
semantic error text, if any
physical status summary
optional RequestID
optional AuthoritativeOutcome projection
optional policy label
optional recovery action
optional recovery latency
```

The current descriptive `FaultOutcomeClass` values are:

```text
PENDING
DELIVERY_SUPPRESSED
PHYSICAL_EFFECT
SEMANTIC_APPLIED
SEMANTIC_IDEMPOTENT
SEMANTIC_IGNORED
SEMANTIC_REJECTED
```

These are experiment-observation classes, not semantic commit outcomes.

Classification precedence is intentionally fail-closed:

```text
REJECTED semantic action
    > IGNORED
    > APPLIED
    > IDEMPOTENT
```

so an event that records non-authoritative observations but whose correctness-sensitive finalization is rejected is classified `SEMANTIC_REJECTED`.

When an adapter and RequestID are available, the linker also records the C2.3 `AuthoritativeOutcome` projection. This lets experiments associate FaultID with the semantic winner without reimplementing C1 authority.

---

# 12. Policy and Recovery Fields

C2.4.2 carries optional:

```text
policy
recovery_action
recovery_latency
```

but does not populate them automatically.

They are external annotations for later policy experiments. The fault linker must not decide them.

`recovery_latency`, when supplied, must be finite and non-negative.

---

# 13. C2.4.2 Validation Obligations

The second slice must validate:

```text
Attempt timeout fault schedules through C2.3 adapter
non-current timeout injection rejected without consuming FaultID
late result requires SUPERSEDED Attempt
late physical success preserves SUPERSEDED authority
stale terminal observation requires SUCCEEDED + SUPERSEDED
stale terminal observation is explicitly rejected for authoritative finalization
replica eviction remains physical-only
worker failure and replica faults link to physical status
FaultID links to adapter action records
FaultID links to final authoritative Request projection when available
invariant violations remain empty for safe traces
pending vs delivered outcome classification
external policy/recovery annotations are pass-through only
non-finite recovery metadata rejected
same-simulator boundaries enforced
all pre-existing C1/C2/C2.4.1 tests remain green
Python 3.11–3.13 CI
```

---

# 14. Explicit Non-Scope After C2.4.2

Still deferred:

```text
persisted probabilistic campaign manifests
fault-schedule serialization/replay
paired-policy schedule reuse contract
full FaultRecord decoded-schema validation
adversarial trust-oracle campaign for malformed fault metadata
end-to-end W1–W10/FTR1–FTR12 simulator representability proof
baseline policy comparison
performance/cost modeling
```

These remain #20, #21, and C2.5.

---

# 15. C2.4.2 Closure Criterion

C2.4.2 may close when:

```text
cross-layer fault helpers use existing public C2.3/C2.2 transition surfaces only
FaultID can be correlated with semantic/resource observations
fail-closed semantic rejection is classified explicitly
late superseded execution cannot regain authority
physical State eviction/loss remains distinct from logical State identity
linkage never chooses recovery policy
fault/outcome metadata is finite and deterministic
full repository tests pass on Python 3.11–3.13
bounded linkage/classification review has no unresolved blocker
```

C2.4 umbrella remains open after this slice.
