from experiments.c74_migration_engine import engine_identity


EXPECTED_C74B_ENGINE_FINGERPRINT = (
    "2083e1a227330ea1a0ac57caf166cf8777865cbe6e3c6d259796cfd56fe02c32"
)


def test_c74b_engine_fingerprint_is_independently_frozen() -> None:
    identity = engine_identity()
    assert identity["engine_fingerprint"] == EXPECTED_C74B_ENGINE_FINGERPRINT
    assert identity["comparative_result_inspection"] == "NONE"
    assert identity["full_comparative_executor_present"] is False
