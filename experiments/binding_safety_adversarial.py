from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from continuity.entities import Evidence, EvidenceAuthority, EvidenceStatus
from continuity.errors import ContinuityError
from continuity.invariants import InvariantOracle
from simulator import MigrationDecision, PolicyID

from .binding_safety import (
    S3_E0_SUBJECT_ID,
    BindingPresentationResult,
    _attempt_binding_commit,
    _authority_snapshot,
    _b4_migration_decision,
    _record_commit_evidence,
    _scaffold_core,
)
from .correctness import (
    CorrectnessEvaluationRecord,
    CorrectnessMetric,
    CorrectnessSummary,
    ExplicitNonSuccess,
    MetricOpportunityScope,
    ResultEvidenceProvenance,
    SemanticResult,
    ValidationEvidenceLevel,
    summarize_correctness,
)


S3_ADVERSARIAL_SCHEMA = "cadi.c4.4b.binding-safety-adversarial-e0.v1"
S3_ADVERSARIAL_COHORT_ID = "C4.4b:S3:EV0"


class BindingPressureFamily(str, Enum):
    EPOCH_DEPTH_REORDER = "A_EPOCH_DEPTH_REORDER"
    CONCURRENT_CANDIDATES = "B_CONCURRENT_CANDIDATES"
    EVIDENCE_SUFFICIENCY = "C_EVIDENCE_SUFFICIENCY"
    PARTIAL_FAILOVER = "D_PARTIAL_FAILOVER"
    REPLAY_REPEAT = "E_REPLAY_REPEAT"


class BindingEvidenceMode(str, Enum):
    NONE = "NONE"
    GOOD = "GOOD"
    WRONG_BINDING_SCOPE = "WRONG_BINDING_SCOPE"
    WRONG_EPOCH_SCOPE = "WRONG_EPOCH_SCOPE"


@dataclass(frozen=True, slots=True)
class BindingAdversarialManifest:
    case_id: str
    pressure_family: BindingPressureFamily
    setup_profile: str
    presentation_binding_id: str
    evidence_mode: BindingEvidenceMode
    reconciliation: str
    expected_binding_id: str
    expected_epoch: int
    fault_class: str | None
    sbdr_event_id: str | None
    explicit_wait: bool = False

    def __post_init__(self) -> None:
        for value, name in (
            (self.case_id, "case_id"),
            (self.setup_profile, "setup_profile"),
            (self.presentation_binding_id, "presentation_binding_id"),
            (self.reconciliation, "reconciliation"),
            (self.expected_binding_id, "expected_binding_id"),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.pressure_family, BindingPressureFamily):
            raise TypeError("pressure_family must be BindingPressureFamily")
        if not isinstance(self.evidence_mode, BindingEvidenceMode):
            raise TypeError("evidence_mode must be BindingEvidenceMode")
        if not isinstance(self.expected_epoch, int) or isinstance(self.expected_epoch, bool):
            raise TypeError("expected_epoch must be int")
        if self.expected_epoch < 1:
            raise ValueError("expected_epoch must be positive")
        if self.fault_class is not None and (
            not isinstance(self.fault_class, str) or not self.fault_class
        ):
            raise ValueError("fault_class must be a non-empty string or None")
        if self.sbdr_event_id is not None and (
            not isinstance(self.sbdr_event_id, str) or not self.sbdr_event_id
        ):
            raise ValueError("sbdr_event_id must be a non-empty string or None")
        if (self.fault_class is None) != (self.sbdr_event_id is None):
            raise ValueError("faulted cases require exactly one SBDR EventID")
        if not isinstance(self.explicit_wait, bool):
            raise TypeError("explicit_wait must be bool")
        if self.explicit_wait and self.fault_class is None:
            raise ValueError("explicit_wait is only meaningful for faulted cases")

    @property
    def fault_id(self) -> str | None:
        return None if self.fault_class is None else f"S3:ADV:{self.case_id}"


