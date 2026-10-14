"""Project explorer confinement, redaction and bounded previews over real temporary files."""

from __future__ import annotations

import multiprocessing
import secrets
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from thymira.core.project_files import (
    MAX_FILE_BYTES,
    ProjectFileConflictError,
    ProjectFileError,
    ProjectFileReadOnlyError,
    ProjectFileRevision,
    ProjectFileTooLargeError,
    list_project_files,
    read_project_file,
    read_project_file_details,
    write_project_file,
)
from thymira.events import sha256_text
from thymira.state.project_file_lock import project_file_lock


def _write_project_file_from_child(root: str, expected_sha256: str) -> str:
    try:
        write_project_file(
            Path(root),
            "notes.md",
            "Second process edit\n",
            expected_sha256=expected_sha256,
        )
    except ProjectFileConflictError:
        return "conflict"
    return "saved"


def test_project_explorer_lists_project_files_and_hides_private_state(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "readme.md").write_text("Hello", encoding="utf-8")
    (tmp_path / ".thymira").mkdir()
    (tmp_path / ".env").write_text("secret", encoding="utf-8")
    (tmp_path / "private.key").write_text("secret", encoding="utf-8")
    entries, truncated = list_project_files(tmp_path)
    assert [entry.path for entry in entries] == ["src", "readme.md"]
    assert not truncated


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "/outside.txt",
        "C:/outside.txt",
        "src\\file.txt",
        ".env",
        ".thymira/api-token",
        "private.key",
        "src/../readme.md",
        "readme.md.",
        "readme.md ",
        "src//file.txt",
    ],
)
def test_project_explorer_rejects_path_escape_aliases_and_private_files(tmp_path, path):
    with pytest.raises(ProjectFileError):
        read_project_file(tmp_path, path)


def test_project_preview_is_bounded_and_redacts_credentials(tmp_path, monkeypatch):
    secret = secrets.token_urlsafe(32)
    monkeypatch.setenv("EXPLORER_TEST_API_KEY", secret)
    (tmp_path / "report.txt").write_text(secret + "\n" + "x" * MAX_FILE_BYTES, encoding="utf-8")
    text, truncated = read_project_file(tmp_path, "report.txt")
    assert secret not in text
    assert "REDACTED" in text
    assert truncated
    assert len(text) <= MAX_FILE_BYTES


def test_project_file_details_mark_plain_text_editable_with_a_revision(tmp_path):
    (tmp_path / "notes.md").write_text("Project notes\n", encoding="utf-8", newline="\n")

    preview = read_project_file_details(tmp_path, "notes.md")

    assert preview.content == "Project notes\n"
    assert preview.truncated is False
    assert preview.sha256 == sha256_text("Project notes\n")
    assert preview.editable is True
    assert preview.read_only_reason is None
    assert read_project_file(tmp_path, "notes.md") == (preview.content, False)


