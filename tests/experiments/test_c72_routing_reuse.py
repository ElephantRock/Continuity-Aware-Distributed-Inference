from __future__ import annotations

from pathlib import Path

import pytest

from continuity.entities import ReconcileOutcome
from experiments.c7_protocol import (
    C7_PROTOCOL_FINGERPRINT,
    EfficiencyEligibility,
    ExperimentSeries,
    ParameterSource,
    WorkloadClass,
)
from experiments.c72_routing_reuse import (
    C72ProgramCase,
    C72ProgramTopology,
    C72OperationCase,
    build_paired_manifests,
    derive_c7_admissible_trace,
    derive_pinned_mooncake_c7_admissible,
    evaluate_paired_program,
)
from experiments.mooncake_trace import mooncake_manifest
from experiments.trace_workload import NormalizedTraceDataset, NormalizedTraceRecord
from simulator.continuity_policy import build_baseline_policies
from simulator.inference_cost_runtime import load_c64f_runtime_profiles
from simulator.policies import PolicyID, PolicyObservation, WorkerObservation


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts" / "c6.4f" / "exhaustive-source-equivalence.json"


class FakeAuthority:
    def __init__(self, *, current: bool = True, compatible: bool = True) -> None:
        self.current = current
        self.compatible = compatible

    def attempt_current(self, request_id: str, attempt_id: str) -> bool:
        return self.current

    def state_compatible(
        self,
        state_id: str,
        *,
        program_id: str,
        session_id: str,
        continuation_id: str,
        request_id: str,
        attempt_id: str,
    ) -> bool:
        return self.compatible


@pytest.fixture(scope="module")
def profile():
    return load_c64f_runtime_profiles(ARTIFACT)["a100-80gb"]


def _workers(*, state_worker_lowest: bool = False) -> tuple[WorkerObservation, ...]:
    if state_worker_lowest:
        w1_queue, w2_queue = 0, 1
    else:
        w1_queue, w2_queue = 1, 0
    return (
        WorkerObservation("w1", True, 1, 0, w1_queue),
        WorkerObservation("w2", True, 1, 0, w2_queue),
    )


def _observation(
    request_id: str,
    continuation_id: str,
    *,
    eligible_state: bool,
    state_worker_lowest: bool = False,
) -> PolicyObservation:
    return PolicyObservation(
        request_id=request_id,
        workers=_workers(state_worker_lowest=state_worker_lowest),
        attempt_id=f"attempt:{request_id}",
        attempt_authority="CURRENT",
        session_id="session:p",
        session_preferred_location="w2",
        continuation_id=continuation_id,
        continuation_ancestry=(),
        state_candidate_key="candidate:x" if eligible_state else None,
        exact_state_id="state:x" if eligible_state else None,
        state_locations=("w1",) if eligible_state else (),
        state_provenance=(),
        producer_attempt_id="attempt:producer" if eligible_state else None,
        binding_id=None,
        binding_epoch=None,
        evidence_authority="AUTHORITATIVE" if eligible_state else None,
        evidence_status="VALID" if eligible_state else None,
        evidence_freshness=0.0 if eligible_state else None,
        program_id="program:p",
        state_lifecycle="ACTIVE" if eligible_state else None,
        reconciliation=ReconcileOutcome.MATCHED.name if eligible_state else None,
    )


def _manifests(series: ExperimentSeries, *, parameters, parameter_sources):
    return build_paired_manifests(
        experiment_id=f"c72-test-{series.value.lower()}",
        git_commit="f" * 40,
        series=series,
        workload_class=WorkloadClass.SYNTHETIC_STRESS,
        hardware_id="a100-80gb",
        program_objective="all required operations complete",
        seed=0,
        source_dataset_fingerprint=None,
        augmentation_fingerprint=None,
        parameters=parameters,
        parameter_sources=parameter_sources,
    )


def test_generic_admissible_derivation_filters_without_mutating_records() -> None:
    manifest = mooncake_manifest()
    source = NormalizedTraceDataset(
        manifest,
        (
            NormalizedTraceRecord("r0", "s0", 0, 0.0, 100, 10, None, 0),
            NormalizedTraceRecord("r1", "s1", 1, 1.0, 4095, 1, "g", 512),
            NormalizedTraceRecord("r2", "s2", 2, 2.0, 4096, 1, "g", 512),
            NormalizedTraceRecord("r3", "s3", 3, 3.0, 0, 1, None, 0),
        ),
    )

    derived = derive_c7_admissible_trace(source)
    assert [record.record_id for record in derived.source_order] == ["r0", "r1"]
    assert derived.source_order[0].to_dict() == source.source_order[0].to_dict()
    assert derived.source_order[1].to_dict() == source.source_order[1].to_dict()
    assert derived.manifest.field_origins == source.manifest.field_origins
    assert derived.manifest.normalization_version != source.manifest.normalization_version
    assert derived.fingerprint != source.fingerprint


