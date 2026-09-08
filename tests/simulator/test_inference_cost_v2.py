from dataclasses import replace

import pytest

from simulator.calibration_validation import (
    VIDUR_PINNED_COMMIT,
    CalibrationReferencePoint,
    ReferenceKind,
    SourceArtifactRef,
    split_reference_family,
)
from simulator.inference_cost import (
    InferenceCostWorkload,
    ParameterProvenance,
    ParameterSourceClass,
    SourcedScalar,
)
from simulator.inference_cost_v2 import (
    C64A_FRESH_PREFILL_AXES,
    C64A_FRESH_PREFILL_BOUNDARY,
    C64A_FRESH_REFERENCE_EVIDENCE,
    C64A_FRESH_TRANSFER_AXES,
    C64A_FRESH_TRANSFER_BOUNDARY,
    C64A_KNOT_PARTITION,
    C64A_REPRESENTATION_ID,
    CurveKnot,
    PiecewiseLinearCostCurve,
    RevisedInferenceCostProfile,
    assert_knots_match_c63_fit,
    estimate_revised_inference_cost,
    verify_fresh_boundary_against_c63_partition,
)


MODEL_CONFIG = SourceArtifactRef(
    path="vidur/config/model_config.py",
    git_blob_sha1="722299bbb556ccbab2b82609598be6b8c2963c29",
)
A100_ATTENTION = SourceArtifactRef(
    path="data/profiling/compute/a100/meta-llama/Llama-2-7b-hf/attention.csv",
    git_blob_sha1="6ce0a3beab1618969d429b4313666b5dff6850dd",
)
A100_MLP = SourceArtifactRef(
    path="data/profiling/compute/a100/meta-llama/Llama-2-7b-hf/mlp.csv",
    git_blob_sha1="479ed2f6ed22049ac444bca9fa44578532cbf28a",
)
A100_SEND_RECV = SourceArtifactRef(
    path="data/profiling/network/a100_dgx/send_recv.csv",
    git_blob_sha1="418cd50858fdd604c3da21eb3aaa06e583059865",
)

P_SRC2 = ParameterProvenance(
    source_class=ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE,
    reference="test-only pinned source fixture",
)


def _scalar(value: float, unit: str) -> SourcedScalar:
    return SourcedScalar(value=value, unit=unit, provenance=P_SRC2)


def _artifacts(kind: ReferenceKind) -> tuple[SourceArtifactRef, ...]:
    if kind is ReferenceKind.POINT_TO_POINT_TRANSFER:
        return (A100_SEND_RECV,)
    return (MODEL_CONFIG, A100_ATTENTION, A100_MLP)


def _point(kind: ReferenceKind, axis: int, index: int) -> CalibrationReferencePoint:
    return CalibrationReferencePoint(
        point_id=f"c6.3b:a100-80gb:{kind.value}:{axis}",
        hardware_id="a100-80gb",
        kind=kind,
        axis_value=axis,
        observed_seconds=1.0 + index * 0.001,
        derivation_id=f"test-{index}",
        source_commit=VIDUR_PINNED_COMMIT,
        source_artifacts=_artifacts(kind),
    )


def _partition(kind: ReferenceKind, axes: list[int]):
    return split_reference_family(
        [_point(kind, axis, index) for index, axis in enumerate(axes)]
    )


def _knot(axis: int, seconds: float, point_id: str) -> CurveKnot:
    return CurveKnot(
        axis_value=axis,
        seconds=_scalar(seconds, "seconds"),
        source_point_id=point_id,
    )


def _curve(
    curve_id: str,
    axis_unit: str,
    axes_seconds: list[tuple[int, float]],
) -> PiecewiseLinearCostCurve:
    return PiecewiseLinearCostCurve(
        curve_id=curve_id,
        axis_unit=axis_unit,
        knots=tuple(
            _knot(axis, seconds, f"fit:{curve_id}:{axis}")
            for axis, seconds in axes_seconds
        ),
    )


