from experiments.c74_migration_protocol import (
    C74_PROTOCOL_FINGERPRINT,
    C74MigrationEfficiencyProtocol,
    protocol_identity,
)


EXPECTED_C74_PROTOCOL_FINGERPRINT = (
    "21601257767442204f1acfae4c6e2e42959e37701c9236a2d2b560a2dda486b6"
)


def test_c74_protocol_fingerprint_is_independently_frozen() -> None:
    assert C74_PROTOCOL_FINGERPRINT == EXPECTED_C74_PROTOCOL_FINGERPRINT
    assert C74MigrationEfficiencyProtocol().fingerprint == EXPECTED_C74_PROTOCOL_FINGERPRINT
    assert protocol_identity()["protocol_fingerprint"] == EXPECTED_C74_PROTOCOL_FINGERPRINT
