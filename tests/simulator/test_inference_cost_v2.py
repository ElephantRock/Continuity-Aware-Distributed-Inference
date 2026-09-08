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
    SensitivityRange,
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
    return (
        (A100_SEND_RECV,)
        if kind is ReferenceKind.POINT_TO_POINT_TRANSFER
        else (MODEL_CONFIG, A100_ATTENTION, A100_MLP)
    )


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


def _knot(
    axis: int,
    seconds: float,
    point_id: str,
    *,
    kind: ReferenceKind,
    hardware: str = "a100-80gb",
) -> CurveKnot:
    return CurveKnot(
        hardware_id=hardware,
        reference_kind=kind,
        axis_value=axis,
        seconds=_scalar(seconds, "seconds"),
        source_point_id=point_id,
    )


def _curve(
    curve_id: str,
    kind: ReferenceKind,
    axes_seconds: list[tuple[int, float]],
    *,
    hardware: str = "a100-80gb",
) -> PiecewiseLinearCostCurve:
    axis_unit = (
        "input-tokens"
        if kind is ReferenceKind.COLD_PREFILL
        else "bytes"
    )
    return PiecewiseLinearCostCurve(
        curve_id=curve_id,
        hardware_id=hardware,
        reference_kind=kind,
        axis_unit=axis_unit,
        knots=tuple(
            _knot(
                axis,
                seconds,
                f"fit:{curve_id}:{axis}",
                kind=kind,
                hardware=hardware,
            )
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
            ReferenceKind.COLD_PREFILL,
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
            ReferenceKind.POINT_TO_POINT_TRANSFER,
            [(10, 0.1), (100, 1.0), (1000, 10.0)],
        ),
    )


def test_piecewise_curve_interpolates_and_forbids_extrapolation() -> None:
    curve = _curve(
        "curve",
        ReferenceKind.COLD_PREFILL,
        [(10, 1.0), (20, 3.0), (40, 7.0)],
    )
    assert curve.evaluate(10) == pytest.approx(1.0)
    assert curve.evaluate(15) == pytest.approx(2.0)
    assert curve.evaluate(30) == pytest.approx(5.0)
    assert curve.evaluate(40) == pytest.approx(7.0)
    with pytest.raises(ValueError, match="extrapolate"):
        curve.evaluate(9)
    with pytest.raises(ValueError, match="extrapolate"):
        curve.evaluate(41)


def test_curve_knots_are_fit_only_psrc2_and_hardware_bound() -> None:
    knot = _knot(
        64, 0.1, "fit-point", kind=ReferenceKind.COLD_PREFILL
    )
    assert knot.source_partition == C64A_KNOT_PARTITION
    with pytest.raises(ValueError, match="only from C6.3 FIT"):
        replace(knot, source_partition="VALIDATION")

    synthetic = SourcedScalar(
        value=0.1,
        unit="seconds",
        provenance=ParameterProvenance(
            source_class=ParameterSourceClass.SYNTHETIC_SENSITIVITY,
            reference="test synthetic",
        ),
        sensitivity=SensitivityRange(low=0.05, high=0.2),
    )
    with pytest.raises(ValueError, match="P-SRC2"):
        replace(knot, seconds=synthetic)

    mixed = replace(knot, hardware_id="h100-80gb", source_point_id="h100")
    with pytest.raises(ValueError, match="match curve hardware and family"):
        PiecewiseLinearCostCurve(
            curve_id="mixed",
            hardware_id="a100-80gb",
            reference_kind=ReferenceKind.COLD_PREFILL,
            axis_unit="input-tokens",
            knots=(knot, replace(mixed, axis_value=128)),
        )


def test_revised_decode_composes_fixed_term_per_output_token() -> None:
    estimate = estimate_revised_inference_cost(
        _profile(),
        InferenceCostWorkload(
            input_tokens=10,
            output_tokens=4,
            reusable_prefix_tokens=5,
            state_tokens=10,
        ),
    )
    steps = 4 * 10 + 4 * 3 // 2
    assert estimate.decode_context_token_steps == 46 == steps
    assert estimate.decode_seconds == pytest.approx(4 * 0.01 + 0.001 * 46)
    assert estimate.decode_seconds != pytest.approx(0.01 + 0.001 * 46)


def test_recompute_uses_same_prefill_curve_and_zero_work_is_zero() -> None:
    profile = _profile()
    estimate = estimate_revised_inference_cost(
        profile,
        InferenceCostWorkload(
            input_tokens=10,
            output_tokens=0,
            reusable_prefix_tokens=5,
            state_tokens=10,
        ),
    )
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


def test_profile_keeps_memory_mechanics_and_binds_curve_hardware() -> None:
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

    with pytest.raises(ValueError, match="transfer_curve hardware"):
        replace(
            profile,
            transfer_curve=_curve(
                "h100-transfer",
                ReferenceKind.POINT_TO_POINT_TRANSFER,
                [(10, 0.1), (100, 1.0)],
                hardware="h100-80gb",
            ),
        )


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
    axes = [
        64, 80, 128, 160, 320, 352, 512, 544,
        1024, 1088, 2048, 2112, 3200, 3264, 4096, 4160,
    ]
    verify_fresh_boundary_against_c63_partition(
        C64A_FRESH_PREFILL_BOUNDARY,
        _partition(ReferenceKind.COLD_PREFILL, axes),
    )


def test_fresh_boundary_collision_fails_closed() -> None:
    partition = _partition(
        ReferenceKind.COLD_PREFILL,
        [64, 80, 127, 160, 320, 352, 4096, 4160],
    )
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
    verify_fresh_boundary_against_c63_partition(
        C64A_FRESH_TRANSFER_BOUNDARY,
        _partition(ReferenceKind.POINT_TO_POINT_TRANSFER, axes),
    )


def test_validation_point_ids_cannot_enter_curve_knots() -> None:
    partition = _partition(ReferenceKind.COLD_PREFILL, [64, 80, 128, 160])
    fit_knots = tuple(
        CurveKnot(
            hardware_id=point.hardware_id,
            reference_kind=point.kind,
            axis_value=point.axis_value,
            seconds=_scalar(point.observed_seconds, "seconds"),
            source_point_id=point.point_id,
        )
        for point in partition.fit
    )
    curve = PiecewiseLinearCostCurve(
        curve_id="fit-only",
        hardware_id="a100-80gb",
        reference_kind=ReferenceKind.COLD_PREFILL,
        axis_unit="input-tokens",
        knots=fit_knots,
    )
    assert_knots_match_c63_fit(curve, partition)

    leaked = PiecewiseLinearCostCurve(
        curve_id="leaked",
        hardware_id="a100-80gb",
        reference_kind=ReferenceKind.COLD_PREFILL,
        axis_unit="input-tokens",
        knots=(
            fit_knots[0],
            CurveKnot(
                hardware_id="a100-80gb",
                reference_kind=ReferenceKind.COLD_PREFILL,
                axis_value=partition.validation[0].axis_value,
                seconds=_scalar(partition.validation[0].observed_seconds, "seconds"),
                source_point_id=partition.validation[0].point_id,
            ),
        ),
    )
    with pytest.raises(ValueError, match="VALIDATION points"):
        assert_knots_match_c63_fit(leaked, partition)
