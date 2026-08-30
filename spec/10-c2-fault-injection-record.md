# 10 — C2.4 Fault Injection
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C2.4 — Deterministic and Probabilistic Fault Injection  
**Prerequisite:** C2.3 CLOSED on `main` via PR #16 / `e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6`  
**Status:** C2.4 IMPLEMENTATION COMPLETE — C2.4.1–C2.4.4 merged; post-merge synchronization tracked by #22

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
#10  C2.4 umbrella — closure bookkeeping via #22
#18  C2.4.1 fault metadata + delivery/resource transformation substrate — CLOSED
#19  C2.4.2 mandatory failure-class injectors + semantic outcome linkage — CLOSED
#20  C2.4.3 probabilistic campaign manifests + replay/reuse contract — CLOSED
#21  C2.4.4 injector trust oracle + closure review — CLOSED
#22  C2.4.5 post-merge documentation synchronization — ACTIVE
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
INVARIANT_VIOLATION
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
INVARIANT_VIOLATION
    > REJECTED semantic action
    > IGNORED
    > APPLIED
    > IDEMPOTENT
```

so an event that records non-authoritative observations but whose correctness-sensitive finalization is rejected is classified `SEMANTIC_REJECTED`.

When an adapter and RequestID are available and the independent invariant oracle is clean, the linker also records the C2.3 `AuthoritativeOutcome` projection. If the oracle reports any violation, the outcome is classified `INVARIANT_VIOLATION` and authoritative projection is suppressed. This lets experiments associate FaultID with the semantic winner without reimplementing or masking C1 authority.

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
invariant violation has highest classification precedence and suppresses authoritative projection
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
invariant-oracle failure is classified explicitly and cannot expose an authoritative projection
late superseded execution cannot regain authority
physical State eviction/loss remains distinct from logical State identity
linkage never chooses recovery policy
fault/outcome metadata is finite and deterministic
full repository tests pass on Python 3.11–3.13
bounded linkage/classification review has no unresolved blocker
```

C2.4 umbrella remains open after this slice.

---

# 16. C2.4.2 Closure

C2.4.2 closed through PR #26.

Final validated PR head:

```text
626e431bc4605a308c564085ae0c527af35e370c
```

Exact-head validation:

```text
Python 3.11  PASS
Python 3.12  PASS
Python 3.13  PASS
238 passed
```

Squash merge on `main`:

```text
467e634e1e7017f84cf3f3282a05ba1fbb13adef
```

The bounded linkage review fixed three issues before merge:

1. non-finite recovery latency metadata was accepted;
2. invariant violations could be recorded while the headline outcome still said `SEMANTIC_APPLIED`;
3. an authoritative projection could be exposed even when the independent invariant oracle failed.

The closed behavior makes `INVARIANT_VIOLATION` highest-precedence and suppresses authoritative projection whenever the oracle is not clean.

---

# 17. C2.4.3 Fault Campaign Artifact

C2.4.3 freezes a machine-readable, policy-neutral fault campaign artifact. It is referenced by the broader experiment manifest required by `05-experimental-plan.md`; it does not replace that experiment manifest.

`FaultCampaignManifest` records:

```text
campaign_id
git_commit
scenario_fingerprint
seed
generator
fault_configuration
probabilistic decisions
realized replay schedule
configuration_fingerprint
schedule_fingerprint
manifest_fingerprint
```

The campaign contains no routing or recovery policy choice.

---

# 18. Decisions and Realized Schedule Are Separate

Probabilistic generation records every decision, including `NO_FAULT` decisions. Only realized faults appear in the replay schedule.

This distinction is required because:

```text
random opportunity evaluated
    !=
fault injected
```

Every selected probabilistic decision must name a FaultID that exists in the realized schedule with the same target and FaultClass. A no-fault decision must not name a FaultID.

Replay does not draw randomness again. The seed and probabilistic decisions are provenance/audit evidence for how the schedule was generated; paired runs consume the already-realized immutable schedule.

---

# 19. Fingerprint Contract

