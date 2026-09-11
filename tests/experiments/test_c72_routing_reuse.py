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
    C72OperationCase,
    C72ProgramCase,
    C72ProgramTopology,
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
    resident: bool = True,
    state_worker_lowest: bool = False,
    stateless: bool = False,
) -> PolicyObservation:
    if stateless and eligible_state:
        raise ValueError("stateless fixture cannot be reuse eligible")
    return PolicyObservation(
        request_id=request_id,
        workers=_workers(state_worker_lowest=state_worker_lowest),
        attempt_id=f"attempt:{request_id}",
        attempt_authority="CURRENT",
        session_id="session:p",
        session_preferred_location=None if stateless else "w2",
        continuation_id=continuation_id,
        continuation_ancestry=(),
        state_candidate_key="candidate:x" if eligible_state else None,
        exact_state_id="state:x" if eligible_state else None,
        state_locations=("w1",) if eligible_state and resident else (),
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


def _p1_manifests(*, session_depth: int = 2, reusable_fraction: float = 0.5, worker_count: int = 2):
    return _manifests(
        ExperimentSeries.P1_DEEP_REUSE,
        parameters=(
            ("reusable_prefix_fraction", reusable_fraction),
            ("session_depth", session_depth),
            ("worker_count", worker_count),
        ),
        parameter_sources=(
            ("reusable_prefix_fraction", ParameterSource.P_SRC4),
            ("session_depth", ParameterSource.P_SRC4),
            ("worker_count", ParameterSource.P_SRC4),
        ),
    )


def _p4_manifests(*, fanout_width: int = 2, shared_fraction: float = 0.5):
    return _manifests(
        ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
        parameters=(
            ("fanout_width", fanout_width),
            ("shared_prefix_fraction", shared_fraction),
        ),
        parameter_sources=(
            ("fanout_width", ParameterSource.P_SRC4),
            ("shared_prefix_fraction", ParameterSource.P_SRC4),
        ),
    )


def _p7_manifests(*, worker_count: int = 2):
    return _manifests(
        ExperimentSeries.P7_STATELESS_OVERHEAD,
        parameters=(("arrival_intensity", 1.0), ("worker_count", worker_count)),
        parameter_sources=(
            ("arrival_intensity", ParameterSource.P_SRC4),
            ("worker_count", ParameterSource.P_SRC4),
        ),
    )


def _p1_case(
    child_observation: PolicyObservation,
    *,
    child_input: int = 32,
    child_output: int = 2,
    child_reuse: int = 16,
    ready=(("w1", 0.0), ("w2", 0.0)),
) -> C72ProgramCase:
    return C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P1_DEEP_REUSE,
        topology=C72ProgramTopology.SERIAL,
        operations=(
            C72OperationCase("op:root", _observation("r0", "c0", eligible_state=False), 32, 2, 0),
            C72OperationCase("op:child", child_observation, child_input, child_output, child_reuse),
        ),
        worker_ready_times_seconds=ready,
    )


def test_generic_admissible_derivation_uses_only_tokens_and_preserves_nullable_prefix_metadata() -> None:
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
    assert derived.source_order[0].prefix_group_id is None
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
    with pytest.raises(ValueError, match="fingerprint"):
        derive_pinned_mooncake_c7_admissible(source)


def test_p1_paired_routing_reuse_is_driven_by_placement_not_policy_label(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = _p1_case(_observation("r1", "c1", eligible_state=True))
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=_p1_manifests(),
        case=case,
        oracle=FakeAuthority(current=True, compatible=True),
        profile=profile,
    )
    assert result.protocol_fingerprint == C7_PROTOCOL_FINGERPRINT
    assert result.program_case_fingerprint == case.fingerprint
    by_policy = {item.policy_id: item for item in result.policy_results}
    assert by_policy[PolicyID.B0].consumed_reuse_tokens == 0
    assert by_policy[PolicyID.B1].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B2].consumed_reuse_tokens == 0
    assert by_policy[PolicyID.B3].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B4].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B1].recomputation_ratio < by_policy[PolicyID.B0].recomputation_ratio
    assert all(item.efficiency_eligibility is EfficiencyEligibility.ELIGIBLE for item in result.policy_results)


def test_reuse_eligible_but_nonresident_state_stays_in_denominator_and_is_cold(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = _p1_case(_observation("r1", "c1", eligible_state=True, resident=False))
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=_p1_manifests(),
        case=case,
        oracle=FakeAuthority(current=True, compatible=True),
        profile=profile,
    )
    for policy_result in result.policy_results:
        assert policy_result.eligible_reuse_opportunities == 1
        assert policy_result.eligible_reuse_tokens == 16
        assert policy_result.consumed_reuse_opportunities == 0
        assert policy_result.consumed_reuse_tokens == 0
        assert policy_result.cold_continuation_rate == 1.0


