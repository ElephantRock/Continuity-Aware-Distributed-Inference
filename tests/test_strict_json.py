import math
import pytest

from continuity import ContinuityCore
from continuity.entities import Evidence, EvidenceAuthority, EvidenceStatus
from continuity.replay import SemanticOperation, operations_to_jsonl
from continuity.serialization import snapshot_core


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