C2.4.3 computes independent SHA-256 fingerprints for:

```text
configuration_fingerprint
    = seed + generator + fault_configuration

schedule_fingerprint
    = ordered FaultReplayEntry sequence

manifest_fingerprint
    = complete campaign payload except the manifest fingerprint field itself
```

The manifest fingerprint therefore covers:

```text
git revision
scenario fingerprint
seed/configuration
all probabilistic decisions
realized fault schedule
```

Canonical JSON is used and non-finite JSON values are rejected.

Generated replacement/duplicate EventIDs are intentionally excluded from the policy-neutral replay entry. Stable target identities, injection times, fault class, duration, and class-specific stable parameters remain in the schedule fingerprint.

---

# 20. Replay Contract

`FaultScheduleReplayer` replays a prepared scenario against the recorded schedule.

Before any replay mutation it requires exact equality of:

```text
actual git_commit == manifest.git_commit
actual scenario_fingerprint == manifest.scenario_fingerprint
```

At each recorded injection boundary it then requires the stable target/resource/semantic precondition needed by that FaultClass.

For pending-delivery faults this includes the exact EventID and expected pending event time. Replay never silently retargets a missing or changed event.

If any condition fails:

```text
FaultReplayError
```

is explicit and the run is invalid for paired comparison.

A failure discovered after an earlier valid replay prefix does not roll back simulator history; the experiment harness must discard that failed run rather than treat the partial prefix as a valid paired sample.

---

# 21. Paired-Policy Reuse Contract

A `PolicyFaultBinding` associates a policy identifier with an existing campaign without changing the campaign.

Paired-policy comparison requires equality of:

```text
campaign_fingerprint
schedule_fingerprint
scenario_fingerprint
```

Policy IDs may differ.

This implements Experimental Plan principle X1: fault ground truth is independent of the policy being evaluated.

If a policy cannot expose the stable target required by the recorded schedule, replay fails explicitly. C2.4.3 does not adapt or regenerate a more favorable schedule for that policy.

---

# 22. C2.4.3 Validation Obligations

C2.4.3 must validate:

```text
strict manifest JSON round-trip
configuration/schedule/manifest fingerprint stability
fingerprint tamper detection
non-finite JSON rejection
probabilistic no-fault decisions preserved
selected probabilistic decisions agree with realized FaultID/class/target
exact deterministic delivery schedule replay
same retry-timeout/late-result semantics under replay
missing or time-shifted stable target fails closed
revision mismatch fails before replay mutation
scenario fingerprint mismatch fails before replay mutation
paired policies reference the same campaign/schedule/scenario
configuration fingerprint changes with seed or fault configuration
all pre-existing C1/C2/C2.4 tests remain green
Python 3.11–3.13 CI
```

---

# 23. Explicit Non-Scope After C2.4.3

Still deferred:

```text
full decoded FaultRecord trust-schema oracle
malformed/tampered fault-record adversarial campaign
C2.4 final trust closure
end-to-end W1–W10/FTR1–FTR12 representability proof
full experiment-run manifest implementation
baseline policy comparison
performance/cost modeling
```

These remain #21/#22 and C2.5+.

---

# 24. C2.4.3 Closure Criterion

C2.4.3 may close only when:

```text
fault campaign generation remains policy-neutral
manifest serialization is strict and fingerprinted
probabilistic decision provenance is internally consistent with realized schedule
replay requires matching software revision and scenario fingerprint
replay never silently retargets a missing/different fault target
same prepared scenario reproduces the recorded fault schedule
paired policy bindings require exact campaign/schedule/scenario identity
full repository tests pass on Python 3.11–3.13
bounded reproducibility review has no unresolved blocker
```

C2.4 umbrella remains open after this slice; #21 performs the final injector trust-oracle closure review before C2.5 may start.


---

# 25. C2.4.3 Closure

C2.4.3 closed through PR #27.

Final validated PR head:

```text
a37bd772cab6bfc0aae2d8d8eb1cd5b4ddabe7de
```

Exact-head validation:

