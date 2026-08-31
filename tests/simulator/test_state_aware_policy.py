import pytest

from simulator import (
    CacheAwarePolicy,
    InformationField,
    PolicyID,
    PolicyObservation,
    StateAwarePolicy,
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


def test_b3_projection_exposes_exact_state_location_without_causal_authority():
    observation = PolicyObservation(
        "r",
        workers(),
        session_id="s",
        session_preferred_location="w3",
        continuation_id="c2",
        continuation_ancestry=("c0", "c1"),
        state_candidate_key="prefix:abc",
        exact_state_id="x",
        state_locations=("w1",),
        state_provenance=(("continuation", "c1"),),
        producer_attempt_id="a1",
        binding_id="b1",
        binding_epoch=1,
        evidence_authority="AUTHORITATIVE",
        evidence_status="VALID",
        evidence_freshness=0.0,
    )
    view = project_observation(observation, PolicyID.B3)

    assert view.available_fields == {
        InformationField.LOGICAL_REQUEST_ID,
        InformationField.RESOURCE_LOAD,
        InformationField.STATE_CANDIDATE_KEY,
        InformationField.EXACT_STATE_ID,
        InformationField.STATE_LOCATION,
    }
    assert view.value(InformationField.EXACT_STATE_ID) == "x"
    assert view.value(InformationField.STATE_LOCATION) == ("w1",)
    for forbidden in (
        InformationField.SESSION_ID,
        InformationField.SESSION_PREFERRED_LOCATION,
        InformationField.CONTINUATION_ID,
        InformationField.CONTINUATION_ANCESTRY,
        InformationField.STATE_PROVENANCE,
        InformationField.PRODUCER_ATTEMPT,
        InformationField.BINDING_EPOCH,
        InformationField.EVIDENCE_AUTHORITY,
    ):
        with pytest.raises(PermissionError):
            view.value(forbidden)


def test_b3_exact_state_locality_works_without_cache_candidate_key():
    observation = PolicyObservation(
        "r",
        workers(),
        exact_state_id="x",
        state_locations=("w1",),
    )

    b1 = decide_placement(CacheAwarePolicy(), observation)
    b3 = decide_placement(StateAwarePolicy(), observation)

    assert b1.ranked_worker_ids == ("w3", "w2", "w1")
    assert b3.worker_id == "w1"
    assert b3.ranked_worker_ids == ("w1", "w3", "w2")
    assert b3.reason == "EXACT_STATE_LOCALITY_THEN_LOAD"


def test_b3_multiple_exact_state_locations_use_shared_load_ordering():
    observation = PolicyObservation(
        "r",
        workers(),
        exact_state_id="x",
        state_locations=("w1", "w2"),
    )

    decision = decide_placement(StateAwarePolicy(), observation)

    assert decision.ranked_worker_ids == ("w2", "w1", "w3")
    assert decision.reason == "EXACT_STATE_LOCALITY_THEN_LOAD"


def test_b3_without_exact_state_id_falls_back_exactly_to_b1_candidate_locality():
    observation = PolicyObservation(
        "r",
        workers(),
        state_candidate_key="prefix:abc",
        state_locations=("w2",),
    )

    b1 = decide_placement(CacheAwarePolicy(), observation)
    b3 = decide_placement(StateAwarePolicy(), observation)

    assert b1.ranked_worker_ids == ("w2", "w3", "w1")
    assert b3.ranked_worker_ids == b1.ranked_worker_ids
    assert b3.reason == "STATE_AWARE_CANDIDATE_FALLBACK"


def test_b3_ignores_unscoped_location_without_exact_id_or_candidate_key():
    observation = PolicyObservation(
        "r",
        workers(),
        state_locations=("w1",),
    )

    b1 = decide_placement(CacheAwarePolicy(), observation)
    b3 = decide_placement(StateAwarePolicy(), observation)

    assert b3.ranked_worker_ids == b1.ranked_worker_ids == ("w3", "w2", "w1")
    assert b3.reason == "STATE_AWARE_LOAD_FALLBACK"


def test_b3_unavailable_exact_state_location_cannot_override_available_workers():
    unavailable_exact = (
        WorkerObservation("w1", False, 8, 0, 0),
        WorkerObservation("w2", True, 1, 1, 0),
        WorkerObservation("w3", True, 4, 1, 0),
    )
    observation = PolicyObservation(
        "r",
        unavailable_exact,
        exact_state_id="x",
        state_locations=("w1",),
    )

    decision = decide_placement(StateAwarePolicy(), observation)

    assert decision.worker_id == "w3"
    assert decision.ranked_worker_ids == ("w3", "w2")
    assert decision.reason == "STATE_AWARE_LOAD_FALLBACK"


def test_b3_reports_no_available_worker():
    observation = PolicyObservation(
        "r",
        (
            WorkerObservation("w1", False, 1, 0, 0),
            WorkerObservation("w2", False, 1, 0, 0),
        ),
        exact_state_id="x",
        state_locations=("w1",),
    )

    decision = decide_placement(StateAwarePolicy(), observation)

    assert decision.worker_id is None
    assert decision.ranked_worker_ids == ()
    assert decision.reason == "NO_AVAILABLE_WORKER"


def test_b3_cannot_distinguish_compatible_and_wrong_branch_provenance():
    compatible = PolicyObservation(
        "r",
        workers(),
        exact_state_id="x",
        state_locations=("w1",),
        continuation_id="current",
        continuation_ancestry=("ancestor",),
        state_provenance=(("continuation", "ancestor"),),
        producer_attempt_id="a-current",
        binding_id="b-current",
        binding_epoch=2,
        evidence_authority="AUTHORITATIVE",
        evidence_status="VALID",
        evidence_freshness=0.0,
    )
    wrong_sibling = PolicyObservation(
        "r",
        workers(),
        exact_state_id="x",
        state_locations=("w1",),
        session_id="other-session",
        session_preferred_location="w3",
        continuation_id="sibling",
        continuation_ancestry=("other-root",),
        state_provenance=(("continuation", "wrong-sibling"),),
        producer_attempt_id="superseded-attempt",
        binding_id="stale-binding",
        binding_epoch=1,
        evidence_authority="ESTIMATED",
        evidence_status="AMBIGUOUS",
        evidence_freshness=999.0,
    )

    assert decide_placement(StateAwarePolicy(), compatible) == decide_placement(
        StateAwarePolicy(), wrong_sibling
    )
