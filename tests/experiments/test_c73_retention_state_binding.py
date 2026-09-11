from __future__ import annotations

import pytest

from experiments.c73_retention_protocol import (
    C73_C6_ACCEPTED_STATE_FIXED_BYTES,
    C73_DEFAULT_STATE_BYTES,
    C73_DEFAULT_STATE_TOKENS,
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    C73_STATE_SIZE_MAP,
    C73_STATE_SIZE_MAP_FINGERPRINT,
    C73RetentionManifest,
    FROZEN_C73_RETENTION_PROTOCOL,
    RetentionPolicyID,
)


def _manifest(**overrides) -> C73RetentionManifest:
    values = dict(
        base_c7_manifest_fingerprint="1" * 64,
        retention_protocol_fingerprint=C73_RETENTION_PROTOCOL_FINGERPRINT,
        retention_policy_id=RetentionPolicyID.LRU,
        ttl_seconds=None,
        program_case_fingerprint="2" * 64,
        state_size_map_fingerprint=C73_STATE_SIZE_MAP_FINGERPRINT,
        state_tokens=C73_DEFAULT_STATE_TOKENS,
        state_bytes=C73_DEFAULT_STATE_BYTES,
        reference_working_set_bytes=16_777_216,
        cache_capacity_ratio=0.5,
        capacity_bytes=8_388_608,
    )
    values.update(overrides)
    return C73RetentionManifest(**values)


def test_accepted_c64f_state_memory_mapping_is_the_frozen_c73_reference() -> None:
    assert C73_C6_ACCEPTED_STATE_FIXED_BYTES == 0
    assert C73_DEFAULT_STATE_TOKENS == 16
    assert C73_DEFAULT_STATE_BYTES == 8_388_608
    assert C73_STATE_SIZE_MAP == {
        "schema": "cadi.c7.3a.state-size-map.v1",
        "state_tokens": 16,
        "state_fixed_bytes": 0,
        "state_bytes_per_token": 524288,
        "state_bytes": 8_388_608,
        "design_source": "P-SRC4",
        "byte_mapping_evidence": "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2",
    }
    rule = FROZEN_C73_RETENTION_PROTOCOL.to_dict()["state_size_rule"]
    assert rule["state_size_map_fingerprint"] == C73_STATE_SIZE_MAP_FINGERPRINT
    assert len(C73_STATE_SIZE_MAP_FINGERPRINT) == 64


def test_retention_manifest_serializes_explicit_frozen_state_size() -> None:
    payload = _manifest().to_dict()
    assert payload["state_size_map_fingerprint"] == C73_STATE_SIZE_MAP_FINGERPRINT
    assert payload["state_tokens"] == 16
    assert payload["state_bytes"] == 8_388_608


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"state_size_map_fingerprint": "3" * 64}, "State-size map"),
        ({"state_tokens": 4}, "design point"),
        ({"state_bytes": 2_097_152}, "State byte mapping"),
    ],
)
def test_retention_manifest_fails_closed_on_state_size_drift(override, message) -> None:
    with pytest.raises(ValueError, match=message):
        _manifest(**override)
