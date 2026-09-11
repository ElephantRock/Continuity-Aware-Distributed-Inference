from __future__ import annotations

from experiments.c73_retention_protocol import (
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    C73_STATE_SIZE_MAP_FINGERPRINT,
)

EXPECTED_C73_RETENTION_PROTOCOL_FINGERPRINT = (
    "ab3ade7ea5ebe98f8a1e359256ceb9cf07ad48acee431cea5f36dc7aca3e5622"
)
EXPECTED_C73_STATE_SIZE_MAP_FINGERPRINT = (
    "36bacaad6462ca3e5eeb2688040e4dcf0d829849158c5b5ad9bad3c58afe173b"
)


def test_retention_protocol_fingerprint_is_independently_frozen() -> None:
    assert C73_RETENTION_PROTOCOL_FINGERPRINT == EXPECTED_C73_RETENTION_PROTOCOL_FINGERPRINT


def test_state_size_map_fingerprint_is_independently_frozen() -> None:
    assert C73_STATE_SIZE_MAP_FINGERPRINT == EXPECTED_C73_STATE_SIZE_MAP_FINGERPRINT
