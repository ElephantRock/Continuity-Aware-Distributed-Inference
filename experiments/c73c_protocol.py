from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from continuity.entities import ContinuationLifecycle
from experiments.c7_protocol import (
    AXES,
    C7_BOOTSTRAP_RESAMPLES,
    C7_BOOTSTRAP_SEED,
    C7_CONVERGENCE_PREFIXES,
    C7_PROTOCOL_FINGERPRINT,
    C7_STOCHASTIC_SEEDS,
    C7_SUPPORTED_HARDWARE_IDS,
    C7ExperimentManifest,
    ExperimentSeries,
    ParameterSource,
    WorkloadClass,
)
from experiments.c73_retention_engine import (
    C73B_ENGINE_SCHEMA,
    RetentionEvent,
    RetentionEventKind,
    RetentionProgramCase,
    RetentionSessionSpec,
    RetentionStateSpec,
    RetentionPolicyResult,
    reference_working_set_bytes,
    tool_return_ttft_from_profile,
)
from experiments.c73_retention_protocol import (
    C73_DEFAULT_STATE_BYTES,
    C73_DEFAULT_STATE_TOKENS,
    C73_PRIMARY_TTL_SECONDS,
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    C73_STATE_SIZE_MAP_FINGERPRINT,
    C73_TTL_SENSITIVITY_SECONDS,
    C73RetentionManifest,
    CapacityOutcome,
    RetentionPolicyID,
    capacity_bytes,
)
from simulator.inference_cost_runtime import ValidatedRuntimeCostProfile
from simulator.policies import PolicyID


C73C_PROTOCOL_SCHEMA = "cadi.c7.3c.paired-retention-evaluation-protocol.v1"
C73C_BASE_COMMIT = "451cafddb21d667f6abb73f48477560c850cda20"
C73C_FROZEN_C71_FINGERPRINT = (
    "706e0d5fff362a1eda8c906b957c914251c6e7949bac6be3c2c143ae21625474"
)
C73C_FROZEN_C73A_FINGERPRINT = (
    "f6165cb9248f5f4292846942e797e5ddcd8d004027ad503b2a7de35210021379"
)
C73C_FROZEN_STATE_SIZE_MAP_FINGERPRINT = (
    "36bacaad6462ca3e5eeb2688040e4dcf0d829849158c5b5ad9bad3c58afe173b"
)
C73C_REUSE_CONTEXT_TOKENS = 1024
C73C_ZERO_QUEUE_COMPONENT = "ZERO_UNEVIDENCED_COMPONENT"
C73C_BOOTSTRAP_INTERVAL = "percentile-95"
C73C_INFEASIBLE_LABEL = "INFEASIBLE_FOR_PAIRED_RANKING"
C73C_INSUFFICIENT_DENOMINATOR_LABEL = "INSUFFICIENT_METRIC_DENOMINATOR"
C73C_ORACLE_TTL_LABEL = "ORACLE_TUNED_TTL_SENSITIVITY_UPPER_BOUND"
C73C_H5_NOT_SUPPORTED = "NOT_SUPPORTED_WITHIN_DECLARED_PHASE_SPACE"

if C7_PROTOCOL_FINGERPRINT != C73C_FROZEN_C71_FINGERPRINT:
    raise RuntimeError("C7.3c parent C7.1 fingerprint drift")
if C73_RETENTION_PROTOCOL_FINGERPRINT != C73C_FROZEN_C73A_FINGERPRINT:
    raise RuntimeError("C7.3c parent C7.3a fingerprint drift")
if C73_STATE_SIZE_MAP_FINGERPRINT != C73C_FROZEN_STATE_SIZE_MAP_FINGERPRINT:
    raise RuntimeError("C7.3c parent State-size-map fingerprint drift")
if C73_DEFAULT_STATE_TOKENS != AXES["state_tokens"].reference_value:
    raise RuntimeError("C7.3c State-token reference drift")
