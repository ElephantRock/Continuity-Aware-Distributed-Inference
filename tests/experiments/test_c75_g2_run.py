from __future__ import annotations

import pytest

from experiments.c75_g2_run import (
    C75B_COMPARATIVE_EXECUTION_READY,
    C75B_PROGRAM_ROWS_SCHEMA,
    C75B_STAGE1_REVIEW_SHA,
    _assert_execution_checkout,
    _checked_out_git_sha,
    _execution_sha,
    execution_plan,
)


def test_stage2_plan_is_exact_and_still_nonexecuting() -> None:
    plan = execution_plan()
    assert plan["stage1_review_sha"] == C75B_STAGE1_REVIEW_SHA
    assert plan["stage1_review_sha"] == "bf9af8fa54a2948c9fa4a77f3a1325ecbf5e2e68"
    assert plan["p1_cells"] == 75
    assert plan["p4_cells"] == 16
    assert plan["p7_cells"] == 3
    assert plan["seed_count"] == 64
    assert plan["hardware_strata"] == ["a100-80gb", "h100-80gb"]
    assert plan["paired_program_evaluations"] == 12032
    assert plan["program_row_count"] == 60160
    assert plan["program_rows_schema"] == C75B_PROGRAM_ROWS_SCHEMA
    assert plan["program_rows_schema"] == "cadi.c7.5b.program-rows.canonical-jsonl.v1"
    assert plan["comparative_execution"] == C75B_COMPARATIVE_EXECUTION_READY
    assert plan["comparative_execution"] == "READY_NOT_RUN"


def test_execution_sha_is_exact_lowercase_git_identity() -> None:
    value = "a" * 40
    assert _execution_sha(value) == value
    for bad in ("a" * 39, "A" * 40, "g" * 40, "", "main"):
        with pytest.raises(ValueError, match="40-hex"):
            _execution_sha(bad)


def test_execution_sha_must_match_running_checkout() -> None:
    observed = _checked_out_git_sha()
    assert len(observed) == 40
    _assert_execution_checkout(observed)
    wrong = "0" * 40 if observed != "0" * 40 else "1" * 40
    with pytest.raises(ValueError, match="running checkout"):
        _assert_execution_checkout(wrong)
