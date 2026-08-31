import pytest

from simulator import DiscreteEventSimulator
from simulator.policies import (
    INFORMATION_CONTRACTS,
    InformationField,
    PolicyID,
    PolicyObservation,
    RequestCentricPolicy,
    WorkerObservation,
    decide_placement,
    observe_resources,
    project_observation,
)
from simulator.resources import ResourceModel


def full_observation(workers):
    return PolicyObservation(
        request_id="r",
        workers=workers,
        attempt_id="a2",
        attempt_authority="CURRENT",
        session_id="s",
        session_preferred_location="w2",
        continuation_id="c2",
        continuation_ancestry=("c0", "c1"),
        state_candidate_key="prefix:abc",
        exact_state_id="x",
        state_locations=("w2",),
        state_provenance=(("continuation", "c1"),),
        producer_attempt_id="a1",
        binding_id="b2",
        binding_epoch=2,
        evidence_authority="AUTHORITATIVE",
        evidence_status="VALID",
        evidence_freshness=0.0,
    )


def test_information_contracts_cover_exact_b0_to_b4():
    assert set(INFORMATION_CONTRACTS) == set(PolicyID)
    assert INFORMATION_CONTRACTS[PolicyID.B0].fields == {
        InformationField.LOGICAL_REQUEST_ID,
        InformationField.RESOURCE_LOAD,
    }
    assert InformationField.SESSION_ID in INFORMATION_CONTRACTS[PolicyID.B2].fields
    assert InformationField.SESSION_PREFERRED_LOCATION in INFORMATION_CONTRACTS[PolicyID.B2].fields
    assert InformationField.EXACT_STATE_ID in INFORMATION_CONTRACTS[PolicyID.B3].fields
    assert InformationField.SESSION_PREFERRED_LOCATION not in INFORMATION_CONTRACTS[PolicyID.B3].fields
    assert InformationField.CONTINUATION_ANCESTRY not in INFORMATION_CONTRACTS[PolicyID.B3].fields
    assert InformationField.PRODUCER_ATTEMPT not in INFORMATION_CONTRACTS[PolicyID.B3].fields
    assert INFORMATION_CONTRACTS[PolicyID.B4].fields == frozenset(InformationField)


def test_information_contract_registry_cannot_be_mutated_after_import():
    with pytest.raises(TypeError):
        INFORMATION_CONTRACTS[PolicyID.B0] = INFORMATION_CONTRACTS[PolicyID.B4]


def test_set_like_policy_observations_require_canonical_ordering():
    workers = (WorkerObservation("w1", True, 1, 0, 0),)
    with pytest.raises(ValueError, match="continuation_ancestry must be unique and sorted"):
        PolicyObservation("r", workers, continuation_ancestry=("c1", "c0"))
    with pytest.raises(ValueError, match="state_locations must be unique and sorted"):
        PolicyObservation("r", workers, state_locations=("w2", "w1"))
    with pytest.raises(ValueError, match="state_provenance must have unique keys"):
        PolicyObservation(
            "r",
            workers,
            state_provenance=(("origin", "c1"), ("origin", "c2")),
        )


def test_b0_projection_structurally_hides_privileged_fields():
    workers = (WorkerObservation("w1", True, 1, 0, 0),)
    view = project_observation(full_observation(workers), PolicyID.B0)

    assert view.available_fields == {
        InformationField.LOGICAL_REQUEST_ID,
        InformationField.RESOURCE_LOAD,
    }
    assert view.value(InformationField.LOGICAL_REQUEST_ID) == "r"
    assert not hasattr(view, "session_id")
    assert not hasattr(view, "session_preferred_location")
    assert not hasattr(view, "continuation_id")
    assert not hasattr(view, "exact_state_id")
    with pytest.raises(PermissionError, match="does not allow session_id"):
        view.value(InformationField.SESSION_ID)
    with pytest.raises(PermissionError, match="does not allow session_preferred_location"):
        view.value(InformationField.SESSION_PREFERRED_LOCATION)


def test_b2_projection_contains_declared_session_affinity_without_causal_lineage():
    workers = (WorkerObservation("w1", True, 1, 0, 0),)
    view = project_observation(full_observation(workers), PolicyID.B2)

    assert view.value(InformationField.SESSION_ID) == "s"
    assert view.value(InformationField.SESSION_PREFERRED_LOCATION) == "w2"
    with pytest.raises(PermissionError, match="does not allow continuation_ancestry"):
        view.value(InformationField.CONTINUATION_ANCESTRY)


def test_resource_observation_is_sorted_and_reflects_c2_queue_state():
    sim = DiscreteEventSimulator()
    resources = ResourceModel(sim)
    resources.add_worker("w2", capacity=2)
    resources.add_worker("w1", capacity=1)
    resources.enqueue_task("w2", "t1", duration=10)
    resources.enqueue_task("w2", "t2", duration=10)
    resources.enqueue_task("w2", "t3", duration=10)
    sim.run(max_events=3)

    observations = observe_resources(resources)
    assert [item.worker_id for item in observations] == ["w1", "w2"]
    assert observations[0].normalized_load == 0.0
    assert observations[1].active_tasks == 2
    assert observations[1].queued_tasks == 1
    assert observations[1].normalized_load == 1.5


def test_b0_selects_least_normalized_load_with_deterministic_tie_break():
    workers = (
        WorkerObservation("w1", True, 1, 1, 0),
        WorkerObservation("w2", True, 2, 1, 0),
        WorkerObservation("w3", True, 4, 2, 0),
    )
    decision = decide_placement(RequestCentricPolicy(), PolicyObservation("r", workers))
    assert decision.worker_id == "w2"
    assert decision.ranked_worker_ids == ("w2", "w3", "w1")
    assert decision.reason == "LEAST_NORMALIZED_LOAD"


def test_b0_ignores_unavailable_worker_even_when_it_is_empty():
    workers = (
        WorkerObservation("down", False, 8, 0, 0),
        WorkerObservation("up", True, 1, 1, 0),
    )
    decision = decide_placement(RequestCentricPolicy(), PolicyObservation("r", workers))
    assert decision.worker_id == "up"
    assert decision.ranked_worker_ids == ("up",)


def test_b0_reports_explicit_no_available_worker():
    workers = (
        WorkerObservation("w1", False, 1, 0, 0),
        WorkerObservation("w2", False, 1, 0, 0),
    )
    decision = decide_placement(RequestCentricPolicy(), PolicyObservation("r", workers))
    assert decision.worker_id is None
    assert decision.ranked_worker_ids == ()
    assert decision.reason == "NO_AVAILABLE_WORKER"


def test_b0_decision_is_independent_of_privileged_continuity_metadata():
    workers = (
        WorkerObservation("w1", True, 1, 0, 1),
        WorkerObservation("w2", True, 1, 0, 0),
    )
    left = full_observation(workers)
    right = PolicyObservation(
        request_id="r",
        workers=workers,
        attempt_id="different-attempt",
        attempt_authority="SUPERSEDED",
        session_id="different-session",
        session_preferred_location="other-worker",
        continuation_id="different-continuation",
        continuation_ancestry=("other-root",),
        state_candidate_key="other-key",
        exact_state_id="other-state",
        state_locations=("w1",),
        state_provenance=(("attempt", "different-attempt"),),
        producer_attempt_id="different-attempt",
        binding_id="other-binding",
        binding_epoch=99,
        evidence_authority="ESTIMATED",
        evidence_status="AMBIGUOUS",
        evidence_freshness=999.0,
    )

    assert decide_placement(RequestCentricPolicy(), left) == decide_placement(
        RequestCentricPolicy(), right
    )
