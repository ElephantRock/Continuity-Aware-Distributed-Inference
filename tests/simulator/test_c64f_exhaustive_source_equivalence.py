import math

from experiments.c64f_exhaustive_source_equivalence import (
    C64F_EXPECTED_PROTOCOL_FINGERPRINT,
    _family_report,
    _ulp_distance,
)
from simulator.inference_cost_v4 import (
    C64E_EQUIVALENCE_ULP_BUDGET,
    C64E_EXHAUSTIVE_PROTOCOL,
)


def test_c64f_binds_exact_c64e_protocol_before_source_evaluation() -> None:
    assert C64E_EXHAUSTIVE_PROTOCOL.fingerprint == C64F_EXPECTED_PROTOCOL_FINGERPRINT
    record = C64E_EXHAUSTIVE_PROTOCOL.to_dict()
    assert record["contains_reference_timings"] is False
    assert record["source_predictions_evaluated_by_this_protocol_freeze"] is False
    assert record["boundary_accounting"]["replacement_fresh_boundary"] is None
    assert record["boundary_accounting"]["holdout_claim"] is False


def test_ulp_distance_counts_representable_steps_across_binade_boundary() -> None:
    left = math.nextafter(1.0, 0.0)
    right = math.nextafter(1.0, math.inf)
    assert _ulp_distance(left, 1.0) == 1
    assert _ulp_distance(1.0, right) == 1
    assert _ulp_distance(left, right) == 2
    assert _ulp_distance(-0.0, 0.0) == 0


def test_family_report_requires_every_point_within_ulp_budget() -> None:
    source = [1.0, 2.0, 3.0]
    projected = list(source)
    report = _family_report(
        family="synthetic",
        axes=range(1, 4),
        source_values=source,
        projected_values=projected,
    )
    assert report["decision"] == "PASS"
    assert report["ulp_violation_count"] == 0
    assert report["max_ulp_distance"] == 0

    drifted = math.nextafter(3.0, math.inf)
    for _ in range(C64E_EQUIVALENCE_ULP_BUDGET):
        drifted = math.nextafter(drifted, math.inf)
    failed = _family_report(
        family="synthetic",
        axes=range(1, 4),
        source_values=source,
        projected_values=[1.0, 2.0, drifted],
    )
    assert failed["decision"] == "FAIL"
    assert failed["ulp_violation_count"] == 1
    assert failed["max_ulp_distance"] == C64E_EQUIVALENCE_ULP_BUDGET + 1


def test_family_report_transfer_public_bytes_are_exact_token_mapping() -> None:
    report = _family_report(
        family="transfer",
        axes=(1, 2),
        source_values=(0.01, 0.02),
        projected_values=(0.01, 0.02),
        bytes_per_axis_unit=8192,
    )
    assert [point["public_bytes"] for point in report["points"]] == [8192, 16384]
