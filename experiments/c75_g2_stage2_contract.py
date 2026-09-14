from __future__ import annotations

from typing import Mapping, Sequence

from experiments.c7_protocol import C7ExperimentManifest, C7_STOCHASTIC_SEEDS, ExperimentSeries
from experiments.c72_routing_reuse import C72PairedResult, C72ProgramCase
from experiments.c75_g2_adjudicate import p1_cell_id, p4_cell_id, p7_cell_id
from experiments.c75_g2_execute import (
    C75B_HARDWARE_STRATA,
    C75ProgramRow,
    parameters_for_case,
    summarize_paired_result,
)
from experiments.c75_g2_protocol import (
    C75_C72_ADMISSIBLE_DATASET_FINGERPRINT,
    C75_SOURCE_SELECTION_FINGERPRINT,
    seed_selection_fingerprint,
    select_seed_records,
)
from experiments.trace_workload import NormalizedTraceRecord
from simulator.policies import PolicyID


C75B_STAGE2_CONTRACT_SCHEMA = "cadi.c7.5b.stage2-provenance-contract.v1"


def frozen_source_records(
    records: Sequence[NormalizedTraceRecord], *, admissible_dataset_fingerprint: str
) -> tuple[NormalizedTraceRecord, ...]:
    if admissible_dataset_fingerprint != C75_C72_ADMISSIBLE_DATASET_FINGERPRINT:
        raise ValueError("C7.5b admissible dataset fingerprint drift")
    fingerprint = seed_selection_fingerprint(
        records,
        admissible_dataset_fingerprint=admissible_dataset_fingerprint,
    )
    if fingerprint != C75_SOURCE_SELECTION_FINGERPRINT:
        raise ValueError("C7.5b frozen source-selection fingerprint drift")
    selected = select_seed_records(records)
    if len(selected) != len(C7_STOCHASTIC_SEEDS):
        raise AssertionError("frozen source selection must contain exactly 64 rows")
    return selected


def source_record_for_seed(
    selected: Sequence[NormalizedTraceRecord], seed: int
) -> NormalizedTraceRecord:
    if tuple(C7_STOCHASTIC_SEEDS) != tuple(range(64)):
        raise RuntimeError("C7.5b frozen seed schedule drift")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed not in C7_STOCHASTIC_SEEDS:
        raise ValueError("seed must be in frozen sequence 0..63")
    if len(selected) != len(C7_STOCHASTIC_SEEDS):
        raise ValueError("selected source cohort must contain exactly 64 rows")
    if not all(isinstance(record, NormalizedTraceRecord) for record in selected):
        raise TypeError("selected source cohort must contain NormalizedTraceRecord values")
    if len({record.record_id for record in selected}) != len(selected):
        raise ValueError("selected source cohort must contain unique record IDs")
    return selected[seed]


def validate_seed_record_binding(
    selected: Sequence[NormalizedTraceRecord], *, seed: int, record: NormalizedTraceRecord
) -> None:
    if not isinstance(record, NormalizedTraceRecord):
        raise TypeError("record must be NormalizedTraceRecord")
    expected = source_record_for_seed(selected, seed)
    if record != expected:
        raise ValueError("source record does not match the frozen same-index record for this seed")


def validate_result_rows_against_frozen_mapping(
    rows: Sequence[C75ProgramRow], selected: Sequence[NormalizedTraceRecord]
) -> None:
    if not rows:
        raise ValueError("result rows must be non-empty")
    expected = {
        seed: source_record_for_seed(selected, seed).record_id for seed in C7_STOCHASTIC_SEEDS
    }
    for row in rows:
        if not isinstance(row, C75ProgramRow):
            raise TypeError("rows must contain C75ProgramRow values")
        if row.seed not in expected:
            raise ValueError("result row seed escaped frozen schedule")
        if row.source_record_id != expected[row.seed]:
            raise ValueError("result row violates the frozen seed-to-source-record mapping")


