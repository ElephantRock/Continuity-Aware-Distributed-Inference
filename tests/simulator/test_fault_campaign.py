from __future__ import annotations

import json

import pytest

from continuity import ContinuityCore
from continuity.entities import AttemptAuthority, ExecutionStatus
from simulator import ContinuityAdapter, DiscreteEventSimulator, EventKind
from simulator.fault_campaign import (
    FaultCampaignManifest,
    FaultReplayEntry,
    FaultReplayError,
    FaultScheduleReplayer,
    assert_paired_policy_reuse,
)
from simulator.fault_linkage import CrossLayerFaultInjector
from simulator.faults import FaultClass


def _scaffold_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    return core


def _probabilistic_manifest() -> FaultCampaignManifest:
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=4, event_id="e1")
    sim.schedule(EventKind.ATTEMPT_FAILED, at=5, event_id="e2")
    faults = CrossLayerFaultInjector(sim, seed=73)
    assert faults.probabilistic_delivery_fault(
        "e1", {FaultClass.DELIVERY_DUPLICATE: 1.0}, max_delay=2.0
    ) is not None
    assert faults.probabilistic_delivery_fault("e2", {}, max_delay=2.0) is None
    return FaultCampaignManifest.from_injector(
        faults,
        campaign_id="campaign-a",
        git_commit="0123456789abcdef",
        scenario_fingerprint="scenario-v1",
        generator="probabilistic-delivery-v1",
        fault_configuration={
            "duplicate_probability": 1.0,
            "drop_probability": 0.0,
            "max_delay": 2.0,
        },
    )


def test_manifest_round_trip_preserves_seed_decisions_schedule_and_fingerprints():
    manifest = _probabilistic_manifest()
    restored = FaultCampaignManifest.from_json(manifest.to_json())

    assert restored == manifest
    assert restored.configuration_fingerprint == manifest.configuration_fingerprint
    assert restored.schedule_fingerprint == manifest.schedule_fingerprint
    assert restored.manifest_fingerprint == manifest.manifest_fingerprint
    assert len(restored.decisions) == 2
    assert restored.decisions[0].selected_fault_class is FaultClass.DELIVERY_DUPLICATE
    assert restored.decisions[1].selected_fault_class is None
    assert len(restored.schedule) == 1


def test_manifest_rejects_tampered_configuration_fingerprint():
    manifest = _probabilistic_manifest()
    payload = json.loads(manifest.to_json())
    payload["fault_configuration"]["max_delay"] = 3.0

    with pytest.raises(ValueError, match="configuration fingerprint mismatch"):
        FaultCampaignManifest.from_dict(payload)


def test_manifest_rejects_nonfinite_json_before_fingerprint_validation():
    manifest = _probabilistic_manifest()
    poisoned = manifest.to_json().replace('"max_delay":2.0', '"max_delay":NaN')
    assert poisoned != manifest.to_json()
    with pytest.raises(ValueError, match="non-finite JSON"):
        FaultCampaignManifest.from_json(poisoned)


def _delivery_scenario():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=4, event_id="delay")
    sim.schedule(EventKind.ATTEMPT_FAILED, at=5, event_id="drop")
    sim.schedule(EventKind.OBSERVATION_CREATED, at=6, event_id="duplicate")
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=6.5, event_id="reorder")
    sim.schedule(EventKind.ATTEMPT_FAILED, at=7, event_id="anchor")
    return sim


def _delivery_campaign():
    sim = _delivery_scenario()
    faults = CrossLayerFaultInjector(sim, seed=19)
    faults.delay_delivery("delay", 2, fault_id="f-delay")
    faults.drop_delivery("drop", fault_id="f-drop")
    faults.duplicate_delivery("duplicate", delay=0.5, fault_id="f-dup")
    faults.reorder_after("reorder", "anchor", gap=1, fault_id="f-reorder")
    manifest = FaultCampaignManifest.from_injector(
        faults,
        campaign_id="delivery-campaign",
        git_commit="deadbeef",
        scenario_fingerprint="delivery-scenario-v1",
        generator="deterministic-delivery-v1",
        fault_configuration={"mode": "deterministic"},
    )
    sim.run()
    return sim, manifest


def test_delivery_fault_schedule_replay_reproduces_exact_fault_entries_and_trace():
    original_sim, manifest = _delivery_campaign()
    replay_sim = _delivery_scenario()
    replayed = FaultScheduleReplayer(replay_sim, manifest).replay()

    assert tuple(
        FaultReplayEntry.from_record(record, ordinal)
        for ordinal, record in enumerate(replayed.records)
    ) == manifest.schedule

    replay_sim.run()
    original_trace = tuple((event.event_id, event.kind, event.time) for event in original_sim.trace)
    replay_trace = tuple((event.event_id, event.kind, event.time) for event in replay_sim.trace)
    assert replay_trace == original_trace
    replayed.assert_ground_truth()


