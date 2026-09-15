from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from continuity.entities import AttemptAuthority, ReconcileOutcome, StateLifecycle
from experiments.c7_protocol import (
    C7_BOOTSTRAP_RESAMPLES,
    C7_BOOTSTRAP_SEED,
    C7_PROTOCOL_FINGERPRINT,
    C7_STOCHASTIC_SEEDS,
    EfficiencyEligibility,
    ExperimentSeries,
    ParameterSource,
    WorkloadClass,
)
from experiments.c72_routing_reuse import (
    C72PairedResult,
    C72ProgramCase,
    C72ProgramTopology,
    C72OperationCase,
    build_paired_manifests,
)
from experiments.c73c_protocol import bootstrap_resample_indices, percentile_95_interval
from experiments.c75_g2_protocol import (
    C75_AUGMENTATION_SCHEMA,
    C75_C72_ADMISSIBLE_DATASET_FINGERPRINT,
    C75_H4_PRIMARY_COMPARATORS,
    C75_H5_DECISION,
    C75_H6_DECISION,
    C75_P1_REUSE_FRACTIONS,
    C75_P1_SESSION_DEPTHS,
    C75_P4_FANOUT_WIDTHS,
    C75_P4_SHARED_FRACTIONS,
    C75_P4_WORKER_COUNT,
    C75_P7_ARRIVAL_INTENSITY,
    C75_PROTOCOL_FINGERPRINT,
    C75_SOURCE_SELECTION_FINGERPRINT,
    C75_WORKER_CAPACITY,
    C75_WORKER_COUNTS,
    C75G2Decision,
    adjudicate_g2,
    augmentation_fingerprint,
    p1_cells_adjacent,
    p4_cells_adjacent,
    p4_state_worker_index,
    p4_worker_queue_depth,
    reusable_tokens,
    session_preferred_worker_index,
    state_worker_index,
    worker_queue_depth,
)
from experiments.trace_workload import NormalizedTraceRecord
from simulator.policies import PolicyID, PolicyObservation, WorkerObservation


C75B_EXECUTOR_SCHEMA = "cadi.c7.5b.g2-executor.v1"
C75B_PROGRAM_ROW_SCHEMA = "cadi.c7.5b.program-row.v1"
C75B_RESULT_SCHEMA = "cadi.c7.5b.g2-result.v1"
C75B_BASE_COMMIT = "fcee10c5c647c9dfe11c7930987e331222fe124d"
C75B_FROZEN_PROTOCOL_FINGERPRINT = (
    "318c415b9531001fc4637b7de6f21cd13ffb33a06d74444fb41c1ace3709ae41"
)
C75B_FROZEN_SOURCE_SELECTION_FINGERPRINT = (
    "bebaf2e535b78177cb3ef2c39fddd9e23180c4aecb3e06deac9390d29b190fe4"
)
C75B_HARDWARE_STRATA = ("a100-80gb", "h100-80gb")
C75B_COMPARATIVE_EXECUTION = "NOT_RUN"
C75B_H4_SUPPORTED = "SUPPORTED_WITHIN_DECLARED_PHASE_SPACE"
C75B_H4_NOT_SUPPORTED = "NOT_SUPPORTED_WITHIN_DECLARED_PHASE_SPACE"
C75B_H7_SUPPORTED = "SUPPORTED_WITHIN_DECLARED_PHASE_SPACE"
C75B_H7_NOT_SUPPORTED = "NOT_SUPPORTED_WITHIN_DECLARED_PHASE_SPACE"
C75B_BOOTSTRAP_INTERVAL = "percentile-95"

if C75_PROTOCOL_FINGERPRINT != C75B_FROZEN_PROTOCOL_FINGERPRINT:
    raise RuntimeError("C7.5b frozen C7.5a protocol fingerprint drift")
if C75_SOURCE_SELECTION_FINGERPRINT != C75B_FROZEN_SOURCE_SELECTION_FINGERPRINT:
    raise RuntimeError("C7.5b frozen source-selection fingerprint drift")
if C7_PROTOCOL_FINGERPRINT != "706e0d5fff362a1eda8c906b957c914251c6e7949bac6be3c2c143ae21625474":
    raise RuntimeError("C7.5b frozen C7.1 protocol fingerprint drift")
if C7_STOCHASTIC_SEEDS != tuple(range(64)):
    raise RuntimeError("C7.5b requires exactly seeds 0..63")
