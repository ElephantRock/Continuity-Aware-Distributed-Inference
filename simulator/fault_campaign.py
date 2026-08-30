from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping, Optional

from .engine import DiscreteEventSimulator
from .events import freeze_payload
from .fault_linkage import CrossLayerFaultInjector
from .faults import FaultClass, FaultInjector, FaultRecord, ProbabilisticFaultDecision
from .resources import ResourceModel
from .semantic_adapter import ContinuityAdapter


FAULT_CAMPAIGN_SCHEMA = "cadi.fault-campaign.v1"
POLICY_FAULT_BINDING_SCHEMA = "cadi.policy-fault-binding.v1"


class FaultReplayError(RuntimeError):
    """Raised when a recorded policy-neutral fault schedule cannot be replayed exactly."""


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


def _require_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _finite_nonnegative(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return numeric


def _scalar(value: Any, name: str) -> Any:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return value
    raise TypeError(f"{name} must be a JSON scalar")


def _freeze_scalar_mapping(values: Mapping[str, Any] | None, name: str) -> tuple[tuple[str, Any], ...]:
    if values is None:
        return ()
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be a mapping")
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{name} keys must be non-empty strings")
        normalized[key] = _scalar(value, f"{name}.{key}")
    return freeze_payload(normalized)


def _mapping_to_dict(values: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    return {key: value for key, value in values}


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _same_time(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)


@dataclass(frozen=True, slots=True)
class FaultReplayEntry:
    ordinal: int
    fault_id: str
    fault_class: FaultClass
    injection_time: float
    target: str
    duration: float
    parameters: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("fault replay ordinal must be a non-negative integer")
        _require_id(self.fault_id, "fault_id")
        if not isinstance(self.fault_class, FaultClass):
            raise TypeError("fault_class must be FaultClass")
        _finite_nonnegative(self.injection_time, "injection_time")
        _require_id(self.target, "target")
        _finite_nonnegative(self.duration, "duration")
        if not isinstance(self.parameters, tuple):
            raise TypeError("parameters must be frozen")
        rebuilt = _freeze_scalar_mapping(dict(self.parameters), "parameters")
        if rebuilt != self.parameters:
            raise ValueError("parameters must be canonical frozen scalar mapping")

    @classmethod
    def from_record(cls, record: FaultRecord, ordinal: int) -> "FaultReplayEntry":
        if not isinstance(record, FaultRecord):
            raise TypeError("record must be FaultRecord")
        params = dict(record.parameters)
        replay: dict[str, Any] = {}

        if record.fault_class in {
            FaultClass.DELIVERY_DELAY,
            FaultClass.DELIVERY_DROP,
            FaultClass.DELIVERY_DUPLICATE,
        }:
            replay["target_time"] = params["original_time"]
        elif record.fault_class is FaultClass.DELIVERY_REORDER:
            replay.update(
                {
                    "target_time": params["original_time"],
                    "anchor_event_id": params["anchor_event_id"],
                    "anchor_time": params["anchor_time"],
                    "gap": max(0.0, float(params["replacement_time"]) - float(params["anchor_time"])),
                }
            )
        elif record.fault_class is FaultClass.ATTEMPT_TIMEOUT:
            replay.update(
                {
                    "request_id": params["request_id"],
                    "retry_attempt_id": params["retry_attempt_id"],
                }
            )
        elif record.fault_class is FaultClass.STALE_ATTEMPT_OBSERVATION:
            replay.update(
                {
                    "request_id": params["request_id"],
                    "evidence_id": params["evidence_id"],
                    "output_id": params["output_id"],
                    "observed_at": params["observed_at"],
                }
            )

        return cls(
            ordinal=ordinal,
            fault_id=record.id,
            fault_class=record.fault_class,
            injection_time=float(record.injection_time),
            target=record.target,
            duration=float(record.duration),
            parameters=_freeze_scalar_mapping(replay, "parameters"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "fault_id": self.fault_id,
            "fault_class": self.fault_class.value,
            "injection_time": self.injection_time,
            "target": self.target,
            "duration": self.duration,
            "parameters": _mapping_to_dict(self.parameters),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "FaultReplayEntry":
        if not isinstance(value, dict):
            raise ValueError("fault replay entry must be an object")
        expected = {
            "ordinal",
            "fault_id",
            "fault_class",
            "injection_time",
            "target",
            "duration",
            "parameters",
        }
        if set(value) != expected:
            raise ValueError("fault replay entry fields do not match schema")
        try:
            fault_class = FaultClass(value["fault_class"])
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown fault replay class") from exc
        return cls(
            ordinal=value["ordinal"],
            fault_id=value["fault_id"],
            fault_class=fault_class,
            injection_time=value["injection_time"],
            target=value["target"],
            duration=value["duration"],
            parameters=_freeze_scalar_mapping(value["parameters"], "parameters"),
        )


def _decision_to_dict(decision: ProbabilisticFaultDecision) -> dict[str, Any]:
    return {
        "seed": decision.seed,
        "ordinal": decision.ordinal,
        "target": decision.target,
        "draw": decision.draw,
        "selected_fault_class": (
            None if decision.selected_fault_class is None else decision.selected_fault_class.value
        ),
        "fault_id": decision.fault_id,
    }


def _decision_from_dict(value: Any) -> ProbabilisticFaultDecision:
    if not isinstance(value, dict):
        raise ValueError("probabilistic decision must be an object")
    expected = {"seed", "ordinal", "target", "draw", "selected_fault_class", "fault_id"}
    if set(value) != expected:
        raise ValueError("probabilistic decision fields do not match schema")
    if not isinstance(value["seed"], int) or isinstance(value["seed"], bool):
        raise ValueError("probabilistic decision seed must be an integer")
    if not isinstance(value["ordinal"], int) or isinstance(value["ordinal"], bool) or value["ordinal"] < 0:
        raise ValueError("probabilistic decision ordinal must be non-negative integer")
    _require_id(value["target"], "probabilistic decision target")
    draw = _finite_nonnegative(value["draw"], "probabilistic decision draw")
    if draw > 1.0:
        raise ValueError("probabilistic decision draw must be within [0, 1]")
    selected = value["selected_fault_class"]
    if selected is None:
        fault_class = None
    else:
        try:
            fault_class = FaultClass(selected)
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown probabilistic decision fault class") from exc
    fault_id = value["fault_id"]
    if fault_id is not None:
        _require_id(fault_id, "probabilistic decision fault_id")
    return ProbabilisticFaultDecision(
        seed=value["seed"],
        ordinal=value["ordinal"],
        target=value["target"],
        draw=draw,
        selected_fault_class=fault_class,
        fault_id=fault_id,
    )


@dataclass(frozen=True, slots=True)
class PolicyFaultBinding:
    policy_id: str
    campaign_fingerprint: str
    schedule_fingerprint: str
    scenario_fingerprint: str

    def __post_init__(self) -> None:
        _require_id(self.policy_id, "policy_id")
        for value, name in (
            (self.campaign_fingerprint, "campaign_fingerprint"),
            (self.schedule_fingerprint, "schedule_fingerprint"),
            (self.scenario_fingerprint, "scenario_fingerprint"),
        ):
            _require_id(value, name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": POLICY_FAULT_BINDING_SCHEMA,
            "policy_id": self.policy_id,
            "campaign_fingerprint": self.campaign_fingerprint,
            "schedule_fingerprint": self.schedule_fingerprint,
            "scenario_fingerprint": self.scenario_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class FaultCampaignManifest:
    campaign_id: str
    git_commit: str
    scenario_fingerprint: str
    seed: int
    generator: str
    fault_configuration: tuple[tuple[str, Any], ...]
    decisions: tuple[ProbabilisticFaultDecision, ...]
    schedule: tuple[FaultReplayEntry, ...]

    def __post_init__(self) -> None:
        _require_id(self.campaign_id, "campaign_id")
        _require_id(self.git_commit, "git_commit")
        _require_id(self.scenario_fingerprint, "scenario_fingerprint")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError("seed must be an integer")
        _require_id(self.generator, "generator")
        if not isinstance(self.fault_configuration, tuple):
            raise TypeError("fault_configuration must be frozen")
        rebuilt = _freeze_scalar_mapping(dict(self.fault_configuration), "fault_configuration")
        if rebuilt != self.fault_configuration:
            raise ValueError("fault_configuration must be canonical frozen scalar mapping")
        if not isinstance(self.decisions, tuple) or not all(
            isinstance(item, ProbabilisticFaultDecision) for item in self.decisions
        ):
            raise TypeError("decisions must be tuple[ProbabilisticFaultDecision, ...]")
        if not isinstance(self.schedule, tuple) or not all(
            isinstance(item, FaultReplayEntry) for item in self.schedule
        ):
            raise TypeError("schedule must be tuple[FaultReplayEntry, ...]")

        if [item.ordinal for item in self.decisions] != list(range(len(self.decisions))):
            raise ValueError("probabilistic decision ordinals must be contiguous from zero")
        if any(item.seed != self.seed for item in self.decisions):
            raise ValueError("probabilistic decision seed must match campaign seed")
        if [item.ordinal for item in self.schedule] != list(range(len(self.schedule))):
            raise ValueError("fault replay ordinals must be contiguous from zero")
        previous = -1.0
        seen_fault_ids: set[str] = set()
        for item in self.schedule:
            if item.injection_time < previous:
                raise ValueError("fault schedule must be ordered by non-decreasing injection time")
            previous = item.injection_time
            if item.fault_id in seen_fault_ids:
                raise ValueError("fault schedule reuses a FaultID")
            seen_fault_ids.add(item.fault_id)

        schedule_by_id = {item.fault_id: item for item in self.schedule}
        for decision in self.decisions:
            if decision.selected_fault_class is None:
                if decision.fault_id is not None:
                    raise ValueError("no-fault probabilistic decision must not name a FaultID")
                continue
            if decision.fault_id is None:
                raise ValueError("selected probabilistic fault decision must name a FaultID")
            realized = schedule_by_id.get(decision.fault_id)
            if realized is None:
                raise ValueError("probabilistic decision FaultID is absent from realized schedule")
            if realized.fault_class is not decision.selected_fault_class:
                raise ValueError("probabilistic decision class disagrees with realized schedule")
            if realized.target != decision.target:
                raise ValueError("probabilistic decision target disagrees with realized schedule")

    @classmethod
    def from_injector(
        cls,
        injector: FaultInjector,
        *,
        campaign_id: str,
        git_commit: str,
        scenario_fingerprint: str,
        generator: str,
        fault_configuration: Mapping[str, Any] | None = None,
    ) -> "FaultCampaignManifest":
        if not isinstance(injector, FaultInjector):
            raise TypeError("injector must be FaultInjector")
        schedule = tuple(
            FaultReplayEntry.from_record(record, ordinal)
            for ordinal, record in enumerate(injector.records)
        )
        return cls(
            campaign_id=campaign_id,
            git_commit=git_commit,
            scenario_fingerprint=scenario_fingerprint,
            seed=injector.seed,
            generator=generator,
            fault_configuration=_freeze_scalar_mapping(
                fault_configuration, "fault_configuration"
            ),
            decisions=injector.decisions,
            schedule=schedule,
        )

    @property
    def configuration_fingerprint(self) -> str:
        return _fingerprint(
            {
                "seed": self.seed,
                "generator": self.generator,
                "fault_configuration": _mapping_to_dict(self.fault_configuration),
            }
        )

    @property
    def schedule_fingerprint(self) -> str:
        return _fingerprint([entry.to_dict() for entry in self.schedule])

    @property
    def manifest_fingerprint(self) -> str:
        return _fingerprint(self._payload(include_manifest_fingerprint=False))

    def bind_policy(self, policy_id: str) -> PolicyFaultBinding:
        return PolicyFaultBinding(
            policy_id=policy_id,
            campaign_fingerprint=self.manifest_fingerprint,
            schedule_fingerprint=self.schedule_fingerprint,
            scenario_fingerprint=self.scenario_fingerprint,
        )

    def _payload(self, *, include_manifest_fingerprint: bool) -> dict[str, Any]:
        payload = {
            "schema": FAULT_CAMPAIGN_SCHEMA,
            "campaign_id": self.campaign_id,
            "git_commit": self.git_commit,
            "scenario_fingerprint": self.scenario_fingerprint,
            "seed": self.seed,
            "generator": self.generator,
            "fault_configuration": _mapping_to_dict(self.fault_configuration),
            "configuration_fingerprint": self.configuration_fingerprint,
            "decisions": [_decision_to_dict(item) for item in self.decisions],
            "schedule": [entry.to_dict() for entry in self.schedule],
            "schedule_fingerprint": self.schedule_fingerprint,
        }
        if include_manifest_fingerprint:
            payload["manifest_fingerprint"] = self.manifest_fingerprint
        return payload

    def to_dict(self) -> dict[str, Any]:
        return self._payload(include_manifest_fingerprint=True)

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, text: str) -> "FaultCampaignManifest":
        return cls.from_dict(_strict_json_loads(text))

    @classmethod
    def from_dict(cls, value: Any) -> "FaultCampaignManifest":
        if not isinstance(value, dict) or value.get("schema") != FAULT_CAMPAIGN_SCHEMA:
            raise ValueError("unsupported fault campaign schema")
        expected = {
            "schema",
            "campaign_id",
            "git_commit",
            "scenario_fingerprint",
            "seed",
            "generator",
            "fault_configuration",
            "configuration_fingerprint",
            "decisions",
            "schedule",
            "schedule_fingerprint",
            "manifest_fingerprint",
        }
        if set(value) != expected:
            raise ValueError("fault campaign fields do not match schema")
        decisions = tuple(_decision_from_dict(item) for item in value["decisions"])
        schedule = tuple(FaultReplayEntry.from_dict(item) for item in value["schedule"])
        manifest = cls(
            campaign_id=value["campaign_id"],
            git_commit=value["git_commit"],
            scenario_fingerprint=value["scenario_fingerprint"],
            seed=value["seed"],
            generator=value["generator"],
            fault_configuration=_freeze_scalar_mapping(
                value["fault_configuration"], "fault_configuration"
            ),
            decisions=decisions,
            schedule=schedule,
        )
        if value["configuration_fingerprint"] != manifest.configuration_fingerprint:
            raise ValueError("fault campaign configuration fingerprint mismatch")
        if value["schedule_fingerprint"] != manifest.schedule_fingerprint:
            raise ValueError("fault campaign schedule fingerprint mismatch")
        if value["manifest_fingerprint"] != manifest.manifest_fingerprint:
            raise ValueError("fault campaign manifest fingerprint mismatch")
        return manifest


class FaultScheduleReplayer:
    """Replay an immutable policy-neutral fault schedule against a prepared scenario.

    The caller prepares the same scenario/workload topology and stable EventIDs first.
    Replay advances the simulator to each injection boundary and applies exactly the
    recorded fault. If a target identity, time, resource, or semantic precondition no
    longer matches, replay fails explicitly rather than adapting the schedule.
    """

    def __init__(
        self,
        simulator: DiscreteEventSimulator,
        manifest: FaultCampaignManifest,
        *,
        git_commit: str,
        scenario_fingerprint: str,
        resources: ResourceModel | None = None,
        adapter: ContinuityAdapter | None = None,
    ) -> None:
        if not isinstance(simulator, DiscreteEventSimulator):
            raise TypeError("simulator must be DiscreteEventSimulator")
        if not isinstance(manifest, FaultCampaignManifest):
            raise TypeError("manifest must be FaultCampaignManifest")
        _require_id(git_commit, "git_commit")
        _require_id(scenario_fingerprint, "scenario_fingerprint")
        if git_commit != manifest.git_commit:
            raise FaultReplayError("replay git commit does not match campaign manifest")
        if scenario_fingerprint != manifest.scenario_fingerprint:
            raise FaultReplayError("replay scenario fingerprint does not match campaign manifest")
        if resources is not None and resources.simulator is not simulator:
            raise ValueError("resources must reference replay simulator")
        if adapter is not None and adapter.simulator is not simulator:
            raise ValueError("adapter must reference replay simulator")
        self.simulator = simulator
        self.manifest = manifest
        self.git_commit = git_commit
        self.scenario_fingerprint = scenario_fingerprint
        self.resources = resources
        self.adapter = adapter

    def replay(self) -> CrossLayerFaultInjector:
        injector = CrossLayerFaultInjector(
            self.simulator,
            self.resources,
            seed=self.manifest.seed,
        )
        for entry in self.manifest.schedule:
            if self.simulator.now > entry.injection_time and not _same_time(
                self.simulator.now, entry.injection_time
            ):
                raise FaultReplayError(
                    f"replay passed injection time for FaultID {entry.fault_id}"
                )
            if self.simulator.now < entry.injection_time and not _same_time(
                self.simulator.now, entry.injection_time
            ):
                self.simulator.run(until=entry.injection_time)
            try:
                fresh = self._apply(injector, entry)
            except Exception as exc:
                raise FaultReplayError(
                    f"cannot replay FaultID {entry.fault_id}: {exc}"
                ) from exc
            replayed = FaultReplayEntry.from_record(fresh, entry.ordinal)
            if replayed != entry:
                raise FaultReplayError(
                    f"replayed FaultID {entry.fault_id} diverged from manifest"
                )
        return injector

    def _pending_time(self, event_id: str) -> float:
        for event in self.simulator.pending_events:
            if event.event_id == event_id:
                return float(event.time)
        raise FaultReplayError(f"manifest target event is not pending: {event_id}")

    def _require_event_time(self, event_id: str, expected: float, name: str) -> None:
        actual = self._pending_time(event_id)
        if not _same_time(actual, expected):
            raise FaultReplayError(
                f"{name} time mismatch for {event_id}: expected {expected}, observed {actual}"
            )

    def _adapter(self) -> ContinuityAdapter:
        if self.adapter is None:
            raise FaultReplayError("cross-layer replay requires ContinuityAdapter")
        return self.adapter

    def _apply(
        self,
        injector: CrossLayerFaultInjector,
        entry: FaultReplayEntry,
    ) -> FaultRecord:
        params = dict(entry.parameters)
        fault_class = entry.fault_class

        if fault_class is FaultClass.DELIVERY_DELAY:
            self._require_event_time(entry.target, params["target_time"], "target")
            return injector.delay_delivery(
                entry.target, entry.duration, fault_id=entry.fault_id
            )
        if fault_class is FaultClass.DELIVERY_DROP:
            self._require_event_time(entry.target, params["target_time"], "target")
            return injector.drop_delivery(entry.target, fault_id=entry.fault_id)
        if fault_class is FaultClass.DELIVERY_DUPLICATE:
            self._require_event_time(entry.target, params["target_time"], "target")
            return injector.duplicate_delivery(
                entry.target, delay=entry.duration, fault_id=entry.fault_id
            )
        if fault_class is FaultClass.DELIVERY_REORDER:
            self._require_event_time(entry.target, params["target_time"], "target")
            self._require_event_time(
                params["anchor_event_id"], params["anchor_time"], "anchor"
            )
            return injector.reorder_after(
                entry.target,
                params["anchor_event_id"],
                gap=params["gap"],
                fault_id=entry.fault_id,
            )
        if fault_class is FaultClass.WORKER_FAILURE:
            return injector.fail_worker(entry.target, fault_id=entry.fault_id)
        if fault_class is FaultClass.REPLICA_LOSS:
            return injector.lose_replica(entry.target, fault_id=entry.fault_id)
        if fault_class is FaultClass.REPLICA_EVICTION:
            return injector.evict_replica(entry.target, fault_id=entry.fault_id)
        if fault_class is FaultClass.ATTEMPT_TIMEOUT:
            return injector.inject_attempt_timeout(
                self._adapter(),
                params["request_id"],
                entry.target,
                params["retry_attempt_id"],
                at=self.simulator.now + entry.duration,
                fault_id=entry.fault_id,
            )
        if fault_class is FaultClass.LATE_ATTEMPT_RESULT:
            return injector.inject_late_attempt_result(
                self._adapter(),
                entry.target,
                at=self.simulator.now + entry.duration,
                fault_id=entry.fault_id,
            )
        if fault_class is FaultClass.STALE_ATTEMPT_OBSERVATION:
            return injector.inject_stale_attempt_observation(
                self._adapter(),
                params["request_id"],
                entry.target,
                params["evidence_id"],
                params["output_id"],
                at=self.simulator.now + entry.duration,
                observed_at=params["observed_at"],
                fault_id=entry.fault_id,
            )
        raise FaultReplayError(f"unsupported replay FaultClass: {fault_class.value}")


def assert_paired_policy_reuse(*bindings: PolicyFaultBinding) -> str:
    if len(bindings) < 2:
        raise ValueError("paired policy reuse requires at least two bindings")
    if not all(isinstance(item, PolicyFaultBinding) for item in bindings):
        raise TypeError("paired policy reuse requires PolicyFaultBinding values")
    campaign = {item.campaign_fingerprint for item in bindings}
    schedule = {item.schedule_fingerprint for item in bindings}
    scenario = {item.scenario_fingerprint for item in bindings}
    if len(campaign) != 1 or len(schedule) != 1 or len(scenario) != 1:
        raise ValueError("policy runs do not share the same fault campaign/schedule/scenario")
    return bindings[0].schedule_fingerprint
