from __future__ import annotations

from continuity.core import ContinuityCore
from continuity.entities import Evidence, EvidenceAuthority, EvidenceStatus

from . import _evidence_safety_adversarial_pre_repair as _base
from ._evidence_safety_adversarial_pre_repair import *
from .evidence_safety import S4_E0_ATTEMPT_ID


_ORIGINAL_BUILD_EVIDENCE = _base._build_evidence
_ORIGINAL_EVIDENCE_DICT = _base._evidence_dict


def _record(
    core: ContinuityCore,
    *,
    evidence_id: str,
    claim: str,
    authority: EvidenceAuthority = EvidenceAuthority.EXACT_OBSERVATION,
    status: EvidenceStatus = EvidenceStatus.VALID,
    observed_at: float = 1.0,
    scope: frozenset[tuple[str, str]] | None = None,
    valid_until: float | None = None,
    derived_from: frozenset[str] = frozenset(),
    derivation_rule: str | None = None,
    claim_key: str | None = None,
    claim_value: str | None = None,
) -> Evidence:
    return core.record_evidence(
        Evidence(
            id=evidence_id,
            claim=claim,
            source="C4.5b adversarial Evidence fixture",
            authority=authority,
            status=status,
            observed_at=observed_at,
            scope=(
                frozenset({("attempt", S4_E0_ATTEMPT_ID)})
                if scope is None
                else scope
            ),
            valid_until=valid_until,
            derived_from=derived_from,
            derivation_rule=derivation_rule,
            claim_key=claim_key,
            claim_value=claim_value,
        )
    )


def _evidence_dict(evidence: Evidence) -> dict[str, object]:
    result = _ORIGINAL_EVIDENCE_DICT(evidence)
    result["claim_key"] = evidence.claim_key
    result["claim_value"] = evidence.claim_value
    return result


def _build_evidence(
    core: ContinuityCore,
    manifest: EvidenceAdversarialManifest,
) -> tuple[str, ...]:
    prefix = f"S4:ADV:{manifest.case_id}"
    good_scope = frozenset({("attempt", S4_E0_ATTEMPT_ID)})
    claim_key = f"attempt:{S4_E0_ATTEMPT_ID}:terminal-outcome"

    if manifest.mode is EvidenceAdversarialMode.CONTRADICTORY_VALID_EXACT:
        success = _record(
            core,
            evidence_id=f"{prefix}:success",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=good_scope,
            claim_key=claim_key,
            claim_value="SUCCEEDED",
        )
        failure = _record(
            core,
            evidence_id=f"{prefix}:failure",
            claim="attempt-terminal-outcome=FAILED",
            scope=good_scope,
            claim_key=claim_key,
            claim_value="FAILED",
        )
        return (success.id, failure.id)

    if manifest.mode is EvidenceAdversarialMode.IDENTICAL_VALID_EXACT_MULTI:
        one = _record(
            core,
            evidence_id=f"{prefix}:one",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=good_scope,
            claim_key=claim_key,
            claim_value="SUCCEEDED",
        )
        two = _record(
            core,
            evidence_id=f"{prefix}:two",
            claim="attempt-terminal-outcome=SUCCEEDED",
            scope=good_scope,
            claim_key=claim_key,
            claim_value="SUCCEEDED",
        )
        return (one.id, two.id)

    return _ORIGINAL_BUILD_EVIDENCE(core, manifest)


# Patch only the behavior-independent fixture construction / serialization hooks used
# by the preserved pre-repair harness. All manifests, oracle rules, EventIDs,
# policy ordering, classification, and summary logic remain byte-identical in the
# preserved base module.
_base._record = _record
_base._build_evidence = _build_evidence
_base._evidence_dict = _evidence_dict

# Private hook used by the anti-false-zero regression test.
_run_s4_adversarial_trial = _base._run_s4_adversarial_trial
