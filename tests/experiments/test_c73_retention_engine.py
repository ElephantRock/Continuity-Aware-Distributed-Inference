from __future__ import annotations

import json
import math

import pytest

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
    C73B_ENGINE_SCHEMA,
    C73B_FROZEN_RETENTION_PROTOCOL_FINGERPRINT,
    C73B_FROZEN_STATE_SIZE_MAP_FINGERPRINT,
    RetentionAuditKind,
    RetentionEvent,
    RetentionEventKind,
    RetentionProgramCase,
    RetentionSessionSpec,
    RetentionStateSpec,
    reference_working_set_bytes,
    run_retention_program,
    tool_return_ttft_from_profile,
    validate_c73_base_manifest,
)
from experiments.c73_retention_protocol import (
    C73_DEFAULT_STATE_BYTES,
    C73_DEFAULT_STATE_TOKENS,
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    C73_STATE_SIZE_MAP_FINGERPRINT,
    C73RetentionManifest,
    CapacityOutcome,
    RetentionPolicyID,
    capacity_bytes,
)
from simulator.inference_cost_runtime import load_c64f_runtime_profile
from simulator.policies import PolicyID


def _state(
    state_id: str,
    ordinal: int,
    *,
    session_id: str = "session-1",
    lifecycle: ContinuationLifecycle = ContinuationLifecycle.ACTIVE,
) -> RetentionStateSpec:
    return RetentionStateSpec(
        state_id=state_id,
        session_id=session_id,
        admission_ordinal=ordinal,
        initial_dependents=(lifecycle,),
    )


def _event(
    event_id: str,
    time_seconds: float,
    ordinal: int,
    kind: RetentionEventKind,
    *,
    state_id: str | None = None,
    session_id: str | None = None,
    dependents: tuple[ContinuationLifecycle, ...] = (),
    session_live: bool | None = None,
    semantic_valid: bool | None = None,
) -> RetentionEvent:
    return RetentionEvent(
        event_id=event_id,
        time_seconds=time_seconds,
        ordinal=ordinal,
        kind=kind,
        state_id=state_id,
        session_id=session_id,
        dependent_lifecycles=dependents,
        session_live=session_live,
        semantic_valid=semantic_valid,
    )


def _case(
    *,
    states: tuple[RetentionStateSpec, ...],
    events: tuple[RetentionEvent, ...],
    sessions: tuple[RetentionSessionSpec, ...] = (RetentionSessionSpec("session-1"),),
    end: float = 10.0,
) -> RetentionProgramCase:
    return RetentionProgramCase(
        program_id="program-1",
        sessions=sessions,
        states=states,
        events=events,
        program_start_seconds=0.0,
        program_end_seconds=end,
    )


def _base_manifest(
    *,
    ratio: float,
    series: ExperimentSeries = ExperimentSeries.P2_TOOL_GAP_RETENTION,
    parameters: tuple[tuple[str, int | float], ...] | None = None,
) -> C7ExperimentManifest:
    if parameters is None:
        if series is ExperimentSeries.P2_TOOL_GAP_RETENTION:
            parameters = (
                ("cache_capacity_ratio", ratio),
                ("state_tokens", C73_DEFAULT_STATE_TOKENS),
                ("tool_gap_seconds", 5.0),
                ("tool_return_probability", 1.0),
            )
        elif series is ExperimentSeries.P3_BRANCH_CACHE_PRESSURE:
            parameters = (
                ("branch_width", 4),
                ("cache_capacity_ratio", ratio),
                ("speculative_fraction", 0.25),
                ("state_tokens", C73_DEFAULT_STATE_TOKENS),
            )
        else:
            parameters = (
                ("cache_capacity_ratio", ratio),
                ("state_tokens", C73_DEFAULT_STATE_TOKENS),
            )
    return C7ExperimentManifest(
        experiment_id="c73b-test",
        git_commit=C73B_BASE_COMMIT,
        protocol_fingerprint=C7_PROTOCOL_FINGERPRINT,
        series=series,
        policy_id=PolicyID.B4,
        workload_class=WorkloadClass.SYNTHETIC_STRESS,
        hardware_id="a100-80gb",
        program_objective="deterministic C7.3b mechanics fixture",
        seed=0,
        source_dataset_fingerprint=None,
        augmentation_fingerprint=None,
        parameters=parameters,
        parameter_sources=tuple((name, ParameterSource.P_SRC4) for name, _ in parameters),
    )


