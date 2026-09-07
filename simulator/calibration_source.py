from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Mapping

from .inference_cost import ParameterSourceClass


C6_CALIBRATION_SOURCE_SCHEMA = "cadi.c6.calibration-source.v1"
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


class CalibrationArtifactRole(str, Enum):
    MODEL_CONFIG = "MODEL_CONFIG"
    COMPUTE_ATTENTION = "COMPUTE_ATTENTION"
    COMPUTE_MLP = "COMPUTE_MLP"
    NETWORK_ALL_REDUCE = "NETWORK_ALL_REDUCE"
    NETWORK_SEND_RECV = "NETWORK_SEND_RECV"


_REQUIRED_ROLES = frozenset(CalibrationArtifactRole)


@dataclass(frozen=True, slots=True)
class CalibrationArtifact:
    role: CalibrationArtifactRole
    path: str
    git_blob_sha1: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.role, CalibrationArtifactRole):
            raise TypeError("role must be CalibrationArtifactRole")
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("path must be a non-empty string")
        if not _SHA1_RE.fullmatch(self.git_blob_sha1):
            raise ValueError("git_blob_sha1 must be 40 lowercase hexadecimal characters")
        if not isinstance(self.size_bytes, int) or isinstance(self.size_bytes, bool) or self.size_bytes <= 0:
            raise ValueError("size_bytes must be a positive integer")

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "path": self.path,
            "git_blob_sha1": self.git_blob_sha1,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class CalibrationSourceManifest:
    source_id: str
    source_class: ParameterSourceClass
    upstream_repository: str
    upstream_commit: str
    license_id: str
    publication_reference: str
    methods_reference: str
    model_id: str
    hardware_id: str
    network_id: str
    artifacts: tuple[CalibrationArtifact, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "source_id",
            "upstream_repository",
            "license_id",
            "publication_reference",
            "methods_reference",
            "model_id",
            "hardware_id",
            "network_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.source_class is not ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE:
            raise ValueError("C6.2 calibration source manifests must be P-SRC2")
        if not _SHA1_RE.fullmatch(self.upstream_commit):
            raise ValueError("upstream_commit must be a 40-character lowercase git SHA")
        if not isinstance(self.artifacts, tuple) or not self.artifacts:
            raise ValueError("artifacts must be a non-empty tuple")
        if not all(isinstance(item, CalibrationArtifact) for item in self.artifacts):
            raise TypeError("artifacts must contain CalibrationArtifact values")
        paths = [item.path for item in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact paths must be unique")
        roles = [item.role for item in self.artifacts]
        if len(roles) != len(set(roles)):
            raise ValueError("artifact roles must be unique")
        if frozenset(roles) != _REQUIRED_ROLES:
            raise ValueError("manifest must contain exactly one artifact for every required role")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": C6_CALIBRATION_SOURCE_SCHEMA,
            "source_id": self.source_id,
            "source_class": self.source_class.value,
            "upstream_repository": self.upstream_repository,
            "upstream_commit": self.upstream_commit,
            "license_id": self.license_id,
            "publication_reference": self.publication_reference,
            "methods_reference": self.methods_reference,
            "model_id": self.model_id,
            "hardware_id": self.hardware_id,
            "network_id": self.network_id,
            "artifacts": [item.to_dict() for item in self.artifacts],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


def assert_source_snapshot(
    manifest: CalibrationSourceManifest,
    observed_git_blobs: Mapping[str, str],
) -> None:
    """Fail closed unless an observed upstream snapshot matches the manifest exactly."""

    if not isinstance(manifest, CalibrationSourceManifest):
        raise TypeError("manifest must be CalibrationSourceManifest")
    expected = {artifact.path: artifact.git_blob_sha1 for artifact in manifest.artifacts}
    observed = dict(observed_git_blobs)
    if set(observed) != set(expected):
        missing = sorted(set(expected) - set(observed))
        extra = sorted(set(observed) - set(expected))
        raise ValueError(f"calibration source path mismatch: missing={missing}, extra={extra}")
    mismatches = {
        path: (expected[path], observed[path])
        for path in sorted(expected)
        if observed[path] != expected[path]
    }
    if mismatches:
        raise ValueError(f"calibration source blob mismatch: {mismatches}")


def _artifact(role: CalibrationArtifactRole, path: str, sha1: str, size: int) -> CalibrationArtifact:
    return CalibrationArtifact(role=role, path=path, git_blob_sha1=sha1, size_bytes=size)


_VIDUR_COMMON = dict(
    source_class=ParameterSourceClass.PUBLISHED_OR_VALIDATED_PROFILE,
    upstream_repository="microsoft/vidur",
    upstream_commit="abae7f63aa857300f5cdc6f5e0d27860cd24721b",
    license_id="MIT",
    publication_reference="Vidur: A Large-Scale Simulation Framework For LLM Inference, MLSys 2024, arXiv:2405.05465",
    methods_reference="microsoft/vidur docs/profiling.md at abae7f63aa857300f5cdc6f5e0d27860cd24721b",
    model_id="meta-llama/Llama-2-7b-hf",
)

VIDUR_LLAMA2_7B_A100_DGX = CalibrationSourceManifest(
    source_id="vidur-llama2-7b-a100-dgx-v1",
    hardware_id="a100-80gb",
    network_id="a100_dgx",
    artifacts=(
        _artifact(CalibrationArtifactRole.MODEL_CONFIG, "vidur/config/model_config.py", "722299bbb556ccbab2b82609598be6b8c2963c29", 12238),
        _artifact(CalibrationArtifactRole.COMPUTE_ATTENTION, "data/profiling/compute/a100/meta-llama/Llama-2-7b-hf/attention.csv", "6ce0a3beab1618969d429b4313666b5dff6850dd", 10680357),
        _artifact(CalibrationArtifactRole.COMPUTE_MLP, "data/profiling/compute/a100/meta-llama/Llama-2-7b-hf/mlp.csv", "479ed2f6ed22049ac444bca9fa44578532cbf28a", 706614),
        _artifact(CalibrationArtifactRole.NETWORK_ALL_REDUCE, "data/profiling/network/a100_dgx/all_reduce.csv", "730776436cbd9e3e40aae50768e2b64921dd0379", 645190),
        _artifact(CalibrationArtifactRole.NETWORK_SEND_RECV, "data/profiling/network/a100_dgx/send_recv.csv", "418cd50858fdd604c3da21eb3aaa06e583059865", 176791),
    ),
    **_VIDUR_COMMON,
)

VIDUR_LLAMA2_7B_H100_DGX = CalibrationSourceManifest(
    source_id="vidur-llama2-7b-h100-dgx-v1",
    hardware_id="h100-80gb",
    network_id="h100_dgx",
    artifacts=(
        _artifact(CalibrationArtifactRole.MODEL_CONFIG, "vidur/config/model_config.py", "722299bbb556ccbab2b82609598be6b8c2963c29", 12238),
        _artifact(CalibrationArtifactRole.COMPUTE_ATTENTION, "data/profiling/compute/h100/meta-llama/Llama-2-7b-hf/attention.csv", "dfc6b05e232e3d243bf3c89feb3f380a77f5ad0d", 10792177),
        _artifact(CalibrationArtifactRole.COMPUTE_MLP, "data/profiling/compute/h100/meta-llama/Llama-2-7b-hf/mlp.csv", "661ea954e9508d67f37d3b24f0e07eb96f83ca46", 715469),
        _artifact(CalibrationArtifactRole.NETWORK_ALL_REDUCE, "data/profiling/network/h100_dgx/all_reduce.csv", "a7a2470393d9ad8786e2423f081d01f8997b4bdb", 274142),
        _artifact(CalibrationArtifactRole.NETWORK_SEND_RECV, "data/profiling/network/h100_dgx/send_recv.csv", "fed60792e5d5900ea008e4d26427f39b7285434f", 177070),
    ),
    **_VIDUR_COMMON,
)

VIDUR_CALIBRATION_SOURCES = (
    VIDUR_LLAMA2_7B_A100_DGX,
    VIDUR_LLAMA2_7B_H100_DGX,
)