def test_worker_side_local_hit_is_common_to_b0_b3_when_placement_lands_local(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = _p1_case(_observation("r1", "c1", eligible_state=True, state_worker_lowest=True))
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority),
        manifests=_p1_manifests(),
        case=case,
        oracle=FakeAuthority(current=True, compatible=True),
        profile=profile,
    )
    by_policy = {item.policy_id: item for item in result.policy_results}
    assert by_policy[PolicyID.B0].operation_results[1].worker_id == "w1"
    assert by_policy[PolicyID.B0].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B1].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B3].consumed_reuse_tokens == 16


def test_independent_semantic_oracle_excludes_invalid_local_reuse_from_ranking(profile) -> None:
    policy_authority = FakeAuthority(current=True, compatible=False)
    case = _p1_case(_observation("r1", "c1", eligible_state=True))
    result = evaluate_paired_program(
        policies=build_baseline_policies(policy_authority),
        manifests=_p1_manifests(),
        case=case,
        oracle=FakeAuthority(current=True, compatible=False),
        profile=profile,
    )
    by_policy = {item.policy_id: item for item in result.policy_results}
    assert by_policy[PolicyID.B1].consumed_reuse_tokens == 16
    assert by_policy[PolicyID.B1].semantic_violation_count == 1
    assert by_policy[PolicyID.B1].efficiency_eligibility is EfficiencyEligibility.SEMANTICALLY_INVALID_FOR_EFFICIENCY_RANKING
    assert by_policy[PolicyID.B3].semantic_violation_count == 1
    assert by_policy[PolicyID.B4].consumed_reuse_tokens == 0
    assert by_policy[PolicyID.B4].semantic_violation_count == 0
    assert by_policy[PolicyID.B4].efficiency_eligibility is EfficiencyEligibility.ELIGIBLE


@pytest.mark.parametrize(
    ("case", "manifests", "message"),
    [
        (_p1_case(_observation("r1", "c1", eligible_state=True)), _p1_manifests(session_depth=4), "session_depth"),
        (_p1_case(_observation("r1", "c1", eligible_state=True)), _p1_manifests(worker_count=4), "worker_count"),
        (
            C72ProgramCase(
                program_id="program:p",
                series=ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
                topology=C72ProgramTopology.FANOUT,
                operations=(
                    C72OperationCase("b1", _observation("r1", "c1", eligible_state=True), 64, 2, 32),
                    C72OperationCase("b2", _observation("r2", "c2", eligible_state=True), 128, 2, 64),
                ),
            ),
            _p4_manifests(fanout_width=4),
            "fanout_width",
        ),
    ],
)
def test_realized_structural_axes_are_fenced(profile, case, manifests, message) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    with pytest.raises(ValueError, match=message):
        evaluate_paired_program(
            policies=build_baseline_policies(authority),
            manifests=manifests,
            case=case,
            oracle=FakeAuthority(current=True, compatible=True),
            profile=profile,
        )


def test_p1_reuse_fraction_is_bound_to_realized_child_operations(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = _p1_case(_observation("r1", "c1", eligible_state=True), child_reuse=8)
    with pytest.raises(ValueError, match="reusable_prefix_fraction"):
        evaluate_paired_program(
            policies=build_baseline_policies(authority), manifests=_p1_manifests(), case=case,
            oracle=FakeAuthority(current=True, compatible=True), profile=profile,
        )


def test_p4_shared_prefix_fraction_is_bound_to_each_branch(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
        topology=C72ProgramTopology.FANOUT,
        operations=(
            C72OperationCase("b1", _observation("r1", "c1", eligible_state=True), 64, 2, 16),
            C72OperationCase("b2", _observation("r2", "c2", eligible_state=True), 128, 2, 64),
        ),
    )
    with pytest.raises(ValueError, match="shared_prefix_fraction"):
        evaluate_paired_program(
            policies=build_baseline_policies(authority), manifests=_p4_manifests(), case=case,
            oracle=FakeAuthority(current=True, compatible=True), profile=profile,
        )


def test_p4_completion_uses_terminal_minus_start_resource_time(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P4_FANOUT_SHARED_PREFIX,
        topology=C72ProgramTopology.FANOUT,
        operations=(
            C72OperationCase("branch:1", _observation("r1", "c1", eligible_state=True), 64, 2, 32, 2.0),
            C72OperationCase("branch:2", _observation("r2", "c2", eligible_state=True), 128, 2, 64, 2.0),
        ),
        program_start_time_seconds=1.0,
        worker_ready_times_seconds=(("w1", 4.0), ("w2", 3.0)),
    )
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority), manifests=_p4_manifests(), case=case,
        oracle=FakeAuthority(current=True, compatible=True), profile=profile,
    )
    for policy_result in result.policy_results:
        completions = [item.completion_time_seconds for item in policy_result.operation_results]
        assert all(value is not None for value in completions)
        terminal = max(float(value) for value in completions if value is not None)
        assert policy_result.program_terminal_time_seconds == terminal
        assert policy_result.program_completion_time_seconds == terminal - 1.0
        assert policy_result.fanout_start_time_seconds == 1.0
        assert policy_result.fanout_terminal_time_seconds == terminal
        assert policy_result.fanout_completion_time_seconds == terminal - 1.0


