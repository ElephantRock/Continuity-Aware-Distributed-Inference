import pytest

from continuity.replay import SemanticOperation


def test_operation_build_rejects_mutable_set_iterable_arguments():
    with pytest.raises(ValueError, match="argument 'evidence_ids' has invalid type"):
        SemanticOperation.build(
            'op', 'create_output',
            id_='o', attempt_id='a', terminal=True, evidence_ids={'e1'},
        )