S3_ADVERSARIAL_MANIFESTS = (
    BindingAdversarialManifest(
        "A-TWO-HOP-EPOCH1-LATE",
        BindingPressureFamily.EPOCH_DEPTH_REORDER,
        "SEQUENTIAL_TWO",
        "b1",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b3",
        3,
        "epoch-1 old owner after two committed migrations",
        "S3:ADV:A1:late-epoch1",
    ),
    BindingAdversarialManifest(
        "A-TWO-HOP-EPOCH2-LATE",
        BindingPressureFamily.EPOCH_DEPTH_REORDER,
        "SEQUENTIAL_TWO",
        "b2",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b3",
        3,
        "epoch-2 old owner after a later committed migration",
        "S3:ADV:A2:late-epoch2",
    ),
    BindingAdversarialManifest(
        "A-THREE-HOP-EPOCH1-LATE",
        BindingPressureFamily.EPOCH_DEPTH_REORDER,
        "SEQUENTIAL_THREE",
        "b1",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b4",
        4,
        "epoch-1 old owner after three committed migrations",
        "S3:ADV:A3:late-epoch1-depth3",
    ),
    BindingAdversarialManifest(
        "B-THREE-CANDIDATES-LOWEST-WINS",
        BindingPressureFamily.CONCURRENT_CANDIDATES,
        "CONCURRENT_THREE_LOWEST_WINS",
        "b4",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b2",
        2,
        "three same-base candidates with lower allocated candidate committed first",
        "S3:ADV:B1:stale-high-loser",
    ),
    BindingAdversarialManifest(
        "B-HIGHER-CANDIDATE-WINS-FIRST",
        BindingPressureFamily.CONCURRENT_CANDIDATES,
        "CONCURRENT_TWO_HIGHER_WINS",
        "b2",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b3",
        3,
        "higher allocated concurrent candidate wins before lower candidate",
        "S3:ADV:B2:stale-low-loser",
    ),
    BindingAdversarialManifest(
        "B-THREE-CANDIDATES-MIDDLE-WINS",
        BindingPressureFamily.CONCURRENT_CANDIDATES,
        "CONCURRENT_THREE_MIDDLE_WINS",
        "b4",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b3",
        3,
        "middle allocated candidate wins among three same-base candidates",
        "S3:ADV:B3:stale-high-loser",
    ),
    BindingAdversarialManifest(
        "C-MISSING-COMMIT-EVIDENCE",
        BindingPressureFamily.EVIDENCE_SUFFICIENCY,
        "SINGLE_CANDIDATE",
        "b2",
        BindingEvidenceMode.NONE,
        "WAIT",
        "b1",
        1,
        "migration commit attempted without Evidence",
        "S3:ADV:C1:missing-evidence",
        explicit_wait=True,
    ),
    BindingAdversarialManifest(
        "C-WRONG-BINDING-EVIDENCE-SCOPE",
        BindingPressureFamily.EVIDENCE_SUFFICIENCY,
        "SINGLE_CANDIDATE",
        "b2",
        BindingEvidenceMode.WRONG_BINDING_SCOPE,
        "WAIT",
        "b1",
        1,
        "EXACT_OBSERVATION Evidence names the wrong Binding",
        "S3:ADV:C2:wrong-binding-scope",
        explicit_wait=True,
    ),
    BindingAdversarialManifest(
        "C-WRONG-EPOCH-EVIDENCE-SCOPE",
        BindingPressureFamily.EVIDENCE_SUFFICIENCY,
        "SINGLE_CANDIDATE",
        "b2",
        BindingEvidenceMode.WRONG_EPOCH_SCOPE,
        "WAIT",
        "b1",
        1,
        "EXACT_OBSERVATION Evidence names the wrong Binding epoch",
        "S3:ADV:C3:wrong-epoch-scope",
        explicit_wait=True,
    ),
    BindingAdversarialManifest(
        "C-VALID-EVIDENCE-CONTROL",
        BindingPressureFamily.EVIDENCE_SUFFICIENCY,
        "SINGLE_CANDIDATE",
        "b2",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b2",
        2,
        None,
        None,
    ),
    BindingAdversarialManifest(
        "D-DESTINATION-FAIL-BEFORE-COMMIT",
        BindingPressureFamily.PARTIAL_FAILOVER,
        "DESTINATION_FAIL_PRECOMMIT",
        "b2",
        BindingEvidenceMode.NONE,
        "WAIT",
        "b1",
        1,
        "destination disappears before semantic migration commit",
        "S3:ADV:D1:destination-fail",
        explicit_wait=True,
    ),
    BindingAdversarialManifest(
        "D-PARTIAL-THEN-ALTERNATE-COMMIT",
        BindingPressureFamily.PARTIAL_FAILOVER,
        "PARTIAL_THEN_ALTERNATE",
        "b2",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b3",
        3,
        "partial candidate later presents after alternate migration committed",
        "S3:ADV:D2:late-partial-candidate",
    ),
    BindingAdversarialManifest(
        "E-DUPLICATE-WINNER-COMMIT",
        BindingPressureFamily.REPLAY_REPEAT,
        "COMMIT_B2",
        "b2",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b2",
        2,
        "duplicate migration commit presentation after authoritative commit",
        "S3:ADV:E1:duplicate-winner",
    ),
    BindingAdversarialManifest(
        "E-REPEATED-STALE-LOSER",
        BindingPressureFamily.REPLAY_REPEAT,
        "REJECT_B3_ONCE",
        "b3",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b2",
        2,
        "repeated stale loser presentation after prior rejection",
        "S3:ADV:E2:repeated-stale-loser",
    ),
    BindingAdversarialManifest(
        "E-MULTIHOP-OLD-OWNER-REPLAY",
        BindingPressureFamily.REPLAY_REPEAT,
        "REJECT_B1_AFTER_DEPTH3_ONCE",
        "b1",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b4",
        4,
        "repeated epoch-1 old-owner replay after three epoch advances",
        "S3:ADV:E3:repeated-old-owner",
    ),
    BindingAdversarialManifest(
        "E-SEQUENTIAL-NEXT-EPOCH-CONTROL",
        BindingPressureFamily.REPLAY_REPEAT,
        "COMMIT_B2_THEN_PROPOSE_B3",
        "b3",
        BindingEvidenceMode.GOOD,
        "MATCHED",
        "b3",
        3,
        None,
        None,
    ),
)
S3_ADVERSARIAL_CASE_IDS = tuple(item.case_id for item in S3_ADVERSARIAL_MANIFESTS)
_MANIFEST_BY_ID: Mapping[str, BindingAdversarialManifest] = {
    item.case_id: item for item in S3_ADVERSARIAL_MANIFESTS
}


