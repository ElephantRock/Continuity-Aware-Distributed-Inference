from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Iterable, Mapping, Optional

from continuity.invariants import InvariantOracle

from .engine import DiscreteEventSimulator
from .events import EventKind, freeze_payload
from .fault_campaign import FaultCampaignManifest, FaultReplayEntry
from .faults import FaultClass, FaultInjector, FaultRecord, ProbabilisticFaultDecision
from .resources import ReplicaRuntimeStatus, ResourceModel, WorkerStatus
from .semantic_adapter import ContinuityAdapter


FAULT_RECORD_SCHEMA = "cadi.fault-record.v1"


_CLASS_PARAMETER_KEYS: dict[FaultClass, frozenset[str]] = {
    FaultClass.DELIVERY_DELAY: frozenset(
        {"original_time", "replacement_time", "replacement_event_id"}
    ),
    FaultClass.DELIVERY_DROP: frozenset({"original_time", "event_kind"}),
    FaultClass.DELIVERY_DUPLICATE: frozenset(
        {"original_time", "duplicate_time", "duplicate_event_id"}
    ),
    FaultClass.DELIVERY_REORDER: frozenset(
        {
            "anchor_event_id",
            "anchor_time",
            "original_time",
            "replacement_time",
            "replacement_event_id",
        }
    ),
    FaultClass.WORKER_FAILURE: frozenset({"event_id"}),
    FaultClass.REPLICA_LOSS: frozenset({"event_id"}),
    FaultClass.REPLICA_EVICTION: frozenset({"event_id"}),
    FaultClass.ATTEMPT_TIMEOUT: frozenset(
        {"request_id", "retry_attempt_id", "event_id"}
    ),
    FaultClass.LATE_ATTEMPT_RESULT: frozenset({"request_id", "event_id"}),
    FaultClass.STALE_ATTEMPT_OBSERVATION: frozenset(
        {"request_id", "evidence_id", "output_id", "observed_at", "event_id"}
    ),
}

