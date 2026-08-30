import pytest

from continuity import ContinuityCore
from continuity.entities import (
    AttemptAuthority,
    Evidence,
    EvidenceAuthority,
    EvidenceStatus,
    ExecutionStatus,
)
from continuity.invariants import InvariantOracle
from continuity.serialization import snapshot_fingerprint
from simulator import (
    AdapterOutcome,
    ContinuityAdapter,
    DiscreteEventSimulator,
    EventKind,
    assert_authoritative_equivalent,
    authoritative_outcome,
)


def _scaffold_core() -> ContinuityCore:
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    return core


def _reference_retry_race() -> ContinuityCore:
    core = _scaffold_core()
    core.create_request("r", "c")
    core.start_attempt("a1", "r")
    core.set_attempt_execution("a1", ExecutionStatus.RUNNING)
    core.start_attempt("a2", "r")
    core.set_attempt_execution("a2", ExecutionStatus.RUNNING)
    core.complete_attempt("a1", succeeded=True)
    core.complete_attempt("a2", succeeded=True)
    evidence = Evidence(
        id="e2",
        claim="terminal_attempt_success",
        source="c2.semantic_adapter",
        authority=EvidenceAuthority.EXACT_OBSERVATION,
        status=EvidenceStatus.VALID,
        observed_at=8.0,
        scope=frozenset({("attempt", "a2")}),
    )
    core.record_evidence(evidence)
    core.create_output("o2", "a2", True, evidence_ids=["e2"])
    core.finalize_request("r", "o2", now=8.0)
    InvariantOracle(core).assert_all()
    return core


def _run_retry_race(*, late_at: float, duplicate_late: bool = False):
    sim = DiscreteEventSimulator(seed=17)
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)

    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    adapter.schedule_timeout("r", "a1", "a2", at=5)
    adapter.schedule_attempt_completion("a1", at=late_at, late=True)
    if duplicate_late:
        adapter.schedule_attempt_completion(
            "a1", at=late_at + 0.25, late=True, event_id="late-result:a1:duplicate"
        )
    adapter.schedule_attempt_completion("a2", at=7)
    adapter.schedule_observation("r", "a2", "e2", "o2", at=8)
    sim.run()
    return sim, core, adapter


def test_canonical_retry_race_matches_c1_authoritative_outcome():
    reference = _reference_retry_race()
    sim, core, adapter = _run_retry_race(late_at=6)

    outcome = assert_authoritative_equivalent(reference, core, "r")

    assert outcome.committed_attempt_id == "a2"
    assert outcome.current_attempt_id is None
    assert core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED
    assert core.attempts["a1"].execution_status is ExecutionStatus.SUCCEEDED
    assert core.attempts["a2"].authority_status is AttemptAuthority.COMMITTED
    assert sim.now == 8
    assert adapter.records
    assert all(record.fingerprint for record in adapter.records)


@pytest.mark.parametrize("late_at", [6.0, 7.5, 9.0])
def test_late_a1_timing_does_not_change_authoritative_winner(late_at):
    reference = _reference_retry_race()
    _, core, _ = _run_retry_race(late_at=late_at)
    outcome = assert_authoritative_equivalent(reference, core, "r")
    assert outcome.committed_attempt_id == "a2"


def test_duplicate_late_completion_is_semantically_idempotent():
    reference = _reference_retry_race()
    _, core, adapter = _run_retry_race(late_at=6, duplicate_late=True)

    assert_authoritative_equivalent(reference, core, "r")
    duplicate = [
        record
        for record in adapter.records
        if record.event_id == "late-result:a1:duplicate"
        and record.operation == "complete_attempt:SUCCEEDED"
    ]
    assert len(duplicate) == 1
    assert duplicate[0].outcome is AdapterOutcome.IDEMPOTENT


def test_delayed_timeout_can_supersede_physically_succeeded_but_unfinalized_a1():
    reference = _reference_retry_race()
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)

    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    adapter.schedule_attempt_completion("a1", at=4)
    adapter.schedule_timeout("r", "a1", "a2", at=5)
    adapter.schedule_attempt_completion("a2", at=7)
    adapter.schedule_observation("r", "a2", "e2", "o2", at=8)
    sim.run()

    assert core.attempts["a1"].execution_status is ExecutionStatus.SUCCEEDED
    assert core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED
    assert_authoritative_equivalent(reference, core, "r")


