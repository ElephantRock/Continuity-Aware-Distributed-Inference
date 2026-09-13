from __future__ import annotations

import re

import pytest

from experiments.c74_migration_protocol import (
    C74_EFFICIENCY_NOT_STRENGTHENED,
    C74_EFFICIENCY_STRENGTHENED,
    C74_PARTIAL_UNRANKED_LABEL,
    C74_RANKABLE_SCENARIOS,
    C74_UNRANKED_SCENARIOS,
    p5_cells,
    p5_cells_adjacent,
)
from experiments.c74_migration_results import (
    C74C_BASE_COMMIT,
    C74C_COMPARATIVE_RESULT_INSPECTION,
    C74C_EXPECTED_RANKABLE_TRACK_B_ROWS,
    C74C_EXPECTED_TRACK_A_ROWS,
    C74C_EXPECTED_TRACK_B_ROWS,
    C74C_EXPECTED_UNRANKED_TRACK_B_ROWS,
    C74C_FROZEN_C74B_ENGINE_FINGERPRINT,
    C74C_HARDWARE_ORDER,
    C74C_POLICY_ORDER,
    C74C_RESULT_SCHEMA,
    _connected_components,
    generate_result,
)
from simulator.policies import PolicyID


EXECUTION_SHA = "a" * 40
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_connected(cells: tuple[tuple[int, int], ...]) -> bool:
    if not cells:
        return False
    seen = {cells[0]}
    frontier = [cells[0]]
    while frontier:
        current = frontier.pop()
        for candidate in cells:
            if candidate in seen:
                continue
            if p5_cells_adjacent(current, candidate):
                seen.add(candidate)
                frontier.append(candidate)
    return len(seen) == len(cells)


def test_frozen_result_executor_identity_is_exact() -> None:
    assert C74C_BASE_COMMIT == "9227e36883af785aef1dc7d253404cee0e8675d1"
    assert (
        C74C_FROZEN_C74B_ENGINE_FINGERPRINT
        == "2083e1a227330ea1a0ac57caf166cf8777865cbe6e3c6d259796cfd56fe02c32"
    )
    assert C74C_COMPARATIVE_RESULT_INSPECTION == "EXECUTED_FROZEN_C7.4C"
    assert C74C_HARDWARE_ORDER == ("a100-80gb", "h100-80gb")
    assert C74C_POLICY_ORDER == tuple(PolicyID)
    assert C74C_EXPECTED_TRACK_A_ROWS == 40
    assert C74C_EXPECTED_TRACK_B_ROWS == 1200
    assert C74C_EXPECTED_RANKABLE_TRACK_B_ROWS == 1000
    assert C74C_EXPECTED_UNRANKED_TRACK_B_ROWS == 200


def test_invalid_execution_sha_fails_closed() -> None:
    for bad in ("", "a" * 39, "A" * 40, "not-a-sha"):
        with pytest.raises(ValueError, match="40-hex Git commit"):
            generate_result(bad)


def test_connected_components_use_only_frozen_p5_adjacency() -> None:
    cells = ((1, 64), (1, 256), (4, 256), (64, 4096))
    components = _connected_components(cells)
    assert components == (((1, 64), (1, 256), (4, 256)), ((64, 4096),))
    assert _is_connected(components[0])
    assert not p5_cells_adjacent((1, 64), (4, 256))


def test_exhaustive_result_is_canonical_complete_and_fingerprint_stable() -> None:
    result = generate_result(EXECUTION_SHA)
    assert result["schema"] == C74C_RESULT_SCHEMA
    assert result["execution_git_commit"] == EXECUTION_SHA
    assert result["base_commit"] == C74C_BASE_COMMIT
    assert result["comparative_result_inspection"] == C74C_COMPARATIVE_RESULT_INSPECTION
    assert _SHA256_RE.fullmatch(result["scientific_fingerprint"])
    assert result["evidence"]["direct_hardware_measurement_claim"] is False
    assert result["row_counts"] == {
        "track_a": 40,
        "track_b": 1200,
        "track_b_rankable": 1000,
        "track_b_partial_unranked": 200,
    }

    track_a = result["track_a_rows"]
    assert len(track_a) == 40
    assert track_a[0]["result"]["hardware_id"] == "a100-80gb"
    assert (
        track_a[0]["result"]["state_tokens"],
        track_a[0]["result"]["recompute_tokens"],
    ) == p5_cells()[0]
    assert track_a[-1]["result"]["hardware_id"] == "h100-80gb"
    assert (
        track_a[-1]["result"]["state_tokens"],
        track_a[-1]["result"]["recompute_tokens"],
    ) == p5_cells()[-1]
    assert all(_SHA256_RE.fullmatch(row["manifest_fingerprint"]) for row in track_a)
    assert all(_SHA256_RE.fullmatch(row["result_fingerprint"]) for row in track_a)

    track_b = result["track_b_rows"]
    assert len(track_b) == 1200
    first = track_b[0]["result"]
    assert first["scenario_id"] == C74_RANKABLE_SCENARIOS[0].value
    assert first["policy_id"] == PolicyID.B0.value
    assert first["hardware_id"] == "a100-80gb"
    assert (first["state_tokens"], first["recompute_tokens"]) == p5_cells()[0]
    last = track_b[-1]["result"]
    assert last["scenario_id"] == C74_UNRANKED_SCENARIOS[0].value
    assert last["policy_id"] == PolicyID.B4.value
    assert last["hardware_id"] == "h100-80gb"
    assert (last["state_tokens"], last["recompute_tokens"]) == p5_cells()[-1]
    for row in track_b:
        assert _SHA256_RE.fullmatch(row["base_manifest_fingerprint"])
        assert _SHA256_RE.fullmatch(row["failover_manifest_fingerprint"])
        assert _SHA256_RE.fullmatch(row["result_fingerprint"])
        assert row["result"]["direct_hardware_measurement_claim"] is False

    partial = [
        row["result"]
        for row in track_b
        if row["result"]["scenario_id"] == C74_UNRANKED_SCENARIOS[0].value
    ]
    assert len(partial) == 200
    assert all(row["efficiency_status"] == C74_PARTIAL_UNRANKED_LABEL for row in partial)
    assert all(row["efficiency_eligible"] is False for row in partial)

    h6 = result["h6_efficiency"]
    assert h6["h6_efficiency_classification"] in {
        C74_EFFICIENCY_STRENGTHENED,
        C74_EFFICIENCY_NOT_STRENGTHENED,
    }
    assert h6["support_region_count"] == len(h6["support_regions"])
    assert h6["track_a_can_support"] is False
    assert h6["partial_materialization_can_support"] is False
    assert h6["correctness_component_reopened"] is False
    for region in h6["support_regions"]:
        cells = tuple(
            (cell["state_tokens"], cell["recompute_tokens"])
            for cell in region["cells"]
        )
        assert region["cell_count"] == len(cells)
        assert len(cells) >= 2
        assert _is_connected(cells)