def test_project_file_write_persists_only_an_existing_file(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("Old notes\n", encoding="utf-8")
    before = read_project_file_details(tmp_path, "notes.md")
    expected_sha256 = before.sha256
    assert expected_sha256 is not None

    saved = write_project_file(
        tmp_path,
        "notes.md",
        "Updated notes\n",
        expected_sha256=expected_sha256,
    )

    assert target.read_text(encoding="utf-8") == "Updated notes\n"
    assert saved.sha256 == sha256_text("Updated notes\n")
    assert read_project_file_details(tmp_path, "notes.md").sha256 == saved.sha256
    assert not (tmp_path / "missing.md").exists()


def test_project_file_write_refuses_a_stale_revision_without_overwriting(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("Current notes\n", encoding="utf-8")

    with pytest.raises(ProjectFileConflictError, match="changed"):
        write_project_file(
            tmp_path,
            "notes.md",
            "Stale draft\n",
            expected_sha256=sha256_text("Older notes\n"),
        )

    assert target.read_text(encoding="utf-8") == "Current notes\n"


def test_concurrent_project_file_writes_publish_only_one_matching_revision(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("Starting notes\n", encoding="utf-8", newline="\n")
    expected_sha256 = sha256_text("Starting notes\n")
    barrier = threading.Barrier(2)

    def save(content: str) -> ProjectFileRevision | ProjectFileConflictError:
        barrier.wait()
        try:
            return write_project_file(
                tmp_path,
                "notes.md",
                content,
                expected_sha256=expected_sha256,
            )
        except ProjectFileConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(save, ("First edit\n", "Second edit\n")))

    assert sum(not isinstance(result, ProjectFileConflictError) for result in results) == 1
    assert sum(isinstance(result, ProjectFileConflictError) for result in results) == 1
    assert target.read_text(encoding="utf-8") in {"First edit\n", "Second edit\n"}


def test_project_file_writers_in_separate_processes_exclude_each_other(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("Starting notes\n", encoding="utf-8", newline="\n")
    expected_sha256 = sha256_text("Starting notes\n")

    with (
        project_file_lock(tmp_path),
        ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor,
    ):
        result = executor.submit(
            _write_project_file_from_child,
            str(tmp_path),
            expected_sha256,
        ).result(timeout=20)

    assert result == "conflict"
    assert target.read_text(encoding="utf-8") == "Starting notes\n"


def test_project_file_write_rejects_a_symlink_lockfile(tmp_path):
    target = tmp_path / "notes.md"
    target.write_text("Original\n", encoding="utf-8")
    outside = tmp_path / "outside.lock"
    outside.write_text("Keep this file\n", encoding="utf-8")
    try:
        (tmp_path / ".thymira-file-edit.lock").symlink_to(outside)
    except OSError:
        pytest.skip("this Windows account cannot create symbolic links")

    with pytest.raises(ProjectFileError, match="lock"):
        write_project_file(
            tmp_path,
            "notes.md",
            "Updated\n",
            expected_sha256=sha256_text("Original\n"),
        )

    assert target.read_text(encoding="utf-8") == "Original\n"
    assert outside.read_text(encoding="utf-8") == "Keep this file\n"


def test_project_file_write_refuses_redacted_truncated_and_secret_content(tmp_path):
    (tmp_path / "personal.md").write_text("Contact ana@example.com\n", encoding="utf-8")
    (tmp_path / "large.md").write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    redacted = read_project_file_details(tmp_path, "personal.md")
    truncated = read_project_file_details(tmp_path, "large.md")
    redacted_revision = redacted.sha256

    assert redacted.editable is False
    assert redacted.read_only_reason == "redacted"
    assert redacted_revision is not None
    assert truncated.editable is False
    assert truncated.read_only_reason == "truncated"
    assert truncated.sha256 is None
    with pytest.raises(ProjectFileReadOnlyError):
        write_project_file(
            tmp_path,
            "personal.md",
            "Replace safely\n",
            expected_sha256=redacted_revision,
        )
    with pytest.raises(ProjectFileReadOnlyError):
        write_project_file(
            tmp_path,
            "large.md",
            "Replace safely\n",
            expected_sha256=sha256_text("unused"),
        )
    with pytest.raises(ProjectFileReadOnlyError, match="secrets"):
        write_project_file(
            tmp_path,
            "personal.md",
            "Contact ana@example.com\n",
            expected_sha256=redacted_revision,
        )
    with pytest.raises(ProjectFileTooLargeError):
        write_project_file(
            tmp_path,
            "personal.md",
            "x" * (MAX_FILE_BYTES + 1),
            expected_sha256=redacted_revision,
        )


def test_project_preview_refuses_binary_files(tmp_path):
    (tmp_path / "sample.bin").write_bytes(b"hello\0world")
    with pytest.raises(ProjectFileError, match="Binary"):
        read_project_file(tmp_path, "sample.bin")


def test_project_explorer_refuses_hardlinks_to_outside_files(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("Outside private content", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "alias.txt").hardlink_to(outside)

    assert list_project_files(project)[0] == ()
    with pytest.raises(ProjectFileError, match="links"):
        read_project_file_details(project, "alias.txt")
    with pytest.raises(ProjectFileError, match="links"):
        write_project_file(
            project,
            "alias.txt",
            "Changed",
            expected_sha256=sha256_text("Outside private content"),
        )
    assert outside.read_text(encoding="utf-8") == "Outside private content"


def test_project_preview_refuses_a_mismatched_open_handle(tmp_path, monkeypatch):
    outside = tmp_path / "outside.txt"
    outside.write_text("Outside private content", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    target = project / "notes.md"
    target.write_text("Inside", encoding="utf-8")
    original_open = Path.open

    def open_after_path_swap(path, *args, **kwargs):
        # Model the handle returned if a path changes after validation but before open.
        return original_open(outside if path == target else path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_after_path_swap)
    with pytest.raises(ProjectFileError, match="changed"):
        read_project_file_details(project, "notes.md")


def test_project_explorer_does_not_follow_links(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("unavailable", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    try:
        (project / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this Windows account cannot create symbolic links")
    entries, _ = list_project_files(project)
    assert entries == ()
    with pytest.raises(ProjectFileError, match="links"):
        read_project_file(project, "link/private.txt")
