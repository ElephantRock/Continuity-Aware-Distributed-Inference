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


def _evidence(
    core,
    id_,
    scope,
    *,
    claim="diagnostic Evidence text",
    claim_key=None,
    claim_value=None,
    status=EvidenceStatus.VALID,
    authority=EvidenceAuthority.EXACT_OBSERVATION,
    observed_at=10.0,
    valid_until=None,
):
    evidence = Evidence(
        id=id_,
        claim=claim,
        source="C4.5b-R1 contradiction regression",
        authority=authority,
        status=status,
        observed_at=observed_at,
        scope=frozenset(scope),
        valid_until=valid_until,
        claim_key=claim_key,
        claim_value=claim_value,
    )
    core.record_evidence(evidence)
    return evidence


def _conflict(core, scope, *, prefix, key, authority=EvidenceAuthority.EXACT_OBSERVATION):
    left = _evidence(
        core,
        f"{prefix}:left",
        scope,
        claim_key=key,
        claim_value="LEFT",
        authority=authority,
    )
    right = _evidence(
        core,
        f"{prefix}:right",
        scope,
        claim_key=key,
        claim_value="RIGHT",
        authority=authority,
    )
    return left, right


def _finalize_scaffold(core):
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request("r", "c")
    core.start_attempt("a", "r")
    core.complete_attempt("a", succeeded=True)


def test_structured_claim_metadata_is_pairwise_and_non_empty():
    base = dict(
        id="e",
        claim="diagnostic",
        source="test",
        authority=EvidenceAuthority.EXACT_OBSERVATION,
        status=EvidenceStatus.VALID,
        observed_at=1.0,
    )

    with pytest.raises(ValueError, match="both present or both absent"):
        Evidence(**base, claim_key="attempt:a:terminal-outcome")
    with pytest.raises(ValueError, match="both present or both absent"):
        Evidence(**base, claim_value="SUCCEEDED")
    with pytest.raises(ValueError, match="claim_key must be a non-empty string"):
        Evidence(**base, claim_key="", claim_value="SUCCEEDED")
    with pytest.raises(ValueError, match="claim_value must be a non-empty string"):
        Evidence(**base, claim_key="attempt:a:terminal-outcome", claim_value="")


def test_structured_conflict_reconciles_ambiguous_and_finalize_fails_closed(core):
    _finalize_scaffold(core)
    scope = {("attempt", "a")}
    success = _evidence(
        core,
        "finalize:success",
        scope,
        claim="attempt-terminal-outcome=SUCCEEDED",
        claim_key="attempt:a:terminal-outcome",
        claim_value="SUCCEEDED",
    )
    failure = _evidence(
        core,
        "finalize:failure",
        scope,
        claim="attempt-terminal-outcome=FAILED",
        claim_key="attempt:a:terminal-outcome",
        claim_value="FAILED",
    )
    ids = (success.id, failure.id)
    core.create_output("o", "a", True, ids)

    assert core.reconcile(
        "finalize", ids, now=10.0, required_scope=scope
    ) is ReconcileOutcome.AMBIGUOUS

    with pytest.raises(InsufficientEvidence, match="contradictory Evidence"):
        core.finalize_request("r", "o", now=10.0)

    request = core.requests["r"]
    attempt = core.attempts["a"]
    assert request.status is RequestStatus.RUNNING
    assert request.current_attempt_id == "a"
    assert request.committed_attempt_id is None
    assert request.authoritative_output_id is None
    assert attempt.authority_status is AttemptAuthority.CURRENT


def test_identical_structured_claim_values_remain_matchable_and_finalize(core):
    _finalize_scaffold(core)
    scope = {("attempt", "a")}
    first = _evidence(
        core,
        "same:first",
        scope,
        claim_key="attempt:a:terminal-outcome",
        claim_value="SUCCEEDED",
    )
    second = _evidence(
        core,
        "same:second",
        scope,
        claim_key="attempt:a:terminal-outcome",
        claim_value="SUCCEEDED",
    )
    ids = (first.id, second.id)
    core.create_output("o", "a", True, ids)

    assert core.reconcile(
        "finalize", ids, now=10.0, required_scope=scope
    ) is ReconcileOutcome.MATCHED
    request = core.finalize_request("r", "o", now=10.0)
    assert request.status is RequestStatus.COMPLETED
    assert request.committed_attempt_id == "a"
    assert core.attempts["a"].authority_status is AttemptAuthority.COMMITTED