def test_duplicate_timeout_schedules_only_one_retry_attempt():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)

    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    adapter.schedule_timeout("r", "a1", "a2", at=5, event_id="timeout-1")
    adapter.schedule_timeout("r", "a1", "a2", at=5, event_id="timeout-2")
    sim.run(until=5)

    attempts = sorted(
        (attempt.id, attempt.generation) for attempt in core.attempts.values()
    )
    assert attempts == [("a1", 1), ("a2", 2)]
    ignored = [
        record
        for record in adapter.records
        if record.event_id == "timeout-2" and record.outcome is AdapterOutcome.IGNORED
    ]
    assert ignored


def test_duplicate_authoritative_observation_is_idempotent():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)

    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    adapter.schedule_attempt_completion("a1", at=2)
    adapter.schedule_observation("r", "a1", "e1", "o1", at=3)
    adapter.schedule_observation(
        "r", "a1", "e1", "o1", at=4, observed_at=3, duplicated=True, event_id="duplicate-observation"
    )
    sim.run()

    assert core.requests["r"].committed_attempt_id == "a1"
    duplicate_records = [
        record for record in adapter.records if record.event_id == "duplicate-observation"
    ]
    assert [record.outcome for record in duplicate_records] == [
        AdapterOutcome.IDEMPOTENT,
        AdapterOutcome.IDEMPOTENT,
        AdapterOutcome.IDEMPOTENT,
    ]


def test_stale_timeout_after_finalization_is_ignored_without_spawning_retry():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)

    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    adapter.schedule_attempt_completion("a1", at=2)
    adapter.schedule_observation("r", "a1", "e1", "o1", at=3)
    adapter.schedule_timeout("r", "a1", "a2", at=5)
    sim.run()

    assert set(core.attempts) == {"a1"}
    assert core.requests["r"].committed_attempt_id == "a1"
    timeout_records = [
        record
        for record in adapter.records
        if record.event_kind is EventKind.ATTEMPT_TIMEOUT
    ]
    assert timeout_records[-1].outcome is AdapterOutcome.IGNORED


def test_superseded_a1_observation_is_explicitly_rejected_as_authoritative():
    sim, core, adapter = _run_retry_race(late_at=6)
    before = authoritative_outcome(core, "r")

    adapter.schedule_observation("r", "a1", "e1", "o1", at=9)
    sim.run()

    after = authoritative_outcome(core, "r")
    assert after == before
    rejected = [
        record
        for record in adapter.records
        if record.event_id == "observation:e1:o1"
        and record.operation == "finalize_request"
    ]
    assert len(rejected) == 1
    assert rejected[0].outcome is AdapterOutcome.REJECTED
    assert core.requests["r"].committed_attempt_id == "a2"


def test_malformed_adapter_event_is_rejected_without_semantic_mutation():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    before = snapshot_fingerprint(core)

    sim.schedule(
        EventKind.ATTEMPT_STARTED,
        at=1,
        event_id="malformed",
        payload={"request_id": "r"},
    )
    sim.run()

    assert snapshot_fingerprint(core) == before
    assert adapter.records[-1].event_id == "malformed"
    assert adapter.records[-1].operation == "event_validation"
    assert adapter.records[-1].outcome is AdapterOutcome.REJECTED


def test_rejected_c1_finalization_is_fingerprint_stable_at_rejection_boundary():
    sim, core, adapter = _run_retry_race(late_at=6)
    adapter.schedule_observation("r", "a1", "e1", "o1", at=9)
    sim.run()

    records = [
        record
        for record in adapter.records
        if record.event_id == "observation:e1:o1"
    ]
    finalize_index = next(
        index for index, record in enumerate(records) if record.operation == "finalize_request"
    )
    rejected = records[finalize_index]
    assert rejected.outcome is AdapterOutcome.REJECTED
    assert rejected.fingerprint == snapshot_fingerprint(core)
    InvariantOracle(core).assert_all()


