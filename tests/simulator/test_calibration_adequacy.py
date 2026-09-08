from dataclasses import replace

import pytest

from simulator.calibration_adequacy import (
    C63C_COMPOSITION_INPUT_CONTEXTS,
    C63C_COMPOSITION_OUTPUT_TOKENS,
    C63C_CORPUS_FINGERPRINTS,
    AffineFitDiagnostic,
    FamilyAdequacyRecord,
    FamilyValidationStatus,
    HardwareAdequacyRecord,
    evaluate_decode_composition,
    evaluate_family_adequacy,
    fit_affine_reference_family_diagnostic,
)
from simulator.calibration_projection import VIDUR_SOURCE_SHA256
from simulator.calibration_validation import (
    VIDUR_PINNED_COMMIT,
    AdequacyDecision,
    CalibrationReferencePoint,
    ReferenceKind,
    SourceArtifactRef,
    split_reference_family,
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


def _artifacts(kind: ReferenceKind) -> tuple[SourceArtifactRef, ...]:
    if kind is ReferenceKind.POINT_TO_POINT_TRANSFER:
        return (A100_SEND_RECV,)
    return (MODEL_CONFIG, A100_ATTENTION, A100_MLP)


def _point(
    point_id: str,
    axis: int,
    observed: float,
    *,
    kind: ReferenceKind = ReferenceKind.COLD_PREFILL,
) -> CalibrationReferencePoint:
    return CalibrationReferencePoint(
        point_id=point_id,
        hardware_id="a100-80gb",
        kind=kind,
        axis_value=axis,
        observed_seconds=observed,
        derivation_id="test-c6.3c",
        source_commit=VIDUR_PINNED_COMMIT,
        source_artifacts=_artifacts(kind),
    )


def _linear_partition(
    *,
    intercept: float,
    slope: float,
    kind: ReferenceKind = ReferenceKind.COLD_PREFILL,
):
    points = [
        _point(
            f"p{axis}",
            axis,
            intercept + slope * axis,
            kind=kind,
        )
        for axis in range(1, 5)
    ]
    return split_reference_family(points)


def test_diagnostic_preserves_negative_intercept_without_clipping() -> None:
    partition = _linear_partition(intercept=-1.0, slope=2.0)
    diagnostic = fit_affine_reference_family_diagnostic(partition)

    assert diagnostic.intercept_seconds == pytest.approx(-1.0)
    assert diagnostic.slope_seconds_per_axis_unit == pytest.approx(2.0)
    assert diagnostic.violations == ("NEGATIVE_INTERCEPT",)
    assert not diagnostic.admissible


def test_inadmissible_fit_is_structural_failure_without_heldout_metrics() -> None:
    partition = _linear_partition(intercept=-1.0, slope=2.0)
    record = evaluate_family_adequacy(partition)

    assert record.validation_status is FamilyValidationStatus.NOT_EVALUABLE_INVALID_FIT
    assert record.validation_report is None
    assert record.decision is AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION
    payload = record.to_dict()
    assert payload["validation_report"] is None
    assert payload["fit"]["intercept_seconds"] == pytest.approx(-1.0)


def test_admissible_fit_uses_exact_heldout_oracle() -> None:
    partition = _linear_partition(intercept=1.0, slope=2.0)
    record = evaluate_family_adequacy(partition)

    assert record.validation_status is FamilyValidationStatus.EVALUATED
    assert record.validation_report is not None
    assert record.validation_report.mae_seconds == pytest.approx(0.0)
    assert record.validation_report.mape == pytest.approx(0.0)
    assert record.validation_report.max_ape == pytest.approx(0.0)
    assert record.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN


def test_family_record_cannot_launder_invalid_fit_into_evaluated_status() -> None:
    diagnostic = AffineFitDiagnostic(
        hardware_id="a100-80gb",
        kind=ReferenceKind.COLD_PREFILL,
        intercept_seconds=-1.0,
        slope_seconds_per_axis_unit=2.0,
        fit_point_ids=("p1", "p3"),
        validation_point_ids=("p2", "p4"),
        violations=("NEGATIVE_INTERCEPT",),
    )

    with pytest.raises(ValueError, match="explicitly not evaluable"):
        FamilyAdequacyRecord(
            fit=diagnostic,
            validation_status=FamilyValidationStatus.EVALUATED,
            validation_report=None,
            decision=AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION,
        )


def test_hardware_record_rejects_cross_hardware_family_evidence() -> None:
    families = tuple(
        evaluate_family_adequacy(
            _linear_partition(intercept=-1.0, slope=2.0, kind=kind)
        )
        for kind in ReferenceKind
    )
    mixed_first = replace(
        families[0],
        fit=replace(families[0].fit, hardware_id="h100-80gb"),
    )
    mixed_families = (mixed_first,) + families[1:]
    composition = evaluate_decode_composition(
        "a100-80gb",
        fixed_seconds=0.0,
        slope_seconds_per_context_step=1e-6,
    )

    with pytest.raises(ValueError, match="family record hardware"):
        HardwareAdequacyRecord(
            hardware_id="a100-80gb",
            source_sha256=VIDUR_SOURCE_SHA256["a100-80gb"],
            corpus_fingerprint=C63C_CORPUS_FINGERPRINTS["a100-80gb"],
            families=mixed_families,
            decode_composition=composition,
            decision=AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION,
        )


def test_decode_composition_grid_is_frozen_and_complete() -> None:
    assert C63C_COMPOSITION_INPUT_CONTEXTS == (32, 256, 1024, 2048, 4032)
    assert C63C_COMPOSITION_OUTPUT_TOKENS == (2, 4, 8, 16, 32)

    report = evaluate_decode_composition(
        "a100-80gb",
        fixed_seconds=0.0,
        slope_seconds_per_context_step=1e-6,
    )
    assert len(report.points) == 25
    assert report.mae_seconds == pytest.approx(0.0)
    assert report.mape == pytest.approx(0.0)
    assert report.max_ape == pytest.approx(0.0)
    assert report.decision is AdequacyDecision.ADEQUATE_WITHIN_DECLARED_DOMAIN


def test_decode_composition_exposes_once_vs_per_step_fixed_term() -> None:
    fixed = 0.01
    report = evaluate_decode_composition(
        "a100-80gb",
        fixed_seconds=fixed,
        slope_seconds_per_context_step=1e-7,
    )

    assert report.decision is AdequacyDecision.INADEQUATE_REVISE_REPRESENTATION
    for point in report.points:
        expected_gap = (point.output_tokens - 1) * fixed
        assert point.expected_source_minus_c6_gap_seconds == pytest.approx(expected_gap)
        assert (
            point.source_primitive_sequence_seconds - point.c6_1_sequence_seconds
        ) == pytest.approx(expected_gap)
        assert point.signed_error_seconds <= 0
        assert point.absolute_percentage_error >= 0


def test_decode_composition_rejects_invalid_coefficients() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        evaluate_decode_composition(
            "a100-80gb",
            fixed_seconds=-1.0,
            slope_seconds_per_context_step=1e-6,
        )
    with pytest.raises(ValueError, match="strictly positive"):
        evaluate_decode_composition(
            "a100-80gb",
            fixed_seconds=0.0,
            slope_seconds_per_context_step=0.0,
        )


def test_diagnostic_point_identity_is_drift_sensitive() -> None:
    diagnostic = fit_affine_reference_family_diagnostic(
        _linear_partition(intercept=1.0, slope=2.0)
    )
    changed = replace(diagnostic, validation_point_ids=("other", "p4"))
    assert changed.to_dict() != diagnostic.to_dict()