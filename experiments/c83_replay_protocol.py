from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from experiments.c8_protocol import C8_PROTOCOL_FINGERPRINT, C8_TRACE_SPECS
from experiments.correctness import CorrectnessMetric
from prototype.c8_faults import DeliveryAction, DeliveryScheduler


C83A_PROTOCOL_SCHEMA = "cadi.c8.3a.cross-layer-replay-protocol.v1"
C83A_BASE_COMMIT = "85a0ef4be82d45dc5e309332c5f41d067e4c952a"
C83A_C81_PROTOCOL_FINGERPRINT = (
    "616cc4daa4c167152875522767a8cc968da25c63d6acc099c265bc3554641cb6"
)
C83A_COMPARATIVE_RESULT_INSPECTION = "NONE"
C83A_ROW_SCHEMA = "cadi.c8.3.layer-row.v1"
C83A_COMPARISON_SCHEMA = "cadi.c8.3.comparison.v1"
C83A_PROTOCOL_FINGERPRINT = (
    "41fdd93aff30e4052516c9226b279c745956774e1425df65a9dfc779f3315b19"
)


class C83Layer(str, Enum):
    C1 = "C1"
    C2 = "C2"
    C8 = "C8"


class C83NormalizedOutcome(str, Enum):
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"
    WAIT = "WAIT"
    RETRY = "RETRY"
    RECOMPUTE = "RECOMPUTE"
    FAIL = "FAIL"
    AMBIGUOUS = "AMBIGUOUS"
    IDEMPOTENT_NOOP = "IDEMPOTENT_NOOP"


