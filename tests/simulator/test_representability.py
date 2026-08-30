import pytest

from continuity import ContinuityCore
from continuity.entities import Evidence, EvidenceAuthority, EvidenceStatus
from continuity.errors import InvalidTransition
from simulator.scenarios import (
    FAILURE_SCENARIOS,
    SCENARIO_BY_CATALOGUE_ID,
    SCENARIO_BY_NAME,
    WORKLOAD_SCENARIOS,
    assert_same_seed_replay,
    build_scenario_schedule,
)
from simulator import (
    ContinuityAdapter,
    DiscreteEventSimulator,
    assert_authoritative_equivalent,
)


def _semantic_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    return core


def _exact(
    core: ContinuityCore,
    evidence_id: str,
    attempt_id: str,
    *,
    observed_at: float = 10.0,
) -> None:
    core.record_evidence(
        Evidence(
            id=evidence_id,
            claim="terminal_attempt_success",
            source="c2.5-reference",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=observed_at,
            scope=frozenset({("attempt", attempt_id)}),
        )
    )


def _finalize(
    core: ContinuityCore,
    attempt_id: str,
    output_id: str,
    evidence_id: str,
) -> None:
    core.complete_attempt(attempt_id, succeeded=True)
    _exact(core, evidence_id, attempt_id)
    core.create_output(output_id, attempt_id, True, [evidence_id])
    core.finalize_request("r", output_id, now=10.0)


def _run_c2(catalogue_id: str, seed: int = 17) -> ContinuityCore:
    core = _semantic_core()
    simulator = DiscreteEventSimulator(seed=seed)
    ContinuityAdapter(simulator, core)
    build_scenario_schedule(catalogue_id, seed=seed).apply(simulator)
    simulator.run()
    return core


def test_registry_covers_exact_normalized_workload_and_failure_catalogues():
    assert {item.catalogue_id for item in WORKLOAD_SCENARIOS} == {
        f"W{index}" for index in range(1, 11)
    }
    assert {item.catalogue_id for item in FAILURE_SCENARIOS} == {
        f"FTR{index}" for index in range(1, 15)
    }
    assert len(SCENARIO_BY_NAME) == 24
    assert len(SCENARIO_BY_CATALOGUE_ID) == 24


def test_registry_uses_stable_semantic_names_not_catalogue_numbers_as_names():
    for scenario in (*WORKLOAD_SCENARIOS, *FAILURE_SCENARIOS):
        assert scenario.stable_name != scenario.catalogue_id.lower()
        assert scenario.stable_name == scenario.stable_name.lower()
        assert "_" not in scenario.stable_name
        assert scenario.stable_name in SCENARIO_BY_NAME


def test_every_schedule_has_monotonic_unique_events_and_a_stable_fingerprint():
    for scenario in (*WORKLOAD_SCENARIOS, *FAILURE_SCENARIOS):
        schedule = scenario.build(seed=23)
        assert schedule.events
        assert [event.time for event in schedule.events] == sorted(
            event.time for event in schedule.events
        )
        event_ids = [event.event_id for event in schedule.events]
        assert len(event_ids) == len(set(event_ids))
        assert len(schedule.fingerprint) == 64
        assert schedule.fingerprint == scenario.build(seed=23).fingerprint
        assert schedule.fingerprint != scenario.build(seed=24).fingerprint


def test_same_seed_replays_all_24_scenarios_exactly():
    for scenario in (*WORKLOAD_SCENARIOS, *FAILURE_SCENARIOS):
        schedule_fingerprint, trace_fingerprint = assert_same_seed_replay(
            scenario.stable_name, seed=991
        )
        assert len(schedule_fingerprint) == 64
        assert len(trace_fingerprint) == 64


def test_failure_registry_uses_current_ftr_numbers_with_legacy_c1_test_locations_explicit():
    assert "superseded_producer" in SCENARIO_BY_CATALOGUE_ID["FTR5"].c1_reference
    assert "concurrent_migration_candidate_fencing" in SCENARIO_BY_CATALOGUE_ID["FTR10"].c1_reference
    assert "test_ftr11_tool_wait_eviction" in SCENARIO_BY_CATALOGUE_ID["FTR13"].c1_reference
    assert "test_ftr12_abandoned_branch" in SCENARIO_BY_CATALOGUE_ID["FTR14"].c1_reference
    assert all(item.c1_reference for item in FAILURE_SCENARIOS)


def test_ftr1_c2_authoritative_outcome_matches_direct_c1_semantics():
    reference = _semantic_core()
    reference.create_request("r", "c")
    reference.start_attempt("a1", "r")
    reference.start_attempt("a2", "r")
    _finalize(reference, "a2", "o2", "e2")
    reference.complete_attempt("a1", succeeded=True)

    candidate = _run_c2("FTR1")
    outcome = assert_authoritative_equivalent(reference, candidate, "r")
    assert outcome.committed_attempt_id == "a2"
    assert outcome.authoritative_output_id == "o2"


def test_ftr2_c2_authoritative_outcome_matches_direct_c1_duplicate_finalization():
    reference = _semantic_core()
    reference.create_request("r", "c")
    reference.start_attempt("a1", "r")
    _finalize(reference, "a1", "o1", "e1")
    reference.finalize_request("r", "o1", now=10.0)

    candidate = _run_c2("FTR2")
    outcome = assert_authoritative_equivalent(reference, candidate, "r")
    assert outcome.committed_attempt_id == "a1"
    assert outcome.authoritative_output_id == "o1"


def test_ftr3_c2_authoritative_outcome_matches_direct_c1_reordered_retry_semantics():
    reference = _semantic_core()
    reference.create_request("r", "c")
    reference.start_attempt("a1", "r")
    reference.start_attempt("a2", "r")
    _finalize(reference, "a2", "o2", "e2")
    reference.complete_attempt("a1", succeeded=True)
    _exact(reference, "e1", "a1")
    reference.create_output("o1", "a1", True, ["e1"])
    with pytest.raises(InvalidTransition):
        reference.finalize_request("r", "o1", now=10.0)

    candidate = _run_c2("FTR3")
    outcome = assert_authoritative_equivalent(reference, candidate, "r")
    assert outcome.committed_attempt_id == "a2"
    assert outcome.authoritative_output_id == "o2"


def test_only_adapter_supported_ftrs_claim_executable_authoritative_equivalence():
    executable = {
        item.catalogue_id
        for item in FAILURE_SCENARIOS
        if item.executable_authoritative_equivalence
    }
    assert executable == {"FTR1", "FTR2", "FTR3"}
