from __future__ import annotations

import math

import pytest

from continuity.entities import ContinuationLifecycle, StateLifecycle
from experiments.c7_protocol import (
    AXES,
    C6_MAX_MODEL_LENGTH,
    C6_STATE_BYTES_PER_TOKEN,
    C7_BOOTSTRAP_RESAMPLES,
    C7_BOOTSTRAP_SEED,
    C7_CONVERGENCE_PREFIXES,
    C7_PROTOCOL_FINGERPRINT,
    C7_PROTOCOL_SCHEMA,
    C7_STOCHASTIC_SEEDS,
)
from experiments.c73_retention_protocol import (
    C73_BASE_COMMIT,
    C73_C6_ACCEPTED_STATE_FIXED_BYTES,
    C73_DEFAULT_STATE_BYTES,
    C73_DEFAULT_STATE_TOKENS,
    C73_EVENT_ORDER,
    C73_PRIMARY_TTL_SECONDS,
    C73_RETENTION_MANIFEST_SCHEMA,
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    C73_RETENTION_PROTOCOL_SCHEMA,
    C73_STATE_SIZE_MAP_FINGERPRINT,
    C73_TTL_SENSITIVITY_SECONDS,
    C73RetentionManifest,
    CapacityOutcome,
    FROZEN_C73_RETENTION_PROTOCOL,
    ResidencyEndReason,
    RetentionPolicyID,
    capacity_bytes,
    derive_state_lifecycle,
    fixed_ttl_expired,
    fixed_ttl_expiry_seconds,
    lifecycle_eviction_key,
    lru_eviction_key,
    residency_interval_byte_seconds,
    strict_capacity_outcome,
    tool_return_ttft_seconds,
)


def test_retention_policy_ids_are_exact_and_canonical() -> None:
    assert tuple(policy.value for policy in RetentionPolicyID) == (
        "LRU",
        "FIXED_TTL",
        "SESSION_PINNING",
        "LIFECYCLE_B4",
    )


@pytest.mark.parametrize(
    ("dependents", "expected"),
    [
        ((ContinuationLifecycle.ACTIVE,), StateLifecycle.ACTIVE),
        ((ContinuationLifecycle.WAITING,), StateLifecycle.WAITING),
        ((ContinuationLifecycle.SPECULATIVE,), StateLifecycle.SPECULATIVE),
        ((ContinuationLifecycle.TERMINAL,), StateLifecycle.TERMINAL),
        ((), StateLifecycle.TERMINAL),
        (
            (ContinuationLifecycle.SPECULATIVE, ContinuationLifecycle.WAITING),
            StateLifecycle.WAITING,
        ),
        (
            (
                ContinuationLifecycle.SPECULATIVE,
                ContinuationLifecycle.WAITING,
                ContinuationLifecycle.ACTIVE,
            ),
            StateLifecycle.ACTIVE,
        ),
    ],
)
def test_state_lifecycle_is_derived_from_live_dependents_by_frozen_priority(
    dependents, expected
) -> None:
    assert derive_state_lifecycle(dependents) is expected


@pytest.mark.parametrize(
    "lifecycle",
    [
        ContinuationLifecycle.CREATED,
        ContinuationLifecycle.JOINING,
        ContinuationLifecycle.ABANDONED,
    ],
)
def test_non_minimum_paper1_continuation_lifecycles_fail_closed(lifecycle) -> None:
    with pytest.raises(ValueError, match="outside the Paper-1 C7.3 minimum"):
        derive_state_lifecycle((lifecycle,))


def test_lifecycle_derivation_rejects_wrong_type() -> None:
    with pytest.raises(TypeError, match="ContinuationLifecycle"):
        derive_state_lifecycle((StateLifecycle.ACTIVE,))  # type: ignore[arg-type]