def _retention_manifest(
    case: RetentionProgramCase,
    base: C7ExperimentManifest,
    policy: RetentionPolicyID,
    *,
    ratio: float,
    ttl_seconds: float | None = None,
) -> C73RetentionManifest:
    reference = reference_working_set_bytes(case)
    return C73RetentionManifest(
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


def _run(
    case: RetentionProgramCase,
    policy: RetentionPolicyID,
    *,
    ratio: float = 1.0,
    ttl_seconds: float | None = None,
):
    base = _base_manifest(ratio=ratio)
    manifest = _retention_manifest(
        case,
        base,
        policy,
        ratio=ratio,
        ttl_seconds=ttl_seconds,
    )
    return run_retention_program(
        case=case,
        base_manifest=base,
        retention_manifest=manifest,
    )


def test_engine_identity_is_exact_and_pre_result(capsys) -> None:
    from experiments.c73_retention_engine import main

    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == C73B_ENGINE_SCHEMA
    assert payload["base_commit"] == C73B_BASE_COMMIT == "2b8a76c54501d9728d6520c5847b41c0e5bb2e82"
    assert (
        payload["retention_protocol_fingerprint"]
        == C73B_FROZEN_RETENTION_PROTOCOL_FINGERPRINT
        == C73_RETENTION_PROTOCOL_FINGERPRINT
        == "f6165cb9248f5f4292846942e797e5ddcd8d004027ad503b2a7de35210021379"
    )
    assert (
        payload["state_size_map_fingerprint"]
        == C73B_FROZEN_STATE_SIZE_MAP_FINGERPRINT
        == C73_STATE_SIZE_MAP_FINGERPRINT
        == "36bacaad6462ca3e5eeb2688040e4dcf0d829849158c5b5ad9bad3c58afe173b"
    )
    assert payload["comparative_result_inspection"] == "NONE"
    assert payload["supported_policies"] == [
        "LRU",
        "FIXED_TTL",
        "SESSION_PINNING",
        "LIFECYCLE_B4",
    ]
    assert "policy_results" not in payload
    assert "p2_results" not in payload
    assert "p3_results" not in payload


def test_state_size_is_frozen() -> None:
    assert C73_DEFAULT_STATE_TOKENS == 16
    assert C73_DEFAULT_STATE_BYTES == 8_388_608
    with pytest.raises(ValueError, match="frozen C7.3a State byte mapping"):
        RetentionStateSpec(
            state_id="s",
            session_id="session-1",
            admission_ordinal=0,
            initial_dependents=(ContinuationLifecycle.ACTIVE,),
            size_bytes=1,
        )


def test_program_case_is_canonical_under_input_tuple_reordering() -> None:
    states = (_state("s0", 0), _state("s1", 1))
    events = (
        _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
        _event("a1", 0.0, 1, RetentionEventKind.ADMIT, state_id="s1"),
    )
    first = _case(states=states, events=events)
    second = _case(states=tuple(reversed(states)), events=tuple(reversed(events)))
    assert first.to_dict() == second.to_dict()
    assert first.fingerprint == second.fingerprint


def test_program_case_requires_admission_ordinals_to_match_logical_admission_order() -> None:
    with pytest.raises(ValueError, match="admission_ordinal"):
        _case(
            states=(_state("s0", 7), _state("s1", 2)),
            events=(
                _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
                _event("a1", 0.0, 1, RetentionEventKind.ADMIT, state_id="s1"),
            ),
        )


def test_program_case_rejects_reuse_or_invalidation_before_state_production() -> None:
    with pytest.raises(ValueError, match="before ADMIT"):
        _case(
            states=(_state("s0", 0),),
            events=(
                _event(
                    "r0",
                    0.0,
                    0,
                    RetentionEventKind.REUSE,
                    state_id="s0",
                    semantic_valid=True,
                ),
                _event("a0", 1.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            ),
        )
    with pytest.raises(ValueError, match="before ADMIT"):
        _case(
            states=(_state("s0", 0),),
            events=(
                _event("i0", 0.0, 0, RetentionEventKind.INVALIDATE, state_id="s0"),
                _event("a0", 1.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            ),
        )


def test_reference_working_set_uses_ground_truth_valid_nonterminal_produced_state() -> None:
    case = _case(
        states=(_state("s0", 0), _state("s1", 1)),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event("a1", 0.0, 1, RetentionEventKind.ADMIT, state_id="s1"),
            _event(
                "t0",
                2.0,
                0,
                RetentionEventKind.DEPENDENTS,
                state_id="s0",
                dependents=(ContinuationLifecycle.TERMINAL,),
            ),
            _event("i1", 3.0, 0, RetentionEventKind.INVALIDATE, state_id="s1"),
        ),
    )
    assert reference_working_set_bytes(case) == 2 * C73_DEFAULT_STATE_BYTES


def test_base_manifest_requires_complete_frozen_series_axes() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(_event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),),
    )
    base = _base_manifest(
        ratio=1.0,
        parameters=(
            ("cache_capacity_ratio", 1.0),
            ("state_tokens", C73_DEFAULT_STATE_TOKENS),
            ("tool_gap_seconds", 5.0),
        ),
    )
    manifest = _retention_manifest(case, base, RetentionPolicyID.LRU, ratio=1.0)
    with pytest.raises(ValueError, match="required parameters"):
        validate_c73_base_manifest(base, manifest)


