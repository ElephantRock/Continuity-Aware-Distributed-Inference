from pathlib import Path

path = Path('spec/10-c2-fault-injection-record.md')
text = path.read_text()
text = text.replace(
    '**Status:** IN PROGRESS — C2.4.1/C2.4.2 CLOSED; C2.4.3 implementation candidate',
    '**Status:** IN PROGRESS — C2.4.1/C2.4.2/C2.4.3 CLOSED; C2.4.4 implementation candidate',
    1,
)
text = text.replace(
    '#20  C2.4.3 probabilistic campaign manifests + replay/reuse contract — ACTIVE\n#21  C2.4.4 injector trust oracle + closure review',
    '#20  C2.4.3 probabilistic campaign manifests + replay/reuse contract — CLOSED\n#21  C2.4.4 injector trust oracle + closure review — ACTIVE',
    1,
)
append = r'''

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
'''
if '# 25. C2.4.3 Closure' in text:
    raise SystemExit('C2.4.4 record already synchronized')
path.write_text(text + append)
