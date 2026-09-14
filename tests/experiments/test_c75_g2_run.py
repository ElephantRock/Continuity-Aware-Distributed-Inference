from __future__ import annotations

import pytest

from experiments.c75_g2_run import (
    C75B_COMPARATIVE_EXECUTION_READY,
    C75B_STAGE1_REVIEW_SHA,
    _execution_sha,
    execution_plan,
)


def test_stage2_plan_is_exact_and_still_nonexecuting() -> None:
    plan = execution_plan()
    assert plan["stage1_review_sha"] == C75B_STAGE1_REVIEW_SHA
    assert plan["stage1_review_sha"] == "6aeb5a3147951645a270088aac0647c03084a73c"
    assert plan["p1_cells"] == 75
    assert plan["p4_cells"] == 16
    assert plan["p7_cells"] == 3
    assert plan["seed_count"] == 64
    assert plan["hardware_strata"] == ["a100-80gb", "h100-80gb"]
    assert plan["paired_program_evaluations"] == 12032
    assert plan["program_row_count"] == 60160
    assert plan["comparative_execution"] == C75B_COMPARATIVE_EXECUTION_READY
    assert plan["comparative_execution"] == "READY_NOT_RUN"


def test_execution_sha_is_exact_lowercase_git_identity() -> None:
    value = "a" * 40
    assert _execution_sha(value) == value
    for bad in ("a" * 39, "A" * 40, "g" * 40, "", "main"):
        with pytest.raises(ValueError, match="40-hex"):
            _execution_sha(bad)
