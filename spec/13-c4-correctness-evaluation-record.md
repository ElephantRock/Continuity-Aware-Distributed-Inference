# 13 — C4 Correctness Evaluation
## Implementation Record

**Project:** Continuity-Aware Distributed Inference  
**Milestone:** C4 — Correctness Evaluation / Gate G1  
**Prerequisite:** C3 CLOSED at `950dbb2303a49482e27ee09468717102eec8b0f0`  
**Tracking:** #44 / C4.1 #45  
**Status:** IN PROGRESS — C4.1 correctness-evaluation contract candidate

---

# 1. C4 purpose

C4 evaluates whether the closed C1/C2/C3 system exhibits the correctness distinction required by Gate G1. It uses systematic adversarial fault injection over the already-representable workload/failure corpus without redefining semantic authority, fault semantics, or baseline information privileges.

The measurement boundary preserves:

```text
ground truth != policy-visible observation
explicit non-success != silent semantic error
```

The primary safety question is whether Continuity prevents silent incorrect outcomes while making availability costs explicit.

---

# 2. Independent experimental ground truth

The Experimental Plan requires an oracle containing at minimum the true Program, Session, Continuation graph, active Attempt, State origin and compatibility, physical replicas, Binding epoch, injected fault, and semantic outcome.

For every evaluated policy decision the harness must preserve:

```text
ground_truth
observed_evidence
policy_decision
semantic_result
```

The policy under test does not automatically receive `ground_truth`.

C4.1 fixes the aggregation unit more narrowly than an individual scheduling call: one `CorrectnessEvaluationRecord` represents one complete correctness-sensitive **operation** under one policy. If that operation requires multiple decisions, such as:

```text
WAIT -> RETRY -> COMMIT
```

those decisions are stored as an ordered trace inside `policy_decision`. They are not emitted as separate correctness rows, because the Failure Model denominator is `TotalFaultedOperations`, not total scheduler/reconciliation decisions.

---

# 3. Paired cohort identity

Every operation record carries:

```text
cohort_id
trial_id
operation_id
policy_id
```

`cohort_id + trial_id + operation_id` is the policy-independent denominator identity. For a paired comparison, the same operation identity must exist for every policy included in the summary.

Across policies for the same operation, the following are required to match exactly:

```text
scenario_id
fault_id / fault_class
validation_level
evidence_provenance
ground_truth
metric_opportunities
```

The following may differ because they are policy-specific observations/outcomes:

```text
observed_evidence
policy_decision
semantic_result
metric_violations
```

A paired summary rejects:

- a policy-specific subset of operations;
- mismatched scenario/fault identity;
- mismatched ground truth;
- mismatched metric opportunities;
- duplicate `(policy_id, cohort_id, trial_id, operation_id)` rows.

This prevents a policy from receiving an easier/narrower correctness cohort while still being displayed as directly comparable.

A single-policy summary remains valid for implementation checks or pilot analysis, but it is not itself a paired B0–B4 comparison.

---

# 4. Evaluation evidence terminology

Gate G0 required methodological validation levels to be distinct from runtime `Evidence` semantics. The normalized validation hierarchy is represented as:

```text
EV0  deterministic semantics
EV1  measured CPU distributed
EV2  trace-derived
EV3  calibrated simulation
EV4  optional accelerator measurement
```

The Research Thesis separately requires concrete result provenance:

```text
MEASURED
SIMULATED
TRACE_DERIVED
SYNTHETICALLY_GENERATED
ANALYTICALLY_DERIVED
ESTIMATED
```

C4.1 models these as two orthogonal fields:

```text
validation_level
evidence_provenance
```

They must not be collapsed. Runtime `Evidence` authority/status/freshness remains the closed C1 semantic concept and is not reused as methodological evidence terminology.

---

# 5. Failure outcome classification

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

WAIT/FAIL/AMBIGUOUS/REJECT without authoritative commit
    -> O3

reported success + incorrect authoritative commit
    -> O4
