from __future__ import annotations

import pytest

from experiments.c73_retention_engine import RetentionPolicyResult
from experiments.c73_retention_protocol import (
    CapacityOutcome,
    RetentionPolicyID,
)
from experiments.c73c_execute import (
    C73C2_BASE_COMMIT,
    C73C2_COMPARABLE,
    C73C2_FROZEN_PROTOCOL_FINGERPRINT,
    C73C2_HARDWARE_STRATA,
    C73C2_PRIMARY_BASELINES,
    C73C2_SEMANTIC_INVALID,
    C73C2_TTFT_DESCRIPTIVE_ONLY,
    _bootstrap_indices_cached,
    _ratio_components_dict,
    ccr_from_rr_comparison,
    logical_primary_cells,
    paired_mean_comparison,
    paired_ratio_comparison,
    policy_key,
    support_components_for_surface,
    wsr_from_usr_comparison,
)
from experiments.c73c_protocol import (
    C73C_PROTOCOL_FINGERPRINT,
    C73CSurfaceID,
    RatioComponents,
    bootstrap_resample_indices,
)


def test_execution_parent_identity_and_baselines_are_exact() -> None:
    assert C73C2_BASE_COMMIT == "e8eb46b482c89c9bd32d820c1ef5cbecbff15d66"
    assert (
        C73C2_FROZEN_PROTOCOL_FINGERPRINT
        == C73C_PROTOCOL_FINGERPRINT
        == "694f3c4e24ecca61b5c265e26f8417eafbcd4c932af3f9e60813fbb76d1b6800"
    )
    assert C73C2_PRIMARY_BASELINES == (
        "LRU",
        "FIXED_TTL(5s)",
        "SESSION_PINNING",
    )
    assert C73C2_HARDWARE_STRATA == ("a100-80gb", "h100-80gb")


def test_policy_key_preserves_primary_ttl_and_sensitivities() -> None:
    assert policy_key(RetentionPolicyID.LRU) == "LRU"
    assert policy_key(RetentionPolicyID.SESSION_PINNING) == "SESSION_PINNING"
    assert policy_key(RetentionPolicyID.LIFECYCLE_B4) == "LIFECYCLE_B4"
    assert policy_key(RetentionPolicyID.FIXED_TTL, 5.0) == "FIXED_TTL(5s)"
    assert policy_key(RetentionPolicyID.FIXED_TTL, 0.25) == "FIXED_TTL(0.25s)"
    with pytest.raises(ValueError, match="requires ttl_seconds"):
        policy_key(RetentionPolicyID.FIXED_TTL)
    with pytest.raises(ValueError, match="valid only"):
        policy_key(RetentionPolicyID.LRU, 5.0)


def test_compact_surfaces_collapse_to_51_unique_logical_cells() -> None:
    cells = logical_primary_cells()
    assert len(cells) == 51
    assert sum(len(cell.memberships) for cell in cells) == 56
    assert sum(len(cell.memberships) == 2 for cell in cells) == 5
    assert len({cell.cell_id for cell in cells}) == 51


def test_cached_bootstrap_schedule_is_exact_frozen_schedule() -> None:
    observed = _bootstrap_indices_cached(8, 0)
    assert observed == bootstrap_resample_indices(8, 0)
    assert _bootstrap_indices_cached(8, 0) is observed


def test_paired_ratio_zero_difference_is_not_favorable() -> None:
    b4 = tuple(RatioComponents(1.0, 2.0) for _ in range(8))
    baseline = tuple(RatioComponents(1.0, 2.0) for _ in range(8))
    result = paired_ratio_comparison(b4, baseline)
    assert result["status"] == C73C2_COMPARABLE
    assert result["point_difference"] == pytest.approx(0.0)
    assert result["ci95"] == pytest.approx([0.0, 0.0])


def test_paired_ratio_fails_closed_on_zero_denominator() -> None:
    b4 = (RatioComponents(0.0, 0.0), RatioComponents(0.0, 0.0))
    baseline = (RatioComponents(0.0, 0.0), RatioComponents(0.0, 0.0))
    result = paired_ratio_comparison(b4, baseline)
    assert result["status"] == "INSUFFICIENT_METRIC_DENOMINATOR"
    assert result["ci95"] is None
    assert result["favorable"] is False


