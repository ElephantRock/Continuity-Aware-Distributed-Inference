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


def finalization_trace():
    evidence = Evidence(
        'terminal-evidence', 'terminal output observed', 'test',
        EvidenceAuthority.EXACT_OBSERVATION, EvidenceStatus.VALID, 10.0,
        frozenset({('attempt', 'a')}), valid_until=100.0,
    )
    return [
        SemanticOperation.build('op01', 'create_program', id_='p'),
        SemanticOperation.build('op02', 'create_session', id_='s', program_id='p'),
        SemanticOperation.build('op03', 'create_continuation', id_='c', session_id='s'),
        SemanticOperation.build('op04', 'create_request', id_='r', continuation_id='c'),
        SemanticOperation.build('op05', 'start_attempt', id_='a', request_id='r'),
        SemanticOperation.build('op06', 'complete_attempt', attempt_id='a', succeeded=True),
        SemanticOperation.build('op07', 'record_evidence', e=evidence),
        SemanticOperation.build(
            'op08', 'create_output', id_='o', attempt_id='a', terminal=True,
            evidence_ids=['terminal-evidence'],
        ),
        SemanticOperation.build('op09', 'finalize_request', request_id='r', output_id='o', now=50.0),
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


def test_time_sensitive_replay_operations_require_explicit_now():
    with pytest.raises(ValueError):
        SemanticOperation.build('finalize', 'finalize_request', request_id='r', output_id='o')
    with pytest.raises(ValueError):
        SemanticOperation.build('migrate', 'commit_migration', binding_id='b', evidence_ids=[])


def test_time_sensitive_record_without_now_is_rejected_during_parse():
    record = {
        'schema': OPERATION_TRACE_SCHEMA,
        'operation_id': 'finalize',
        'action': 'finalize_request',
        'arguments': {
            '$dict': [
                ['output_id', 'o'],
                ['request_id', 'r'],
            ]
        },
    }
    with pytest.raises(ValueError):
        operations_from_jsonl(json.dumps(record) + '\n')


def test_explicit_replay_time_makes_expiring_evidence_deterministic():
    trace = operations_to_jsonl(finalization_trace())
    first = ContinuityCore()
    second = ContinuityCore()
    replay_operation_jsonl(first, trace)
    replay_operation_jsonl(second, trace)
    assert first.requests['r'].committed_attempt_id == 'a'
    assert second.requests['r'].committed_attempt_id == 'a'
    assert snapshot_core(first) == snapshot_core(second)
    InvariantOracle(first).assert_all()
    InvariantOracle(second).assert_all()


def test_operation_jsonl_rejects_forged_evidence_authority_type():
    evidence = Evidence(
        'forged', 'terminal output observed', 'external',
        EvidenceAuthority.EXACT_OBSERVATION, EvidenceStatus.VALID, 1.0,
        frozenset({('attempt', 'a')}),
    )
    operation = SemanticOperation.build('op', 'record_evidence', e=evidence)
    record = json.loads(operations_to_jsonl([operation]).strip())
    encoded_evidence = next(
        value for key, value in record['arguments']['$dict'] if key == 'e'
    )
    encoded_evidence['fields']['authority'] = 999

    with pytest.raises(ValueError, match="argument 'e' has invalid type"):
        operations_from_jsonl(json.dumps(record) + '\n')


def test_operation_build_rejects_invalid_nested_dataclass_member_types():
    forged = Evidence(
        'forged', 'claim', 'external',
        999, EvidenceStatus.VALID, 1.0, frozenset({('attempt', 'a')}),
    )
    with pytest.raises(ValueError, match="argument 'e' has invalid type"):
        SemanticOperation.build('op', 'record_evidence', e=forged)


@pytest.mark.parametrize(
    ('action', 'arguments'),
    [
        ('create_program', {'id_': 123}),
        ('set_attempt_execution', {'attempt_id': 'a', 'status': 'RUNNING'}),
        ('create_phase', {'id_': 'f', 'attempt_id': 'a', 'phase_type': 7}),
    ],
)
def test_operation_build_rejects_wrong_action_argument_types(action, arguments):
    with pytest.raises(ValueError, match='has invalid type'):
        SemanticOperation.build('op', action, **arguments)


def test_replay_revalidates_direct_semantic_operation_arguments_before_dispatch():
    malformed = SemanticOperation(
        id='op', action='create_program', arguments=(('id_', 123),)
    )
    core = ContinuityCore()
    with pytest.raises(ValueError, match="argument 'id_' has invalid type"):
        replay_operations(core, [malformed])
    assert core.programs == {}


def test_operation_emitter_rejects_direct_invalid_operation_types():
    malformed = SemanticOperation(
        id='op', action='create_program', arguments=(('id_', 123),)
    )
    with pytest.raises(ValueError, match="argument 'id_' has invalid type"):
        operations_to_jsonl([malformed])


def test_operation_emitter_rejects_duplicate_argument_names():
    malformed = SemanticOperation(
        id='op', action='create_program',
        arguments=(('id_', 'p'), ('id_', 'q')),
    )
    with pytest.raises(ValueError, match='duplicate semantic operation argument name'):
        operations_to_jsonl([malformed])


def test_operation_build_rejects_one_shot_iterable_arguments():
    parent_ids = (item for item in ['c0'])
    with pytest.raises(ValueError, match="argument 'parent_ids' has invalid type"):
        SemanticOperation.build(
            'op', 'create_continuation',
            id_='c1', session_id='s', parent_ids=parent_ids,
        )
