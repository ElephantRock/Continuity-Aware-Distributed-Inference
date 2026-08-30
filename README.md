# Continuity-Aware Distributed Inference

**Continuity-Aware Distributed Inference (CADI)** is a provider-neutral distributed-systems research project studying how stateful generative workloads can preserve causal execution continuity and reusable-state lineage across retries, phases, branches, asynchronous gaps, state movement, and failures.

## Current status

- **C0.1 Research Specification:** complete
- **Gate G0:** PASS
- **C1 Deterministic Continuity Core:** **CLOSED** — PR #5 squash-merged to `main` as `562718f8137ed7f0e7087eab861c5e939e028e57` after exact-head CI and bounded independent semantic closure review
- **C1 validation:** 157 deterministic/invariant/adversarial tests passing; permanent CI covers Python 3.11, 3.12, and 3.13
- **C1 closure review:** fifteen substantive Codex correctness/consistency findings plus two independent closure-audit findings fixed and regression-tested; all review threads resolved
- **Final Codex rerun:** requested on the prior exact head but unavailable because the GitHub Codex integration reached its code-review usage limit; this is recorded as a tooling/quota limitation, not a successful review
- **C2 Discrete-Event Simulator:** **IN PROGRESS** — C2.1 event kernel CLOSED via PR #14 / `f4e854fa930b09c27d2ea2bea9ecbca04b7ff00d`; C2.2 resource model CLOSED via PR #15 / `9c2c3f0801bca9abf165bf626970c2e9d8fa7d5e`; C2.3 semantic adapter CLOSED via PR #16 / `e1ff3e7c3f63a12755d519b6061ad7fc2feecfb6`; C2.4 fault injection is next
- **Current repository validation:** 203 tests passing on Python 3.11, 3.12, and 3.13 at the C2.3 closure head

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

The merged C2 substrate now includes:

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
- causal observation-time fencing; and
- retry scheduling whose semantic result depends on simulated delivery order rather than host/Python setup order.

C2.4 will add validated deterministic and seeded probabilistic fault injection; C2.5 will close representability against the required workload and failure families.

## Repository layout

```text
spec/               canonical research specification, coverage registry, milestone records
continuity/         deterministic semantic kernel, serialization, replay
simulator/          deterministic C2 event/resource/semantic-adapter substrate
tests/              invariant, failure-trace, replay, simulator, and adversarial tests
.github/workflows/  reproducibility / CI
```

## Run the repository test suite

```bash
python -m pip install pytest
python -m pytest
```

C1 deliberately excludes queueing, networking, accelerator timing, public-trace ingestion, and performance simulation. Those concerns enter at C2 and later milestones without redefining the merged C1 semantics. C2 itself still does not provide calibrated accelerator cost evidence; performance calibration remains a later milestone.

## Research discipline

Every Paper 1 mechanism must map to a research question, hypothesis or safety property, experiment, metric, and evidence class. Measured, trace-derived, synthetic, and simulated results are kept explicitly distinct.