def test_equal_time_event_order_is_exact() -> None:
    assert C73_EVENT_ORDER == (
        "VALIDITY_INVALIDATION_AND_COMMON_INVALID_RELEASE",
        "GROUND_TRUTH_CONTINUATION_SESSION_TRANSITIONS_AND_LIFECYCLE_RECOMPUTE",
        "POLICY_SPECIFIC_LIFECYCLE_OR_SESSION_END_RELEASE",
        "FIXED_TTL_EXPIRY",
        "REUSE_LOOKUP_AND_SEMANTIC_VALIDITY",
        "SUCCESSFUL_REUSE_TOUCH",
        "STATE_ADMISSION",
        "CAPACITY_ENFORCEMENT",
        "INTERVAL_ACCOUNTING_CHECKPOINT",
    )


def test_protocol_visibility_does_not_leak_b4_lifecycle_to_lru_or_ttl() -> None:
    protocol = FROZEN_C73_RETENTION_PROTOCOL.to_dict()
    visibility = protocol["policy_visibility"]
    assert visibility["LRU_sees_lifecycle_for_ranking_or_release"] is False
    assert visibility["FIXED_TTL_sees_lifecycle_for_ranking_or_release"] is False
    assert visibility["SESSION_PINNING_sees"] == ["SessionID", "SessionLiveStatus"]
    assert visibility["LIFECYCLE_B4_sees_state_lifecycle"] is True
    assert visibility["common_invalid_state_release"] is True

    policies = protocol["policies"]
    assert policies["LRU"]["terminal_lifecycle_release"] is False
    assert policies["FIXED_TTL"]["terminal_lifecycle_release"] is False
    assert policies["LIFECYCLE_B4"]["terminal_release"] is True


def test_b4_lifecycle_priorities_and_dispositions_match_closed_policy() -> None:
    classes = FROZEN_C73_RETENTION_PROTOCOL.to_dict()["policies"]["LIFECYCLE_B4"]["classes"]
    assert classes == {
        "ACTIVE": {"priority": 3, "disposition": "PROTECT"},
        "WAITING": {"priority": 2, "disposition": "RETAIN"},
        "SPECULATIVE": {"priority": 1, "disposition": "BEST_EFFORT"},
        "TERMINAL": {"priority": 0, "disposition": "RELEASE"},
    }


def test_fixed_ttl_grid_is_predeclared_and_equal_to_tool_gap_axis() -> None:
    assert C73_PRIMARY_TTL_SECONDS == 5.0
    assert C73_TTL_SENSITIVITY_SECONDS == (0.25, 1.0, 5.0, 30.0, 120.0)
    assert C73_TTL_SENSITIVITY_SECONDS == AXES["tool_gap_seconds"].values
    policy = FROZEN_C73_RETENTION_PROTOCOL.to_dict()["policies"]["FIXED_TTL"]
    assert policy["ttl_parameter_source"] == "P-SRC4"
    assert policy["reuse_refreshes_expiry"] is False
    assert policy["expiry_at_equality_precedes_reuse"] is True
    assert "oracle-tuned sensitivity upper bound" in policy["best_of_grid_rule"]


def test_fixed_ttl_is_from_admission_and_expires_at_equality() -> None:
    expiry = fixed_ttl_expiry_seconds(admission_time_seconds=10.0, ttl_seconds=5.0)
    assert expiry == 15.0
    assert fixed_ttl_expired(now_seconds=14.999, expiry_seconds=expiry) is False
    assert fixed_ttl_expired(now_seconds=15.0, expiry_seconds=expiry) is True
    assert fixed_ttl_expired(now_seconds=16.0, expiry_seconds=expiry) is True
    with pytest.raises(ValueError, match="predeclared sensitivity"):
        fixed_ttl_expiry_seconds(admission_time_seconds=0.0, ttl_seconds=7.0)


