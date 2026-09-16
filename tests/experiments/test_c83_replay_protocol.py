from __future__ import annotations

import hashlib

import pytest

from experiments.c8_protocol import C8_TRACE_SPECS, C8_TRANSPORT_ID
from experiments.c83_replay_protocol import (
    C83A_COMPARISON_SCHEMA,
    C83A_PROTOCOL_FINGERPRINT,
    C83A_REPLAY_EXECUTION_FAILURE_RAW,
    C83A_ROW_SCHEMA,
    C83A_TRACE_SPECS,
    C83Layer,
    C83NormalizedOutcome,
    normalize_raw_outcome,
    protocol_fingerprint,
    validate_comparison,
    validate_layer_row,
    validate_protocol_identity,
)


EXECUTION_SHA = "1" * 40


def _state_fp(trace_id: str, checkpoint_id: str) -> str:
    return hashlib.sha256(f"{trace_id}:{checkpoint_id}".encode("utf-8")).hexdigest()


def _topology(spec) -> dict[str, object]:
    return {
        "authority_pid": 1001,
        "authority_port": 41001,
        "fault_harness_pid": 1002,
        "worker_port": 41002,
        "worker_pids": [1003 + index for index in range(spec.worker_generations)],
        "transport_id": C8_TRANSPORT_ID,
        "real_process_boundary": True,
    }


def _safe_rule(checkpoint, layer: C83Layer):
    return next(
        rule
        for rule in checkpoint.raw_rules
        if rule.layer is layer
        and rule.normalized_outcome is checkpoint.expected
        and not rule.forbidden_violation
    )


def _violating_rule(checkpoint, layer: C83Layer):
    return next(
        rule
        for rule in checkpoint.raw_rules
        if rule.layer is layer and rule.forbidden_violation
    )


def _row(
    spec,
    checkpoint,
    layer: C83Layer,
    *,
    raw_outcome: str | None = None,
    normalized_outcome: C83NormalizedOutcome | None = None,
    replay_execution_failure: bool = False,
    state_fp: str | None = None,
) -> dict[str, object]:
    if replay_execution_failure:
        raw = C83A_REPLAY_EXECUTION_FAILURE_RAW
        normalized = C83NormalizedOutcome.FAIL
        violations = 0
    else:
        rule = _safe_rule(checkpoint, layer) if raw_outcome is None else checkpoint.rule_for(layer, raw_outcome)
        raw = rule.raw_outcome
        normalized = rule.normalized_outcome if normalized_outcome is None else normalized_outcome
        violations = 1 if checkpoint.opportunity and rule.forbidden_violation else 0
    explicit_non_success = normalized in {
        C83NormalizedOutcome.REJECTED,
        C83NormalizedOutcome.WAIT,
        C83NormalizedOutcome.RETRY,
        C83NormalizedOutcome.RECOMPUTE,
        C83NormalizedOutcome.FAIL,
        C83NormalizedOutcome.AMBIGUOUS,
    }
    return {
        "schema": C83A_ROW_SCHEMA,
        "trace_id": spec.trace_id,
        "trial_id": f"trial:{spec.trace_id}",
        "execution_git_commit": EXECUTION_SHA,
        "layer": layer.value,
        "checkpoint_id": checkpoint.checkpoint_id,
        "raw_outcome": raw,
        "normalized_outcome": normalized.value,
        "opportunities": 1 if checkpoint.opportunity else 0,
        "violations": violations,
        "explicit_non_success": explicit_non_success,
        "replay_execution_failure": replay_execution_failure,
        "semantic_state_fingerprint": state_fp or _state_fp(spec.trace_id, checkpoint.checkpoint_id),
        "topology_provenance": _topology(spec) if layer is C83Layer.C8 else None,
        "fault_script_fingerprint": spec.fault_script_fingerprint() if layer is C83Layer.C8 else None,
    }


def _safe_rows(spec) -> list[dict[str, object]]:
    return [
        _row(spec, checkpoint, layer)
        for layer in C83Layer
        for checkpoint in spec.checkpoints
    ]