def test_duplicate_delivery_before_original_preserves_exact_evidence_identity():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    adapter.schedule_attempt_completion("a1", at=2)
    adapter.schedule_observation(
        "r", "a1", "e1", "o1", at=3, observed_at=2.5, duplicated=True, event_id="duplicate-first"
    )
    adapter.schedule_observation(
        "r", "a1", "e1", "o1", at=4, observed_at=2.5, event_id="original-late"
    )
    sim.run()

    assert core.evidence["e1"].observed_at == 2.5
    assert core.requests["r"].committed_attempt_id == "a1"
    later = [record for record in adapter.records if record.event_id == "original-late"]
    assert [record.outcome for record in later] == [
        AdapterOutcome.IDEMPOTENT, AdapterOutcome.IDEMPOTENT, AdapterOutcome.IDEMPOTENT
    ]


def test_conflicting_preexisting_evidence_identity_is_rejected():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    core.create_request("r", "c")
    core.start_attempt("a1", "r")
    core.set_attempt_execution("a1", ExecutionStatus.RUNNING)
    core.complete_attempt("a1", succeeded=True)
    core.record_evidence(Evidence(
        id="e1", claim="terminal_attempt_success", source="c2.semantic_adapter",
        authority=EvidenceAuthority.EXACT_OBSERVATION, status=EvidenceStatus.VALID,
        observed_at=1.0, scope=frozenset({("attempt", "a1")}),
    ))
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_observation("r", "a1", "e1", "o1", at=3, observed_at=2.0)
    sim.run()

    assert "o1" not in core.outputs
    assert core.requests["r"].committed_attempt_id is None
    assert adapter.records[-1].operation == "observation_identity"
    assert adapter.records[-1].outcome is AdapterOutcome.REJECTED


def test_terminal_observation_cannot_predate_delivered_attempt_success():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    adapter.schedule_attempt_completion("a1", at=5)
    adapter.schedule_observation("r", "a1", "e1", "o1", at=6, observed_at=4)
    sim.run()

    assert "e1" not in core.evidence
    assert "o1" not in core.outputs
    assert core.requests["r"].committed_attempt_id is None
    record = next(
        record for record in adapter.records
        if record.event_id == "observation:e1:o1"
    )
    assert record.operation == "observation_timestamp"
    assert record.outcome is AdapterOutcome.REJECTED


@pytest.mark.parametrize("timeout_first", [True, False])
def test_simultaneous_timeout_and_completion_orderings_preserve_a2_authority(timeout_first):
    reference = _reference_retry_race()
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    if timeout_first:
        adapter.schedule_timeout("r", "a1", "a2", at=5)
        adapter.schedule_attempt_completion("a1", at=5)
    else:
        adapter.schedule_attempt_completion("a1", at=5)
        adapter.schedule_timeout("r", "a1", "a2", at=5)
    adapter.schedule_attempt_completion("a2", at=7)
    adapter.schedule_observation("r", "a2", "e2", "o2", at=8)
    sim.run()

    assert_authoritative_equivalent(reference, core, "r")
    assert core.attempts["a1"].execution_status is ExecutionStatus.SUCCEEDED
    assert core.attempts["a1"].authority_status is AttemptAuthority.SUPERSEDED


def test_explicit_retry_reservation_deduplicates_later_timeout_delivery():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    adapter.schedule_request("r", "c", at=0)
    adapter.schedule_attempt_start("r", "a1", at=1)
    adapter.schedule_retry_start("r", "a1", "a2", at=5, event_id="preplanned-retry")
    adapter.schedule_timeout("r", "a1", "a2", at=4, event_id="timeout")
    sim.run()

    assert [(a.id, a.generation) for a in sorted(core.attempts.values(), key=lambda a: a.generation)] == [
        ("a1", 1), ("a2", 2)
    ]
    timeout = [record for record in adapter.records if record.event_id == "timeout"]
    assert timeout[-1].outcome is AdapterOutcome.IGNORED


def test_retry_attempt_identity_must_differ_from_superseded_attempt():
    sim = DiscreteEventSimulator()
    core = _scaffold_core()
    adapter = ContinuityAdapter(sim, core)
    with pytest.raises(ValueError, match="must differ"):
        adapter.schedule_timeout("r", "a1", "a1", at=1)
    with pytest.raises(ValueError, match="must differ"):
        adapter.schedule_retry_start("r", "a1", "a1", at=1)