def test_fixed_state_size_design_point_is_frozen_before_results() -> None:
    assert C73_DEFAULT_STATE_TOKENS == 16
    assert C73_DEFAULT_STATE_TOKENS == AXES["state_tokens"].reference_value
    assert C73_C6_ACCEPTED_STATE_FIXED_BYTES == 0
    assert C73_DEFAULT_STATE_BYTES == 16 * C6_STATE_BYTES_PER_TOKEN == 8_388_608
    rule = FROZEN_C73_RETENTION_PROTOCOL.to_dict()["state_size_rule"]
    assert rule["default_state_tokens"] == 16
    assert rule["default_state_tokens_source"] == "P-SRC4"
    assert rule["accepted_c6_state_fixed_bytes"] == 0
    assert rule["accepted_c6_state_bytes_per_token"] == C6_STATE_BYTES_PER_TOKEN
    assert rule["byte_mapping_evidence"] == "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"
    assert rule["state_size_map_fingerprint"] == C73_STATE_SIZE_MAP_FINGERPRINT
    assert rule["default_state_bytes"] == 8_388_608
    assert rule["new_state_size_sweep"] is False


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.25, 250), (0.5, 500), (1.0, 1000), (2.0, 2000)],
)
def test_capacity_normalization_uses_exact_frozen_ratios(ratio: float, expected: int) -> None:
    assert capacity_bytes(reference_working_set_bytes=1000, cache_capacity_ratio=ratio) == expected


def test_capacity_normalization_fails_closed_on_invalid_reference_or_ratio() -> None:
    with pytest.raises(ValueError, match="positive"):
        capacity_bytes(reference_working_set_bytes=0, cache_capacity_ratio=1.0)
    with pytest.raises(ValueError, match="frozen C7.1 axis"):
        capacity_bytes(reference_working_set_bytes=1000, cache_capacity_ratio=0.75)


def test_lru_and_lifecycle_eviction_keys_are_deterministic() -> None:
    assert lru_eviction_key(
        last_touch_time_seconds=3.0, admission_ordinal=2, state_id="s-b"
    ) < lru_eviction_key(
        last_touch_time_seconds=4.0, admission_ordinal=0, state_id="s-a"
    )
    assert lru_eviction_key(
        last_touch_time_seconds=3.0, admission_ordinal=1, state_id="s-b"
    ) < lru_eviction_key(
        last_touch_time_seconds=3.0, admission_ordinal=2, state_id="s-a"
    )
    assert lru_eviction_key(
        last_touch_time_seconds=3.0, admission_ordinal=2, state_id="s-a"
    ) < lru_eviction_key(
        last_touch_time_seconds=3.0, admission_ordinal=2, state_id="s-b"
    )

    terminal = lifecycle_eviction_key(
        lifecycle=StateLifecycle.TERMINAL,
        last_touch_time_seconds=100.0,
        admission_ordinal=10,
        state_id="terminal",
    )
    speculative = lifecycle_eviction_key(
        lifecycle=StateLifecycle.SPECULATIVE,
        last_touch_time_seconds=0.0,
        admission_ordinal=0,
        state_id="speculative",
    )
    waiting = lifecycle_eviction_key(
        lifecycle=StateLifecycle.WAITING,
        last_touch_time_seconds=0.0,
        admission_ordinal=0,
        state_id="waiting",
    )
    active = lifecycle_eviction_key(
        lifecycle=StateLifecycle.ACTIVE,
        last_touch_time_seconds=0.0,
        admission_ordinal=0,
        state_id="active",
    )
    assert terminal < speculative < waiting < active


def test_strict_pinning_and_active_protection_never_overcommit() -> None:
    assert strict_capacity_outcome(protected_bytes=100, capacity=100) is CapacityOutcome.ELIGIBLE
    assert (
        strict_capacity_outcome(protected_bytes=101, capacity=100)
        is CapacityOutcome.CAPACITY_INFEASIBLE
    )


@pytest.mark.parametrize("reason", list(ResidencyEndReason))
def test_residency_byte_seconds_partition_useful_and_wasted(reason: ResidencyEndReason) -> None:
    useful, wasted = residency_interval_byte_seconds(
        state_size_bytes=100,
        start_seconds=2.0,
        end_seconds=5.0,
        end_reason=reason,
    )
    assert useful + wasted == 300.0
    if reason is ResidencyEndReason.REUSE:
        assert useful == 300.0 and wasted == 0.0
    else:
        assert useful == 0.0 and wasted == 300.0


