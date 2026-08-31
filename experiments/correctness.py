from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from simulator.policies import PolicyID


CORRECTNESS_EVALUATION_SCHEMA = "cadi.correctness-evaluation.v1"


class ValidationEvidenceLevel(str, Enum):
    """Methodological validation hierarchy, normalized by Gate G0."""

    EV0_DETERMINISTIC_SEMANTICS = "EV0"
    EV1_MEASURED_CPU_DISTRIBUTED = "EV1"
    EV2_TRACE_DERIVED = "EV2"
    EV3_CALIBRATED_SIMULATION = "EV3"
    EV4_OPTIONAL_ACCELERATOR_MEASUREMENT = "EV4"


class ResultEvidenceProvenance(str, Enum):
    """How a concrete result was produced; orthogonal to validation level."""

    MEASURED = "MEASURED"
    SIMULATED = "SIMULATED"
    TRACE_DERIVED = "TRACE_DERIVED"
    SYNTHETICALLY_GENERATED = "SYNTHETICALLY_GENERATED"
    ANALYTICALLY_DERIVED = "ANALYTICALLY_DERIVED"
    ESTIMATED = "ESTIMATED"


class OutcomeClass(str, Enum):
    O1_CORRECT_TRANSPARENT_RECOVERY = "O1"
    O2_CORRECT_DEGRADED_RECOVERY = "O2"
    O3_EXPLICIT_NON_SUCCESS = "O3"
    O4_SILENT_SEMANTIC_VIOLATION = "O4"


class ExplicitNonSuccess(str, Enum):
    WAIT = "WAIT"
    FAIL = "FAIL"
    AMBIGUOUS = "AMBIGUOUS"
    REJECT = "REJECT"


class RecoveryAction(str, Enum):
    RETRY = "RETRY"
    RECOMPUTE = "RECOMPUTE"
    MIGRATION = "MIGRATION"
    REPAIR = "REPAIR"


class CorrectnessMetric(str, Enum):
    STALE_ATTEMPT_ACCEPTANCE_RATE = "Stale Attempt Acceptance Rate"
    WRONG_BRANCH_REUSE_RATE = "Wrong-Branch Reuse Rate"
    WRONG_STATE_CONSUMPTION_RATE = "Wrong-State Consumption Rate"
    SILENT_BINDING_DIVERGENCE_RATE = "Silent Binding Divergence Rate"
    AMBIGUOUS_COMMIT_RATE = "Ambiguous Commit Rate"
    DUPLICATE_FINALIZATION_RATE = "Duplicate Finalization Rate"
    SILENT_SEMANTIC_ERROR_RATE = "Silent Semantic Error Rate"
    EXPLICIT_NON_SUCCESS_RATE = "Explicit Non-Success Rate"
    RECOVERY_RATE = "Recovery Rate"


GATE_G1_METRICS = frozenset(
    {
        CorrectnessMetric.STALE_ATTEMPT_ACCEPTANCE_RATE,
        CorrectnessMetric.WRONG_BRANCH_REUSE_RATE,
        CorrectnessMetric.WRONG_STATE_CONSUMPTION_RATE,
        CorrectnessMetric.SILENT_BINDING_DIVERGENCE_RATE,
        CorrectnessMetric.AMBIGUOUS_COMMIT_RATE,
        CorrectnessMetric.DUPLICATE_FINALIZATION_RATE,
    }
)


def _require_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} mapping keys must be strings")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise TypeError(f"{path} contains unsupported value type {type(value).__name__}")


def _canonical_mapping_json(value: Mapping[str, Any], name: str) -> str:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    _validate_json_value(value, name)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _enum_tuple(
    values: Iterable[Any],
    enum_type: type[Enum],
    name: str,
) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of {enum_type.__name__}")
    result = tuple(values)
    if not all(isinstance(value, enum_type) for value in result):
        raise TypeError(f"{name} must contain only {enum_type.__name__}")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(sorted(result, key=lambda value: value.value))