```

An explicit non-success cannot simultaneously commit authoritative semantic state. A claimed silent semantic violation must include an authoritative commit.

O1–O4 are **fault outcome classes**, so summary counts are computed over faulted operations only. Controls remain visible in `operation_count` but do not contribute to O1–O4 counts.

---

# 6. Gate G1 correctness metrics

The six Gate G1 safety metrics are:

```text
Stale Attempt Acceptance Rate
Wrong-Branch Reuse Rate
Wrong-State Consumption Rate
Silent Binding Divergence Rate
Ambiguous Commit Rate
Duplicate Finalization Rate
```

C4.1 also derives:

```text
Silent Semantic Error Rate
Explicit Non-Success Rate
Recovery Rate
```

For B4, Gate G1 ultimately requires the six safety rates to be zero for covered modeled failure classes. C4.1 produces no favorable or unfavorable research result; it fixes how later results are counted.

---

# 7. Denominator semantics

Each Gate G1 metric records an explicit opportunity denominator. If no opportunity has been evaluated:

```text
numerator   = 0
denominator = 0
rate        = null
```

not `rate = 0`.

Gate-metric opportunities require a faulted operation.

The three Failure Model aggregate rates use faulted operations only:

```text
SSER = O4 / TotalFaultedOperations
ExplicitNonSuccessRate = O3 / TotalFaultedOperations
RecoveryRate = (O1 + O2) / TotalFaultedOperations
```

Because each record is one complete operation and duplicate operation identities are rejected, a multi-decision recovery sequence cannot inflate `TotalFaultedOperations`.

---

# 8. Canonical record and reproducibility

`CorrectnessEvaluationRecord` is immutable and contains:

```text
cohort_id
trial_id
operation_id
policy_id
scenario_id
fault_id / fault_class
validation_level
evidence_provenance
ground_truth
observed_evidence
policy_decision
semantic_result
outcome_class
metric_opportunities
metric_violations
```

The experimental views are copied into canonical JSON at record construction. Consequences:

- later mutation of caller-owned dictionaries cannot rewrite recorded ground truth;
- mapping insertion order cannot change a fingerprint;
- non-finite numeric values are rejected;
- duplicate metric entries are rejected;
- a violation cannot be counted without a matching opportunity;
- record JSON round-trips through a schema check;
- serialized `outcome_class` must agree with the semantic result.

Schema:

```text
cadi.correctness-evaluation.v1
```

Records and summaries expose stable SHA-256 fingerprints over canonical JSON.

---

# 9. Deterministic aggregation

`summarize_correctness(...)` validates denominator identity and paired cohort integrity before computing rates.

For every present policy it emits:

```text
operation_count
faulted_operation_count
O1/O2/O3/O4 faulted-operation counts
metric numerator
metric denominator
metric rate
```

Policies are emitted in canonical B0→B4 order. Input ordering cannot change the summary fingerprint.

The aggregator is policy-neutral. It does not infer that B4 is correct, does not penalize weaker policies by construction, and does not convert a placement decision into an authoritative semantic result.

---

# 10. Baseline fairness and C3 boundary

The C3 information contract remains closed. C4.1 does not:

```text
add fields to B0-B3
remove ordinary correctness mechanisms from baselines
change B4 behavior
change C1 commit semantics
change C2 scenario/fault semantics
```

In particular:

> placement admission is not equivalent to authoritative result acceptance.

S1 must evaluate stale-Attempt authoritative acceptance using an explicit semantic-result experiment rather than counting a B0–B3 placement as a stale finalization.

If competent simpler baselines independently provide equivalent result fencing, H1 must be narrowed rather than weakened baselines being manufactured.

---

# 11. Planned C4 slices

```text
C4.1  correctness-evaluation contract / independent oracle records
C4.2  S1 Attempt Fencing
C4.3  S2 State-Lineage Safety
C4.4  S3 Binding Safety
C4.5  S4 Evidence Safety
C4.6  S5 Idempotence / Ordering + Gate G1 closure
```

The series maps to:

```text
S1 -> SAAR, DFR
S2 -> WBRR, WSCR
S3 -> SBDR
S4 -> ACR, SSER, Explicit Non-Success Rate
S5 -> DFR, invariant violations, semantic-state equivalence
```

---

# 12. C4.1 bounded-review findings

Four substantive measurement-contract defects were found before merge:

1. **Evaluation evidence conflation.** The first candidate collapsed EV0–EV4 validation level and result provenance into one enum and omitted `ESTIMATED`. The repaired contract models the two dimensions independently.
2. **Decision rows could inflate operation denominators.** The first candidate counted every record carrying a fault ID as one faulted operation even though an operation can require multiple policy decisions. The repaired contract introduces explicit `operation_id` and defines one record as one complete operation; multi-step decision sequences stay inside `policy_decision`.
3. **Policy cohorts could be mismatched.** The first candidate allowed B4 to be summarized over a narrower/easier set of trials or different ground truth/opportunities than comparison policies. The repaired aggregator validates exact paired operation coverage and invariant metadata across policies.
4. **Controls contaminated O1–O4 counts.** The first candidate counted controls in failure-outcome classes. The repaired summary computes O1–O4 over faulted operations only and requires the counts to sum to `faulted_operation_count`.

All findings require regression coverage before C4.1 can close.

---

# 13. C4.1 test obligations

C4.1 tests must prove at minimum:

```text
O1/O2/O3/O4 classification is deterministic
explicit non-success is not counted as SSER
Gate metrics use explicit opportunity denominators
zero-opportunity rates remain null
controls do not inflate fault denominators or O1-O4 counts
EV0-EV4 validation levels are complete
result provenance includes measured/simulated/trace-derived/synthetic/analytic/estimated
validation level and provenance serialize independently
one operation cannot be split into multiple denominator rows
paired cohorts reject policy-specific operation subsets
paired cohorts reject mismatched ground truth/opportunities
canonical fingerprints ignore mapping insertion order
record construction snapshots mutable caller input
record JSON round-trips with schema checking
tampered outcome classification is rejected
metric violations require matching opportunities
Gate metric opportunities require a faulted operation
non-finite record data is rejected
single-policy pilot summaries remain possible
paired policy summaries are emitted in canonical B0-B4 order
```

All pre-existing C1/C2/C3 tests remain regression obligations.

---

# 14. Gate G1 boundary

For B4 under covered modeled failures:

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

C4 is not closed merely because B4 reaches zero. The research gate also requires a defensible correctness distinction from simpler competent baselines. If simpler baselines independently supply equivalent mechanisms, the corresponding novelty claim must be narrowed rather than hidden.

---

# 15. C4.1 exit criterion

C4.1 may close when:

1. the machine-readable evaluation schema is merged;
2. ground truth and policy-visible observations remain separate records;
3. validation hierarchy and result provenance remain separate evidence dimensions;
4. each aggregate row has explicit cohort/trial/operation identity;
5. paired policy cohorts are integrity-checked before comparison;
6. O1–O4 and all Gate G1 metrics are represented;
7. denominator semantics distinguish zero violations from zero coverage and decisions from operations;
8. deterministic serialization/fingerprints are regression-tested;
9. existing C1/C2/C3 tests remain green on Python 3.11, 3.12, and 3.13;
10. bounded review finds no remaining measurement rule that can manufacture a correctness advantage.

After C4.1 closes, C4.2 may implement S1 Attempt Fencing experiments against this fixed measurement contract.