def test_state_consumption_fails_closed_on_structured_contradiction(core):
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request("r", "c")
    core.start_attempt("a", "r")
    core.create_state("x", origin_type="continuation", origin_id="c")
    core.add_replica("rp", "x", "w1")
    ctx = ExecutionContext("p", "s", "c", "r", "a")

    scope = {("state", "x"), ("replica", "rp")}
    left, right = _conflict(
        core,
        scope,
        prefix="consume",
        key="state:x:replica:rp:usable",
    )
    ids = (left.id, right.id)

    assert core.reconcile(
        "consume_state", ids, now=10.0, required_scope=scope
    ) is ReconcileOutcome.AMBIGUOUS
    assert not core.can_consume_state("x", "rp", ctx, ids, now=10.0)


def test_migration_commit_fails_closed_on_structured_contradiction(core):
    initial = core.activate_initial_binding("b1", "subject", "w1")
    candidate = core.propose_binding("b2", "subject", "w2")
    core.begin_migration("b2")

    scope = {("binding", "b2"), ("epoch", str(candidate.epoch))}
    left, right = _conflict(
        core,
        scope,
        prefix="migration",
        key=f"binding:b2:epoch:{candidate.epoch}:commit-ready",
    )
    ids = (left.id, right.id)

    assert core.reconcile(
        "commit_migration", ids, now=10.0, required_scope=scope
    ) is ReconcileOutcome.AMBIGUOUS
    with pytest.raises(InsufficientEvidence, match="contradictory Evidence"):
        core.commit_migration("b2", ids, now=10.0)

    assert core.current_binding_by_subject["subject"] == "b1"
    assert core.current_epoch_by_subject["subject"] == initial.epoch
    assert core.bindings["b1"].status is BindingStatus.ACTIVE
    assert core.bindings["b2"].status is BindingStatus.MIGRATING


@pytest.mark.parametrize("irrelevant_kind", ("stale", "expired", "below-authority"))
def test_irrelevant_conflicting_item_does_not_create_false_contradiction(core, irrelevant_kind):
    scope = {("attempt", "a")}
    good = _evidence(
        core,
        f"{irrelevant_kind}:good",
        scope,
        claim_key="attempt:a:terminal-outcome",
        claim_value="SUCCEEDED",
    )
    kwargs = {}
    if irrelevant_kind == "stale":
        kwargs["status"] = EvidenceStatus.STALE
    elif irrelevant_kind == "expired":
        kwargs["valid_until"] = 9.0
    else:
        kwargs["authority"] = EvidenceAuthority.ESTIMATED
    conflicting = _evidence(
        core,
        f"{irrelevant_kind}:conflict",
        scope,
        claim_key="attempt:a:terminal-outcome",
        claim_value="FAILED",
        **kwargs,
    )
    ids = (good.id, conflicting.id)

    assert core.reconcile(
        "finalize", ids, now=10.0, required_scope=scope
    ) is ReconcileOutcome.MATCHED
    assert core.require_evidence(
        "finalize", ids, now=10.0, required_scope=scope
    ) == [good]


def test_max_age_filters_old_conflicting_item_before_contradiction_check(core):
    scope = {("attempt", "a")}
    fresh = _evidence(
        core,
        "age:fresh",
        scope,
        claim_key="attempt:a:terminal-outcome",
        claim_value="SUCCEEDED",
        observed_at=10.0,
    )
    old = _evidence(
        core,
        "age:old",
        scope,
        claim_key="attempt:a:terminal-outcome",
        claim_value="FAILED",
        observed_at=1.0,
    )

    accepted = core.require_evidence(
        "finalize",
        (fresh.id, old.id),
        now=10.0,
        required_scope=scope,
        max_age=2.0,
    )
    assert accepted == [fresh]


def test_performance_requirement_remains_degradable_while_reconcile_reports_conflict(core):
    scope = {("endpoint", "w1")}
    left, right = _conflict(
        core,
        scope,
        prefix="rank",
        key="endpoint:w1:availability",
        authority=EvidenceAuthority.ESTIMATED,
    )
    ids = (left.id, right.id)

    assert core.require_evidence(
        "rank_endpoint", ids, now=10.0, required_scope=scope
    ) == [left, right]
    assert core.reconcile(
        "rank_endpoint", ids, now=10.0, required_scope=scope
    ) is ReconcileOutcome.AMBIGUOUS


def test_opaque_free_text_claims_are_not_parsed_for_contradiction(core):
    scope = {("attempt", "a")}
    success = _evidence(
        core,
        "opaque:success",
        scope,
        claim="attempt-terminal-outcome=SUCCEEDED",
    )
    failure = _evidence(
        core,
        "opaque:failure",
        scope,
        claim="attempt-terminal-outcome=FAILED",
    )
    ids = (success.id, failure.id)

    assert core.reconcile(
        "finalize", ids, now=10.0, required_scope=scope
    ) is ReconcileOutcome.MATCHED
    assert core.require_evidence(
        "finalize", ids, now=10.0, required_scope=scope
    ) == [success, failure]
