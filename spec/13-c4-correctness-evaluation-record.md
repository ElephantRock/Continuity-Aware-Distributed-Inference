# 13 — C4 Correctness Evaluation
## C4.1 Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C4 — Correctness Evaluation / Gate G1  
**Prerequisite:** C3 CLOSED at `950dbb2303a49482e27ee09468717102eec8b0f0`  
**Tracking:** #44 / #45 / PR #46  
**Status:** IN PROGRESS — C4.1 final review candidate

---

# 1. Purpose and non-negotiable boundary

C4 evaluates the closed C1/C2/C3 stack. It does not redefine semantic authority, fault semantics, or policy information privileges.

The Experimental Plan requires four distinct views for evaluated policy behavior:

```text
ground_truth
observed_evidence
policy_decision
semantic_result
```

The first is an independent experimental oracle. It is not automatically exposed to the policy under test.

C4 preserves two distinctions throughout:

```text
ground truth != policy-visible observation
explicit non-success != silent semantic error
```

This is measurement infrastructure. It must not manufacture a correctness advantage for B4 by changing baseline capabilities, opportunity sets, denominator units, or evidence labels.

---

# 2. Denominator unit: one complete operation

The Failure Model defines aggregate correctness rates over `TotalFaultedOperations`.

Therefore one `CorrectnessEvaluationRecord` represents one complete correctness-sensitive **operation** under one policy, identified by:

```text
cohort_id
trial_id
operation_id
policy_id
```

A multi-step recovery path such as:

```text
WAIT -> RETRY -> COMMIT
```

is one operation. Its ordered decision sequence belongs inside the `policy_decision` trace. It must not be emitted as three correctness rows.

Duplicate:

```text
(policy_id, cohort_id, trial_id, operation_id)
```

identities are rejected before aggregation.

This prevents scheduler/reconciliation decision count from inflating the faulted-operation denominator.

---

# 3. Paired cohort integrity

For any multi-policy summary, every policy included in the comparison must have exactly the same operation keys.

For the same `cohort_id + trial_id + operation_id`, the following are policy-independent and must match:

```text
scenario_id
fault_id
fault_class
ground_truth
metric_opportunities
```

The following are policy-specific and may differ:

```text
observed_evidence
policy_decision
semantic_result
metric_violations
```

A summary rejects:

- policy-specific operation subsets;
- mismatched scenario or fault identity;
- mismatched independent ground truth;
- mismatched metric opportunity sets.

A single-policy summary remains valid for implementation checks or pilot evidence, but it is not itself a paired B0–B4 comparison.

This makes the opportunity denominator an invariant of the experiment cohort rather than a property a policy can select after the fact.

---

# 4. Evidence terminology

Gate G0 normalized methodological validation levels separately from runtime semantic `Evidence`.

C4.1 represents the validation hierarchy as:

```text
EV0  deterministic semantics
EV1  measured CPU distributed
EV2  trace-derived
EV3  calibrated simulation
EV4  optional accelerator measurement
```

The Research Thesis separately requires result provenance labels:

```text
MEASURED
SIMULATED
TRACE_DERIVED
SYNTHETICALLY_GENERATED
ANALYTICALLY_DERIVED
ESTIMATED
```

The machine-readable record therefore has two independent fields:

```text
validation_level
evidence_provenance
```

A `CorrectnessSummary` is restricted to exactly one `(validation_level, evidence_provenance)` stratum and serializes both fields into the summary artifact and fingerprint.

Mixed strata are rejected rather than silently pooled. Thus an EV1/MEASURED result cannot become indistinguishable from an EV3/ESTIMATED result with the same numeric counts.

Runtime C1 `Evidence` authority/status/freshness is a different semantic concept and is not renamed or reused here.

---

# 5. Failure outcome classes

The Failure Model requires every faulted operation to be classified as:

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

Classification is deterministic:

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

An explicit non-success cannot simultaneously commit authoritative semantic state.

A claimed silent semantic violation must include an authoritative commit.

O1–O4 counts are computed over **faulted operations only**. Controls remain visible in total `operation_count` but do not enter failure-outcome counts.

---

# 6. Correctness metrics and explicit opportunity denominators

