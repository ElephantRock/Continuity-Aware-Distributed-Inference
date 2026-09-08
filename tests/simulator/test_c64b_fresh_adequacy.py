from __future__ import annotations

from pathlib import Path

import pytest

import experiments.c64b_fresh_adequacy as evaluator


def test_upstream_snapshot_rejects_dirty_worktree_before_commit_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_git(repo: Path, *args: str) -> str:
        assert repo == tmp_path
        calls.append(args)
        if args == ("status", "--porcelain", "--untracked-files=all"):
            return " M vidur/execution_time_predictor/sklearn_execution_time_predictor.py"
        raise AssertionError(f"unexpected git call after dirty status: {args}")

    monkeypatch.setattr(evaluator, "_git", fake_git)
    with pytest.raises(ValueError, match="working tree must be clean"):
        evaluator.verify_upstream_snapshot(tmp_path)

    assert calls == [("status", "--porcelain", "--untracked-files=all")]
