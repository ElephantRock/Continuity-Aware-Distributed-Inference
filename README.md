# Continuity-Aware Distributed Inference

**Continuity-Aware Distributed Inference (CADI)** is a provider-neutral distributed-systems research project studying how stateful generative workloads can preserve causal execution continuity and reusable-state lineage across retries, phases, branches, asynchronous gaps, state movement, and failures.

## Current status

- **C0.1 Research Specification:** complete
- **Gate G0:** PASS
- **C1 Deterministic Continuity Core:** **CLOSED** — PR #5 squash-merged to `main` as `562718f8137ed7f0e7087eab861c5e939e028e57` after exact-head CI and bounded independent semantic closure review
- **C1 validation:** 157 deterministic/invariant/adversarial tests passing; permanent CI covers Python 3.11, 3.12, and 3.13
- **C1 closure review:** fifteen substantive Codex correctness/consistency findings plus two independent closure-audit findings fixed and regression-tested; all review threads resolved
- **Final Codex rerun:** requested on the prior exact head but unavailable because the GitHub Codex integration reached its code-review usage limit; this is recorded as a tooling/quota limitation, not a successful review
- **C2 Discrete-Event Simulator:** **CLOSED** — C2.1 event kernel via PR #14 / `f4e854fa930b09c27d2ea2bea9ecbca04b7ff00d`; C2.2 resource model via PR #15 / `9c2c3f0801bca9abf165bf626970c2e9d8fa7d5e`; C2.3 semantic adapter via PR #16 / `e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6`; C2.4 deterministic/probabilistic fault injection via PR #28 / `f33b82d0802cfc97835049e274b288e06f87d369`; C2.5 workload/failure representability via PR #30 / `462e63685500e022a97386618ba606f1384d1c56`
- **C3 Baseline Policies:** **CLOSED** — B0 Request-Centric via PR #34 / `b17e03867004ed0dc51aa821cbe10e98a888aebb`; B1 Cache-Aware via PR #36 / `d0742284de450b86ae98db4f52ea993e90d5f8e7`; B2 Session-Affinity via PR #38 / `b2a13e3bc372449fc5cd3bc8e5b925f7983d991e`; B3 State-Aware via PR #40 / `1ce103ab0805fa81a4da4c9c4cc708475a891bd6`; B4 Continuity-Aware + paired-interface closure via PR #42 / `ffa1ebb8a2aec63221252152c65069eef653259c`
- **Current repository validation:** 327 tests passing on Python 3.11, 3.12, and 3.13 at the exact reviewed C3.5 head `5d42dcd93842664034698180854ccd5b6877d0a6`; clean automated re-review reported no major issues before merge
- **Next implementation milestone:** C4 — correctness evaluation over the normalized fault/workload catalogue and the closed B0–B4 policy set

The repository is the canonical system of record for the project.

## Paper 1 scope

The first paper is intentionally restricted to four mechanisms/policies:

1. attempt fencing;
2. compatible-state routing;
3. lifecycle-aware state retention;
4. safe migration.

The core semantic model separates:

- logical execution from physical execution;
- Attempt execution outcome from Attempt authority;
- reusable-State lineage from physical replicas;
- State lifecycle from State validity;
- observations/Evidence from committed semantic truth;
- immutable semantic Events from state-changing semantic Operations.

## C1 semantic reference

The deterministic core now includes:

- Program → Session → Continuation → LogicalRequest → Attempt → Phase identity;
- `CurrentAttempt` / `CommittedAttempt` fencing;
- monotonic Attempt execution and Phase status transitions;
- supersession fencing that prevents superseded Attempts from authoritatively completing Phases or producing new Attempt-/Phase-origin State;
- terminal-request fencing that prevents restored `FAILED`/`CANCELLED` requests from being finalized and rejects inconsistent terminal-request authority during restore;
- producer-aware and Phase-aware reusable-State compatibility;
- request-origin State constrained to completed requests with producer exactly equal to `CommittedAttempt(request)`;
- State lifecycle and validity;
- BindingID + monotonic epoch migration fencing;
- Evidence authority/status/scope/freshness and explicit DERIVED Evidence provenance;
- Output Evidence references that must resolve at Output creation;
- fail-closed reconciliation;
- semantic Event identity/idempotence;
- canonical snapshots and fingerprints;
- schema-versioned Event and Operation JSONL traces with strict finite-number JSON serialization and parsing, including exponent-overflow rejection;
- typed semantic-operation validation against whitelisted `ContinuityCore` signatures before construction, canonical emission, and dispatch, including recursive dataclass/enum/container validation;
- deterministic trace hardening that rejects malformed direct Operations, duplicate argument names, one-shot iterator arguments, and mutable-set `Iterable` arguments unsupported by the canonical encoder;
- snapshot restoration with decoded-state type/schema validation, global logical-ID uniqueness, and non-strippable invariant validation before returning a live core;
- completed-request restoration checks that revalidate committed Attempt authority/success and terminal authoritative Output consistency;
- deterministic semantic-operation replay with explicit replay time for time-sensitive actions;
- an independent invariant oracle including declared State-origin re-resolution, cached-provenance consistency, Phase-dependency temporal-order validation, and global identity uniqueness;
- all 12 mandatory Failure Model traces;
- a deterministic adversarial sequence matrix plus seeded sequence fuzzing;
- an executable **38-invariant** coverage registry whose expected IDs are derived directly from the canonical invariant catalogue.

