"""Bounded, read-only source and installed-distribution file inventories."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

TRANSFORMER_DISTRIBUTIONS: tuple[str, ...] = (
    "litellm",
    "openai",
    "pydantic",
    "pydantic-core",
    "typing-extensions",
    "typing-inspection",
    "annotated-types",
)
_TRANSFORMER_PACKAGES: dict[str, str] = {
    "litellm": "litellm",
    "openai": "openai",
    "pydantic": "pydantic",
    "pydantic-core": "pydantic_core",
    "typing-extensions": "typing_extensions",
    "typing-inspection": "typing_inspection",
    "annotated-types": "annotated_types",
}
_CODE_SUFFIXES = frozenset({".py", ".pyi", ".pyd", ".so", ".dll", ".dylib"})
_RESOURCE_SUFFIXES = frozenset({".md", ".yaml", ".yml", ".json"})
_RESOURCE_DIRS = frozenset({"prompts", "defaults", "catalog", "catalogs", "specs"})
_EXCLUDED_DIRS = frozenset(
    {
        ".aws",
        ".git",
        ".pytest_cache",
        ".venv",
        "artifacts",
        "data",
        "history",
        "histories",
        "runs",
        "secrets",
        "__pycache__",
    }
)
_RUNTIME_MEMBERS = frozenset(
    {
        "agents",
        "api",
        "core",
        "events",
        "mira",
        "observability",
        "policies",
        "schemas",
        "state",
        "thy",
        "tools",
    }
)


@dataclass(frozen=True)
class FileInventory:
    """Typed result of hashing a bounded set of installed files."""

    entries: tuple[tuple[str, str], ...]
    reasons: tuple[str, ...]
    status: Literal["verified", "unavailable"]


def installed_source_roots() -> tuple[Path, ...]:
    """Return installed runtime namespace roots, excluding unrelated client packages."""
    namespace = sys.modules.get("thymira")
    locations = getattr(namespace, "__path__", None)
    if locations is None:
        return ()
    roots: list[Path] = []
    for location in locations:
        root = Path(location)
        # Editable installs can expose sibling namespace packages that are intentionally outside
        # the runtime fingerprint allowlist (for example CLI and web clients). Explicit test roots
        # bypass this installed-package filter and still validate their own in-scope files.
        if root.parent.name == "src" and root.parent.parent.name not in _RUNTIME_MEMBERS:
            continue
        roots.append(root)
    return tuple(roots)


def source_file_inventory(roots: Sequence[Path]) -> FileInventory:
    """Hash runtime Python and selected package resources without following symlinks."""
    entries: list[tuple[str, str]] = []
    reasons: list[str] = []
    if not roots:
        return FileInventory(
            (), ("installed thymira namespace source roots were unavailable",), "unavailable"
        )
    for root in roots:
        source_root = Path(root)
        if source_root.is_symlink() or not source_root.is_dir():
            reasons.append("a thymira source root was missing or linked")
            continue
        root_label = _source_root_label(source_root)
        root_items = 0

        def record_walk_error(_error: OSError) -> None:
            reasons.append("a thymira source directory could not be enumerated")

        for current, directories, filenames in os.walk(
            source_root, followlinks=False, onerror=record_walk_error
        ):
            current_path = Path(current)
            linked_relevant = [
                name
                for name in directories
                if (current_path / name).is_symlink()
                and name.lower() not in _EXCLUDED_DIRS
                and _source_file_is_relevant(PurePosixPath(name, "placeholder.py"))
            ]
            if linked_relevant:
                reasons.append("a relevant source directory was a symlink")
            directories[:] = sorted(
                name
                for name in directories
                if name.lower() not in _EXCLUDED_DIRS and not (current_path / name).is_symlink()
            )
            for filename in sorted(filenames):
                path = current_path / filename
                relative = PurePosixPath(*path.relative_to(source_root).parts)
                if not _source_file_is_relevant(relative):
                    continue
                if path.is_symlink():
                    reasons.append("a relevant source snapshot file was a symlink")
                    continue
                try:
                    content = path.read_bytes()
                except OSError:
                    reasons.append("a relevant source snapshot file could not be read")
                    continue
                entries.append(
                    (f"{root_label}/{relative.as_posix()}", hashlib.sha256(content).hexdigest())
                )
                root_items += 1
        if root_items == 0:
            reasons.append("a thymira source root contained no in-scope files")
    if not entries and not reasons:
        reasons.append("no in-scope thymira source files were available")
    status: Literal["verified", "unavailable"] = "unavailable" if reasons else "verified"
    return FileInventory(tuple(sorted(entries)), tuple(dict.fromkeys(reasons)), status)


def transformer_file_inventory(distributions: Sequence[str]) -> FileInventory:
    """Hash allowlisted installed package code and metadata without importing transformers."""
    entries: list[tuple[str, str]] = []
    reasons: list[str] = []
    if not distributions:
        return FileInventory((), (), "verified")
    for distribution_name in sorted(set(distributions)):
        inventory = _one_transformer_distribution(distribution_name)
        entries.extend(inventory.entries)
        reasons.extend(inventory.reasons)
    if not entries and not reasons:
        reasons.append("no installed transformer files were available")
    status = "unavailable" if reasons else "verified"
    return FileInventory(tuple(sorted(entries)), tuple(dict.fromkeys(reasons)), status)


def _one_transformer_distribution(distribution_name: str) -> FileInventory:
    package_name = _TRANSFORMER_PACKAGES.get(distribution_name)
    if package_name is None:
        return FileInventory(
            (),
            (f"transformer distribution {distribution_name} is outside the allowlist",),
            "unavailable",
        )
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return FileInventory(
            (), (f"transformer distribution {distribution_name} is not installed",), "unavailable"
        )
    except (OSError, ValueError):
        return FileInventory(
            (),
            (f"transformer distribution {distribution_name} metadata is unreadable",),
            "unavailable",
        )
    try:
        files = distribution.files
    except (OSError, ValueError):
        return FileInventory(
            (),
            (f"transformer distribution {distribution_name} file inventory is unreadable",),
            "unavailable",
        )
    if files is None:
        return FileInventory(
            (),
            (f"transformer distribution {distribution_name} has no file inventory",),
            "unavailable",
        )

    entries: list[tuple[str, str]] = []
    reasons: list[str] = []
    package_file_count = 0
    for item in files:
        result = _installed_file_entry(distribution, distribution_name, package_name, item)
        if result is None:
            continue
        if isinstance(result, str):
            reasons.append(result)
            continue
        key, content_hash, is_package_code = result
        entries.append((key, content_hash))
        package_file_count += int(is_package_code)
    if package_file_count == 0:
        reasons.append(
            f"transformer distribution {distribution_name} has no readable package code files"
        )
    status = "unavailable" if reasons else "verified"
    return FileInventory(tuple(entries), tuple(dict.fromkeys(reasons)), status)


def _installed_file_entry(
    distribution: importlib.metadata.Distribution,
    distribution_name: str,
    package_name: str,
    item: object,
) -> tuple[str, str, bool] | str | None:
    relative = PurePosixPath(str(item).replace("\\", "/"))
    if not _transformer_file_is_relevant(relative, package_name):
        return None
    try:
        path = Path(str(distribution.locate_file(PurePosixPath(str(item)))))
        base = Path(str(distribution.locate_file(PurePosixPath(".")))).resolve(strict=True)
        if relative.is_absolute() or ".." in relative.parts or path.is_symlink():
            return (
                f"transformer distribution {distribution_name} contains a linked or escaping file"
            )
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(base) or not resolved.is_file():
            return f"transformer distribution {distribution_name} file is outside its install root"
        content_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except (OSError, RuntimeError, ValueError):
        return f"transformer distribution {distribution_name} file could not be read"
    key = f"{distribution_name}/{relative.as_posix()}"
    return key, content_hash, _is_package_code_path(relative, package_name)


def _source_file_is_relevant(relative: PurePosixPath) -> bool:
    parts = tuple(part.lower() for part in relative.parts)
    if not parts or any(part in _EXCLUDED_DIRS for part in parts):
        return False
    if parts[0] not in _RUNTIME_MEMBERS and parts[0] not in _RESOURCE_DIRS:
        return False
    suffix = PurePosixPath(parts[-1]).suffix
    if suffix == ".py":
        return True
    return suffix in _RESOURCE_SUFFIXES and any(part in _RESOURCE_DIRS for part in parts[:-1])


def _source_root_label(root: Path) -> str:
    if root.parent.name == "src":
        member = root.parent.parent.name
        if member:
            return member
    return "namespace"


def _transformer_file_is_relevant(path: PurePosixPath, package_name: str) -> bool:
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    suffix = path.suffix.lower()
    if _is_package_code_path(path, package_name) and suffix in _CODE_SUFFIXES:
        return True
    first = path.parts[0].lower()
    normalized_package = package_name.replace("_", "-").lower()
    return (
        first.replace("_", "-").startswith(f"{normalized_package}-")
        and first.endswith((".dist-info", ".egg-info"))
        and path.name in {"METADATA", "RECORD"}
    )


def _is_package_code_path(path: PurePosixPath, package_name: str) -> bool:
    first = path.parts[0].lower() if path.parts else ""
    return first.replace("-", "_") in {package_name, f"{package_name}.py"}
