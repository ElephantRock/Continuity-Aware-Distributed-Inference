from __future__ import annotations

from pathlib import Path

from experiments.c83_replay_protocol import C83A_TRACE_SPECS
from prototype.c8_transport import MESSAGE_KINDS


def test_frozen_c1_entry_points_exist_in_declared_source() -> None:
    for spec in C83A_TRACE_SPECS:
        source_path, function_name = spec.c1_entry.split("::", 1)
        path = Path(source_path)
        assert path.is_file(), spec.trace_id
        source = path.read_text(encoding="utf-8")
        assert f"def {function_name}(" in source, spec.trace_id


def test_required_c8_message_kinds_are_supported_by_frozen_transport() -> None:
    for spec in C83A_TRACE_SPECS:
        assert set(spec.c8_required_message_kinds) <= MESSAGE_KINDS, spec.trace_id


def test_c2_entries_remain_explicit_simulator_mappings() -> None:
    for spec in C83A_TRACE_SPECS:
        assert spec.c2_entry.startswith("simulator."), spec.trace_id