def _profile() -> RevisedInferenceCostProfile:
    return RevisedInferenceCostProfile(
        profile_id="test-v2",
        model_id="meta-llama/Llama-2-7b-hf",
        hardware_id="a100-80gb",
        prefill_curve=_curve(
            "prefill",
            "input-tokens",
            [(1, 0.1), (10, 1.0), (100, 10.0)],
        ),
        decode_fixed_seconds_per_output_token=_scalar(
            0.01, "seconds/output-token"
        ),
        decode_seconds_per_context_token_step=_scalar(
            0.001, "seconds/context-token-step"
        ),
        state_fixed_bytes=_scalar(0.0, "bytes"),
        state_bytes_per_token=_scalar(10.0, "bytes/token"),
        memory_capacity_bytes=_scalar(10_000.0, "bytes"),
        transfer_curve=_curve(
            "transfer",
            "bytes",
            [(10, 0.1), (100, 1.0), (1000, 10.0)],
        ),
    )


def test_piecewise_curve_interpolates_and_forbids_extrapolation() -> None:
    curve = _curve("curve", "input-tokens", [(10, 1.0), (20, 3.0), (40, 7.0)])

    assert curve.evaluate(10) == pytest.approx(1.0)
    assert curve.evaluate(15) == pytest.approx(2.0)
    assert curve.evaluate(30) == pytest.approx(5.0)
    assert curve.evaluate(40) == pytest.approx(7.0)

    with pytest.raises(ValueError, match="extrapolate"):
        curve.evaluate(9)
    with pytest.raises(ValueError, match="extrapolate"):
        curve.evaluate(41)


def test_curve_knots_are_strict_fit_only_psrc2_evidence() -> None:
    knot = _knot(64, 0.1, "fit-point")
    assert knot.source_partition == C64A_KNOT_PARTITION

    with pytest.raises(ValueError, match="only from C6.3 FIT"):
        replace(knot, source_partition="VALIDATION")

    synthetic_provenance = ParameterProvenance(
        source_class=ParameterSourceClass.SYNTHETIC_SENSITIVITY,
        reference="test synthetic",
    )
    synthetic_seconds = SourcedScalar(
        value=0.1,
        unit="seconds",
        provenance=synthetic_provenance,
        sensitivity=replace(
            __import__("simulator.inference_cost", fromlist=["SensitivityRange"]).SensitivityRange(
                low=0.05, high=0.2
            )
        ),
    )
    with pytest.raises(ValueError, match="P-SRC2"):
        CurveKnot(
            axis_value=64,
            seconds=synthetic_seconds,
            source_point_id="synthetic",
        )

    with pytest.raises(ValueError, match="strictly increasing"):
        PiecewiseLinearCostCurve(
            curve_id="bad",
            axis_unit="input-tokens",
            knots=(
                _knot(64, 0.1, "a"),
                _knot(64, 0.2, "b"),
            ),
        )


def test_revised_decode_composes_fixed_term_per_output_token() -> None:
    profile = _profile()
    workload = InferenceCostWorkload(
        input_tokens=10,
        output_tokens=4,
        reusable_prefix_tokens=5,
        state_tokens=10,
    )
    estimate = estimate_revised_inference_cost(profile, workload)

    steps = 4 * 10 + 4 * 3 // 2
    assert steps == 46
    assert estimate.decode_context_token_steps == 46
    assert estimate.decode_seconds == pytest.approx(4 * 0.01 + 0.001 * 46)
    assert estimate.decode_seconds != pytest.approx(0.01 + 0.001 * 46)


def test_recompute_uses_same_prefill_curve_and_zero_work_is_zero() -> None:
    profile = _profile()
    workload = InferenceCostWorkload(
        input_tokens=10,
        output_tokens=0,
        reusable_prefix_tokens=5,
        state_tokens=10,
    )
    estimate = estimate_revised_inference_cost(profile, workload)

    assert estimate.prefill_seconds == pytest.approx(1.0)
    assert estimate.recompute_tokens == 5
    assert estimate.recompute_seconds == pytest.approx(0.5)
    assert estimate.decode_seconds == 0.0

    no_recompute = estimate_revised_inference_cost(
        profile,
        InferenceCostWorkload(
            input_tokens=10,
            output_tokens=0,
            reusable_prefix_tokens=10,
            state_tokens=10,
        ),
    )
    assert no_recompute.recompute_seconds == 0.0


