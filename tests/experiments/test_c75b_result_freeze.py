from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.c75_g2_run import C75B_STAGE1_REVIEW_SHA, execution_plan


FROZEN_C75B_GENERATION_SHA = "823bcb1f6ad9178d21052eec81934fb0199e1efa"
FROZEN_C75B_PRE_RESULT_REVIEW_SHA = "4bd4a8d268356daf1c856512a34b1b5a02cd8a31"
FROZEN_C75B_STAGE1_REVIEW_SHA = "bf9af8fa54a2948c9fa4a77f3a1325ecbf5e2e68"
EXPECTED_C75B_SCIENTIFIC_FINGERPRINT = "1ff70aa4985743483ca897120e819889846576a66d320ec23674406919ba2b17"
EXPECTED_C75B_RESULT_SHA256 = "f3eb3b8c5ee9e031aa9d3a1465e7e50c995cc6dab1020c3ee03662bfaa6765d7"
EXPECTED_C75B_SUMMARY_SHA256 = "e20e2cfb517007ae197f4ecc13d25995f49e43721850a4a0b4402cc0e28a63bd"
EXPECTED_C75B_PROGRAM_ROWS_SHA256 = "63692ca2b5940649c83e8d3e9cd2794eb93521bbbbcfa57b77a2d71bac5474be"
EXPECTED_C75B_MANIFEST_SHA256 = "774eb4e59ba61f254b6fd5d4b5db908649ca5ce42cb93d1b9e2296da3208b3d5"
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
    assert artifact["pre_result_review_sha"] == FROZEN_C75B_PRE_RESULT_REVIEW_SHA
    assert artifact["stage1_review_sha"] == FROZEN_C75B_STAGE1_REVIEW_SHA
    assert artifact["scientific_fingerprint"] == EXPECTED_C75B_SCIENTIFIC_FINGERPRINT
    assert artifact["result_json_sha256"] == EXPECTED_C75B_RESULT_SHA256
    assert artifact["summary_json_sha256"] == EXPECTED_C75B_SUMMARY_SHA256
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
    assert manifest["independent_raw_row_audit"]["h4_support_exact_match"] is True
    assert manifest["independent_raw_row_audit"]["h7_qualifying_run_signatures_exact_match"] is True
    assert manifest["supersession"]["status"] == "SUPERSEDED_NON_FINAL_EVIDENCE"
    assert manifest["evidence"]["direct_hardware_measurement_claim"] is False
    assert manifest["evidence"]["physical_routing_overhead_claim"] is False
    assert manifest["evidence"]["production_prevalence_claim"] is False
    assert manifest["evidence"]["universal_superiority_claim"] is False
    assert manifest["evidence"]["correctness_reopened"] is False