if C73C_REUSE_CONTEXT_TOKENS != AXES["recompute_tokens"].reference_value:
    raise RuntimeError("C7.3c reuse-context projection must use the frozen P5 reference point")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fp(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _axis_value(name: str, value: int | float) -> int | float:
    if value not in AXES[name].values:
        raise ValueError(f"{name} must be a frozen C7.1 axis value")
    return value


def _seed(seed: int) -> int:
    if not isinstance(seed, int) or isinstance(seed, bool) or seed not in C7_STOCHASTIC_SEEDS:
        raise ValueError("seed must be one of the frozen C7.1 stochastic seeds 0..63")
    return seed


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _finite_nonnegative(value: int | float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def _counter_uniform(*parts: object) -> float:
    payload = [C73C_PROTOCOL_SCHEMA, *parts]
    digest = hashlib.sha256(_json(payload).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def p2_tool_return_uniform(seed: int) -> float:
    return _counter_uniform("P2", _seed(seed), "target", "tool-return")


def p2_pressure_priority(seed: int, pressure_state_id: str) -> float:
    _nonempty(pressure_state_id, "pressure_state_id")
    return _counter_uniform(
        "P2", _seed(seed), pressure_state_id, "admission-priority"
    )


def p3_speculative_uniform(seed: int, branch_index: int) -> float:
    if not isinstance(branch_index, int) or isinstance(branch_index, bool) or branch_index < 0:
        raise ValueError("branch_index must be a non-negative integer")
    return _counter_uniform(
        "P3", _seed(seed), branch_index, "speculative-class"
    )


def p3_admission_priority(seed: int, branch_index: int) -> float:
    if not isinstance(branch_index, int) or isinstance(branch_index, bool) or branch_index < 0:
        raise ValueError("branch_index must be a non-negative integer")
    return _counter_uniform(
        "P3", _seed(seed), branch_index, "admission-priority"
    )


class C73CSurfaceID(str, Enum):
    P2_GAP_CACHE = "P2_GAP_CACHE"
    P2_GAP_RETURN = "P2_GAP_RETURN"
    P3_WIDTH_CACHE = "P3_WIDTH_CACHE"


@dataclass(frozen=True, slots=True)
class C73CPrimarySurface:
    surface_id: C73CSurfaceID
    series: ExperimentSeries
    x_axis: str
    y_axis: str
    fixed_axes: tuple[tuple[str, int | float], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id.value,
            "series": self.series.value,
            "x_axis": self.x_axis,
            "y_axis": self.y_axis,
            "fixed_axes": {name: value for name, value in self.fixed_axes},
            "x_values": list(AXES[self.x_axis].values),
            "y_values": list(AXES[self.y_axis].values),
        }


C73C_PRIMARY_SURFACES = (
    C73CPrimarySurface(
        C73CSurfaceID.P2_GAP_CACHE,
        ExperimentSeries.P2_TOOL_GAP_RETENTION,
        "tool_gap_seconds",
        "cache_capacity_ratio",
        (("tool_return_probability", AXES["tool_return_probability"].reference_value),),
    ),
    C73CPrimarySurface(
        C73CSurfaceID.P2_GAP_RETURN,
        ExperimentSeries.P2_TOOL_GAP_RETENTION,
        "tool_gap_seconds",
        "tool_return_probability",
        (("cache_capacity_ratio", AXES["cache_capacity_ratio"].reference_value),),
    ),
    C73CPrimarySurface(
        C73CSurfaceID.P3_WIDTH_CACHE,
        ExperimentSeries.P3_BRANCH_CACHE_PRESSURE,
        "branch_width",
        "cache_capacity_ratio",
        (("speculative_fraction", AXES["speculative_fraction"].reference_value),),
    ),
)
_SURFACE_BY_ID = {surface.surface_id: surface for surface in C73C_PRIMARY_SURFACES}


@dataclass(frozen=True, slots=True)
class C73CPrimaryCell:
    surface_id: C73CSurfaceID
    x_value: int | float
    y_value: int | float

    def __post_init__(self) -> None:
        if not isinstance(self.surface_id, C73CSurfaceID):
            raise TypeError("surface_id must be C73CSurfaceID")
        surface = _SURFACE_BY_ID[self.surface_id]
        _axis_value(surface.x_axis, self.x_value)
        _axis_value(surface.y_axis, self.y_value)

    @property
    def series(self) -> ExperimentSeries:
        return _SURFACE_BY_ID[self.surface_id].series

    @property
    def parameters(self) -> dict[str, int | float]:
        surface = _SURFACE_BY_ID[self.surface_id]
        result = dict(surface.fixed_axes)
        result[surface.x_axis] = self.x_value
        result[surface.y_axis] = self.y_value
        result["state_tokens"] = C73_DEFAULT_STATE_TOKENS
        return result

    @property
    def logical_cell_key(self) -> tuple[str, tuple[tuple[str, int | float], ...]]:
        return (
            self.series.value,
            tuple(sorted(self.parameters.items())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id.value,
            "series": self.series.value,
            "x_value": self.x_value,
            "y_value": self.y_value,
            "parameters": dict(sorted(self.parameters.items())),
            "logical_cell_key": [self.logical_cell_key[0], list(self.logical_cell_key[1])],
        }


def primary_surface_cells(surface_id: C73CSurfaceID) -> tuple[C73CPrimaryCell, ...]:
    if not isinstance(surface_id, C73CSurfaceID):
        raise TypeError("surface_id must be C73CSurfaceID")
    surface = _SURFACE_BY_ID[surface_id]
    return tuple(
        C73CPrimaryCell(surface_id, x, y)
        for x in AXES[surface.x_axis].values
        for y in AXES[surface.y_axis].values
    )


def primary_cells_adjacent(a: C73CPrimaryCell, b: C73CPrimaryCell) -> bool:
    if not isinstance(a, C73CPrimaryCell) or not isinstance(b, C73CPrimaryCell):
        raise TypeError("a and b must be C73CPrimaryCell")
    if a.surface_id is not b.surface_id or a.logical_cell_key == b.logical_cell_key:
        return False
    surface = _SURFACE_BY_ID[a.surface_id]
    x_values = AXES[surface.x_axis].values
    y_values = AXES[surface.y_axis].values
    ax = x_values.index(a.x_value)
    bx = x_values.index(b.x_value)
    ay = y_values.index(a.y_value)
    by = y_values.index(b.y_value)
    return (abs(ax - bx) == 1 and ay == by) or (
        ax == bx and abs(ay - by) == 1
    )


def bootstrap_resample_indices(
    sample_size: int, resample_index: int
) -> tuple[int, ...]:
    if not isinstance(sample_size, int) or isinstance(sample_size, bool) or sample_size <= 0:
        raise ValueError("sample_size must be a positive integer")
    if (
        not isinstance(resample_index, int)
        or isinstance(resample_index, bool)
        or not 0 <= resample_index < C7_BOOTSTRAP_RESAMPLES
    ):
        raise ValueError("resample_index is outside the frozen bootstrap range")
    return tuple(
        int(
            _counter_uniform(
                "bootstrap",
                C7_BOOTSTRAP_SEED,
                sample_size,
                resample_index,
                draw_index,
            )
            * sample_size
        )
        for draw_index in range(sample_size)
    )


def percentile_type7(values: Iterable[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("values must be non-empty")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must lie in [0,1]")
    if not all(math.isfinite(value) for value in ordered):
        raise ValueError("percentile values must be finite")
    if len(ordered) == 1:
        return ordered[0]
    h = (len(ordered) - 1) * probability
    lower = math.floor(h)
    upper = math.ceil(h)
    if lower == upper:
        return ordered[lower]
    weight = h - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def percentile_95_interval(values: Iterable[float]) -> tuple[float, float]:
    values = tuple(values)
    return (
        percentile_type7(values, 0.025),
        percentile_type7(values, 0.975),
    )


def _p2_program_case(
    *, tool_gap_seconds: float, tool_return_probability: float, seed: int
) -> RetentionProgramCase:
    gap = float(_axis_value("tool_gap_seconds", tool_gap_seconds))
    probability = float(
        _axis_value("tool_return_probability", tool_return_probability)
    )
    seed = _seed(seed)
    pressure_classes = {
        "pressure-active": ContinuationLifecycle.ACTIVE,
        "pressure-speculative-0": ContinuationLifecycle.SPECULATIVE,
        "pressure-speculative-1": ContinuationLifecycle.SPECULATIVE,
    }
    pressure_order = sorted(
        pressure_classes,
        key=lambda state_id: (p2_pressure_priority(seed, state_id), state_id),
    )
    admission_ordinal = {"target": 0}
    admission_ordinal.update(
        {state_id: index for index, state_id in enumerate(pressure_order, start=1)}
    )
    sessions = (
        RetentionSessionSpec("target-session"),
        RetentionSessionSpec("pressure-session-active"),
        RetentionSessionSpec("pressure-session-speculative-0"),
        RetentionSessionSpec("pressure-session-speculative-1"),
    )
    session_by_state = {
        "target": "target-session",
        "pressure-active": "pressure-session-active",
        "pressure-speculative-0": "pressure-session-speculative-0",
        "pressure-speculative-1": "pressure-session-speculative-1",
    }
    states = [
        RetentionStateSpec(
            "target",
            "target-session",
            admission_ordinal["target"],
            (ContinuationLifecycle.ACTIVE,),
        )
    ]
    for state_id in pressure_classes:
        states.append(
            RetentionStateSpec(
                state_id,
                session_by_state[state_id],
                admission_ordinal[state_id],
                (pressure_classes[state_id],),
            )
        )

    events: list[RetentionEvent] = [
        RetentionEvent("target-admit", 0.0, 0, RetentionEventKind.ADMIT, state_id="target"),
        RetentionEvent(
            "target-waiting",
            gap * 0.10,
            0,
            RetentionEventKind.DEPENDENTS,
            state_id="target",
            dependent_lifecycles=(ContinuationLifecycle.WAITING,),
        ),
    ]
    for slot, state_id in enumerate(pressure_order, start=1):
        events.append(
            RetentionEvent(
                f"{state_id}-admit",
                gap * (0.25 * slot),
                0,
                RetentionEventKind.ADMIT,
                state_id=state_id,
            )
        )

    returned = p2_tool_return_uniform(seed) < probability
    if returned:
        events.extend(
            (
                RetentionEvent(
                    "target-return-active",
                    gap,
                    0,
                    RetentionEventKind.DEPENDENTS,
                    state_id="target",
                    dependent_lifecycles=(ContinuationLifecycle.ACTIVE,),
                ),
                RetentionEvent(
                    "target-return-reuse",
                    gap,
                    0,
                    RetentionEventKind.REUSE,
                    state_id="target",
                    semantic_valid=True,
                ),
            )
        )
    else:
        events.extend(
            (
                RetentionEvent(
                    "target-no-return-terminal",
                    gap,
                    0,
                    RetentionEventKind.DEPENDENTS,
                    state_id="target",
                    dependent_lifecycles=(ContinuationLifecycle.TERMINAL,),
                ),
                RetentionEvent(
                    "target-session-end",
                    gap,
                    1,
                    RetentionEventKind.SESSION_STATUS,
                    session_id="target-session",
                    session_live=False,
                ),
            )
        )

    cleanup = gap + max(0.001, gap * 0.01)
    for ordinal, state_id in enumerate(sorted(pressure_classes)):
        events.append(
            RetentionEvent(
                f"{state_id}-terminal",
                cleanup,
                ordinal,
                RetentionEventKind.DEPENDENTS,
                state_id=state_id,
                dependent_lifecycles=(ContinuationLifecycle.TERMINAL,),
            )
        )
    for offset, state_id in enumerate(sorted(pressure_classes), start=10):
        events.append(
            RetentionEvent(
                f"{session_by_state[state_id]}-end",
                cleanup,
                offset,
                RetentionEventKind.SESSION_STATUS,
                session_id=session_by_state[state_id],
                session_live=False,
            )
        )
    if returned:
        events.extend(
            (
                RetentionEvent(
                    "target-cleanup-terminal",
                    cleanup,
                    20,
                    RetentionEventKind.DEPENDENTS,
                    state_id="target",
                    dependent_lifecycles=(ContinuationLifecycle.TERMINAL,),
                ),
                RetentionEvent(
                    "target-cleanup-session-end",
                    cleanup,
                    21,
                    RetentionEventKind.SESSION_STATUS,
                    session_id="target-session",
                    session_live=False,
                ),
            )
        )
    program_end = cleanup + max(0.001, gap * 0.01)
    return RetentionProgramCase(
        program_id=f"p2-seed-{seed}-gap-{gap:g}-return-{probability:g}",
        sessions=sessions,
        states=tuple(states),
        events=tuple(events),
        program_start_seconds=0.0,
        program_end_seconds=program_end,
    )


def _p3_program_case(
    *, branch_width: int, speculative_fraction: float, seed: int
) -> RetentionProgramCase:
    width = int(_axis_value("branch_width", branch_width))
    fraction = float(_axis_value("speculative_fraction", speculative_fraction))
    seed = _seed(seed)
    indices = tuple(range(width))
    lifecycle_by_index = {
        index: (
            ContinuationLifecycle.SPECULATIVE
            if p3_speculative_uniform(seed, index) < fraction
            else ContinuationLifecycle.WAITING
        )
        for index in indices
    }
    admission_order = sorted(
        indices,
        key=lambda index: (p3_admission_priority(seed, index), index),
    )
    admission_ordinal = {
        branch_index: ordinal
        for ordinal, branch_index in enumerate(admission_order)
    }
    states = tuple(
        RetentionStateSpec(
            f"branch-{index}",
            "branch-session",
            admission_ordinal[index],
            (lifecycle_by_index[index],),
        )
        for index in indices
    )
    events: list[RetentionEvent] = []
    for slot, index in enumerate(admission_order, start=1):
        events.append(
            RetentionEvent(
                f"branch-{index}-admit",
                0.5 * slot / (width + 1),
                0,
                RetentionEventKind.ADMIT,
                state_id=f"branch-{index}",
            )
        )
    resolution = 1.0
    for index in indices:
        if lifecycle_by_index[index] is ContinuationLifecycle.SPECULATIVE:
            events.append(
                RetentionEvent(
                    f"branch-{index}-discard",
                    resolution,
                    index,
                    RetentionEventKind.DEPENDENTS,
                    state_id=f"branch-{index}",
                    dependent_lifecycles=(ContinuationLifecycle.TERMINAL,),
                )
            )
        else:
            events.extend(
                (
                    RetentionEvent(
                        f"branch-{index}-activate",
                        resolution,
                        index,
                        RetentionEventKind.DEPENDENTS,
                        state_id=f"branch-{index}",
                        dependent_lifecycles=(ContinuationLifecycle.ACTIVE,),
                    ),
                    RetentionEvent(
                        f"branch-{index}-reuse",
                        resolution,
                        index,
                        RetentionEventKind.REUSE,
                        state_id=f"branch-{index}",
                        semantic_valid=True,
                    ),
                )
            )
    cleanup = 1.1
    for index in indices:
        if lifecycle_by_index[index] is not ContinuationLifecycle.SPECULATIVE:
            events.append(
                RetentionEvent(
                    f"branch-{index}-terminal",
                    cleanup,
                    index,
                    RetentionEventKind.DEPENDENTS,
                    state_id=f"branch-{index}",
                    dependent_lifecycles=(ContinuationLifecycle.TERMINAL,),
                )
            )
    events.append(
        RetentionEvent(
            "branch-session-end",
            cleanup,
            width + 1,
            RetentionEventKind.SESSION_STATUS,
            session_id="branch-session",
            session_live=False,
        )
    )
    return RetentionProgramCase(
        program_id=f"p3-seed-{seed}-width-{width}-spec-{fraction:g}",
        sessions=(RetentionSessionSpec("branch-session"),),
        states=states,
        events=tuple(events),
        program_start_seconds=0.0,
        program_end_seconds=1.2,
    )


def build_primary_program_case(
    cell: C73CPrimaryCell, *, seed: int
) -> RetentionProgramCase:
    if not isinstance(cell, C73CPrimaryCell):
        raise TypeError("cell must be C73CPrimaryCell")
    params = cell.parameters
    if cell.series is ExperimentSeries.P2_TOOL_GAP_RETENTION:
        return _p2_program_case(
            tool_gap_seconds=float(params["tool_gap_seconds"]),
            tool_return_probability=float(params["tool_return_probability"]),
            seed=seed,
        )
    if cell.series is ExperimentSeries.P3_BRANCH_CACHE_PRESSURE:
        return _p3_program_case(
            branch_width=int(params["branch_width"]),
            speculative_fraction=float(params["speculative_fraction"]),
            seed=seed,
        )
    raise AssertionError("unsupported C7.3c primary series")


def build_base_manifest(
    cell: C73CPrimaryCell,
    *,
    seed: int,
    hardware_id: str,
    execution_git_commit: str,
) -> C7ExperimentManifest:
    if not isinstance(cell, C73CPrimaryCell):
        raise TypeError("cell must be C73CPrimaryCell")
    if hardware_id not in C7_SUPPORTED_HARDWARE_IDS:
        raise ValueError("hardware_id is outside the accepted C6 runtime family")
    _nonempty(execution_git_commit, "execution_git_commit")
    params = tuple(sorted(cell.parameters.items()))
    return C7ExperimentManifest(
        experiment_id=(
            f"c73c-{cell.series.value.lower()}-"
            f"{_fp([cell.series.value, list(params)])[:16]}-seed-{_seed(seed)}"
        ),
        git_commit=execution_git_commit,
        protocol_fingerprint=C7_PROTOCOL_FINGERPRINT,
        series=cell.series,
        policy_id=PolicyID.B4,
        workload_class=WorkloadClass.SYNTHETIC_STRESS,
        hardware_id=hardware_id,
        program_objective="paired lifecycle-retention isolation under frozen C7.3c protocol",
        seed=seed,
        source_dataset_fingerprint=None,
        augmentation_fingerprint=None,
        parameters=params,
        parameter_sources=tuple(
            (name, ParameterSource.P_SRC4)
            for name, _ in params
        ),
    )


def validate_case_matches_base_manifest(
    case: RetentionProgramCase, base_manifest: C7ExperimentManifest
) -> None:
    if not isinstance(case, RetentionProgramCase):
        raise TypeError("case must be RetentionProgramCase")
    if not isinstance(base_manifest, C7ExperimentManifest):
        raise TypeError("base_manifest must be C7ExperimentManifest")
    if base_manifest.seed is None:
        raise ValueError("C7.3c synthetic base manifest requires a frozen seed")
    params = dict(base_manifest.parameters)
    if base_manifest.series is ExperimentSeries.P2_TOOL_GAP_RETENTION:
        expected = _p2_program_case(
            tool_gap_seconds=float(params["tool_gap_seconds"]),
            tool_return_probability=float(params["tool_return_probability"]),
            seed=base_manifest.seed,
        )
    elif base_manifest.series is ExperimentSeries.P3_BRANCH_CACHE_PRESSURE:
        expected = _p3_program_case(
            branch_width=int(params["branch_width"]),
            speculative_fraction=float(params["speculative_fraction"]),
            seed=base_manifest.seed,
        )
    else:
        raise ValueError("C7.3c base manifest must be P2 or P3")
    if case.fingerprint != expected.fingerprint:
        raise ValueError(
            "Program case does not match deterministic realization of the base C7 manifest"
        )


def build_retention_manifest(
    *,
    case: RetentionProgramCase,
    base_manifest: C7ExperimentManifest,
    policy_id: RetentionPolicyID,
    ttl_seconds: float | None = None,
) -> C73RetentionManifest:
    validate_case_matches_base_manifest(case, base_manifest)
    if base_manifest.policy_id is not PolicyID.B4:
        raise ValueError("C7.3c must hold the underlying routing/control policy at B4")
    if base_manifest.workload_class is not WorkloadClass.SYNTHETIC_STRESS:
        raise ValueError("C7.3c P2/P3 realization is synthetic stress")
    ratio = float(dict(base_manifest.parameters)["cache_capacity_ratio"])
    reference = reference_working_set_bytes(case)
    return C73RetentionManifest(
        base_c7_manifest_fingerprint=base_manifest.fingerprint,
        retention_protocol_fingerprint=C73_RETENTION_PROTOCOL_FINGERPRINT,
        retention_policy_id=policy_id,
        ttl_seconds=ttl_seconds,
        program_case_fingerprint=case.fingerprint,
        state_size_map_fingerprint=C73_STATE_SIZE_MAP_FINGERPRINT,
        state_tokens=C73_DEFAULT_STATE_TOKENS,
        state_bytes=C73_DEFAULT_STATE_BYTES,
        reference_working_set_bytes=reference,
        cache_capacity_ratio=ratio,
        capacity_bytes=capacity_bytes(
            reference_working_set_bytes=reference,
            cache_capacity_ratio=ratio,
        ),
    )


def retention_manifest_variants(
    *, case: RetentionProgramCase, base_manifest: C7ExperimentManifest
) -> tuple[C73RetentionManifest, ...]:
    manifests = [
        build_retention_manifest(
            case=case,
            base_manifest=base_manifest,
            policy_id=RetentionPolicyID.LRU,
        ),
        build_retention_manifest(
            case=case,
            base_manifest=base_manifest,
            policy_id=RetentionPolicyID.SESSION_PINNING,
        ),
        build_retention_manifest(
            case=case,
            base_manifest=base_manifest,
            policy_id=RetentionPolicyID.LIFECYCLE_B4,
        ),
    ]
    manifests.extend(
        build_retention_manifest(
            case=case,
            base_manifest=base_manifest,
            policy_id=RetentionPolicyID.FIXED_TTL,
            ttl_seconds=ttl,
        )
        for ttl in C73_TTL_SENSITIVITY_SECONDS
    )
    return tuple(manifests)


@dataclass(frozen=True, slots=True)
class RatioComponents:
    numerator: float
    denominator: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "numerator", _finite_nonnegative(self.numerator, "numerator")
        )
        object.__setattr__(
            self, "denominator", _finite_nonnegative(self.denominator, "denominator")
        )
        if self.numerator > self.denominator and self.denominator > 0:
            raise ValueError("ratio numerator cannot exceed denominator")

    @property
    def value(self) -> float | None:
        return None if self.denominator == 0.0 else self.numerator / self.denominator

    def to_dict(self) -> dict[str, float | None]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class C73CMetricComponents:
    useful_state_residency: RatioComponents
    wasted_state_residency: RatioComponents
    recomputation_ratio: RatioComponents
    cold_continuation_rate: RatioComponents

    def to_dict(self) -> dict[str, Any]:
        return {
            "USEFUL_STATE_RESIDENCY": self.useful_state_residency.to_dict(),
            "WASTED_STATE_RESIDENCY": self.wasted_state_residency.to_dict(),
            "RECOMPUTATION_RATIO": self.recomputation_ratio.to_dict(),
            "COLD_CONTINUATION_RATE": self.cold_continuation_rate.to_dict(),
        }


def metric_components(result: RetentionPolicyResult) -> C73CMetricComponents:
    if not isinstance(result, RetentionPolicyResult):
        raise TypeError("result must be RetentionPolicyResult")
    if result.semantic_rejection_count:
        raise ValueError("semantic rejection cannot be ranked as retention efficiency")
    total = result.total_classified_byte_seconds
    misses = result.eligible_reuse_opportunities - result.consumed_reuse_opportunities
    if misses < 0:
        raise AssertionError("consumed reuse exceeds eligible opportunities")
    return C73CMetricComponents(
        useful_state_residency=RatioComponents(result.useful_byte_seconds, total),
        wasted_state_residency=RatioComponents(result.wasted_byte_seconds, total),
        recomputation_ratio=RatioComponents(
            misses * C73C_REUSE_CONTEXT_TOKENS,
            result.eligible_reuse_opportunities * C73C_REUSE_CONTEXT_TOKENS,
        ),
        cold_continuation_rate=RatioComponents(
            misses,
            result.eligible_reuse_opportunities,
        ),
    )


def aggregate_ratio(components: Iterable[RatioComponents]) -> RatioComponents:
    values = tuple(components)
    return RatioComponents(
        sum(item.numerator for item in values),
        sum(item.denominator for item in values),
    )


def tool_return_ttft_projection(
    *,
    profile: ValidatedRuntimeCostProfile,
    consumed_reuse: bool,
) -> float:
    projection = tool_return_ttft_from_profile(
        profile=profile,
        expected_hardware_id=profile.hardware_id,
        resume_eligibility_seconds=0.0,
        service_start_seconds=0.0,
        full_context_tokens=C73C_REUSE_CONTEXT_TOKENS,
        consumed_reuse_tokens=(
            C73C_REUSE_CONTEXT_TOKENS if consumed_reuse else 0
        ),
    )
    return projection.ttft_seconds


@dataclass(frozen=True, slots=True)
class C73CPairedEvaluationProtocol:
    base_commit: str = C73C_BASE_COMMIT

    def __post_init__(self) -> None:
        if self.base_commit != C73C_BASE_COMMIT:
            raise ValueError("C7.3c1 base commit is frozen")
        if C73_PRIMARY_TTL_SECONDS != 5.0:
            raise RuntimeError("primary ordinary TTL drift")
        if C73_TTL_SENSITIVITY_SECONDS != AXES["tool_gap_seconds"].values:
            raise RuntimeError("TTL sensitivity grid drift")
        expected_surfaces = {
            (
                ExperimentSeries.P2_TOOL_GAP_RETENTION,
                "tool_gap_seconds",
                "cache_capacity_ratio",
            ),
            (
                ExperimentSeries.P2_TOOL_GAP_RETENTION,
                "tool_gap_seconds",
                "tool_return_probability",
            ),
            (
                ExperimentSeries.P3_BRANCH_CACHE_PRESSURE,
                "branch_width",
                "cache_capacity_ratio",
            ),
        }
        observed = {
            (surface.series, surface.x_axis, surface.y_axis)
            for surface in C73C_PRIMARY_SURFACES
        }
        if observed != expected_surfaces:
            raise RuntimeError("C7.3c compact primary surface drift")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C73C_PROTOCOL_SCHEMA,
            "base_commit": self.base_commit,
            "comparative_result_inspection": "NONE",
            "parent_identities": {
                "c7_1_protocol_fingerprint": C73C_FROZEN_C71_FINGERPRINT,
                "c7_3a_retention_protocol_fingerprint": C73C_FROZEN_C73A_FINGERPRINT,
                "c7_3a_state_size_map_fingerprint": C73C_FROZEN_STATE_SIZE_MAP_FINGERPRINT,
                "c7_3b_engine_schema": C73B_ENGINE_SCHEMA,
                "c7_3b_merge_sha": C73C_BASE_COMMIT,
            },
            "evidence_boundary": {
                "workload_structure": ParameterSource.P_SRC4.value,
                "timing_cost_evidence": "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2",
                "direct_hardware_measurement_claim": False,
                "synthetic_prevalence_claim": False,
            },
            "paired_fairness": {
                "same_program_case_fingerprint": True,
                "same_base_c7_manifest_fingerprint": True,
                "case_must_regenerate_exactly_from_base_manifest_parameters_and_seed": True,
                "same_semantic_validity_outcomes": True,
                "same_event_stream": True,
                "same_state_size_and_capacity": True,
                "underlying_routing_policy": PolicyID.B4.value,
                "retention_policy_is_only_comparison_dimension": True,
                "queue_control_component": C73C_ZERO_QUEUE_COMPONENT,
                "queue_control_component_is_physical_zero_claim": False,
            },
            "primary_surfaces": [item.to_dict() for item in C73C_PRIMARY_SURFACES],
            "full_cartesian_search_forbidden": True,
            "duplicate_surface_intersection_is_one_logical_cell": True,
            "retention_policies": [policy.value for policy in RetentionPolicyID],
            "fixed_ttl": {
                "primary_seconds": C73_PRIMARY_TTL_SECONDS,
                "sensitivity_seconds": list(C73_TTL_SENSITIVITY_SECONDS),
                "oracle_envelope_label": C73C_ORACLE_TTL_LABEL,
                "oracle_envelope_primary_baseline": False,
            },
            "hardware_strata": list(C7_SUPPORTED_HARDWARE_IDS),
            "state": {
                "state_tokens": C73_DEFAULT_STATE_TOKENS,
                "state_bytes": C73_DEFAULT_STATE_BYTES,
                "reuse_context_tokens": C73C_REUSE_CONTEXT_TOKENS,
                "reuse_context_source": ParameterSource.P_SRC4.value,
            },
            "common_random_numbers": {
                "counter_rng": "SHA-256 first 64 bits / 2^64",
                "axis_values_in_draw_key": False,
                "p2_tool_return_key": [
                    "schema", "P2", "seed", "target", "tool-return"
                ],
                "p2_pressure_order_key": [
                    "schema", "P2", "seed", "pressure-state", "admission-priority"
                ],
                "p3_speculative_key": [
                    "schema", "P3", "seed", "branch-index", "speculative-class"
                ],
                "p3_admission_key": [
                    "schema", "P3", "seed", "branch-index", "admission-priority"
                ],
                "nested_threshold_coupling": True,
            },
            "p2_realization": {
                "workload": "W3_TOOL_GAP_WITH_W8_PRESSURE",
                "target_states": 1,
                "pressure_states": 3,
                "pressure_classes": {
                    "ACTIVE": 1,
                    "SPECULATIVE": 2,
                },
                "target_lifecycle_during_gap": "WAITING",
                "pressure_admission_gap_fractions": [0.25, 0.5, 0.75],
                "tool_return_is_common_bernoulli_threshold": True,
                "return_event": "WAITING-only State dependency set gains ACTIVE child dependency; semantically-valid reuse lookup; waiting ancestor completes",
                "no_return_event": "WAITING dependency completes to TERMINAL; Session ends; no reuse opportunity",
                "reference_working_set_states": 4,
            },
            "p3_realization": {
                "workload": "W8_CACHE_PRESSURE_WITH_W6_BRANCHING",
                "branch_state_count": "branch_width",
                "cache_pressure_lifecycle": {
                    "SPECULATIVE": "uniform < speculative_fraction",
                    "WAITING": "otherwise",
                },
                "resolution": {
                    "WAITING": "required branch gains ACTIVE continuation dependency before semantically-valid reuse lookup",
                    "SPECULATIVE": "TERMINAL without reuse lookup",
                },
                "extra_waiting_active_ratio_parameter": False,
                "reference_working_set_states": "branch_width",
            },
            "metric_estimators": {
                "USR": "ratio-of-sums useful byte-seconds / classified byte-seconds",
                "WSR": "ratio-of-sums wasted byte-seconds / classified byte-seconds",
                "RR": "ratio-of-sums recomputed eligible-prefix tokens / prefill-equivalent eligible tokens",
                "CCR": "ratio-of-sums cold eligible executions / eligible continuation executions",
                "P2_TOOL_RETURN_TTFT": "arithmetic mean over common returning-Program subset",
                "zero_denominator_label": C73C_INSUFFICIENT_DENOMINATOR_LABEL,
                "zero_denominator_bootstrap_resample_rule": "fail cell/metric interval closed; never skip or replace a resample",
                "p2_ttft_minimum_returning_programs_for_inference": C7_CONVERGENCE_PREFIXES[0],
                "rr_ccr_algebraically_identical_under_fixed_1024_token_projection": True,
                "rr_ccr_independent_corroboration_claim": False,
            },
            "statistics": {
                "stochastic_seeds": list(C7_STOCHASTIC_SEEDS),
                "execution_seed_count": len(C7_STOCHASTIC_SEEDS),
                "fixed_full_64_run_design": True,
                "convergence_prefixes_reported_diagnostically": list(C7_CONVERGENCE_PREFIXES),
                "common_random_numbers": True,
                "bootstrap_resamples": C7_BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": C7_BOOTSTRAP_SEED,
                "bootstrap_interval": C73C_BOOTSTRAP_INTERVAL,
                "percentile_quantile_algorithm": "Hyndman-Fan type 7 linear interpolation",
                "bootstrap_index_rng": "SHA-256 counter keyed only by bootstrap seed, sample size, resample index, draw index",
                "cluster_unit": "Program",
                "ratio_bootstrap": "paired Program-cluster resample, recompute ratio-of-sums, then B4-baseline",
                "ttft_bootstrap": "paired Program resample over common returning-Program subset",
            },
            "capacity_infeasibility": {
                "label": C73C_INFEASIBLE_LABEL,
                "ranking_requires_all_64_capacity_eligible_for_both_policies": True,
                "infeasibility_rate_reported": True,
                "infeasible_is_policy_win": False,
            },
            "h5_rule": {
                "primary_baselines": [
                    RetentionPolicyID.LRU.value,
                    f"{RetentionPolicyID.FIXED_TTL.value}({C73_PRIMARY_TTL_SECONDS:g}s)",
                    RetentionPolicyID.SESSION_PINNING.value,
                ],
                "difference_orientation": "LIFECYCLE_B4 - baseline",
                "favorable_direction": {
                    "USR": "positive",
                    "WSR": "negative",
                    "RR": "negative",
                    "CCR": "negative",
                    "P2_TOOL_RETURN_TTFT": "negative",
                },
                "cell_support": (
                    "capacity-comparable for all 64 Programs; no semantic violation; "
                    "paired 95% interval favorable versus all three primary baselines"
                ),
                "meaningful_range": (
                    "connected component of >=2 immediately adjacent cells on one primary surface "
                    "for the same metric"
                ),
                "adjacency": "one primary-surface axis changes by one neighboring frozen value",
                "support_metrics": ["USR", "RR", "P2_TOOL_RETURN_TTFT", "CCR"],
                "wsr_can_trigger_support": False,
                "null_decision": C73C_H5_NOT_SUPPORTED,
                "familywise_error_rate_claim": False,
            },
            "result_integrity": [
                "C7.1 protocol fingerprint",
                "C7.3a retention fingerprint",
                "C7.3b engine schema and merge SHA",
                "C7.3c protocol fingerprint",
                "execution git SHA",
                "surface/cell/hardware/seed",
                "Program-case fingerprint",
                "base C7 manifest fingerprint",
                "retention-manifest fingerprint",
                "retention-policy result fingerprint",
                "capacity outcome",
                "metric numerator/denominator payload",
                "bootstrap and convergence metadata",
                "H5 decision and supporting-region coordinates if any",
            ],
            "invalid_result_conditions": [
                "full P2/P3 Cartesian search substituted for frozen compact surfaces",
                "axis value included in stochastic draw key and breaks common-random-number coupling",
                "retention policy changes Program case, base manifest, semantic oracle, or capacity",
                "underlying routing/control policy differs across retention comparisons",
                "fixed TTL sensitivity or oracle envelope replaces the ordinary 5-second baseline",
                "capacity infeasibility counted as a policy win",
                "zero metric denominator silently converted to a performance value",
                "RR or CCR treated as independent corroborating evidence in this fixed-token construction",
                "synthetic workload frequency relabeled as empirical prevalence",
                "source-model-derived C6 timing relabeled as direct hardware measurement",
                "observed comparative result mutates this protocol in place",
            ],
            "revision_rule": (
                "After any C7.3c comparative result is observed, a required change creates a new "
                "protocol version and preserves the prior outcome."
            ),
        }

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


FROZEN_C73C_PROTOCOL = C73CPairedEvaluationProtocol()
C73C_PROTOCOL_FINGERPRINT = FROZEN_C73C_PROTOCOL.fingerprint


def main() -> None:
    print(
        _json(
            {
                "protocol": FROZEN_C73C_PROTOCOL.to_dict(),
                "protocol_fingerprint": C73C_PROTOCOL_FINGERPRINT,
            }
        )
    )


if __name__ == "__main__":
    main()