def test_wsr_and_ccr_use_only_frozen_exact_algebraic_identities() -> None:
    usr = {
        "status": C73C2_COMPARABLE,
        "point_difference": 0.2,
        "ci95": [0.1, 0.3],
        "favorable": True,
    }
    wsr = wsr_from_usr_comparison(usr)
    assert wsr["point_difference"] == pytest.approx(-0.2)
    assert wsr["ci95"] == pytest.approx([-0.3, -0.1])
    assert wsr["favorable"] is True
    assert wsr["derived_from"] == "EXACT_USR_COMPLEMENT"

    rr = {
        "status": C73C2_COMPARABLE,
        "point_difference": -0.125,
        "ci95": [-0.25, -0.01],
        "favorable": True,
    }
    ccr = ccr_from_rr_comparison(rr)
    assert ccr["point_difference"] == rr["point_difference"]
    assert ccr["ci95"] == rr["ci95"]
    assert ccr["favorable"] is True
    assert ccr["derived_from"] == "EXACT_RR_FIXED_1024_TOKEN_IDENTITY"


def test_semantic_rejection_fails_closed_without_efficiency_components() -> None:
    result = RetentionPolicyResult(
        base_c7_manifest_fingerprint="0" * 64,
        retention_manifest_fingerprint="1" * 64,
        retention_protocol_fingerprint="2" * 64,
        program_case_fingerprint="3" * 64,
        policy_id=RetentionPolicyID.LRU,
        capacity_outcome=CapacityOutcome.ELIGIBLE,
        eligible_reuse_opportunities=1,
        consumed_reuse_opportunities=0,
        semantic_rejection_count=1,
        useful_byte_seconds=0.0,
        wasted_byte_seconds=1.0,
        total_classified_byte_seconds=1.0,
        useful_residency_fraction=0.0,
        wasted_residency_fraction=1.0,
        max_resident_bytes=0,
        capacity_bytes=1,
        audit=(),
    )
    assert _ratio_components_dict(result) is None
    assert C73C2_SEMANTIC_INVALID == "SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING"


def test_ttft_minimum_sample_and_favorable_direction() -> None:
    descriptive = paired_mean_comparison(
        tuple(1.0 for _ in range(7)),
        tuple(2.0 for _ in range(7)),
        minimum_sample_size=8,
    )
    assert descriptive["status"] == C73C2_TTFT_DESCRIPTIVE_ONLY
    assert descriptive["sample_size"] == 7
    assert descriptive["favorable"] is False

    inferential = paired_mean_comparison(
        tuple(1.0 for _ in range(8)),
        tuple(2.0 for _ in range(8)),
        minimum_sample_size=8,
    )
    assert inferential["status"] == C73C2_COMPARABLE
    assert inferential["point_difference"] == pytest.approx(-1.0)
    assert inferential["ci95"] == pytest.approx([-1.0, -1.0])
    assert inferential["favorable"] is True


def test_support_region_requires_two_adjacent_cells_on_same_surface() -> None:
    cells = logical_primary_cells()
    summaries = {
        cell.cell_id: {
            "h5_support_cell": {
                "USR": False,
                "RR": False,
                "CCR": False,
                "P2_TOOL_RETURN_TTFT": False,
            }
        }
        for cell in cells
    }

    target_members = []
    for logical in cells:
        for member in logical.memberships:
            if (
                member.surface_id is C73CSurfaceID.P2_GAP_CACHE
                and member.y_value == 1.0
                and member.x_value in (1.0, 5.0)
            ):
                summaries[logical.cell_id]["h5_support_cell"]["USR"] = True
                target_members.append((member.x_value, logical.cell_id))
    assert len(target_members) == 2

    components = support_components_for_surface(
        C73CSurfaceID.P2_GAP_CACHE,
        "USR",
        summaries,
        cells,
    )
    assert len(components) == 1
    assert len(components[0]) == 2
    assert {item["x_value"] for item in components[0]} == {1.0, 5.0}


def test_isolated_support_cell_is_not_a_meaningful_range() -> None:
    cells = logical_primary_cells()
    summaries = {
        cell.cell_id: {
            "h5_support_cell": {
                "USR": False,
                "RR": False,
                "CCR": False,
                "P2_TOOL_RETURN_TTFT": False,
            }
        }
        for cell in cells
    }
    for logical in cells:
        for member in logical.memberships:
            if (
                member.surface_id is C73CSurfaceID.P3_WIDTH_CACHE
                and member.x_value == 4
                and member.y_value == 1.0
            ):
                summaries[logical.cell_id]["h5_support_cell"]["RR"] = True

    assert (
        support_components_for_surface(
            C73CSurfaceID.P3_WIDTH_CACHE,
            "RR",
            summaries,
            cells,
        )
        == []
    )
