from __future__ import annotations

import pytest

from simulator import InferenceCostWorkload
from simulator.inference_cost_runtime import (
    estimate_validated_runtime_cost,
    load_c64f_runtime_profiles,
)


@pytest.mark.parametrize("hardware_id", ["a100-80gb", "h100-80gb"])
def test_positive_decode_requires_positive_initial_context(hardware_id: str) -> None:
    profile = load_c64f_runtime_profiles()[hardware_id]
    with pytest.raises(ValueError, match="positive context"):
        estimate_validated_runtime_cost(profile, InferenceCostWorkload(0, 1, 0, 0))


@pytest.mark.parametrize("hardware_id", ["a100-80gb", "h100-80gb"])
def test_positive_decode_cannot_exceed_4096_total_tokens(hardware_id: str) -> None:
    profile = load_c64f_runtime_profiles()[hardware_id]
    estimate_validated_runtime_cost(profile, InferenceCostWorkload(4095, 1, 4095, 0))
    with pytest.raises(ValueError, match="max-model-length"):
        estimate_validated_runtime_cost(profile, InferenceCostWorkload(4096, 1, 4096, 0))
    with pytest.raises(ValueError, match="max-model-length"):
        estimate_validated_runtime_cost(profile, InferenceCostWorkload(4095, 2, 4095, 0))