@dataclass(frozen=True, slots=True)
class BindingAdversarialTrial:
    policy_id: PolicyID
    case_id: str
    evaluation: CorrectnessEvaluationRecord
    presentation: BindingPresentationResult
    policy_migration_decisions: tuple[MigrationDecision, ...]
    setup_records: tuple[Mapping[str, Any], ...]
    expected_binding_id: str
    expected_epoch: int
    final_binding_id: str
    final_epoch: int
    injected_divergence: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.case_id not in S3_ADVERSARIAL_CASE_IDS:
            raise ValueError("case_id must be canonical C4.4b case")
        if self.evaluation.policy_id is not self.policy_id:
            raise ValueError("evaluation policy must match trial policy")
        if self.evaluation.scenario_id != self.case_id:
            raise ValueError("evaluation scenario must match case_id")
        if not isinstance(self.presentation, BindingPresentationResult):
            raise TypeError("presentation must be BindingPresentationResult")
        if self.policy_id is not PolicyID.B4 and self.policy_migration_decisions:
            raise ValueError("only B4 has the frozen migration-admission surface")
        if not all(isinstance(item, MigrationDecision) for item in self.policy_migration_decisions):
            raise TypeError("policy_migration_decisions must contain MigrationDecision")
        if not isinstance(self.injected_divergence, bool):
            raise TypeError("injected_divergence must be bool")


@dataclass(frozen=True, slots=True)
class BindingAdversarialEvaluation:
    trials: tuple[BindingAdversarialTrial, ...]
    summary: CorrectnessSummary

    def __post_init__(self) -> None:
        expected = tuple(
            (manifest.case_id, policy_id)
            for manifest in S3_ADVERSARIAL_MANIFESTS
            for policy_id in PolicyID
        )
        actual = tuple((trial.case_id, trial.policy_id) for trial in self.trials)
        if actual != expected:
            raise ValueError("C4.4b trials must use canonical case then B0-B4 ordering")
        if not isinstance(self.summary, CorrectnessSummary):
            raise TypeError("summary must be CorrectnessSummary")


