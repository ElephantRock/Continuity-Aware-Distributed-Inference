# 07 — C1 Deterministic Continuity Core
## Completion Candidate Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C1 — Deterministic Continuity Core  
**Prerequisite:** Gate G0 PASS after C0.1 normalization  
**Status:** Completion candidate; implementation exit criteria satisfied, pending final PR review and merge

---

# 1. Purpose

C1 establishes the provider-neutral semantic kernel independently of timing, queueing, networking, accelerator performance, or deployment infrastructure.

The milestone is intended to answer one question before C2 begins:

> Can the Continuity semantics be represented, transitioned, replayed, and adversarially tested as deterministic logical state while preserving the canonical invariants?

C1 does not attempt to model performance.

---

# 2. Implemented Semantic Surface

The deterministic kernel implements:

```text
Program
Session
Continuation DAG
LogicalRequest
Attempt
Phase
ExecutionContext
ReusableState
StateReplica
Binding
Evidence
Output
SemanticEvent
SemanticOperation
```

with the normalized dimensions:

```text
Attempt.ExecutionStatus
Attempt.AttemptAuthority
CurrentAttempt
CommittedAttempt

PhaseType
PhaseStatus
per-Attempt Phase ordinal

StateLifecycle
StateValidity
producer Attempt
producer Phase
State derivation graph

BindingID
base_epoch
epoch

EvidenceAuthority
EvidenceStatus
scope
freshness bounds
DERIVED Evidence provenance

SemanticEvent identity
SemanticOperation identity
```

---

# 3. Execution Continuity

## Attempt fencing

- at most one `CURRENT` Attempt authority exists per nonterminal LogicalRequest;
- starting a newer Attempt supersedes the older current Attempt;
- execution outcome and semantic authority are independent;
- a superseded Attempt may physically succeed without regaining authority;
- finalization requires the exact `CURRENT` Attempt at the semantic commit point;
- successful finalization atomically records `CommittedAttempt`, clears `CurrentAttempt`, and marks the winning Attempt `COMMITTED`;
- completed request output is immutable.

## Attempt generations

Attempt generations are monotonically and contiguously allocated per LogicalRequest and independently checked by the invariant oracle.

## Attempt execution status

Attempt execution status is monotonic across the nonterminal path:

```text
CREATED → DISPATCHED → RUNNING
```

A nonterminal status cannot move backward, and terminal outcomes:

```text
SUCCEEDED
FAILED
CANCELLED
```

are immutable once established.

---

# 4. Phase Semantics and State Production Ordering

C1 models Phase identity where causal ordering of reusable State requires it.

Paper 1 Phase types are:

```text
PREFILL
DECODE
STATE_PULL
STATE_TRANSFER
OTHER
```

External tool waits remain Continuation lifecycle events and are not represented as normative Attempt Phases.

For each Attempt:

```text
Phase ordinal = 1, 2, 3, ...
```

and Phase ordinals are contiguous and monotonic.

Phase status follows the monotonic nonterminal path:

```text
CREATED → RUNNING
```

and cannot regress from `RUNNING` to `CREATED`; terminal Phase outcomes are immutable.

A Phase belonging to a superseded Attempt cannot transition to `COMPLETED`. This fences delayed Phase completion from mutating authoritative semantic state after supersession.

Phase-origin State requires a `COMPLETED` producer Phase, and neither Attempt-origin nor Phase-origin State may be newly produced by a superseded Attempt.

Within the same Attempt, consuming State produced by a Phase requires a strictly later consumer Phase.

A Phase-origin State also cannot derive from State produced by the same or a later Phase of that Attempt.

This prevents temporal inversion from being hidden inside State provenance.

---

# 5. State Compatibility and Provenance

State compatibility is evaluated against an exact `ExecutionContext` and requires:

```text
coherent Program/Session/Continuation/Request/Attempt/Phase identity
+
Continuation ancestry
+
producer-Attempt authority where applicable
+
producer-Phase ordering where applicable
+
StateValidity = VALID
+
recursive derived-State validity
+
representation-specific SemanticValidity
```

