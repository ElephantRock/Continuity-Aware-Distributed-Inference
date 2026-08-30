from pathlib import Path

readme = Path('README.md')
text = readme.read_text()
text = text.replace(
    '- **C2 Discrete-Event Simulator:** **IN PROGRESS** — C2.1 event kernel CLOSED via PR #14 / `f4e854fa930b09c27d2ea2bea9ecbca04b7ff00d`; C2.2 resource model CLOSED via PR #15 / `9c2c3f0801bca9abf165bf626970c2e9d8fa7d5e`; C2.3 semantic adapter CLOSED via PR #16 / `e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6`; C2.4 fault injection is next\n- **Current repository validation:** 203 tests passing on Python 3.11, 3.12, and 3.13 at the C2.3 closure head',
    '- **C2 Discrete-Event Simulator:** **IN PROGRESS** — C2.1 event kernel CLOSED via PR #14 / `f4e854fa930b09c27d2ea2bea9ecbca04b7ff00d`; C2.2 resource model CLOSED via PR #15 / `9c2c3f0801bca9abf165bf626970c2e9d8fa7d5e`; C2.3 semantic adapter CLOSED via PR #16 / `e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6`; **C2.4 deterministic/probabilistic fault injection CLOSED** via PR #28 / `f33b82d0802cfc97835049e274b288e06f87d369`; C2.5 representability is next\n- **Current repository validation:** 268 tests passing on Python 3.11, 3.12, and 3.13 at the C2.4.4 closure head',
    1,
)
text = text.replace(
    '- retry scheduling whose semantic result depends on simulated delivery order rather than host/Python setup order.\n\nC2.4 will add validated deterministic and seeded probabilistic fault injection; C2.5 will close representability against the required workload and failure families.',
    '- retry scheduling whose semantic result depends on simulated delivery order rather than host/Python setup order;\n- policy-neutral deterministic delivery/resource fault transformations and explicit cross-layer Attempt/State fault injectors;\n- FaultID → semantic/resource outcome linkage with fail-closed `INVARIANT_VIOLATION` precedence;\n- policy-neutral seeded fault campaign manifests, canonical fingerprints, exact schedule replay, and paired-policy schedule reuse; and\n- an independent `FaultTrustOracle` with strict FaultRecord JSON/JSONL validation, adversarial malformed/tampered metadata coverage, transformation-graph checks, runtime/resource observation checks, C1 invariant integration, and campaign-consistency validation.\n\nC2.4 is closed. C2.5 is the next simulator milestone and will close representability against the required workload and failure families before policy comparison begins.',
    1,
)
readme.write_text(text)

spec = Path('spec/10-c2-fault-injection-record.md')
text = spec.read_text()
text = text.replace(
    '**Status:** IN PROGRESS — C2.4.1/C2.4.2/C2.4.3 CLOSED; C2.4.4 implementation candidate',
    '**Status:** C2.4 IMPLEMENTATION COMPLETE — C2.4.1–C2.4.4 merged; post-merge synchronization tracked by #22',
    1,
)
text = text.replace(
    '#10  C2.4 umbrella\n#18  C2.4.1 fault metadata + delivery/resource transformation substrate — CLOSED\n#19  C2.4.2 mandatory failure-class injectors + semantic outcome linkage — CLOSED\n#20  C2.4.3 probabilistic campaign manifests + replay/reuse contract — CLOSED\n#21  C2.4.4 injector trust oracle + closure review — ACTIVE\n#22  C2.4.5 post-merge documentation synchronization',
    '#10  C2.4 umbrella — closure bookkeeping via #22\n#18  C2.4.1 fault metadata + delivery/resource transformation substrate — CLOSED\n#19  C2.4.2 mandatory failure-class injectors + semantic outcome linkage — CLOSED\n#20  C2.4.3 probabilistic campaign manifests + replay/reuse contract — CLOSED\n#21  C2.4.4 injector trust oracle + closure review — CLOSED\n#22  C2.4.5 post-merge documentation synchronization — ACTIVE',
    1,
)
append = r'''

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
'''
if '# 31. C2.4.4 Closure' in text:
    raise SystemExit('C2.4 closure already synchronized')
spec.write_text(text + append)
