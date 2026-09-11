from __future__ import annotations

from experiments.c73_retention_protocol import (
    C73_RETENTION_PROTOCOL_FINGERPRINT,
    C73_STATE_SIZE_MAP_FINGERPRINT,
)

EXPECTED_C73_RETENTION_PROTOCOL_FINGERPRINT = (
    "f6165cb9248f5f4292846942e797e5ddcd8d004027ad503b2a7de35210021379"
)
EXPECTED_C73_STATE_SIZE_MAP_FINGERPRINT = (
    "36bacaad6462ca3e5eeb2688040e4dcf0d829849158c5b5ad9bad3c58afe173b"
)


def test_retention_protocol_fingerprint_is_independently_frozen() -> None:
    assert C73_RETENTION_PROTOCOL_FINGERPRINT == EXPECTED_C73_RETENTION_PROTOCOL_FINGERPRINT


def test_state_size_map_fingerprint_remains_independently_frozen() -> None:
    assert C73_STATE_SIZE_MAP_FINGERPRINT == EXPECTED_C73_STATE_SIZE_MAP_FINGERPRINT
