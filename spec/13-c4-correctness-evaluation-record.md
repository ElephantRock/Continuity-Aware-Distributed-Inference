# 13 — C4 Correctness Evaluation
## C4.1 Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C4 — Correctness Evaluation / Gate G1  
**Prerequisite:** C3 CLOSED at `950dbb2303a49482e27ee09468717102eec8b0f0`  
**Tracking:** #44 / #45 / PR #46  
**Status:** IN PROGRESS — implementation/test gate green; final exact-head review pending

---

# 1. Purpose and boundary

C4 evaluates the closed C1/C2/C3 stack. It does not redefine semantic authority, fault semantics, or policy information privileges.

Every evaluated operation keeps four views separate:

```text
ground_truth
observed_evidence
policy_decision
semantic_result
```

`ground_truth` is an independent experimental oracle and is not automatically policy-visible.

C4.1 preserves these distinctions:

```text
ground truth != policy-visible observation
explicit non-success != silent semantic error
Gate-specific event rates != O1/O2/O3/O4 terminal outcome class
operation-level outcome denominator != Gate-event denominator
exogenous paired cohort != policy-derived denominator events
paired event cardinality != paired event identity
```

The measurement layer must not manufacture a correctness advantage by changing baseline capabilities, cohort membership, evidence strata, denominator cardinality, event identity, or zero-coverage semantics.

---

# 2. One record per complete operation

One `CorrectnessEvaluationRecord` represents one complete correctness-sensitive operation under one policy:

```text
cohort_id
trial_id
operation_id
policy_id
```

A multi-step path such as:

```text
WAIT -> RETRY -> COMMIT
```

remains one operation and one terminal O1/O2/O3/O4 row. Its ordered decisions live inside `policy_decision`.

Duplicate `(policy_id, cohort_id, trial_id, operation_id)` records are rejected before aggregation. This prevents scheduler or reconciliation decision count from inflating `TotalFaultedOperations`.

---

# 3. Gate events preserve cardinality and identity

The six Gate G1 safety rates are event-level ratios. A complete operation can contain multiple denominator events of the same metric, and those events must remain distinguishable.

Each Gate opportunity therefore carries three aligned fields:

```text
metric_opportunities
metric_opportunity_event_ids
metric_opportunity_scopes
```

Each Gate violation carries:

```text
metric_violations
metric_violation_event_ids
```

Event IDs are non-empty and unique within an operation. A violation is valid only when its `(metric, event_id)` exactly references a declared opportunity event.

Example:

```text
metric_opportunities          = [SAAR, SAAR, SAAR]
metric_opportunity_event_ids  = [stale:a1, stale:a2, stale:a3]
metric_violations             = [SAAR, SAAR]
metric_violation_event_ids    = [stale:a1, stale:a2]
```

This contributes `2 / 3` to SAAR while the operation still contributes one terminal O-class outcome.

Gate aggregation sums explicit event rows. It never collapses repeated events to a binary per-operation indicator.

---

# 4. Exogenous paired events versus policy-derived events

Paired B0–B4 evaluation requires the same exogenous operation cohort across compared policies. For the same `cohort_id + trial_id + operation_id`, all compared policies must share:

```text
scenario_id
fault_id
fault_class
ground_truth
```

Every included policy must have the same operation keys.

C4.1 also distinguishes Gate denominator events by causal source.

## 4.1 Exogenous paired Gate opportunities

These opportunities are fixed by the experiment/oracle and must therefore carry the same stable event identities across paired policies:

```text
SAAR  stale Attempt result presentations
WBRR  incompatible branch-reuse opportunities
SBDR  Binding-sensitive operations
ACR   ambiguous correctness-sensitive decisions
```

Machine scope:

```text
EXOGENOUS_PAIRED
```

Equal metric counts are insufficient. If B0 is evaluated on stale result `a1` and B4 on stale result `a2`, the cohort is rejected even though both have one SAAR opportunity. The same rule applies to ambiguity: if the oracle classifies a correctness-sensitive decision as ambiguous, every paired policy is evaluated against that same ambiguity event even if a simpler policy does not explicitly represent ambiguity internally.

