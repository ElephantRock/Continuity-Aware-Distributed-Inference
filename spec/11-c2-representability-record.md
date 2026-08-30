# 11 — C2.5 Workload and Failure Representability
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C2.5 — Workload/Failure Representability and C2 Closure  
**Prerequisite:** C2.4 CLOSED on `main` through `5092e92cd455d8188f0dc46043c2cc6c970ce9eb`  
**Tracking:** #11  
**Status:** **CLOSED** — PR #30 squash-merged as `462e63685500e022a97386618ba606f1384d1c56`

---

# 1. Purpose

C2.5 closes the C2 representational requirement without introducing C3 scheduling policy.

It establishes that the deterministic simulator can encode the canonical workload families and mandatory Failure Model traces as reproducible schedules while preserving the architectural rule:

> **C2 represents when physical/observational facts occur; C1 remains the semantic authority.**

C2.5 therefore does not implement B0–B4, choose placement/recovery policy, or add a second implementation of State compatibility, Attempt authority, Binding authority, Evidence sufficiency, or reconciliation.

---

# 2. Canonical Catalogue

The representability registry covers exactly:

```text
W1–W10
FTR1–FTR14
```

Each entry carries both:

```text
stable semantic scenario name
canonical catalogue ID
```

The stable semantic name is the durable programmatic key. The catalogue ID preserves traceability to `spec/05-experimental-plan.md` and `spec/04-failure-model.md`.

This distinction prevents implementation identity from depending on historical numbering.

---

# 3. Failure-Trace Numbering Normalization

The current normalized Failure Model defines FTR1–FTR14.

An older C1 counterexample file still names twelve tests according to the pre-normalization catalogue. C2.5 does not rewrite those historical test names. Instead, `C1_FAILURE_REFERENCES` maps each current canonical FTR number to its authoritative C1 semantic test.

The important normalized additions are:

```text
FTR5   superseded-producer State
FTR10  losing concurrent migration candidate
FTR13  tool-wait eviction
FTR14  abandoned-branch residual State
```

Their current C1 semantic locations are explicit in the registry. In particular, FTR13/FTR14 map to the older counterexample names `test_ftr11_*` and `test_ftr12_*`; FTR5 and FTR10 map to invariant tests outside that file.

The C2.5 test suite additionally resolves every configured `path::test_function` reference against the repository so stale provenance cannot silently pass as an opaque string.

---

# 4. Scenario Schedule Contract

`simulator/scenarios.py` defines immutable:

```text
ScenarioDefinition
ScenarioStep
ScenarioSchedule
ScenarioEvent
```

A generated schedule contains:

```text
schema
stable semantic name
canonical catalogue ID
scenario family
seed
ordered simulator events
```

Events remain ordinary C2 `EventKind` values with frozen canonical payloads.

The registry introduces no semantic entity identity beyond the C0/C1/C2 model.

---

# 5. Determinism and Fingerprints

Every schedule is deterministically generated from:

```text
scenario definition
+
integer seed
```

Seed-dependent sub-microtime jitter is derived from SHA-256 rather than host/Python hash state. It exists only to make seed participation explicit while preserving the semantic ordering encoded by each scenario template.

The schedule fingerprint is SHA-256 over canonical JSON containing:

```text
schema
stable name
catalogue ID
family
seed
events
```

Executed simulator traces receive a separate canonical SHA-256 fingerprint that includes:

```text
time
insertion sequence
EventID
EventKind
payload
```

Same scenario + same seed reproduces both fingerprints exactly.

Different seeds produce different schedule fingerprints.

---

# 6. C1↔C2 Semantic Equivalence Boundary

Not every representable FTR should be reimplemented semantically inside the C2 adapter.

The existing `ContinuityAdapter` intentionally handles request/Attempt/finalization observations through public C1 APIs. Therefore C2.5 executes authoritative C1↔C2 equivalence assertions for the canonical traces with direct support at that adapter boundary:

```text
FTR1  late stale completion
FTR2  duplicate result
FTR3  reordered retry events
```