def test_residency_interval_handles_zero_duration_and_rejects_reverse_time() -> None:
    assert residency_interval_byte_seconds(
        state_size_bytes=100,
        start_seconds=2.0,
        end_seconds=2.0,
        end_reason=ResidencyEndReason.PROGRAM_END,
    ) == (0.0, 0.0)
    with pytest.raises(ValueError, match="cannot precede"):
        residency_interval_byte_seconds(
            state_size_bytes=100,
            start_seconds=3.0,
            end_seconds=2.0,
            end_reason=ResidencyEndReason.EVICTION,
        )


def test_tool_return_ttft_includes_queue_recompute_and_first_decode_step_only() -> None:
    # Resume at 10, service starts at 12: queue delay 2.
    # Recompute prefill 3.0; first decode step = fixed 0.5 + 0.01*100 = 1.5.
    result = tool_return_ttft_seconds(
        resume_eligibility_seconds=10.0,
        service_start_seconds=12.0,
        recompute_prefill_seconds=3.0,
        decode_fixed_seconds=0.5,
        decode_seconds_per_context_token_step=0.01,
        full_context_tokens=100,
    )
    assert math.isclose(result, 6.5)
    with pytest.raises(ValueError, match="cannot precede"):
        tool_return_ttft_seconds(
            resume_eligibility_seconds=10.0,
            service_start_seconds=9.0,
            recompute_prefill_seconds=0.0,
            decode_fixed_seconds=0.0,
            decode_seconds_per_context_token_step=0.0,
            full_context_tokens=1,
        )
    with pytest.raises(ValueError, match="positive"):
        tool_return_ttft_seconds(
            resume_eligibility_seconds=10.0,
            service_start_seconds=10.0,
            recompute_prefill_seconds=0.0,
            decode_fixed_seconds=0.1,
            decode_seconds_per_context_token_step=0.01,
            full_context_tokens=0,
        )


def test_protocol_binds_exact_parent_base_axes_statistics_and_no_results() -> None:
    payload = FROZEN_C73_RETENTION_PROTOCOL.to_dict()
    assert payload["schema"] == C73_RETENTION_PROTOCOL_SCHEMA
    assert payload["base_commit"] == C73_BASE_COMMIT
    assert payload["parent_protocol"] == {
        "schema": C7_PROTOCOL_SCHEMA,
        "fingerprint": C7_PROTOCOL_FINGERPRINT,
    }
    assert payload["comparative_result_inspection"] == "NONE"
    assert "policy_results" not in payload
    assert payload["p2_axes"] == {
        name: AXES[name].to_dict()
        for name in ("tool_gap_seconds", "tool_return_probability", "cache_capacity_ratio")
    }
    assert payload["p3_axes"] == {
        name: AXES[name].to_dict()
        for name in ("branch_width", "speculative_fraction", "cache_capacity_ratio")
    }
    assert payload["statistics"]["stochastic_seeds"] == list(C7_STOCHASTIC_SEEDS)
    assert payload["statistics"]["convergence_prefixes"] == list(C7_CONVERGENCE_PREFIXES)
    assert payload["statistics"]["bootstrap_resamples"] == C7_BOOTSTRAP_RESAMPLES
    assert payload["statistics"]["bootstrap_seed"] == C7_BOOTSTRAP_SEED
    assert FROZEN_C73_RETENTION_PROTOCOL.fingerprint == C73_RETENTION_PROTOCOL_FINGERPRINT


def _manifest(policy: RetentionPolicyID, ttl_seconds: float | None = None) -> C73RetentionManifest:
    return C73RetentionManifest(
        base_c7_manifest_fingerprint="1" * 64,
        retention_protocol_fingerprint=C73_RETENTION_PROTOCOL_FINGERPRINT,
        retention_policy_id=policy,
        ttl_seconds=ttl_seconds,
        program_case_fingerprint="2" * 64,
        state_size_map_fingerprint=C73_STATE_SIZE_MAP_FINGERPRINT,
        state_tokens=C73_DEFAULT_STATE_TOKENS,
        state_bytes=C73_DEFAULT_STATE_BYTES,
        reference_working_set_bytes=1000,
        cache_capacity_ratio=0.5,
        capacity_bytes=500,
    )


