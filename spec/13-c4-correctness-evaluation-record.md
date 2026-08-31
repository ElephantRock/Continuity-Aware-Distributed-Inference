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

The Experimental Plan requires the harness to maintain an oracle containing at minimum the true Program, Session, Continuation graph, active Attempt, State origin and compatibility, physical replicas, Binding epoch, injected fault, and semantic outcome.

For every evaluated policy decision C4 records four separate views:

```text
ground_truth
observed_evidence
policy_decision
semantic_result
```

The policy under test does not automatically receive the `ground_truth` record. C4.1 provides the immutable record contract; later C4 slices are responsible for populating it from independent experiment/oracle logic rather than from policy-visible fields.

---

# 3. Evaluation evidence terminology

Gate G0 required methodological validation levels to be distinct from the runtime `Evidence` vocabulary. The normalized validation hierarchy is therefore represented separately as:

```text
EV0  deterministic semantics
EV1  measured CPU distributed
EV2  trace-derived
EV3  calibrated simulation
EV4  optional accelerator measurement
```

The Research Thesis separately requires each concrete result to identify how its evidence was produced:

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

They must not be collapsed into one enum. For example, a result can belong to EV3 while its reported quantity is explicitly ESTIMATED or SIMULATED; the record preserves both claims rather than silently upgrading one into the other.

This terminology is methodological. Runtime `Evidence` authority/status/freshness remains the closed C1 semantic concept and is not renamed or reused here.

---

# 4. Failure outcome classification

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

An explicit non-success cannot simultaneously commit authoritative semantic state. A claimed silent semantic violation must include an authoritative commit; otherwise the record is internally inconsistent and rejected.

---

# 5. Gate G1 correctness metrics

The six Gate G1 safety metrics are:

```text
Stale Attempt Acceptance Rate
Wrong-Branch Reuse Rate
Wrong-State Consumption Rate
Silent Binding Divergence Rate
Ambiguous Commit Rate
Duplicate Finalization Rate
```

C4.1 also derives the Failure Model aggregate rates:

```text
Silent Semantic Error Rate
Explicit Non-Success Rate
Recovery Rate
```

For B4, Gate G1 ultimately requires the six safety rates to be zero for the covered modeled failure classes. C4.1 itself produces no favorable or unfavorable research result; it fixes how later results are counted.

---

# 6. Denominator semantics

C4 must not silently turn missing coverage into a perfect score. Each Gate G1 metric therefore records an explicit opportunity denominator.

Example:

```text
two stale-Attempt presentations
one stale authoritative acceptance

SAAR = 1 / 2
```

If no stale-Attempt opportunity has been evaluated:

```text
numerator   = 0
denominator = 0
rate        = null
```

not `rate = 0`.

This distinguishes zero observed violations from no experiment capable of observing the violation. Gate-metric opportunities require a faulted trial.

The three Failure Model aggregate rates use faulted operations only:

```text
SSER = O4 / TotalFaultedOperations
ExplicitNonSuccessRate = O3 / TotalFaultedOperations
RecoveryRate = (O1 + O2) / TotalFaultedOperations
```

Control trials remain visible in total trial counts but cannot dilute these denominators.

---

# 7. Canonical record and reproducibility

`CorrectnessEvaluationRecord` is immutable and contains:

```text
trial_id
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

The four experimental views are copied into canonical JSON at record construction. Consequences:

- later mutation of caller-owned dictionaries cannot rewrite recorded ground truth;
- mapping insertion order cannot change a record fingerprint;
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

# 8. Deterministic aggregation

`summarize_correctness(...)` groups records in canonical B0→B4 order. For every present policy it emits:

```text
trial_count
faulted_trial_count
O1/O2/O3/O4 counts
numerator
denominator
rate
```

for every correctness metric.

Duplicate `(PolicyID, trial_id)` identities are rejected so accidental replay/double ingestion cannot silently bias a rate.

The aggregator is policy-neutral. It does not infer that B4 is correct, does not penalize weaker policies by construction, and does not convert a placement decision into an authoritative semantic result.

---

# 9. Baseline fairness and C3 boundary

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

# 10. Planned C4 slices

```text
C4.1  correctness-evaluation contract / independent oracle records
C4.2  S1 Attempt Fencing
C4.3  S2 State-Lineage Safety
C4.4  S3 Binding Safety
C4.5  S4 Evidence Safety
C4.6  S5 Idempotence / Ordering + Gate G1 closure
```

The series maps to the Experimental Plan:

```text
S1 -> SAAR, DFR
S2 -> WBRR, WSCR
S3 -> SBDR
S4 -> ACR, SSER, Explicit Non-Success Rate
S5 -> DFR, invariant violations, semantic-state equivalence
```

---

# 11. C4.1 bounded-review finding

The first C4.1 candidate incorrectly collapsed methodological validation level and result provenance into a single `EvidenceClass` enum. That contradicted the Gate G0 terminology normalization and omitted the Thesis-required `estimated` provenance category.

The repaired contract now uses independent `ValidationEvidenceLevel` and `ResultEvidenceProvenance` enums and regression-tests the complete vocabularies plus independent serialization.

---

# 12. C4.1 test obligations

C4.1 tests must prove at minimum:

```text
O1/O2/O3/O4 classification is deterministic
explicit non-success is not counted as SSER
Gate metrics use explicit opportunity denominators
zero-opportunity rates remain null
control trials do not inflate faulted-operation denominators
EV0-EV4 validation levels are complete
result provenance includes measured/simulated/trace-derived/synthetic/analytic/estimated
validation level and provenance serialize independently
canonical record fingerprints ignore mapping insertion order
record construction snapshots mutable caller input
record JSON round-trips with schema checking
tampered outcome classification is rejected
metric violations require matching opportunities
Gate metric opportunities require a faulted trial
non-finite record data is rejected
duplicate policy/trial identities cannot be double-counted
policy summaries are emitted in canonical B0-B4 order
```

All pre-existing C1/C2/C3 tests remain regression obligations.

---

# 13. Gate G1 boundary

The Failure Model requires B4, for covered modeled failures, to target:

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

# 14. C4.1 exit criterion

C4.1 may close when:

1. the machine-readable evaluation schema is merged;
2. ground truth and policy-visible observations remain separate records;
3. validation hierarchy and result provenance remain separate evidence dimensions;
4. O1–O4 and all Gate G1 metrics are represented;
5. denominator semantics distinguish zero violations from zero coverage;
6. deterministic serialization/fingerprints are regression-tested;
7. existing C1/C2/C3 tests remain green on Python 3.11, 3.12, and 3.13;
8. bounded review finds no measurement rule that can manufacture a correctness advantage.

After C4.1 closes, C4.2 may implement S1 Attempt Fencing experiments against this fixed measurement contract.
