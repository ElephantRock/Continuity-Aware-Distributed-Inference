from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Any

from experiments import c83_replay_protocol as _v2


C83A_PROTOCOL_SCHEMA = "cadi.c8.3a.cross-layer-replay-protocol.v3"
C83A_BASE_COMMIT = _v2.C83A_BASE_COMMIT
C83A_REPAIRED_SUBSTRATE_COMMIT = "d08283ec8e13567609b4b472ce6e45587d93d9ea"
C83A_C81_PROTOCOL_FINGERPRINT = _v2.C83A_C81_PROTOCOL_FINGERPRINT
C83A_COMPARATIVE_RESULT_INSPECTION = "NONE"
C83A_ROW_SCHEMA = _v2.C83A_ROW_SCHEMA
C83A_COMPARISON_SCHEMA = _v2.C83A_COMPARISON_SCHEMA
C83A_REPLAY_EXECUTION_FAILURE_RAW = _v2.C83A_REPLAY_EXECUTION_FAILURE_RAW
C83A_PREDECESSOR_PROTOCOL_FINGERPRINT = _v2.C83A_PROTOCOL_FINGERPRINT
C83A_PROTOCOL_FINGERPRINT: str | None = None

C83Layer = _v2.C83Layer
C83NormalizedOutcome = _v2.C83NormalizedOutcome
C83RawRule = _v2.C83RawRule
C83CheckpointSpec = _v2.C83CheckpointSpec
C83TraceAdapterSpec = _v2.C83TraceAdapterSpec


_C2_ENTRY_OVERRIDES = {
    "C8-X1-LATE-SUPERSEDED-ATTEMPT": (
        "simulator.semantic_adapter.ContinuityAdapter + "
        "simulator.faults.FaultInjector.reorder_after over frozen late-result/observation deliveries"
    ),
    "C8-X3-WRONG-SIBLING-STATE": (
        "simulator.engine.DiscreteEventSimulator checkpoint + "
        "continuity.core.ContinuityCore.state_compatible under policy-neutral State placement"
    ),
    "C8-X4-STALE-BINDING": (
        "simulator.engine.DiscreteEventSimulator + "
        "simulator.faults.FaultInjector.reorder_after + "
        "continuity.core.ContinuityCore Binding observation"
    ),
    "C8-X5-AMBIGUOUS-OWNERSHIP": (
        "simulator.engine.DiscreteEventSimulator + "
        "continuity.core.ContinuityCore.reconcile on frozen ambiguous Evidence"
    ),
    "C8-X6-STATE-EVICTION-TOOL-WAIT": (
        "simulator.fault_linkage.CrossLayerFaultInjector.evict_replica + "
        "simulator.fault_campaign.FaultScheduleReplayer + "
        "simulator.fault_oracle.FaultTrustOracle"
    ),
    "C8-X7-PARTIAL-MIGRATION": (
        "simulator.fault_linkage.CrossLayerFaultInjector.fail_worker + "
        "simulator.resources.ResourceModel + "
        "simulator.fault_oracle.FaultTrustOracle + "
        "continuity.core.ContinuityCore migration interruption/commit path"
    ),
}

C83A_TRACE_SPECS = tuple(
    replace(spec, c2_entry=_C2_ENTRY_OVERRIDES.get(spec.trace_id, spec.c2_entry))
    for spec in _v2.C83A_TRACE_SPECS
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def protocol_payload() -> dict[str, Any]:
    payload = dict(_v2.protocol_payload())
    payload["schema"] = C83A_PROTOCOL_SCHEMA
    payload["trace_specs"] = [item.to_dict() for item in C83A_TRACE_SPECS]
    payload["amendment"] = {
        "predecessor_protocol_fingerprint": C83A_PREDECESSOR_PROTOCOL_FINGERPRINT,
        "repaired_substrate_commit": C83A_REPAIRED_SUBSTRATE_COMMIT,
        "scope": "C2_ENTRY_POINT_DESCRIPTION_ONLY_PLUS_PARENT_REBIND",
        "comparative_result_inspection": C83A_COMPARATIVE_RESULT_INSPECTION,
    }
    return payload


def protocol_fingerprint() -> str:
    return _fingerprint(protocol_payload())


def _without_c2_entry(spec: C83TraceAdapterSpec) -> dict[str, Any]:
    value = spec.to_dict()
    value.pop("c2_entry")
    return value


def validate_mapping_only_amendment() -> None:
    if len(C83A_TRACE_SPECS) != len(_v2.C83A_TRACE_SPECS):
        raise RuntimeError("C8.3a v3 trace cardinality drifted")
    for predecessor, amended in zip(_v2.C83A_TRACE_SPECS, C83A_TRACE_SPECS, strict=True):
        if predecessor.trace_id != amended.trace_id:
            raise RuntimeError("C8.3a v3 trace order/identity drifted")
        if _without_c2_entry(predecessor) != _without_c2_entry(amended):
            raise RuntimeError(
                f"C8.3a v3 changed scientific trace content outside c2_entry: {amended.trace_id}"
            )
        expected_entry = _C2_ENTRY_OVERRIDES.get(predecessor.trace_id, predecessor.c2_entry)
        if amended.c2_entry != expected_entry:
            raise RuntimeError(f"C8.3a v3 c2_entry mismatch: {amended.trace_id}")
    changed = {
        amended.trace_id
        for predecessor, amended in zip(_v2.C83A_TRACE_SPECS, C83A_TRACE_SPECS, strict=True)
        if predecessor.c2_entry != amended.c2_entry
    }
    if changed != set(_C2_ENTRY_OVERRIDES):
        raise RuntimeError("C8.3a v3 changed an unexpected c2_entry set")


def validate_protocol_identity(*, require_frozen: bool = True) -> None:
    _v2.validate_protocol_identity()
    validate_mapping_only_amendment()
    if C83A_COMPARATIVE_RESULT_INSPECTION != "NONE":
        raise RuntimeError("C8.3a v3 must remain pre-result")
    if require_frozen:
        if C83A_PROTOCOL_FINGERPRINT is None:
            raise RuntimeError("C8.3a v3 protocol fingerprint is not literal-frozen")
        actual = protocol_fingerprint()
        if actual != C83A_PROTOCOL_FINGERPRINT:
            raise RuntimeError(
                f"C8.3a v3 protocol payload diverged from frozen fingerprint: {actual}"
            )


normalize_raw_outcome = _v2.normalize_raw_outcome
validate_layer_row = _v2.validate_layer_row
validate_comparison = _v2.validate_comparison


validate_protocol_identity(require_frozen=False)
