import pytest

from continuity.entities import (
    AttemptAuthority,
    BindingStatus,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionContext,
    ReconcileOutcome,
    RequestStatus,
)
from continuity.errors import InsufficientEvidence


def _evidence(core, id_, scope, *, status=EvidenceStatus.VALID, authority=EvidenceAuthority.EXACT_OBSERVATION):
    evidence = Evidence(
        id=id_,
        claim="test Evidence",
        source="C4.5a-R1 regression",
        authority=authority,
        status=status,
        observed_at=10.0,
        scope=frozenset(scope),
    )
    core.record_evidence(evidence)
    return evidence


def _mixed_evidence(core, scope, *, prefix):
    valid = _evidence(core, f"{prefix}:valid", scope)
    ambiguous = _evidence(
        core,
        f"{prefix}:ambiguous",
        scope,
        status=EvidenceStatus.AMBIGUOUS,
    )
    return valid, ambiguous


@pytest.mark.parametrize("action", ("finalize", "consume_state", "commit_migration"))
def test_correctness_sensitive_require_evidence_rejects_mixed_ambiguity(core, action):
    scope = {("subject", "x")}
    valid, ambiguous = _mixed_evidence(core, scope, prefix=action)

    with pytest.raises(InsufficientEvidence, match="ambiguous Evidence"):
        core.require_evidence(
            action,
            (valid.id, ambiguous.id),
            now=10.0,
            required_scope=scope,
        )

    assert core.reconcile(
        action,
        (valid.id, ambiguous.id),
        now=10.0,
        required_scope=scope,
    ) is ReconcileOutcome.AMBIGUOUS


def test_performance_evidence_requirement_retains_existing_degradable_behavior(core):
    scope = {("endpoint", "w1")}
    valid = _evidence(
        core,
        "rank:valid",
        scope,
        authority=EvidenceAuthority.ESTIMATED,
    )
    ambiguous = _evidence(
        core,
        "rank:ambiguous",
        scope,
        status=EvidenceStatus.AMBIGUOUS,
        authority=EvidenceAuthority.ESTIMATED,
    )

    accepted = core.require_evidence(
        "rank_endpoint",
        (valid.id, ambiguous.id),
        now=10.0,
        required_scope=scope,
    )

    assert accepted == [valid]
    assert core.reconcile(
        "rank_endpoint",
        (valid.id, ambiguous.id),
        now=10.0,
        required_scope=scope,
    ) is ReconcileOutcome.AMBIGUOUS


def test_finalize_fails_closed_on_valid_plus_ambiguous_evidence(core):
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request("r", "c")
    core.start_attempt("a", "r")
    core.complete_attempt("a", succeeded=True)

    scope = {("attempt", "a")}
    valid, ambiguous = _mixed_evidence(core, scope, prefix="finalize")
    core.create_output("o", "a", True, (valid.id, ambiguous.id))

    assert core.reconcile(
        "finalize",
        (valid.id, ambiguous.id),
        now=10.0,
        required_scope=scope,
    ) is ReconcileOutcome.AMBIGUOUS

    with pytest.raises(InsufficientEvidence, match="ambiguous Evidence"):
        core.finalize_request("r", "o", now=10.0)

    request = core.requests["r"]
    attempt = core.attempts["a"]
    assert request.status is RequestStatus.RUNNING
    assert request.current_attempt_id == "a"
    assert request.committed_attempt_id is None
    assert request.authoritative_output_id is None
    assert attempt.authority_status is AttemptAuthority.CURRENT


def test_state_consumption_fails_closed_on_valid_plus_ambiguous_evidence(core):
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request("r", "c")
    core.start_attempt("a", "r")
    core.create_state("x", origin_type="continuation", origin_id="c")
    core.add_replica("rp", "x", "w1")
    ctx = ExecutionContext("p", "s", "c", "r", "a")

    scope = {("state", "x"), ("replica", "rp")}
    valid, ambiguous = _mixed_evidence(core, scope, prefix="consume")

    assert core.state_compatible("x", ctx)
    assert core.reconcile(
        "consume_state",
        (valid.id, ambiguous.id),
        now=10.0,
        required_scope=scope,
    ) is ReconcileOutcome.AMBIGUOUS
    assert not core.can_consume_state(
        "x",
        "rp",
        ctx,
        (valid.id, ambiguous.id),
        now=10.0,
    )


def test_migration_commit_fails_closed_on_valid_plus_ambiguous_evidence(core):
    initial = core.activate_initial_binding("b1", "subject", "w1")
    candidate = core.propose_binding("b2", "subject", "w2")
    core.begin_migration("b2")

    scope = {("binding", "b2"), ("epoch", str(candidate.epoch))}
    valid, ambiguous = _mixed_evidence(core, scope, prefix="migration")

    assert core.reconcile(
        "commit_migration",
        (valid.id, ambiguous.id),
        now=10.0,
        required_scope=scope,
    ) is ReconcileOutcome.AMBIGUOUS

    with pytest.raises(InsufficientEvidence, match="ambiguous Evidence"):
        core.commit_migration("b2", (valid.id, ambiguous.id), now=10.0)

    assert core.current_binding_by_subject["subject"] == "b1"
    assert core.current_epoch_by_subject["subject"] == initial.epoch
    assert core.bindings["b1"].status is BindingStatus.ACTIVE
    assert core.bindings["b2"].status is BindingStatus.MIGRATING


def test_valid_only_semantic_evidence_still_succeeds(core):
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request("r", "c")
    core.start_attempt("a", "r")
    core.complete_attempt("a", succeeded=True)

    evidence = _evidence(core, "finalize:valid-only", {("attempt", "a")})
    core.create_output("o", "a", True, (evidence.id,))
    request = core.finalize_request("r", "o", now=10.0)

    assert request.status is RequestStatus.COMPLETED
    assert request.committed_attempt_id == "a"
    assert request.authoritative_output_id == "o"
    assert core.attempts["a"].authority_status is AttemptAuthority.COMMITTED