The kernel rejects:

- sibling-branch State;
- State produced by a superseded Attempt;
- derived State that launders incompatible branch or producer provenance;
- same-Attempt State consumed before or at its producer Phase;
- State derivations that invert same-Attempt Phase order;
- invalid logical State;
- request-origin State before the LogicalRequest has completed and established a `CommittedAttempt`.

For Paper 1 C1, request-origin State is explicitly post-commit: its producer is exactly `CommittedAttempt(request)`. This removes temporal ambiguity from request-origin provenance.

Logical State identity remains distinct from physical replicas.

Loss of all replicas does not erase logical provenance.

---

# 6. State Lifecycle

State lifecycle is derived from known compatible live descendant Continuations:

```text
ACTIVE
WAITING
SPECULATIVE
TERMINAL
```

Logical validity remains independent:

```text
VALID
INVALID
```

Adding a new live descendant refreshes relevant ancestor-State lifecycle.

`INVALID` State cannot be resurrected under the same logical StateID.

Terminal and abandoned Continuations cannot be reactivated.

---

# 7. Binding and Migration Fencing

Migration candidates carry:

```text
BindingID
base_epoch
epoch
```

Candidate epochs are unique and monotonically allocated per subject.

A migration commit requires:

```text
candidate.base_epoch == CurrentEpoch(subject)
+
exact candidate BindingID
+
exact candidate epoch
+
sufficient destination Evidence
```

Physical transfer or candidate creation does not independently change ownership.

Once one candidate commits, competing candidates based on the previous epoch lose commit eligibility.

Delayed observations referring to an older Binding cannot restore old semantic ownership.

---

# 8. Evidence and Fail-Closed Reconciliation

Runtime Evidence separates:

## Authority

```text
ESTIMATED
DERIVED
EXACT_OBSERVATION
AUTHORITATIVE
```

## Status

```text
VALID
STALE
UNKNOWN
FAILED
AMBIGUOUS
```

Authority, status, scope, and freshness are evaluated independently.

Correctness-sensitive operations require action-specific minimum authority and exact semantic scope.

Missing or insufficient Evidence fails closed.

Ambiguous Evidence produces an explicit `AMBIGUOUS` outcome rather than semantic success.

## DERIVED Evidence provenance

`DERIVED` Evidence must contain:

```text
support Evidence IDs
+
explicit derivation rule
```

C1 rejects:

- DERIVED Evidence without explicit support;
- derivations referencing unknown Evidence;
- a `VALID` derivation from non-VALID support;
- derived scope exceeding the union of support scope;
- silent promotion of derived provenance to stronger authority.

Paper 1 C1 therefore has no implicit Evidence-authority escalation policy.

## Output Evidence references

An `Output` may only be created with Evidence IDs that already resolve in the semantic Evidence store.

C1 does not model pending/unresolved Output Evidence references. A missing Evidence reference is rejected at `create_output()` so the public transition API cannot create a state that violates the independent Output-to-Evidence integrity invariant.

---

# 9. Semantic Event Identity and Idempotence

`SemanticEvent` is an immutable logical event record with stable EventID.

The core maintains:

```text
events: EventID → SemanticEvent
event_order: first-delivery EventID order
```

Rules:

- identical duplicate delivery of the same EventID is idempotent;
- conflicting reuse of an EventID is a semantic violation;
- EventIDs participate in global semantic identity uniqueness;
- first-delivery order is deterministic;
- duplicate Evidence delivery is likewise idempotent when byte-for-byte semantically identical;
- conflicting reuse of an EvidenceID is rejected.

Observation order never establishes semantic authority by itself.

---

# 10. Deterministic Snapshots

C1 defines canonical full-core snapshots using:

```text
schema = cadi.core.snapshot.v1
```

Snapshots include all semantic state required to restore the deterministic kernel, including:

```text
Programs
Sessions
Continuations
Requests
Attempts
Phases
States
Replicas
Bindings
Evidence
Outputs
SemanticEvents
event first-delivery order
Binding epoch directories
```