This is required by the canonical ACR denominator:

```text
ambiguous correctness-sensitive decisions
```

A policy cannot erase an ACR opportunity merely by failing to detect or encode the ambiguity.

## 4.2 Policy-derived Gate opportunities

These denominators arise from actual policy execution and may legitimately differ:

```text
WSCR  actual State consumptions
DFR   completed LogicalRequests
```

Machine scope:

```text
POLICY_DERIVED
```

For example, if B0 consumes an incompatible State while B4 rejects it before consumption:

```text
B0 WSCR = 1 / 1
B4 WSCR = 0 / 0 = null
```

This is a valid paired comparison. Forcing a fictitious B4 consumption event would manufacture a `0 / 1` safety pass.

Thus paired fairness is:

```text
same exogenous workload/fault/ground truth
+ same stable identities for exogenous Gate opportunities, including ambiguity
+ policy-specific execution behavior
+ policy-specific behavior-derived denominator events
```

---

# 5. Evidence terminology

C4.1 keeps methodological validation level separate from result provenance.

Validation hierarchy:

```text
EV0  deterministic semantics
EV1  measured CPU distributed
EV2  trace-derived
EV3  calibrated simulation
EV4  optional accelerator measurement
```

Result provenance:

```text
MEASURED
SIMULATED
TRACE_DERIVED
SYNTHETICALLY_GENERATED
ANALYTICALLY_DERIVED
ESTIMATED
```

The machine-readable fields are `validation_level` and `evidence_provenance`. A `CorrectnessSummary` contains exactly one evidence stratum. Mixed strata are rejected and the stratum is serialized into the summary fingerprint.

Runtime C1 `Evidence` authority/status/freshness remains a separate semantic concept.

---

# 6. O1–O4 terminal outcomes

Every faulted operation is classified once:

```text
O1  Correct transparent recovery
O2  Correct degraded recovery
O3  Explicit non-success
O4  Silent semantic violation
```

Classification:

```text
reported success + correct + no recovery action
    -> O1

reported success + correct + RETRY/RECOMPUTE/MIGRATION/REPAIR
    -> O2

WAIT/FAIL/AMBIGUOUS/REJECT with no authoritative commit
    -> O3

reported success + incorrect authoritative commit
    -> O4
```

O1–O4 counts are over faulted operations only. Controls remain visible in total `operation_count` but do not enter failure-outcome counts.

---

# 7. Gate rates remain independent from O1–O4

Gate G1 metrics are:

```text
Stale Attempt Acceptance Rate      (SAAR)
Wrong-Branch Reuse Rate            (WBRR)
Wrong-State Consumption Rate       (WSCR)
Silent Binding Divergence Rate     (SBDR)
Ambiguous Commit Rate              (ACR)
Duplicate Finalization Rate        (DFR)
```

C4.1 also derives:

```text
Silent Semantic Error Rate (SSER)
Explicit Non-Success Rate
Recovery Rate
```

The six Gate metrics count specific unsafe events. O4 is narrower: a semantically incorrect committed success.

```text
Gate violation does not imply O4
O4 does not imply a named Gate violation
```

An operation may consume incompatible State, detect the problem, recompute, and finish correctly. That operation is O2 while still contributing a WSCR violation.

Gate metrics use:

```text
numerator   = total matching violation events
denominator = total matching opportunity events
rate        = numerator / denominator
```

Zero coverage is explicit:

```text
0 / 0 -> null
```

Operation-level aggregates use:

```text
SSER                   = O4 / TotalFaultedOperations
ExplicitNonSuccessRate = O3 / TotalFaultedOperations
RecoveryRate           = (O1 + O2) / TotalFaultedOperations
```

---

# 8. Fail-closed serialization

Schema:

```text
cadi.correctness-evaluation.v1
```

Canonical serialization emits the complete record field set. Deserialization requires that exact field set; missing or misspelled safety fields are rejected rather than defaulted.

Safety-relevant JSON parsing is fail-closed in three additional ways:

1. non-finite JSON constants are rejected;
2. duplicate object member names are rejected recursively before last-value-wins semantics can erase evidence;
3. serialized repeated fields must be actual JSON arrays before conversion, so strings such as `""` cannot collapse safety evidence into empty tuples.

The strict array rule applies to all five Gate event fields:

```text
metric_opportunities
metric_opportunity_event_ids
metric_opportunity_scopes
metric_violations
metric_violation_event_ids
```

and to `semantic_result.recovery_actions`.

For example, persisted JSON containing both a non-empty and a later empty `metric_violations` member is invalid rather than silently becoming safe. Likewise, replacing a real ACR event array with an empty string is invalid rather than becoming `0 / 0 = null`.

The record also snapshots caller-owned mappings into canonical JSON, validates serialized `outcome_class` against `semantic_result`, preserves explicit Gate event identities, and produces a stable SHA-256 fingerprint.

---

# 9. Deterministic aggregation

`summarize_correctness(...)`:

1. requires at least one operation record;
2. rejects non-record inputs;
3. rejects duplicate policy/operation identities;
4. requires one evidence stratum;
5. validates paired operation coverage and exogenous invariant metadata;
6. validates identical exogenous Gate opportunity event identities across paired policies;
7. leaves policy-derived Gate events policy-specific;
8. aggregates policies in canonical B0→B4 order.

Each policy summary reports:

```text
operation_count
faulted_operation_count
O1/O2/O3/O4 faulted-operation counts
metric numerator
metric denominator
metric rate
```

---

# 10. Baseline fairness and S1 boundary

C4.1 does not change the closed C3 information contract and does not remove ordinary correctness mechanisms from B0–B3.

A critical S1 rule is:

> placement admission is not authoritative result acceptance.

S1 must measure whether a stale Attempt result is accepted authoritatively. It must not count mere scheduling of stale physical work as stale authoritative acceptance.

If competent simpler baselines independently fence stale result acceptance through ordinary correctness mechanisms allowed by their abstraction, the hypothesis must be narrowed rather than those mechanisms being disabled. Negative or null results remain valid research outcomes.

---

# 11. C4 safety slices

```text
C4.1  measurement contract / independent oracle records
C4.2  S1 Attempt Fencing
C4.3  S2 State-Lineage Safety
C4.4  S3 Binding Safety
C4.5  S4 Evidence Safety
C4.6  S5 Idempotence / Ordering + Gate G1 closure
```

Canonical metric mapping:

```text
S1 -> SAAR, DFR
S2 -> WBRR, WSCR
S3 -> SBDR
S4 -> ACR, SSER, Explicit Non-Success Rate
S5 -> DFR, invariant violations, semantic-state equivalence
```

---

# 12. Bounded-review findings repaired before final review

Thirteen substantive measurement-integrity findings have been repaired:

1. **Validation/provenance conflation.** EV0–EV4 and result provenance were collapsed and `ESTIMATED` omitted. Fixed by orthogonal fields.
2. **Decision rows could inflate faulted-operation denominators.** Fixed by one record per complete operation.
3. **Paired policy cohorts could differ in exogenous operations or ground truth.** Fixed by exact operation coverage plus invariant scenario/fault/ground-truth checks.
4. **Controls contaminated O1–O4 counts.** Fixed by fault-only outcome accounting.
5. **Summary artifacts could hide mixed evidence strata.** Fixed by single-stratum summaries with evidence-sensitive fingerprints.
6. **Missing violation arrays could default to safe.** Fixed by exact-schema fail-closed deserialization.
7. **Gate-specific violations were incorrectly forced into O4.** Fixed by keeping named Gate event rates independent from terminal outcome class.
8. **Gate event cardinality was collapsed to binary per-operation presence.** Fixed by explicit event-level denominator/numerator records.
9. **Behavior-dependent Gate opportunities were incorrectly forced equal across paired policies.** Fixed by separating exogenous paired events from policy-derived events.
10. **Equal opportunity counts could hide different paired events.** Fixed by stable event identities and exact paired validation for exogenous Gate opportunities.
11. **Duplicate JSON object members could erase safety evidence.** Fixed by recursive duplicate-member rejection before deserialization.
12. **ACR ambiguity opportunities were misclassified as policy-derived.** Fixed by treating oracle-classified ambiguous correctness-sensitive decisions as paired exogenous events so a policy cannot suppress its ACR denominator by failing to recognize ambiguity.
13. **Malformed strings could masquerade as empty repeated fields.** Fixed by requiring serialized Gate event fields and recovery actions to be JSON arrays before any tuple/enum conversion.

