from dataclasses import replace

import pytest

from continuity.core import ContinuityCore
from continuity.entities import ContinuationLifecycle
from continuity.serialization import snapshot_fingerprint
from simulator import (
    INFORMATION_CONTRACTS,
    POLICY_CONTRACT_SCHEMA,
    ContinuityAwarePolicy,
    CoreContinuityAuthority,
    InformationField,
    MigrationDisposition,
    PolicyID,
    PolicyObservation,
    RetentionDisposition,
    WorkerObservation,
    build_baseline_policies,
    decide_paired_placements,
    decide_placement,
    project_observation,
)


def workers():
    return (
        WorkerObservation("w1", True, 1, 1, 0),
        WorkerObservation("w2", True, 1, 0, 0),
    )


def compatible_core():
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c0", "s", lifecycle=ContinuationLifecycle.ACTIVE)
    core.create_state("x", origin_type="continuation", origin_id="c0")
    core.create_request("r", "c0")
    core.start_attempt("a", "r")
    return core


def wrong_sibling_core():
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c0", "s", lifecycle=ContinuationLifecycle.ACTIVE)
    core.create_continuation(
        "c1", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
    )
    core.create_continuation(
        "c2", "s", parent_ids=("c0",), lifecycle=ContinuationLifecycle.ACTIVE
    )
    core.create_state("x", origin_type="continuation", origin_id="c1")
    core.create_request("r", "c2")
    core.start_attempt("a", "r")
    return core


def observation(**changes):
    base = PolicyObservation(
        request_id="r",
        workers=workers(),
        program_id="p",
        attempt_id="a",
        attempt_authority="CURRENT",
        session_id="s",
        session_preferred_location="w1",
        continuation_id="c0",
        continuation_ancestry=(),
        state_candidate_key="prefix:abc",
        exact_state_id="x",
        state_locations=("w1",),
        state_provenance=(("origin_continuation", "c0"),),
        state_lifecycle="ACTIVE",
        producer_attempt_id=None,
        binding_id="b1",
        binding_epoch=1,
        evidence_authority="EXACT_OBSERVATION",
        evidence_status="VALID",
        evidence_freshness=0.0,
        reconciliation="MATCHED",
    )
    return replace(base, **changes)


def test_contract_schema_v2_repairs_b4_only_without_expanding_b0_to_b3():
    assert POLICY_CONTRACT_SCHEMA == "cadi.policy-information-contract.v2"
    new_fields = {
        InformationField.PROGRAM_ID,
        InformationField.STATE_LIFECYCLE,
        InformationField.RECONCILIATION,
    }
    for policy_id in (PolicyID.B0, PolicyID.B1, PolicyID.B2, PolicyID.B3):
        assert INFORMATION_CONTRACTS[policy_id].fields.isdisjoint(new_fields)
    assert new_fields <= INFORMATION_CONTRACTS[PolicyID.B4].fields
    assert INFORMATION_CONTRACTS[PolicyID.B4].fields == frozenset(InformationField)


def test_schema_v2_preserves_v1_policy_observation_positional_slots():
    obs = PolicyObservation("r", workers(), "a", "CURRENT", "s")

    assert obs.attempt_id == "a"
    assert obs.attempt_authority == "CURRENT"
    assert obs.session_id == "s"
    assert obs.program_id is None
    assert obs.state_lifecycle is None
    assert obs.reconciliation is None


def test_b4_projection_exposes_repaired_contract_and_b3_cannot_read_it():
    b4 = project_observation(observation(), PolicyID.B4)
    assert b4.value(InformationField.PROGRAM_ID) == "p"
    assert b4.value(InformationField.STATE_LIFECYCLE) == "ACTIVE"
    assert b4.value(InformationField.RECONCILIATION) == "MATCHED"

    b3 = project_observation(observation(), PolicyID.B3)
    for field in (
        InformationField.PROGRAM_ID,
        InformationField.STATE_LIFECYCLE,
        InformationField.RECONCILIATION,
    ):
        with pytest.raises(PermissionError):
            b3.value(field)


def test_b4_prefers_exact_state_locality_only_when_c1_reports_compatible():
    core = compatible_core()
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))

    decision = decide_placement(policy, observation())

    assert decision.worker_id == "w1"
    assert decision.ranked_worker_ids == ("w1", "w2")
    assert decision.reason == "COMPATIBLE_STATE_LOCALITY_THEN_LOAD"


def test_b4_wrong_sibling_state_fails_closed_to_recompute_load_routing():
    core = wrong_sibling_core()
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))
    obs = observation(
        continuation_id="c2",
        continuation_ancestry=("c0",),
        state_provenance=(("origin_continuation", "c1"),),
    )

    decision = decide_placement(policy, obs)

    assert decision.worker_id == "w2"
    assert decision.ranked_worker_ids == ("w2", "w1")
    assert decision.reason == "INCOMPATIBLE_STATE_RECOMPUTE"