Gate G1 uses six safety rates:

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
Silent Semantic Error Rate
Explicit Non-Success Rate
Recovery Rate
```

Each Gate metric has an explicit `metric_opportunities` denominator.

If no opportunity was evaluated:

```text
numerator   = 0
denominator = 0
rate        = null
```

not `rate = 0`.

A Gate metric violation must:

1. have a matching declared opportunity; and
2. correspond to an O4 silent semantic violation.

The Failure Model aggregate rates use faulted operations only:

```text
SSER = O4 / TotalFaultedOperations
ExplicitNonSuccessRate = O3 / TotalFaultedOperations
RecoveryRate = (O1 + O2) / TotalFaultedOperations
```

---

# 7. Fail-closed serialization

Schema:

```text
cadi.correctness-evaluation.v1
```

The canonical serializer always emits the complete record field set.

The deserializer therefore requires the exact v1 field set. Missing or misspelled safety fields are rejected; they are never defaulted to a safe value.

In particular, omission of:

```text
metric_violations
```

cannot silently become:

```text
metric_violations = []
```

Likewise, `SemanticResult` deserialization requires its complete canonical field set.

The record additionally:

- snapshots ground truth / observed evidence / policy decision into canonical JSON;
- rejects non-finite numeric data;
- validates serialized `outcome_class` against `semantic_result`;
- rejects duplicate metric entries;
- produces a stable SHA-256 fingerprint over canonical JSON.

---

# 8. Deterministic aggregation

`summarize_correctness(...)` performs checks in this order:

1. require at least one operation record;
2. reject non-record inputs;
3. reject duplicate policy/operation identities;
4. require one evidence stratum;
5. validate paired operation coverage and invariant metadata;
6. aggregate each policy in canonical B0→B4 order.

Each policy summary reports:

```text
operation_count
faulted_operation_count
O1/O2/O3/O4 faulted-operation counts
metric numerator
metric denominator
metric rate
```

The top-level summary records:

```text
validation_level
evidence_provenance
```

so the summary fingerprint is evidence-sensitive.

---

# 9. Baseline fairness and H1 boundary

C4.1 does not change the closed C3 information contract and does not remove ordinary correctness mechanisms from B0–B3.

A critical S1 rule is:

> placement admission is not authoritative result acceptance.

S1 must measure whether a stale Attempt can authoritatively finalize or otherwise be accepted as the current LogicalRequest result. It must not count a weaker baseline merely scheduling stale physical work as stale authoritative acceptance.

If competent simpler baselines independently fence stale result acceptance through ordinary correctness mechanisms allowed by their abstraction, H1 must be narrowed rather than those mechanisms being disabled.

Negative or null results remain valid research outcomes.

---

# 10. C4 safety slices

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

# 11. Bounded-review findings resolved before merge

Six substantive measurement-integrity findings were found during C4.1 review:

1. **Validation/provenance conflation.** The first candidate collapsed EV0–EV4 validation level and result provenance into one enum and omitted `ESTIMATED`. Fixed by orthogonal machine-readable fields.
2. **Decision rows could inflate faulted-operation denominators.** Fixed by explicit operation identity and one record per complete operation.
3. **Paired policy cohorts could differ.** Fixed by exact operation coverage plus invariant scenario/fault/ground-truth/opportunity checks across compared policies.
4. **Controls contaminated O1–O4 counts.** Fixed by restricting O-class counts to faulted operations.
5. **Summary artifacts could hide mixed evidence strata.** Fixed by rejecting mixed `(validation_level, evidence_provenance)` summaries and serializing the stratum into the summary/fingerprint.
6. **Missing violation arrays could default to safe.** Fixed by exact-schema deserialization; absent or misspelled `metric_violations` is rejected.

The final candidate must retain regression coverage for all six findings.

---

# 12. C4.1 test obligations

The C4.1 tests require at minimum:

```text
O1/O2/O3/O4 deterministic classification
explicit non-success is not SSER
Gate metrics use explicit opportunity denominators
zero opportunity => null rate, not zero
controls do not inflate fault denominators or O counts
EV0-EV4 vocabulary is complete
result provenance includes MEASURED/SIMULATED/TRACE_DERIVED/SYNTHETICALLY_GENERATED/ANALYTICALLY_DERIVED/ESTIMATED
validation level and provenance serialize independently
one operation cannot be split into multiple denominator rows
paired cohorts reject policy-specific operation subsets
paired cohorts reject mismatched ground truth/opportunities
mixed validation levels are rejected in one summary
mixed result provenance is rejected in one summary
summary serialization/fingerprint preserves evidence stratum
missing metric_violations is rejected
misspelled metric_violations is rejected
Gate violations require O4
canonical record fingerprints ignore mapping insertion order
record construction snapshots caller-owned mappings
record JSON round-trips with strict schema checking
tampered outcome classification is rejected
violations require matching opportunities
Gate opportunities require a faulted operation
non-finite record data is rejected
single-policy pilot summaries remain possible
paired summaries use canonical policy order
```

All pre-existing C1/C2/C3 tests remain regression obligations.

---

# 13. Gate G1 boundary

For B4 under covered modeled failures, Gate G1 targets:

```text
SAAR = 0
WBRR = 0
WSCR = 0
SBDR = 0
ACR = 0
DFR = 0
```

Gate G1 does not require:

```text
Explicit Failure Rate = 0
Recomputation Rate = 0
```

Safety and availability remain separate.

C4 does not close merely because B4 reaches zero. The gate also requires a defensible correctness distinction from simpler competent abstractions. If the distinction is absent for a claimed failure class, the claim must be narrowed.

---

# 14. C4.1 exit criterion

C4.1 closes only when:

1. `cadi.correctness-evaluation.v1` is merged;
2. independent ground truth remains distinct from policy-visible observation;
3. denominator units are complete operations, not decision rows;
4. paired policy cohorts are integrity-checked before comparison;
5. evidence strata are explicit and cannot be silently mixed;
6. O1–O4 and all Gate G1 metrics are represented;
7. zero coverage is distinguishable from zero violations;
8. persisted safety fields fail closed on omission/corruption;
9. deterministic serialization/fingerprints are regression-tested;
10. the full repository suite passes on Python 3.11, 3.12, and 3.13;
11. a final exact-head bounded review finds no remaining rule capable of manufacturing a correctness advantage.

After C4.1 closes, C4.2 may begin S1 Attempt Fencing against this fixed measurement contract.
