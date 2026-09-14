from __future__ import annotations

import math

import pytest

from experiments.c7_protocol import C7_PROTOCOL_FINGERPRINT
from experiments.c75_g2_protocol import (
    C75_COMPARATIVE_RESULT_INSPECTION,
    C75_G2_DECISION if False else C75G2Decision,
    C75_H4_PRIMARY_COMPARATORS,
    C75_P1_REUSE_FRACTIONS,
    C75_PROTOCOL_FINGERPRINT,
    C75_SURFACES,
    C75_WORKER_COUNTS,
    FROZEN_C75_PROTOCOL,
    adjudicate_g2,
    p1_cells,
    p1_cells_adjacent,
    p4_cells,
    p4_cells_adjacent,
    p7_control_cells,
    relative_timing_benefit,
    reusable_tokens,
    select_seed_records,
    session_preferred_worker_index,
    source_record_eligible,
    source_record_order_key,
    state_worker_index,
    worker_queue_depth,
)
from experiments.trace_workload import NormalizedTraceRecord
from simulator.policies import PolicyID


def _record(index: int, *, input_tokens: int = 1024, output_tokens: int = 32) -> NormalizedTraceRecord:
    return NormalizedTraceRecord(
        f"r{index:03d}",
        f"s{index:03d}",
        index,
        float(index),
        input_tokens,
        output_tokens,
        f"g{index:03d}",
        input_tokens // 2,
    )


def test_parent_and_result_inspection_are_frozen() -> None:
    assert C7_PROTOCOL_FINGERPRINT == "706e0d5fff362a1eda8c906b957c914251c6e7949bac6be3c2c143ae21625474"
    assert C75_COMPARATIVE_RESULT_INSPECTION == "NONE"
    payload = FROZEN_C75_PROTOCOL.to_dict()
    assert payload["comparative_result_inspection"] == "NONE"
    assert "policy_results" not in payload
    assert len(C75_PROTOCOL_FINGERPRINT) == 64
    int(C75_PROTOCOL_FINGERPRINT, 16)


def test_rankable_surface_counts_are_exact() -> None:
    assert len(p1_cells()) == 5 * 5 * 3
    assert len(p4_cells()) == 4 * 4
    assert p7_control_cells() == (2, 4, 8)
    assert {surface.surface_id for surface in C75_SURFACES} == {
        "P1_DEPTH_REUSE_BY_WORKERS",
        "P1_REUSE_CACHE_UNREALIZED",
        "P4_FANOUT_SHARED_PREFIX",
        "P7_STATELESS_WORKERS_CONTROL",
        "P7_WORKERS_ARRIVAL_UNREALIZED",
    }


def test_adjacency_never_crosses_p1_worker_strata() -> None:
    assert p1_cells_adjacent((2, 0.25, 2), (4, 0.25, 2))
    assert p1_cells_adjacent((2, 0.25, 2), (2, 0.5, 2))
    assert not p1_cells_adjacent((2, 0.25, 2), (4, 0.5, 2))
    assert not p1_cells_adjacent((2, 0.25, 2), (2, 0.25, 4))
    assert p4_cells_adjacent((2, 0.25), (4, 0.25))
    assert p4_cells_adjacent((2, 0.25), (2, 0.5))
    assert not p4_cells_adjacent((2, 0.25), (4, 0.5))


def test_source_selection_arithmetic_filter_and_seed_mapping_are_deterministic() -> None:
    records = [_record(i) for i in range(80)]
    records.extend(
        [
            _record(100, input_tokens=1023),
            _record(101, input_tokens=4092, output_tokens=8),
        ]
    )
    selected_a = select_seed_records(tuple(records))
    selected_b = select_seed_records(tuple(reversed(records)))
    assert selected_a == selected_b
    assert len(selected_a) == 64
    assert len({record.record_id for record in selected_a}) == 64
    assert all(record.input_tokens % 4 == 0 for record in selected_a)  # type: ignore[operator]
    assert all(source_record_eligible(record.input_tokens, record.output_tokens) for record in selected_a)  # type: ignore[arg-type]
    assert [source_record_order_key(r.record_id) for r in selected_a] == sorted(
        source_record_order_key(r.record_id) for r in selected_a
    )


def test_source_selection_fails_closed_when_pool_is_too_small() -> None:
    with pytest.raises(ValueError, match="fewer than 64"):
        select_seed_records(tuple(_record(i) for i in range(63)))


