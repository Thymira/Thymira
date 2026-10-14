"""Run Ruff and yamllint over maintained repository inputs only."""

from __future__ import annotations

import argparse
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

SOURCE_ROOTS = (
    "packages",
    "runtime",
    "apps",
    "adapters",
    "scripts",
    "tests",
    ".agents/skills",
)
"""Required roots that own maintained Python and YAML inputs."""

YAML_ROOTS = (".github", "docs", "infrastructure", "services", "snapshots")
"""Additional roots whose maintained YAML files are quality-gated."""

ROOT_YAML_CONFIGS = (
    ".pre-commit-config.yaml",
    ".yamllint.yaml",
    "compose.yaml",
)
"""Authoritative root YAML configurations."""

EXAMPLE_YAML_CONFIGS = (
    "examples/credit-risk/.thymira/config.yaml",
    "examples/credit-risk/.thymira/policies.yaml",
)
"""The example's authoritative configs; its historical runtime tree is not scanned."""

MAX_COMMAND_UNITS = 12_000
"""Keep explicit Ruff and yamllint batches below Windows' command-line limit."""

PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
YAML_SUFFIXES = frozenset({".yaml", ".yml"})
YAMLLINT_OWNED_INPUT_CONFIG = "extends: .yamllint.yaml\nignore: []"


class QualityScanError(RuntimeError):
    """Raised when an owned scan root or input cannot be enumerated or read."""


@dataclass(frozen=True, slots=True)
class QualityInputs:
    """The discovered maintained Python and YAML inputs, relative to one repository root."""

    python_files: tuple[str, ...]
    yaml_files: tuple[str, ...]


def _is_redirected_path(path: Path, boundary: Path) -> bool:
    """Return whether a path or ancestor before ``boundary`` is a link or junction."""
    current = path
    while current != boundary:
        is_junction = getattr(current, "is_junction", None)
        if current.is_symlink() or (callable(is_junction) and is_junction()):
            return True
        current = current.parent
    return False


def _required_directory(repository: Path, relative: str) -> Path:
    """Return a required scan directory or fail closed when it is absent or unreadable."""
    path = repository / relative
    try:
        if _is_redirected_path(path, repository):
            raise QualityScanError(f"symlink is not an owned quality root: {relative}")
        mode = path.stat().st_mode
    except OSError as exc:
        raise QualityScanError(f"cannot access required quality root {relative}: {exc}") from exc
    if not stat.S_ISDIR(mode):
        raise QualityScanError(f"required quality root is not a directory: {relative}")
    return path


def _optional_directory(repository: Path, relative: str) -> Path | None:
    """Return an optional YAML root, distinguishing absence from access errors."""
    path = repository / relative
    try:
        if _is_redirected_path(path, repository):
            raise QualityScanError(f"symlink is not an owned quality root: {relative}")
        mode = path.stat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise QualityScanError(f"cannot access optional quality root {relative}: {exc}") from exc
    if not stat.S_ISDIR(mode):
        raise QualityScanError(f"optional quality root is not a directory: {relative}")
    return path


def _walk_files(root: Path) -> Iterator[Path]:
    """Yield every file below ``root`` and propagate directory traversal failures."""

    def fail_on_error(error: OSError) -> None:
        raise error

    try:
        for directory, directory_names, filenames in root.walk(
            on_error=fail_on_error, follow_symlinks=False
        ):
            for name in (*directory_names, *filenames):
                path = directory / name
                if _is_redirected_path(path, root):
                    raise QualityScanError(f"symlink is not an owned quality input: {path}")
            for filename in filenames:
                yield directory / filename
    except OSError as exc:
        raise QualityScanError(f"cannot enumerate quality root {root}: {exc}") from exc


def _discover_files(roots: Sequence[Path], suffixes: frozenset[str]) -> set[Path]:
    """Return files of selected suffixes under already validated roots."""
    files: set[Path] = set()
    for root in roots:
        for path in _walk_files(root):
            if path.suffix.lower() in suffixes:
                files.add(path)
    return files


def _repository_root_files(repository: Path, suffixes: frozenset[str]) -> set[Path]:
    """Enumerate matching direct repository files, propagating listing failures."""
    try:
        return {path for path in repository.iterdir() if path.suffix.lower() in suffixes}
    except OSError as exc:
        raise QualityScanError(f"cannot enumerate repository root {repository}: {exc}") from exc


def _required_yaml_configs(repository: Path) -> set[Path]:
    """Validate and return the authoritative root and example YAML configurations."""
    paths: set[Path] = set()
    for relative in (*ROOT_YAML_CONFIGS, *EXAMPLE_YAML_CONFIGS):
        path = repository / relative
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise QualityScanError(f"required YAML config is not a file: {relative}")
        except OSError as exc:
            raise QualityScanError(f"cannot access required YAML config {relative}: {exc}") from exc
        paths.add(path)
    return paths