def _record_custom_evidence(
    core: Any,
    *,
    evidence_id: str,
    binding_id: str,
    mode: BindingEvidenceMode,
    observed_at: float,
) -> str:
    binding = core.bindings[binding_id]
    scope_binding = binding.id
    scope_epoch = binding.epoch
    if mode is BindingEvidenceMode.WRONG_BINDING_SCOPE:
        scope_binding = "b1" if binding.id != "b1" else "not-current-binding"
    elif mode is BindingEvidenceMode.WRONG_EPOCH_SCOPE:
        scope_epoch = binding.epoch + 100
    elif mode is not BindingEvidenceMode.GOOD:
        raise ValueError("custom evidence requires GOOD or a wrong-scope mode")
    core.record_evidence(
        Evidence(
            id=evidence_id,
            claim="migration commit readiness",
            source="C4.4b deterministic adversarial fixture",
            authority=EvidenceAuthority.EXACT_OBSERVATION,
            status=EvidenceStatus.VALID,
            observed_at=observed_at,
            scope=frozenset(
                {
                    ("binding", scope_binding),
                    ("epoch", str(scope_epoch)),
                }
            ),
        )
    )
    return evidence_id


def _commit_setup(core: Any, binding_id: str, label: str, now: float) -> Mapping[str, Any]:
    evidence_id = _record_commit_evidence(
        core,
        binding_id=binding_id,
        evidence_id=f"S3:ADV:setup:{label}:{binding_id}",
        observed_at=now,
    )
    before = _authority_snapshot(core)
    core.commit_migration(binding_id, (evidence_id,), now=now)
    InvariantOracle(core).assert_all()
    after = _authority_snapshot(core)
    if after == before:
        raise AssertionError("setup migration must change authoritative Binding")
    return {
        "kind": "SETUP_COMMIT",
        "binding_id": binding_id,
        "evidence_id": evidence_id,
        "before": before,
        "after": after,
    }


def _reject_setup_presentation(
    core: Any,
    *,
    binding_id: str,
    evidence_id: str,
    now: float,
) -> Mapping[str, Any]:
    before = _authority_snapshot(core)
    error_type: str | None = None
    try:
        core.commit_migration(binding_id, (evidence_id,), now=now)
    except ContinuityError as exc:
        error_type = type(exc).__name__
    after = _authority_snapshot(core)
    if error_type is None or after != before:
        raise AssertionError("setup stale presentation must be rejected without authority change")
    InvariantOracle(core).assert_all()
    return {
        "kind": "SETUP_REJECTED_PRESENTATION",
        "binding_id": binding_id,
        "evidence_id": evidence_id,
        "error_type": error_type,
        "before": before,
        "after": after,
    }


def _propose_begin(core: Any, binding_id: str, location_id: str) -> Mapping[str, Any]:
    binding = core.propose_binding(binding_id, S3_E0_SUBJECT_ID, location_id)
    core.begin_migration(binding_id)
    return {
        "kind": "CANDIDATE",
        "binding_id": binding.id,
        "location_id": binding.location_id,
        "base_epoch": binding.base_epoch,
        "epoch": binding.epoch,
    }


