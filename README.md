# Continuity-Aware Distributed Inference

**Continuity-Aware Distributed Inference (CADI)** is a provider-neutral distributed-systems research project studying how stateful generative workloads can preserve causal execution continuity and reusable-state lineage across retries, branches, asynchronous gaps, state movement, and failures.

## Current status

- **C0.1 Research Specification:** complete
- **Gate G0:** PASS
- **C1 Deterministic Continuity Core:** initial kernel implemented
- **C2 simulator / performance modeling:** intentionally not started

The repository is now the canonical system of record for the project.

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
- observations/Evidence from committed semantic truth.

## Repository layout

```text
spec/               canonical research specification and milestone records
continuity/         deterministic semantic kernel
tests/              invariant and adversarial trace tests
.github/workflows/  reproducibility / CI
```

## Run the C1 deterministic suite

```bash
python -m pip install pytest
python -m pytest
```

The current C1 artifact deliberately excludes queueing, networking, GPU timing, trace ingestion, and performance simulation. Those enter at C2 and later milestones only after the semantic kernel satisfies its invariant coverage requirements.

## Research discipline

Every Paper 1 mechanism must map to a research question, hypothesis or safety property, experiment, metric, and evidence class. Measured, trace-derived, synthetic, and simulated results are kept explicitly distinct.