def test_profile_keeps_memory_mechanics_and_curve_transfer() -> None:
    profile = _profile()
    estimate = estimate_revised_inference_cost(
        profile,
        InferenceCostWorkload(
            input_tokens=10,
            output_tokens=1,
            reusable_prefix_tokens=10,
            state_tokens=10,
        ),
    )

    assert profile.representation_id == C64A_REPRESENTATION_ID
    assert estimate.state_bytes == pytest.approx(100.0)
    assert estimate.transfer_seconds == pytest.approx(1.0)
    assert estimate.memory_capacity_fraction == pytest.approx(0.01)
    assert profile.fingerprint == profile.fingerprint


def test_fresh_boundary_is_identity_only_and_exactly_frozen() -> None:
    assert C64A_FRESH_PREFILL_AXES == (127, 257, 509, 1021, 2039, 3079, 4001)
    assert C64A_FRESH_TRANSFER_AXES == (
        8192,
        32768,
        131072,
        524288,
        2105344,
        8396800,
        33562624,
        58728448,
    )
    for boundary in (
        C64A_FRESH_PREFILL_BOUNDARY,
        C64A_FRESH_TRANSFER_BOUNDARY,
    ):
        payload = boundary.to_dict()
        assert payload["contains_reference_timings"] is False
        assert payload["evidence_class"] == C64A_FRESH_REFERENCE_EVIDENCE
        assert "seconds" not in payload


def test_prefill_fresh_boundary_is_unseen_and_fit_bracketed() -> None:
    # Even ordinals are FIT. These identities bracket every frozen fresh point.
    axes = [
        64,
        80,
        128,
        160,
        320,
        352,
        512,
        544,
        1024,
        1088,
        2048,
        2112,
        3200,
        3264,
        4096,
        4160,
    ]
    partition = _partition(ReferenceKind.COLD_PREFILL, axes)
    verify_fresh_boundary_against_c63_partition(
        C64A_FRESH_PREFILL_BOUNDARY, partition
    )


def test_fresh_boundary_collision_fails_closed() -> None:
    # 127 is an exact frozen fresh axis. Put it into the source partition and
    # verify that the boundary checker rejects reuse, independent of timing data.
    axes = [64, 80, 127, 160, 320, 352, 4096, 4160]
    partition = _partition(ReferenceKind.COLD_PREFILL, axes)
    with pytest.raises(ValueError, match="already present in C6.3"):
        verify_fresh_boundary_against_c63_partition(
            C64A_FRESH_PREFILL_BOUNDARY, partition
        )


def test_transfer_fresh_boundary_is_unseen_and_fit_bracketed() -> None:
    fit_axes = [
        2048,
        18432,
        34816,
        133120,
        526336,
        2162688,
        8454144,
        33816576,
        58982400,
        67108864,
    ]
    axes: list[int] = []
    for index, fit_axis in enumerate(fit_axes):
        axes.append(fit_axis)
        if index + 1 < len(fit_axes):
            axes.append((fit_axis + fit_axes[index + 1]) // 2)
    partition = _partition(ReferenceKind.POINT_TO_POINT_TRANSFER, axes)
    verify_fresh_boundary_against_c63_partition(
        C64A_FRESH_TRANSFER_BOUNDARY, partition
    )


def test_validation_point_ids_cannot_enter_curve_knots() -> None:
    partition = _partition(ReferenceKind.COLD_PREFILL, [64, 80, 128, 160])
    fit_knots = tuple(
        CurveKnot(
            axis_value=point.axis_value,
            seconds=_scalar(point.observed_seconds, "seconds"),
            source_point_id=point.point_id,
        )
        for point in partition.fit
    )
    curve = PiecewiseLinearCostCurve(
        curve_id="fit-only",
        axis_unit="input-tokens",
        knots=fit_knots,
    )
    assert_knots_match_c63_fit(curve, partition)

    leaked = PiecewiseLinearCostCurve(
        curve_id="leaked",
        axis_unit="input-tokens",
        knots=(
            fit_knots[0],
            CurveKnot(
                axis_value=partition.validation[0].axis_value,
                seconds=_scalar(partition.validation[0].observed_seconds, "seconds"),
                source_point_id=partition.validation[0].point_id,
            ),
        ),
    )
    with pytest.raises(ValueError, match="VALIDATION points"):
        assert_knots_match_c63_fit(leaked, partition)