def test_pinned_mooncake_derivation_rejects_manifest_spoofed_fixture() -> None:
    source = NormalizedTraceDataset(
        mooncake_manifest(),
        (NormalizedTraceRecord("r0", "s0", 0, 0.0, 100, 10, None, 0),),
    )
    with pytest.raises(ValueError, match="fingerprint drift"):
        derive_pinned_mooncake_c7_admissible(source)


def test_p1_paired_routing_reuse_is_driven_by_placement_not_policy_label(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    policies = build_baseline_policies(authority)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P1_DEEP_REUSE,
        topology=C72ProgramTopology.SERIAL,
        operations=(
            C72OperationCase("op:root", _observation("r0", "c0", eligible_state=False), 32, 2, 0),
            C72OperationCase("op:child", _observation("r1", "c1", eligible_state=True), 32, 2, 16),
        ),
    )
    manifests = _manifests(
        ExperimentSeries.P1_DEEP_REUSE,
        parameters=(
            ("cache_capacity_ratio", 1.0),
            ("reusable_prefix_fraction", 0.5),
            ("session_depth", 2),
        ),
        parameter_sources=(
            ("cache_capacity_ratio", ParameterSource.P_SRC4),
            ("reusable_prefix_fraction", ParameterSource.P_SRC4),
            ("session_depth", ParameterSource.P_SRC4),
        ),
    )

    result = evaluate_paired_program(
        policies=policies,
        manifests=manifests,
        case=case,
        oracle=FakeAuthority(current=True, compatible=True),
        profile=profile,
    )
    assert result.protocol_fingerprint == C7_PROTOCOL_FINGERPRINT
    by_policy = {item.policy_id: item for item in result.policy_results}

    assert by_policy[PolicyID.B0].consumed_reuse_tokens == 0
    assert by_policy[PolicyID.B1].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B2].consumed_reuse_tokens == 0
    assert by_policy[PolicyID.B3].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B4].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B1].recomputation_ratio < by_policy[PolicyID.B0].recomputation_ratio
    assert all(item.efficiency_eligibility is EfficiencyEligibility.ELIGIBLE for item in result.policy_results)


def test_worker_side_local_hit_is_common_to_b0_b3_when_placement_lands_local(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P1_DEEP_REUSE,
        topology=C72ProgramTopology.SERIAL,
        operations=(
            C72OperationCase(
                "op:child",
                _observation("r1", "c1", eligible_state=True, state_worker_lowest=True),
                32,
                2,
                16,
            ),
        ),
    )
    manifests = _manifests(
        ExperimentSeries.P1_DEEP_REUSE,
        parameters=(("session_depth", 2),),
        parameter_sources=(("session_depth", ParameterSource.P_SRC4),),
    )
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=manifests,
        case=case,
        oracle=FakeAuthority(current=True, compatible=True),
        profile=profile,
    )
    by_policy = {item.policy_id: item for item in result.policy_results}
    assert by_policy[PolicyID.B0].operation_results[0].worker_id == "w1"
    assert by_policy[PolicyID.B0].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B1].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B3].consumed_reuse_tokens == 16


def test_independent_semantic_oracle_excludes_invalid_local_reuse_from_ranking(profile) -> None:
    policy_authority = FakeAuthority(current=True, compatible=False)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P1_DEEP_REUSE,
        topology=C72ProgramTopology.SERIAL,
        operations=(
            C72OperationCase("op:bad", _observation("r1", "c1", eligible_state=True), 32, 2, 16),
        ),
    )
    manifests = _manifests(
        ExperimentSeries.P1_DEEP_REUSE,
        parameters=(("session_depth", 2),),
        parameter_sources=(("session_depth", ParameterSource.P_SRC4),),
    )
    result = evaluate_paired_program(
        policies=build_baseline_policies(policy_authority),
        manifests=manifests,
        case=case,
        oracle=FakeAuthority(current=True, compatible=False),
        profile=profile,
    )
    by_policy = {item.policy_id: item for item in result.policy_results}

    assert by_policy[PolicyID.B1].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B1].semantic_violation_count == 1
    assert (
        by_policy[PolicyID.B1].efficiency_eligibility
        is EfficiencyEligibility.SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING
    )
    assert by_policy[PolicyID.B3].semantic_violation_count == 1
    assert by_policy[PolicyID.B4].consumed_reuse_tokens == 0
    assert by_policy[PolicyID.B4].semantic_violation_count == 0
    assert by_policy[PolicyID.B4].efficiency_eligibility is EfficiencyEligibility.ELIGIBLE


