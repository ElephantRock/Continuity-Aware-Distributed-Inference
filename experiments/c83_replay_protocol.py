from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from experiments.c8_protocol import (
    C8_PROTOCOL_FINGERPRINT,
    C8_TRACE_SPECS,
    C8_TRANSPORT_ID,
)
from experiments.correctness import CorrectnessMetric
from prototype.c8_faults import DeliveryAction, DeliveryScheduler


C83A_PROTOCOL_SCHEMA = "cadi.c8.3a.cross-layer-replay-protocol.v2"
C83A_BASE_COMMIT = "85a0ef4be82d45dc5e309332c5f41d067e4c952a"
C83A_C81_PROTOCOL_FINGERPRINT = (
    "616cc4daa4c167152875522767a8cc968da25c63d6acc099c265bc3554641cb6"
)
C83A_COMPARATIVE_RESULT_INSPECTION = "NONE"
C83A_ROW_SCHEMA = "cadi.c8.3.layer-row.v2"
C83A_COMPARISON_SCHEMA = "cadi.c8.3.comparison.v2"
C83A_REPLAY_EXECUTION_FAILURE_RAW = "REPLAY_EXECUTION_FAILURE"
C83A_PROTOCOL_FINGERPRINT = (
    "627eb6497a849fe3532a2e7163715ef624a48b5a923f8d8cd3a84e2558b02890"
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


_EXPLICIT_NON_SUCCESS = frozenset(
    {
        C83NormalizedOutcome.REJECTED,
        C83NormalizedOutcome.WAIT,
        C83NormalizedOutcome.RETRY,
        C83NormalizedOutcome.RECOMPUTE,
        C83NormalizedOutcome.FAIL,
        C83NormalizedOutcome.AMBIGUOUS,
    }
)


@dataclass(frozen=True, slots=True)
class C83RawRule:
    layer: C83Layer
    raw_outcome: str
    normalized_outcome: C83NormalizedOutcome
    forbidden_violation: bool

    def __post_init__(self) -> None:
        if not isinstance(self.layer, C83Layer):
            raise TypeError("layer must be C83Layer")
        if not isinstance(self.raw_outcome, str) or not self.raw_outcome:
            raise ValueError("raw_outcome must be non-empty")
        if self.raw_outcome == C83A_REPLAY_EXECUTION_FAILURE_RAW:
            raise ValueError("reserved replay-execution-failure raw outcome cannot be a semantic rule")
        if not isinstance(self.normalized_outcome, C83NormalizedOutcome):
            raise TypeError("normalized_outcome must be C83NormalizedOutcome")
        if not isinstance(self.forbidden_violation, bool):
            raise TypeError("forbidden_violation must be bool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "raw_outcome": self.raw_outcome,
            "normalized_outcome": self.normalized_outcome.value,
            "forbidden_violation": self.forbidden_violation,
        }


@dataclass(frozen=True, slots=True)
class C83CheckpointSpec:
    checkpoint_id: str
    expected: C83NormalizedOutcome
    opportunity: bool
    raw_rules: tuple[C83RawRule, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_id, str) or not self.checkpoint_id:
            raise ValueError("checkpoint_id must be non-empty")
        if not isinstance(self.expected, C83NormalizedOutcome):
            raise TypeError("expected must be C83NormalizedOutcome")
        if not isinstance(self.opportunity, bool):
            raise TypeError("opportunity must be bool")
        if not isinstance(self.raw_rules, tuple) or not self.raw_rules:
            raise ValueError("raw_rules must be non-empty")
        if not all(isinstance(item, C83RawRule) for item in self.raw_rules):
            raise TypeError("raw_rules must contain C83RawRule")
        keys = tuple((item.layer, item.raw_outcome) for item in self.raw_rules)
        if len(keys) != len(set(keys)):
            raise ValueError("raw normalization keys must be unique")
        for layer in C83Layer:
            layer_rules = tuple(item for item in self.raw_rules if item.layer is layer)
            if not layer_rules:
                raise ValueError(f"raw normalization must cover {layer.value}")
            if not any(
                item.normalized_outcome is self.expected and not item.forbidden_violation
                for item in layer_rules
            ):
                raise ValueError(f"{layer.value} requires at least one safe rule for expected outcome")
            if any(item.forbidden_violation for item in layer_rules) and not self.opportunity:
                raise ValueError("non-opportunity checkpoint cannot define forbidden violations")
            if self.opportunity and not any(item.forbidden_violation for item in layer_rules):
                raise ValueError(f"{layer.value} opportunity requires a frozen violating rule")

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "expected": self.expected.value,
            "opportunity": self.opportunity,
            "raw_rules": [item.to_dict() for item in self.raw_rules],
        }

    def rule_for(self, layer: C83Layer, raw_outcome: str) -> C83RawRule:
        rule = next(
            (
                item
                for item in self.raw_rules
                if item.layer is layer and item.raw_outcome == raw_outcome
            ),
            None,
        )
        if rule is None:
            raise ValueError("raw outcome is not frozen for this trace/checkpoint/layer")
        return rule