def test_replay_fails_closed_before_mutation_when_target_timing_differs():
    _, manifest = _delivery_campaign()
    replay_sim = DiscreteEventSimulator()
    replay_sim.schedule(EventKind.ATTEMPT_COMPLETED, at=4.25, event_id="delay")
    replay_sim.schedule(EventKind.ATTEMPT_FAILED, at=5, event_id="drop")
    replay_sim.schedule(EventKind.OBSERVATION_CREATED, at=6, event_id="duplicate")
    replay_sim.schedule(EventKind.ATTEMPT_COMPLETED, at=6.5, event_id="reorder")
    replay_sim.schedule(EventKind.ATTEMPT_FAILED, at=7, event_id="anchor")

    with pytest.raises(FaultReplayError, match="time mismatch"):
        FaultScheduleReplayer(replay_sim, manifest).replay()

    assert {event.event_id for event in replay_sim.pending_events} == {
        "delay",
        "drop",
        "duplicate",
        "reorder",
        "anchor",
    }
    assert not any(event.event_id.startswith("fault:") for event in replay_sim.pending_events)


def _cross_layer_campaign():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    sim.run(until=1)

    faults = CrossLayerFaultInjector(sim, seed=5)
    faults.inject_attempt_timeout(adapter, "r", "a1", "a2", at=2, fault_id="timeout")
    sim.run(until=2)
    assert core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED
    faults.inject_late_attempt_result(adapter, "a1", at=3, fault_id="late")

    manifest = FaultCampaignManifest.from_injector(
        faults,
        campaign_id="retry-campaign",
        git_commit="cafebabe",
        scenario_fingerprint="retry-scenario-v1",
        generator="retry-race-v1",
        fault_configuration={"timeout": 1.0, "late_delay": 1.0},
    )
    sim.run()
    return sim, core, manifest


def test_cross_layer_schedule_replay_preserves_timeout_and_late_result_semantics():
    original_sim, original_core, manifest = _cross_layer_campaign()

    replay_sim = DiscreteEventSimulator()
    replay_core = _scaffold_core()
    replay_adapter = ContinuityAdapter(replay_sim, replay_core)
    replay_adapter.schedule_request("r", "c", at=0)
    replay_adapter.schedule_attempt_start("r", "a1", at=1)

    replayed = FaultScheduleReplayer(
        replay_sim,
        manifest,
        adapter=replay_adapter,
    ).replay()
    replay_sim.run()

    assert tuple(
        FaultReplayEntry.from_record(record, ordinal)
        for ordinal, record in enumerate(replayed.records)
    ) == manifest.schedule
    assert replay_core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED
    assert replay_core.attempts["a1"].execution_status is ExecutionStatus.SUCCEEDED
    assert replay_core.requests["r"].current_attempt_id == "a2"
    assert replay_core.attempts["a2"].authority_status is AttemptAuthority.CURRENT
    assert tuple((e.kind, e.time) for e in replay_sim.trace) == tuple(
        (e.kind, e.time) for e in original_sim.trace
    )
    assert original_core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED


def test_cross_layer_replay_requires_adapter_and_fails_explicitly():
    _, _, manifest = _cross_layer_campaign()
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)

    with pytest.raises(FaultReplayError, match="ContinuityAdapter"):
        FaultScheduleReplayer(sim, manifest).replay()


def test_policy_bindings_reuse_exact_campaign_schedule_and_scenario():
    manifest = _probabilistic_manifest()
    b1 = manifest.bind_policy("B1-cache-aware")
    b4 = manifest.bind_policy("B4-continuity-aware")

    assert b1.policy_id != b4.policy_id
    assert b1.campaign_fingerprint == b4.campaign_fingerprint
    assert b1.schedule_fingerprint == b4.schedule_fingerprint
    assert assert_paired_policy_reuse(b1, b4) == manifest.schedule_fingerprint


def test_paired_policy_reuse_rejects_different_campaign_even_if_policy_ids_are_valid():
    first = _probabilistic_manifest()
    second = FaultCampaignManifest(
        campaign_id="campaign-b",
        git_commit=first.git_commit,
        scenario_fingerprint=first.scenario_fingerprint,
        seed=first.seed,
        generator=first.generator,
        fault_configuration=first.fault_configuration,
        decisions=first.decisions,
        schedule=first.schedule,
    )

    with pytest.raises(ValueError, match="same fault campaign"):
        assert_paired_policy_reuse(first.bind_policy("B1"), second.bind_policy("B4"))


def test_configuration_fingerprint_changes_with_seed_or_fault_configuration():
    base = _probabilistic_manifest()
    changed_seed = FaultCampaignManifest(
        campaign_id=base.campaign_id,
        git_commit=base.git_commit,
        scenario_fingerprint=base.scenario_fingerprint,
        seed=74,
        generator=base.generator,
        fault_configuration=base.fault_configuration,
        decisions=(),
        schedule=base.schedule,
    )
    changed_config = FaultCampaignManifest(
        campaign_id=base.campaign_id,
        git_commit=base.git_commit,
        scenario_fingerprint=base.scenario_fingerprint,
        seed=base.seed,
        generator=base.generator,
        fault_configuration=(
            ("drop_probability", 0.1),
            ("duplicate_probability", 0.9),
            ("max_delay", 2.0),
        ),
        decisions=base.decisions,
        schedule=base.schedule,
    )

    assert changed_seed.configuration_fingerprint != base.configuration_fingerprint
    assert changed_config.configuration_fingerprint != base.configuration_fingerprint
