import json
import pytest

from continuity.core import ContinuityCore
from continuity.entities import (
    Evidence, EvidenceAuthority, EvidenceStatus, ExecutionStatus, PhaseStatus, PhaseType,
    SemanticEvent,
)
from continuity.invariants import InvariantOracle
from continuity.serialization import (
    EVENT_TRACE_SCHEMA,
    events_from_jsonl,
    events_to_jsonl,
    export_event_log,
    replay_event_log,
    restore_core,
    snapshot_core,
    snapshot_fingerprint,
)


def build_nontrivial_core():
    core = ContinuityCore()
    core.create_program('p')
    core.create_session('s', 'p')
    core.create_continuation('c0', 's')
    core.create_request('r', 'c0')
    core.start_attempt('a', 'r')
    core.set_attempt_execution('a', ExecutionStatus.RUNNING)
    core.create_phase('prefill', 'a', PhaseType.PREFILL)
    core.set_phase_status('prefill', PhaseStatus.RUNNING)
    core.complete_phase('prefill')
    core.create_state('x', origin_type='phase', origin_id='prefill')
    core.add_replica('rp', 'x', 'worker-1')
    core.activate_initial_binding('b1', 'session:s', 'worker-1')
    core.record_evidence(Evidence(
        'e1', 'replica present', 'test', EvidenceAuthority.EXACT_OBSERVATION,
        EvidenceStatus.VALID, 1.0, frozenset({('state', 'x'), ('replica', 'rp')})
    ))
    core.record_event(SemanticEvent('ev1', 'REQUEST_CREATED', 'request', 'r'))
    core.record_event(SemanticEvent('ev2', 'STATE_MATERIALIZED', 'state', 'x', frozenset({('replica', 'rp')})))
    InvariantOracle(core).assert_all()
    return core


def test_snapshot_is_canonical_and_deterministic():
    core = build_nontrivial_core()
    first = snapshot_core(core)
    second = snapshot_core(core)
    assert first == second
    assert json.loads(first)['schema'] == 'cadi.core.snapshot.v1'
    assert snapshot_fingerprint(core) == snapshot_fingerprint(core)


def test_restore_round_trip_preserves_full_semantic_state():
    original = build_nontrivial_core()
    snapshot = snapshot_core(original)
    restored = restore_core(snapshot)
    InvariantOracle(restored).assert_all()
    assert snapshot_core(restored) == snapshot
    assert snapshot_fingerprint(restored) == snapshot_fingerprint(original)


def test_snapshot_is_immutable_value_after_core_mutates():
    core = build_nontrivial_core()
    before = snapshot_core(core)
    core.record_event(SemanticEvent('ev3', 'AFTER_SNAPSHOT', 'system', 'runtime'))
    after = snapshot_core(core)
    assert before != after
    assert 'ev3' not in before


def test_semantic_event_jsonl_is_canonical_and_round_trips():
    events = [
        SemanticEvent('ev1', 'FIRST', 'system', 'runtime', frozenset({('b', '2'), ('a', '1')})),
        SemanticEvent('ev2', 'SECOND', 'system', 'runtime'),
    ]
    text = events_to_jsonl(events)
    assert events_from_jsonl(text) == events
    for line in text.splitlines():
        assert json.loads(line)['schema'] == EVENT_TRACE_SCHEMA
    assert text == events_to_jsonl(events)


def test_event_log_export_and_replay_preserve_first_delivery_order():
    source = ContinuityCore()
    source.record_event(SemanticEvent('ev2', 'SECOND', 'system', 'runtime'))
    source.record_event(SemanticEvent('ev1', 'FIRST', 'system', 'runtime'))
    source.record_event(SemanticEvent('ev2', 'SECOND', 'system', 'runtime'))
    trace = export_event_log(source)

    target = ContinuityCore()
    replay_event_log(target, trace)
    assert target.event_order == ['ev2', 'ev1']
    assert export_event_log(target) == trace
    InvariantOracle(target).assert_all()


def test_replaying_identical_trace_twice_is_idempotent():
    source = ContinuityCore()
    source.record_event(SemanticEvent('ev1', 'ONLY', 'system', 'runtime'))
    trace = export_event_log(source)
    target = ContinuityCore()
    replay_event_log(target, trace)
    replay_event_log(target, trace)
    assert target.event_order == ['ev1']


def test_unknown_snapshot_schema_is_rejected():
    with pytest.raises(ValueError):
        restore_core('{"schema":"future","state":{}}')


def test_unknown_event_schema_is_rejected():
    bad = '{"schema":"future","event":{}}\n'
    with pytest.raises(ValueError):
        events_from_jsonl(bad)