if C7_BOOTSTRAP_RESAMPLES != 10_000 or C7_BOOTSTRAP_SEED != 20260911:
    raise RuntimeError("C7.5b bootstrap contract drift")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fp(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _source_record(record: NormalizedTraceRecord) -> tuple[str, int, int]:
    if not isinstance(record, NormalizedTraceRecord):
        raise TypeError("record must be NormalizedTraceRecord")
    if record.input_tokens is None or record.output_tokens is None:
        raise ValueError("C7.5b source row requires input/output token counts")
    input_tokens = record.input_tokens
    output_tokens = record.output_tokens
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
        raise TypeError("input_tokens must be int")
    if not isinstance(output_tokens, int) or isinstance(output_tokens, bool):
        raise TypeError("output_tokens must be int")
    if input_tokens <= 0 or input_tokens % 4:
        raise ValueError("C7.5b source input_tokens must be positive and divisible by four")
    if output_tokens <= 0 or input_tokens + output_tokens > 4096:
        raise ValueError("C7.5b source row escaped the frozen C6 domain")
    return record.record_id, input_tokens, output_tokens


def _workers_p1(seed: int, worker_count: int, operation_ordinal: int) -> tuple[WorkerObservation, ...]:
    return tuple(
        WorkerObservation(
            f"w{index:02d}",
            True,
            C75_WORKER_CAPACITY,
            0,
            worker_queue_depth(seed, index, worker_count, operation_ordinal=operation_ordinal),
        )
        for index in range(worker_count)
    )


def _workers_p4(seed: int, branch_ordinal: int) -> tuple[WorkerObservation, ...]:
    return tuple(
        WorkerObservation(
            f"w{index:02d}",
            True,
            C75_WORKER_CAPACITY,
            0,
            p4_worker_queue_depth(seed, index, branch_ordinal=branch_ordinal),
        )
        for index in range(C75_P4_WORKER_COUNT)
    )


def _workers_p7(seed: int, worker_count: int) -> tuple[WorkerObservation, ...]:
    return tuple(
        WorkerObservation(
            f"w{index:02d}",
            True,
            C75_WORKER_CAPACITY,
            0,
            worker_queue_depth(seed, index, worker_count, operation_ordinal=0),
        )
        for index in range(worker_count)
    )


def _stateful_observation(
    *,
    program_id: str,
    session_id: str,
    request_id: str,
    continuation_id: str,
    attempt_id: str,
    workers: tuple[WorkerObservation, ...],
    session_preferred_location: str,
    state_id: str | None,
    state_location: str | None,
    producer_attempt_id: str | None,
) -> PolicyObservation:
    has_state = state_id is not None
    if has_state != (state_location is not None):
        raise ValueError("state_id and state_location must be jointly present or absent")
    return PolicyObservation(
        request_id=request_id,
        workers=workers,
        attempt_id=attempt_id,
        attempt_authority=AttemptAuthority.CURRENT.name,
        session_id=session_id,
        session_preferred_location=session_preferred_location,
        continuation_id=continuation_id,
        continuation_ancestry=(),
        state_candidate_key=(f"candidate:{state_id}" if has_state else None),
        exact_state_id=state_id,
        state_locations=((state_location,) if state_location is not None else ()),
        state_provenance=(),
        producer_attempt_id=producer_attempt_id,
        binding_id=None,
        binding_epoch=None,
        evidence_authority=("AUTHORITATIVE" if has_state else None),
        evidence_status=("VALID" if has_state else None),
        evidence_freshness=(0.0 if has_state else None),
        program_id=program_id,
        state_lifecycle=(StateLifecycle.ACTIVE.name if has_state else None),
        reconciliation=(ReconcileOutcome.MATCHED.name if has_state else None),
    )


def build_p1_program_case(
    record: NormalizedTraceRecord,
    *,
    seed: int,
    session_depth: int,
    reusable_prefix_fraction: float,
    worker_count: int,
) -> C72ProgramCase:
    record_id, input_tokens, output_tokens = _source_record(record)
    if seed not in C7_STOCHASTIC_SEEDS:
        raise ValueError("seed must be in 0..63")
    if session_depth not in C75_P1_SESSION_DEPTHS:
        raise ValueError("session_depth escaped frozen P1 axis")
    if reusable_prefix_fraction not in C75_P1_REUSE_FRACTIONS:
        raise ValueError("reusable_prefix_fraction escaped frozen P1 axis")
    if worker_count not in C75_WORKER_COUNTS:
        raise ValueError("worker_count escaped frozen P1 axis")

    program_id = f"c75:p1:{record_id}:s{seed}:d{session_depth}:r{reusable_prefix_fraction:g}:w{worker_count}"
    session_id = f"session:{program_id}"
    session_worker = f"w{session_preferred_worker_index(seed, worker_count):02d}"
    reuse = reusable_tokens(input_tokens, reusable_prefix_fraction)
    operations: list[C72OperationCase] = []
    prior_attempt: str | None = None
    for ordinal in range(session_depth):
        request_id = f"request:{program_id}:{ordinal:02d}"
        attempt_id = f"attempt:{program_id}:{ordinal:02d}"
        state_id = None
        state_location = None
        eligible = 0
        producer = None
        if ordinal > 0 and reuse > 0:
            state_id = f"state:{program_id}:{ordinal:02d}"
            state_location = f"w{state_worker_index(seed, worker_count, operation_ordinal=ordinal):02d}"
            eligible = reuse
            producer = prior_attempt
        observation = _stateful_observation(
            program_id=program_id,
            session_id=session_id,
            request_id=request_id,
            continuation_id=f"continuation:{program_id}:{ordinal:02d}",
            attempt_id=attempt_id,
            workers=_workers_p1(seed, worker_count, ordinal),
            session_preferred_location=session_worker,
            state_id=state_id,
            state_location=state_location,
            producer_attempt_id=producer,
        )
        operations.append(
            C72OperationCase(
                f"op:{ordinal:02d}",
                observation,
                input_tokens,
                output_tokens,
                eligible,
            )
        )
        prior_attempt = attempt_id
    return C72ProgramCase(
        program_id=program_id,
        series=ExperimentSeries.P1_DEEP_REUSE,
        topology=C72ProgramTopology.SERIAL,
        operations=tuple(operations),
    )


def build_p4_program_case(
    record: NormalizedTraceRecord,
    *,
    seed: int,
    fanout_width: int,
    shared_prefix_fraction: float,
) -> C72ProgramCase:
    record_id, input_tokens, output_tokens = _source_record(record)
    if seed not in C7_STOCHASTIC_SEEDS:
        raise ValueError("seed must be in 0..63")
    if fanout_width not in C75_P4_FANOUT_WIDTHS:
        raise ValueError("fanout_width escaped frozen P4 axis")
    if shared_prefix_fraction not in C75_P4_SHARED_FRACTIONS:
        raise ValueError("shared_prefix_fraction escaped frozen P4 axis")

    program_id = f"c75:p4:{record_id}:s{seed}:f{fanout_width}:r{shared_prefix_fraction:g}"
    session_id = f"session:{program_id}"
    session_worker = f"w{session_preferred_worker_index(seed, C75_P4_WORKER_COUNT):02d}"
    state_id = f"state:{program_id}:shared"
    state_location = f"w{p4_state_worker_index(seed):02d}"
    reuse = reusable_tokens(input_tokens, shared_prefix_fraction)
    operations: list[C72OperationCase] = []
    for ordinal in range(fanout_width):
        request_id = f"request:{program_id}:{ordinal:02d}"
        attempt_id = f"attempt:{program_id}:{ordinal:02d}"
        observation = _stateful_observation(
            program_id=program_id,
            session_id=session_id,
            request_id=request_id,
            continuation_id=f"continuation:{program_id}:{ordinal:02d}",
            attempt_id=attempt_id,
            workers=_workers_p4(seed, ordinal),
            session_preferred_location=session_worker,
            state_id=state_id,
            state_location=state_location,
            producer_attempt_id=f"attempt:{program_id}:producer",
        )
        operations.append(
            C72OperationCase(
                f"branch:{ordinal:02d}",
                observation,
                input_tokens,
                output_tokens,
                reuse,
            )
        )
    return C72ProgramCase(
        program_id=program_id,
        series=ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
        topology=C72ProgramTopology.FANOUT,
        operations=tuple(operations),
    )


def build_p7_program_case(
    record: NormalizedTraceRecord,
    *,
    seed: int,
    worker_count: int,
) -> C72ProgramCase:
    record_id, input_tokens, output_tokens = _source_record(record)
    if seed not in C7_STOCHASTIC_SEEDS:
        raise ValueError("seed must be in 0..63")
    if worker_count not in C75_WORKER_COUNTS:
        raise ValueError("worker_count escaped frozen P7 axis")
    program_id = f"c75:p7:{record_id}:s{seed}:w{worker_count}"
    observation = PolicyObservation(
        request_id=f"request:{program_id}:00",
        workers=_workers_p7(seed, worker_count),
        attempt_id=f"attempt:{program_id}:00",
        attempt_authority=AttemptAuthority.CURRENT.name,
        session_id=f"session:{program_id}",
        session_preferred_location=None,
        continuation_id=f"continuation:{program_id}:00",
        continuation_ancestry=(),
        state_candidate_key=None,
        exact_state_id=None,
        state_locations=(),
        state_provenance=(),
        producer_attempt_id=None,
        binding_id=None,
        binding_epoch=None,
        evidence_authority=None,
        evidence_status=None,
        evidence_freshness=None,
        program_id=program_id,
        state_lifecycle=None,
        reconciliation=None,
    )
    return C72ProgramCase(
        program_id=program_id,
        series=ExperimentSeries.P7_STATELESS_OVERHEAD,
        topology=C72ProgramTopology.SERIAL,
        operations=(C72OperationCase("op:00", observation, input_tokens, output_tokens, 0),),
    )


def parameters_for_case(case: C72ProgramCase) -> tuple[tuple[str, int | float], ...]:
    if case.series is ExperimentSeries.P1_DEEP_REUSE:
        depth = len(case.operations)
        if depth not in C75_P1_SESSION_DEPTHS:
            raise ValueError("P1 case depth escaped frozen axis")
        eligible = case.operations[1].eligible_reuse_tokens if depth > 1 else 0
        input_tokens = case.operations[1].input_tokens if depth > 1 else case.operations[0].input_tokens
        fraction = 0.0 if input_tokens == 0 else eligible / input_tokens
        return (
            ("reusable_prefix_fraction", fraction),
            ("session_depth", depth),
            ("worker_count", case.worker_count),
        )
    if case.series is ExperimentSeries.P4_FANOUT_SHARED_PREFIX:
        fraction = case.operations[0].eligible_reuse_tokens / case.operations[0].input_tokens
        return (
            ("fanout_width", len(case.operations)),
            ("shared_prefix_fraction", fraction),
            ("worker_count", case.worker_count),
        )
    if case.series is ExperimentSeries.P7_STATELESS_OVERHEAD:
        return (
            ("arrival_intensity", C75_P7_ARRIVAL_INTENSITY),
            ("worker_count", case.worker_count),
        )
    raise ValueError("case series is outside C7.5b")


def build_manifests_for_case(
    case: C72ProgramCase,
    record: NormalizedTraceRecord,
    *,
    seed: int,
    hardware_id: str,
    execution_git_sha: str,
) -> Mapping[PolicyID, object]:
    record_id, _, _ = _source_record(record)
    parameters = parameters_for_case(case)
    augmentation = augmentation_fingerprint(
        seed=seed,
        series=case.series,
        source_record_id=record_id,
        program_case_fingerprint=case.fingerprint,
        parameters=parameters,
    )
    return build_paired_manifests(
        experiment_id=f"c75b:{case.series.value}:{case.fingerprint[:16]}:{hardware_id}",
        git_commit=execution_git_sha,
        series=case.series,
        workload_class=WorkloadClass.TRACE_AUGMENTED,
        hardware_id=hardware_id,
        program_objective="all required operations complete",
        seed=seed,
        source_dataset_fingerprint=C75_C72_ADMISSIBLE_DATASET_FINGERPRINT,
        augmentation_fingerprint=augmentation,
        parameters=parameters,
        parameter_sources=tuple((name, ParameterSource.P_SRC4) for name, _ in parameters),
    )


class C75AlwaysValidAuthority:
    def attempt_current(self, request_id: str, attempt_id: str) -> bool:
        return bool(request_id and attempt_id)

    def state_compatible(
        self,
        state_id: str,
        *,
        program_id: str,
        session_id: str,
        continuation_id: str,
        request_id: str,
        attempt_id: str,
    ) -> bool:
        return all((state_id, program_id, session_id, continuation_id, request_id, attempt_id))


@dataclass(frozen=True, slots=True)
class C75ProgramRow:
    hardware_id: str
    series: ExperimentSeries
    cell_id: str
    seed: int
    source_record_id: str
    program_case_fingerprint: str
    paired_result_fingerprint: str
    policy_id: PolicyID
    manifest_fingerprint: str
    total_input_tokens: int
    eligible_reuse_opportunities: int
    consumed_reuse_opportunities: int
    eligible_reuse_tokens: int
    consumed_reuse_tokens: int
    semantic_violation_count: int
    efficiency_eligibility: EfficiencyEligibility
    recomputation_ratio: float
    state_reuse_ratio: float
    state_reuse_token_ratio: float
    cold_continuation_rate: float
    program_completion_time_seconds: float | None
    fanout_completion_time_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"schema": C75B_PROGRAM_ROW_SCHEMA}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, (PolicyID, ExperimentSeries, EfficiencyEligibility)):
                result[name] = value.value
            else:
                result[name] = value
        return result