def test_base_manifest_rejects_non_p2_p3_series_and_capacity_ratio_drift() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(_event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),),
    )
    wrong_series = _base_manifest(ratio=1.0, series=ExperimentSeries.P1_DEEP_REUSE)
    wrong_series_manifest = _retention_manifest(
        case, wrong_series, RetentionPolicyID.LRU, ratio=1.0
    )
    with pytest.raises(ValueError, match="must be P2 or P3"):
        validate_c73_base_manifest(wrong_series, wrong_series_manifest)

    base = _base_manifest(ratio=1.0)
    reference = reference_working_set_bytes(case)
    drift = C73RetentionManifest(
        base_c7_manifest_fingerprint=base.fingerprint,
        retention_protocol_fingerprint=C73_RETENTION_PROTOCOL_FINGERPRINT,
        retention_policy_id=RetentionPolicyID.LRU,
        ttl_seconds=None,
        program_case_fingerprint=case.fingerprint,
        state_size_map_fingerprint=C73_STATE_SIZE_MAP_FINGERPRINT,
        state_tokens=C73_DEFAULT_STATE_TOKENS,
        state_bytes=C73_DEFAULT_STATE_BYTES,
        reference_working_set_bytes=reference,
        cache_capacity_ratio=2.0,
        capacity_bytes=capacity_bytes(
            reference_working_set_bytes=reference,
            cache_capacity_ratio=2.0,
        ),
    )
    with pytest.raises(ValueError, match="cache_capacity_ratio"):
        validate_c73_base_manifest(base, drift)


def test_lru_equal_time_capacity_eviction_uses_admission_ordinal_tie_break() -> None:
    case = _case(
        states=(_state("s0", 0), _state("s1", 1)),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event("a1", 0.0, 1, RetentionEventKind.ADMIT, state_id="s1"),
            _event(
                "r1", 1.0, 0, RetentionEventKind.REUSE, state_id="s1", semantic_valid=True
            ),
        ),
        end=2.0,
    )
    result = _run(case, RetentionPolicyID.LRU, ratio=0.5)
    evictions = [record for record in result.audit if record.kind is RetentionAuditKind.EVICTED]
    assert [record.state_id for record in evictions] == ["s0"]
    assert result.eligible_reuse_opportunities == 1
    assert result.consumed_reuse_opportunities == 1
    assert result.max_resident_bytes <= result.capacity_bytes == C73_DEFAULT_STATE_BYTES


def test_lru_successful_reuse_refreshes_recency_before_later_pressure() -> None:
    states = tuple(_state(f"s{i}", i) for i in range(4))
    case = _case(
        states=states,
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event("a1", 0.0, 1, RetentionEventKind.ADMIT, state_id="s1"),
            _event(
                "r0", 1.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=True
            ),
            _event("a2", 2.0, 0, RetentionEventKind.ADMIT, state_id="s2"),
            _event("a3", 9.0, 0, RetentionEventKind.ADMIT, state_id="s3"),
        ),
        end=10.0,
    )
    result = _run(case, RetentionPolicyID.LRU, ratio=0.5)
    first_eviction = next(record for record in result.audit if record.kind is RetentionAuditKind.EVICTED)
    assert first_eviction.state_id == "s1"