The final candidate must retain regression coverage for all thirteen findings.

---

# 13. C4.1 test obligations

The regression suite covers, at minimum:

```text
O1/O2/O3/O4 deterministic classification
explicit non-success is not SSER
Gate metrics use explicit event-level opportunity denominators
multiple same-metric events preserve cardinality
violations reference exact declared opportunity event IDs
zero opportunity => null rate, not zero
controls do not inflate fault denominators or O counts
EV0-EV4 vocabulary is complete
result provenance includes ESTIMATED
validation level and provenance serialize independently
one operation cannot be split into multiple denominator rows
paired cohorts reject policy-specific exogenous operation subsets
paired cohorts reject mismatched scenario/fault/ground truth
same metric/count with different exogenous event IDs is rejected
ACR ambiguity events are paired exogenous events
paired ACR events with different identities are rejected
canonical Gate metric scope is enforced
policy-derived opportunity sets may differ across paired policies
B0 WSCR 1/1 versus B4 WSCR 0/0 is representable
mixed evidence strata are rejected
summary fingerprint preserves evidence stratum
missing or misspelled safety fields are rejected
non-array Gate event fields are rejected
non-array recovery_actions is rejected
duplicate top-level JSON members are rejected
duplicate nested JSON members are rejected
Gate metric violations do not imply O4
O2 can carry a Gate violation while SSER remains zero
O4 can exist without named Gate attribution
canonical fingerprints ignore mapping insertion order
record construction snapshots caller-owned mappings
record JSON round-trips with strict schema checking
tampered outcome classification is rejected
Gate opportunities require a faulted operation
non-finite record data is rejected
single-policy pilot summaries remain possible
paired summaries use canonical policy order
```

All pre-existing C1/C2/C3 tests remain regression obligations.

Behavior-bearing candidate before this documentation synchronization:

`1fa0b1df99c6b4001c9eb2f50a8c0ac921e51930`

Exact candidate matrix:

```text
Python 3.11  365 passed
Python 3.12  365 passed
Python 3.13  365 passed
```

The documentation-synchronized exact PR head must pass the same full matrix before final review/merge.

---

# 14. Gate G1 boundary

For B4 under covered modeled failures, Gate G1 targets:

```text
SAAR = 0
WBRR = 0
WSCR = 0
SBDR = 0
ACR = 0
DFR = 0
```

Gate G1 does not require zero explicit failure or zero recomputation. Safety and availability remain separate.

C4 does not close merely because B4 reaches zero. The gate also requires a defensible correctness distinction from simpler competent abstractions. If that distinction is absent for a claimed failure class, the claim must be narrowed.

---

# 15. C4.1 exit criterion

C4.1 closes only when:

1. `cadi.correctness-evaluation.v1` is merged;
2. independent ground truth remains distinct from policy-visible observation;
3. O1–O4 denominators are complete faulted operations, not decision rows;
4. Gate-specific denominators preserve event cardinality and stable event identity;
5. paired exogenous cohorts and exogenous Gate event identities are integrity-checked before comparison;
6. behavior-derived denominator events remain policy-specific and are not fabricated for comparability;
7. evidence strata are explicit and cannot be silently mixed;
8. O1–O4 and Gate metrics remain semantically independent;
9. zero coverage is distinguishable from zero violations;
10. persisted safety fields fail closed on omission, duplicate members, malformed schema, malformed repeated-field types, and non-finite data;
11. deterministic serialization/fingerprints are regression-tested;
12. the full repository suite passes on Python 3.11, 3.12, and 3.13;
13. a final exact-head bounded review finds no remaining rule capable of manufacturing a correctness advantage.

After C4.1 closes, C4.2 may begin S1 Attempt Fencing against this fixed measurement contract.