def _setup_profile(core: Any, manifest: BindingAdversarialManifest) -> tuple[Mapping[str, Any], ...]:
    records: list[Mapping[str, Any]] = []
    profile = manifest.setup_profile

    if profile == "SINGLE_CANDIDATE":
        records.append(_propose_begin(core, "b2", "w2"))

    elif profile == "SEQUENTIAL_TWO":
        records.append(_propose_begin(core, "b2", "w2"))
        records.append(_commit_setup(core, "b2", manifest.case_id, 2.0))
        records.append(_propose_begin(core, "b3", "w3"))
        records.append(_commit_setup(core, "b3", manifest.case_id, 3.0))

    elif profile == "SEQUENTIAL_THREE":
        records.append(_propose_begin(core, "b2", "w2"))
        records.append(_commit_setup(core, "b2", manifest.case_id, 2.0))
        records.append(_propose_begin(core, "b3", "w3"))
        records.append(_commit_setup(core, "b3", manifest.case_id, 3.0))
        records.append(_propose_begin(core, "b4", "w4"))
        records.append(_commit_setup(core, "b4", manifest.case_id, 4.0))

    elif profile == "CONCURRENT_THREE_LOWEST_WINS":
        records.extend(
            (
                _propose_begin(core, "b2", "w2"),
                _propose_begin(core, "b3", "w3"),
                _propose_begin(core, "b4", "w4"),
            )
        )
        records.append(_commit_setup(core, "b2", manifest.case_id, 2.0))

    elif profile == "CONCURRENT_TWO_HIGHER_WINS":
        records.extend(
            (
                _propose_begin(core, "b2", "w2"),
                _propose_begin(core, "b3", "w3"),
            )
        )
        records.append(_commit_setup(core, "b3", manifest.case_id, 2.0))

    elif profile == "CONCURRENT_THREE_MIDDLE_WINS":
        records.extend(
            (
                _propose_begin(core, "b2", "w2"),
                _propose_begin(core, "b3", "w3"),
                _propose_begin(core, "b4", "w4"),
            )
        )
        records.append(_commit_setup(core, "b3", manifest.case_id, 2.0))

    elif profile == "DESTINATION_FAIL_PRECOMMIT":
        records.append(_propose_begin(core, "b2", "w2"))
        records.append(
            {
                "kind": "PHYSICAL_DESTINATION_FAILURE",
                "binding_id": "b2",
                "location_id": "w2",
                "semantic_authority_changed": False,
            }
        )

    elif profile == "PARTIAL_THEN_ALTERNATE":
        records.append(_propose_begin(core, "b2", "w2"))
        records.append(
            {
                "kind": "PARTIAL_MATERIALIZATION",
                "binding_id": "b2",
                "location_id": "w2",
                "semantic_authority_changed": False,
            }
        )
        records.append(_propose_begin(core, "b3", "w3"))
        records.append(_commit_setup(core, "b3", manifest.case_id, 2.0))

    elif profile == "COMMIT_B2":
        records.append(_propose_begin(core, "b2", "w2"))
        records.append(_commit_setup(core, "b2", manifest.case_id, 2.0))

    elif profile == "REJECT_B3_ONCE":
        records.extend(
            (
                _propose_begin(core, "b2", "w2"),
                _propose_begin(core, "b3", "w3"),
            )
        )
        records.append(_commit_setup(core, "b2", manifest.case_id, 2.0))
        evidence_id = _record_commit_evidence(
            core,
            binding_id="b3",
            evidence_id=f"S3:ADV:setup:{manifest.case_id}:b3-stale",
            observed_at=2.5,
        )
        records.append(
            _reject_setup_presentation(
                core,
                binding_id="b3",
                evidence_id=evidence_id,
                now=2.5,
            )
        )

    elif profile == "REJECT_B1_AFTER_DEPTH3_ONCE":
        records.extend(_setup_profile(core, BindingAdversarialManifest(
            case_id=f"{manifest.case_id}:inner",
            pressure_family=manifest.pressure_family,
            setup_profile="SEQUENTIAL_THREE",
            presentation_binding_id="b1",
            evidence_mode=BindingEvidenceMode.GOOD,
            reconciliation="MATCHED",
            expected_binding_id="b4",
            expected_epoch=4,
            fault_class=None,
            sbdr_event_id=None,
        )))
        evidence_id = _record_custom_evidence(
            core,
            evidence_id=f"S3:ADV:setup:{manifest.case_id}:b1-old",
            binding_id="b1",
            mode=BindingEvidenceMode.GOOD,
            observed_at=4.5,
        )
        records.append(
            _reject_setup_presentation(
                core,
                binding_id="b1",
                evidence_id=evidence_id,
                now=4.5,
            )
        )

    elif profile == "COMMIT_B2_THEN_PROPOSE_B3":
        records.append(_propose_begin(core, "b2", "w2"))
        records.append(_commit_setup(core, "b2", manifest.case_id, 2.0))
        records.append(_propose_begin(core, "b3", "w3"))

    else:
        raise AssertionError(f"unknown C4.4b setup profile: {profile}")

    return tuple(records)


