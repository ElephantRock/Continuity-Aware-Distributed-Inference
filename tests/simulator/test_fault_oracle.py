from __future__ import annotations

from dataclasses import replace
import json

import pytest

from continuity.core import ContinuityCore
from simulator import ContinuityAdapter, DiscreteEventSimulator, EventKind, ResourceModel
from simulator.events import freeze_payload
from simulator.fault_campaign import FaultCampaignManifest
from simulator.fault_oracle import (
    FaultTrustOracle,
    fault_record_from_json,
    fault_record_to_dict,
    fault_record_to_json,
    fault_records_from_jsonl,
    fault_records_to_jsonl,
)
from simulator.fault_linkage import CrossLayerFaultInjector
from simulator.faults import FaultClass, FaultInjector, ProbabilisticFaultDecision
from simulator.resources import WorkerStatus


def _delay_case(*, run: bool = False):
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=2, event_id="e")
    injector = FaultInjector(sim, seed=17)
    record = injector.delay_delivery("e", 3, fault_id="delay")
    if run:
        sim.run()
    return sim, injector, record


def test_trust_oracle_accepts_valid_fault_before_and_after_delivery():
    sim, injector, _ = _delay_case(run=False)
    assert FaultTrustOracle.from_injector(injector).assert_all().ok
    sim.run()
    report = FaultTrustOracle.from_injector(injector).assert_all()
    assert report.ok
    assert report.checked_fault_ids == ("delay",)
    assert report.semantic_invariants_ok


def test_fault_record_json_and_jsonl_round_trip_are_strict_and_canonical():
    _, injector, record = _delay_case()
    text = fault_record_to_json(record)
    assert fault_record_from_json(text) == record
    jsonl = fault_records_to_jsonl(injector.records)
    assert fault_records_from_jsonl(jsonl) == injector.records
    assert jsonl == text + "\n"