def _comparison(spec, rows: list[dict[str, object]]) -> dict[str, object]:
    vectors = {
        layer.value: [
            next(
                row["normalized_outcome"]
                for row in rows
                if row["layer"] == layer.value
                and row["checkpoint_id"] == checkpoint.checkpoint_id
            )
            for checkpoint in spec.checkpoints
        ]
        for layer in C83Layer
    }
    state_vectors = {
        layer.value: [
            next(
                row["semantic_state_fingerprint"]
                for row in rows
                if row["layer"] == layer.value
                and row["checkpoint_id"] == checkpoint.checkpoint_id
            )
            for checkpoint in spec.checkpoints
        ]
        for layer in C83Layer
    }
    expected_vector = [checkpoint.expected.value for checkpoint in spec.checkpoints]
    expected_match = {
        layer.value: vectors[layer.value] == expected_vector for layer in C83Layer
    }
    opportunities = {
        layer.value: sum(row["opportunities"] for row in rows if row["layer"] == layer.value)
        for layer in C83Layer
    }
    violations = {
        layer.value: sum(row["violations"] for row in rows if row["layer"] == layer.value)
        for layer in C83Layer
    }
    state_equivalent = len({tuple(items) for items in state_vectors.values()}) == 1
    semantic_equivalent = (
        len({tuple(items) for items in vectors.values()}) == 1 and state_equivalent
    )
    opportunity_counts_match = len(set(opportunities.values())) == 1
    execution_failure_count = sum(1 for row in rows if row["replay_execution_failure"])
    rankable = execution_failure_count == 0 and opportunity_counts_match
    forbidden_violation_free = all(value == 0 for value in violations.values())
    correctness_pass = (
        rankable
        and semantic_equivalent
        and all(expected_match.values())
        and forbidden_violation_free
    )
    return {
        "schema": C83A_COMPARISON_SCHEMA,
        "trace_id": spec.trace_id,
        "trial_id": f"trial:{spec.trace_id}",
        "execution_git_commit": EXECUTION_SHA,
        "layer_rows": rows,
        "checkpoint_vector_by_layer": vectors,
        "state_fingerprint_vector_by_layer": state_vectors,
        "state_equivalent": state_equivalent,
        "expected_checkpoint_vector": expected_vector,
        "expected_outcome_match_by_layer": expected_match,
        "semantic_equivalent": semantic_equivalent,
        "opportunity_count_by_layer": opportunities,
        "opportunity_counts_match": opportunity_counts_match,
        "violation_count_by_layer": violations,
        "forbidden_violation_free": forbidden_violation_free,
        "execution_failure_count": execution_failure_count,
        "rankable": rankable,
        "correctness_pass": correctness_pass,
    }


def _spec(trace_id: str):
    return next(item for item in C83A_TRACE_SPECS if item.trace_id == trace_id)


def test_protocol_identity_is_frozen_and_matches_c81_trace_set() -> None:
    validate_protocol_identity()
    assert protocol_fingerprint() == C83A_PROTOCOL_FINGERPRINT
    assert C83A_PROTOCOL_FINGERPRINT == (
        "40780a318a5a692c713d7d27ded86d1f0fc6cd920d1c1ef82114889f9d9e099a"
    )
    assert tuple(item.trace_id for item in C83A_TRACE_SPECS) == tuple(
        item.trace_id for item in C8_TRACE_SPECS
    )
    assert tuple(item.forbidden_metric.value for item in C83A_TRACE_SPECS) == tuple(
        item.forbidden_metric for item in C8_TRACE_SPECS
    )


def test_every_checkpoint_freezes_safe_and_violating_normalization() -> None:
    for spec in C83A_TRACE_SPECS:
        assert spec.c8_required_message_kinds
        assert spec.c8_lifecycle_actions
        for checkpoint in spec.checkpoints:
            assert checkpoint.semantic_projection_fields
            for layer in C83Layer:
                safe = _safe_rule(checkpoint, layer)
                normalized, violation = normalize_raw_outcome(
                    spec.trace_id,
                    checkpoint.checkpoint_id,
                    layer,
                    safe.raw_outcome,
                )
                assert normalized is checkpoint.expected
                assert violation is False
                if checkpoint.opportunity:
                    bad = _violating_rule(checkpoint, layer)
                    normalized, violation = normalize_raw_outcome(
                        spec.trace_id,
                        checkpoint.checkpoint_id,
                        layer,
                        bad.raw_outcome,
                    )
                    assert normalized is C83NormalizedOutcome.COMMITTED
                    assert violation is True