@dataclass(frozen=True, slots=True)
class C83TraceAdapterSpec:
    trace_id: str
    c1_entry: str
    c2_entry: str
    c8_driver: str
    c8_fault_script: tuple[tuple[str, tuple[DeliveryAction, ...]], ...]
    c8_required_message_kinds: tuple[str, ...]
    c8_lifecycle_actions: tuple[str, ...]
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
        if not isinstance(self.c8_required_message_kinds, tuple) or not self.c8_required_message_kinds:
            raise ValueError("c8_required_message_kinds must be a non-empty tuple")
        if not all(isinstance(item, str) and item for item in self.c8_required_message_kinds):
            raise ValueError("c8_required_message_kinds must contain non-empty strings")
        if len(self.c8_required_message_kinds) != len(set(self.c8_required_message_kinds)):
            raise ValueError("c8_required_message_kinds must be unique")
        if tuple(sorted(self.c8_required_message_kinds)) != self.c8_required_message_kinds:
            raise ValueError("c8_required_message_kinds must be canonically ordered")
        if not isinstance(self.c8_lifecycle_actions, tuple) or not self.c8_lifecycle_actions:
            raise ValueError("c8_lifecycle_actions must be a non-empty tuple")
        if not all(isinstance(item, str) and item for item in self.c8_lifecycle_actions):
            raise ValueError("c8_lifecycle_actions must contain non-empty strings")
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
            if kind not in self.c8_required_message_kinds:
                raise ValueError("fault-script message kind must also be a required message kind")
            if not isinstance(actions, tuple) or not actions:
                raise ValueError("fault script actions must be non-empty tuples")
            if not all(isinstance(action, DeliveryAction) for action in actions):
                raise TypeError("fault script actions must be DeliveryAction")
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
            "c8_required_message_kinds": list(self.c8_required_message_kinds),
            "c8_lifecycle_actions": list(self.c8_lifecycle_actions),
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
            "required_message_kinds": list(self.c8_required_message_kinds),
            "lifecycle_actions": list(self.c8_lifecycle_actions),
            "fault_script": self.to_dict()["c8_fault_script"],
        }
        return _fingerprint(payload)


def _rules(
    expected: C83NormalizedOutcome,
    c1_safe: str,
    c2_safe: str,
    c8_safe: str,
    *,
    violating_raw: str | None = None,
    allow_semantic_fail: bool = True,
) -> tuple[C83RawRule, ...]:
    values: list[C83RawRule] = []
    safe = {
        C83Layer.C1: c1_safe,
        C83Layer.C2: c2_safe,
        C83Layer.C8: c8_safe,
    }
    for layer in C83Layer:
        values.append(C83RawRule(layer, safe[layer], expected, False))
        if allow_semantic_fail and expected is not C83NormalizedOutcome.FAIL:
            values.append(C83RawRule(layer, "SEMANTIC_CHECKPOINT_FAILED", C83NormalizedOutcome.FAIL, False))
        if violating_raw is not None:
            values.append(C83RawRule(layer, violating_raw, C83NormalizedOutcome.COMMITTED, True))
    return tuple(values)