def test_fixed_ttl_expiry_precedes_reuse_at_exact_equality_and_does_not_refresh() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event(
                "r-before",
                0.1,
                0,
                RetentionEventKind.REUSE,
                state_id="s0",
                semantic_valid=True,
            ),
            _event(
                "r-equal",
                0.25,
                0,
                RetentionEventKind.REUSE,
                state_id="s0",
                semantic_valid=True,
            ),
        ),
        end=1.0,
    )
    result = _run(
        case,
        RetentionPolicyID.FIXED_TTL,
        ratio=1.0,
        ttl_seconds=0.25,
    )
    at_equality = [record.kind for record in result.audit if record.time_seconds == 0.25]
    assert RetentionAuditKind.TTL_EXPIRED in at_equality
    assert RetentionAuditKind.REUSE_MISS in at_equality
    assert at_equality.index(RetentionAuditKind.TTL_EXPIRED) < at_equality.index(
        RetentionAuditKind.REUSE_MISS
    )
    assert result.eligible_reuse_opportunities == 2
    assert result.consumed_reuse_opportunities == 1


def test_terminal_but_valid_state_persists_for_lru_and_ttl_but_b4_releases() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event(
                "terminal",
                1.0,
                0,
                RetentionEventKind.DEPENDENTS,
                state_id="s0",
                dependents=(ContinuationLifecycle.TERMINAL,),
            ),
            _event(
                "reuse", 2.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=True
            ),
        ),
        end=3.0,
    )
    lru = _run(case, RetentionPolicyID.LRU)
    ttl = _run(case, RetentionPolicyID.FIXED_TTL, ttl_seconds=5.0)
    b4 = _run(case, RetentionPolicyID.LIFECYCLE_B4)
    assert lru.consumed_reuse_opportunities == 1
    assert ttl.consumed_reuse_opportunities == 1
    assert b4.consumed_reuse_opportunities == 0
    assert any(
        record.kind is RetentionAuditKind.POLICY_RELEASED
        and record.reason == "B4_TERMINAL_RELEASE"
        for record in b4.audit
    )


def test_session_end_release_precedes_same_time_reuse() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event(
                "session-end",
                1.0,
                0,
                RetentionEventKind.SESSION_STATUS,
                session_id="session-1",
                session_live=False,
            ),
            _event(
                "reuse", 1.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=True
            ),
        ),
        end=2.0,
    )
    result = _run(case, RetentionPolicyID.SESSION_PINNING)
    same_time = [record.kind for record in result.audit if record.time_seconds == 1.0]
    assert RetentionAuditKind.POLICY_RELEASED in same_time
    assert RetentionAuditKind.REUSE_MISS in same_time
    assert same_time.index(RetentionAuditKind.POLICY_RELEASED) < same_time.index(
        RetentionAuditKind.REUSE_MISS
    )
    assert result.consumed_reuse_opportunities == 0


def test_session_pinning_marks_capacity_infeasible_without_overcommit() -> None:
    case = _case(
        states=(_state("s0", 0), _state("s1", 1)),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event("a1", 0.0, 1, RetentionEventKind.ADMIT, state_id="s1"),
        ),
        end=1.0,
    )
    result = _run(case, RetentionPolicyID.SESSION_PINNING, ratio=0.5)
    assert result.capacity_outcome is CapacityOutcome.CAPACITY_INFEASIBLE
    assert any(
        record.kind is RetentionAuditKind.CAPACITY_INFEASIBLE
        and record.reason == "SESSION_PINNED_BYTES_EXCEED_CAPACITY"
        for record in result.audit
    )
    assert all(record.resident_bytes_after <= result.capacity_bytes for record in result.audit)


