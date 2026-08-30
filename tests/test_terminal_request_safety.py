from dataclasses import replace

import pytest

from continuity import ContinuityCore
from continuity.entities import (
    Evidence, EvidenceAuthority, EvidenceStatus, RequestStatus,
)
from continuity.errors import InvalidTransition
from continuity.serialization import restore_core, snapshot_core


def _candidate_core():
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request("r", "c")
    core.start_attempt("a", "r")
    core.complete_attempt("a", True)
    core.record_evidence(Evidence(
        "e", "terminal output observed", "test",
        EvidenceAuthority.EXACT_OBSERVATION, EvidenceStatus.VALID, 1.0,
        frozenset({("attempt", "a")}), valid_until=100.0,
    ))
    core.create_output("o", "a", True, ["e"])
    return core


@pytest.mark.parametrize("status", [RequestStatus.FAILED, RequestStatus.CANCELLED])
def test_finalize_rejects_failed_or_cancelled_request(status):
    core = _candidate_core()
    core.requests["r"] = replace(core.requests["r"], status=status)
    with pytest.raises(InvalidTransition, match="cannot be finalized"):
        core.finalize_request("r", "o", now=10.0)


@pytest.mark.parametrize("status", [RequestStatus.FAILED, RequestStatus.CANCELLED])
def test_restore_rejects_terminal_request_with_current_attempt(status):
    core = ContinuityCore()
    core.create_program("p")
    core.create_session("s", "p")
    core.create_continuation("c", "s")
    core.create_request("r", "c")
    core.start_attempt("a", "r")
    core.requests["r"] = replace(core.requests["r"], status=status)
    with pytest.raises(ValueError, match="snapshot violates Continuity invariants"):
        restore_core(snapshot_core(core))
