from __future__ import annotations

from typing import Sequence

from . import attempt_fencing_sequences_base as _base
from .attempt_fencing_sequences_base import *  # noqa: F401,F403


def _validated_manifest(
    manifest: AttemptFencingSequenceManifest,
) -> AttemptFencingSequenceManifest:
    """Normalize generated case templates into executable exogenous manifests.

    Repeated stale authority presentations are distinct correctness-sensitive
    presentations, so each must carry a distinct Evidence/Output identity even
    when it targets the same stale Attempt. Their denominator EventIDs are then
    ordered by deterministic delivery order, not template declaration order.
    """

    stale_ids = frozenset(manifest.stale_presentation_event_ids)
    seen_evidence_ids: set[str] = set()
    seen_output_ids: set[str] = set()
    actions: list[SequenceAction] = []

    for action in manifest.actions:
        payload = action.payload_dict
        if action.kind in {
            SequenceActionKind.OBSERVE,
            SequenceActionKind.OBSERVE_DUPLICATE,
        }:
            evidence_id = payload["evidence_id"]
            output_id = payload["output_id"]
            if action.event_id in stale_ids and (
                evidence_id in seen_evidence_ids or output_id in seen_output_ids
            ):
                payload["evidence_id"] = f"{evidence_id}:presentation:{action.event_id}"
                payload["output_id"] = f"{output_id}:presentation:{action.event_id}"
            seen_evidence_ids.add(payload["evidence_id"])
            seen_output_ids.add(payload["output_id"])

        actions.append(
            SequenceAction(
                kind=action.kind,
                at=action.at,
                event_id=action.event_id,
                payload=tuple(payload.items()),
            )
        )

    ordered_stale_ids = tuple(
        action.event_id for action in actions if action.event_id in stale_ids
    )
    return AttemptFencingSequenceManifest(
        case_id=manifest.case_id,
        seed=manifest.seed,
        pressure_labels=manifest.pressure_labels,
        actions=tuple(actions),
        stale_presentation_event_ids=ordered_stale_ids,
        expected_committed_attempt_id=manifest.expected_committed_attempt_id,
        schema=manifest.schema,
    )


S1_SEQUENCE_MANIFESTS = tuple(
    _validated_manifest(manifest) for manifest in _base.S1_SEQUENCE_MANIFESTS
)


def run_s1_sequence_paired(
    manifests: Sequence[AttemptFencingSequenceManifest] = S1_SEQUENCE_MANIFESTS,
) -> AttemptFencingSequenceEvaluation:
    return _base.run_s1_sequence_paired(manifests)