```text
Python 3.11  PASS
Python 3.12  PASS
Python 3.13  PASS
250 passed
```

Squash merge on `main`:

```text
484e46e6daac41716654cf8ed76b0f0a6a0606a2
```

The bounded reproducibility review fixed two gaps before merge:

1. campaign revision/scenario fields were recorded but replay did not initially require equality with the prepared run;
2. probabilistic decisions were fingerprinted but not initially cross-checked against the realized FaultID/class/target schedule.

The closed contract fails before replay mutation on revision/scenario mismatch and requires decision-to-realized-schedule consistency.

---

# 26. C2.4.4 Independent Fault Trust Oracle

C2.4.4 adds an independent trust surface around the C2.4 injector artifacts.

`FaultTrustOracle` does not call `FaultInjector.assert_ground_truth()` and does not authorize semantic outcomes. It validates whether immutable fault metadata agrees with independent simulator/resource/C1 observations.

The oracle checks:

```text
FaultRecord schema and finite numeric fields
class-specific parameter keys, scalar types, and temporal relations
independent ground_truth_effect contract
independent expected_invariant_pressure contract
independent expected_safe_outcomes contract
FaultID uniqueness
produced/cancelled EventID structure
probabilistic decision -> FaultRecord consistency
transformation production/cancellation ordering
transformation cycles
runtime produced/cancelled event observations
runtime EventKind/time/target agreement
ResourceModel worker/replica effects
ContinuityAdapter semantic-target and action observations
C1 InvariantOracle status
optional FaultCampaignManifest schedule/decision consistency
```

The class contracts are intentionally duplicated in the trust module rather than imported from private injector tables. A defect in the injector's own expectation metadata should therefore be detectable by the oracle instead of being accepted by construction.

---

# 27. Malformed Metadata Contract

Malformed `FaultRecord` metadata is untrusted input to the trust oracle.

The required behavior is:

```text
malformed metadata
    -> explicit trust violation
    != oracle crash
```

The adversarial corpus covers, among other cases:

```text
non-finite JSON
unknown decoded fields
invalid/unhashable FaultID
invalid/unhashable produced or cancelled EventID entries
wrong class-specific parameter keys/types
wrong ground-truth effect/pressure/safe-outcome metadata
missing produced runtime event
wrong runtime EventKind
wrong runtime target payload
repeated cancellation
transformation cycles
probabilistic decision/record disagreement
campaign schedule disagreement
forced C1 invariant-oracle failure
```

Legitimate composition remains accepted, including delay followed by drop and worker failure followed by a later explicit worker recovery.

---

# 28. C2.4.4 Review Findings

The bounded trust review found and fixed three classes of defects before closure candidacy:

1. malformed numeric duration could be diagnosed and then still coerced later in class-specific validation, turning invalid metadata into an exception;
2. class-specific parameter keys were frozen but some values were not initially type/temporal constrained strongly enough;
3. malformed produced/cancelled EventID tuples containing unhashable values could be diagnosed and then still passed to `set(...)`.

The hardened oracle validates first and only performs downstream operations on fields that passed the relevant structural predicate.

---

# 29. C2.4.4 Validation Obligations

C2.4.4 must validate:

```text
FaultTrustOracle is structurally independent of FaultInjector.assert_ground_truth
strict FaultRecord JSON/JSONL decoding
non-finite and unknown decoded metadata rejected
malformed metadata reported without escaping exceptions
independent class-specific effect/pressure/safe-outcome checks
class-specific parameter value typing and temporal constraints
FaultID/EventID uniqueness and transformation graph consistency
conflicting/repeated transformation detected
valid composed transformations accepted
runtime missing/wrong-kind/wrong-time/wrong-target effects detected
worker/replica physical effects checked without rejecting later legitimate recovery
probabilistic decision-to-record consistency checked
campaign schedule/decision consistency checked
C1 invariant-oracle failure surfaced explicitly
no routing/retry/recovery/migration policy selected by the oracle
all pre-existing C1/C2/C2.4 tests remain green
Python 3.11-3.13 CI
```