For these traces, tests construct the same semantic result directly in C1 and through the timed C2 schedule, then compare `AuthoritativeOutcome` including:

```text
request status
CurrentAttempt
CommittedAttempt
authoritative Output
authoritative Evidence IDs
Attempt generation/execution/authority projections
```

The remaining FTRs still have explicit C1 authoritative semantic references, but C2.5 does not add State/Binding/lifecycle mutation handlers merely to execute them a second time. Their C2 requirement is deterministic schedule representability; their semantic obligation remains enforced by C1.

This is a deliberate non-redefinition boundary, not missing coverage.

---

# 7. Workload Representability

The ten registered workload scenarios are:

```text
W1   independent-requests
W2   deep-stateful-session
W3   tool-gap-resume
W4   retry-race
W5   stateful-failover
W6   branching-program
W7   fanout-fanin
W8   cache-pressure
W9   stale-evidence
W10  state-migration
```

These schedules encode the semantic/physical structures required for later experiments without selecting a policy response.

---

# 8. Failure Representability

The fourteen registered failure scenarios are:

```text
FTR1   late-stale-completion
FTR2   duplicate-result
FTR3   reordered-retry-events
FTR4   wrong-sibling-state
FTR5   superseded-producer-state
FTR6   lost-valid-state
FTR7   partial-migration
FTR8   destination-crash-before-commit
FTR9   late-old-binding-event
FTR10  losing-concurrent-migration-candidate
FTR11  ambiguous-ownership
FTR12  stale-high-authority-evidence
FTR13  tool-wait-eviction
FTR14  abandoned-branch-residual-state
```

Every scenario uses only the merged C2 event vocabulary.

---

# 9. Test Obligations

`tests/simulator/test_representability.py` requires:

```text
exact W1–W10 coverage
exact FTR1–FTR14 coverage
unique stable semantic names
canonical catalogue lookup
monotonic unique scheduled events
stable 64-hex schedule fingerprints
same-seed exact schedule replay
same-seed exact executed-trace replay
explicit normalized C1 reference mapping
all C1 references resolve to declared test functions
C1↔C2 authoritative equivalence for FTR1–FTR3
FTR2 duplicate delivery is accepted idempotently rather than rejected as conflicting Evidence
no semantic-equivalence claims beyond the adapter-supported boundary
```

The full repository suite is the closure test because C2.5 must not regress C1 invariants, C2 resources, semantic adapter behavior, or C2.4 fault instrumentation.

---

# 10. Bounded Closure Review

The initial PR head passed CI but was not accepted solely because the outcome-level equivalence test could mask a malformed duplicate delivery.

The review found one substantive defect:

```text
FTR2 first observation:
    EvidenceID = e1
    observed_at = generated delivery time

FTR2 duplicate observation:
    EvidenceID = e1
    observed_at = 3.0
```

Because Evidence identity is immutable, the second delivery could be rejected as a conflicting object with the same EvidenceID while the final request outcome still matched the direct C1 reference.

The fix made both deliveries carry the exact same observation identity and added a regression assertion that the duplicate adapter record is not `REJECTED`.

The review also strengthened provenance validation so all fourteen normalized C1 references must resolve to real test functions.

No State/Binding semantic handlers were added as part of the fix.

---

# 11. Exact-Head Closure Evidence

Final reviewed PR head:

```text
a03ab77afd481005521e1cecacc5f25228ff1e29
```

Full repository validation on that exact head:

```text
Python 3.11  PASS
Python 3.12  PASS
Python 3.13  PASS
278 tests
```

PR #30 was SHA-fenced to that head and squash-merged to `main` as:

```text
462e63685500e022a97386618ba606f1384d1c56
```

Issue #11 closes through the merged PR.

Therefore:

```text
C2.5  CLOSED
C2    CLOSED
```

---

# 12. Scope Boundary After C2

C2.5 does not implement or compare:

```text
B0 request-centric
B1 cache-aware
B2 session-affinity
B3 state-aware
B4 continuity-aware
```

Those policies begin at C3 and must consume identical C2 workload/fault schedules rather than embedding policy choices into representability.
