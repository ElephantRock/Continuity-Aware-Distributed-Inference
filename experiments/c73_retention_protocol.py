from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Iterable

from continuity.entities import StateLifecycle
from experiments.c7_protocol import (
    AXES,
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    C7_PROTOCOL_FINGERPRINT,
    C7_PROTOCOL_SCHEMA,
    CONVERGENCE_PREFIXES,
    STOCHASTIC_SEEDS,
)
from simulator.continuity_policy import RetentionDisposition


C73_RETENTION_PROTOCOL_SCHEMA = "cadi.c7.3a.retention-protocol.v1"
C73_RETENTION_MANIFEST_SCHEMA = "cadi.c7.3a.retention-manifest.v1"
C73_BASE_COMMIT = "47f1574c79f84c2e32e432a5da0f0522a6436937"
C73_PRIMARY_TTL_SECONDS = 5.0
C73_TTL_SENSITIVITY_SECONDS = (0.25, 1.0, 5.0, 30.0, 120.0)
C73_EVENT_ORDER = (
    "VALIDITY_OR_TERMINAL_TRANSITION",
    "LIFECYCLE_RECOMPUTE_AND_TERMINAL_RELEASE",
    "FIXED_TTL_EXPIRY",
    "REUSE_LOOKUP_AND_SEMANTIC_VALIDITY",
    "SUCCESSFUL_REUSE_TOUCH",
    "STATE_ADMISSION",
    "CAPACITY_ENFORCEMENT",
    "INTERVAL_ACCOUNTING_CHECKPOINT",
)


class RetentionPolicyID(str, Enum):
    LRU = "LRU"
    FIXED_TTL = "FIXED_TTL"
    SESSION_PINNING = "SESSION_PINNING"
    LIFECYCLE_B4 = "LIFECYCLE_B4"


