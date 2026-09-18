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


REPAIRED_SUBSTRATE = "d08283ec8e13567609b4b472ce6e45587d93d9ea"
FROZEN_V3_FINGERPRINT = "b25ac3d7c623c8d2f3a30dd9eca378a9c3e3fc7b7dce3af2455425888428fa22"


def test_v3_is_mapping_only_scientific_amendment() -> None:
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
        for predecessor, amended in zip(v2.C83A_TRACE_SPECS, v3.C83A_TRACE_SPECS, strict=True)
    )


def test_v3_changes_only_expected_c2_entry_set() -> None:
    changed = {
        amended.trace_id
        for predecessor, amended in zip(v2.C83A_TRACE_SPECS, v3.C83A_TRACE_SPECS, strict=True)
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


def test_v3_frozen_identity_binds_repaired_substrate_and_stays_pre_result() -> None:
    assert v3.C83A_REPAIRED_SUBSTRATE_COMMIT == REPAIRED_SUBSTRATE
    assert v3.C83A_BASE_COMMIT == REPAIRED_SUBSTRATE
    assert v3.protocol_payload()["base_commit"] == REPAIRED_SUBSTRATE
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