@dataclass(frozen=True, slots=True)
class SemanticResult:
    reported_success: bool
    authoritative_commit: bool
    semantically_correct: bool | None
    explicit_non_success: ExplicitNonSuccess | None = None
    recovery_actions: tuple[RecoveryAction, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reported_success, bool):
            raise TypeError("reported_success must be bool")
        if not isinstance(self.authoritative_commit, bool):
            raise TypeError("authoritative_commit must be bool")
        object.__setattr__(
            self,
            "recovery_actions",
            _enum_tuple(self.recovery_actions, RecoveryAction, "recovery_actions"),
        )
        if self.reported_success:
            if self.explicit_non_success is not None:
                raise ValueError("successful result cannot also be explicit non-success")
            if not isinstance(self.semantically_correct, bool):
                raise TypeError("successful result requires semantically_correct bool")
            if not self.semantically_correct and not self.authoritative_commit:
                raise ValueError(
                    "silent semantic violation requires an authoritative commit"
                )
        else:
            if self.authoritative_commit:
                raise ValueError("explicit non-success cannot authoritatively commit")
            if self.semantically_correct is not None:
                raise ValueError(
                    "explicit non-success uses semantically_correct=None because no success was committed"
                )
            if not isinstance(self.explicit_non_success, ExplicitNonSuccess):
                raise TypeError(
                    "non-success result requires an ExplicitNonSuccess disposition"
                )

    @property
    def outcome_class(self) -> OutcomeClass:
        if not self.reported_success:
            return OutcomeClass.O3_EXPLICIT_NON_SUCCESS
        if not self.semantically_correct:
            return OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
        if self.recovery_actions:
            return OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY
        return OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY

    def to_dict(self) -> dict[str, Any]:
        return {
            "reported_success": self.reported_success,
            "authoritative_commit": self.authoritative_commit,
            "semantically_correct": self.semantically_correct,
            "explicit_non_success": (
                None if self.explicit_non_success is None else self.explicit_non_success.value
            ),
            "recovery_actions": [action.value for action in self.recovery_actions],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SemanticResult":
        if not isinstance(value, Mapping):
            raise TypeError("semantic_result must be a mapping")
        return cls(
            reported_success=value.get("reported_success"),
            authoritative_commit=value.get("authoritative_commit"),
            semantically_correct=value.get("semantically_correct"),
            explicit_non_success=(
                None
                if value.get("explicit_non_success") is None
                else ExplicitNonSuccess(value["explicit_non_success"])
            ),
            recovery_actions=tuple(
                RecoveryAction(item) for item in value.get("recovery_actions", ())
            ),
        )


@dataclass(frozen=True, slots=True)
class CorrectnessEvaluationRecord:
    trial_id: str
    policy_id: PolicyID
    scenario_id: str
    validation_level: ValidationEvidenceLevel
    evidence_provenance: ResultEvidenceProvenance
    ground_truth_json: str
    observed_evidence_json: str
    policy_decision_json: str
    semantic_result: SemanticResult
    metric_opportunities: tuple[CorrectnessMetric, ...] = ()
    metric_violations: tuple[CorrectnessMetric, ...] = ()
    fault_id: str | None = None
    fault_class: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.trial_id, "trial_id")
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        _require_id(self.scenario_id, "scenario_id")
        if not isinstance(self.validation_level, ValidationEvidenceLevel):
            raise TypeError("validation_level must be ValidationEvidenceLevel")
        if not isinstance(self.evidence_provenance, ResultEvidenceProvenance):
            raise TypeError("evidence_provenance must be ResultEvidenceProvenance")
        for value, name in (
            (self.ground_truth_json, "ground_truth_json"),
            (self.observed_evidence_json, "observed_evidence_json"),
            (self.policy_decision_json, "policy_decision_json"),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{name} must be canonical JSON text")
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise TypeError(f"{name} must encode a JSON object")
            if _canonical_mapping_json(parsed, name) != value:
                raise ValueError(f"{name} must be canonical JSON")
        if not isinstance(self.semantic_result, SemanticResult):
            raise TypeError("semantic_result must be SemanticResult")
        object.__setattr__(
            self,
            "metric_opportunities",
            _enum_tuple(
                self.metric_opportunities, CorrectnessMetric, "metric_opportunities"
            ),
        )
        object.__setattr__(
            self,
            "metric_violations",
            _enum_tuple(
                self.metric_violations, CorrectnessMetric, "metric_violations"
            ),
        )
        opportunities = set(self.metric_opportunities)
        violations = set(self.metric_violations)
        if not opportunities <= GATE_G1_METRICS:
            raise ValueError("metric_opportunities may contain only Gate G1 metrics")
        if not violations <= opportunities:
            raise ValueError("metric_violations must be a subset of metric_opportunities")
        if (self.fault_id is None) != (self.fault_class is None):
            raise ValueError("fault_id and fault_class must either both be set or both be None")
        if self.fault_id is not None:
            _require_id(self.fault_id, "fault_id")
            _require_id(self.fault_class, "fault_class")
        elif opportunities:
            raise ValueError("Gate G1 metric opportunities require a faulted trial")

    @classmethod
    def create(
        cls,
        *,
        trial_id: str,
        policy_id: PolicyID,
        scenario_id: str,
        validation_level: ValidationEvidenceLevel,
        evidence_provenance: ResultEvidenceProvenance,
        ground_truth: Mapping[str, Any],
        observed_evidence: Mapping[str, Any],
        policy_decision: Mapping[str, Any],
        semantic_result: SemanticResult,
        metric_opportunities: Iterable[CorrectnessMetric] = (),
        metric_violations: Iterable[CorrectnessMetric] = (),
        fault_id: str | None = None,
        fault_class: str | None = None,
    ) -> "CorrectnessEvaluationRecord":
        return cls(
            trial_id=trial_id,
            policy_id=policy_id,
            scenario_id=scenario_id,
            validation_level=validation_level,
            evidence_provenance=evidence_provenance,
            ground_truth_json=_canonical_mapping_json(ground_truth, "ground_truth"),
            observed_evidence_json=_canonical_mapping_json(
                observed_evidence, "observed_evidence"
            ),
            policy_decision_json=_canonical_mapping_json(
                policy_decision, "policy_decision"
            ),
            semantic_result=semantic_result,
            metric_opportunities=tuple(metric_opportunities),
            metric_violations=tuple(metric_violations),
            fault_id=fault_id,
            fault_class=fault_class,
        )

    @property
    def ground_truth(self) -> dict[str, Any]:
        return json.loads(self.ground_truth_json)

    @property
    def observed_evidence(self) -> dict[str, Any]:
        return json.loads(self.observed_evidence_json)

    @property
    def policy_decision(self) -> dict[str, Any]:
        return json.loads(self.policy_decision_json)

    @property
    def outcome_class(self) -> OutcomeClass:
        return self.semantic_result.outcome_class

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CORRECTNESS_EVALUATION_SCHEMA,
            "trial_id": self.trial_id,
            "policy_id": self.policy_id.value,
            "scenario_id": self.scenario_id,
            "fault_id": self.fault_id,
            "fault_class": self.fault_class,
            "validation_level": self.validation_level.value,
            "evidence_provenance": self.evidence_provenance.value,
            "ground_truth": self.ground_truth,
            "observed_evidence": self.observed_evidence,
            "policy_decision": self.policy_decision,
            "semantic_result": self.semantic_result.to_dict(),
            "outcome_class": self.outcome_class.value,
            "metric_opportunities": [item.value for item in self.metric_opportunities],
            "metric_violations": [item.value for item in self.metric_violations],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CorrectnessEvaluationRecord":
        if not isinstance(value, Mapping):
            raise TypeError("evaluation record must be a mapping")
        if value.get("schema") != CORRECTNESS_EVALUATION_SCHEMA:
            raise ValueError("unsupported correctness evaluation schema")
        semantic_result = SemanticResult.from_dict(value.get("semantic_result", {}))
        record = cls.create(
            trial_id=value.get("trial_id"),
            policy_id=PolicyID(value.get("policy_id")),
            scenario_id=value.get("scenario_id"),
            validation_level=ValidationEvidenceLevel(value.get("validation_level")),
            evidence_provenance=ResultEvidenceProvenance(
                value.get("evidence_provenance")
            ),
            ground_truth=value.get("ground_truth", {}),
            observed_evidence=value.get("observed_evidence", {}),
            policy_decision=value.get("policy_decision", {}),
            semantic_result=semantic_result,
            metric_opportunities=tuple(
                CorrectnessMetric(item) for item in value.get("metric_opportunities", ())
            ),
            metric_violations=tuple(
                CorrectnessMetric(item) for item in value.get("metric_violations", ())
            ),
            fault_id=value.get("fault_id"),
            fault_class=value.get("fault_class"),
        )
        if value.get("outcome_class") != record.outcome_class.value:
            raise ValueError("outcome_class does not match semantic_result")
        return record

    @classmethod
    def from_json(cls, value: str) -> "CorrectnessEvaluationRecord":
        if not isinstance(value, str):
            raise TypeError("evaluation JSON must be str")
        try:
            parsed = json.loads(
                value,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON constant: {token}")
                ),
            )
        except json.JSONDecodeError as exc:
            raise ValueError("invalid correctness evaluation JSON") from exc
        return cls.from_dict(parsed)


