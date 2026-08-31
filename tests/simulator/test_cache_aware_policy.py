import pytest

from simulator import (
    CacheAwarePolicy,
    InformationField,
    PolicyID,
    PolicyObservation,
    RequestCentricPolicy,
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


def test_b1_projection_exposes_only_declared_cache_and_load_information():
    observation = PolicyObservation(
        "r",
        workers(),
        session_id="s",
        session_preferred_location="w3",
        continuation_id="c",
        continuation_ancestry=("c0",),
        state_candidate_key="prefix:abc",
        exact_state_id="x",
        state_locations=("w1",),
        state_provenance=(("continuation", "c0"),),
        producer_attempt_id="a1",
        binding_id="b1",
        binding_epoch=1,
        evidence_authority="AUTHORITATIVE",
        evidence_status="VALID",
        evidence_freshness=0.0,
    )
    view = project_observation(observation, PolicyID.B1)

    assert view.available_fields == {
        InformationField.LOGICAL_REQUEST_ID,
        InformationField.RESOURCE_LOAD,
        InformationField.STATE_CANDIDATE_KEY,
        InformationField.STATE_LOCATION,
    }
    assert view.value(InformationField.STATE_CANDIDATE_KEY) == "prefix:abc"
    assert view.value(InformationField.STATE_LOCATION) == ("w1",)
    for forbidden in (
        InformationField.SESSION_ID,
        InformationField.SESSION_PREFERRED_LOCATION,
        InformationField.CONTINUATION_ANCESTRY,
        InformationField.EXACT_STATE_ID,
        InformationField.STATE_PROVENANCE,
        InformationField.PRODUCER_ATTEMPT,
        InformationField.BINDING_EPOCH,
        InformationField.EVIDENCE_AUTHORITY,
    ):
        with pytest.raises(PermissionError):
            view.value(forbidden)


def test_b1_prefers_available_candidate_locality_before_lower_remote_load():
    observation = PolicyObservation(
        "r",
        workers(),
        state_candidate_key="prefix:abc",
        state_locations=("w1",),
    )

    decision = decide_placement(CacheAwarePolicy(), observation)

    assert decision.worker_id == "w1"
    assert decision.ranked_worker_ids == ("w1", "w3", "w2")
    assert decision.reason == "CACHE_LOCALITY_THEN_LOAD"


def test_b1_uses_b0_load_order_within_local_and_remote_classes():
    observation = PolicyObservation(
        "r",
        workers(),
        state_candidate_key="prefix:abc",
        state_locations=("w1", "w2"),
    )

    decision = decide_placement(CacheAwarePolicy(), observation)

    assert decision.ranked_worker_ids == ("w2", "w1", "w3")


def test_b1_ignores_unscoped_location_when_candidate_key_is_absent():
    observation = PolicyObservation(
        "r",
        workers(),
        state_locations=("w1",),
    )

    b0 = decide_placement(RequestCentricPolicy(), observation)
    b1 = decide_placement(CacheAwarePolicy(), observation)

    assert b1.worker_id == b0.worker_id == "w3"
    assert b1.ranked_worker_ids == b0.ranked_worker_ids == ("w3", "w2", "w1")
    assert b1.reason == "CACHE_AWARE_LOAD_FALLBACK"


def test_b1_falls_back_to_load_when_only_candidate_location_is_unavailable():
    unavailable_local = (
        WorkerObservation("w1", False, 8, 0, 0),
        WorkerObservation("w2", True, 1, 1, 0),
        WorkerObservation("w3", True, 4, 1, 0),
    )
    observation = PolicyObservation(
        "r",
        unavailable_local,
        state_candidate_key="prefix:abc",
        state_locations=("w1",),
    )

    decision = decide_placement(CacheAwarePolicy(), observation)

    assert decision.worker_id == "w3"
    assert decision.ranked_worker_ids == ("w3", "w2")
    assert decision.reason == "CACHE_AWARE_LOAD_FALLBACK"


def test_b1_reports_no_available_worker():
    observation = PolicyObservation(
        "r",
        (
            WorkerObservation("w1", False, 1, 0, 0),
            WorkerObservation("w2", False, 1, 0, 0),
        ),
        state_candidate_key="prefix:abc",
        state_locations=("w1",),
    )

    decision = decide_placement(CacheAwarePolicy(), observation)

    assert decision.worker_id is None
    assert decision.ranked_worker_ids == ()
    assert decision.reason == "NO_AVAILABLE_WORKER"


def test_b1_decision_is_independent_of_hidden_continuity_metadata():
    left = PolicyObservation(
        "r",
        workers(),
        state_candidate_key="prefix:abc",
        state_locations=("w2",),
        exact_state_id="x1",
        session_id="s1",
        session_preferred_location="w1",
        continuation_id="c1",
        continuation_ancestry=("c0",),
        state_provenance=(("continuation", "c0"),),
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
        state_candidate_key="prefix:abc",
        state_locations=("w2",),
        exact_state_id="x2",
        session_id="s2",
        session_preferred_location="w3",
        continuation_id="other",
        continuation_ancestry=("other-root",),
        state_provenance=(("attempt", "other-attempt"),),
        producer_attempt_id="other-attempt",
        binding_id="other-binding",
        binding_epoch=99,
        evidence_authority="ESTIMATED",
        evidence_status="AMBIGUOUS",
        evidence_freshness=999.0,
    )

    assert decide_placement(CacheAwarePolicy(), left) == decide_placement(
        CacheAwarePolicy(), right
    )
