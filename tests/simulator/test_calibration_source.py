from dataclasses import replace

import pytest

from simulator.calibration_source import (
    C6_CALIBRATION_SOURCE_SCHEMA,
    CalibrationArtifactRole,
    VIDUR_CALIBRATION_SOURCES,
    VIDUR_LLAMA2_7B_A100_DGX,
    VIDUR_LLAMA2_7B_H100_DGX,
    assert_source_snapshot,
)
from simulator.inference_cost import ParameterSourceClass


EXPECTED_FINGERPRINTS = {
    "vidur-llama2-7b-a100-dgx-v1": "2b43edc3d5e5b62337cb87da4431ccbf24a0eb36a1d3914e49d2ba126766a758",
    "vidur-llama2-7b-h100-dgx-v1": "631438ac078a01db212a005557f7fe74e4da8a03d87738afb83c9985a276b6d7",
}


def _snapshot(manifest):
    return {artifact.path: artifact.git_blob_sha1 for artifact in manifest.artifacts}


def test_vidur_source_family_is_pinned_and_cross_device() -> None:
    assert len(VIDUR_CALIBRATION_SOURCES) == 2
    assert {source.hardware_id for source in VIDUR_CALIBRATION_SOURCES} == {
        "a100-80gb",
        "h100-80gb",
    }
    assert {source.network_id for source in VIDUR_CALIBRATION_SOURCES} == {
        "a100_dgx",
        "h100_dgx",
    }
    assert {source.model_id for source in VIDUR_CALIBRATION_SOURCES} == {
        "meta-llama/Llama-2-7b-hf"
    }
    assert {source.upstream_repository for source in VIDUR_CALIBRATION_SOURCES} == {
        "microsoft/vidur"
    }
    assert {source.upstream_commit for source in VIDUR_CALIBRATION_SOURCES} == {
        "abae7f63aa857300f5cdc6f5e0d27860cd24721b"
    }
    assert {source.license_id for source in VIDUR_CALIBRATION_SOURCES} == {"MIT"}
    assert {source.source_class for source in VIDUR_CALIBRATION_SOURCES} == {
        ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE
    }


def test_every_source_has_exactly_one_required_artifact_role() -> None:
    expected_roles = set(CalibrationArtifactRole)
    for source in VIDUR_CALIBRATION_SOURCES:
        assert {artifact.role for artifact in source.artifacts} == expected_roles
        assert len(source.artifacts) == len(expected_roles)
        device = next(
            artifact
            for artifact in source.artifacts
            if artifact.role is CalibrationArtifactRole.DEVICE_CONFIG
        )
        assert device.path == "vidur/config/device_sku_config.py"
        assert device.git_blob_sha1 == "8ac9bf57ac03070cd42cdf48792ccf8ffd73ca04"


def test_manifest_serialization_has_frozen_cross_python_fingerprints() -> None:
    for source in VIDUR_CALIBRATION_SOURCES:
        assert source.to_dict()["schema"] == C6_CALIBRATION_SOURCE_SCHEMA
        assert source.fingerprint == EXPECTED_FINGERPRINTS[source.source_id]
        assert source.to_json() == source.to_json()
    assert VIDUR_LLAMA2_7B_A100_DGX.fingerprint != VIDUR_LLAMA2_7B_H100_DGX.fingerprint


def test_source_snapshot_accepts_exact_blob_map() -> None:
    for source in VIDUR_CALIBRATION_SOURCES:
        assert_source_snapshot(source, _snapshot(source))


def test_source_snapshot_fails_closed_on_blob_or_path_drift() -> None:
    source = VIDUR_LLAMA2_7B_A100_DGX
    observed = _snapshot(source)
    first_path = source.artifacts[0].path
    observed[first_path] = "0" * 40
    with pytest.raises(ValueError, match="blob mismatch"):
        assert_source_snapshot(source, observed)

    observed = _snapshot(source)
    observed.pop(first_path)
    with pytest.raises(ValueError, match="path mismatch"):
        assert_source_snapshot(source, observed)


def test_manifest_rejects_missing_role() -> None:
    with pytest.raises(ValueError, match="every required role"):
        replace(
            VIDUR_LLAMA2_7B_A100_DGX,
            artifacts=VIDUR_LLAMA2_7B_A100_DGX.artifacts[:-1],
        )


def test_c6_2_does_not_create_a_calibrated_cost_profile() -> None:
    # This slice freezes upstream P-SRC2 corpus identity only. C6.3 performs
    # the fit/projection and error accounting into InferenceCostProfile.
    for source in VIDUR_CALIBRATION_SOURCES:
        serialized = source.to_dict()
        assert "prefill_fixed_seconds" not in serialized
        assert "decode_fixed_seconds" not in serialized
        assert "transfer_bandwidth_bytes_per_second" not in serialized