@dataclass(frozen=True, slots=True)
class RateCount:
    metric: CorrectnessMetric
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if not isinstance(self.metric, CorrectnessMetric):
            raise TypeError("metric must be CorrectnessMetric")
        for value, name in (
            (self.numerator, "numerator"),
            (self.denominator, "denominator"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.numerator > self.denominator:
            raise ValueError("numerator cannot exceed denominator")

    @property
    def rate(self) -> float | None:
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "rate": self.rate,
        }


@dataclass(frozen=True, slots=True)
class PolicyCorrectnessSummary:
    policy_id: PolicyID
    trial_count: int
    faulted_trial_count: int
    outcome_counts: tuple[tuple[OutcomeClass, int], ...]
    rates: tuple[RateCount, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, PolicyID):
            raise TypeError("policy_id must be PolicyID")
        if (
            not isinstance(self.trial_count, int)
            or isinstance(self.trial_count, bool)
            or self.trial_count < 0
        ):
            raise ValueError("trial_count must be a non-negative integer")
        if (
            not isinstance(self.faulted_trial_count, int)
            or isinstance(self.faulted_trial_count, bool)
            or self.faulted_trial_count < 0
            or self.faulted_trial_count > self.trial_count
        ):
            raise ValueError("faulted_trial_count must be between zero and trial_count")
        if tuple(item[0] for item in self.outcome_counts) != tuple(OutcomeClass):
            raise ValueError("outcome_counts must contain every OutcomeClass in canonical order")
        if sum(count for _, count in self.outcome_counts) != self.trial_count:
            raise ValueError("outcome_counts must sum to trial_count")
        if tuple(rate.metric for rate in self.rates) != tuple(CorrectnessMetric):
            raise ValueError("rates must contain every metric in canonical order")

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id.value,
            "trial_count": self.trial_count,
            "faulted_trial_count": self.faulted_trial_count,
            "outcome_counts": {
                outcome.value: count for outcome, count in self.outcome_counts
            },
            "rates": [rate.to_dict() for rate in self.rates],
        }