def _validated_manifest_mapping(
    manifests: Mapping[PolicyID, object], *, hardware_id: str
) -> Mapping[PolicyID, C7ExperimentManifest]:
    if hardware_id not in C75B_HARDWARE_STRATA:
        raise ValueError("hardware_id escaped frozen strata")
    if set(manifests) != set(PolicyID):
        raise ValueError("paired manifests must contain B0 through B4 exactly")
    typed: dict[PolicyID, C7ExperimentManifest] = {}
    for policy_id in PolicyID:
        manifest = manifests[policy_id]
        if not isinstance(manifest, C7ExperimentManifest):
            raise TypeError("paired manifests must contain C7ExperimentManifest values")
        if manifest.policy_id is not policy_id:
            raise ValueError("manifest key/policy_id mismatch")
        if manifest.hardware_id != hardware_id:
            raise ValueError("manifest hardware does not match requested result stratum")
        typed[policy_id] = manifest
    return typed


def cell_id_for_case(case: C72ProgramCase) -> str:
    if not isinstance(case, C72ProgramCase):
        raise TypeError("case must be C72ProgramCase")
    params = dict(parameters_for_case(case))
    if case.series is ExperimentSeries.P1_DEEP_REUSE:
        return p1_cell_id(
            (
                int(params["session_depth"]),
                float(params["reusable_prefix_fraction"]),
                int(params["worker_count"]),
            )
        )
    if case.series is ExperimentSeries.P4_FANOUT_SHARED_PREFIX:
        return p4_cell_id(
            (
                int(params["fanout_width"]),
                float(params["shared_prefix_fraction"]),
            )
        )
    if case.series is ExperimentSeries.P7_STATELESS_OVERHEAD:
        return p7_cell_id(int(params["worker_count"]))
    raise ValueError("C7.5b stage2 contract supports only P1/P4/P7")


def summarize_verified_paired_result(
    *,
    case: C72ProgramCase,
    paired: C72PairedResult,
    manifests: Mapping[PolicyID, object],
    hardware_id: str,
    seed: int,
    selected_source_records: Sequence[NormalizedTraceRecord],
    source_record: NormalizedTraceRecord,
    cell_id: str | None = None,
) -> tuple[C75ProgramRow, ...]:
    validate_seed_record_binding(selected_source_records, seed=seed, record=source_record)
    typed_manifests = _validated_manifest_mapping(manifests, hardware_id=hardware_id)
    if paired.program_case_fingerprint != case.fingerprint:
        raise ValueError("paired result does not bind to the Program case")
    if paired.series is not case.series:
        raise ValueError("paired result series does not bind to the Program case")
    if tuple(result.policy_id for result in paired.policy_results) != tuple(PolicyID):
        raise ValueError("paired result must contain B0 through B4 in canonical order")
    for result in paired.policy_results:
        manifest = typed_manifests[result.policy_id]
        if result.manifest_fingerprint != manifest.fingerprint:
            raise ValueError("paired result manifest fingerprint does not bind to the supplied hardware manifest")
    realized_cell_id = cell_id_for_case(case)
    if cell_id is not None and cell_id != realized_cell_id:
        raise ValueError("supplied cell_id does not match the realized Program surface cell")
    rows = summarize_paired_result(
        case=case,
        paired=paired,
        hardware_id=hardware_id,
        seed=seed,
        source_record_id=source_record.record_id,
        cell_id=realized_cell_id,
    )
    if any(row.hardware_id != hardware_id for row in rows):
        raise AssertionError("summarized hardware label drift")
    if any(row.source_record_id != source_record.record_id for row in rows):
        raise AssertionError("summarized source-record binding drift")
    if any(row.cell_id != realized_cell_id for row in rows):
        raise AssertionError("summarized surface-cell binding drift")
    return rows


def stage2_contract_identity() -> dict[str, object]:
    return {
        "schema": C75B_STAGE2_CONTRACT_SCHEMA,
        "seed_schedule": list(C7_STOCHASTIC_SEEDS),
        "source_selection_fingerprint": C75_SOURCE_SELECTION_FINGERPRINT,
        "seed_record_rule": "seed N must use selected_source_records[N] exactly",
        "hardware_binding_rule": "row hardware must equal manifest.hardware_id whose fingerprint is present in C72 paired result",
        "surface_cell_binding_rule": "row cell_id is derived from the realized C72 Program case; caller labels cannot redefine it",
        "comparative_execution": "NOT_RUN",
    }
