from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.c74_migration_results import _json, generate_result


FROZEN_C74C_GENERATION_SHA = "4e9d9ad5fd119a266dfb9282274cd414354d0d72"
EXPECTED_C74C_SCIENTIFIC_FINGERPRINT = (
    "a8d435456b1d784592eda96e3352190456fb9aeb34dda6c12c5a0edb58b54944"
)
EXPECTED_C74C_ARTIFACT_SHA256 = (
    "3550516d9d2355849f5d6a731d7577f906856ec94a60d8947ad81e8382e25e18"
)
EXPECTED_C74C_MANIFEST_SHA256 = (
    "84c7f62ecf9f799cc87f1750cfd58507d760d69afba6321f97471540410ebee7"
)
EXPECTED_H6_EFFICIENCY = "EFFICIENCY_STRENGTHENED_WITHIN_DECLARED_PHASE_SPACE"


def test_c74c_result_identity_is_independently_frozen() -> None:
    result = generate_result(FROZEN_C74C_GENERATION_SHA)
    assert result["scientific_fingerprint"] == EXPECTED_C74C_SCIENTIFIC_FINGERPRINT
    canonical_bytes = (_json(result) + "\n").encode("utf-8")
    assert hashlib.sha256(canonical_bytes).hexdigest() == EXPECTED_C74C_ARTIFACT_SHA256
    assert result["h6_efficiency"]["h6_efficiency_classification"] == EXPECTED_H6_EFFICIENCY
    assert result["h6_efficiency"]["support_region_count"] == 6
    assert result["row_counts"] == {
        "track_a": 40,
        "track_b": 1200,
        "track_b_rankable": 1000,
        "track_b_partial_unranked": 200,
    }


def test_committed_result_manifest_matches_literal_freeze() -> None:
    path = Path("artifacts/c7.4c/migration-efficiency-result-manifest.json")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPECTED_C74C_MANIFEST_SHA256
    manifest = json.loads(raw)
    artifact = manifest["canonical_artifact"]
    assert artifact["generation_git_commit"] == FROZEN_C74C_GENERATION_SHA
    assert artifact["scientific_fingerprint"] == EXPECTED_C74C_SCIENTIFIC_FINGERPRINT
    assert artifact["artifact_json_sha256"] == EXPECTED_C74C_ARTIFACT_SHA256
    assert manifest["h6_efficiency"]["h6_efficiency_classification"] == EXPECTED_H6_EFFICIENCY
    assert manifest["h6_efficiency"]["support_region_count"] == 6
    assert manifest["evidence"]["direct_hardware_measurement_claim"] is False
