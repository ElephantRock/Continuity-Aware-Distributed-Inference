from __future__ import annotations

from continuity.entities import ContinuationLifecycle
from experiments.c7_protocol import (
    C7_PROTOCOL_FINGERPRINT,
    C7ExperimentManifest,
    ExperimentSeries,
    ParameterSource,
    WorkloadClass,
)
from experiments.c73_retention_engine import (
    C73B_BASE_COMMIT,
    RetentionAuditKind,
    RetentionEvent,
    RetentionEventKind,
    RetentionProgramCase,
    RetentionSessionSpec,
    RetentionStateSpec,
    reference_working_set_bytes,
    run_retention_program,
)
from experiments.c73_retention_protocol import (
    C73_DEFAULT_STATE_BYTES,
    C73_DEFAULT_STATE_TOKENS,
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    C73_STATE_SIZE_MAP_FINGERPRINT,
    C73RetentionManifest,
    RetentionPolicyID,
    capacity_bytes,
)
from simulator.policies import PolicyID


def _state(state_id: str, admission_ordinal: int) -> RetentionStateSpec:
    return RetentionStateSpec(
        state_id=state_id,
        session_id="session-1",
        admission_ordinal=admission_ordinal,
        initial_dependents=(ContinuationLifecycle.ACTIVE,),
    )


def _event(
    event_id: str,
    time_seconds: float,
    ordinal: int,
    kind: RetentionEventKind,
    state_id: str,
    *,
    semantic_valid: bool | None = None,
) -> RetentionEvent:
    return RetentionEvent(
        event_id=event_id,
        time_seconds=time_seconds,
        ordinal=ordinal,
        kind=kind,
        state_id=state_id,
        semantic_valid=semantic_valid,
    )


def _base_manifest(ratio: float) -> C7ExperimentManifest:
    parameters = (
        ("cache_capacity_ratio", ratio),
        ("state_tokens", C73_DEFAULT_STATE_TOKENS),
        ("tool_gap_seconds", 5.0),
        ("tool_return_probability", 1.0),
    )
    return C7ExperimentManifest(
        experiment_id="c73b-review-regression",
        git_commit=C73B_BASE_COMMIT,
        protocol_fingerprint=C7_PROTOCOL_FINGERPRINT,
        series=ExperimentSeries.P2_TOOL_GAP_RETENTION,
        policy_id=PolicyID.B4,
        workload_class=WorkloadClass.SYNTHETIC_STRESS,
        hardware_id="a100-80gb",
        program_objective="pre-result deterministic review regression",
        seed=0,
        source_dataset_fingerprint=None,
        augmentation_fingerprint=None,
        parameters=parameters,
        parameter_sources=tuple((name, ParameterSource.P_SRC4) for name, _ in parameters),
    )


def _run(
    case: RetentionProgramCase,
    policy: RetentionPolicyID,
    *,
    ratio: float,
    ttl_seconds: float | None = None,
):
    base = _base_manifest(ratio)
    reference = reference_working_set_bytes(case)
    manifest = C73RetentionManifest(
        base_c7_manifest_fingerprint=base.fingerprint,
        retention_protocol_fingerprint=C73_RETENTION_PROTOCOL_FINGERPRINT,
        retention_policy_id=policy,
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
    return run_retention_program(
        case=case,
        base_manifest=base,
        retention_manifest=manifest,
    )


def test_lru_allows_same_state_readmission_after_eviction() -> None:
    case = RetentionProgramCase(
        program_id="program-readmit",
        sessions=(RetentionSessionSpec("session-1"),),
        states=(_state("s0", 0), _state("s1", 1)),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, "s0"),
            _event("a1", 1.0, 0, RetentionEventKind.ADMIT, "s1"),
            _event(
                "miss-s0",
                2.0,
                0,
                RetentionEventKind.REUSE,
                "s0",
                semantic_valid=True,
            ),
            _event("readmit-s0", 3.0, 0, RetentionEventKind.ADMIT, "s0"),
            _event(
                "hit-s0",
                4.0,
                0,
                RetentionEventKind.REUSE,
                "s0",
                semantic_valid=True,
            ),
        ),
        program_start_seconds=0.0,
        program_end_seconds=5.0,
    )
    result = _run(case, RetentionPolicyID.LRU, ratio=0.5)
    assert result.eligible_reuse_opportunities == 2
    assert result.consumed_reuse_opportunities == 1
    assert sum(
        record.kind is RetentionAuditKind.ADMITTED and record.state_id == "s0"
        for record in result.audit
    ) == 2


def test_fixed_ttl_readmission_starts_a_new_admission_anchored_expiry() -> None:
    case = RetentionProgramCase(
        program_id="program-ttl-readmit",
        sessions=(RetentionSessionSpec("session-1"),),
        states=(_state("s0", 0),),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, "s0"),
            _event("readmit", 0.5, 0, RetentionEventKind.ADMIT, "s0"),
            _event(
                "before-new-expiry",
                0.74,
                0,
                RetentionEventKind.REUSE,
                "s0",
                semantic_valid=True,
            ),
            _event(
                "at-new-expiry",
                0.75,
                0,
                RetentionEventKind.REUSE,
                "s0",
                semantic_valid=True,
            ),
        ),
        program_start_seconds=0.0,
        program_end_seconds=1.0,
    )
    result = _run(
        case,
        RetentionPolicyID.FIXED_TTL,
        ratio=1.0,
        ttl_seconds=0.25,
    )
    expiries = [
        record.time_seconds
        for record in result.audit
        if record.kind is RetentionAuditKind.TTL_EXPIRED
    ]
    assert expiries == [0.25, 0.75]
    assert result.eligible_reuse_opportunities == 2
    assert result.consumed_reuse_opportunities == 1


def test_lru_capacity_enforcement_handles_multiple_same_time_evictions_atomically() -> None:
    states = tuple(_state(f"s{i}", i) for i in range(4))
    events = tuple(
        _event(f"a{i}", 0.0, i, RetentionEventKind.ADMIT, f"s{i}")
        for i in range(4)
    )
    case = RetentionProgramCase(
        program_id="program-batch-evict",
        sessions=(RetentionSessionSpec("session-1"),),
        states=states,
        events=events,
        program_start_seconds=0.0,
        program_end_seconds=1.0,
    )
    result = _run(case, RetentionPolicyID.LRU, ratio=0.25)
    evicted = [
        record.state_id
        for record in result.audit
        if record.kind is RetentionAuditKind.EVICTED
    ]
    assert evicted == ["s0", "s1", "s2"]
    assert result.max_resident_bytes <= result.capacity_bytes == C73_DEFAULT_STATE_BYTES
    assert all(record.resident_bytes_after <= result.capacity_bytes for record in result.audit)
