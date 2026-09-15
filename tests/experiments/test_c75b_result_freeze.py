from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.c75_g2_run import C75B_STAGE1_REVIEW_SHA, execution_plan


FROZEN_C75B_GENERATION_SHA = "9ecb0d0f7c825d5da19c9b3814e4a1ce489b1b59"
FROZEN_C75B_STAGE1_REVIEW_SHA = "bf9af8fa54a2948c9fa4a77f3a1325ecbf5e2e68"
EXPECTED_C75B_SCIENTIFIC_FINGERPRINT = (
    "3bfcfa2a140757ebdfb9bf06d9e167efb6d93ffec5f3cae5ca8c712ff84e64bc"
)
EXPECTED_C75B_RESULT_SHA256 = (
    "ce3a34a13d885ee6ef5e3d9cbab392bb5ec9e69df89b7d143a8a7c348bb68bb6"
)
EXPECTED_C75B_PROGRAM_ROWS_SHA256 = (
    "95adba17f8048ecbca3bd96affef363e7366f664abe189bca1c7b116118ac534"
)
EXPECTED_C75B_MANIFEST_SHA256 = (
    "d3f13149e59bf11aae47a61655121d735df9a9a8d91cc3c0a51930fdf0a8123c"
)
EXPECTED_GATE_G2 = "G2_A_STRONG_EFFICIENCY_SUPPORT"


def test_c75b_predeclared_execution_contract_remains_frozen() -> None:
    assert C75B_STAGE1_REVIEW_SHA == FROZEN_C75B_STAGE1_REVIEW_SHA
    plan = execution_plan()
    assert plan["program_row_count"] == 60160
    assert plan["p1_cells"] == 75
    assert plan["p4_cells"] == 16
    assert plan["p7_cells"] == 3
    assert plan["seed_count"] == 64
    assert plan["hardware_strata"] == ["a100-80gb", "h100-80gb"]


def test_committed_c75b_result_manifest_matches_literal_freeze() -> None:
    path = Path("artifacts/c7.5b/g2-result-manifest.json")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPECTED_C75B_MANIFEST_SHA256
    manifest = json.loads(raw)

    artifact = manifest["canonical_artifact"]
    assert artifact["generation_git_commit"] == FROZEN_C75B_GENERATION_SHA
    assert artifact["stage1_review_sha"] == FROZEN_C75B_STAGE1_REVIEW_SHA
    assert artifact["scientific_fingerprint"] == EXPECTED_C75B_SCIENTIFIC_FINGERPRINT
    assert artifact["result_json_sha256"] == EXPECTED_C75B_RESULT_SHA256
    assert artifact["program_rows_sha256"] == EXPECTED_C75B_PROGRAM_ROWS_SHA256
    assert artifact["program_row_count"] == 60160

    assert manifest["coverage"]["semantic_violation_rows"] == 0
    assert manifest["coverage"]["efficiency_eligible_rows"] == 60160
    assert manifest["h4"]["decision"] == "SUPPORTED_WITHIN_DECLARED_PHASE_SPACE"
    assert manifest["h4"]["qualifying_region_sizes"] == {
        "B0": [20, 20, 20],
        "B1": [],
        "B2": [20, 20, 20],
    }
    assert manifest["h7"]["decision"] == "SUPPORTED_WITHIN_DECLARED_PHASE_SPACE"
    assert manifest["h7"]["p7_negative_control_pass"] is True
    assert manifest["h7"]["qualifying_run_count"] == 65
    assert manifest["h7"]["qualifying_runs_by_axis"] == {
        "P1_DEEP_REUSE.reusable_prefix_fraction": 45,
        "P1_DEEP_REUSE.session_depth": 20,
        "P4_FANOUT_SHARED_PREFIX.fanout_width": 0,
        "P4_FANOUT_SHARED_PREFIX.shared_prefix_fraction": 0,
    }
    assert manifest["gate_g2"]["classification"] == EXPECTED_GATE_G2
    assert manifest["independent_raw_row_audit"]["h4_recomputed_exact_match"] is True
    assert manifest["independent_raw_row_audit"]["h7_qualifying_run_signatures_exact_match"] is True
    assert manifest["evidence"]["direct_hardware_measurement_claim"] is False
    assert manifest["evidence"]["universal_superiority_claim"] is False
