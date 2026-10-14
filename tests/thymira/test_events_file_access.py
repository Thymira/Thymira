"""Tests for the events-owned private-file publication seam."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from thymira.events import create_private_file, file_access, secure_directory, secure_file

if TYPE_CHECKING:
    from ctypes import wintypes
    from pathlib import Path


def test_create_private_file_secures_before_publishing_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exclusive file is private before its caller-provided bytes are written."""
    path = tmp_path / "api-token"
    secured_before_publish = False
    secure_existing_file = file_access.secure_file

    def verify_empty_file_before_publishing(
        target: Path,
    ) -> file_access.StoragePermissionEvidence:
        nonlocal secured_before_publish
        if target == path and not secured_before_publish:
            assert target.read_bytes() == b""
            evidence = secure_existing_file(target)
            if evidence.platform == "nt":
                assert file_access._read_sddl(target) == file_access._expected_sddl(
                    file_access._current_user_sid(), directory=False
                )
            secured_before_publish = True
            return evidence
        return secure_existing_file(target)

    monkeypatch.setattr(file_access, "secure_file", verify_empty_file_before_publishing)

    evidence = create_private_file(path, b"token-value")

    assert path.read_bytes() == b"token-value"
    assert evidence.path == path
    assert secured_before_publish
    if evidence.platform == "nt":
        assert file_access._read_sddl(path) == file_access._expected_sddl(
            file_access._current_user_sid(), directory=False
        )
    else:
        assert path.stat().st_mode & 0o777 == 0o600


def test_create_private_file_refuses_overwrite(tmp_path: Path) -> None:
    """A publication target cannot silently replace an existing credential file."""
    path = tmp_path / "api-token"
    path.write_bytes(b"original")

    with pytest.raises(FileExistsError):
        create_private_file(path, b"replacement")

    assert path.read_bytes() == b"original"


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL inheritance behavior")
def test_securing_directory_does_not_reinherit_existing_child(tmp_path: Path) -> None:
    """Securing one directory changes neither existing child DACLs nor their protection."""
    directory = tmp_path / "run"
    directory.mkdir()
    sid = file_access._current_user_sid()
    initial_directory_sddl = f"D:P(A;;FA;;;{sid})(A;OICI;FR;;;SY)"
    descriptor, dacl = file_access._descriptor_dacl(initial_directory_sddl)
    try:
        result = file_access._ADVAPI.SetNamedSecurityInfoW(
            str(directory),
            file_access._SE_FILE_OBJECT,
            file_access._DACL_SECURITY_INFORMATION
            | file_access._PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )
        assert result == 0
    finally:
        file_access._KERNEL32.LocalFree(descriptor)
    path = directory / "existing"
    path.write_bytes(b"")
    original = file_access._read_sddl(path)
    assert original == "D:AI(A;ID;FR;;;SY)"

    secure_directory(directory)

    assert file_access._read_sddl(directory) == file_access._expected_sddl(sid, directory=True)
    assert file_access._read_sddl(path) == original


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL inheritance behavior")
def test_concurrent_parent_acl_update_preserves_existing_file_dacl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent DACL update cannot replace a protected child's explicit ACL."""
    expected = file_access._expected_sddl(file_access._current_user_sid(), directory=False)

    build_descriptor = file_access._descriptor_dacl

    for index in range(300):
        directory = tmp_path / f"run-{index}"
        directory.mkdir()
        secure_directory(directory)
        path = directory / ".run-staged.tmp"
        path.write_bytes(b"")
        barrier = threading.Barrier(2)

        def synchronized_descriptor(
            sddl: str, sync_barrier: threading.Barrier = barrier
        ) -> tuple[wintypes.LPVOID, wintypes.LPVOID]:
            sync_barrier.wait(timeout=5)
            return build_descriptor(sddl)

        with monkeypatch.context() as patch:
            patch.setattr(file_access, "_descriptor_dacl", synchronized_descriptor)
            with ThreadPoolExecutor(max_workers=2) as workers:
                parent_update = workers.submit(secure_directory, directory)
                file_update = workers.submit(secure_file, path)
                parent_update.result()
                file_update.result()
        actual = file_access._read_sddl(path)
        assert actual == expected, f"existing child DACL was re-inherited: {actual}"
