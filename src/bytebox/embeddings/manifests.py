"""Model manifest and checksum helpers for offline FastEmbed workflows."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from ..errors import ProviderError

DEFAULT_MODEL_MANIFEST_NAME = "bytebox-model.yaml"


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ModelManifestFile(_ManifestModel):
    path: str
    sha256: str | None = None


class ModelManifest(_ManifestModel):
    schema_version: int = Field(default=1, ge=1)
    provider: str
    capability: Literal["embedding", "reranker"]
    model_name: str
    revision: str | None = None
    artifact_format: str | None = None
    vector_dimension: int | None = Field(default=None, ge=1)
    normalization: bool | None = None
    files: list[ModelManifestFile] = Field(default_factory=list)
    license: str | None = None
    digest: str | None = None


@dataclass(frozen=True, slots=True)
class ModelManifestVerification:
    ok: bool
    model_root: Path
    manifest_path: Path
    verified_files: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()
    checksum_mismatches: tuple[str, ...] = ()


def resolve_manifest_path(
    model_path: Path | None,
    manifest_path: Path | None = None,
) -> Path | None:
    if manifest_path is not None:
        return Path(manifest_path)
    if model_path is None:
        return None
    candidate = Path(model_path)
    if candidate.is_dir() or candidate.suffix == "":
        return candidate / DEFAULT_MODEL_MANIFEST_NAME
    return candidate.parent / DEFAULT_MODEL_MANIFEST_NAME


def read_model_manifest(manifest_path: Path) -> ModelManifest:
    manifest_file = Path(manifest_path)
    if not manifest_file.exists():
        raise ProviderError(
            "Configured model manifest was not found.",
            code="provider_manifest_missing",
        )

    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ProviderError(
            "PyYAML is required to load model manifests.",
            code="provider_manifest_invalid",
        ) from exc

    try:
        raw = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
        return ModelManifest.model_validate(raw)
    except (OSError, PydanticValidationError, yaml.YAMLError) as exc:
        raise ProviderError(
            "Configured model manifest is invalid.",
            code="provider_manifest_invalid",
        ) from exc


def write_model_manifest(manifest_path: Path, manifest: ModelManifest) -> Path:
    destination = Path(manifest_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ProviderError(
            "PyYAML is required to write model manifests.",
            code="provider_manifest_invalid",
        ) from exc

    payload = manifest.model_dump(mode="python", exclude_none=True)
    destination.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    return destination


def build_model_manifest_from_directory(
    model_path: Path,
    *,
    provider: str,
    capability: Literal["embedding", "reranker"],
    model_name: str,
    revision: str | None = None,
    digest: str | None = None,
    vector_dimension: int | None = None,
    normalization: bool | None = None,
    license_value: str | None = None,
) -> ModelManifest:
    root = _resolve_model_root(model_path)
    files: list[ModelManifestFile] = []

    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        if candidate.name == DEFAULT_MODEL_MANIFEST_NAME:
            continue
        relative_path = candidate.relative_to(root).as_posix()
        files.append(ModelManifestFile(path=relative_path, sha256=compute_sha256(candidate)))

    artifact_format = _infer_artifact_format(files)
    return ModelManifest(
        provider=provider,
        capability=capability,
        model_name=model_name,
        revision=revision,
        artifact_format=artifact_format,
        vector_dimension=vector_dimension,
        normalization=normalization,
        files=files,
        license=license_value,
        digest=digest,
    )


def verify_model_manifest(
    model_path: Path,
    manifest: ModelManifest,
    *,
    manifest_path: Path,
    require_checksums: bool = False,
) -> ModelManifestVerification:
    root = _resolve_model_root(model_path)
    if not root.exists():
        raise ProviderError(
            "Configured model files are unavailable.",
            code="provider_model_missing",
        )

    verified: list[str] = []
    missing: list[str] = []
    mismatches: list[str] = []

    for entry in manifest.files:
        candidate = (root / entry.path).resolve()
        if not candidate.is_relative_to(root.resolve()):
            mismatches.append(f"{entry.path} (invalid path)")
            continue
        if not candidate.exists() or not candidate.is_file():
            missing.append(entry.path)
            continue
        verified.append(entry.path)
        if entry.sha256 is None:
            if require_checksums:
                mismatches.append(f"{entry.path} (missing sha256)")
            continue
        if compute_sha256(candidate) != entry.sha256:
            mismatches.append(entry.path)

    return ModelManifestVerification(
        ok=not missing and not mismatches,
        model_root=root,
        manifest_path=Path(manifest_path),
        verified_files=tuple(verified),
        missing_files=tuple(missing),
        checksum_mismatches=tuple(mismatches),
    )


def compute_sha256(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_model_root(path: Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_dir() or candidate.suffix == "" else candidate.parent


def _infer_artifact_format(files: list[ModelManifestFile]) -> str | None:
    suffixes = [Path(item.path).suffix.lower().lstrip(".") for item in files]
    for preferred in ("onnx", "gguf", "bin"):
        if preferred in suffixes:
            return preferred
    for suffix in suffixes:
        if suffix:
            return suffix
    return None