def test_b4_evicts_lower_lifecycle_priority_before_lru_recency() -> None:
    states = (
        _state("waiting", 0, lifecycle=ContinuationLifecycle.WAITING),
        _state("speculative", 1, lifecycle=ContinuationLifecycle.SPECULATIVE),
        _state("active", 2, lifecycle=ContinuationLifecycle.ACTIVE),
        _state("future", 3, lifecycle=ContinuationLifecycle.ACTIVE),
    )
    case = _case(
        states=states,
        events=(
            _event("a-w", 0.0, 0, RetentionEventKind.ADMIT, state_id="waiting"),
            _event("a-s", 0.0, 1, RetentionEventKind.ADMIT, state_id="speculative"),
            _event("a-a", 1.0, 0, RetentionEventKind.ADMIT, state_id="active"),
            _event("a-f", 9.0, 0, RetentionEventKind.ADMIT, state_id="future"),
        ),
        end=10.0,
    )
    result = _run(case, RetentionPolicyID.LIFECYCLE_B4, ratio=0.5)
    first_eviction = next(record for record in result.audit if record.kind is RetentionAuditKind.EVICTED)
    assert first_eviction.state_id == "speculative"
    assert first_eviction.reason == "B4_SPECULATIVE_CAPACITY"


def test_b4_active_protection_marks_capacity_infeasible_without_overcommit() -> None:
    case = _case(
        states=(_state("s0", 0), _state("s1", 1)),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event("a1", 0.0, 1, RetentionEventKind.ADMIT, state_id="s1"),
        ),
        end=1.0,
    )
    result = _run(case, RetentionPolicyID.LIFECYCLE_B4, ratio=0.5)
    assert result.capacity_outcome is CapacityOutcome.CAPACITY_INFEASIBLE
    assert any(
        record.kind is RetentionAuditKind.CAPACITY_INFEASIBLE
        and record.reason == "B4_ACTIVE_PROTECTED_BYTES_EXCEED_CAPACITY"
        for record in result.audit
    )
    assert all(record.resident_bytes_after <= result.capacity_bytes for record in result.audit)


def test_common_invalidation_precedes_same_time_reuse_and_forces_semantic_reject() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event("invalidate", 1.0, 0, RetentionEventKind.INVALIDATE, state_id="s0"),
            _event(
                "reuse", 1.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=True
            ),
        ),
        end=2.0,
    )
    for policy in RetentionPolicyID:
        kwargs = {"ttl_seconds": 5.0} if policy is RetentionPolicyID.FIXED_TTL else {}
        result = _run(case, policy, **kwargs)
        same_time = [record.kind for record in result.audit if record.time_seconds == 1.0]
        assert same_time.index(RetentionAuditKind.INVALIDATED) < same_time.index(
            RetentionAuditKind.REUSE_SEMANTIC_REJECT
        )
        assert result.eligible_reuse_opportunities == 1
        assert result.semantic_rejection_count == 1
        assert result.consumed_reuse_opportunities == 0


def test_independent_semantic_reject_does_not_consume_resident_state() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event(
                "reject", 1.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=False
            ),
            _event(
                "accept", 2.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=True
            ),
        ),
        end=3.0,
    )
    result = _run(case, RetentionPolicyID.LRU)
    assert result.eligible_reuse_opportunities == 2
    assert result.semantic_rejection_count == 1
    assert result.consumed_reuse_opportunities == 1


def test_reuse_denominator_is_policy_independent_on_same_program_case() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event(
                "r0", 1.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=True
            ),
            _event(
                "r1", 2.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=False
            ),
        ),
        end=3.0,
    )
    results = []
    for policy in RetentionPolicyID:
        kwargs = {"ttl_seconds": 5.0} if policy is RetentionPolicyID.FIXED_TTL else {}
        results.append(_run(case, policy, **kwargs))
    assert {result.program_case_fingerprint for result in results} == {case.fingerprint}
    assert {result.eligible_reuse_opportunities for result in results} == {2}
    assert {result.semantic_rejection_count for result in results} == {1}


def test_residency_intervals_partition_exact_byte_seconds() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event(
                "reuse", 2.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=True
            ),
        ),
        end=5.0,
    )
    result = _run(case, RetentionPolicyID.LRU)
    assert result.useful_byte_seconds == C73_DEFAULT_STATE_BYTES * 2.0
    assert result.wasted_byte_seconds == C73_DEFAULT_STATE_BYTES * 3.0
    assert result.total_classified_byte_seconds == C73_DEFAULT_STATE_BYTES * 5.0
    assert math.isclose(result.useful_residency_fraction, 0.4)
    assert math.isclose(result.wasted_residency_fraction, 0.6)
    assert math.isclose(
        result.useful_residency_fraction + result.wasted_residency_fraction,
        1.0,
    )