def _evidence_for_presentation(
    core: Any,
    manifest: BindingAdversarialManifest,
) -> tuple[str, ...]:
    if manifest.evidence_mode is BindingEvidenceMode.NONE:
        return ()
    evidence_id = f"S3:ADV:{manifest.case_id}:presentation-evidence"
    if manifest.evidence_mode is BindingEvidenceMode.GOOD:
        _record_commit_evidence(
            core,
            binding_id=manifest.presentation_binding_id,
            evidence_id=evidence_id,
            observed_at=10.0,
        )
    else:
        _record_custom_evidence(
            core,
            evidence_id=evidence_id,
            binding_id=manifest.presentation_binding_id,
            mode=manifest.evidence_mode,
            observed_at=10.0,
        )
    return (evidence_id,)


def _run_adversarial_trial(
    policy_id: PolicyID,
    case_id: str,
    *,
    inject_divergence: bool = False,
) -> BindingAdversarialTrial:
    if not isinstance(policy_id, PolicyID):
        raise TypeError("policy_id must be PolicyID")
    manifest = _MANIFEST_BY_ID.get(case_id)
    if manifest is None:
        raise ValueError(f"case_id must be one of {S3_ADVERSARIAL_CASE_IDS!r}")
    if inject_divergence and manifest.fault_id is None:
        raise ValueError("divergence injection requires a faulted case")

    core = _scaffold_core()
    setup_records = _setup_profile(core, manifest)
    setup_final = _authority_snapshot(core)

    decision = _b4_migration_decision(
        core,
        policy_id,
        binding_id=manifest.presentation_binding_id,
        reconciliation=manifest.reconciliation,
    )
    decisions = () if decision is None else (decision,)
    evidence_ids = _evidence_for_presentation(core, manifest)

    presentation = _attempt_binding_commit(
        core,
        event_id=(
            manifest.sbdr_event_id
            if manifest.sbdr_event_id is not None
            else f"S3:ADV:{manifest.case_id}:positive-control-presentation"
        ),
        binding_id=manifest.presentation_binding_id,
        evidence_ids=evidence_ids,
        expected_binding_id=manifest.expected_binding_id,
        expected_epoch=manifest.expected_epoch,
        now=10.0,
        inject_divergence=inject_divergence,
    )
    final = _authority_snapshot(core)
    final_binding_id = str(final["current_binding_id"])
    final_epoch = int(final["current_epoch"])
    final_matches_oracle = (
        final_binding_id == manifest.expected_binding_id
        and final_epoch == manifest.expected_epoch
    )
    if presentation.diverged_from_oracle != (not final_matches_oracle):
        raise AssertionError("presentation divergence must match final authority divergence")

    opportunities: tuple[CorrectnessMetric, ...] = ()
    opportunity_event_ids: tuple[str, ...] = ()
    opportunity_scopes: tuple[MetricOpportunityScope, ...] = ()
    violations: tuple[CorrectnessMetric, ...] = ()
    violation_event_ids: tuple[str, ...] = ()
    if manifest.sbdr_event_id is not None:
        opportunities = (CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,)
        opportunity_event_ids = (manifest.sbdr_event_id,)
        opportunity_scopes = (MetricOpportunityScope.EXOGENOUS_PAIRED,)
        if not final_matches_oracle:
            violations = (CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,)
            violation_event_ids = (manifest.sbdr_event_id,)

    if inject_divergence:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=False,
        )
    elif manifest.explicit_wait:
        semantic_result = SemanticResult(
            reported_success=False,
            authoritative_commit=False,
            semantically_correct=None,
            explicit_non_success=ExplicitNonSuccess.WAIT,
        )
    else:
        semantic_result = SemanticResult(
            reported_success=True,
            authoritative_commit=True,
            semantically_correct=final_matches_oracle,
        )

    ground_truth = {
        "schema": S3_ADVERSARIAL_SCHEMA,
        "case_id": manifest.case_id,
        "pressure_family": manifest.pressure_family.value,
        "fault_class": manifest.fault_class,
        "sbdr_event_id": manifest.sbdr_event_id,
        "setup_profile": manifest.setup_profile,
        "presentation_binding_id": manifest.presentation_binding_id,
        "evidence_mode": manifest.evidence_mode.value,
        "reconciliation": manifest.reconciliation,
        "expected_final_binding_id": manifest.expected_binding_id,
        "expected_final_epoch": manifest.expected_epoch,
        "explicit_wait": manifest.explicit_wait,
        "oracle_rule": "MANIFEST_EXPECTED_AUTHORITY_IS_INDEPENDENT_OF_POLICY_OUTPUT",
        "semantic_authority": "C1_COMMON_TO_B0_B4",
    }
    observed_evidence = {
        "initial_authority": {
            "current_binding_id": "b1",
            "current_epoch": 1,
        },
        "setup_records": [dict(item) for item in setup_records],
        "setup_final_authority": setup_final,
        "presentation": presentation.to_dict(),
        "final_authority": final,
        "injected_divergence": inject_divergence,
    }
    policy_decision = {
        "semantic_authority": "C1_COMMON_TO_B0_B4",
        "binding_information_contract": (
            "B4_BINDING_ID_EPOCH_RECONCILIATION"
            if policy_id is PolicyID.B4
            else "NO_BINDING_AWARE_MIGRATION_POLICY_SURFACE"
        ),
        "migration_decisions": [
            {
                "binding_id": item.binding_id,
                "binding_epoch": item.binding_epoch,
                "disposition": item.disposition.value,
                "reason": item.reason,
            }
            for item in decisions
        ],
        "oracle_expected_authority_is_not_policy_visible": True,
        "c1_commit_is_authoritative_not_policy_decision": True,
    }

    evaluation = CorrectnessEvaluationRecord.create(
        cohort_id=S3_ADVERSARIAL_COHORT_ID,
        trial_id=manifest.case_id,
        operation_id=f"binding:{S3_E0_SUBJECT_ID}",
        policy_id=policy_id,
        scenario_id=manifest.case_id,
        validation_level=ValidationEvidenceLevel.EV0_DETERMINISTIC_SEMANTICS,
        evidence_provenance=ResultEvidenceProvenance.SYNTHETICALLY_GENERATED,
        ground_truth=ground_truth,
        observed_evidence=observed_evidence,
        policy_decision=policy_decision,
        semantic_result=semantic_result,
        metric_opportunities=opportunities,
        metric_opportunity_event_ids=opportunity_event_ids,
        metric_opportunity_scopes=opportunity_scopes,
        metric_violations=violations,
        metric_violation_event_ids=violation_event_ids,
        fault_id=manifest.fault_id,
        fault_class=manifest.fault_class,
    )

    return BindingAdversarialTrial(
        policy_id=policy_id,
        case_id=manifest.case_id,
        evaluation=evaluation,
        presentation=presentation,
        policy_migration_decisions=decisions,
        setup_records=setup_records,
        expected_binding_id=manifest.expected_binding_id,
        expected_epoch=manifest.expected_epoch,
        final_binding_id=final_binding_id,
        final_epoch=final_epoch,
        injected_divergence=inject_divergence,
    )


def run_s3_adversarial_trial(policy_id: PolicyID, case_id: str) -> BindingAdversarialTrial:
    return _run_adversarial_trial(policy_id, case_id)


def run_s3_adversarial_case(case_id: str) -> tuple[BindingAdversarialTrial, ...]:
    if case_id not in _MANIFEST_BY_ID:
        raise ValueError(f"case_id must be one of {S3_ADVERSARIAL_CASE_IDS!r}")
    return tuple(run_s3_adversarial_trial(policy_id, case_id) for policy_id in PolicyID)


def run_s3_adversarial_paired() -> BindingAdversarialEvaluation:
    trials = tuple(
        run_s3_adversarial_trial(policy_id, manifest.case_id)
        for manifest in S3_ADVERSARIAL_MANIFESTS
        for policy_id in PolicyID
    )
    return BindingAdversarialEvaluation(
        trials=trials,
        summary=summarize_correctness(tuple(item.evaluation for item in trials)),
    )