def _readable_regular_file(path: Path, repository: Path) -> str:
    """Read an owned input in bounded chunks and return its repository-relative name."""
    relative = path.relative_to(repository).as_posix()
    if _is_redirected_path(path, repository):
        raise QualityScanError(f"symlink is not an owned quality input: {relative}")
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise QualityScanError(f"cannot inspect quality input {relative}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise QualityScanError(f"quality input is not a regular file: {relative}")
    try:
        with path.open("rb") as source:
            while source.read(64 * 1024):
                pass
    except OSError as exc:
        raise QualityScanError(f"cannot read quality input {relative}: {exc}") from exc
    return relative


def collect_quality_inputs(repository: Path) -> QualityInputs:
    """Discover and read-check every maintained Python and YAML input under owned roots."""
    repository = repository.resolve(strict=True)
    source_roots = tuple(_required_directory(repository, name) for name in SOURCE_ROOTS)
    yaml_roots = [*source_roots]
    github_root = _required_directory(repository, ".github")
    yaml_roots.append(github_root)
    for relative in YAML_ROOTS[1:]:
        optional_root = _optional_directory(repository, relative)
        if optional_root is not None:
            yaml_roots.append(optional_root)

    python_files = _discover_files(source_roots, PYTHON_SUFFIXES)
    python_files.update(_repository_root_files(repository, PYTHON_SUFFIXES))
    yaml_files = _discover_files(yaml_roots, YAML_SUFFIXES)
    yaml_files.update(_repository_root_files(repository, YAML_SUFFIXES))
    yaml_files.update(_required_yaml_configs(repository))

    source_names = sorted(_readable_regular_file(path, repository) for path in python_files)
    yaml_names = sorted(_readable_regular_file(path, repository) for path in yaml_files)
    return QualityInputs(
        python_files=tuple(source_names),
        yaml_files=tuple(yaml_names),
    )


def _argument_units(argument: str) -> int:
    """Approximate the Windows command-line width of one safely quoted argument."""
    units = len(argument.encode("utf-16-le")) // 2
    return units + (2 if any(character.isspace() for character in argument) else 0)


def _path_batches(paths: Sequence[str], prefix: Sequence[str]) -> Iterator[list[str]]:
    """Yield explicit file batches below the Windows command-line safety limit."""
    prefix_units = sum(_argument_units(argument) + 1 for argument in prefix)
    batch: list[str] = []
    units = prefix_units
    for path in paths:
        path_units = _argument_units(path) + 1
        if batch and units + path_units > MAX_COMMAND_UNITS:
            yield batch
            batch = []
            units = prefix_units
        if units + path_units > MAX_COMMAND_UNITS:
            raise QualityScanError(f"quality path exceeds safe command-line limit: {path}")
        batch.append(path)
        units += path_units
    if batch:
        yield batch


def _run(command: Sequence[str], repository: Path, *, description: str) -> int:
    """Run one quality command and return its exit status."""
    print(f"Running {description}")
    try:
        return subprocess.run(command, cwd=repository, check=False).returncode
    except OSError as exc:
        print(f"could not start quality command: {exc}", file=sys.stderr)
        return 2


def _run_ruff(repository: Path, inputs: QualityInputs, arguments: Sequence[str]) -> int:
    """Run Ruff over every enumerated Python input in bounded, explicit batches."""
    prefix = [sys.executable, "-m", "ruff", *arguments]
    for paths in _path_batches(inputs.python_files, prefix):
        options = " ".join(arguments)
        result = _run(
            [*prefix, *paths],
            repository,
            description=f"ruff {options} ({len(paths)} owned Python files)",
        )
        if result:
            return result
    return 0


def _run_yamllint(repository: Path, inputs: QualityInputs, *, strict: bool) -> int:
    """Lint owned YAML paths in bounded batches, stopping at the first failing batch."""
    prefix = [
        sys.executable,
        "-m",
        "yamllint",
        "--config-data",
        YAMLLINT_OWNED_INPUT_CONFIG,
    ]
    if strict:
        prefix.append("--strict")
    for paths in _path_batches(inputs.yaml_files, prefix):
        strict_text = " --strict" if strict else ""
        result = _run(
            [*prefix, *paths],
            repository,
            description=f"yamllint{strict_text} ({len(paths)} owned YAML files)",
        )
        if result:
            return result
    return 0


def _check_quality(
    repository: Path,
    inputs: QualityInputs,
    *,
    output_format: Sequence[str],
    strict_yaml: bool,
) -> int:
    """Run read-only source, format, and YAML checks in fail-fast order."""
    result = _run_ruff(repository, inputs, ("check", *output_format))
    if result:
        return result
    result = _run_ruff(repository, inputs, ("format", "--check"))
    if result:
        return result
    return _run_yamllint(repository, inputs, strict=strict_yaml)


def run_quality(mode: str, *, repository: Path, ci: bool = False) -> int:
    """Run check, format, or safe-fix commands over the same owned source/config inputs."""
    if mode not in {"check", "format", "fix"}:
        raise ValueError(f"unknown quality mode: {mode}")
    if ci and mode != "check":
        raise ValueError("--ci is available only with check mode")
    try:
        inputs = collect_quality_inputs(repository)
    except QualityScanError as exc:
        print(f"quality scan failed closed: {exc}", file=sys.stderr)
        return 2

    output_format = ["--output-format", "github"] if ci else []
    if mode == "format":
        return _run_ruff(repository, inputs, ("format",))
    if mode == "fix":
        result = _run_ruff(repository, inputs, ("check", "--fix"))
        if result:
            return result
        return _run_ruff(repository, inputs, ("format",))

    return _check_quality(
        repository,
        inputs,
        output_format=output_format,
        strict_yaml=ci,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the local/CI quality mode and run its owned checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "format", "fix"))
    parser.add_argument("--ci", action="store_true", help="use CI diagnostics and strict YAML lint")
    args = parser.parse_args(argv)
    try:
        return run_quality(args.mode, repository=Path.cwd(), ci=args.ci)
    except (OSError, QualityScanError) as exc:
        print(f"quality scan failed closed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