## C2 simulator reference

The closed C2 substrate now includes:

- deterministic logical time and `(time, insertion-sequence)` event ordering;
- stable simulator EventIDs, cancellation, immutable canonical payloads, and seeded reproducibility;
- deterministic worker queues and capacity;
- synthetic network latency/bandwidth timing;
- non-authoritative physical `StateReplica` runtime shadows;
- State materialization, transfer, eviction/loss, and worker failure/recovery behavior;
- a `ContinuityAdapter` that invokes only public C1 transitions and never reimplements semantic authority;
- post-operation C1 fingerprints plus invariant-oracle validation at the adapter boundary;
- authoritative C1/C2 equivalence for the canonical Attempt timeout/retry/finalization race;
- exact immutable Evidence identity across reordered duplicate delivery;
- causal observation-time fencing;
- retry scheduling whose semantic result depends on simulated delivery order rather than host/Python setup order;
- policy-neutral deterministic delivery/resource fault transformations and explicit cross-layer Attempt/State fault injectors;
- FaultID → semantic/resource outcome linkage with fail-closed `INVARIANT_VIOLATION` precedence;
- policy-neutral seeded fault campaign manifests, canonical fingerprints, exact schedule replay, and paired-policy schedule reuse;
- an independent `FaultTrustOracle` with strict FaultRecord JSON/JSONL validation, adversarial malformed/tampered metadata coverage, transformation-graph checks, runtime/resource observation checks, C1 invariant integration, and campaign-consistency validation;
- an exact 24-entry representability registry for W1–W10 plus normalized FTR1–FTR14 under stable semantic scenario names;
- canonical schedule and executed-trace fingerprints with exact same-seed replay checks;
- explicit normalized FTR→C1 semantic provenance, including the pre-normalization C1 test-number mapping;
- C1↔C2 authoritative-equivalence assertions for the adapter-supported FTR1–FTR3 execution traces, without adding a second State/Binding semantic implementation.

## C3 baseline-policy reference

The closed C3 policy layer now includes:

- machine-readable information contracts for B0–B4 under `cadi.policy-information-contract.v2`;
- B0 deterministic normalized-load routing;
- B1 candidate-key-scoped cache locality with exact B0 fallback ordering;
- B2 SessionID-scoped preferred-location affinity with exact B1 fallback ordering;
- B3 exact StateID/location awareness without Continuation ancestry, producer-Attempt authority, Binding/Evidence authority, or reconciliation semantics;
- B4 Program/Session/Continuation/LogicalRequest/Attempt/State/Binding/Evidence/Reconciliation visibility through the declared contract;
- read-only C1 delegation for authoritative current-Attempt checks and State compatibility;
- attempt fencing before physical placement;
- compatible-State locality only after `MATCHED` reconciliation and C1 compatibility;
- deterministic lifecycle-aware retention classes over ACTIVE / WAITING / SPECULATIVE / TERMINAL;
- fail-closed migration eligibility while actual migration commit remains C1-authoritative;
- one paired B0–B4 placement harness over the same immutable `PolicyObservation`, with independent per-policy projection and policy-ID registration validation;
- explicit regression coverage for wrong-sibling State, stale Attempt observations, candidate-only locality, positional contract compatibility, and malformed paired-policy registration.

C1, C2, and C3 are closed. Correctness evaluation begins at C4 without redefining their merged semantic, simulator, or policy boundaries.

## Repository layout

```text
spec/               canonical research specification, coverage registry, milestone records
continuity/         deterministic semantic kernel, serialization, replay
simulator/          deterministic C2 event/resource/semantic-adapter and C3 policy substrate
tests/              invariant, failure-trace, replay, simulator, policy, and adversarial tests
.github/workflows/  reproducibility / CI
```

## Run the repository test suite

```bash
python -m pip install pytest
python -m pytest
```

C1 deliberately excludes queueing, networking, accelerator timing, public-trace ingestion, and performance simulation. Those concerns enter at C2 and later milestones without redefining the merged C1 semantics. C2 and C3 still do not provide calibrated accelerator cost evidence; performance calibration remains a later milestone.

## Research discipline

Every Paper 1 mechanism must map to a research question, hypothesis or safety property, experiment, metric, and evidence class. Measured, trace-derived, synthetic, and simulated results are kept explicitly distinct.