def test_p4_fanout_completion_is_maximum_required_branch_compute(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    operations = (
        C72OperationCase("branch:1", _observation("r1", "c1", eligible_state=True), 64, 2, 32),
        C72OperationCase("branch:2", _observation("r2", "c2", eligible_state=True), 128, 2, 64),
    )
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
        topology=C72ProgramTopology.FANOUT,
        operations=operations,
    )
    manifests = _manifests(
        ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
        parameters=(("fanout_width", 2), ("shared_prefix_fraction", 0.5)),
        parameter_sources=(
            ("fanout_width", ParameterSource.P_SRC4),
            ("shared_prefix_fraction", ParameterSource.P_SRC4),
        ),
    )
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=manifests,
        case=case,
        oracle=FakeAuthority(current=True, compatible=True),
        profile=profile,
    )
    for policy_result in result.policy_results:
        values = [item.modeled_compute_seconds for item in policy_result.operation_results]
        assert all(value is not None for value in values)
        expected = max(float(value) for value in values if value is not None)
        assert policy_result.fanout_completion_time_seconds == expected
        assert policy_result.program_completion_time_seconds == expected


def test_p7_has_no_reuse_advantage_and_same_modeled_compute(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P7_STATELESS_OVERHEAD,
        topology=C72ProgramTopology.SERIAL,
        operations=(
            C72OperationCase("op:stateless", _observation("r0", "c0", eligible_state=False), 32, 2, 0),
        ),
    )
    manifests = _manifests(
        ExperimentSeries.P7_STATELESS_OVERHEAD,
        parameters=(("arrival_intensity", 1.0), ("worker_count", 2)),
        parameter_sources=(
            ("arrival_intensity", ParameterSource.P_SRC4),
            ("worker_count", ParameterSource.P_SRC4),
        ),
    )
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=manifests,
        case=case,
        oracle=FakeAuthority(current=True, compatible=True),
        profile=profile,
    )
    compute = {
        item.operation_results[0].modeled_compute_seconds for item in result.policy_results
    }
    assert len(compute) == 1
    assert all(item.consumed_reuse_tokens == 0 for item in result.policy_results)
    assert all(item.state_reuse_ratio == 0.0 for item in result.policy_results)
    assert all(item.recomputation_ratio == 0.0 for item in result.policy_results)


def test_paired_manifests_cannot_drift_between_policies(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P7_STATELESS_OVERHEAD,
        topology=C72ProgramTopology.SERIAL,
        operations=(C72OperationCase("op", _observation("r0", "c0", eligible_state=False), 32, 2, 0),),
    )
    manifests = dict(
        _manifests(
            ExperimentSeries.P7_STATELESS_OVERHEAD,
            parameters=(("arrival_intensity", 1.0), ("worker_count", 2)),
            parameter_sources=(
                ("arrival_intensity", ParameterSource.P_SRC4),
                ("worker_count", ParameterSource.P_SRC4),
            ),
        )
    )
    manifests[PolicyID.B1] = build_paired_manifests(
        experiment_id="different-experiment",
        git_commit="f" * 40,
        series=ExperimentSeries.P7_STATELESS_OVERHEAD,
        workload_class=WorkloadClass.SYNTHETIC_STRESS,
        hardware_id="a100-80gb",
        program_objective="all required operations complete",
        seed=0,
        source_dataset_fingerprint=None,
        augmentation_fingerprint=None,
        parameters=(("arrival_intensity", 1.0), ("worker_count", 2)),
        parameter_sources=(
            ("arrival_intensity", ParameterSource.P_SRC4),
            ("worker_count", ParameterSource.P_SRC4),
        ),
    )[PolicyID.B1]

    with pytest.raises(ValueError, match="differ outside policy_id"):
        evaluate_paired_program(
            policies=build_baseline_policies(authority),
            manifests=manifests,
            case=case,
            oracle=FakeAuthority(current=True, compatible=True),
            profile=profile,
        )
