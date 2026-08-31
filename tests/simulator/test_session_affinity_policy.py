import pytest

from simulator import (
    CacheAwarePolicy,
    InformationField,
    PolicyID,
    PolicyObservation,
    SessionAffinityPolicy,
    WorkerObservation,
    decide_placement,
    project_observation,
)


def workers():
    return (
        WorkerObservation("w1", True, 1, 1, 2),
        WorkerObservation("w2", True, 2, 1, 0),
        WorkerObservation("w3", True, 4, 0, 0),
    )


def test_b2_projection_exposes_session_affinity_without_causal_lineage():
    observation = PolicyObservation(
        "r",
        workers(),
        session_id="s",
        session_preferred_location="w1",
        continuation_id="c2",
        continuation_ancestry=("c0", "c1"),
        state_candidate_key="prefix:abc",
        exact_state_id="x",
        state_locations=("w2",),
        state_provenance=(("continuation", "c1"),),
        producer_attempt_id="a1",
        binding_id="b1",
        binding_epoch=1,
        evidence_authority="AUTHORITATIVE",
        evidence_status="VALID",
        evidence_freshness=0.0,
    )
    view = project_observation(observation, PolicyID.B2)

    assert view.available_fields == {
        InformationField.LOGICAL_REQUEST_ID,
        InformationField.RESOURCE_LOAD,
        InformationField.STATE_CANDIDATE_KEY,
        InformationField.STATE_LOCATION,
        InformationField.SESSION_ID,
        InformationField.SESSION_PREFERRED_LOCATION,
    }
    assert view.value(InformationField.SESSION_ID) == "s"
    assert view.value(InformationField.SESSION_PREFERRED_LOCATION) == "w1"
    for forbidden in (
        InformationField.CONTINUATION_ID,
        InformationField.CONTINUATION_ANCESTRY,
        InformationField.EXACT_STATE_ID,
        InformationField.STATE_PROVENANCE,
        InformationField.PRODUCER_ATTEMPT,
        InformationField.BINDING_EPOCH,
        InformationField.EVIDENCE_AUTHORITY,
    ):
        with pytest.raises(PermissionError):
            view.value(forbidden)


def test_b2_scoped_available_session_preference_wins_before_cache_locality():
    observation = PolicyObservation(
        "r",
        workers(),
        session_id="s",
        session_preferred_location="w1",
        state_candidate_key="prefix:abc",
        state_locations=("w2",),
    )

    decision = decide_placement(SessionAffinityPolicy(), observation)

    assert decision.worker_id == "w1"
    assert decision.ranked_worker_ids == ("w1", "w2", "w3")
    assert decision.reason == "SESSION_AFFINITY_THEN_CACHE_LOAD"


def test_b2_preserves_exact_b1_order_for_workers_after_preferred_location():
    observation = PolicyObservation(
        "r",
        workers(),
        session_id="s",
        session_preferred_location="w3",
        state_candidate_key="prefix:abc",
        state_locations=("w1", "w2"),
    )

    b1 = decide_placement(CacheAwarePolicy(), observation)
    b2 = decide_placement(SessionAffinityPolicy(), observation)

    assert b1.ranked_worker_ids == ("w2", "w1", "w3")
    assert b2.ranked_worker_ids == ("w3", "w2", "w1")
    assert b2.ranked_worker_ids[1:] == tuple(
        worker_id for worker_id in b1.ranked_worker_ids if worker_id != "w3"
    )


def test_b2_ignores_unscoped_preferred_location_without_session_id():
    observation = PolicyObservation(
        "r",
        workers(),
        session_preferred_location="w1",
        state_candidate_key="prefix:abc",
        state_locations=("w2",),
    )

    b1 = decide_placement(CacheAwarePolicy(), observation)
    b2 = decide_placement(SessionAffinityPolicy(), observation)

    assert b2.ranked_worker_ids == b1.ranked_worker_ids == ("w2", "w3", "w1")
    assert b2.reason == "SESSION_AFFINITY_B1_FALLBACK"


def test_b2_falls_back_to_b1_when_preferred_session_location_is_unavailable():
    unavailable_preference = (
        WorkerObservation("w1", False, 8, 0, 0),
        WorkerObservation("w2", True, 1, 1, 0),
        WorkerObservation("w3", True, 4, 1, 0),
    )
    observation = PolicyObservation(
        "r",
        unavailable_preference,
        session_id="s",
        session_preferred_location="w1",
        state_candidate_key="prefix:abc",
        state_locations=("w2",),
    )

    b1 = decide_placement(CacheAwarePolicy(), observation)
    b2 = decide_placement(SessionAffinityPolicy(), observation)

    assert b2.ranked_worker_ids == b1.ranked_worker_ids == ("w2", "w3")
    assert b2.reason == "SESSION_AFFINITY_B1_FALLBACK"


def test_b2_falls_back_to_b1_when_session_has_no_preferred_location():
    observation = PolicyObservation(
        "r",
        workers(),
        session_id="s",
        state_candidate_key="prefix:abc",
        state_locations=("w2",),
    )

    b1 = decide_placement(CacheAwarePolicy(), observation)
    b2 = decide_placement(SessionAffinityPolicy(), observation)

    assert b2.ranked_worker_ids == b1.ranked_worker_ids
    assert b2.reason == "SESSION_AFFINITY_B1_FALLBACK"


def test_b2_reports_no_available_worker():
    observation = PolicyObservation(
        "r",
        (
            WorkerObservation("w1", False, 1, 0, 0),
            WorkerObservation("w2", False, 1, 0, 0),
        ),
        session_id="s",
        session_preferred_location="w1",
    )

    decision = decide_placement(SessionAffinityPolicy(), observation)

    assert decision.worker_id is None
    assert decision.ranked_worker_ids == ()
    assert decision.reason == "NO_AVAILABLE_WORKER"


def test_b2_decision_is_independent_of_hidden_branch_and_continuity_metadata():
    left = PolicyObservation(
        "r",
        workers(),
        session_id="s",
        session_preferred_location="w2",
        state_candidate_key="prefix:abc",
        state_locations=("w1",),
        continuation_id="branch-a",
        continuation_ancestry=("root-a",),
        exact_state_id="x1",
        state_provenance=(("continuation", "root-a"),),
        producer_attempt_id="a1",
        binding_id="b1",
        binding_epoch=1,
        evidence_authority="AUTHORITATIVE",
        evidence_status="VALID",
        evidence_freshness=0.0,
    )
    right = PolicyObservation(
        "r",
        workers(),
        session_id="s",
        session_preferred_location="w2",
        state_candidate_key="prefix:abc",
        state_locations=("w1",),
        continuation_id="sibling-branch",
        continuation_ancestry=("other-root",),
        exact_state_id="x2",
        state_provenance=(("attempt", "other-attempt"),),
        producer_attempt_id="other-attempt",
        binding_id="other-binding",
        binding_epoch=99,
        evidence_authority="ESTIMATED",
        evidence_status="AMBIGUOUS",
        evidence_freshness=999.0,
    )

    assert decide_placement(SessionAffinityPolicy(), left) == decide_placement(
        SessionAffinityPolicy(), right
    )
