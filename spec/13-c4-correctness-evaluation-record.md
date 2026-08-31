# 13 — C4 Correctness Evaluation
## C4.1 Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C4 — Correctness Evaluation / Gate G1  
**Prerequisite:** C3 CLOSED at `950dbb2303a49482e27ee09468717102eec8b0f0`  
**Tracking:** #44 / #45 / PR #46  
**Status:** IN PROGRESS — C4.1 final review candidate

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
```

The measurement layer must not manufacture a correctness advantage by changing baseline capabilities, cohort membership, evidence strata, denominator cardinality, or zero-coverage semantics.

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

Duplicate:

```text
(policy_id, cohort_id, trial_id, operation_id)
```

records are rejected before aggregation.

This prevents scheduler or reconciliation decision count from inflating the Failure Model's `TotalFaultedOperations` denominator.

---

# 3. Gate metrics preserve event cardinality

The six Gate G1 safety rates are event-level ratios. A complete operation can contain more than one denominator event of the same metric.

C4.1 therefore treats:

```text
metric_opportunities
metric_violations
```

as canonical multisets of Gate metric identifiers. Repeated identifiers preserve event cardinality.

Example:

```text
metric_opportunities = [SAAR, SAAR, SAAR]
metric_violations    = [SAAR, SAAR]
```

means three stale-Attempt-result presentation events occurred inside one complete operation and two were accepted authoritatively. Its SAAR contribution is `2 / 3`, while the operation contributes only one terminal O-class outcome.

For every Gate metric:

```text
violation_event_count <= opportunity_event_count
```

is enforced.

Gate aggregation sums event counts across records. It does not reduce repeated events to a binary per-operation indicator.

---

# 4. Exogenous paired cohorts versus behavior-dependent opportunities

Paired B0–B4 evaluation requires the same **exogenous operation cohort** across compared policies.

For one `cohort_id + trial_id + operation_id`, the following must match across policies:

```text
scenario_id
fault_id
fault_class
ground_truth
```

Every included policy must have the same operation keys.

The following are policy-specific and may differ:

```text
observed_evidence
policy_decision
semantic_result
metric_opportunities
metric_violations
```

This distinction is necessary because some canonical Gate denominators depend on policy behavior. For example:

```text
WSCR denominator = State consumptions
DFR denominator  = completed LogicalRequests
```

If B0 consumes a presented incompatible State while B4 rejects it before consumption, truthful records are:

```text
B0 WSCR opportunities = 1
B4 WSCR opportunities = 0
```

Forcing those opportunity counts equal would either reject a valid paired comparison or fabricate a denominator event for B4, turning genuine no-coverage `0/0 = null` into a fictitious `0/1 = 0` safety pass.

Therefore paired fairness means:

```text
same exogenous workload/fault/ground truth
+ policy-specific observed behavior
+ policy-specific denominator events derived from that behavior
```

not identical post-decision event counts.

A single-policy summary remains valid for implementation checks or pilot evidence, but it is not itself a paired B0–B4 comparison.

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

The machine-readable fields are:

```text
validation_level
evidence_provenance
```

A `CorrectnessSummary` contains exactly one `(validation_level, evidence_provenance)` stratum. Mixed strata are rejected, and both dimensions are serialized into the summary fingerprint.

Runtime C1 `Evidence` authority/status/freshness is a distinct semantic concept.

---

# 6. O1–O4 terminal outcomes

Every faulted operation is classified once:

```text
O1  Correct transparent recovery
O2  Correct degraded recovery
O3  Explicit non-success
O4  Silent semantic violation
```

`SemanticResult` records:

```text
reported_success
authoritative_commit
semantically_correct
explicit_non_success
recovery_actions
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

# 7. Gate-specific rates are independent from O1–O4

Gate G1 uses:

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

Thus:

```text
Gate violation does not imply O4
O4 does not imply a named Gate violation
```

An operation may consume incompatible State, detect the problem, recompute, and complete correctly. It is O2 while still contributing a WSCR violation. Conversely an O4 semantic violation may exist without attribution to one of the six named Gate metrics.

For Gate metrics:

```text
numerator   = total matching violation events
denominator = total matching opportunity events
rate        = numerator / denominator
```

If no opportunity event was evaluated:

```text
numerator   = 0
denominator = 0
rate        = null
```

For operation-level aggregate outcomes:

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

In particular, omission of `metric_violations` cannot become an empty safe result.

The record also:

- snapshots caller-owned ground truth / observation / decision mappings into canonical JSON;
- rejects non-finite numeric data;
- validates serialized `outcome_class` against `semantic_result`;
- canonicalizes Gate-event multisets while preserving multiplicity;
- produces a stable SHA-256 fingerprint.