def test_serial_completion_obeys_release_and_worker_ready_time(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P7_STATELESS_OVERHEAD,
        topology=C72ProgramTopology.SERIAL,
        operations=(
            C72OperationCase(
                "op:stateless",
                _observation("r0", "c0", eligible_state=False, stateless=True),
                32, 2, 0, 3.0,
            ),
        ),
        program_start_time_seconds=1.0,
        worker_ready_times_seconds=(("w1", 10.0), ("w2", 5.0)),
    )
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority), manifests=_p7_manifests(), case=case,
        oracle=FakeAuthority(current=True, compatible=True), profile=profile,
    )
    for policy_result in result.policy_results:
        operation = policy_result.operation_results[0]
        assert operation.service_start_time_seconds == 5.0
        assert operation.modeled_service_seconds is not None
        assert operation.completion_time_seconds == 5.0 + operation.modeled_service_seconds
        assert policy_result.program_completion_time_seconds == operation.completion_time_seconds - 1.0
        assert operation.modeled_routing_control_seconds == 0.0


def test_p7_has_no_reuse_advantage_and_zero_unevidenced_control_component(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P7_STATELESS_OVERHEAD,
        topology=C72ProgramTopology.SERIAL,
        operations=(
            C72OperationCase(
                "op:stateless",
                _observation("r0", "c0", eligible_state=False, stateless=True),
                32, 2, 0,
            ),
        ),
    )
    result = evaluate_paired_program(
        policies=build_baseline_policies(authority), manifests=_p7_manifests(), case=case,
        oracle=FakeAuthority(current=True, compatible=True), profile=profile,
    )
    compute = {item.operation_results[0].modeled_compute_seconds for item in result.policy_results}
    assert len(compute) == 1
    assert all(item.consumed_reuse_tokens == 0 for item in result.policy_results)
    assert all(item.state_reuse_ratio == 0.0 for item in result.policy_results)
    assert all(item.recomputation_ratio == 0.0 for item in result.policy_results)
    assert all(item.operation_results[0].modeled_routing_control_seconds == 0.0 for item in result.policy_results)


@pytest.mark.parametrize(
    "mutator",
    [
        {"session_preferred_location": "w1"},
        {"state_candidate_key": "candidate:x"},
        {"exact_state_id": "state:x"},
        {"state_locations": ("w1",)},
        {"continuation_ancestry": ("parent",)},
        {"state_provenance": (("k", "v"),)},
    ],
)
def test_p7_fails_closed_on_cross_request_locality_or_continuity_hints(mutator) -> None:
    base = _observation("r0", "c0", eligible_state=False, stateless=True)
    values = {name: getattr(base, name) for name in base.__dataclass_fields__}
    values.update(mutator)
    observation = PolicyObservation(**values)
    with pytest.raises(ValueError, match="P7 stateless control"):
        C72ProgramCase(
            program_id="program:p",
            series=ExperimentSeries.P7_STATELESS_OVERHEAD,
            topology=C72ProgramTopology.SERIAL,
            operations=(C72OperationCase("op", observation, 32, 2, 0),),
        )


def test_program_case_fingerprint_changes_with_placement_relevant_observation() -> None:
    case_a = _p1_case(_observation("r1", "c1", eligible_state=True))
    case_b = _p1_case(_observation("r1", "c1", eligible_state=True, state_worker_lowest=True))
    assert case_a.fingerprint != case_b.fingerprint


def test_paired_manifests_cannot_drift_between_policies(profile) -> None:
    authority = FakeAuthority(current=True, compatible=True)
    case = C72ProgramCase(
        program_id="program:p",
        series=ExperimentSeries.P7_STATELESS_OVERHEAD,
        topology=C72ProgramTopology.SERIAL,
        operations=(
            C72OperationCase(
                "op", _observation("r0", "c0", eligible_state=False, stateless=True), 32, 2, 0
            ),
        ),
    )
    manifests = dict(_p7_manifests())
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
        parameter_sources=(("arrival_intensity", ParameterSource.P_SRC4), ("worker_count", ParameterSource.P_SRC4)),
    )[PolicyID.B1]
    with pytest.raises(ValueError, match="differ outside policy_id"):
        evaluate_paired_program(
            policies=build_baseline_policies(authority), manifests=manifests, case=case,
            oracle=FakeAuthority(current=True, compatible=True), profile=profile,
        )
