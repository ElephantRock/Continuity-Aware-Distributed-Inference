from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping

from experiments.c7_protocol import (
    AXES,
    C6_ARTIFACT_SHA256,
    C6_EVIDENCE_CLASS,
    C6_SCIENTIFIC_FINGERPRINT,
    C7_PROTOCOL_FINGERPRINT,
    C7_SUPPORTED_HARDWARE_IDS,
    ExperimentSeries,
    ParameterSource,
    WorkloadClass,
    validated_transfer_bytes_for_state_tokens,
)
from simulator.policies import POLICY_CONTRACT_SCHEMA, PolicyID


C74_PROTOCOL_SCHEMA = "cadi.c7.4a.migration-efficiency-protocol.v1"
C74_CROSSOVER_MANIFEST_SCHEMA = "cadi.c7.4a.p5-crossover-manifest.v1"
C74_FAILOVER_MANIFEST_SCHEMA = "cadi.c7.4a.failover-efficiency-manifest.v1"
C74_BASE_COMMIT = "32d77b6082583724ddace6e47856aa4db47f2957"
C74_C44_BINDING_SAFETY_MERGE = "0d93e9147a7e184b33588e49b63e7a8c2b39beca"
C74_FROZEN_C71_FINGERPRINT = (
    "706e0d5fff362a1eda8c906b957c914251c6e7949bac6be3c2c143ae21625474"
)
C74_FROZEN_C6_SCIENTIFIC_FINGERPRINT = (
    "cc1b62c4e38a1612720f601375c425c0bd7d67b4eff11b101bfdf87b79a7a61a"
)
C74_FROZEN_C6_ARTIFACT_SHA256 = (
    "93990386135ecbf6fc38e579841eace3431a068b10ee59209fe6ad7084bd8889"
)
C74_TIMING_EVIDENCE = "SIMULATED_SOURCE_MODEL_DERIVED_P_SRC2"
C74_ZERO_UNEVIDENCED_COMPONENT = "ZERO_UNEVIDENCED_COMPONENT"
C74_CANONICAL_DETERMINISTIC_SEED = 0
C74_COMPARATIVE_RESULT_INSPECTION = "NONE"
C74_PARTIAL_UNRANKED_LABEL = "UNRANKED_NO_PARTIAL_TRANSFER_FRACTION_EVIDENCE"
C74_SEMANTIC_INVALID_LABEL = "SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING"
C74_EFFICIENCY_STRENGTHENED = "EFFICIENCY_STRENGTHENED_WITHIN_DECLARED_PHASE_SPACE"
C74_EFFICIENCY_NOT_STRENGTHENED = (
    "EFFICIENCY_NOT_STRENGTHENED_WITHIN_DECLARED_PHASE_SPACE"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


if C7_PROTOCOL_FINGERPRINT != C74_FROZEN_C71_FINGERPRINT:
    raise RuntimeError("C7.4a parent C7.1 fingerprint drift")
if C6_SCIENTIFIC_FINGERPRINT != C74_FROZEN_C6_SCIENTIFIC_FINGERPRINT:
    raise RuntimeError("C7.4a C6 scientific fingerprint drift")
if C6_ARTIFACT_SHA256 != C74_FROZEN_C6_ARTIFACT_SHA256:
    raise RuntimeError("C7.4a C6 artifact drift")
if C6_EVIDENCE_CLASS != C74_TIMING_EVIDENCE:
    raise RuntimeError("C7.4a C6 evidence-class drift")
if AXES["state_tokens"].values != (1, 4, 16, 64):
    raise RuntimeError("C7.4a P5 State-token axis drift")
if AXES["recompute_tokens"].values != (64, 256, 1024, 2048, 4096):
    raise RuntimeError("C7.4a P5 recompute-token axis drift")
if AXES["state_tokens"].source is not ParameterSource.P_SRC4:
    raise RuntimeError("C7.4a State-token design source drift")
if AXES["recompute_tokens"].source is not ParameterSource.P_SRC4:
    raise RuntimeError("C7.4a recompute-token design source drift")
if tuple(C7_SUPPORTED_HARDWARE_IDS) != ("a100-80gb", "h100-80gb"):
    raise RuntimeError("C7.4a accepted hardware family drift")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fp(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _git_sha(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA40_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase 40-hex Git commit")
    return value


def _p5_axis(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value not in AXES[name].values:
        raise ValueError(f"{name} must be a frozen C7.1 P5 axis value")
    return value


class C74Track(str, Enum):
    P5_CROSSOVER = "P5_CROSSOVER"
    FAILOVER_EFFICIENCY = "FAILOVER_EFFICIENCY"


class C74CrossoverClass(str, Enum):
    TRANSFER_FASTER = "TRANSFER_FASTER"
    RECOMPUTE_FASTER = "RECOMPUTE_FASTER"
    TIE = "TIE"


class C74RecoveryAction(str, Enum):
    TRANSFER = "TRANSFER"
    RECOMPUTE = "RECOMPUTE"


class C74ScenarioID(str, Enum):
    MATCHED_PLANNED_MIGRATION = "MATCHED_PLANNED_MIGRATION"
    SOURCE_FAILURE_VALID_REMOTE = "SOURCE_FAILURE_VALID_REMOTE"
    DESTINATION_FAILURE_AFTER_TRANSFER_BEFORE_COMMIT = (
        "DESTINATION_FAILURE_AFTER_TRANSFER_BEFORE_COMMIT"
    )
    STALE_BINDING_RESIDUAL = "STALE_BINDING_RESIDUAL"
    CONCURRENT_CANDIDATE_LOSER = "CONCURRENT_CANDIDATE_LOSER"
    PARTIAL_MATERIALIZATION_NO_COMMIT = "PARTIAL_MATERIALIZATION_NO_COMMIT"


class C74ScenarioRanking(str, Enum):
    RANKABLE = "RANKABLE"
    UNRANKED_NO_PARTIAL_TRANSFER_FRACTION_EVIDENCE = C74_PARTIAL_UNRANKED_LABEL


class C74SupportMode(str, Enum):
    RECOVERY_TIME = "RECOVERY_TIME"
    TRANSFER_VOLUME = "TRANSFER_VOLUME"


class C74EfficiencyDecision(str, Enum):
    STRENGTHENED = C74_EFFICIENCY_STRENGTHENED
    NOT_STRENGTHENED = C74_EFFICIENCY_NOT_STRENGTHENED


C74_RANKABLE_SCENARIOS = (
    C74ScenarioID.MATCHED_PLANNED_MIGRATION,
    C74ScenarioID.SOURCE_FAILURE_VALID_REMOTE,
    C74ScenarioID.DESTINATION_FAILURE_AFTER_TRANSFER_BEFORE_COMMIT,
    C74ScenarioID.STALE_BINDING_RESIDUAL,
    C74ScenarioID.CONCURRENT_CANDIDATE_LOSER,
)
C74_UNRANKED_SCENARIOS = (C74ScenarioID.PARTIAL_MATERIALIZATION_NO_COMMIT,)

C74_EVENT_ORDER = (
    "FAULT_OR_MIGRATION_TRIGGER",
    "COMMON_PHYSICAL_STATE_OBSERVATION",
    "POLICY_VIEW_PROJECTION",
    "ACTION_SELECTION",
    "TRANSFER_OR_RECOMPUTE_SERVICE",
    "DESTINATION_FAILURE_IF_PREDECLARED",
    "COMMON_C1_MIGRATION_COMMIT_ATTEMPT_IF_TRANSFERRED",
    "FALLBACK_RECOMPUTE_IF_TRANSFER_CANNOT_AUTHORITATIVELY_COMMIT",
    "FIRST_AUTHORITATIVE_USEFUL_PROGRESS",
    "FINAL_METRIC_ACCOUNTING",
)

C74_POLICY_ACTION_RULES: Mapping[PolicyID, Mapping[str, Any]] = {
    PolicyID.B0: {
        "primary_action": "RECOMPUTE",
        "reason": "NO_EXACT_MIGRATION_STATE_OR_BINDING_CONTEXT",
        "may_select_transfer": False,
        "binding_generation_visible": False,
        "reconciliation_visible": False,
    },
    PolicyID.B1: {
        "primary_action": "RECOMPUTE",
        "reason": "CACHE_LOCALITY_IS_NOT_EXACT_MIGRATION_AUTHORITY",
        "may_select_transfer": False,
        "binding_generation_visible": False,
        "reconciliation_visible": False,
    },
    PolicyID.B2: {
        "primary_action": "RECOMPUTE",
        "reason": "SESSION_AFFINITY_IS_NOT_EXACT_MIGRATION_AUTHORITY",
        "may_select_transfer": False,
        "binding_generation_visible": False,
        "reconciliation_visible": False,
    },
    PolicyID.B3: {
        "primary_action": "MIN_COST_IF_EXACT_STATE_PHYSICALLY_AVAILABLE",
        "reason": "EXACT_STATE_LOCATION_WITH_COMMON_C1_COMMIT_FENCE",
        "may_select_transfer": True,
        "binding_generation_visible": False,
        "reconciliation_visible": False,
    },
    PolicyID.B4: {
        "primary_action": "MIN_COST_IF_CONTINUITY_MIGRATION_ELIGIBLE",
        "reason": "EXACT_COMPATIBLE_STATE_PLUS_BINDING_AND_RECONCILIATION",
        "may_select_transfer": True,
        "binding_generation_visible": True,
        "reconciliation_visible": True,
    },
}


def scenario_ranking(scenario_id: C74ScenarioID) -> C74ScenarioRanking:
    if not isinstance(scenario_id, C74ScenarioID):
        raise TypeError("scenario_id must be C74ScenarioID")
    return (
        C74ScenarioRanking.UNRANKED_NO_PARTIAL_TRANSFER_FRACTION_EVIDENCE
        if scenario_id is C74ScenarioID.PARTIAL_MATERIALIZATION_NO_COMMIT
        else C74ScenarioRanking.RANKABLE
    )


def p5_state_bytes(state_tokens: int) -> int:
    state_tokens = _p5_axis("state_tokens", state_tokens)
    return validated_transfer_bytes_for_state_tokens(state_tokens)


def p5_point(state_tokens: int, recompute_tokens: int) -> tuple[int, int]:
    return (
        _p5_axis("state_tokens", state_tokens),
        _p5_axis("recompute_tokens", recompute_tokens),
    )


def p5_cells() -> tuple[tuple[int, int], ...]:
    return tuple(
        (int(state_tokens), int(recompute_tokens))
        for state_tokens in AXES["state_tokens"].values
        for recompute_tokens in AXES["recompute_tokens"].values
    )


def p5_cells_adjacent(a: tuple[int, int], b: tuple[int, int]) -> bool:
    a_state, a_recompute = p5_point(*a)
    b_state, b_recompute = p5_point(*b)
    if a == b:
        return False
    states = AXES["state_tokens"].values
    recomputes = AXES["recompute_tokens"].values
    a_si = states.index(a_state)
    b_si = states.index(b_state)
    a_ri = recomputes.index(a_recompute)
    b_ri = recomputes.index(b_recompute)
    return (abs(a_si - b_si) == 1 and a_ri == b_ri) or (
        a_si == b_si and abs(a_ri - b_ri) == 1
    )


def crossover_class(*, transfer_seconds: float, recompute_seconds: float) -> C74CrossoverClass:
    for value, name in (
        (transfer_seconds, "transfer_seconds"),
        (recompute_seconds, "recompute_seconds"),
    ):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be finite and non-negative")
    if transfer_seconds < recompute_seconds:
        return C74CrossoverClass.TRANSFER_FASTER
    if transfer_seconds > recompute_seconds:
        return C74CrossoverClass.RECOMPUTE_FASTER
    return C74CrossoverClass.TIE


def recomputation_ratio_components(
    *, recompute_tokens: int, transfer_authoritative_success: bool
) -> tuple[int, int]:
    tokens = _p5_axis("recompute_tokens", recompute_tokens)
    if not isinstance(transfer_authoritative_success, bool):
        raise TypeError("transfer_authoritative_success must be bool")
    numerator = 0 if transfer_authoritative_success else tokens
    denominator = tokens
    return numerator, denominator


@dataclass(frozen=True, slots=True)
class C74MigrationEfficiencyProtocol:
    base_commit: str = C74_BASE_COMMIT
    parent_c7_protocol_fingerprint: str = C74_FROZEN_C71_FINGERPRINT
    c6_scientific_fingerprint: str = C74_FROZEN_C6_SCIENTIFIC_FINGERPRINT
    c6_artifact_sha256: str = C74_FROZEN_C6_ARTIFACT_SHA256
    c44_binding_safety_merge: str = C74_C44_BINDING_SAFETY_MERGE

    def __post_init__(self) -> None:
        if self.base_commit != C74_BASE_COMMIT:
            raise ValueError("C7.4a base commit is frozen")
        if self.parent_c7_protocol_fingerprint != C7_PROTOCOL_FINGERPRINT:
            raise ValueError("C7.4a must bind frozen C7.1")
        if self.c6_scientific_fingerprint != C6_SCIENTIFIC_FINGERPRINT:
            raise ValueError("C7.4a must bind frozen C6 scientific fingerprint")
        if self.c6_artifact_sha256 != C6_ARTIFACT_SHA256:
            raise ValueError("C7.4a must bind frozen C6 artifact")
        _git_sha(self.c44_binding_safety_merge, "c44_binding_safety_merge")
        if C74_CANONICAL_DETERMINISTIC_SEED != 0:
            raise RuntimeError("C7.4a deterministic manifest seed drift")
        if len(p5_cells()) != 20:
            raise RuntimeError("C7.4a P5 grid must contain exactly 20 cells")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C74_PROTOCOL_SCHEMA,
            "base_commit": self.base_commit,
            "parent_c7": {
                "protocol_fingerprint": self.parent_c7_protocol_fingerprint,
                "series": ExperimentSeries.P5_MIGRATION_VS_RECOMPUTE.value,
                "policy_contract_schema": POLICY_CONTRACT_SCHEMA,
            },
            "closed_correctness_dependency": {
                "c4_4_binding_safety_merge": self.c44_binding_safety_merge,
                "common_c1_commit_authority_preserved": True,
                "unique_b4_correctness_advantage_claim": False,
                "correctness_component_reopened": False,
            },
            "comparative_result_inspection": C74_COMPARATIVE_RESULT_INSPECTION,
            "tracks": {
                C74Track.P5_CROSSOVER.value: {
                    "policy_free": True,
                    "grid": {
                        "state_tokens": list(AXES["state_tokens"].values),
                        "recompute_tokens": list(AXES["recompute_tokens"].values),
                        "hardware_ids": list(C7_SUPPORTED_HARDWARE_IDS),
                        "cell_count_per_hardware": len(p5_cells()),
                    },
                    "state_bytes": "validated_transfer_bytes_for_state_tokens(state_tokens)",
                    "transfer_seconds": "accepted C6 transfer lookup(state_bytes)",
                    "recompute_seconds": "accepted C6 prefill/recompute lookup(recompute_tokens)",
                    "classification": [item.value for item in C74CrossoverClass],
                    "excluded_components": {
                        "queueing": C74_ZERO_UNEVIDENCED_COMPONENT,
                        "network_concurrency": C74_ZERO_UNEVIDENCED_COMPONENT,
                        "migration_control": C74_ZERO_UNEVIDENCED_COMPONENT,
                        "reconciliation": C74_ZERO_UNEVIDENCED_COMPONENT,
                        "semantic_commit": C74_ZERO_UNEVIDENCED_COMPONENT,
                    },
                    "can_adjudicate_h6": False,
                },
                C74Track.FAILOVER_EFFICIENCY.value: {
                    "workload_class": WorkloadClass.SYNTHETIC_STRESS.value,
                    "canonical_manifest_seed": C74_CANONICAL_DETERMINISTIC_SEED,
                    "scenario_construction_uses_seed": False,
                    "artificial_repeated_seeds": False,
                    "bootstrap_applied": False,
                    "rankable_scenarios": [item.value for item in C74_RANKABLE_SCENARIOS],
                    "unranked_scenarios": [item.value for item in C74_UNRANKED_SCENARIOS],
                    "partial_materialization_label": C74_PARTIAL_UNRANKED_LABEL,
                    "event_order": list(C74_EVENT_ORDER),
                    "one_recovery_action_at_a_time": True,
                    "concurrent_candidate_means_semantic_not_link_overlap": True,
                },
            },
            "strategy_contract": {
                "same_physical_fault_realization": True,
                "same_cost_tables": True,
                "same_cost_comparison": True,
                "same_common_c1_commit_authority": True,
                "cost_knowledge_is_b4_only": False,
                "policy_rules": {
                    policy.value: dict(C74_POLICY_ACTION_RULES[policy]) for policy in PolicyID
                },
                "failed_or_fenced_transfer_falls_back_to_recompute": True,
                "failed_transfer_service_is_charged_before_fallback": True,
            },
            "metrics": {
                "RECOVERY_TIME": {
                    "fault_or_trigger_time_seconds": 0.0,
                    "recompute_success": "recompute_seconds",
                    "transfer_success": "transfer_seconds",
                    "transfer_then_fallback": "transfer_seconds + recompute_seconds",
                    "semantic_commit_duration": C74_ZERO_UNEVIDENCED_COMPONENT,
                },
                "STATE_TRANSFER_VOLUME_BYTES": {
                    "transfer": "state_bytes actually transferred",
                    "recompute": 0,
                    "failed_or_fenced_completed_transfer_counts": True,
                    "partial_materialization_ranked": False,
                },
                "RECOMPUTATION_RATIO": {
                    "eligible_reuse_tokens": "recompute_tokens",
                    "total_input_tokens": "recompute_tokens",
                    "consumed_reuse_tokens": (
                        "recompute_tokens iff transfer authoritatively succeeds, otherwise 0"
                    ),
                    "definition": "frozen C7.1 ratio-of-token-sums semantics",
                },
                "redundant_transfer_bytes": (
                    "bytes transferred by an action that cannot become authoritative in the realized common C1 trace"
                ),
                "redundant_recompute_tokens": (
                    "recompute tokens executed when a semantically authorizable physically available transfer would have completed strictly faster"
                ),
            },
            "evidence": {
                "p5_axis_design": ParameterSource.P_SRC4.value,
                "timing_cost": C74_TIMING_EVIDENCE,
                "fault_scenario_structure_and_event_order": ParameterSource.P_SRC4.value,
                "policy_information": POLICY_CONTRACT_SCHEMA,
                "semantic_commit": "DETERMINISTIC_COMMON_C1_AUTHORITY",
                "direct_hardware_measurement_claim": False,
            },
            "hardware_rule": {
                "report_strata_separately": list(C7_SUPPORTED_HARDWARE_IDS),
                "hardware_agnostic_recovery_support": (
                    "same connected support region in intersection of A100 and H100 support cells"
                ),
                "volume_support": (
                    "hardware-invariant volume improvement plus no Recovery-Time regression on either stratum"
                ),
            },
            "h6_efficiency": {
                "classifications": [item.value for item in C74EfficiencyDecision],
                "support_modes": [item.value for item in C74SupportMode],
                "adjacency": "one-axis neighbor on ordered state_tokens x recompute_tokens grid",
                "minimum_connected_cells": 2,
                "cell_requirements": [
                    "semantic violations == 0",
                    "B4 Recovery Time <= B3",
                    "B4 Recovery Time <= B0, B1, and B2",
                    "RECOVERY_TIME mode: strict Recovery-Time improvement versus at least one relevant comparator",
                    "TRANSFER_VOLUME mode: State Transfer Volume strictly lower than B3 and Recovery Time no worse than B3/B0/B1/B2",
                ],
                "track_a_cannot_support": True,
                "partial_materialization_cannot_support": True,
                "negative_efficiency_does_not_reopen_c4_4_correctness": True,
            },
            "manifest_contracts": {
                C74Track.P5_CROSSOVER.value: {
                    "schema": C74_CROSSOVER_MANIFEST_SCHEMA,
                    "policy_id_present": False,
                    "canonical_seed": C74_CANONICAL_DETERMINISTIC_SEED,
                },
                C74Track.FAILOVER_EFFICIENCY.value: {
                    "schema": C74_FAILOVER_MANIFEST_SCHEMA,
                    "per_policy": True,
                    "base_c7_series": ExperimentSeries.P5_MIGRATION_VS_RECOMPUTE.value,
                    "base_c7_workload_class": WorkloadClass.SYNTHETIC_STRESS.value,
                    "base_c7_seed": C74_CANONICAL_DETERMINISTIC_SEED,
                    "base_parameters": ["recompute_tokens", "state_tokens"],
                    "base_parameter_sources": {
                        "recompute_tokens": ParameterSource.P_SRC4.value,
                        "state_tokens": ParameterSource.P_SRC4.value,
                    },
                },
            },
            "result_identity_requirements": [
                "C7.1 protocol fingerprint",
                "C6 scientific fingerprint and artifact SHA",
                "C4.4 correctness merge SHA",
                "C7.4a protocol fingerprint",
                "execution Git SHA",
                "track and scenario ID",
                "hardware ID",
                "state_tokens/recompute_tokens plus parameter sources",
                "State bytes",
                "policy ID for Track B only",
                "Program/case fingerprint",
                "physical observation/fault fingerprint",
                "policy-view fingerprint",
                "C1 pre/post Binding-authority snapshot fingerprint",
                "action selected and reason",
                "transfer/recompute/fallback component seconds",
                "State Transfer Volume",
                "Recomputation Ratio components",
                "redundant-work diagnostics",
                "semantic violation counters and efficiency eligibility",
                "Recovery Time",
            ],
            "invalid_result_conditions": [
                "C7.1 or C6 frozen identity drift",
                "State size outside {1,4,16,64}",
                "recompute point outside {64,256,1024,2048,4096}",
                "transfer bytes outside exact C6 lattice/domain",
                "implicit transfer chunking or extrapolation",
                "policy information leakage",
                "B3 receives Binding generation or reconciliation information",
                "B4 bypasses common C1 commit authority",
                "baseline correctness weakened",
                "partial-materialization efficiency ranked without independently frozen byte fraction evidence",
                "post-result scenario, threshold, or comparator change",
                "semantic-invalid outcome counted as efficiency support",
                "source-model timing described as direct hardware measurement",
            ],
        }

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


FROZEN_C74_MIGRATION_PROTOCOL = C74MigrationEfficiencyProtocol()
C74_PROTOCOL_FINGERPRINT = FROZEN_C74_MIGRATION_PROTOCOL.fingerprint


@dataclass(frozen=True, slots=True)
class C74CrossoverManifest:
    execution_git_commit: str
    protocol_fingerprint: str
    hardware_id: str
    state_tokens: int
    recompute_tokens: int
    seed: int = C74_CANONICAL_DETERMINISTIC_SEED

    def __post_init__(self) -> None:
        _git_sha(self.execution_git_commit, "execution_git_commit")
        if self.protocol_fingerprint != C74_PROTOCOL_FINGERPRINT:
            raise ValueError("crossover manifest must bind frozen C7.4a protocol")
        if self.hardware_id not in C7_SUPPORTED_HARDWARE_IDS:
            raise ValueError("hardware_id is outside accepted C6 source-model family")
        p5_point(self.state_tokens, self.recompute_tokens)
        if self.seed != C74_CANONICAL_DETERMINISTIC_SEED:
            raise ValueError("deterministic C7.4a manifest seed is frozen to 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C74_CROSSOVER_MANIFEST_SCHEMA,
            "execution_git_commit": self.execution_git_commit,
            "c7_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
            "c74_protocol_fingerprint": self.protocol_fingerprint,
            "c6_scientific_fingerprint": C6_SCIENTIFIC_FINGERPRINT,
            "c6_artifact_sha256": C6_ARTIFACT_SHA256,
            "hardware_id": self.hardware_id,
            "state_tokens": self.state_tokens,
            "state_tokens_source": ParameterSource.P_SRC4.value,
            "state_bytes": p5_state_bytes(self.state_tokens),
            "recompute_tokens": self.recompute_tokens,
            "recompute_tokens_source": ParameterSource.P_SRC4.value,
            "cost_evidence": C74_TIMING_EVIDENCE,
            "seed": self.seed,
            "policy_id": None,
        }

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


@dataclass(frozen=True, slots=True)
class C74FailoverManifest:
    execution_git_commit: str
    base_c7_manifest_fingerprint: str
    protocol_fingerprint: str
    scenario_id: C74ScenarioID
    policy_id: PolicyID
    hardware_id: str
    state_tokens: int
    recompute_tokens: int
    program_case_fingerprint: str
    physical_fault_fingerprint: str
    policy_view_fingerprint: str
    seed: int = C74_CANONICAL_DETERMINISTIC_SEED

    def __post_init__(self) -> None:
        _git_sha(self.execution_git_commit, "execution_git_commit")
        _sha256(self.base_c7_manifest_fingerprint, "base_c7_manifest_fingerprint")
        if self.protocol_fingerprint != C74_PROTOCOL_FINGERPRINT:
            raise ValueError("failover manifest must bind frozen C7.4a protocol")
        if not isinstance(self.scenario_id, C74ScenarioID):
            raise TypeError("scenario_id must be C74ScenarioID")
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if self.hardware_id not in C7_SUPPORTED_HARDWARE_IDS:
            raise ValueError("hardware_id is outside accepted C6 source-model family")
        p5_point(self.state_tokens, self.recompute_tokens)
        for value, name in (
            (self.program_case_fingerprint, "program_case_fingerprint"),
            (self.physical_fault_fingerprint, "physical_fault_fingerprint"),
            (self.policy_view_fingerprint, "policy_view_fingerprint"),
        ):
            _sha256(value, name)
        if self.seed != C74_CANONICAL_DETERMINISTIC_SEED:
            raise ValueError("deterministic C7.4a manifest seed is frozen to 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": C74_FAILOVER_MANIFEST_SCHEMA,
            "execution_git_commit": self.execution_git_commit,
            "base_c7_manifest_fingerprint": self.base_c7_manifest_fingerprint,
            "c7_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
            "c74_protocol_fingerprint": self.protocol_fingerprint,
            "c44_binding_safety_merge": C74_C44_BINDING_SAFETY_MERGE,
            "scenario_id": self.scenario_id.value,
            "scenario_ranking": scenario_ranking(self.scenario_id).value,
            "policy_id": self.policy_id.value,
            "hardware_id": self.hardware_id,
            "state_tokens": self.state_tokens,
            "state_tokens_source": ParameterSource.P_SRC4.value,
            "state_bytes": p5_state_bytes(self.state_tokens),
            "recompute_tokens": self.recompute_tokens,
            "recompute_tokens_source": ParameterSource.P_SRC4.value,
            "cost_evidence": C74_TIMING_EVIDENCE,
            "program_case_fingerprint": self.program_case_fingerprint,
            "physical_fault_fingerprint": self.physical_fault_fingerprint,
            "policy_view_fingerprint": self.policy_view_fingerprint,
            "seed": self.seed,
        }

    @property
    def fingerprint(self) -> str:
        return _fp(self.to_dict())


def protocol_identity() -> dict[str, Any]:
    return {
        "schema": C74_PROTOCOL_SCHEMA,
        "base_commit": C74_BASE_COMMIT,
        "c7_protocol_fingerprint": C7_PROTOCOL_FINGERPRINT,
        "c6_scientific_fingerprint": C6_SCIENTIFIC_FINGERPRINT,
        "c6_artifact_sha256": C6_ARTIFACT_SHA256,
        "c44_binding_safety_merge": C74_C44_BINDING_SAFETY_MERGE,
        "protocol_fingerprint": C74_PROTOCOL_FINGERPRINT,
        "comparative_result_inspection": C74_COMPARATIVE_RESULT_INSPECTION,
        "p5_cell_count": len(p5_cells()),
        "rankable_scenario_count": len(C74_RANKABLE_SCENARIOS),
        "unranked_scenario_count": len(C74_UNRANKED_SCENARIOS),
    }


def main() -> None:
    print(_json(protocol_identity()))


if __name__ == "__main__":
    main()