def test_b4_requires_matched_reconciliation_before_state_locality():
    core = compatible_core()
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))

    decision = decide_placement(policy, observation(reconciliation="AMBIGUOUS"))

    assert decision.worker_id == "w2"
    assert decision.ranked_worker_ids == ("w2", "w1")
    assert decision.reason == "RECONCILIATION_NOT_MATCHED_RECOMPUTE"


def test_b4_fences_non_current_attempt_before_placement():
    core = compatible_core()
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))

    decision = decide_placement(policy, observation(attempt_authority="SUPERSEDED"))

    assert decision.worker_id is None
    assert decision.ranked_worker_ids == ()
    assert decision.reason == "ATTEMPT_FENCED"


def test_b4_cross_checks_observed_attempt_authority_with_c1_authority():
    core = compatible_core()
    core.start_attempt("a2", "r")
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))

    decision = decide_placement(
        policy,
        observation(attempt_id="a", attempt_authority="CURRENT"),
    )

    assert decision.worker_id is None
    assert decision.ranked_worker_ids == ()
    assert decision.reason == "ATTEMPT_FENCED"


def test_b4_attempt_fencing_precedes_physical_worker_availability():
    core = compatible_core()
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))

    decision = decide_placement(
        policy,
        observation(workers=(), attempt_authority="SUPERSEDED"),
    )

    assert decision.worker_id is None
    assert decision.ranked_worker_ids == ()
    assert decision.reason == "ATTEMPT_FENCED"


def test_b4_does_not_promote_candidate_only_locality_without_exact_state_identity():
    core = compatible_core()
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))

    decision = decide_placement(policy, observation(exact_state_id=None))

    assert decision.worker_id == "w2"
    assert decision.ranked_worker_ids == ("w2", "w1")
    assert decision.reason == "CONTINUITY_RECOMPUTE_LOAD_FALLBACK"


def test_b4_retention_priority_uses_only_canonical_lifecycle_order():
    core = compatible_core()
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))
    expected = (
        ("ACTIVE", 3, RetentionDisposition.PROTECT),
        ("WAITING", 2, RetentionDisposition.RETAIN),
        ("SPECULATIVE", 1, RetentionDisposition.BEST_EFFORT),
        ("TERMINAL", 0, RetentionDisposition.RELEASE),
    )

    decisions = []
    for lifecycle, priority, disposition in expected:
        view = project_observation(observation(state_lifecycle=lifecycle), PolicyID.B4)
        decision = policy.decide_retention(view)
        decisions.append(decision)
        assert decision.priority == priority
        assert decision.disposition is disposition

    assert [decision.priority for decision in decisions] == [3, 2, 1, 0]


def test_b4_migration_guard_allows_only_reconciled_declared_binding():
    core = compatible_core()
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))

    matched = policy.decide_migration(project_observation(observation(), PolicyID.B4))
    ambiguous = policy.decide_migration(
        project_observation(observation(reconciliation="AMBIGUOUS"), PolicyID.B4)
    )
    missing = policy.decide_migration(
        project_observation(observation(binding_epoch=None), PolicyID.B4)
    )

    assert matched.disposition is MigrationDisposition.ALLOW_COMMIT
    assert matched.reason == "RECONCILED_BINDING_COMMIT_ELIGIBLE"
    assert ambiguous.disposition is MigrationDisposition.WAIT
    assert ambiguous.reason == "RECONCILIATION_REQUIRED"
    assert missing.disposition is MigrationDisposition.WAIT
    assert missing.reason == "MISSING_BINDING_CONTEXT"


def test_b4_policy_queries_do_not_mutate_c1_semantic_state():
    core = compatible_core()
    policy = ContinuityAwarePolicy(CoreContinuityAuthority(core))
    before = snapshot_fingerprint(core)
    obs = observation()

    decide_placement(policy, obs)
    policy.decide_retention(project_observation(obs, PolicyID.B4))
    policy.decide_migration(project_observation(obs, PolicyID.B4))

    assert snapshot_fingerprint(core) == before


def test_paired_interface_executes_exactly_b0_through_b4_over_one_observation():
    core = compatible_core()
    policies = build_baseline_policies(CoreContinuityAuthority(core))
    obs = observation()

    decisions = decide_paired_placements(policies, obs)

    assert tuple(decision.policy_id for decision in decisions) == tuple(PolicyID)
    assert len(decisions) == 5
    assert decisions[-1].reason == "COMPATIBLE_STATE_LOCALITY_THEN_LOAD"


def test_paired_interface_is_deterministic_for_equal_observations():
    core = compatible_core()
    policies = build_baseline_policies(CoreContinuityAuthority(core))
    obs = observation()

    left = decide_paired_placements(policies, obs)
    right = decide_paired_placements(policies, obs)

    assert left == right