def test_result_and_audit_are_deterministic_and_manifest_bound() -> None:
    case = _case(
        states=(_state("s0", 0),),
        events=(
            _event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),
            _event(
                "r0", 1.0, 0, RetentionEventKind.REUSE, state_id="s0", semantic_valid=True
            ),
        ),
        end=2.0,
    )
    base = _base_manifest(ratio=1.0)
    manifest = _retention_manifest(case, base, RetentionPolicyID.LRU, ratio=1.0)
    first = run_retention_program(case=case, base_manifest=base, retention_manifest=manifest)
    second = run_retention_program(case=case, base_manifest=base, retention_manifest=manifest)
    assert first.to_dict() == second.to_dict()
    assert first.fingerprint == second.fingerprint
    assert first.base_c7_manifest_fingerprint == base.fingerprint
    assert first.retention_manifest_fingerprint == manifest.fingerprint
    assert first.retention_protocol_fingerprint == C73_RETENTION_PROTOCOL_FINGERPRINT
    assert first.program_case_fingerprint == case.fingerprint
    assert [record.sequence for record in first.audit] == list(range(len(first.audit)))


def test_retention_manifest_program_case_binding_fails_closed() -> None:
    first_case = _case(
        states=(_state("s0", 0),),
        events=(_event("a0", 0.0, 0, RetentionEventKind.ADMIT, state_id="s0"),),
    )
    second_case = _case(
        states=(_state("other", 0),),
        events=(_event("a-other", 0.0, 0, RetentionEventKind.ADMIT, state_id="other"),),
    )
    base = _base_manifest(ratio=1.0)
    manifest = _retention_manifest(first_case, base, RetentionPolicyID.LRU, ratio=1.0)
    with pytest.raises(ValueError, match="Program case"):
        run_retention_program(
            case=second_case,
            base_manifest=base,
            retention_manifest=manifest,
        )


def test_tool_return_ttft_uses_closed_c6_runtime_profile_and_first_decode_step() -> None:
    profile = load_c64f_runtime_profile("a100-80gb")
    projection = tool_return_ttft_from_profile(
        profile=profile,
        expected_hardware_id="a100-80gb",
        resume_eligibility_seconds=10.0,
        service_start_seconds=12.0,
        full_context_tokens=100,
        consumed_reuse_tokens=40,
    )
    expected_recompute = profile.prefill_seconds(60)
    expected_decode = (
        profile.decode_fixed_seconds_per_output_token
        + profile.decode_seconds_per_context_token_step * 100
    )
    assert projection.recompute_seconds == expected_recompute
    assert projection.first_decode_seconds == expected_decode
    assert projection.queue_delay_seconds == 2.0
    assert math.isclose(
        projection.ttft_seconds,
        2.0 + expected_recompute + expected_decode,
        rel_tol=0.0,
        abs_tol=1e-15,
    )
    assert projection.scientific_fingerprint == profile.scientific_fingerprint
    assert projection.artifact_sha256 == profile.artifact_sha256


def test_tool_return_ttft_fails_closed_on_hardware_or_context_domain_drift() -> None:
    profile = load_c64f_runtime_profile("a100-80gb")
    with pytest.raises(ValueError, match="hardware"):
        tool_return_ttft_from_profile(
            profile=profile,
            expected_hardware_id="h100-80gb",
            resume_eligibility_seconds=0.0,
            service_start_seconds=0.0,
            full_context_tokens=100,
            consumed_reuse_tokens=0,
        )
    with pytest.raises(ValueError):
        tool_return_ttft_from_profile(
            profile=profile,
            expected_hardware_id="a100-80gb",
            resume_eligibility_seconds=0.0,
            service_start_seconds=0.0,
            full_context_tokens=4096,
            consumed_reuse_tokens=0,
        )
    accepted = tool_return_ttft_from_profile(
        profile=profile,
        expected_hardware_id="a100-80gb",
        resume_eligibility_seconds=0.0,
        service_start_seconds=0.0,
        full_context_tokens=4095,
        consumed_reuse_tokens=0,
    )
    assert accepted.ttft_seconds >= 0.0