def test_all_seven_safe_comparisons_are_representable_without_inspecting_results() -> None:
    for spec in C83A_TRACE_SPECS:
        comparison = _comparison(spec, _safe_rows(spec))
        validated = validate_comparison(comparison)
        assert validated["semantic_equivalent"] is True
        assert validated["state_equivalent"] is True
        assert validated["forbidden_violation_free"] is True
        assert validated["rankable"] is True
        assert validated["correctness_pass"] is True


def test_same_wrong_behavior_can_be_equivalent_but_cannot_pass() -> None:
    spec = _spec("C8-X1-LATE-SUPERSEDED-ATTEMPT")
    checkpoint = next(item for item in spec.checkpoints if item.opportunity)
    rows = _safe_rows(spec)
    for index, row in enumerate(rows):
        if row["checkpoint_id"] != checkpoint.checkpoint_id:
            continue
        layer = C83Layer(row["layer"])
        bad = _violating_rule(checkpoint, layer)
        rows[index] = _row(spec, checkpoint, layer, raw_outcome=bad.raw_outcome)
    comparison = _comparison(spec, rows)
    validated = validate_comparison(comparison)
    assert validated["semantic_equivalent"] is True
    assert validated["forbidden_violation_free"] is False
    assert validated["violation_count_by_layer"] == {"C1": 1, "C2": 1, "C8": 1}
    assert validated["correctness_pass"] is False


def test_one_layer_violation_breaks_semantic_equivalence_and_pass() -> None:
    spec = _spec("C8-X2-DUPLICATE-COMPLETION")
    checkpoint = next(item for item in spec.checkpoints if item.opportunity)
    rows = _safe_rows(spec)
    target = next(
        index
        for index, row in enumerate(rows)
        if row["layer"] == "C8" and row["checkpoint_id"] == checkpoint.checkpoint_id
    )
    bad = _violating_rule(checkpoint, C83Layer.C8)
    rows[target] = _row(spec, checkpoint, C83Layer.C8, raw_outcome=bad.raw_outcome)
    comparison = _comparison(spec, rows)
    validated = validate_comparison(comparison)
    assert validated["semantic_equivalent"] is False
    assert validated["violation_count_by_layer"] == {"C1": 0, "C2": 0, "C8": 1}
    assert validated["correctness_pass"] is False


def test_equal_semantic_failure_is_not_mistaken_for_expected_correctness() -> None:
    spec = _spec("C8-X2-DUPLICATE-COMPLETION")
    checkpoint = spec.checkpoints[0]
    rows = _safe_rows(spec)
    for index, row in enumerate(rows):
        if row["checkpoint_id"] != checkpoint.checkpoint_id:
            continue
        layer = C83Layer(row["layer"])
        rows[index] = _row(
            spec,
            checkpoint,
            layer,
            raw_outcome="SEMANTIC_CHECKPOINT_FAILED",
        )
    comparison = _comparison(spec, rows)
    validated = validate_comparison(comparison)
    assert validated["semantic_equivalent"] is True
    assert validated["forbidden_violation_free"] is True
    assert validated["expected_outcome_match_by_layer"] == {
        "C1": False,
        "C2": False,
        "C8": False,
    }
    assert validated["correctness_pass"] is False


def test_state_fingerprint_divergence_breaks_semantic_equivalence() -> None:
    spec = _spec("C8-X3-WRONG-SIBLING-STATE")
    rows = _safe_rows(spec)
    target = next(index for index, row in enumerate(rows) if row["layer"] == "C8")
    rows[target] = dict(rows[target])
    rows[target]["semantic_state_fingerprint"] = hashlib.sha256(b"different-state").hexdigest()
    validated = validate_comparison(_comparison(spec, rows))
    assert validated["checkpoint_vector_by_layer"] == {
        "C1": ["REJECTED"],
        "C2": ["REJECTED"],
        "C8": ["REJECTED"],
    }
    assert validated["state_equivalent"] is False
    assert validated["semantic_equivalent"] is False
    assert validated["correctness_pass"] is False