_EXPECTED_EVENT_KIND: dict[FaultClass, EventKind] = {
    FaultClass.WORKER_FAILURE: EventKind.WORKER_FAILED,
    FaultClass.REPLICA_LOSS: EventKind.STATE_LOST,
    FaultClass.REPLICA_EVICTION: EventKind.STATE_EVICTED,
    FaultClass.ATTEMPT_TIMEOUT: EventKind.ATTEMPT_TIMEOUT,
    FaultClass.LATE_ATTEMPT_RESULT: EventKind.LATE_RESULT,
    FaultClass.STALE_ATTEMPT_OBSERVATION: EventKind.OBSERVATION_CREATED,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _strict_json_loads(text: str) -> Any:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON numeric constant is not allowed: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite JSON numeric value is not allowed: {value}")
        return parsed

    return json.loads(text, parse_constant=reject_constant, parse_float=finite_float)


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _finite_nonnegative(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _finite_unit(value: Any) -> bool:
    return _finite_nonnegative(value) and float(value) <= 1.0


def _json_scalar(value: Any) -> bool:
    return value is None or type(value) in (bool, int, str) or (
        type(value) is float and math.isfinite(value)
    )


def _string_tuple(value: Any) -> bool:
    return isinstance(value, tuple) and all(_is_nonempty_string(item) for item in value)


def _parameter_dict(record: FaultRecord) -> dict[str, Any] | None:
    if not isinstance(record.parameters, tuple):
        return None
    try:
        result = dict(record.parameters)
    except (TypeError, ValueError):
        return None
    if len(result) != len(record.parameters):
        return None
    if not all(_is_nonempty_string(key) and _json_scalar(value) for key, value in result.items()):
        return None
    if freeze_payload(result) != record.parameters:
        return None
    return result


def _record_shape_violations(
    record: Any,
    *,
    injector_seed: int | None = None,
) -> list[str]:
    if not isinstance(record, FaultRecord):
        return [f"record is not FaultRecord: {type(record).__name__}"]

    errors: list[str] = []
    prefix = f"FaultID {record.id!r}"
    if not _is_nonempty_string(record.id):
        errors.append("FaultID must be a non-empty string")
    if not isinstance(record.fault_class, FaultClass):
        errors.append(f"{prefix}: fault_class must be FaultClass")
        return errors
    if not _is_nonempty_string(record.target):
        errors.append(f"{prefix}: target must be a non-empty string")
    if not _finite_nonnegative(record.injection_time):
        errors.append(f"{prefix}: injection_time must be finite and non-negative")
    if not _finite_nonnegative(record.duration):
        errors.append(f"{prefix}: duration must be finite and non-negative")
    if not _is_nonempty_string(record.ground_truth_effect):
        errors.append(f"{prefix}: ground_truth_effect must be a non-empty string")
    if not _string_tuple(record.expected_invariant_pressure):
        errors.append(f"{prefix}: expected_invariant_pressure must be tuple[str, ...]")
    if not _string_tuple(record.expected_safe_outcomes):
        errors.append(f"{prefix}: expected_safe_outcomes must be tuple[str, ...]")
    if not _string_tuple(record.produced_event_ids):
        errors.append(f"{prefix}: produced_event_ids must be tuple[str, ...]")
    if not _string_tuple(record.cancelled_event_ids):
        errors.append(f"{prefix}: cancelled_event_ids must be tuple[str, ...]")
    if isinstance(record.produced_event_ids, tuple) and len(set(record.produced_event_ids)) != len(
        record.produced_event_ids
    ):
        errors.append(f"{prefix}: produced_event_ids contain duplicates")
    if isinstance(record.cancelled_event_ids, tuple) and len(set(record.cancelled_event_ids)) != len(
        record.cancelled_event_ids
    ):
        errors.append(f"{prefix}: cancelled_event_ids contain duplicates")
    if isinstance(record.produced_event_ids, tuple) and isinstance(record.cancelled_event_ids, tuple):
        if set(record.produced_event_ids) & set(record.cancelled_event_ids):
            errors.append(f"{prefix}: one EventID is both produced and cancelled by the same fault")

    params = _parameter_dict(record)
    if params is None:
        errors.append(f"{prefix}: parameters are not a canonical frozen scalar mapping")
        return errors
    expected_keys = _CLASS_PARAMETER_KEYS[record.fault_class]
    if frozenset(params) != expected_keys:
        errors.append(
            f"{prefix}: parameter keys for {record.fault_class.value} do not match schema"
        )

    if (record.seed is None) != (record.draw is None):
        errors.append(f"{prefix}: probabilistic seed and draw must be both present or both absent")
    if record.seed is not None:
        if not isinstance(record.seed, int) or isinstance(record.seed, bool):
            errors.append(f"{prefix}: seed must be an integer")
        elif injector_seed is not None and record.seed != injector_seed:
            errors.append(f"{prefix}: probabilistic seed differs from injector seed")
        if not _finite_unit(record.draw):
            errors.append(f"{prefix}: probabilistic draw must be finite and within [0, 1]")

    produced = record.produced_event_ids if isinstance(record.produced_event_ids, tuple) else ()
    cancelled = record.cancelled_event_ids if isinstance(record.cancelled_event_ids, tuple) else ()

    if record.fault_class in {
        FaultClass.DELIVERY_DELAY,
        FaultClass.DELIVERY_REORDER,
    }:
        if len(produced) != 1 or cancelled != (record.target,):
            errors.append(f"{prefix}: replacement fault must produce one event and cancel target")
    elif record.fault_class is FaultClass.DELIVERY_DROP:
        if produced or cancelled != (record.target,) or float(record.duration) != 0.0:
            errors.append(f"{prefix}: drop must only cancel target with zero duration")
    elif record.fault_class is FaultClass.DELIVERY_DUPLICATE:
        if len(produced) != 1 or cancelled:
            errors.append(f"{prefix}: duplicate must retain target and produce exactly one event")
    else:
        if len(produced) != 1 or cancelled:
            errors.append(f"{prefix}: class must produce exactly one event and cancel none")

    if params is not None and produced:
        event_id = params.get("event_id")
        if record.fault_class in _EXPECTED_EVENT_KIND and event_id != produced[0]:
            errors.append(f"{prefix}: event_id parameter does not match produced EventID")
        replacement = params.get("replacement_event_id")
        if replacement is not None and replacement != produced[0]:
            errors.append(f"{prefix}: replacement_event_id does not match produced EventID")
        duplicate = params.get("duplicate_event_id")
        if duplicate is not None and duplicate != produced[0]:
            errors.append(f"{prefix}: duplicate_event_id does not match produced EventID")

    if params is not None and record.fault_class is FaultClass.DELIVERY_DELAY:
        if _finite_nonnegative(params.get("original_time")) and _finite_nonnegative(
            params.get("replacement_time")
        ):
            expected = float(params["original_time"]) + float(record.duration)
            if not math.isclose(float(params["replacement_time"]), expected, rel_tol=0.0, abs_tol=1e-12):
                errors.append(f"{prefix}: replacement_time does not equal original_time + duration")
    if params is not None and record.fault_class is FaultClass.DELIVERY_DUPLICATE:
        if _finite_nonnegative(params.get("original_time")) and _finite_nonnegative(
            params.get("duplicate_time")
        ):
            expected = float(params["original_time"]) + float(record.duration)
            if not math.isclose(float(params["duplicate_time"]), expected, rel_tol=0.0, abs_tol=1e-12):
                errors.append(f"{prefix}: duplicate_time does not equal original_time + duration")
    if params is not None and record.fault_class is FaultClass.DELIVERY_REORDER:
        time_keys = ("anchor_time", "original_time", "replacement_time")
        if all(_finite_nonnegative(params.get(key)) for key in time_keys):
            original = float(params["original_time"])
            replacement = float(params["replacement_time"])
            anchor = float(params["anchor_time"])
            if not math.isclose(replacement - original, float(record.duration), rel_tol=0.0, abs_tol=1e-12):
                errors.append(f"{prefix}: reorder duration disagrees with replacement/original time")
            if replacement < original or replacement < anchor:
                errors.append(f"{prefix}: reordered replacement precedes target or anchor")

    return errors


def fault_record_to_dict(record: FaultRecord) -> dict[str, Any]:
    errors = _record_shape_violations(record)
    if errors:
        raise ValueError("invalid FaultRecord: " + "; ".join(errors))
    return {
        "schema": FAULT_RECORD_SCHEMA,
        "id": record.id,
        "fault_class": record.fault_class.value,
        "target": record.target,
        "injection_time": float(record.injection_time),
        "duration": float(record.duration),
        "parameters": dict(record.parameters),
        "ground_truth_effect": record.ground_truth_effect,
        "expected_invariant_pressure": list(record.expected_invariant_pressure),
        "expected_safe_outcomes": list(record.expected_safe_outcomes),
        "produced_event_ids": list(record.produced_event_ids),
        "cancelled_event_ids": list(record.cancelled_event_ids),
        "seed": record.seed,
        "draw": record.draw,
    }


def fault_record_to_json(record: FaultRecord) -> str:
    return _canonical_json(fault_record_to_dict(record))


def _tuple_of_strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(_is_nonempty_string(item) for item in value):
        raise ValueError(f"{name} must be a list of non-empty strings")
    return tuple(value)


def fault_record_from_dict(value: Any) -> FaultRecord:
    if not isinstance(value, dict) or value.get("schema") != FAULT_RECORD_SCHEMA:
        raise ValueError("unsupported fault-record schema")
    expected = {
        "schema",
        "id",
        "fault_class",
        "target",
        "injection_time",
        "duration",
        "parameters",
        "ground_truth_effect",
        "expected_invariant_pressure",
        "expected_safe_outcomes",
        "produced_event_ids",
        "cancelled_event_ids",
        "seed",
        "draw",
    }
    if set(value) != expected:
        raise ValueError("fault-record fields do not match schema")
    try:
        fault_class = FaultClass(value["fault_class"])
    except (TypeError, ValueError) as exc:
        raise ValueError("unknown FaultClass in fault record") from exc
    if not isinstance(value["parameters"], Mapping):
        raise ValueError("fault-record parameters must be an object")
    record = FaultRecord(
        id=value["id"],
        fault_class=fault_class,
        target=value["target"],
        injection_time=value["injection_time"],
        duration=value["duration"],
        parameters=freeze_payload(value["parameters"]),
        ground_truth_effect=value["ground_truth_effect"],
        expected_invariant_pressure=_tuple_of_strings(
            value["expected_invariant_pressure"], "expected_invariant_pressure"
        ),
        expected_safe_outcomes=_tuple_of_strings(
            value["expected_safe_outcomes"], "expected_safe_outcomes"
        ),
        produced_event_ids=_tuple_of_strings(value["produced_event_ids"], "produced_event_ids"),
        cancelled_event_ids=_tuple_of_strings(
            value["cancelled_event_ids"], "cancelled_event_ids"
        ),
        seed=value["seed"],
        draw=value["draw"],
    )
    errors = _record_shape_violations(record)
    if errors:
        raise ValueError("fault record violates schema: " + "; ".join(errors))
    return record


def fault_record_from_json(text: str) -> FaultRecord:
    return fault_record_from_dict(_strict_json_loads(text))


def fault_records_to_jsonl(records: Iterable[FaultRecord]) -> str:
    lines = [fault_record_to_json(record) for record in records]
    return "\n".join(lines) + ("\n" if lines else "")


def fault_records_from_jsonl(text: str) -> tuple[FaultRecord, ...]:
    records: list[FaultRecord] = []
    for line in text.splitlines():
        if line.strip():
            records.append(fault_record_from_json(line))
    return tuple(records)


@dataclass(frozen=True, slots=True)
class FaultTrustReport:
    checked_fault_ids: tuple[str, ...]
    violations: tuple[str, ...]
    semantic_invariants_ok: bool
    manifest_fingerprint: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.violations


class FaultTrustOracle:
    """Independent validator for C2.4 fault records, effects, and campaign linkage."""

    def __init__(
        self,
        simulator: DiscreteEventSimulator,
        records: Iterable[FaultRecord],
        *,
        injector_seed: int,
        decisions: Iterable[ProbabilisticFaultDecision] = (),
        resources: ResourceModel | None = None,
        adapter: ContinuityAdapter | None = None,
        manifest: FaultCampaignManifest | None = None,
    ) -> None:
        if not isinstance(simulator, DiscreteEventSimulator):
            raise TypeError("simulator must be DiscreteEventSimulator")
        if not isinstance(injector_seed, int) or isinstance(injector_seed, bool):
            raise TypeError("injector_seed must be an integer")
        if resources is not None and resources.simulator is not simulator:
            raise ValueError("resources must reference oracle simulator")
        if adapter is not None and adapter.simulator is not simulator:
            raise ValueError("adapter must reference oracle simulator")
        if manifest is not None and not isinstance(manifest, FaultCampaignManifest):
            raise TypeError("manifest must be FaultCampaignManifest or None")
        self.simulator = simulator
        self.records = tuple(records)
        self.injector_seed = injector_seed
        self.decisions = tuple(decisions)
        self.resources = resources
        self.adapter = adapter
        self.manifest = manifest

    @classmethod
    def from_injector(
        cls,
        injector: FaultInjector,
        *,
        adapter: ContinuityAdapter | None = None,
        manifest: FaultCampaignManifest | None = None,
    ) -> "FaultTrustOracle":
        if not isinstance(injector, FaultInjector):
            raise TypeError("injector must be FaultInjector")
        return cls(
            injector.simulator,
            injector.records,
            injector_seed=injector.seed,
            decisions=injector.decisions,
            resources=injector.resources,
            adapter=adapter,
            manifest=manifest,
        )

    def inspect(self) -> FaultTrustReport:
        violations: list[str] = []
        valid_records: list[FaultRecord] = []
        seen_ids: set[str] = set()
        previous_time = -1.0

        for record in self.records:
            shape_errors = _record_shape_violations(record, injector_seed=self.injector_seed)
            violations.extend(shape_errors)
            if not isinstance(record, FaultRecord):
                continue
            if record.id in seen_ids:
                violations.append(f"duplicate FaultID in oracle input: {record.id}")
            seen_ids.add(record.id)
            if _finite_nonnegative(record.injection_time):
                if float(record.injection_time) < previous_time:
                    violations.append("FaultRecord order is not monotonic by injection_time")
                previous_time = max(previous_time, float(record.injection_time))
                if float(record.injection_time) > self.simulator.now + 1e-12:
                    violations.append(f"FaultID {record.id}: injection_time is in simulator future")
            if not shape_errors:
                valid_records.append(record)

        violations.extend(self._decision_violations(valid_records))
        violations.extend(self._transformation_violations(valid_records))
        violations.extend(self._runtime_violations(valid_records))
        semantic_ok = self._semantic_invariants(violations)
        violations.extend(self._manifest_violations(valid_records))

        checked = tuple(record.id for record in valid_records)
        return FaultTrustReport(
            checked_fault_ids=checked,
            violations=tuple(violations),
            semantic_invariants_ok=semantic_ok,
            manifest_fingerprint=(
                None if self.manifest is None else self.manifest.manifest_fingerprint
            ),
        )

    def assert_all(self) -> FaultTrustReport:
        report = self.inspect()
        if report.violations:
            raise AssertionError(
                "fault trust oracle violations:\n- " + "\n- ".join(report.violations)
            )
        return report

    def _decision_violations(self, records: list[FaultRecord]) -> list[str]:
        errors: list[str] = []
        by_id = {record.id: record for record in records}
        expected_ordinals = list(range(len(self.decisions)))
        actual_ordinals: list[Any] = []
        for decision in self.decisions:
            if not isinstance(decision, ProbabilisticFaultDecision):
                errors.append(
                    f"probabilistic decision is not ProbabilisticFaultDecision: {type(decision).__name__}"
                )
                continue
            actual_ordinals.append(decision.ordinal)
            if decision.seed != self.injector_seed:
                errors.append("probabilistic decision seed differs from injector seed")
            if not isinstance(decision.ordinal, int) or isinstance(decision.ordinal, bool) or decision.ordinal < 0:
                errors.append("probabilistic decision ordinal is invalid")
            if not _is_nonempty_string(decision.target):
                errors.append("probabilistic decision target is invalid")
            if not _finite_unit(decision.draw):
                errors.append("probabilistic decision draw is outside [0, 1]")
            if decision.selected_fault_class is None:
                if decision.fault_id is not None:
                    errors.append("no-fault probabilistic decision names a FaultID")
                continue
            if not isinstance(decision.selected_fault_class, FaultClass):
                errors.append("probabilistic decision selected class is invalid")
                continue
            if not _is_nonempty_string(decision.fault_id):
                errors.append("selected probabilistic decision has no valid FaultID")
                continue
            record = by_id.get(decision.fault_id)
            if record is None:
                errors.append("selected probabilistic decision FaultID is absent from records")
                continue
            if record.fault_class is not decision.selected_fault_class:
                errors.append("probabilistic decision class disagrees with FaultRecord")
            if record.target != decision.target:
                errors.append("probabilistic decision target disagrees with FaultRecord")
            if record.seed != decision.seed or record.draw != decision.draw:
                errors.append("probabilistic decision seed/draw disagrees with FaultRecord")
        if actual_ordinals != expected_ordinals[: len(actual_ordinals)]:
            errors.append("probabilistic decision ordinals are not contiguous from zero")
        return errors

    def _transformation_violations(self, records: list[FaultRecord]) -> list[str]:
        errors: list[str] = []
        produced_owner: dict[str, int] = {}
        cancelled_owner: dict[str, int] = {}
        graph: dict[str, set[str]] = {}

        for index, record in enumerate(records):
            for event_id in record.produced_event_ids:
                if event_id in produced_owner:
                    errors.append(f"EventID produced by multiple faults: {event_id}")
                produced_owner[event_id] = index
                graph.setdefault(record.target, set()).add(event_id)
            for event_id in record.cancelled_event_ids:
                if event_id in cancelled_owner:
                    errors.append(f"EventID cancelled by multiple faults: {event_id}")
                cancelled_owner[event_id] = index

        for event_id, cancel_index in cancelled_owner.items():
            produce_index = produced_owner.get(event_id)
            if produce_index is not None and produce_index >= cancel_index:
                errors.append(
                    f"fault transformation order invalid for EventID {event_id}: cancelled before production"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                errors.append(f"fault transformation graph contains cycle at EventID {node}")
                return
            visiting.add(node)
            for child in graph.get(node, ()):
                visit(child)
            visiting.remove(node)
            visited.add(node)

        for node in tuple(graph):
            visit(node)
        return errors

    def _runtime_violations(self, records: list[FaultRecord]) -> list[str]:
        errors: list[str] = []
        trace = {event.event_id: event for event in self.simulator.trace}
        pending = {event.event_id: event for event in self.simulator.pending_events}
        cancelled_by_fault = {
            event_id for record in records for event_id in record.cancelled_event_ids
        }
        trace_order = {event.event_id: index for index, event in enumerate(self.simulator.trace)}

        for record in records:
            prefix = f"FaultID {record.id}"
            for event_id in record.cancelled_event_ids:
                if event_id in trace or event_id in pending:
                    errors.append(f"{prefix}: cancelled EventID remained live or executed: {event_id}")
            for event_id in record.produced_event_ids:
                if event_id not in trace and event_id not in pending and event_id not in cancelled_by_fault:
                    errors.append(f"{prefix}: produced EventID missing from runtime: {event_id}")

            expected_kind = _EXPECTED_EVENT_KIND.get(record.fault_class)
            if expected_kind is not None and record.produced_event_ids:
                event_id = record.produced_event_ids[0]
                event = trace.get(event_id) or pending.get(event_id)
                if event is not None and event.kind is not expected_kind:
                    errors.append(
                        f"{prefix}: produced event kind {event.kind.value} != {expected_kind.value}"
                    )

            params = _parameter_dict(record) or {}
            if record.fault_class is FaultClass.DELIVERY_REORDER and record.produced_event_ids:
                anchor_id = params.get("anchor_event_id")
                replacement_id = record.produced_event_ids[0]
                if anchor_id in trace_order and replacement_id in trace_order:
                    if trace_order[replacement_id] <= trace_order[anchor_id]:
                        errors.append(f"{prefix}: reordered delivery executed before/at anchor")

            if self.resources is not None and record.fault_class is FaultClass.WORKER_FAILURE:
                event_id = record.produced_event_ids[0]
                if event_id in trace and record.target in self.resources.workers:
                    if self.resources.workers[record.target].status is not WorkerStatus.DOWN:
                        failure_index = trace_order[event_id]
                        recovered = any(
                            index > failure_index
                            and event.kind is EventKind.WORKER_RECOVERED
                            and dict(event.payload).get("worker_id") == record.target
                            for index, event in enumerate(self.simulator.trace)
                        )
                        if not recovered:
                            errors.append(f"{prefix}: worker failure did not produce DOWN state")

            if self.resources is not None and record.fault_class is FaultClass.REPLICA_LOSS:
                event_id = record.produced_event_ids[0]
                if event_id in trace and record.target in self.resources.replicas:
                    if self.resources.replicas[record.target].status is not ReplicaRuntimeStatus.LOST:
                        errors.append(f"{prefix}: replica loss did not produce LOST state")

            if self.resources is not None and record.fault_class is FaultClass.REPLICA_EVICTION:
                event_id = record.produced_event_ids[0]
                if event_id in trace and record.target in self.resources.replicas:
                    if self.resources.replicas[record.target].status is not ReplicaRuntimeStatus.EVICTED:
                        errors.append(f"{prefix}: replica eviction did not produce EVICTED state")
        return errors

    def _semantic_invariants(self, violations: list[str]) -> bool:
        if self.adapter is None:
            return True
        try:
            InvariantOracle(self.adapter.core).assert_all()
            return True
        except Exception as exc:
            violations.append(f"C1 invariant oracle failure: {type(exc).__name__}: {exc}")
            return False

    def _manifest_violations(self, records: list[FaultRecord]) -> list[str]:
        if self.manifest is None:
            return []
        errors: list[str] = []
        if self.manifest.seed != self.injector_seed:
            errors.append("campaign manifest seed differs from injector seed")
        if self.manifest.decisions != self.decisions:
            errors.append("campaign manifest decisions differ from oracle decisions")
        try:
            expected_schedule = tuple(
                FaultReplayEntry.from_record(record, ordinal)
                for ordinal, record in enumerate(records)
            )
        except Exception as exc:
            errors.append(f"cannot derive replay schedule from trusted records: {type(exc).__name__}: {exc}")
            return errors
        if self.manifest.schedule != expected_schedule:
            errors.append("campaign manifest schedule differs from FaultRecord sequence")
        return errors