Serialization is deterministic and canonical. Canonical JSON serialization rejects non-finite floating-point values (`NaN`, `+Infinity`, `-Infinity`) rather than emitting non-standard JSON tokens.

A SHA-256 snapshot fingerprint is available for semantic-state equivalence checks.

Snapshot restore must reproduce the same canonical snapshot and satisfy the independent invariant oracle. `restore_core` validates the independent oracle before returning a live core and rejects invariant-invalid persisted state.

External representation-specific `SemanticValidity` callables are intentionally not serialized and must be supplied again by the embedding adapter when required.

---

# 11. Machine-Readable Trace Formats and Replay

C1 uses two distinct trace concepts.

## Semantic Event trace

```text
schema = cadi.semantic-event.v1
format = canonical JSONL
```

This preserves immutable event delivery records and first-delivery order.

Replaying an Event trace reconstructs the deterministic Event log with duplicate-event idempotence.

## Semantic Operation trace

```text
schema = cadi.semantic-operation.v1
format = canonical JSONL
```

A `SemanticOperation` represents one whitelisted state-changing ContinuityCore invocation.

Operation records contain:

```text
operation_id
action
canonical arguments
```

Replay only dispatches explicitly whitelisted semantic actions.

Duplicate operation identities within a trace are rejected rather than treated as delivery duplicates.

Time-sensitive replay actions are deterministic by construction: replayed `finalize_request` and `commit_migration` operations must carry an explicit `now` argument. Replay rejects those actions when replay time is omitted rather than falling back to wall-clock time.

Two fresh cores replaying the same valid operation trace must therefore produce identical canonical snapshots and identical snapshot fingerprints independently of when replay occurs. Event and Operation JSONL use the same strict finite-number canonical JSON rule. Snapshot, Event JSONL, and Operation JSONL ingress likewise reject non-finite JSON constants before decoding or replay.

This distinction keeps:

```text
observation/event delivery
```

separate from:

```text
semantic state-changing intent
```

while providing deterministic reconstruction for C1.

---

# 12. Independent Invariant Oracle

The invariant oracle is intentionally separate from transition-time checks.

It traverses stored semantic state and checks at least:

```text
functional parent-scope consistency
single CurrentAttempt authority
single CommittedAttempt
Attempt generation monotonicity
completed-request finalization postcondition consistency
global logical-ID uniqueness
Continuation DAG acyclicity
Phase ownership and ordinal ordering
State provenance graph acyclicity
State producer Attempt/Phase consistency
declared State origin re-resolution and cached-provenance consistency
same-Attempt Phase dependency temporal ordering
Replica → logical State integrity
single current Binding consistency
Binding epoch uniqueness and allocation consistency
DERIVED Evidence provenance
SemanticEvent identity/log consistency
Output → Attempt/Evidence integrity
invalid-State incompatibility
```

The test suite also deliberately corrupts otherwise unreachable internal states and verifies that the oracle detects:

- Continuation cycles;
- cross-request Attempt authority;
- non-monotonic Attempt generation;
- State derivation cycles;
- broken Replica parentage;
- Phase-provenance temporal inversion inside a State derivation;
- mismatch between a declared State origin and its cached producer provenance.

The oracle uses non-strippable runtime checks rather than Python `assert`, so validation remains active under `python -O`. This prevents the oracle from being merely a mirror of public transition guards.

---

# 13. Mandatory Failure Model Traces

The complete deterministic FTR1–FTR12 corpus is executable:

```text
FTR1  late stale completion
FTR2  duplicate result
FTR3  reordered retry events
FTR4  wrong sibling State
FTR5  total physical State loss
FTR6  partial migration
FTR7  destination failure before migration commit
FTR8  late old-Binding observation
FTR9  ambiguous ownership
FTR10 stale high-authority Evidence
FTR11 tool-wait eviction
FTR12 abandoned-branch residual State
```

The traces distinguish semantic correctness from availability/performance degradation.

Examples:

- a lost valid State may require later recomputation but does not lose provenance;
- a partial migration remains uncommitted;
- a late observation may be stored without regaining authority;
- a tool-wait eviction preserves lineage even though warm physical State is unavailable.