class CapacityOutcome(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    CAPACITY_INFEASIBLE = "CAPACITY_INFEASIBLE"


class ResidencyEndReason(str, Enum):
    REUSE = "REUSE"
    EVICTION = "EVICTION"
    TTL_EXPIRY = "TTL_EXPIRY"
    RELEASE = "RELEASE"
    INVALIDATION = "INVALIDATION"
    PROGRAM_END = "PROGRAM_END"


_LIFECYCLE_PRIORITY = {
    StateLifecycle.ACTIVE: 3,
    StateLifecycle.WAITING: 2,
    StateLifecycle.SPECULATIVE: 1,
    StateLifecycle.TERMINAL: 0,
}

_B4_DISPOSITION = {
    StateLifecycle.ACTIVE: RetentionDisposition.PROTECT,
    StateLifecycle.WAITING: RetentionDisposition.RETAIN,
    StateLifecycle.SPECULATIVE: RetentionDisposition.BEST_EFFORT,
    StateLifecycle.TERMINAL: RetentionDisposition.RELEASE,
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonnegative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _positive_int(value: Any, name: str) -> int:
    result = _nonnegative_int(value, name)
    if result == 0:
        raise ValueError(f"{name} must be positive")
    return result


def _finite_nonnegative(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return result


def derive_state_lifecycle(
    dependent_lifecycles: Iterable[StateLifecycle],
) -> StateLifecycle:
    values = tuple(dependent_lifecycles)
    if not all(isinstance(value, StateLifecycle) for value in values):
        raise TypeError("dependent_lifecycles must contain StateLifecycle values")
    for lifecycle in (
        StateLifecycle.ACTIVE,
        StateLifecycle.WAITING,
        StateLifecycle.SPECULATIVE,
    ):
        if lifecycle in values:
            return lifecycle
    return StateLifecycle.TERMINAL


def capacity_bytes(*, reference_working_set_bytes: int, cache_capacity_ratio: float) -> int:
    reference = _positive_int(reference_working_set_bytes, "reference_working_set_bytes")
    ratio = _finite_nonnegative(cache_capacity_ratio, "cache_capacity_ratio")
    if ratio not in AXES["cache_capacity_ratio"].values:
        raise ValueError("cache_capacity_ratio must be a frozen C7.1 axis value")
    return math.floor(reference * ratio)


def lru_eviction_key(
    *, last_touch_time_seconds: float, admission_ordinal: int, state_id: str
) -> tuple[float, int, str]:
    touch = _finite_nonnegative(last_touch_time_seconds, "last_touch_time_seconds")
    ordinal = _nonnegative_int(admission_ordinal, "admission_ordinal")
    if not isinstance(state_id, str) or not state_id:
        raise ValueError("state_id must be a non-empty string")
    return (touch, ordinal, state_id)


def lifecycle_eviction_key(
    *, lifecycle: StateLifecycle, last_touch_time_seconds: float,
    admission_ordinal: int, state_id: str
) -> tuple[int, float, int, str]:
    if not isinstance(lifecycle, StateLifecycle):
        raise TypeError("lifecycle must be StateLifecycle")
    return (
        _LIFECYCLE_PRIORITY[lifecycle],
        *lru_eviction_key(
            last_touch_time_seconds=last_touch_time_seconds,
            admission_ordinal=admission_ordinal,
            state_id=state_id,
        ),
    )


def fixed_ttl_expiry_seconds(*, admission_time_seconds: float, ttl_seconds: float) -> float:
    admission = _finite_nonnegative(admission_time_seconds, "admission_time_seconds")
    ttl = _finite_nonnegative(ttl_seconds, "ttl_seconds")
    if ttl not in C73_TTL_SENSITIVITY_SECONDS:
        raise ValueError("ttl_seconds must be one of the predeclared sensitivity values")
    return admission + ttl


def fixed_ttl_expired(*, now_seconds: float, expiry_seconds: float) -> bool:
    now = _finite_nonnegative(now_seconds, "now_seconds")
    expiry = _finite_nonnegative(expiry_seconds, "expiry_seconds")
    return now >= expiry


def strict_capacity_outcome(*, protected_bytes: int, capacity: int) -> CapacityOutcome:
    protected = _nonnegative_int(protected_bytes, "protected_bytes")
    limit = _nonnegative_int(capacity, "capacity")
    return (
        CapacityOutcome.CAPACITY_INFEASIBLE
        if protected > limit
        else CapacityOutcome.ELIGIBLE
    )


def residency_interval_byte_seconds(
    *, state_size_bytes: int, start_seconds: float, end_seconds: float,
    end_reason: ResidencyEndReason
) -> tuple[float, float]:
    size = _nonnegative_int(state_size_bytes, "state_size_bytes")
    start = _finite_nonnegative(start_seconds, "start_seconds")
    end = _finite_nonnegative(end_seconds, "end_seconds")
    if end < start:
        raise ValueError("residency interval end cannot precede start")
    if not isinstance(end_reason, ResidencyEndReason):
        raise TypeError("end_reason must be ResidencyEndReason")
    byte_seconds = size * (end - start)
    if end_reason is ResidencyEndReason.REUSE:
        return byte_seconds, 0.0
    return 0.0, byte_seconds


def tool_return_ttft_seconds(
    *, resume_eligibility_seconds: float, service_start_seconds: float,
    recompute_prefill_seconds: float, decode_fixed_seconds: float,
    decode_seconds_per_context_token_step: float, full_context_tokens: int,
) -> float:
    resume = _finite_nonnegative(resume_eligibility_seconds, "resume_eligibility_seconds")
    start = _finite_nonnegative(service_start_seconds, "service_start_seconds")
    if start < resume:
        raise ValueError("service start cannot precede resume eligibility")
    recompute = _finite_nonnegative(recompute_prefill_seconds, "recompute_prefill_seconds")
    fixed = _finite_nonnegative(decode_fixed_seconds, "decode_fixed_seconds")
    slope = _finite_nonnegative(
        decode_seconds_per_context_token_step,
        "decode_seconds_per_context_token_step",
    )
    context = _nonnegative_int(full_context_tokens, "full_context_tokens")
    first_token_completion = start + recompute + fixed + slope * context
    return first_token_completion - resume


@dataclass(frozen=True, slots=True)
class RetentionProtocol:
    base_commit: str = C73_BASE_COMMIT
    parent_protocol_schema: str = C7_PROTOCOL_SCHEMA
    parent_protocol_fingerprint: str = C7_PROTOCOL_FINGERPRINT

    def __post_init__(self) -> None:
        if self.base_commit != C73_BASE_COMMIT:
            raise ValueError("C7.3a base commit is frozen")
        if self.parent_protocol_schema != C7_PROTOCOL_SCHEMA:
            raise ValueError("C7.3a must bind the frozen C7.1 schema")
        if self.parent_protocol_fingerprint != C7_PROTOCOL_FINGERPRINT:
            raise ValueError("C7.3a must bind the frozen C7.1 fingerprint")
        if C73_PRIMARY_TTL_SECONDS not in C73_TTL_SENSITIVITY_SECONDS:
            raise ValueError("primary TTL must belong to the sensitivity grid")
        if C73_TTL_SENSITIVITY_SECONDS != AXES["tool_gap_seconds"].values:
            raise ValueError("TTL sensitivity grid must equal the frozen tool-gap grid")

    def to_dict(self) -> dict[str, Any]:
        p2_axes = {
            name: AXES[name].to_dict()
            for name in (
                "tool_gap_seconds",
                "tool_return_probability",
                "cache_capacity_ratio",
            )
        }
        p3_axes = {
            name: AXES[name].to_dict()
            for name in (
                "branch_width",
                "speculative_fraction",
                "cache_capacity_ratio",
            )
        }
        return {
            "schema": C73_RETENTION_PROTOCOL_SCHEMA,
            "base_commit": self.base_commit,
            "parent_protocol": {
                "schema": self.parent_protocol_schema,
                "fingerprint": self.parent_protocol_fingerprint,
            },
            "comparative_result_inspection": "NONE",
            "policy_ids": [policy.value for policy in RetentionPolicyID],
            "lifecycle_validity_boundary": {
                "lifecycle_role": "RETENTION_POLICY_ONLY",
                "validity_role": "CORRECTNESS_AND_SEMANTIC_REUSE",
                "derivation_priority": [
                    StateLifecycle.ACTIVE.name,
                    StateLifecycle.WAITING.name,
                    StateLifecycle.SPECULATIVE.name,
                    StateLifecycle.TERMINAL.name,
                ],
                "derivation_basis": "live dependent Continuations, not origin Continuation alone",
                "invalid_state_rule": "INVALID State is never reusable and is released independently of retention policy",
            },
            "event_order": list(C73_EVENT_ORDER),
            "policies": {
                RetentionPolicyID.LRU.value: {
                    "admission": "valid non-TERMINAL State if object size <= byte capacity",
                    "reuse_refreshes_recency": True,
                    "eviction": "least recently touched until within byte capacity",
                    "tie_break": ["last_touch_time_seconds", "admission_ordinal", "StateID"],
                    "lifecycle_visible_for_ranking": False,
                    "capacity_overcommit": False,
                },
                RetentionPolicyID.FIXED_TTL.value: {
                    "primary_ttl_seconds": C73_PRIMARY_TTL_SECONDS,
                    "sensitivity_ttl_seconds": list(C73_TTL_SENSITIVITY_SECONDS),
                    "expiry": "admission_time + ttl",
                    "reuse_refreshes_expiry": False,
                    "expiry_at_equality_precedes_reuse": True,
                    "best_of_grid_rule": "oracle-tuned sensitivity upper bound only; never sole ordinary baseline",
                    "capacity_overcommit": False,
                },
                RetentionPolicyID.SESSION_PINNING.value: {
                    "pin_scope": "all valid State belonging to a live Session",
                    "release": "Session has no live dependent Continuation",
                    "visible_information": ["SessionID", "SessionLiveStatus"],
                    "b4_lifecycle_visible": False,
                    "pinned_evictable_for_ordinary_pressure": False,
                    "protected_over_capacity": CapacityOutcome.CAPACITY_INFEASIBLE.value,
                    "capacity_overcommit": False,
                },
                RetentionPolicyID.LIFECYCLE_B4.value: {
                    "classes": {
                        lifecycle.name: {
                            "priority": _LIFECYCLE_PRIORITY[lifecycle],
                            "disposition": _B4_DISPOSITION[lifecycle].value,
                        }
                        for lifecycle in StateLifecycle
                    },
                    "eviction": "lowest lifecycle priority first; equal-priority LRU tie-break",
                    "active_protected_evictable_for_ordinary_pressure": False,
                    "protected_over_capacity": CapacityOutcome.CAPACITY_INFEASIBLE.value,
                    "capacity_overcommit": False,
                },
            },
            "capacity_normalization": {
                "reference_working_set_bytes": (
                    "max over event time of sum(State.size_bytes) for valid States with lifecycle != TERMINAL"
                ),
                "capacity_bytes": "floor(cache_capacity_ratio * reference_working_set_bytes)",
                "reference_must_be_positive": True,
                "ratio_axis_source": AXES["cache_capacity_ratio"].source.value,
                "policy_visible_future_information": False,
                "same_capacity_for_all_policies": True,
            },
            "state_size_rule": {
                "immutable_workload_input": True,
                "provenance_required": True,
                "c6_memory_mapping_evidence": "P-SRC2 when used",
                "synthetic_state_token_choice": "P-SRC4",
                "new_state_size_sweep": False,
            },
            "residency_intervals": {
                "starts": ["ADMISSION_OR_READMISSION", "AFTER_SUCCESSFUL_REUSE"],
                "ends": [reason.value for reason in ResidencyEndReason],
                "useful_iff_end_reason": ResidencyEndReason.REUSE.value,
                "byte_seconds": "State.size_bytes * interval_duration_seconds",
                "useful_wasted_partition": True,
            },
            "tool_return_ttft": {
                "definition": "first generated-token completion minus tool-return/resume eligibility time",
                "first_token_service": (
                    "required recompute-prefill + first carried C6.3 decode step"
                ),
                "queue_delay_included": True,
                "full_decode_substitution_forbidden": True,
            },
            "p2_axes": p2_axes,
            "p3_axes": p3_axes,
            "statistics": {
                "stochastic_seeds": list(STOCHASTIC_SEEDS),
                "convergence_prefixes": list(CONVERGENCE_PREFIXES),
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "bootstrap_seed": BOOTSTRAP_SEED,
                "cluster_unit": "Program; Session only when Program is not independent",
            },
            "invalid_result_conditions": [
                "policy-specific reuse-opportunity denominator",
                "capacity overcommit",
                "silent eviction of strict pinned/protected State",
                "post-result TTL selection presented as ordinary baseline",
                "lifecycle used as semantic-validity authority",
                "retention policy changes C7.1 source admissibility",
                "observed P2/P3 result mutates this protocol in place",
            ],
            "revision_rule": (
                "After any P2/P3 comparative result is observed, changes require a new retention protocol version and preservation of prior results"
            ),
        }

    @property
    def fingerprint(self) -> str:
        return _sha256(self.to_dict())


FROZEN_C73_RETENTION_PROTOCOL = RetentionProtocol()


def main() -> None:
    payload = FROZEN_C73_RETENTION_PROTOCOL.to_dict()
    print(_canonical_json({**payload, "protocol_fingerprint": FROZEN_C73_RETENTION_PROTOCOL.fingerprint}))


if __name__ == "__main__":
    main()