def test_physical_replay_execution_failure_is_recorded_but_unrankable() -> None:
    spec = _spec("C8-X5-AMBIGUOUS-OWNERSHIP")
    checkpoint = spec.checkpoints[0]
    rows = _safe_rows(spec)
    target = next(index for index, row in enumerate(rows) if row["layer"] == "C8")
    rows[target] = _row(
        spec,
        checkpoint,
        C83Layer.C8,
        replay_execution_failure=True,
    )
    validated = validate_comparison(_comparison(spec, rows))
    assert validated["execution_failure_count"] == 1
    assert validated["rankable"] is False
    assert validated["correctness_pass"] is False


def test_layer_row_rejects_unknown_raw_and_denominator_tampering() -> None:
    spec = _spec("C8-X3-WRONG-SIBLING-STATE")
    checkpoint = spec.checkpoints[0]
    row = _row(spec, checkpoint, C83Layer.C1)
    bad = dict(row)
    bad["raw_outcome"] = "POSTHOC_UNFROZEN_CLASSIFICATION"
    with pytest.raises(ValueError, match="not frozen"):
        validate_layer_row(bad)
    bad = dict(row)
    bad["opportunities"] = 0
    with pytest.raises(ValueError, match="denominator"):
        validate_layer_row(bad)


def test_c8_topology_requires_real_distinct_pids_ports_and_generation_count() -> None:
    spec = _spec("C8-X1-LATE-SUPERSEDED-ATTEMPT")
    checkpoint = spec.checkpoints[0]
    row = _row(spec, checkpoint, C83Layer.C8)
    topology = dict(row["topology_provenance"])
    topology["worker_pids"] = [1001, 1004]
    bad = dict(row)
    bad["topology_provenance"] = topology
    with pytest.raises(ValueError, match="distinct"):
        validate_layer_row(bad)

    topology = dict(row["topology_provenance"])
    topology["worker_pids"] = [1003]
    bad = dict(row)
    bad["topology_provenance"] = topology
    with pytest.raises(ValueError, match="generation count"):
        validate_layer_row(bad)

    topology = dict(row["topology_provenance"])
    topology["worker_port"] = topology["authority_port"]
    bad = dict(row)
    bad["topology_provenance"] = topology
    with pytest.raises(ValueError, match="ports must be distinct"):
        validate_layer_row(bad)


def test_c8_fault_script_fingerprint_is_mandatory_and_trace_specific() -> None:
    spec = _spec("C8-X4-STALE-BINDING")
    checkpoint = spec.checkpoints[0]
    row = _row(spec, checkpoint, C83Layer.C8)
    bad = dict(row)
    bad["fault_script_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="differs"):
        validate_layer_row(bad)


def test_comparison_rejects_noncanonical_row_order_and_execution_sha_mismatch() -> None:
    spec = _spec("C8-X4-STALE-BINDING")
    rows = _safe_rows(spec)
    comparison = _comparison(spec, rows)
    reordered = list(rows)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    bad = _comparison(spec, reordered)
    with pytest.raises(ValueError, match="canonical order"):
        validate_comparison(bad)

    bad_rows = _safe_rows(spec)
    bad_rows[-1] = dict(bad_rows[-1])
    bad_rows[-1]["execution_git_commit"] = "2" * 40
    bad = _comparison(spec, bad_rows)
    with pytest.raises(ValueError, match="execution identity"):
        validate_comparison(bad)


def test_row_rejects_fake_state_digest_and_non_c8_execution_failure() -> None:
    spec = _spec("C8-X6-STATE-EVICTION-TOOL-WAIT")
    checkpoint = spec.checkpoints[0]
    row = _row(spec, checkpoint, C83Layer.C1)
    bad = dict(row)
    bad["semantic_state_fingerprint"] = "not-a-digest"
    with pytest.raises(ValueError, match="SHA-256"):
        validate_layer_row(bad)

    bad = dict(row)
    bad["raw_outcome"] = C83A_REPLAY_EXECUTION_FAILURE_RAW
    bad["normalized_outcome"] = "FAIL"
    bad["explicit_non_success"] = True
    bad["replay_execution_failure"] = True
    with pytest.raises(ValueError, match="reserved for physical C8"):
        validate_layer_row(bad)