def test_retention_manifest_schema_identity_and_capacity_binding() -> None:
    manifest = _manifest(RetentionPolicyID.LRU)
    payload = manifest.to_dict()
    assert payload["schema"] == C73_RETENTION_MANIFEST_SCHEMA
    assert payload["retention_protocol_fingerprint"] == C73_RETENTION_PROTOCOL_FINGERPRINT
    assert payload["retention_policy_id"] == "LRU"
    assert payload["capacity_bytes"] == 500
    assert len(manifest.fingerprint) == 64
    assert manifest.fingerprint == _manifest(RetentionPolicyID.LRU).fingerprint


def test_retention_manifest_ttl_field_is_policy_specific_and_predeclared() -> None:
    assert _manifest(RetentionPolicyID.FIXED_TTL, 5.0).ttl_seconds == 5.0
    with pytest.raises(ValueError, match="requires ttl_seconds"):
        _manifest(RetentionPolicyID.FIXED_TTL)
    with pytest.raises(ValueError, match="predeclared sensitivity"):
        _manifest(RetentionPolicyID.FIXED_TTL, 7.0)
    with pytest.raises(ValueError, match="only for FIXED_TTL"):
        _manifest(RetentionPolicyID.LRU, 5.0)


def test_retention_manifest_rejects_protocol_or_capacity_drift() -> None:
    with pytest.raises(ValueError, match="protocol fingerprint"):
        C73RetentionManifest(
            base_c7_manifest_fingerprint="1" * 64,
            retention_protocol_fingerprint="0" * 64,
            retention_policy_id=RetentionPolicyID.LRU,
            ttl_seconds=None,
            program_case_fingerprint="2" * 64,
            state_size_map_fingerprint=C73_STATE_SIZE_MAP_FINGERPRINT,
        state_tokens=C73_DEFAULT_STATE_TOKENS,
        state_bytes=C73_DEFAULT_STATE_BYTES,
            reference_working_set_bytes=1000,
            cache_capacity_ratio=0.5,
            capacity_bytes=500,
        )
    with pytest.raises(ValueError, match="capacity_bytes"):
        C73RetentionManifest(
            base_c7_manifest_fingerprint="1" * 64,
            retention_protocol_fingerprint=C73_RETENTION_PROTOCOL_FINGERPRINT,
            retention_policy_id=RetentionPolicyID.SESSION_PINNING,
            ttl_seconds=None,
            program_case_fingerprint="2" * 64,
            state_size_map_fingerprint=C73_STATE_SIZE_MAP_FINGERPRINT,
        state_tokens=C73_DEFAULT_STATE_TOKENS,
        state_bytes=C73_DEFAULT_STATE_BYTES,
            reference_working_set_bytes=1000,
            cache_capacity_ratio=0.5,
            capacity_bytes=501,
        )


def test_tool_return_ttft_fails_closed_at_c6_first_decode_context_boundary() -> None:
    accepted = tool_return_ttft_seconds(
        resume_eligibility_seconds=0.0, service_start_seconds=0.0,
        recompute_prefill_seconds=0.0, decode_fixed_seconds=0.1,
        decode_seconds_per_context_token_step=0.001,
        full_context_tokens=C6_MAX_MODEL_LENGTH - 1,
    )
    assert accepted > 0.0
    with pytest.raises(ValueError, match="accepted C6 first-token decode domain 1..4095"):
        tool_return_ttft_seconds(
            resume_eligibility_seconds=0.0, service_start_seconds=0.0,
            recompute_prefill_seconds=0.0, decode_fixed_seconds=0.1,
            decode_seconds_per_context_token_step=0.001,
            full_context_tokens=C6_MAX_MODEL_LENGTH,
        )


def test_protocol_serializes_finite_first_decode_context_fence() -> None:
    rule = FROZEN_C73_RETENTION_PROTOCOL.to_dict()["tool_return_ttft"]
    assert rule["positive_context_required"] is True
    assert rule["max_first_decode_context_tokens"] == C6_MAX_MODEL_LENGTH - 1 == 4095