@dataclass(frozen=True, slots=True)
class CorrectnessSummary:
    policy_summaries: tuple[PolicyCorrectnessSummary, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.policy_summaries, tuple) or not all(
            isinstance(summary, PolicyCorrectnessSummary)
            for summary in self.policy_summaries
        ):
            raise TypeError(
                "policy_summaries must be tuple[PolicyCorrectnessSummary, ...]"
            )
        policy_ids = tuple(summary.policy_id for summary in self.policy_summaries)
        if len(policy_ids) != len(set(policy_ids)):
            raise ValueError("policy_summaries must not contain duplicate policy IDs")
        expected = tuple(policy for policy in PolicyID if policy in set(policy_ids))
        if policy_ids != expected:
            raise ValueError("policy_summaries must be in canonical B0-B4 order")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CORRECTNESS_EVALUATION_SCHEMA,
            "policy_summaries": [summary.to_dict() for summary in self.policy_summaries],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


def summarize_correctness(
    records: Iterable[CorrectnessEvaluationRecord],
) -> CorrectnessSummary:
    materialized = tuple(records)
    if not all(isinstance(record, CorrectnessEvaluationRecord) for record in materialized):
        raise TypeError("records must contain only CorrectnessEvaluationRecord")
    identities = [(record.policy_id, record.trial_id) for record in materialized]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate (policy_id, trial_id) evaluation record")
    summaries: list[PolicyCorrectnessSummary] = []
    for policy_id in PolicyID:
        policy_records = tuple(
            sorted(
                (record for record in materialized if record.policy_id is policy_id),
                key=lambda record: record.trial_id,
            )
        )
        if not policy_records:
            continue
        outcome_counts = tuple(
            (
                outcome,
                sum(record.outcome_class is outcome for record in policy_records),
            )
            for outcome in OutcomeClass
        )
        faulted_records = tuple(
            record for record in policy_records if record.fault_id is not None
        )
        rates = []
        for metric in CorrectnessMetric:
            if metric in GATE_G1_METRICS:
                applicable = [
                    record
                    for record in policy_records
                    if metric in record.metric_opportunities
                ]
                numerator = sum(
                    metric in record.metric_violations for record in applicable
                )
                denominator = len(applicable)
            elif metric is CorrectnessMetric.SILENT_SEMANTIC_ERROR_RATE:
                numerator = sum(
                    record.outcome_class
                    is OutcomeClass.O4_SILENT_SEMANTIC_VIOLATION
                    for record in faulted_records
                )
                denominator = len(faulted_records)
            elif metric is CorrectnessMetric.EXPLICIT_NON_SUCCESS_RATE:
                numerator = sum(
                    record.outcome_class is OutcomeClass.O3_EXPLICIT_NON_SUCCESS
                    for record in faulted_records
                )
                denominator = len(faulted_records)
            elif metric is CorrectnessMetric.RECOVERY_RATE:
                numerator = sum(
                    record.outcome_class
                    in {
                        OutcomeClass.O1_CORRECT_TRANSPARENT_RECOVERY,
                        OutcomeClass.O2_CORRECT_DEGRADED_RECOVERY,
                    }
                    for record in faulted_records
                )
                denominator = len(faulted_records)
            else:
                raise AssertionError(f"unhandled correctness metric {metric}")
            rates.append(RateCount(metric, numerator, denominator))
        summaries.append(
            PolicyCorrectnessSummary(
                policy_id=policy_id,
                trial_count=len(policy_records),
                faulted_trial_count=len(faulted_records),
                outcome_counts=outcome_counts,
                rates=tuple(rates),
            )
        )
    return CorrectnessSummary(tuple(summaries))