def _checkpoint(
    checkpoint_id: str,
    expected: C83NormalizedOutcome,
    opportunity: bool,
    c1_safe: str,
    c2_safe: str,
    c8_safe: str,
    *,
    violating_raw: str | None = None,
) -> C83CheckpointSpec:
    if opportunity and violating_raw is None:
        raise ValueError("opportunity checkpoint requires a violating_raw classification")
    return C83CheckpointSpec(
        checkpoint_id=checkpoint_id,
        expected=expected,
        opportunity=opportunity,
        raw_rules=_rules(
            expected,
            c1_safe,
            c2_safe,
            c8_safe,
            violating_raw=violating_raw,
        ),
    )


C83A_TRACE_SPECS = (
    C83TraceAdapterSpec(
        trace_id="C8-X1-LATE-SUPERSEDED-ATTEMPT",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr3_reordered_old_retry_event_cannot_override_committed_attempt",
        c2_entry="simulator.faults.FaultInjector.reorder_after + FaultClass.LATE_ATTEMPT_RESULT",
        c8_driver="authority-local Attempt setup; old worker COMPLETE is held until a newer Attempt commits, then presented late",
        c8_fault_script=(("COMPLETE", (DeliveryAction.LATE_DELIVER,)),),
        c8_required_message_kinds=("COMPLETE", "PROCESS_FAILURE", "REGISTER", "WORK"),
        c8_lifecycle_actions=(
            "START_WORKER_GENERATION_1",
            "CAPTURE_AND_HOLD_GENERATION_1_COMPLETE",
            "TERMINATE_WORKER_GENERATION_1",
            "START_WORKER_GENERATION_2",
            "DELIVER_GENERATION_2_COMPLETE",
            "LATE_DELIVER_GENERATION_1_COMPLETE",
        ),
        worker_generations=2,
        requires_worker_restart=True,
        forbidden_metric=CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,
        opportunity_definition="one presentation of a superseded Attempt result after a newer Attempt is authoritative",
        violation_definition="the superseded Attempt becomes the LogicalRequest committed Attempt or authoritative output source",
        checkpoints=(
            _checkpoint(
                "current-attempt-finalize",
                C83NormalizedOutcome.COMMITTED,
                False,
                "CURRENT_ATTEMPT_FINALIZED",
                "CURRENT_ATTEMPT_COMMITTED",
                "CURRENT_ATTEMPT_COMMITTED",
            ),
            _checkpoint(
                "stale-attempt-presentation",
                C83NormalizedOutcome.REJECTED,
                True,
                "INVALID_TRANSITION_STALE_ATTEMPT",
                "IGNORE_STALE",
                "STALE_ATTEMPT_FENCED",
                violating_raw="STALE_ATTEMPT_COMMITTED",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X2-DUPLICATE-COMPLETION",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr2_duplicate_result_finalizes_once",
        c2_entry="simulator.faults.FaultInjector.duplicate_delivery + FaultClass.DELIVERY_DUPLICATE",
        c8_driver="one worker COMPLETE is duplicated by the external harness with identical transport message identity",
        c8_fault_script=(("COMPLETE", (DeliveryAction.DUPLICATE,)),),
        c8_required_message_kinds=("COMPLETE", "REGISTER", "WORK"),
        c8_lifecycle_actions=("START_WORKER_GENERATION_1", "DUPLICATE_COMPLETE"),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
        opportunity_definition="one duplicate presentation of an already-authoritatively-finalized completion",
        violation_definition="the duplicate causes a second authoritative finalization or changes the committed output",
        checkpoints=(
            _checkpoint(
                "first-finalization",
                C83NormalizedOutcome.COMMITTED,
                False,
                "FIRST_FINALIZATION",
                "FIRST_FINALIZATION",
                "FIRST_FINALIZATION",
            ),
            _checkpoint(
                "duplicate-presentation",
                C83NormalizedOutcome.IDEMPOTENT_NOOP,
                True,
                "IDEMPOTENT_FINALIZE",
                "IGNORE_DUPLICATE",
                "IDEMPOTENT_FINALIZE",
                violating_raw="DUPLICATE_FINALIZATION",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X3-WRONG-SIBLING-STATE",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr4_wrong_sibling_state_is_rejected",
        c2_entry="simulator.semantic_adapter.ContinuityAdapter state compatibility path under policy-neutral State placement",
        c8_driver="authority constructs sibling Continuations; real worker completion triggers an authority-local consume decision against sibling State",
        c8_fault_script=(),
        c8_required_message_kinds=("COMPLETE", "REGISTER", "WORK"),
        c8_lifecycle_actions=("START_WORKER_GENERATION_1", "REQUEST_SIBLING_STATE_CONSUME"),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.WRONG_BRANCH_REUSE_RATE,
        opportunity_definition="one attempted consume of State whose lineage is an incompatible sibling of the target Continuation",
        violation_definition="the incompatible sibling State is consumed or treated as reusable",
        checkpoints=(
            _checkpoint(
                "incompatible-state-consume",
                C83NormalizedOutcome.REJECTED,
                True,
                "STATE_INCOMPATIBLE",
                "REJECT_REUSE",
                "STATE_INCOMPATIBLE",
                violating_raw="WRONG_SIBLING_STATE_CONSUMED",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X4-STALE-BINDING",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr8_late_old_binding_observation_cannot_restore_old_owner",
        c2_entry="simulator.faults.FaultInjector.delay_delivery/reorder_after + simulator.semantic_adapter.ContinuityAdapter",
        c8_driver="authority commits a replacement Binding before a delayed old-owner observation is presented",
        c8_fault_script=(("COMPLETE", (DeliveryAction.DELAY,)),),
        c8_required_message_kinds=("COMPLETE", "REGISTER", "WORK"),
        c8_lifecycle_actions=("START_WORKER_GENERATION_1", "DELAY_OLD_BINDING_OBSERVATION", "COMMIT_REPLACEMENT_BINDING", "FLUSH_DELAYED_OBSERVATION"),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,
        opportunity_definition="one stale Binding-epoch observation presented after a newer Binding is authoritative",
        violation_definition="the stale observation restores the prior owner or regresses the authoritative epoch",
        checkpoints=(
            _checkpoint(
                "replacement-binding-commit",
                C83NormalizedOutcome.COMMITTED,
                False,
                "NEW_BINDING_COMMITTED",
                "NEW_BINDING_COMMITTED",
                "NEW_BINDING_COMMITTED",
            ),
            _checkpoint(
                "stale-binding-presentation",
                C83NormalizedOutcome.IDEMPOTENT_NOOP,
                True,
                "STALE_EVENT_RECORDED_NO_AUTHORITY_CHANGE",
                "IGNORE_STALE",
                "STALE_BINDING_IGNORED",
                violating_raw="STALE_BINDING_RESTORED",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X5-AMBIGUOUS-OWNERSHIP",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr9_ambiguous_ownership_fails_closed",
        c2_entry="simulator.fault_oracle + simulator.semantic_adapter.ContinuityAdapter reconciliation path",
        c8_driver="real worker observations trigger authority-local ambiguous Evidence reconciliation before ownership commit",
        c8_fault_script=(),
        c8_required_message_kinds=("COMPLETE", "REGISTER", "WORK"),
        c8_lifecycle_actions=("START_WORKER_GENERATION_1", "PRESENT_AMBIGUOUS_OWNERSHIP_EVIDENCE"),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,
        opportunity_definition="one correctness-sensitive ownership commit opportunity with ambiguous or conflicting sufficient-scope observations",
        violation_definition="an authoritative ownership commit occurs while reconciliation is AMBIGUOUS",
        checkpoints=(
            _checkpoint(
                "ambiguous-reconciliation",
                C83NormalizedOutcome.AMBIGUOUS,
                True,
                "RECONCILE_AMBIGUOUS",
                "AMBIGUOUS",
                "RECONCILE_AMBIGUOUS",
                violating_raw="AMBIGUOUS_OWNERSHIP_COMMITTED",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X6-STATE-EVICTION-TOOL-WAIT",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr11_tool_wait_eviction_preserves_lineage_but_forces_cold_resume",
        c2_entry="simulator.faults.FaultClass.REPLICA_EVICTION + simulator.fault_campaign replay",
        c8_driver="authority suspends the Continuation; the worker holding the sole replica is terminated; resume attempts reuse after the explicit failure observation",
        c8_fault_script=(),
        c8_required_message_kinds=("PROCESS_FAILURE", "REGISTER", "WORK"),
        c8_lifecycle_actions=("START_WORKER_GENERATION_1", "SUSPEND_CONTINUATION", "TERMINATE_WORKER_GENERATION_1", "OBSERVE_REPLICA_LOSS", "RESUME_AND_ATTEMPT_REUSE"),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE,
        opportunity_definition="one resume-time consume decision after the only physical replica was lost during WAITING",
        violation_definition="the absent/lost replica is consumed or reported as a successful reusable State path",
        checkpoints=(
            _checkpoint(
                "resume-after-eviction",
                C83NormalizedOutcome.REJECTED,
                True,
                "CAN_CONSUME_FALSE",
                "REJECT_REUSE",
                "REPLICA_LOST_REUSE_REJECTED",
                violating_raw="LOST_REPLICA_CONSUMED",
            ),
        ),
    ),
    C83TraceAdapterSpec(
        trace_id="C8-X7-PARTIAL-MIGRATION",
        c1_entry="tests/counterexamples/test_failure_model_traces.py::test_ftr6_partial_migration_does_not_commit_destination",
        c2_entry="simulator.fault_oracle + migration interruption/failure path",
        c8_driver="authority begins migration; the destination worker is terminated after materialization starts but before authoritative commit",
        c8_fault_script=(),
        c8_required_message_kinds=("PROCESS_FAILURE", "REGISTER", "WORK"),
        c8_lifecycle_actions=("START_DESTINATION_WORKER", "BEGIN_MATERIALIZATION", "TERMINATE_DESTINATION_BEFORE_COMMIT", "ATTEMPT_MIGRATION_COMMIT"),
        worker_generations=1,
        requires_worker_restart=False,
        forbidden_metric=CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,
        opportunity_definition="one destination ownership commit attempt after only partial materialization and without sufficient commit Evidence",
        violation_definition="the destination Binding becomes authoritative despite missing/insufficient Evidence",
        checkpoints=(
            _checkpoint(
                "partial-migration-commit",
                C83NormalizedOutcome.WAIT,
                True,
                "INSUFFICIENT_EVIDENCE",
                "WAIT",
                "INSUFFICIENT_EVIDENCE",
                violating_raw="PARTIAL_DESTINATION_COMMITTED",
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
        "replay_execution_failure",
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
        "expected_checkpoint_vector",
        "expected_outcome_match_by_layer",
        "semantic_equivalent",
        "opportunity_count_by_layer",
        "opportunity_counts_match",
        "violation_count_by_layer",
        "forbidden_violation_free",
        "execution_failure_count",
        "rankable",
        "correctness_pass",
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def protocol_payload() -> dict[str, Any]:
    return {
        "schema": C83A_PROTOCOL_SCHEMA,
        "base_commit": C83A_BASE_COMMIT,
        "c81_protocol_fingerprint": C83A_C81_PROTOCOL_FINGERPRINT,
        "comparative_result_inspection": C83A_COMPARATIVE_RESULT_INSPECTION,
        "row_schema": C83A_ROW_SCHEMA,
        "comparison_schema": C83A_COMPARISON_SCHEMA,
        "reserved_replay_execution_failure_raw": C83A_REPLAY_EXECUTION_FAILURE_RAW,
        "normalization": [item.value for item in C83NormalizedOutcome],
        "layers": [item.value for item in C83Layer],
        "trace_specs": [item.to_dict() for item in C83A_TRACE_SPECS],
        "equivalence": {
            "unit": "ordered semantic checkpoint vector",
            "timing_used": False,
            "semantic_equivalent_requires_expected_outcome": False,
            "semantic_equivalent_requires_zero_forbidden_violations": False,
            "pass_requires_expected_outcomes": True,
            "pass_requires_zero_forbidden_violations": True,
            "pass_requires_rankable_execution": True,
            "opportunity_denominators_are_frozen": True,
        },
        "multiplicity": {
            "deterministic_trials_per_trace_fixture": 1,
            "bootstrap": False,
            "stochastic_seed_multiplication": False,
        },
        "invalidity": {
            "missing_or_duplicate_layer_checkpoint_row": "INVALID",
            "replay_execution_failure": "UNRANKABLE",
            "topology_or_fault_script_provenance_mismatch": "INVALID",
            "timing_used_for_semantic_equivalence": "INVALID",
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
    actual = protocol_fingerprint()
    if actual != C83A_PROTOCOL_FINGERPRINT:
        raise RuntimeError(
            f"C8.3a protocol payload diverged from frozen fingerprint: {actual}"
        )


def normalize_raw_outcome(
    trace_id: str,
    checkpoint_id: str,
    layer: C83Layer,
    raw: str,
) -> tuple[C83NormalizedOutcome, bool]:
    if not isinstance(layer, C83Layer):
        raise TypeError("layer must be C83Layer")
    spec = next((item for item in C83A_TRACE_SPECS if item.trace_id == trace_id), None)
    if spec is None:
        raise ValueError("unknown trace_id")
    checkpoint = next((item for item in spec.checkpoints if item.checkpoint_id == checkpoint_id), None)
    if checkpoint is None:
        raise ValueError("unknown checkpoint_id")
    rule = checkpoint.rule_for(layer, raw)
    return rule.normalized_outcome, rule.forbidden_violation


def _validate_topology(value: object, spec: C83TraceAdapterSpec) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("C8 row requires topology provenance")
    required = {
        "authority_pid",
        "fault_harness_pid",
        "worker_pids",
        "transport_id",
        "real_process_boundary",
    }
    if set(value) != required or value["real_process_boundary"] is not True:
        raise ValueError("C8 topology provenance is incomplete")
    authority_pid = value["authority_pid"]
    harness_pid = value["fault_harness_pid"]
    worker_pids = value["worker_pids"]
    for pid, name in ((authority_pid, "authority_pid"), (harness_pid, "fault_harness_pid")):
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if not isinstance(worker_pids, list) or len(worker_pids) != spec.worker_generations:
        raise ValueError("worker_pids must match frozen worker generation count")
    if not all(isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 for pid in worker_pids):
        raise ValueError("worker_pids must contain positive integers")
    all_pids = [authority_pid, harness_pid, *worker_pids]
    if len(all_pids) != len(set(all_pids)):
        raise ValueError("C8 topology PIDs must be distinct")
    if value["transport_id"] != C8_TRANSPORT_ID:
        raise ValueError("C8 topology transport differs from frozen reference transport")
    return dict(value)


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
    if not all(
        isinstance(item, str) and item
        for item in (trace_id, trial_id, checkpoint_id, raw_outcome)
    ):
        raise ValueError("row identifiers/outcomes must be non-empty strings")
    if not _is_sha256(state_fp):
        raise ValueError("semantic_state_fingerprint must be a SHA-256 hex digest")
    try:
        layer = C83Layer(value["layer"])
        normalized = C83NormalizedOutcome(value["normalized_outcome"])
    except (TypeError, ValueError) as exc:
        raise ValueError("unknown layer or normalized outcome") from exc
    spec = next((item for item in C83A_TRACE_SPECS if item.trace_id == trace_id), None)
    if spec is None:
        raise ValueError("unknown trace_id")
    checkpoint = next((item for item in spec.checkpoints if item.checkpoint_id == checkpoint_id), None)
    if checkpoint is None:
        raise ValueError("unknown checkpoint_id")
    replay_failure = value["replay_execution_failure"]
    if not isinstance(replay_failure, bool):
        raise TypeError("replay_execution_failure must be bool")
    if replay_failure:
        expected_normalized = C83NormalizedOutcome.FAIL
        expected_violation = False
        if raw_outcome != C83A_REPLAY_EXECUTION_FAILURE_RAW:
            raise ValueError("replay execution failure must use reserved raw outcome")
    else:
        expected_normalized, expected_violation = normalize_raw_outcome(
            trace_id, checkpoint_id, layer, raw_outcome
        )
    if normalized is not expected_normalized:
        raise ValueError("normalized outcome does not match frozen raw normalization")
    for name in ("opportunities", "violations"):
        count = value[name]
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    expected_opportunities = 1 if checkpoint.opportunity else 0
    if value["opportunities"] != expected_opportunities:
        raise ValueError("opportunity denominator differs from frozen checkpoint contract")
    expected_violations = 1 if checkpoint.opportunity and expected_violation and not replay_failure else 0
    if value["violations"] != expected_violations:
        raise ValueError("violation count does not match frozen raw-outcome classification")
    if not isinstance(value["explicit_non_success"], bool):
        raise TypeError("explicit_non_success must be bool")
    if value["explicit_non_success"] != (normalized in _EXPLICIT_NON_SUCCESS):
        raise ValueError("explicit_non_success disagrees with normalized outcome")
    topology = value["topology_provenance"]
    fault_fp = value["fault_script_fingerprint"]
    if layer is C83Layer.C8:
        _validate_topology(topology, spec)
        if not _is_sha256(fault_fp):
            raise ValueError("C8 row requires a SHA-256 fault-script fingerprint")
        if fault_fp != spec.fault_script_fingerprint():
            raise ValueError("C8 fault-script fingerprint differs from frozen trace contract")
    else:
        if replay_failure:
            raise ValueError("replay_execution_failure is reserved for physical C8 replay")
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
                if row["layer"] == layer.value
                and row["checkpoint_id"] == checkpoint.checkpoint_id
            )
            for checkpoint in spec.checkpoints
        ]
        for layer in C83Layer
    }
    expected_vector = [checkpoint.expected.value for checkpoint in spec.checkpoints]
    expected_match = {
        layer.value: derived_vectors[layer.value] == expected_vector for layer in C83Layer
    }
    derived_opportunities = {
        layer.value: sum(
            row["opportunities"] for row in validated if row["layer"] == layer.value
        )
        for layer in C83Layer
    }
    derived_violations = {
        layer.value: sum(
            row["violations"] for row in validated if row["layer"] == layer.value
        )
        for layer in C83Layer
    }
    execution_failures = sum(1 for row in validated if row["replay_execution_failure"])
    semantic_equivalent = len({tuple(items) for items in derived_vectors.values()}) == 1
    opportunity_counts_match = len(set(derived_opportunities.values())) == 1
    forbidden_violation_free = all(count == 0 for count in derived_violations.values())
    rankable = execution_failures == 0 and opportunity_counts_match
    correctness_pass = (
        rankable
        and semantic_equivalent
        and all(expected_match.values())
        and forbidden_violation_free
    )

    derived_fields = {
        "checkpoint_vector_by_layer": derived_vectors,
        "expected_checkpoint_vector": expected_vector,
        "expected_outcome_match_by_layer": expected_match,
        "semantic_equivalent": semantic_equivalent,
        "opportunity_count_by_layer": derived_opportunities,
        "opportunity_counts_match": opportunity_counts_match,
        "violation_count_by_layer": derived_violations,
        "forbidden_violation_free": forbidden_violation_free,
        "execution_failure_count": execution_failures,
        "rankable": rankable,
        "correctness_pass": correctness_pass,
    }
    for field, derived in derived_fields.items():
        if value[field] != derived:
            raise ValueError(f"comparison {field} is not derived from frozen layer rows")
    return dict(value)


validate_protocol_identity()