The hardened implementation tree was validated at:

```text
b04065778c287a17209e497642e7777fcbd063b7
```

with:

```text
Python 3.11  PASS
Python 3.12  PASS
Python 3.13  PASS
268 passed
```

This is an implementation closure candidate. PR #28 and post-merge documentation checkpoint #22 must still close before the C2.4 umbrella is marked CLOSED.

---

# 30. C2.4.4 Closure Criterion

C2.4.4 may close only when:

```text
independent trust oracle accepts valid deterministic/probabilistic fault records
malformed/tampered fault metadata fails explicitly rather than crashing the oracle
injector expectation metadata is checked against an independent class contract
conflicting transformations are detected
runtime/resource effects agree with recorded fault ground truth
C1 invariant failures are visible to the trust report
campaign manifest and probabilistic decision linkage agree with trusted records
oracle remains descriptive and policy-neutral
full repository tests pass on Python 3.11-3.13
bounded trust/closure review has no unresolved blocker
```

C2.4 remains open after this implementation PR. #22 is a bookkeeping-only post-merge synchronization checkpoint; only after #22 may umbrella #10 close and C2.5 become authorized.


---

# 31. C2.4.4 Closure

C2.4.4 closed through PR #28.

Final validated PR head:

```text
95b31642e205c389bc19acc59bf9bf90d955e5b0
```

Exact-head validation:

```text
Python 3.11  PASS
Python 3.12  PASS
Python 3.13  PASS
268 passed
```

Squash merge on `main`:

```text
f33b82d0802cfc97835049e274b288e06f87d369
```

The final trust review fixed three concrete failure modes before merge:

1. malformed numeric duration could be diagnosed and then still coerced by later class-specific logic;
2. class-specific parameter keys were frozen while some scalar types and temporal relations remained underconstrained;
3. malformed produced/cancelled EventID tuples containing unhashable values could be diagnosed and then still reach hashing/set operations.

The merged trust oracle now treats malformed metadata as untrusted data that produces explicit trust violations rather than an escaping oracle exception across the covered adversarial corpus.

---

# 32. C2.4 Closure Summary

C2.4 now provides a policy-neutral fault-evaluation substrate with four closed implementation slices:

```text
C2.4.1
    deterministic delivery/resource transformations
    explicit immutable FaultRecord ground truth
    injector-local seeded probabilistic generation

C2.4.2
    cross-layer Attempt/State fault primitives
    FaultID -> semantic/resource outcome linkage
    fail-closed invariant-violation classification

C2.4.3
    machine-readable policy-neutral fault campaigns
    configuration/schedule/manifest fingerprints
    exact fault-schedule replay
    paired-policy schedule reuse contract

C2.4.4
    independent FaultRecord trust schema/oracle
    adversarial malformed/tampered metadata campaign
    transformation/runtime/resource/C1/campaign consistency validation
```

The governing authority boundary remains:

> **The fault injector decides what physical/observational disturbance occurs; policies decide how to respond; C1 decides what semantic result is valid.**

No C2.4 component chooses routing, retry, recovery, migration, State compatibility, Attempt authority, or semantic commit.

---

# 33. C2.4 Exit Gate

C2.4 implementation is complete because:

```text
deterministic fault primitives are explicit and reproducible
seeded probabilistic decisions are isolated from simulator RNG
FaultID ground truth is immutable and linkable to observed outcomes
cross-layer faults use existing public C2.3/C2.2 transition surfaces
fault campaigns are canonical, fingerprinted, and replayable
paired policies can reference the same realized fault schedule
replay fails closed instead of silently retargeting divergent scenarios
an independent trust oracle validates fault metadata and observed effects
malformed/tampered metadata is adversarially tested
C1 remains the only semantic authority
all repository tests pass on Python 3.11-3.13
bounded C2.4 closure review has no unresolved implementation blocker
```

Post-merge synchronization issue #22 is bookkeeping only and changes no simulator or semantic code. Once #22 and umbrella #10 are closed, C2.5 representability is authorized.
