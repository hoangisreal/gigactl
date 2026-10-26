"""Owner leases on temporary files, never the real /run hardware lease."""
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gigactld import ownership
from gigactld.ec import EcError, LOCK_PATH
from gigactld.ownership import OWNER_LOCK_PATH, OwnerLease


def test_owner_and_mailbox_locks_are_distinct():
    assert OWNER_LOCK_PATH == "/run/gigactl/owner.lock"
    assert OWNER_LOCK_PATH != LOCK_PATH


def test_acquire_returns_self_and_release_is_idempotent(tmp_path):
    lease = OwnerLease(str(tmp_path / "owner.lock"))
    assert lease.acquire() is lease
    assert lease.acquire() is lease
    lease.release()
    lease.release()
    with OwnerLease(str(tmp_path / "owner.lock"), timeout_s=0):
        pass


def test_bounded_contention_and_release(tmp_path):
    path = str(tmp_path / "owner.lock")
    with OwnerLease(path):
        with pytest.raises(EcError, match="owner lease.*timeout"):
            OwnerLease(path, timeout_s=0).acquire()
    with OwnerLease(path, timeout_s=0):
        pass


def test_positive_contention_timeout_uses_monotonic_clock(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("gigactld.ec.time.monotonic", lambda: now[0])
    monkeypatch.setattr("gigactld.ec.time.sleep", lambda duration: now.__setitem__(0, now[0] + duration))
    path = str(tmp_path / "owner.lock")
    with OwnerLease(path):
        with pytest.raises(EcError, match="owner lease.*timeout"):
            OwnerLease(path, timeout_s=0.05).acquire()
    assert now[0] == pytest.approx(0.05)


def test_exception_releases_the_lease(tmp_path):
    path = str(tmp_path / "owner.lock")
    with pytest.raises(RuntimeError):
        with OwnerLease(path):
            raise RuntimeError("interrupted")
    with OwnerLease(path, timeout_s=0):
        pass


def test_lock_is_private_cloexec_and_not_unlinked_or_truncated(tmp_path):
    path = tmp_path / "owner.lock"
    path.write_text("evidence")
    path.chmod(0o666)
    with OwnerLease(str(path)) as lease:
        assert fcntl.fcntl(lease._fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text() == "evidence"
    assert path.read_text() == "evidence"


def test_symlink_lock_does_not_touch_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("preserve")
    link = tmp_path / "owner.lock"
    link.symlink_to(target)
    with pytest.raises(OSError):
        OwnerLease(str(link)).acquire()
    assert target.read_text() == "preserve"


def test_symlink_parent_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "parent"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        OwnerLease(str(link / "owner.lock")).acquire()
    assert list(target.iterdir()) == []


def test_hardlinked_and_fifo_locks_are_rejected_without_blocking(tmp_path):
    target = tmp_path / "target"
    target.touch()
    link = tmp_path / "hardlink"
    os.link(target, link)
    with pytest.raises(EcError, match="unsafe"):
        OwnerLease(str(link)).acquire()
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(EcError, match="unsafe"):
        OwnerLease(str(fifo)).acquire()


def test_root_directory_validation_uses_uid_and_permissions(monkeypatch):
    for uid, mode in ((1000, 0o40700), (0, 0o40777), (0, 0o40775)):
        monkeypatch.setattr(ownership.os, "fstat", lambda fd: type("S", (), {
            "st_uid": uid, "st_mode": mode})())
        with pytest.raises(EcError, match="root-owned"):
            ownership._check_root_directory(123, "emulated /run")


def test_run_path_uses_root_checked_open_without_opening_real_run(monkeypatch):
    calls = []

    def reject(path):
        calls.append(path)
        raise EcError("emulated unsafe /run parent")

    monkeypatch.setattr(ownership, "_open_run_lock", reject)
    with pytest.raises(EcError, match="unsafe /run"):
        OwnerLease().acquire()
    assert calls == [OWNER_LOCK_PATH]


@pytest.fixture
def emulated_run(tmp_path, monkeypatch):
    """Map only /run's root open to a temporary tree and emulate its root UID."""
    root = tmp_path / "emulated-run"
    root.mkdir(mode=0o700)
    real_open, real_fstat = os.open, os.fstat

    def safe_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == "/run":
            path = str(root)
        elif dir_fd is None:
            pytest.fail(f"unexpected real absolute open: {path}")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    def root_stat(fd):
        info = real_fstat(fd)
        return SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_nlink=info.st_nlink)

    monkeypatch.setattr(ownership.os, "open", safe_open)
    monkeypatch.setattr(ownership.os, "fstat", root_stat)
    return root


def test_real_run_open_walk_is_private_and_cloexec_in_emulation(emulated_run):
    fd = ownership._open_run_lock("/run/gigactl/owner.lock")
    try:
        assert fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        assert (emulated_run / "gigactl" / "owner.lock").is_file()
    finally:
        os.close(fd)


def test_run_open_walk_rejects_writable_parent_in_emulation(emulated_run):
    parent = emulated_run / "gigactl"
    parent.mkdir(mode=0o700)
    parent.chmod(0o777)
    with pytest.raises(EcError, match="unsafe owner lease parent"):
        ownership._open_run_lock("/run/gigactl/owner.lock")
    assert not (parent / "owner.lock").exists()


def test_run_open_walk_rejects_symlink_parent_in_emulation(emulated_run, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (emulated_run / "gigactl").symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        ownership._open_run_lock("/run/gigactl/owner.lock")
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True])
def test_invalid_timeouts_are_rejected_before_opening(timeout):
    with pytest.raises(ValueError):
        OwnerLease("unused", timeout_s=timeout)


def test_process_exit_releases_lock(tmp_path):
    path = str(tmp_path / "owner.lock")
    # Child opens only the injected temporary lock. os._exit avoids Python's
    # context-manager cleanup and tests the kernel descriptor lifetime itself.
    script = (
        "import os; from gigactld.ownership import OwnerLease; "
        "lease = OwnerLease(os.environ['TEST_OWNER_LOCK']).acquire(); os._exit(0)"
    )
    env = dict(os.environ, TEST_OWNER_LOCK=path,
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    with OwnerLease(path, timeout_s=0):
        pass