def test_fault_record_json_rejects_nonfinite_numeric_and_unknown_fields():
    _, _, record = _delay_case()
    payload = fault_record_to_dict(record)
    payload["injection_time"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        fault_record_from_json(json.dumps(payload))

    payload = fault_record_to_dict(record)
    payload["unknown"] = 1
    with pytest.raises(ValueError, match="fields do not match"):
        fault_record_from_json(json.dumps(payload))


def test_malformed_record_is_reported_not_crashable_by_bad_duration_or_unhashable_id():
    sim, injector, record = _delay_case()
    bad_duration = replace(record, duration="not-a-number")
    bad_id = replace(record, id=[])
    report = FaultTrustOracle(
        sim,
        (bad_duration, bad_id),
        injector_seed=injector.seed,
    ).inspect()
    assert not report.ok
    assert any("duration must be finite" in item for item in report.violations)
    assert any("FaultID must be a non-empty string" in item for item in report.violations)


def test_class_contract_metadata_tampering_is_detected():
    sim, injector, record = _delay_case()
    forged = replace(
        record,
        ground_truth_effect="different effect",
        expected_invariant_pressure=("different pressure",),
        expected_safe_outcomes=("different outcome",),
    )
    report = FaultTrustOracle(sim, (forged,), injector_seed=injector.seed).inspect()
    assert any("ground_truth_effect disagrees" in item for item in report.violations)
    assert any("expected_invariant_pressure disagrees" in item for item in report.violations)
    assert any("expected_safe_outcomes disagree" in item for item in report.violations)


def test_missing_runtime_produced_event_is_detected():
    sim, injector, record = _delay_case()
    forged = replace(
        record,
        produced_event_ids=("missing",),
        parameters=freeze_payload(
            {
                "original_time": 2.0,
                "replacement_time": 5.0,
                "replacement_event_id": "missing",
            }
        ),
    )
    report = FaultTrustOracle(sim, (forged,), injector_seed=injector.seed).inspect()
    assert any("produced EventID missing" in item for item in report.violations)


def test_runtime_event_kind_and_target_payload_are_independently_checked():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    resources.add_worker("w2")
    injector = FaultInjector(sim, resources)
    valid = injector.fail_worker("w1", fault_id="worker")

    sim.schedule(
        EventKind.ATTEMPT_FAILED,
        at=0,
        event_id="wrong-kind",
        payload={"worker_id": "w1"},
    )
    wrong_kind = replace(
        valid,
        produced_event_ids=("wrong-kind",),
        parameters=freeze_payload({"event_id": "wrong-kind"}),
    )
    report = FaultTrustOracle(
        sim,
        (wrong_kind,),
        injector_seed=injector.seed,
        resources=resources,
    ).inspect()
    assert any("produced event kind" in item for item in report.violations)

    sim.schedule(
        EventKind.WORKER_FAILED,
        at=0,
        event_id="wrong-target",
        payload={"worker_id": "w2"},
    )
    wrong_target = replace(
        valid,
        produced_event_ids=("wrong-target",),
        parameters=freeze_payload({"event_id": "wrong-target"}),
    )
    report = FaultTrustOracle(
        sim,
        (wrong_target,),
        injector_seed=injector.seed,
        resources=resources,
    ).inspect()
    assert any("target payload disagrees" in item for item in report.violations)


def test_composed_delay_then_drop_is_trusted_but_repeated_cancellation_is_not():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    injector = FaultInjector(sim)
    delayed = injector.delay_delivery("e", 2, fault_id="delay")
    dropped = injector.drop_delivery(delayed.produced_event_ids[0], fault_id="drop")
    sim.run()
    assert FaultTrustOracle.from_injector(injector).assert_all().ok

    duplicate_cancel = replace(dropped, id="drop-again")
    report = FaultTrustOracle(
        sim,
        (delayed, dropped, duplicate_cancel),
        injector_seed=injector.seed,
    ).inspect()
    assert any("cancelled by multiple faults" in item for item in report.violations)


def test_transformation_cycle_is_detected_even_when_runtime_ids_are_fully_consumed():
    _, _, template = _delay_case()
    sim = DiscreteEventSimulator()
    sim.run(until=10)
    first = replace(
        template,
        id="f1",
        target="a",
        injection_time=0.0,
        duration=1.0,
        parameters=freeze_payload(
            {
                "original_time": 0.0,
                "replacement_time": 1.0,
                "replacement_event_id": "b",
            }
        ),
        produced_event_ids=("b",),
        cancelled_event_ids=("a",),
    )
    second = replace(
        template,
        id="f2",
        target="b",
        injection_time=1.0,
        duration=1.0,
        parameters=freeze_payload(
            {
                "original_time": 1.0,
                "replacement_time": 2.0,
                "replacement_event_id": "a",
            }
        ),
        produced_event_ids=("a",),
        cancelled_event_ids=("b",),
    )
    report = FaultTrustOracle(sim, (first, second), injector_seed=17).inspect()
    assert any("contains cycle" in item for item in report.violations)


def test_probabilistic_decision_must_match_record_seed_draw_class_target_and_faultid():
    sim = DiscreteEventSimulator()
    sim.schedule(EventKind.ATTEMPT_COMPLETED, at=1, event_id="e")
    injector = FaultInjector(sim, seed=91)
    record = injector.probabilistic_delivery_fault(
        "e",
        {FaultClass.DELIVERY_DROP: 1.0},
    )
    assert record is not None
    decision = injector.decisions[0]
    forged = replace(decision, target="different")
    report = FaultTrustOracle(
        sim,
        injector.records,
        injector_seed=injector.seed,
        decisions=(forged,),
    ).inspect()
    assert any("target disagrees" in item for item in report.violations)


def test_no_fault_probabilistic_decision_cannot_name_faultid():
    sim = DiscreteEventSimulator()
    decision = ProbabilisticFaultDecision(7, 0, "e", 0.5, None, "ghost")
    report = FaultTrustOracle(
        sim,
        (),
        injector_seed=7,
        decisions=(decision,),
    ).inspect()
    assert any(
        "no-fault probabilistic decision names a FaultID" in item
        for item in report.violations
    )


def test_campaign_manifest_must_match_trusted_record_schedule_and_decisions():
    _, injector, _ = _delay_case()
    manifest = FaultCampaignManifest.from_injector(
        injector,
        campaign_id="campaign",
        git_commit="revision",
        scenario_fingerprint="scenario",
        generator="test",
    )
    report = FaultTrustOracle.from_injector(
        injector,
        manifest=manifest,
    ).assert_all()
    assert report.manifest_fingerprint == manifest.manifest_fingerprint

    forged_manifest = replace(manifest, schedule=())
    report = FaultTrustOracle.from_injector(
        injector,
        manifest=forged_manifest,
    ).inspect()
    assert any("schedule differs" in item for item in report.violations)


def test_worker_failure_followed_by_legitimate_recovery_remains_trusted():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    injector = FaultInjector(sim, resources)
    injector.fail_worker("w1", fault_id="down")
    resources.recover_worker("w1")
    sim.run()
    assert resources.workers["w1"].status is WorkerStatus.UP
    assert FaultTrustOracle.from_injector(injector).assert_all().ok


def test_c1_invariant_failure_is_reported_by_independent_trust_oracle(monkeypatch):
    sim = DiscreteEventSimulator()
    adapter = ContinuityAdapter(sim, ContinuityCore())
    injector = FaultInjector(sim)

    def fail(_self):
        raise AssertionError("forced semantic invariant failure")

    monkeypatch.setattr("simulator.fault_oracle.InvariantOracle.assert_all", fail)
    report = FaultTrustOracle.from_injector(
        injector,
        adapter=adapter,
    ).inspect()
    assert not report.semantic_invariants_ok
    assert any("C1 invariant oracle failure" in item for item in report.violations)



def test_delivery_time_parameter_wrong_scalar_type_is_rejected_by_schema():
    sim, injector, record = _delay_case()
    forged = replace(
        record,
        parameters=freeze_payload(
            {
                "original_time": "not-a-time",
                "replacement_time": 5.0,
                "replacement_event_id": record.produced_event_ids[0],
            }
        ),
    )
    report = FaultTrustOracle(sim, (forged,), injector_seed=injector.seed).inspect()
    assert any(
        "parameter original_time must be finite and non-negative" in item
        for item in report.violations
    )


def test_physical_resource_fault_duration_must_be_zero():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w1")
    injector = FaultInjector(sim, resources)
    record = injector.fail_worker("w1", fault_id="down")
    forged = replace(record, duration=1.0)
    report = FaultTrustOracle(
        sim,
        (forged,),
        injector_seed=injector.seed,
        resources=resources,
    ).inspect()
    assert any(
        "physical resource fault duration must be zero" in item
        for item in report.violations
    )


def test_cross_layer_identity_parameters_must_be_nonempty():
    sim = DiscreteEventSimulator()
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    sim.run(until=1)
    injector = CrossLayerFaultInjector(sim)
    record = injector.inject_attempt_timeout(
        adapter,
        "r",
        "a1",
        "a2",
        at=2,
        fault_id="timeout",
    )
    forged = replace(
        record,
        parameters=freeze_payload(
            {
                "request_id": "",
                "retry_attempt_id": "a2",
                "event_id": record.produced_event_ids[0],
            }
        ),
    )
    report = FaultTrustOracle(
        sim,
        (forged,),
        injector_seed=injector.seed,
        adapter=adapter,
    ).inspect()
    assert any(
        "parameter request_id must be a non-empty string" in item
        for item in report.violations
    )



def test_malformed_unhashable_produced_or_cancelled_event_ids_are_reported_not_raised():
    sim, injector, record = _delay_case()
    malformed_produced = replace(record, produced_event_ids=([],))
    malformed_cancelled = replace(record, cancelled_event_ids=([],))
    report = FaultTrustOracle(
        sim,
        (malformed_produced, malformed_cancelled),
        injector_seed=injector.seed,
    ).inspect()
    assert any(
        "produced_event_ids must be tuple[str, ...]" in item
        for item in report.violations
    )
    assert any(
        "cancelled_event_ids must be tuple[str, ...]" in item
        for item in report.violations
    )
