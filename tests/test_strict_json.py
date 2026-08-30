import math
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
    op = SemanticOperation.build('op', 'finalize_request', request_id='r', output_id='o', now=value)
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


def test_snapshot_parser_rejects_non_finite_number():
    with pytest.raises(ValueError):
        restore_core('{"schema":"cadi.core.snapshot.v1","state":NaN}')


def test_event_jsonl_parser_rejects_non_finite_number():
    with pytest.raises(ValueError):
        events_from_jsonl('{"schema":"cadi.semantic-event.v1","event":Infinity}\n')