---

# 9. Deterministic aggregation

`summarize_correctness(...)`:

1. requires at least one operation record;
2. rejects non-record inputs;
3. rejects duplicate policy/operation identities;
4. requires one evidence stratum;
5. validates paired operation coverage and exogenous invariant metadata;
6. leaves behavior-derived Gate opportunity/violation events policy-specific;
7. aggregates policies in canonical B0→B4 order.

Each policy summary reports:

```text
operation_count
faulted_operation_count
O1/O2/O3/O4 faulted-operation counts
metric numerator
metric denominator
metric rate
```

The top-level summary records `validation_level` and `evidence_provenance`.

---

# 10. Baseline fairness and S1 boundary

C4.1 does not change the closed C3 information contract and does not remove ordinary correctness mechanisms from B0–B3.

A critical S1 rule is:

> placement admission is not authoritative result acceptance.

S1 must measure whether a stale Attempt result is accepted authoritatively. It must not count mere scheduling of stale physical work as stale authoritative acceptance.

If competent simpler baselines independently fence stale result acceptance through ordinary correctness mechanisms allowed by their abstraction, the hypothesis must be narrowed rather than those mechanisms being disabled.

Negative or null results remain valid research outcomes.

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

# 12. Bounded-review findings resolved before merge

Nine substantive measurement-integrity findings were identified:

1. **Validation/provenance conflation.** EV0–EV4 and result provenance were collapsed and `ESTIMATED` omitted. Fixed by orthogonal fields.
2. **Decision rows could inflate faulted-operation denominators.** Fixed by one record per complete operation.
3. **Paired policy cohorts could differ in exogenous operations or ground truth.** Fixed by exact operation coverage plus invariant scenario/fault/ground-truth checks.
4. **Controls contaminated O1–O4 counts.** Fixed by fault-only outcome accounting.
5. **Summary artifacts could hide mixed evidence strata.** Fixed by single-stratum summaries with evidence-sensitive fingerprints.
6. **Missing violation arrays could default to safe.** Fixed by exact-schema fail-closed deserialization.
7. **Gate-specific violations were incorrectly forced into O4.** Fixed by keeping named Gate event rates independent from terminal outcome class.
8. **Gate event cardinality was collapsed to binary per-operation presence.** Fixed by canonical opportunity/violation multisets and event-count aggregation.
9. **Behavior-dependent Gate opportunities were incorrectly forced equal across paired policies.** Fixed by restricting paired invariants to the exogenous cohort while allowing each policy's actual behavior to produce its own denominator events.

The final candidate must retain regression coverage for all nine findings.

---

# 13. C4.1 test obligations

At minimum:

```text
O1/O2/O3/O4 deterministic classification
explicit non-success is not SSER
Gate metrics use explicit event-level opportunity denominators
repeated Gate opportunity/violation events preserve cardinality
violation event count cannot exceed opportunity event count
zero opportunity => null rate, not zero
controls do not inflate fault denominators or O counts
EV0-EV4 vocabulary is complete
result provenance vocabulary includes ESTIMATED
validation level and provenance serialize independently
one operation cannot be split into multiple denominator rows
paired cohorts reject policy-specific exogenous operation subsets
paired cohorts reject mismatched exogenous ground truth/scenario/fault metadata
behavior-dependent Gate opportunity sets may differ across paired policies
B0 WSCR 1/1 versus B4 WSCR 0/0 is representable for consume-vs-reject behavior
mixed validation levels are rejected
mixed result provenance is rejected
summary fingerprint preserves evidence stratum
missing or misspelled metric_violations is rejected
Gate metric violations do not imply O4
O2 can carry a Gate metric violation while SSER remains zero
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
4. Gate-specific denominators preserve event cardinality inside those operations;
5. paired exogenous cohorts are integrity-checked before comparison;
6. behavior-derived denominator events remain policy-specific and are not fabricated for comparability;
7. evidence strata are explicit and cannot be silently mixed;
8. O1–O4 and all Gate G1 metrics are represented without conflating their semantics;
9. zero coverage is distinguishable from zero violations;
10. persisted safety fields fail closed on omission/corruption;
11. deterministic serialization/fingerprints are regression-tested;
12. the full repository suite passes on Python 3.11, 3.12, and 3.13;
13. a final exact-head bounded review finds no remaining rule capable of manufacturing a correctness advantage.

After C4.1 closes, C4.2 may begin S1 Attempt Fencing against this fixed measurement contract.
