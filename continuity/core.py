from __future__ import annotations

import time
from typing import Iterable, Optional

from .core_base import ContinuityCore as _BaseContinuityCore
from .entities import Evidence, EvidenceStatus, ReconcileOutcome
from .errors import InsufficientEvidence


class ContinuityCore(_BaseContinuityCore):
    """C1 semantic core with explicit structured-Evidence contradiction safety."""

    def _has_conflicting_structured_claims(
        self,
        action: str,
        evs: Iterable[Evidence],
        *,
        now: float,
        max_age: Optional[float] = None,
    ) -> bool:
        req = self.REQUIRED_AUTHORITY[action]
        values_by_key: dict[str, set[str]] = {}
        for e in evs:
            if e.claim_key is None:
                continue
            if e.status != EvidenceStatus.VALID:
                continue
            if e.authority < req:
                continue
            if e.valid_until is not None and now > e.valid_until:
                continue
            if max_age is not None and now - e.observed_at > max_age:
                continue
            values = values_by_key.setdefault(e.claim_key, set())
            values.add(e.claim_value)  # claim_value is paired with claim_key by Evidence.
            if len(values) > 1:
                return True
        return False

    def require_evidence(
        self,
        action: str,
        evidence_ids: Iterable[str],
        *,
        now: Optional[float] = None,
        required_scope: set[tuple[str, str]] | None = None,
        max_age: Optional[float] = None,
    ) -> list[Evidence]:
        now = time.time() if now is None else now
        req = self.REQUIRED_AUTHORITY[action]
        evs = [self.evidence[eid] for eid in evidence_ids if eid in self.evidence]
        if action in self.CORRECTNESS_SENSITIVE_EVIDENCE_ACTIONS:
            if any(e.status == EvidenceStatus.AMBIGUOUS for e in evs):
                raise InsufficientEvidence(f"ambiguous Evidence for {action}")
            if self._has_conflicting_structured_claims(
                action, evs, now=now, max_age=max_age
            ):
                raise InsufficientEvidence(f"contradictory Evidence for {action}")
        good = []
        for e in evs:
            if e.status != EvidenceStatus.VALID:
                continue
            if e.authority < req:
                continue
            if e.valid_until is not None and now > e.valid_until:
                continue
            if max_age is not None and now - e.observed_at > max_age:
                continue
            if required_scope and not required_scope.issubset(set(e.scope)):
                continue
            good.append(e)
        if not good:
            raise InsufficientEvidence(f"insufficient Evidence for {action}")
        return good

    def reconcile(
        self,
        action: str,
        evidence_ids: Iterable[str],
        *,
        now: Optional[float] = None,
        required_scope: set[tuple[str, str]] | None = None,
    ) -> ReconcileOutcome:
        now = time.time() if now is None else now
        ids = tuple(evidence_ids)
        evs = [self.evidence[eid] for eid in ids if eid in self.evidence]
        if any(e.status == EvidenceStatus.AMBIGUOUS for e in evs):
            return ReconcileOutcome.AMBIGUOUS
        if self._has_conflicting_structured_claims(action, evs, now=now):
            return ReconcileOutcome.AMBIGUOUS
        try:
            self.require_evidence(
                action, ids, now=now, required_scope=required_scope
            )
            return ReconcileOutcome.MATCHED
        except InsufficientEvidence:
            return ReconcileOutcome.WAIT
