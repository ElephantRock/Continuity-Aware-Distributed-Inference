import json
import pytest

from continuity.core import ContinuityCore
from continuity.entities import (
    Evidence, EvidenceAuthority, EvidenceStatus, ExecutionStatus, PhaseStatus, PhaseType,
    SemanticEvent,
)
from continuity.invariants import InvariantOracle
from continuity.replay import (
    OPERATION_TRACE_SCHEMA,
    SemanticOperation,
    operations_from_jsonl,
    operations_to_jsonl,
    replay_operation_jsonl,
    replay_operations,
)
from continuity.serialization import snapshot_core, snapshot_fingerprint


def operation_trace():
    evidence = Evidence(
        'e1', 'replica present', 'test', EvidenceAuthority.EXACT_OBSERVATION,
        EvidenceStatus.VALID, 1.0, frozenset({('state', 'x'), ('replica', 'rp')})
    )
    event = SemanticEvent('ev1', 'STATE_MATERIALIZED', 'state', 'x', frozenset({('replica', 'rp')}))
    return [
        SemanticOperation.build('op01', 'create_program', id_='p'),
        SemanticOperation.build('op02', 'create_session', id_='s', program_id='p'),
        SemanticOperation.build('op03', 'create_continuation', id_='c', session_id='s'),
        SemanticOperation.build('op04', 'create_request', id_='r', continuation_id='c'),
        SemanticOperation.build('op05', 'start_attempt', id_='a', request_id='r'),
        SemanticOperation.build('op06', 'set_attempt_execution', attempt_id='a', status=ExecutionStatus.RUNNING),
        SemanticOperation.build('op07', 'create_phase', id_='prefill', attempt_id='a', phase_type=PhaseType.PREFILL),
        SemanticOperation.build('op08', 'set_phase_status', phase_id='prefill', status=PhaseStatus.RUNNING),
        SemanticOperation.build('op09', 'complete_phase', phase_id='prefill'),
        SemanticOperation.build('op10', 'create_state', id_='x', origin_type='phase', origin_id='prefill'),
        SemanticOperation.build('op11', 'add_replica', id_='rp', state_id='x', location_id='worker-1'),
        SemanticOperation.build('op12', 'activate_initial_binding', id_='b1', subject_id='session:s', location_id='worker-1'),
        SemanticOperation.build('op13', 'record_evidence', e=evidence),
        SemanticOperation.build('op14', 'record_event', event=event),
    ]


def test_operation_jsonl_is_canonical_and_round_trips():
    operations = operation_trace()
    text = operations_to_jsonl(operations)
    assert operations_from_jsonl(text) == operations
    assert text == operations_to_jsonl(operations)
    for line in text.splitlines():
        assert json.loads(line)['schema'] == OPERATION_TRACE_SCHEMA


def test_same_operation_trace_produces_identical_snapshots():
    trace = operations_to_jsonl(operation_trace())
    first = ContinuityCore()
    second = ContinuityCore()
    replay_operation_jsonl(first, trace)
    replay_operation_jsonl(second, trace)
    InvariantOracle(first).assert_all()
    InvariantOracle(second).assert_all()
    assert snapshot_core(first) == snapshot_core(second)
    assert snapshot_fingerprint(first) == snapshot_fingerprint(second)


def test_operation_replay_matches_directly_executed_semantics():
    operations = operation_trace()
    direct = ContinuityCore()
    for operation in operations:
        getattr(direct, operation.action)(**operation.kwargs())
    replayed = ContinuityCore()
    replay_operations(replayed, operations)
    assert snapshot_core(replayed) == snapshot_core(direct)


def test_operation_order_is_semantically_significant():
    operations = operation_trace()
    invalid = [operations[1], operations[0], *operations[2:]]
    with pytest.raises(KeyError):
        replay_operations(ContinuityCore(), invalid)


def test_duplicate_operation_identity_is_rejected():
    operation = SemanticOperation.build('same', 'create_program', id_='p')
    with pytest.raises(ValueError):
        replay_operations(ContinuityCore(), [operation, operation])
    text = operations_to_jsonl([operation, operation])
    with pytest.raises(ValueError):
        operations_from_jsonl(text)


def test_unknown_operation_action_is_rejected():
    record = {
        'schema': OPERATION_TRACE_SCHEMA,
        'operation_id': 'op',
        'action': '__dict__',
        'arguments': {'$dict': []},
    }
    with pytest.raises(ValueError):
        operations_from_jsonl(json.dumps(record) + '\n')


def test_unknown_operation_schema_is_rejected():
    record = {'schema': 'future', 'operation_id': 'op', 'action': 'create_program', 'arguments': {'$dict': []}}
    with pytest.raises(ValueError):
        operations_from_jsonl(json.dumps(record) + '\n')