@dataclass(frozen=True, slots=True)
class C83CheckpointSpec:
    checkpoint_id: str
    expected: C83NormalizedOutcome
    opportunity: bool
    raw: tuple[tuple[C83Layer, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_id, str) or not self.checkpoint_id:
            raise ValueError("checkpoint_id must be non-empty")
        if not isinstance(self.expected, C83NormalizedOutcome):
            raise TypeError("expected must be C83NormalizedOutcome")
        if not isinstance(self.opportunity, bool):
            raise TypeError("opportunity must be bool")
        if not isinstance(self.raw, tuple) or len(self.raw) != 3:
            raise ValueError("raw normalization must contain exactly C1/C2/C8")
        layers = tuple(layer for layer, _ in self.raw)
        if layers != (C83Layer.C1, C83Layer.C2, C83Layer.C8):
            raise ValueError("raw normalization must be ordered C1, C2, C8")
        if len(set(layers)) != 3:
            raise ValueError("raw normalization layers must be unique")
        if not all(isinstance(value, str) and value for _, value in self.raw):
            raise ValueError("raw outcomes must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "expected": self.expected.value,
            "opportunity": self.opportunity,
            "raw": {layer.value: value for layer, value in self.raw},
        }

    def raw_for(self, layer: C83Layer) -> str:
        return dict(self.raw)[layer]


@dataclass(frozen=True, slots=True)
class C83TraceAdapterSpec:
    trace_id: str
    c1_entry: str
    c2_entry: str
    c8_driver: str
    c8_fault_script: tuple[tuple[str, tuple[DeliveryAction, ...]], ...]
    worker_generations: int
    requires_worker_restart: bool
    forbidden_metric: CorrectnessMetric
    opportunity_definition: str
    violation_definition: str
    checkpoints: tuple[C83CheckpointSpec, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.trace_id, "trace_id"),
            (self.c1_entry, "c1_entry"),
            (self.c2_entry, "c2_entry"),
            (self.c8_driver, "c8_driver"),
            (self.opportunity_definition, "opportunity_definition"),
            (self.violation_definition, "violation_definition"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be non-empty")
        if not isinstance(self.worker_generations, int) or isinstance(self.worker_generations, bool):
            raise TypeError("worker_generations must be int")
        if self.worker_generations < 1:
            raise ValueError("worker_generations must be positive")
        if not isinstance(self.requires_worker_restart, bool):
            raise TypeError("requires_worker_restart must be bool")
        if self.requires_worker_restart and self.worker_generations < 2:
            raise ValueError("restart trace requires at least two worker generations")
        if not isinstance(self.forbidden_metric, CorrectnessMetric):
            raise TypeError("forbidden_metric must be CorrectnessMetric")
        if not isinstance(self.checkpoints, tuple) or not self.checkpoints:
            raise ValueError("checkpoints must be non-empty")
        checkpoint_ids = tuple(item.checkpoint_id for item in self.checkpoints)
        if len(checkpoint_ids) != len(set(checkpoint_ids)):
            raise ValueError("checkpoint IDs must be unique within a trace")
        if sum(1 for item in self.checkpoints if item.opportunity) < 1:
            raise ValueError("every trace requires at least one forbidden-metric opportunity")
        script_kinds = tuple(kind for kind, _ in self.c8_fault_script)
        if script_kinds != tuple(sorted(script_kinds)) or len(script_kinds) != len(set(script_kinds)):
            raise ValueError("C8 fault script kinds must be unique and canonically ordered")
        for kind, actions in self.c8_fault_script:
            if not isinstance(kind, str) or not kind:
                raise ValueError("fault script message kind must be non-empty")
            if not isinstance(actions, tuple) or not actions:
                raise ValueError("fault script actions must be non-empty tuples")
            if not all(isinstance(action, DeliveryAction) for action in actions):
                raise TypeError("fault script actions must be DeliveryAction")
            # Constructor validation also proves paired REORDER semantics.
            DeliveryScheduler(actions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "c1_entry": self.c1_entry,
            "c2_entry": self.c2_entry,
            "c8_driver": self.c8_driver,
            "c8_fault_script": {
                kind: [action.value for action in actions]
                for kind, actions in self.c8_fault_script
            },
            "worker_generations": self.worker_generations,
            "requires_worker_restart": self.requires_worker_restart,
            "forbidden_metric": self.forbidden_metric.value,
            "opportunity_definition": self.opportunity_definition,
            "violation_definition": self.violation_definition,
            "checkpoints": [item.to_dict() for item in self.checkpoints],
        }

    def fault_script_fingerprint(self) -> str:
        payload = {
            "trace_id": self.trace_id,
            "driver": self.c8_driver,
            "worker_generations": self.worker_generations,
            "requires_worker_restart": self.requires_worker_restart,
            "fault_script": self.to_dict()["c8_fault_script"],
        }
        return _fingerprint(payload)


def _checkpoint(
    checkpoint_id: str,
    expected: C83NormalizedOutcome,
    opportunity: bool,
    c1_raw: str,
    c2_raw: str,
    c8_raw: str,
) -> C83CheckpointSpec:
    return C83CheckpointSpec(
        checkpoint_id,
        expected,
        opportunity,
        (
            (C83Layer.C1, c1_raw),
            (C83Layer.C2, c2_raw),
            (C83Layer.C8, c8_raw),
        ),
    )


C83A_TRACE_SPECS = (
    C83TraceAdapterSpec(
        trace_id="C8-X1-LATE-SUPERSEDED-ATTEMPT",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr3_reordered_old_retry_event_cannot_override_committed_attempt",
        c2_entry="simulator.faults.FaultInjector.reorder_after + FaultClass.LATE_ATTEMPT_RESULT",
        c8_driver="authority-local Attempt setup; old worker COMPLETE held as LATE_DELIVER until a2 is current",
        c8_fault_script=(("COMPLETE", (DeliveryAction.LATE_DELIVER,)),),
        worker_generations=2,
        requires_worker_restart=True,
        forbidden_metric=CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,
        opportunity_definition="one presentation of a superseded Attempt result after a newer Attempt is authoritative",
        violation_definition="the superseded Attempt becomes the LogicalRequest committed Attempt or authoritative output source",
        checkpoints=(
            _checkpoint(
                "current-attempt-finalize", C83NormalizedOutcome.COMMITTED, False,
                "CURRENT_ATTEMPT_FINALIZED", "CURRENT_ATTEMPT_COMMITTED", "CURRENT_ATTEMPT_COMMITTED",
            ),
            _checkpoint(
                "stale-attempt-presentation", C83NormalizedOutcome.REJECTED, True,
                "INVALID_TRANSITION_STALE_ATTEMPT", "IGNORE_STALE", "STALE_ATTEMPT_FENCED",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X2-DUPLICATE-COMPLETION",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr2_duplicate_result_finalizes_once",
        c2_entry="simulator.faults.FaultInjector.duplicate_delivery + FaultClass.DELIVERY_DUPLICATE",
        c8_driver="one worker COMPLETE duplicated by external harness with identical transport message identity",
        c8_fault_script=(("COMPLETE", (DeliveryAction.DUPLICATE,)),),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
        opportunity_definition="one duplicate presentation of an already-authoritatively-finalized completion",
        violation_definition="the duplicate causes a second authoritative finalization or changes the committed output",
        checkpoints=(
            _checkpoint(
                "first-finalization", C83NormalizedOutcome.COMMITTED, False,
                "FIRST_FINALIZATION", "FIRST_FINALIZATION", "FIRST_FINALIZATION",
            ),
            _checkpoint(
                "duplicate-presentation", C83NormalizedOutcome.IDEMPOTENT_NOOP, True,
                "IDEMPOTENT_FINALIZE", "IGNORE_DUPLICATE", "IDEMPOTENT_FINALIZE",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X3-WRONG-SIBLING-STATE",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr4_wrong_sibling_state_is_rejected",
        c2_entry="simulator.semantic_adapter.ContinuityAdapter state compatibility path under policy-neutral State placement",
        c8_driver="authority constructs sibling Continuations; real worker completion triggers consume decision against sibling State",
        c8_fault_script=(),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.WRONG_BRANCH_REUSE_RATE,
        opportunity_definition="one attempted consume of State whose lineage is an incompatible sibling of the target Continuation",
        violation_definition="the incompatible sibling State is consumed or treated as reusable",
        checkpoints=(
            _checkpoint(
                "incompatible-state-consume", C83NormalizedOutcome.REJECTED, True,
                "STATE_INCOMPATIBLE", "REJECT_REUSE", "STATE_INCOMPATIBLE",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X4-STALE-BINDING",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr8_late_old_binding_observation_cannot_restore_old_owner",
        c2_entry="simulator.faults.FaultInjector.delay_delivery/reorder_after + simulator.semantic_adapter.ContinuityAdapter",
        c8_driver="authority commits replacement Binding before delayed old-owner completion/observation is delivered",
        c8_fault_script=(("COMPLETE", (DeliveryAction.DELAY,)),),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,
        opportunity_definition="one stale Binding-epoch observation presented after a newer Binding is authoritative",
        violation_definition="the stale observation restores the prior owner or regresses the authoritative epoch",
        checkpoints=(
            _checkpoint(
                "replacement-binding-commit", C83NormalizedOutcome.COMMITTED, False,
                "NEW_BINDING_COMMITTED", "NEW_BINDING_COMMITTED", "NEW_BINDING_COMMITTED",
            ),
            _checkpoint(
                "stale-binding-presentation", C83NormalizedOutcome.IDEMPOTENT_NOOP, True,
                "STALE_EVENT_RECORDED_NO_AUTHORITY_CHANGE", "IGNORE_STALE", "STALE_BINDING_IGNORED",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X5-AMBIGUOUS-OWNERSHIP",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr9_ambiguous_ownership_fails_closed",
        c2_entry="simulator.fault_oracle + simulator.semantic_adapter.ContinuityAdapter reconciliation path",
        c8_driver="real worker observations trigger authority-local conflicting/ambiguous Evidence reconciliation",
        c8_fault_script=(),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,
        opportunity_definition="one correctness-sensitive ownership commit opportunity with ambiguous or conflicting sufficient-scope observations",
        violation_definition="an authoritative ownership commit occurs while reconciliation is AMBIGUOUS",
        checkpoints=(
            _checkpoint(
                "ambiguous-reconciliation", C83NormalizedOutcome.AMBIGUOUS, True,
                "RECONCILE_AMBIGUOUS", "AMBIGUOUS", "RECONCILE_AMBIGUOUS",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X6-STATE-EVICTION-TOOL-WAIT",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr11_tool_wait_eviction_preserves_lineage_but_forces_cold_resume",
        c2_entry="simulator.faults.FaultClass.REPLICA_EVICTION + simulator.fault_campaign replay",
        c8_driver="authority suspends Continuation; real worker holding the sole replica is terminated; resume attempts reuse after failure observation",
        c8_fault_script=(),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE,
        opportunity_definition="one resume-time consume decision after the only physical replica was lost during WAITING",
        violation_definition="the absent/lost replica is consumed or reported as a successful reusable State path",
        checkpoints=(
            _checkpoint(
                "resume-after-eviction", C83NormalizedOutcome.REJECTED, True,
                "CAN_CONSUME_FALSE", "REJECT_REUSE", "REPLICA_LOST_REUSE_REJECTED",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X7-PARTIAL-MIGRATION",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr6_partial_migration_does_not_commit_destination",
        c2_entry="simulator.fault_oracle + migration interruption/failure path",
        c8_driver="authority begins migration; destination worker is terminated after materialization starts but before authoritative commit",
        c8_fault_script=(),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,
        opportunity_definition="one destination ownership commit attempt after only partial materialization and without sufficient commit Evidence",
        violation_definition="the destination Binding becomes authoritative despite missing/insufficient Evidence",
        checkpoints=(
            _checkpoint(
                "partial-migration-commit", C83NormalizedOutcome.WAIT, True,
                "INSUFFICIENT_EVIDENCE", "WAIT", "INSUFFICIENT_EVIDENCE",
            ),
        ),
    ),
)


_ROW_FIELDS = frozenset(
    {
        "schema",
        "trace_id",
        "trial_id",
        "layer",
        "checkpoint_id",
        "raw_outcome",
        "normalized_outcome",
        "opportunities",
        "violations",
        "explicit_non_success",
        "semantic_state_fingerprint",
        "topology_provenance",
        "fault_script_fingerprint",
    }
)
_COMPARISON_FIELDS = frozenset(
    {
        "schema",
        "trace_id",
        "trial_id",
        "layer_rows",
        "checkpoint_vector_by_layer",
        "semantic_equivalent",
        "opportunity_count_by_layer",
        "violation_count_by_layer",
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def protocol_payload() -> dict[str, Any]:
    return {
        "schema": C83A_PROTOCOL_SCHEMA,
        "base_commit": C83A_BASE_COMMIT,
        "c81_protocol_fingerprint": C83A_C81_PROTOCOL_FINGERPRINT,
        "comparative_result_inspection": C83A_COMPARATIVE_RESULT_INSPECTION,
        "row_schema": C83A_ROW_SCHEMA,
        "comparison_schema": C83A_COMPARISON_SCHEMA,
        "normalization": [item.value for item in C83NormalizedOutcome],
        "layers": [item.value for item in C83Layer],
        "trace_specs": [item.to_dict() for item in C83A_TRACE_SPECS],
        "equivalence": {
            "unit": "ordered semantic checkpoint vector",
            "timing_used": False,
            "requires_identical_normalized_checkpoint_vector": True,
            "requires_identical_opportunity_count": True,
            "requires_zero_forbidden_violations_for_pass": True,
        },
        "multiplicity": {
            "deterministic_trials_per_trace_fixture": 1,
            "bootstrap": False,
            "stochastic_seed_multiplication": False,
        },
    }


def protocol_fingerprint() -> str:
    return _fingerprint(protocol_payload())


def validate_protocol_identity() -> None:
    if C8_PROTOCOL_FINGERPRINT != C83A_C81_PROTOCOL_FINGERPRINT:
        raise RuntimeError("C8.1 protocol fingerprint drifted")
    frozen_ids = tuple(item.trace_id for item in C8_TRACE_SPECS)
    adapter_ids = tuple(item.trace_id for item in C83A_TRACE_SPECS)
    if frozen_ids != adapter_ids:
        raise RuntimeError("C8.3a trace adapters do not exactly match frozen C8.1 trace order")
    frozen_metrics = tuple(item.forbidden_metric for item in C8_TRACE_SPECS)
    adapter_metrics = tuple(item.forbidden_metric.value for item in C83A_TRACE_SPECS)
    if frozen_metrics != adapter_metrics:
        raise RuntimeError("C8.3a forbidden metrics drifted from C8.1")
    if protocol_fingerprint() != C83A_PROTOCOL_FINGERPRINT:
        raise RuntimeError("C8.3a protocol payload diverged from frozen fingerprint")


def normalize_raw_outcome(trace_id: str, checkpoint_id: str, layer: C83Layer, raw: str) -> C83NormalizedOutcome:
    spec = next((item for item in C83A_TRACE_SPECS if item.trace_id == trace_id), None)
    if spec is None:
        raise ValueError("unknown trace_id")
    checkpoint = next((item for item in spec.checkpoints if item.checkpoint_id == checkpoint_id), None)
    if checkpoint is None:
        raise ValueError("unknown checkpoint_id")
    if checkpoint.raw_for(layer) != raw:
        raise ValueError("raw outcome is not frozen for this trace/checkpoint/layer")
    return checkpoint.expected


def validate_layer_row(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ROW_FIELDS:
        raise ValueError("layer row fields do not exactly match schema")
    if value["schema"] != C83A_ROW_SCHEMA:
        raise ValueError("unexpected layer-row schema")
    trace_id = value["trace_id"]
    trial_id = value["trial_id"]
    checkpoint_id = value["checkpoint_id"]
    raw_outcome = value["raw_outcome"]
    state_fp = value["semantic_state_fingerprint"]
    if not all(isinstance(item, str) and item for item in (trace_id, trial_id, checkpoint_id, raw_outcome, state_fp)):
        raise ValueError("row identifiers/outcomes/fingerprint must be non-empty strings")
    try:
        layer = C83Layer(value["layer"])
        normalized = C83NormalizedOutcome(value["normalized_outcome"])
    except (TypeError, ValueError) as exc:
        raise ValueError("unknown layer or normalized outcome") from exc
    expected = normalize_raw_outcome(trace_id, checkpoint_id, layer, raw_outcome)
    if normalized is not expected:
        raise ValueError("normalized outcome does not match frozen raw normalization")
    for name in ("opportunities", "violations"):
        count = value[name]
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if value["violations"] > value["opportunities"]:
        raise ValueError("violations cannot exceed opportunities")
    spec = next(item for item in C83A_TRACE_SPECS if item.trace_id == trace_id)
    checkpoint = next(item for item in spec.checkpoints if item.checkpoint_id == checkpoint_id)
    if value["opportunities"] != (1 if checkpoint.opportunity else 0):
        raise ValueError("opportunity denominator differs from frozen checkpoint contract")
    if not isinstance(value["explicit_non_success"], bool):
        raise TypeError("explicit_non_success must be bool")
    expected_non_success = normalized in {
        C83NormalizedOutcome.REJECTED,
        C83NormalizedOutcome.WAIT,
        C83NormalizedOutcome.RETRY,
        C83NormalizedOutcome.RECOMPUTE,
        C83NormalizedOutcome.FAIL,
        C83NormalizedOutcome.AMBIGUOUS,
    }
    if value["explicit_non_success"] != expected_non_success:
        raise ValueError("explicit_non_success disagrees with normalized outcome")
    topology = value["topology_provenance"]
    fault_fp = value["fault_script_fingerprint"]
    if layer is C83Layer.C8:
        if not isinstance(topology, Mapping):
            raise ValueError("C8 row requires topology provenance")
        required = {"authority_pid", "fault_harness_pid", "worker_pids", "transport_id", "real_process_boundary"}
        if set(topology) != required or topology["real_process_boundary"] is not True:
            raise ValueError("C8 topology provenance is incomplete")
        if not isinstance(fault_fp, str) or len(fault_fp) != 64:
            raise ValueError("C8 row requires fault-script fingerprint")
        if fault_fp != spec.fault_script_fingerprint():
            raise ValueError("C8 fault-script fingerprint differs from frozen trace contract")
    else:
        if topology is not None or fault_fp is not None:
            raise ValueError("only C8 rows may carry C8 topology/fault provenance")
    return dict(value)


def validate_comparison(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _COMPARISON_FIELDS:
        raise ValueError("comparison fields do not exactly match schema")
    if value["schema"] != C83A_COMPARISON_SCHEMA:
        raise ValueError("unexpected comparison schema")
    trace_id = value["trace_id"]
    trial_id = value["trial_id"]
    if not isinstance(trace_id, str) or not trace_id or not isinstance(trial_id, str) or not trial_id:
        raise ValueError("comparison trace_id/trial_id must be non-empty")
    rows = value["layer_rows"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("comparison requires layer_rows")
    validated = [validate_layer_row(row) for row in rows]
    if any(row["trace_id"] != trace_id or row["trial_id"] != trial_id for row in validated):
        raise ValueError("comparison rows escaped trace/trial identity")
    spec = next((item for item in C83A_TRACE_SPECS if item.trace_id == trace_id), None)
    if spec is None:
        raise ValueError("unknown comparison trace_id")
    expected_keys = {
        (layer.value, checkpoint.checkpoint_id)
        for layer in C83Layer
        for checkpoint in spec.checkpoints
    }
    actual_keys = {(row["layer"], row["checkpoint_id"]) for row in validated}
    if actual_keys != expected_keys or len(actual_keys) != len(validated):
        raise ValueError("comparison must contain exactly one row per layer/checkpoint")

    derived_vectors = {
        layer.value: [
            next(
                row["normalized_outcome"]
                for row in validated
                if row["layer"] == layer.value and row["checkpoint_id"] == checkpoint.checkpoint_id
            )
            for checkpoint in spec.checkpoints
        ]
        for layer in C83Layer
    }
    derived_opportunities = {
        layer.value: sum(row["opportunities"] for row in validated if row["layer"] == layer.value)
        for layer in C83Layer
    }
    derived_violations = {
        layer.value: sum(row["violations"] for row in validated if row["layer"] == layer.value)
        for layer in C83Layer
    }
    if value["checkpoint_vector_by_layer"] != derived_vectors:
        raise ValueError("comparison checkpoint vectors are not derived from layer rows")
    if value["opportunity_count_by_layer"] != derived_opportunities:
        raise ValueError("comparison opportunity counts are not derived from rows")
    if value["violation_count_by_layer"] != derived_violations:
        raise ValueError("comparison violation counts are not derived from rows")
    equivalent = (
        len({tuple(items) for items in derived_vectors.values()}) == 1
        and len(set(derived_opportunities.values())) == 1
        and all(count == 0 for count in derived_violations.values())
    )
    if value["semantic_equivalent"] is not equivalent:
        raise ValueError("semantic_equivalent must be derived without timing")
    return dict(value)


validate_protocol_identity()