def test_quarter_step_reuse_never_rounds() -> None:
    assert C75_P1_REUSE_FRACTIONS == (0.0, 0.25, 0.5, 0.75, 1.0)
    assert [reusable_tokens(1024, f) for f in C75_P1_REUSE_FRACTIONS] == [0, 256, 512, 768, 1024]
    with pytest.raises(ValueError, match="divisible by four"):
        reusable_tokens(1023, 0.25)
    with pytest.raises(ValueError, match="frozen"):
        reusable_tokens(1024, 0.1)


def test_resource_fact_domains_are_deterministic_and_separated() -> None:
    for seed in range(64):
        for workers in C75_WORKER_COUNTS:
            state = state_worker_index(seed, workers, operation_ordinal=1)
            session = session_preferred_worker_index(seed, workers)
            queues = tuple(worker_queue_depth(seed, i, operation_ordinal=1) for i in range(workers))
            assert 0 <= state < workers
            assert 0 <= session < workers
            assert all(0 <= q < 4 for q in queues)
            assert state == state_worker_index(seed, workers, operation_ordinal=1)
            assert session == session_preferred_worker_index(seed, workers)
    # Domain separation is structural; it need not force unequal values for every seed.
    assert source_record_order_key("r0") != source_record_order_key("r1")


def test_relative_timing_benefit_is_fenced() -> None:
    assert relative_timing_benefit(baseline_seconds=10.0, b4_seconds=8.0) == pytest.approx(0.2)
    assert relative_timing_benefit(baseline_seconds=10.0, b4_seconds=10.0) == 0.0
    with pytest.raises(ValueError, match="positive"):
        relative_timing_benefit(baseline_seconds=0.0, b4_seconds=0.0)
    with pytest.raises(ValueError, match="finite"):
        relative_timing_benefit(baseline_seconds=math.inf, b4_seconds=1.0)
    with pytest.raises(TypeError, match="numeric"):
        relative_timing_benefit(baseline_seconds=True, b4_seconds=1.0)


def test_h4_is_per_comparator_not_all_baselines_required() -> None:
    payload = FROZEN_C75_PROTOCOL.to_dict()["h4"]
    assert C75_H4_PRIMARY_COMPARATORS == (PolicyID.B0, PolicyID.B1, PolicyID.B2)
    assert payload["cell_support_is_per_comparator"] is True
    assert "at least one primary comparator" in payload["hypothesis_support"]
    assert "all B0/B1/B2 maps" in payload["hypothesis_support"]
    assert "B1 and B4 coincide" in payload["cache_aware_confluence_note"]


def test_h7_requires_one_or_more_statefulness_gradients_not_both_surfaces() -> None:
    payload = FROZEN_C75_PROTOCOL.to_dict()["h7"]
    assert payload["eligible_statefulness_axes"]["P1"] == ["session_depth", "reusable_prefix_fraction"]
    assert payload["eligible_statefulness_axes"]["P4"] == ["fanout_width", "shared_prefix_fraction"]
    assert "at least one qualifying P1 or P4" in payload["hypothesis_support"]
    assert "not required by H7" in payload["multi_surface_confirmation"]
    assert "does not require monotonicity for every variable" in payload["interaction_rule"]


def test_p7_is_only_a_negative_control() -> None:
    payload = FROZEN_C75_PROTOCOL.to_dict()["p7_negative_control"]
    assert payload["ranked_as_efficiency_support"] is False
    assert payload["routing_control_cost_model"] == "ZERO_UNEVIDENCED_COMPONENT"
    assert payload["physical_overhead_claim"] is False


def test_gate_g2_truth_table() -> None:
    assert adjudicate_g2(h4_supported=True, h5_supported=True, h6_supported=False, h7_supported=True) is C75G2Decision.A
    assert adjudicate_g2(h4_supported=False, h5_supported=True, h6_supported=True, h7_supported=True) is C75G2Decision.A
    assert adjudicate_g2(h4_supported=False, h5_supported=False, h6_supported=False, h7_supported=False) is C75G2Decision.C
    assert adjudicate_g2(h4_supported=True, h5_supported=False, h6_supported=False, h7_supported=False) is C75G2Decision.B
    assert adjudicate_g2(h4_supported=False, h5_supported=True, h6_supported=True, h7_supported=False) is C75G2Decision.B
    with pytest.raises(TypeError):
        adjudicate_g2(h4_supported=1, h5_supported=True, h6_supported=True, h7_supported=True)  # type: ignore[arg-type]
