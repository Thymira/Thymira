"""Native lock contention, including Windows locks held beyond an empty file's end."""

import pytest

from thymira.state._lifecycle_lock import FileLock
from thymira.state.lifecycle_errors import OwnerBusyError


@pytest.mark.parametrize("contents", [b"", b"\0"])
def test_windows_contending_lock_never_writes_before_acquiring(tmp_path, contents):
    native = pytest.importorskip("msvcrt")
    path = tmp_path / "review.lock"
    path.write_bytes(contents)
    with path.open("r+b") as owner:
        native.locking(owner.fileno(), native.LK_NBLCK, 1)
        try:
            with pytest.raises(OwnerBusyError), FileLock(path):
                pytest.fail("a second handle acquired an already held review lock")
        finally:
            owner.seek(0)
            native.locking(owner.fileno(), native.LK_UNLCK, 1)
    with FileLock(path):
        assert path.exists()
    assert path.read_bytes() == contents


def test_file_lock_excludes_a_second_owner_and_releases(tmp_path):
    path = tmp_path / "owner.lock"
    with FileLock(path), pytest.raises(OwnerBusyError), FileLock(path):
        pytest.fail("a competing owner entered the lock")
    with FileLock(path):
        assert path.exists()
