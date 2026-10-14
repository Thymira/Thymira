from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tooling.conftest import load_script


def make_repository(root: Path) -> Path:
    """Create the minimum maintained-root layout expected by the quality scanner."""
    quality = load_script("scripts/check_quality.py")
    for relative in (*quality.SOURCE_ROOTS, ".github/workflows"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir()
    configs = {
        ".pre-commit-config.yaml": "---\nrepos: []\n",
        ".yamllint.yaml": (
            "---\nextends: default\nignore: |\n"
            "  .venv/\n  runs/\n  .pytest-tmp*/\n  .pytest_tmp*/\n"
        ),
        "compose.yaml": "---\nservices: {}\n",
        ".github/workflows/ci.yml": "---\nname: CI\n",
        "examples/credit-risk/.thymira/config.yaml": "---\nproject: demo\n",
        "examples/credit-risk/.thymira/policies.yaml": "---\npolicies: []\n",
    }
    for relative, content in configs.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    (root / "packages" / "baseline.py").write_text(
        '"""Baseline source fixture."""\n\n\n'
        "def baseline() -> int:\n"
        '    """Return the baseline value."""\n'
        "    return 1\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def test_inventory_includes_untracked_owned_inputs_and_excludes_history(tmp_path: Path) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    source = root / "runtime" / "new_untracked.py"
    source.write_text(
        '"""Untracked maintained source fixture."""\n\n'
        "def new_source() -> int:\n"
        '    """Return the new source value."""\n'
        "    return 1\n",
        encoding="utf-8",
    )
    config = root / ".agents" / "skills" / "new_untracked.yaml"
    config.write_text("---\nvalue: maintained\n", encoding="utf-8")
    root_script = root / "repository_probe.py"
    root_script.write_text('"""Root-level source fixture."""\n', encoding="utf-8", newline="\n")
    root_config = root / "repository_probe.yml"
    root_config.write_text("---\nvalue: maintained\n", encoding="utf-8", newline="\n")
    historical = root / "examples" / "credit-risk" / ".thymira" / "runtime" / "old.yaml"
    historical.parent.mkdir(parents=True)
    historical.write_text("historical: [\n", encoding="utf-8")
    frozen = root / ".tmp-integrated-closure-review" / "generated.py"
    frozen.parent.mkdir()
    frozen.write_text("import os\n", encoding="utf-8")

    inputs = quality.collect_quality_inputs(root)

    assert "runtime/new_untracked.py" in inputs.python_files
    assert "repository_probe.py" in inputs.python_files
    assert ".agents/skills/new_untracked.yaml" in inputs.yaml_files
    assert "repository_probe.yml" in inputs.yaml_files
    assert "examples/credit-risk/.thymira/config.yaml" in inputs.yaml_files
    assert "examples/credit-risk/.thymira/runtime/old.yaml" not in inputs.yaml_files
    assert all(not path.startswith(".tmp-") for path in (*inputs.python_files, *inputs.yaml_files))


def test_check_rejects_an_untracked_python_source(tmp_path: Path, capfd) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    untracked = root / "packages" / "new_untracked.py"
    untracked.write_text("import os\n", encoding="utf-8", newline="\n")

    result = quality.run_quality("check", repository=root)
    output = capfd.readouterr()

    assert result != 0
    assert untracked.name in output.out + output.err
    assert "F401" in output.out + output.err


def test_check_rejects_an_untracked_yaml_config(tmp_path: Path, capfd) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    untracked = root / "tests" / "new_untracked.yaml"
    untracked.write_text("broken: [\n", encoding="utf-8", newline="\n")

    result = quality.run_quality("check", repository=root)
    output = capfd.readouterr()

    assert result != 0
    assert untracked.name in output.out + output.err
    assert "syntax error" in (output.out + output.err).lower()


@pytest.mark.parametrize(
    "relative_path",
    [
        "apps/monitoring/.venv/project-config.yaml",
        "runtime/agents/runs/project-config.yaml",
        "tests/.pytest-tmp-case/project-config.yaml",
        ".agents/skills/tool/.pytest_tmp-cache/project-config.yaml",
    ],
)
@pytest.mark.parametrize("ci", [False, True], ids=["local", "ci"])
def test_check_rejects_yaml_despite_inherited_ignore_patterns(
    tmp_path: Path, capfd, relative_path: str, ci: bool
) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    invalid = root / relative_path
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("broken: [\n", encoding="utf-8", newline="\n")

    result = quality.run_quality("check", repository=root, ci=ci)
    output = capfd.readouterr()

    assert result != 0
    assert invalid.name in output.out + output.err
    assert "syntax error" in (output.out + output.err).lower()


def test_check_leaves_generated_review_evidence_outside_owned_inputs(tmp_path: Path) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    generated = root / ".tmp-integrated-closure-review"
    generated.mkdir()
    bad_source = generated / "generated.py"
    bad_yaml = generated / "generated.yaml"
    bad_source.write_text("import os\n", encoding="utf-8", newline="\n")
    bad_yaml.write_text("broken: [\n", encoding="utf-8", newline="\n")
    before = bad_source.read_bytes(), bad_yaml.read_bytes()

    result = quality.run_quality("check", repository=root)

    assert result == 0
    assert (bad_source.read_bytes(), bad_yaml.read_bytes()) == before


def test_inventory_fails_closed_when_required_root_is_missing(tmp_path: Path) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    (root / "apps").rmdir()

    with pytest.raises(quality.QualityScanError, match="apps"):
        quality.collect_quality_inputs(root)


def test_inventory_fails_closed_when_walking_a_root_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    runtime_root = root / "runtime"
    original_walk = Path.walk

    def fail_runtime_walk(
        path: Path,
        *,
        top_down: bool = True,
        on_error=None,
        follow_symlinks: bool = False,
    ):
        if path == runtime_root:
            yield path, [], []
            raise PermissionError("test traversal denial")
        yield from original_walk(
            path,
            top_down=top_down,
            on_error=on_error,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(Path, "walk", fail_runtime_walk)

    with pytest.raises(quality.QualityScanError, match="cannot enumerate quality root"):
        quality.collect_quality_inputs(root)


def test_inventory_fails_closed_when_an_owned_file_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    owned = root / "runtime" / "locked.py"
    owned.write_text("def locked():\n    return 1\n", encoding="utf-8")
    original_open = Path.open

    def fail_owned_open(path: Path, *args, **kwargs):
        if path == owned:
            raise PermissionError("test read denial")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_owned_open)

    with pytest.raises(quality.QualityScanError, match="cannot read quality input"):
        quality.collect_quality_inputs(root)


def test_inventory_rejects_symlinked_directories_in_owned_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    redirected = root / "runtime" / "redirected"
    redirected.mkdir()
    original_is_symlink = Path.is_symlink

    def redirected_is_symlink(path: Path) -> bool:
        return path == redirected or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", redirected_is_symlink)

    with pytest.raises(quality.QualityScanError, match="symlink is not an owned quality input"):
        quality.collect_quality_inputs(root)


@pytest.mark.parametrize("redirected_relative", [".agents", "examples/credit-risk/.thymira"])
def test_inventory_rejects_symlinked_ancestors_of_nested_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redirected_relative: str
) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    redirected = root / redirected_relative
    original_is_symlink = Path.is_symlink

    def redirected_is_symlink(path: Path) -> bool:
        return path == redirected or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", redirected_is_symlink)

    with pytest.raises(quality.QualityScanError, match="symlink is not an owned"):
        quality.collect_quality_inputs(root)


def test_later_yaml_batch_failure_is_returned(tmp_path: Path, monkeypatch, capfd) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    monkeypatch.setattr(quality, "MAX_COMMAND_UNITS", 450)
    for index in range(40):
        path = root / "tests" / f"{index:03d}-{'x' * 62}.yaml"
        path.write_text(f"---\nvalue: {index}\n", encoding="utf-8", newline="\n")
    invalid = root / "tests" / f"zzz-last-{'x' * 62}.yaml"
    invalid.write_text("broken: [\n", encoding="utf-8", newline="\n")

    result = quality.run_quality("check", repository=root)
    output = capfd.readouterr()

    assert result != 0
    yaml_commands = [line for line in output.out.splitlines() if "yamllint" in line]
    assert len(yaml_commands) > 1
    assert invalid.name in output.out + output.err
    assert "syntax error" in (output.out + output.err).lower()


def test_ruff_failure_in_a_later_python_batch_is_returned(
    tmp_path: Path, monkeypatch, capfd
) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    monkeypatch.setattr(quality, "MAX_COMMAND_UNITS", 450)
    for index in range(40):
        path = root / "tests" / f"{index:03d}-{'x' * 62}.py"
        path.write_text(
            '"""Batch source fixture."""\n\n\n'
            f"def valid_{index}() -> int:\n"
            '    """Return the batch value."""\n'
            f"    return {index}\n",
            encoding="utf-8",
            newline="\n",
        )
    invalid = root / "tests" / f"zzz-last-{'x' * 62}.py"
    invalid.write_text("import os\n", encoding="utf-8", newline="\n")

    result = quality.run_quality("check", repository=root)
    output = capfd.readouterr()

    assert result != 0
    ruff_commands = [line for line in output.out.splitlines() if "ruff" in line]
    assert len(ruff_commands) > 1
    assert invalid.name in output.out + output.err
    assert "F401" in output.out + output.err


def test_ruff_batcher_keeps_format_and_fix_on_the_same_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    quality = load_script("scripts/check_quality.py")
    root = make_repository(tmp_path)
    calls: list[list[str]] = []

    def successful_run(command, *, cwd, check):
        assert cwd == root
        assert check is False
        calls.append(list(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(quality.subprocess, "run", successful_run)
    assert quality.run_quality("format", repository=root) == 0
    format_paths = [command[command.index("ruff") + 2 :] for command in calls]
    calls.clear()
    assert quality.run_quality("fix", repository=root) == 0
    check_paths = [command[command.index("ruff") + 3 :] for command in calls if "check" in command]
    fixed_paths = [command[command.index("ruff") + 2 :] for command in calls if "format" in command]

    assert format_paths == fixed_paths
    assert check_paths == format_paths