def summarize_paired_result(
    *,
    case: C72ProgramCase,
    paired: C72PairedResult,
    hardware_id: str,
    seed: int,
    source_record_id: str,
    cell_id: str,
) -> tuple[C75ProgramRow, ...]:
    if paired.program_case_fingerprint != case.fingerprint:
        raise ValueError("paired result does not bind to Program case")
    total_input = sum(operation.input_tokens for operation in case.operations)
    rows = []
    for result in paired.policy_results:
        rows.append(
            C75ProgramRow(
                hardware_id=hardware_id,
                series=case.series,
                cell_id=cell_id,
                seed=seed,
                source_record_id=source_record_id,
                program_case_fingerprint=case.fingerprint,
                paired_result_fingerprint=paired.fingerprint,
                policy_id=result.policy_id,
                manifest_fingerprint=result.manifest_fingerprint,
                total_input_tokens=total_input,
                eligible_reuse_opportunities=result.eligible_reuse_opportunities,
                consumed_reuse_opportunities=result.consumed_reuse_opportunities,
                eligible_reuse_tokens=result.eligible_reuse_tokens,
                consumed_reuse_tokens=result.consumed_reuse_tokens,
                semantic_violation_count=result.semantic_violation_count,
                efficiency_eligibility=result.efficiency_eligibility,
                recomputation_ratio=result.recomputation_ratio,
                state_reuse_ratio=result.state_reuse_ratio,
                state_reuse_token_ratio=result.state_reuse_token_ratio,
                cold_continuation_rate=result.cold_continuation_rate,
                program_completion_time_seconds=result.program_completion_time_seconds,
                fanout_completion_time_seconds=result.fanout_completion_time_seconds,
            )
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class RatioComponents:
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        for value, name in ((self.numerator, "numerator"), (self.denominator, "denominator")):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.numerator > self.denominator:
            raise ValueError("ratio numerator cannot exceed denominator")

    @property
    def value(self) -> float | None:
        return None if self.denominator == 0 else self.numerator / self.denominator


def recomputation_components(row: C75ProgramRow) -> RatioComponents:
    if not isinstance(row, C75ProgramRow):
        raise TypeError("row must be C75ProgramRow")
    numerator = row.eligible_reuse_tokens - row.consumed_reuse_tokens
    return RatioComponents(numerator, row.total_input_tokens)


def _aggregate_ratio(items: Iterable[RatioComponents]) -> float | None:
    values = tuple(items)
    numerator = sum(item.numerator for item in values)
    denominator = sum(item.denominator for item in values)
    return None if denominator == 0 else numerator / denominator


@lru_cache(maxsize=None)
def _bootstrap_indices(sample_size: int, resample_index: int) -> tuple[int, ...]:
    return bootstrap_resample_indices(sample_size, resample_index)


@dataclass(frozen=True, slots=True)
class C75PairedComparison:
    point: float
    ci95: tuple[float, float]
    favorable: bool

    def to_dict(self) -> dict[str, Any]:
        return {"point": self.point, "ci95": list(self.ci95), "favorable": self.favorable}


def paired_ratio_difference(
    b4: Sequence[RatioComponents], baseline: Sequence[RatioComponents]
) -> C75PairedComparison:
    if not b4 or len(b4) != len(baseline):
        raise ValueError("paired ratio comparison requires equal non-empty sequences")
    b4_value = _aggregate_ratio(b4)
    baseline_value = _aggregate_ratio(baseline)
    if b4_value is None or baseline_value is None:
        raise ValueError("paired ratio comparison has zero aggregate denominator")
    diffs: list[float] = []
    for resample_index in range(C7_BOOTSTRAP_RESAMPLES):
        indices = _bootstrap_indices(len(b4), resample_index)
        b4_resampled = _aggregate_ratio(tuple(b4[index] for index in indices))
        baseline_resampled = _aggregate_ratio(tuple(baseline[index] for index in indices))
        if b4_resampled is None or baseline_resampled is None:
            raise ValueError("bootstrap resample has zero ratio denominator")
        diffs.append(b4_resampled - baseline_resampled)
    interval = percentile_95_interval(diffs)
    point = b4_value - baseline_value
    return C75PairedComparison(point, interval, interval[1] < 0.0)


def _finite_positive(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def paired_timing_benefit(
    b4_seconds: Sequence[float], baseline_seconds: Sequence[float]
) -> tuple[C75PairedComparison, float]:
    if not b4_seconds or len(b4_seconds) != len(baseline_seconds):
        raise ValueError("paired timing comparison requires equal non-empty sequences")
    b4 = tuple(_finite_positive(value, "b4_seconds") for value in b4_seconds)
    baseline = tuple(_finite_positive(value, "baseline_seconds") for value in baseline_seconds)
    baseline_sum = sum(baseline)
    point = (baseline_sum - sum(b4)) / baseline_sum
    benefits = tuple((base - candidate) / base for base, candidate in zip(baseline, b4, strict=True))
    boot: list[float] = []
    for resample_index in range(C7_BOOTSTRAP_RESAMPLES):
        indices = _bootstrap_indices(len(b4), resample_index)
        rb = sum(baseline[index] for index in indices)
        rc = sum(b4[index] for index in indices)
        if rb <= 0:
            raise ValueError("bootstrap timing denominator must be positive")
        boot.append((rb - rc) / rb)
    interval = percentile_95_interval(boot)
    return C75PairedComparison(point, interval, interval[0] > 0.0), float(median(benefits))


def _rows_by_seed(rows: Sequence[C75ProgramRow], policy_id: PolicyID) -> tuple[C75ProgramRow, ...]:
    selected = tuple(sorted((row for row in rows if row.policy_id is policy_id), key=lambda row: row.seed))
    if tuple(row.seed for row in selected) != C7_STOCHASTIC_SEEDS:
        raise ValueError("comparison rows must cover exactly seeds 0..63")
    if any(
        row.efficiency_eligibility is not EfficiencyEligibility.ELIGIBLE
        or row.semantic_violation_count != 0
        for row in selected
    ):
        raise ValueError("semantic-invalid Program row cannot enter efficiency comparison")
    return selected


def verify_non_timing_hardware_invariance(
    rows: Sequence[C75ProgramRow], policy_ids: Sequence[PolicyID]
) -> None:
    grouped: dict[tuple[str, PolicyID], dict[str, tuple[Any, ...]]] = defaultdict(dict)
    for row in rows:
        if row.policy_id not in policy_ids:
            continue
        key = (row.cell_id, row.policy_id)
        snapshot = (
            row.seed,
            row.total_input_tokens,
            row.eligible_reuse_opportunities,
            row.consumed_reuse_opportunities,
            row.eligible_reuse_tokens,
            row.consumed_reuse_tokens,
            row.semantic_violation_count,
            row.efficiency_eligibility.value,
            row.recomputation_ratio,
            row.state_reuse_ratio,
            row.state_reuse_token_ratio,
            row.cold_continuation_rate,
        )
        grouped[key].setdefault(row.hardware_id, tuple())
        grouped[key][row.hardware_id] = grouped[key][row.hardware_id] + (snapshot,)
    for key, by_hardware in grouped.items():
        if set(by_hardware) != set(C75B_HARDWARE_STRATA):
            raise ValueError(f"missing hardware stratum for {key}")
        left = tuple(sorted(by_hardware[C75B_HARDWARE_STRATA[0]]))
        right = tuple(sorted(by_hardware[C75B_HARDWARE_STRATA[1]]))
        if left != right:
            raise ValueError(f"non-timing hardware invariance failed for {key}")


def connected_support_components_p1(cells: Iterable[tuple[int, float, int]]) -> tuple[tuple[tuple[int, float, int], ...], ...]:
    remaining = set(cells)
    components: list[tuple[tuple[int, float, int], ...]] = []
    while remaining:
        start = min(remaining)
        queue = deque([start])
        remaining.remove(start)
        component = [start]
        while queue:
            current = queue.popleft()
            neighbors = sorted(candidate for candidate in remaining if p1_cells_adjacent(current, candidate))
            for neighbor in neighbors:
                remaining.remove(neighbor)
                queue.append(neighbor)
                component.append(neighbor)
        components.append(tuple(sorted(component)))
    return tuple(sorted(components, key=lambda component: component[0]))


def connected_support_components_p4(cells: Iterable[tuple[int, float]]) -> tuple[tuple[tuple[int, float], ...], ...]:
    remaining = set(cells)
    components: list[tuple[tuple[int, float], ...]] = []
    while remaining:
        start = min(remaining)
        queue = deque([start])
        remaining.remove(start)
        component = [start]
        while queue:
            current = queue.popleft()
            neighbors = sorted(candidate for candidate in remaining if p4_cells_adjacent(current, candidate))
            for neighbor in neighbors:
                remaining.remove(neighbor)
                queue.append(neighbor)
                component.append(neighbor)
        components.append(tuple(sorted(component)))
    return tuple(sorted(components, key=lambda component: component[0]))


@dataclass(frozen=True, slots=True)
class C75TimingCellEvidence:
    series: ExperimentSeries
    cell: tuple[int | float, ...]
    hardware_id: str
    comparison: C75PairedComparison
    median_program_benefit: float

    def __post_init__(self) -> None:
        if self.hardware_id not in C75B_HARDWARE_STRATA:
            raise ValueError("hardware_id escaped frozen strata")
        if not math.isfinite(self.median_program_benefit):
            raise ValueError("median_program_benefit must be finite")


def _axis_run_ok(items: Sequence[C75TimingCellEvidence]) -> bool:
    if len(items) < 3:
        return False
    return (
        all(item.comparison.favorable for item in items)
        and all(
            items[index].median_program_benefit <= items[index + 1].median_program_benefit
            for index in range(len(items) - 1)
        )
        and any(
            items[index].median_program_benefit < items[index + 1].median_program_benefit
            for index in range(len(items) - 1)
        )
    )


def h7_gradient_runs(
    evidence: Sequence[C75TimingCellEvidence],
) -> tuple[dict[str, Any], ...]:
    by_series_cell_hardware = {
        (item.series, item.cell, item.hardware_id): item for item in evidence
    }
    if len(by_series_cell_hardware) != len(evidence):
        raise ValueError("duplicate H7 cell/hardware evidence")
    runs: list[dict[str, Any]] = []

    def inspect(
        *,
        series: ExperimentSeries,
        axis_name: str,
        axis_index: int,
        axis_values: tuple[int | float, ...],
        fixed_indices: tuple[int, ...],
        cells: tuple[tuple[int | float, ...], ...],
    ) -> None:
        groups: dict[tuple[int | float, ...], dict[int | float, tuple[int | float, ...]]] = defaultdict(dict)
        for cell in cells:
            fixed = tuple(cell[index] for index in fixed_indices)
            groups[fixed][cell[axis_index]] = cell
        for fixed, by_axis in sorted(groups.items()):
            for start in range(len(axis_values)):
                for end in range(start + 3, len(axis_values) + 1):
                    levels = axis_values[start:end]
                    if not all(level in by_axis for level in levels):
                        continue
                    candidate_cells = tuple(by_axis[level] for level in levels)
                    by_hardware: dict[str, list[C75TimingCellEvidence]] = {}
                    valid = True
                    for hardware_id in C75B_HARDWARE_STRATA:
                        items = []
                        for cell in candidate_cells:
                            item = by_series_cell_hardware.get((series, cell, hardware_id))
                            if item is None:
                                valid = False
                                break
                            items.append(item)
                        if not valid or not _axis_run_ok(items):
                            valid = False
                            break
                        by_hardware[hardware_id] = items
                    if valid:
                        runs.append(
                            {
                                "series": series.value,
                                "axis": axis_name,
                                "fixed": list(fixed),
                                "levels": list(levels),
                                "cells": [list(cell) for cell in candidate_cells],
                                "hardware": {
                                    hardware_id: {
                                        "median_program_benefits": [
                                            item.median_program_benefit for item in by_hardware[hardware_id]
                                        ],
                                        "ci95": [list(item.comparison.ci95) for item in by_hardware[hardware_id]],
                                    }
                                    for hardware_id in C75B_HARDWARE_STRATA
                                },
                            }
                        )

    p1_cells = tuple(
        sorted(
            {
                item.cell
                for item in evidence
                if item.series is ExperimentSeries.P1_DEEP_REUSE
            }
        )
    )
    p4_cells = tuple(
        sorted(
            {
                item.cell
                for item in evidence
                if item.series is ExperimentSeries.P4_FANOUT_SHARED_PREFIX
            }
        )
    )
    inspect(
        series=ExperimentSeries.P1_DEEP_REUSE,
        axis_name="session_depth",
        axis_index=0,
        axis_values=tuple(C75_P1_SESSION_DEPTHS),
        fixed_indices=(1, 2),
        cells=p1_cells,
    )
    inspect(
        series=ExperimentSeries.P1_DEEP_REUSE,
        axis_name="reusable_prefix_fraction",
        axis_index=1,
        axis_values=tuple(C75_P1_REUSE_FRACTIONS),
        fixed_indices=(0, 2),
        cells=p1_cells,
    )
    inspect(
        series=ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
        axis_name="fanout_width",
        axis_index=0,
        axis_values=tuple(C75_P4_FANOUT_WIDTHS),
        fixed_indices=(1,),
        cells=p4_cells,
    )
    inspect(
        series=ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
        axis_name="shared_prefix_fraction",
        axis_index=1,
        axis_values=tuple(C75_P4_SHARED_FRACTIONS),
        fixed_indices=(0,),
        cells=p4_cells,
    )
    return tuple(runs)


def p7_negative_control_pass(rows: Sequence[C75ProgramRow]) -> bool:
    expected_policies = set(PolicyID)
    for hardware_id in C75B_HARDWARE_STRATA:
        for worker_count in C75_WORKER_COUNTS:
            cell_id = f"P7:w{worker_count}"
            cell_rows = [
                row for row in rows
                if row.hardware_id == hardware_id and row.cell_id == cell_id
            ]
            if {row.policy_id for row in cell_rows} != expected_policies:
                return False
            for policy_id in PolicyID:
                policy_rows = sorted(
                    (row for row in cell_rows if row.policy_id is policy_id),
                    key=lambda row: row.seed,
                )
                if tuple(row.seed for row in policy_rows) != C7_STOCHASTIC_SEEDS:
                    return False
                if any(
                    row.semantic_violation_count != 0
                    or row.efficiency_eligibility is not EfficiencyEligibility.ELIGIBLE
                    or row.state_reuse_ratio != 0.0
                    or row.recomputation_ratio != 0.0
                    or row.program_completion_time_seconds is None
                    for row in policy_rows
                ):
                    return False
            for seed in C7_STOCHASTIC_SEEDS:
                values = {
                    row.program_completion_time_seconds
                    for row in cell_rows
                    if row.seed == seed
                }
                if len(values) != 1:
                    return False
    return True


def preflight_payload() -> dict[str, Any]:
    return {
        "schema": C75B_EXECUTOR_SCHEMA,
        "base_commit": C75B_BASE_COMMIT,
        "c75_protocol_fingerprint": C75_PROTOCOL_FINGERPRINT,
        "source_selection_fingerprint": C75_SOURCE_SELECTION_FINGERPRINT,
        "augmentation_schema": C75_AUGMENTATION_SCHEMA,
        "hardware_strata": list(C75B_HARDWARE_STRATA),
        "p1_cell_count": len(C75_P1_SESSION_DEPTHS) * len(C75_P1_REUSE_FRACTIONS) * len(C75_WORKER_COUNTS),
        "p4_cell_count": len(C75_P4_FANOUT_WIDTHS) * len(C75_P4_SHARED_FRACTIONS),
        "p7_control_cell_count": len(C75_WORKER_COUNTS),
        "bootstrap_resamples": C7_BOOTSTRAP_RESAMPLES,
        "bootstrap_seed": C7_BOOTSTRAP_SEED,
        "bootstrap_interval": C75B_BOOTSTRAP_INTERVAL,
        "h4_primary_comparators": [policy.value for policy in C75_H4_PRIMARY_COMPARATORS],
        "h5_decision_imported": C75_H5_DECISION,
        "h6_decision_imported": C75_H6_DECISION,
        "result_schema": C75B_RESULT_SCHEMA,
        "comparative_execution": C75B_COMPARATIVE_EXECUTION,
    }


def main() -> None:
    print(_json(preflight_payload()))


if __name__ == "__main__":
    main()
