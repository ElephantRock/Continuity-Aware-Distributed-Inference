from __future__ import annotations

from experiments.c73_retention_protocol import (
    C73_RETENTION_PROTOCOL_FINGERPRINT, C73_STATE_SIZE_MAP_FINGERPRINT,
)

EXPECTED_C73_STATE_SIZE_MAP_FINGERPRINT = "36bacaad6462ca3e5eeb2688040e4dcf0d829849158c5b5ad9bad3c58afe173b"

def test_candidate_retention_protocol_fingerprint_is_canonical_sha256() -> None:
    assert len(C73_RETENTION_PROTOCOL_FINGERPRINT) == 64
    int(C73_RETENTION_PROTOCOL_FINGERPRINT, 16)

def test_state_size_map_fingerprint_remains_independently_frozen() -> None:
    assert C73_STATE_SIZE_MAP_FINGERPRINT == EXPECTED_C73_STATE_SIZE_MAP_FINGERPRINT