---

# 14. Adversarial Deterministic Corpus

C1 includes both seeded fuzzing and an explicit adversarial matrix.

The deterministic matrix covers:

```text
retry-event delivery permutations
competing migration-candidate winner permutations
EvidenceAuthority × EvidenceStatus cross product
branch ancestry State-compatibility matrix
delayed/stale Evidence arrival sequences
```

The randomized-but-reproducible sequence test remains seeded and checks the independent oracle after every accepted mutation.

These are complementary:

```text
explicit adversarial cases
+
seeded sequence exploration
```

rather than relying on stochastic testing alone.

---

# 15. Invariant-to-Test Coverage Contract

C1 introduces:

```text
spec/invariant-coverage.json
schema = cadi.invariant-coverage.v1
```

The registry covers all 38 canonical invariant IDs:

```text
A1–A4
B1–B7
C1–C7
D1–D5
E1–E7
F1–F8
```

Every invariant maps to at least one named pytest function.

A meta-test verifies that:

- the registry IDs are derived from and must exactly match the canonical invariant headings in `spec/03-invariants.md`;
- every entry has a non-empty title;
- every entry maps to one or more test node IDs;
- every referenced test file exists;
- every referenced pytest function still exists;
- mappings remain inside the test tree.

Therefore invariant coverage cannot silently become stale as tests are renamed or removed.

---

# 16. Validation Result

Permanent GitHub Actions CI runs the complete deterministic suite on:

```text
Python 3.11
Python 3.12
Python 3.13
```

Latest completion-candidate result after all review fixes:

```text
157 passed
```

The final review-regression set verifies that:

- Attempt execution status cannot regress through its nonterminal state machine;
- Phase status cannot regress through its nonterminal state machine;
- time-sensitive operation replay rejects omitted replay time;
- replayed finalization remains deterministic across different wall-clock times;
- replayed migration commit remains deterministic across different wall-clock times;
- the independent oracle rejects corrupted same-Attempt Phase-provenance temporal inversion;
- Output creation rejects unresolved Evidence references instead of storing oracle-invalid state;
- the independent oracle rejects a Phase-origin State whose declared `origin_id` disagrees with its cached producer Phase;
- request-origin State is rejected before request completion, and the oracle rejects a request-origin State whose cached producer differs from `CommittedAttempt(request)`;
- a superseded Attempt cannot authoritatively complete a Phase or produce new Attempt-/Phase-origin State;
- canonical snapshots and Operation JSONL reject `NaN`, `+Infinity`, and `-Infinity`;
- snapshot, Event JSONL, and Operation JSONL parsers reject externally supplied non-finite constants;
- `restore_core` rejects snapshots that violate the independent invariant oracle before exposing a live core;
- snapshot restoration rejects wrong collection/member/enum/scalar types before installing state;
- completed requests are rechecked against committed-Attempt success/authority and terminal authoritative Output;
- global logical-ID uniqueness is revalidated across all entity collections;
- restore validation remains active under `python -O`;
- strict JSON parsing rejects numeric exponent overflow that would become ±infinity;
- Operation JSONL arguments are validated against whitelisted `ContinuityCore` signatures before construction, serialization, and dispatch;
- nested semantic-operation dataclass, enum, scalar, finite-float, and typed-container values are recursively validated;
- malformed directly constructed `SemanticOperation` objects, duplicate argument names, one-shot iterator arguments, and mutable sets are rejected before canonical emission or mutation.
- `FAILED` and `CANCELLED` requests are fenced from finalization, and restore rejects such terminal requests when stale current/committed/output authority remains;
- the invariant-coverage registry includes canonical F8 and derives its expected ID set directly from the invariant catalogue rather than a hard-coded range.

The full suite includes:

- unit-level transition tests;
- independent invariant-oracle tests;
- deliberate corruption detection;
- mandatory Failure Model counterexamples;
- deterministic adversarial parameter matrices;
- event idempotence tests;
- Phase-order and Phase-status monotonicity tests;
- Attempt execution-status monotonicity tests;
- DERIVED Evidence provenance tests;
- Output/Evidence referential-integrity tests;
- canonical snapshot round trips;
- Event JSONL round trips;
- Operation JSONL deterministic replay;
- replay-clock determinism tests;
- seeded operation-sequence fuzzing;
- invariant-registry self-validation.

---

# 17. C1 Exit-Criteria Status

The eight planned C1 exit artifacts are now implemented:

```text
[complete] broader mandatory Failure Model trace coverage
[complete] expanded adversarial deterministic sequence corpus
[complete] explicit event identity/idempotence model
[complete] Phase entities and State-production ordering
[complete] expanded independent invariant oracle
[complete] deterministic snapshots and semantic-operation replay
[complete] machine-readable Event and Operation trace formats
[complete] all 38 canonical invariants mapped to named executable tests
```

Implementation exit criteria are therefore satisfied.

Codex review identified fifteen correctness inconsistencies during closure:

1. wall-clock-dependent time-sensitive operation replay;
2. missing independent Phase-dependency ordering validation in restored/corrupted State provenance;
3. public Output creation accepting unresolved Evidence references while the oracle rejected them;
4. restored/corrupted State could declare one Phase origin while carrying cached producer provenance from another Phase;
5. request-origin State producer identity was ambiguous unless request completion was made a precondition;
6. delayed Phase completion after Attempt supersession could still mutate semantic state and enable new State production;
7. canonical JSON/JSONL serialization could emit non-standard `NaN`/`Infinity` tokens;
8. external JSON/JSONL parsing accepted non-finite constants that could bypass freshness semantics;
9. snapshot restoration could expose invariant-invalid persisted state without running the independent oracle;
10. restored completed requests were not rechecked against finalization execution/output postconditions;
11. decoded snapshot collection members and internal enum/scalar types were not schema-validated;
12. restore validation depended on Python `assert` and could disappear under `python -O`;
13. snapshot restore did not enforce the core global logical-ID namespace across entity collections;
14. external semantic-operation traces could inject wrongly typed decoded arguments, including forged Evidence authority, because replay did not validate action argument/entity types;
15. typed `Iterable[...]` operation arguments admitted mutable `set` values that the canonical Operation encoder could not serialize.

All fifteen are fixed and covered by regression tests.

An independent exact-head semantic audit was performed after the GitHub Codex integration reached its code-review usage limit and could not execute the requested final exact-head pass. That audit identified two additional closure inconsistencies:

1. `finalize_request()` fenced `COMPLETED` requests but did not explicitly reject restored `FAILED` or `CANCELLED` requests, contrary to the Safe Finalization predicate requiring a nonterminal request at commit;
2. the machine-readable coverage registry and its meta-test stopped at F7 even though the canonical invariant catalogue contains F8, allowing the registry and test to agree while drifting from the specification.

Both independent findings are fixed and covered by regression tests. The registry now derives its canonical ID set from `spec/03-invariants.md`, and the bounded independent review found no further blocking contradiction between the C1 kernel and the canonical A–F safety catalogue.

The unavailable Codex exact-head rerun is recorded as a tooling/quota limitation, not as a successful Codex review. C1 closure therefore relies on the completed independent exact-head semantic audit plus permanent exact-head CI.

Formal milestone closure still requires:

```text
independent exact-head semantic review with no unresolved blocking finding
clean permanent CI on that exact head
resolution of all review threads
merge of the C1 completion PR to main
```

C2 must not begin before those closure steps are complete.

---

# 18. Deliberately Not Implemented in C1

C1 intentionally excludes:

```text
queueing simulation
network timing
worker performance
accelerator cost models
public trace ingestion
routing optimization
retention optimization
migration optimization
Gateway API integration
real distributed process execution
```

Those belong to C2 and later milestones.

---

# 19. C1 Closure Decision Rule

C1 closes only when the completion PR is merged after clean review and CI.

At that point the deterministic kernel becomes the semantic reference implementation for C2.

C2 may then add time, queues, failures, resources, and simulated costs around the C1 semantics, but must not silently redefine them.
