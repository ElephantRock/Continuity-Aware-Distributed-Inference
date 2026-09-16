from __future__ import annotations

from continuity.core import ContinuityCore
from experiments import c83_replay_protocol as v2
from experiments import c83_replay_protocol_v3 as v3
from simulator.engine import DiscreteEventSimulator
from simulator.fault_campaign import FaultScheduleReplayer
from simulator.fault_linkage import CrossLayerFaultInjector
from simulator.fault_oracle import FaultTrustOracle
from simulator.faults import FaultInjector
from simulator.resources import ResourceModel
from simulator.semantic_adapter import ContinuityAdapter


FROZEN_V3_FINGERPRINT = "8441753a3773070a2a53c5896df3a8ca20803d82ad228bc6ad8e992c779a097c"


def test_v3_is_mapping_only_amendment() -> None:
    v3.validate_mapping_only_amendment()
    assert tuple(item.trace_id for item in v3.C83A_TRACE_SPECS) == tuple(
        item.trace_id for item in v2.C83A_TRACE_SPECS
    )
    assert tuple(item.forbidden_metric for item in v3.C83A_TRACE_SPECS) == tuple(
        item.forbidden_metric for item in v2.C83A_TRACE_SPECS
    )
    assert all(
        amended.checkpoints == predecessor.checkpoints
        and amended.c8_fault_script == predecessor.c8_fault_script
        and amended.c8_required_message_kinds == predecessor.c8_required_message_kinds
        and amended.c8_lifecycle_actions == predecessor.c8_lifecycle_actions
        and amended.worker_generations == predecessor.worker_generations
        and amended.requires_worker_restart == predecessor.requires_worker_restart
        and amended.opportunity_definition == predecessor.opportunity_definition
        and amended.violation_definition == predecessor.violation_definition
        for predecessor, amended in zip(
            v2.C83A_TRACE_SPECS, v3.C83A_TRACE_SPECS, strict=True
        )
    )


def test_v3_changes_only_expected_c2_entry_set() -> None:
    changed = {
        amended.trace_id
        for predecessor, amended in zip(
            v2.C83A_TRACE_SPECS, v3.C83A_TRACE_SPECS, strict=True
        )
        if predecessor.c2_entry != amended.c2_entry
    }
    assert changed == {
        "C8-X1-LATE-SUPERSEDED-ATTEMPT",
        "C8-X3-WRONG-SIBLING-STATE",
        "C8-X4-STALE-BINDING",
        "C8-X5-AMBIGUOUS-OWNERSHIP",
        "C8-X6-STATE-EVICTION-TOOL-WAIT",
        "C8-X7-PARTIAL-MIGRATION",
    }
    assert next(
        item for item in v3.C83A_TRACE_SPECS if item.trace_id == "C8-X2-DUPLICATE-COMPLETION"
    ).c2_entry == next(
        item for item in v2.C83A_TRACE_SPECS if item.trace_id == "C8-X2-DUPLICATE-COMPLETION"
    ).c2_entry


def test_v3_named_c2_mechanics_exist() -> None:
    assert hasattr(ContinuityAdapter, "schedule_attempt_completion")
    assert hasattr(FaultInjector, "reorder_after")
    assert hasattr(FaultInjector, "duplicate_delivery")
    assert hasattr(ContinuityCore, "state_compatible")
    assert hasattr(ContinuityCore, "reconcile")
    assert hasattr(CrossLayerFaultInjector, "evict_replica")
    assert hasattr(CrossLayerFaultInjector, "fail_worker")
    assert callable(FaultScheduleReplayer.replay)
    assert callable(FaultTrustOracle.assert_all)
    assert callable(ResourceModel.evict_replica)
    assert callable(ResourceModel.fail_worker)
    assert callable(DiscreteEventSimulator.run)


def test_v3_pre_result_boundary_and_frozen_identity() -> None:
    assert v3.C83A_COMPARATIVE_RESULT_INSPECTION == "NONE"
    assert v3.C83A_PREDECESSOR_PROTOCOL_FINGERPRINT == (
        "40780a318a5a692c713d7d27ded86d1f0fc6cd920d1c1ef82114889f9d9e099a"
    )
    assert v3.C83A_PROTOCOL_SCHEMA.endswith(".v3")
    assert v3.C83A_ROW_SCHEMA == v2.C83A_ROW_SCHEMA
    assert v3.C83A_COMPARISON_SCHEMA == v2.C83A_COMPARISON_SCHEMA
    assert v3.C83A_PROTOCOL_FINGERPRINT == FROZEN_V3_FINGERPRINT
    assert v3.protocol_fingerprint() == FROZEN_V3_FINGERPRINT
    v3.validate_protocol_identity()
