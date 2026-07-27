"""CLI-facing model inspection, verification, and installation helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
import shutil
from typing import Any

from ..config import EmbeddingSettings, MemoryStoreSettings, RerankerSettings
from ..errors import ProviderError
from .fastembed_provider import FastEmbedProvider, FastEmbedRerankerProvider
from .framework import ProviderRegistry
from .llamacpp_provider import LlamaCppEmbeddingProvider, LlamaCppRerankerProvider
from .manifests import (
    ModelManifest,
    build_model_manifest_from_directory,
    read_model_manifest,
    resolve_manifest_path,
    verify_model_manifest,
    write_model_manifest,
)
from .ollama_provider import OllamaEmbeddingProvider, OllamaLLMRerankerProvider


def list_models(settings: MemoryStoreSettings) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    registry = _build_registry()
    for capability, section in _configured_sections(settings):
        provider = _create_provider(registry, capability, section)
        identity = provider.identity()
        manifest_path = resolve_manifest_path(section.model_path, section.manifest_path)
        entries.append(
            {
                "capability": capability,
                "provider": section.provider,
                "model_name": identity.model_name,
                "model_revision": identity.revision,
                "model_digest": identity.digest,
                "model_path": _path_or_none(section.model_path),
                "manifest_path": _path_or_none(manifest_path),
                "strict_offline": bool(section.local_files_only or section.hf_hub_offline),
                "runtime_available": provider.available(),
                "require_manifest": section.require_manifest,
                "require_checksums": section.require_checksums,
            }
        )
    return entries


def inspect_model(
    settings: MemoryStoreSettings,
    *,
    capability: str,
) -> dict[str, Any]:
    registry = _build_registry()
    section = _section_for_capability(settings, capability)
    provider = _create_provider(registry, capability, section)
    try:
        identity = asdict(provider.identity())
        identity["manifest_path"] = _path_or_none(provider.identity().manifest_path)
        health = asdict(provider.health())
        manifest = _read_manifest_if_present(section)
        verification = verify_models(settings, capability=capability)
        return {
            "capability": capability,
            "provider": section.provider,
            "identity": identity,
            "health": health,
            "manifest": manifest.model_dump(mode="python", exclude_none=True) if manifest else None,
            "verification": verification,
        }
    finally:
        _close_provider(provider)


def verify_models(
    settings: MemoryStoreSettings,
    *,
    capability: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    results = [
        _verify_section(capability_name, section)
        for capability_name, section in _configured_sections(settings, capability=capability)
    ]
    if capability is not None:
        return results[0]
    return results


def install_model(
    settings: MemoryStoreSettings,
    *,
    capability: str,
    source: Path,
    destination: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    section = _section_for_capability(settings, capability)
    source_path = Path(source)
    if not source_path.exists():
        raise ProviderError(
            "Model installation source does not exist.",
            code="provider_install_source_invalid",
        )

    target_path = destination or section.model_path
    if target_path is None:
        raise ProviderError(
            "Configure model_path or pass --destination before installing local models.",
            code="provider_model_missing",
        )

    target = Path(target_path)
    if target.exists():
        if not force:
            raise ProviderError(
                "Model installation destination already exists.",
                code="provider_install_destination_exists",
            )
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    if source_path.is_dir():
        shutil.copytree(source_path, target)
    else:
        target.mkdir(parents=True, exist_ok=True)
        try:
            shutil.unpack_archive(str(source_path), str(target))
        except (shutil.ReadError, ValueError) as exc:
            raise ProviderError(
                "Model installation source must be a directory or a supported archive.",
                code="provider_install_source_invalid",
            ) from exc

    manifest_path = resolve_manifest_path(target, section.manifest_path)
    if manifest_path is None:
        raise ProviderError(
            "Unable to determine the manifest location for the installed model.",
            code="provider_manifest_invalid",
        )

    if not manifest_path.exists():
        manifest = build_model_manifest_from_directory(
            target,
            provider=section.provider,
            capability=capability,
            model_name=section.model,
            revision=section.model_revision or section.model_version,
            digest=section.model_digest,
            vector_dimension=getattr(section, "dim", None),
            normalization=getattr(section, "normalize", None),
        )
        write_model_manifest(manifest_path, manifest)
    else:
        manifest = read_model_manifest(manifest_path)

    verification = verify_model_manifest(
        target,
        manifest,
        manifest_path=manifest_path,
        require_checksums=section.require_checksums,
    )
    return {
        "capability": capability,
        "provider": section.provider,
        "source": str(source_path),
        "model_path": str(target),
        "manifest_path": str(manifest_path),
        "installed_files": sum(1 for item in target.rglob("*") if item.is_file()),
        "verification": _verification_to_dict(verification),
    }


def export_model_manifest(
    settings: MemoryStoreSettings,
    *,
    capability: str,
    out: Path | None = None,
) -> dict[str, Any]:
    section = _section_for_capability(settings, capability)
    if section.model_path is None:
        raise ProviderError(
            "Configured model_path is required to export a manifest.",
            code="provider_model_missing",
        )

    manifest = _read_manifest_if_present(section)
    if manifest is None:
        manifest = build_model_manifest_from_directory(
            section.model_path,
            provider=section.provider,
            capability=capability,
            model_name=section.model,
            revision=section.model_revision or section.model_version,
            digest=section.model_digest,
            vector_dimension=getattr(section, "dim", None),
            normalization=getattr(section, "normalize", None),
        )

    destination = out or resolve_manifest_path(section.model_path, section.manifest_path)
    if destination is None:
        raise ProviderError(
            "Unable to determine the manifest destination for this model.",
            code="provider_manifest_invalid",
        )
    write_model_manifest(destination, manifest)
    return {
        "capability": capability,
        "manifest_path": str(destination),
        "manifest": manifest.model_dump(mode="python", exclude_none=True),
    }


def doctor_models(
    settings: MemoryStoreSettings,
    *,
    capability: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    registry = _build_registry()
    reports: list[dict[str, Any]] = []
    for capability_name, section in _configured_sections(settings, capability=capability):
        provider = _create_provider(registry, capability_name, section)
        try:
            health = asdict(provider.health())
            verification = _verify_section(capability_name, section)
            reports.append(
                {
                    "capability": capability_name,
                    "provider": section.provider,
                    "strict_offline": bool(section.local_files_only or section.hf_hub_offline),
                    "runtime_available": provider.available(),
                    "health": health,
                    "verification": verification,
                }
            )
        finally:
            _close_provider(provider)
    if capability is not None:
        return reports[0]
    return reports


def _build_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register_embedding("fastembed", FastEmbedProvider.from_settings)
    registry.register_embedding("ollama", OllamaEmbeddingProvider.from_settings)
    registry.register_embedding("llamacpp", LlamaCppEmbeddingProvider.from_settings)
    registry.register_reranker("fastembed", FastEmbedRerankerProvider.from_settings)
    registry.register_reranker("llamacpp", LlamaCppRerankerProvider.from_settings)
    registry.register_reranker("ollama_llm", OllamaLLMRerankerProvider.from_settings)
    return registry


def _close_provider(provider: Any) -> None:
    closer = getattr(provider, "close", None)
    if callable(closer):
        closer()


def _configured_sections(
    settings: MemoryStoreSettings,
    *,
    capability: str | None = None,
) -> Iterator[tuple[str, EmbeddingSettings | RerankerSettings]]:
    available: list[tuple[str, EmbeddingSettings | RerankerSettings]] = [
        ("embedding", settings.embeddings)
    ]
    if settings.reranker.enabled:
        available.append(("reranker", settings.reranker))

    for capability_name, section in available:
        if capability is None or capability == capability_name:
            yield capability_name, section


def _section_for_capability(
    settings: MemoryStoreSettings,
    capability: str,
) -> EmbeddingSettings | RerankerSettings:
    if capability == "embedding":
        return settings.embeddings
    if capability == "reranker":
        return settings.reranker
    raise ProviderError(
        f"Unsupported model capability: {capability}",
        code="provider_manifest_invalid",
    )


def _create_provider(
    registry: ProviderRegistry,
    capability: str,
    section: EmbeddingSettings | RerankerSettings,
) -> Any:
    if capability == "embedding":
        return registry.create_embedding(section.provider, section)
    return registry.create_reranker(section.provider, section)


def _read_manifest_if_present(
    section: EmbeddingSettings | RerankerSettings,
) -> ModelManifest | None:
    manifest_path = resolve_manifest_path(section.model_path, section.manifest_path)
    if manifest_path is None or not manifest_path.exists():
        return None
    return read_model_manifest(manifest_path)


def _verify_section(
    capability: str,
    section: EmbeddingSettings | RerankerSettings,
) -> dict[str, Any]:
    manifest_path = resolve_manifest_path(section.model_path, section.manifest_path)
    if section.model_path is None:
        return {
            "capability": capability,
            "ok": not (section.require_manifest or section.require_checksums),
            "model_path": None,
            "manifest_path": _path_or_none(manifest_path),
            "verified_files": [],
            "missing_files": [],
            "checksum_mismatches": [],
        }

    manifest = _read_manifest_if_present(section)
    if manifest is None:
        if section.require_manifest or section.require_checksums:
            return {
                "capability": capability,
                "ok": False,
                "model_path": _path_or_none(section.model_path),
                "manifest_path": _path_or_none(manifest_path),
                "verified_files": [],
                "missing_files": ["manifest"],
                "checksum_mismatches": [],
            }
        return {
            "capability": capability,
            "ok": True,
            "model_path": _path_or_none(section.model_path),
            "manifest_path": _path_or_none(manifest_path),
            "verified_files": [],
            "missing_files": [],
            "checksum_mismatches": [],
        }

    verification = verify_model_manifest(
        section.model_path,
        manifest,
        manifest_path=manifest_path or Path(section.model_path),
        require_checksums=section.require_checksums,
    )
    payload = _verification_to_dict(verification)
    payload["capability"] = capability
    return payload


def _verification_to_dict(result: Any) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "model_path": str(result.model_root),
        "manifest_path": str(result.manifest_path),
        "verified_files": list(result.verified_files),
        "missing_files": list(result.missing_files),
        "checksum_mismatches": list(result.checksum_mismatches),
    }


def _path_or_none(value: Path | None) -> str | None:
    return str(value) if value is not None else None