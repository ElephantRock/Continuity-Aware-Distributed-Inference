import pytest

from continuity import ContinuityCore
from continuity.entities import Evidence, EvidenceAuthority, EvidenceStatus
from continuity.replay import SemanticOperation, operations_from_jsonl, operations_to_jsonl
from continuity.serialization import events_from_jsonl, restore_core, snapshot_core


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_snapshot_rejects_non_finite_numbers(value):
    core = ContinuityCore()
    core.record_evidence(Evidence(
        'e', 'x', 'test', EvidenceAuthority.EXACT_OBSERVATION,
        EvidenceStatus.VALID, value, frozenset({('attempt', 'a')})
    ))
    with pytest.raises(ValueError):
        snapshot_core(core)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_operation_jsonl_rejects_non_finite_numbers(value):
    # Bypass SemanticOperation.build() deliberately so this test continues to
    # exercise the canonical JSON emitter's allow_nan=False boundary.
    op = SemanticOperation(
        id='op', action='finalize_request',
        arguments=(('now', value), ('output_id', 'o'), ('request_id', 'r')),
    )
    with pytest.raises(ValueError):
        operations_to_jsonl([op])


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_operation_jsonl_parser_rejects_non_finite_numbers(constant):
    text = (
        '{"schema":"cadi.semantic-operation.v1","operation_id":"op",'
        '"action":"finalize_request","arguments":{"$dict":['
        '["now",' + constant + '],["output_id","o"],["request_id","r"]]}}\n'
    )
    with pytest.raises(ValueError):
        operations_from_jsonl(text)


def test_operation_jsonl_parser_rejects_float_overflow_to_infinity():
    text = (
        '{"schema":"cadi.semantic-operation.v1","operation_id":"op",'
        '"action":"finalize_request","arguments":{"$dict":['
        '["now",-1e999],["output_id","o"],["request_id","r"]]}}\n'
    )
    with pytest.raises(ValueError):
        operations_from_jsonl(text)


def test_snapshot_parser_rejects_non_finite_number():
    with pytest.raises(ValueError):
        restore_core('{"schema":"cadi.core.snapshot.v1","state":NaN}')


def test_event_jsonl_parser_rejects_non_finite_number():
    with pytest.raises(ValueError):
        events_from_jsonl('{"schema":"cadi.semantic-event.v1","event":Infinity}\n')
