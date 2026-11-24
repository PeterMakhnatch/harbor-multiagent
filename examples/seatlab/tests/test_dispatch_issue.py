import errno
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from examples.seatlab.dispatch_issue import open_spool

_SUBJECT = "a" * 64
_OTHER = "b" * 64
_UNSAFE = (OSError, ValueError)


@pytest.fixture
def spool_path(tmp_path: Path) -> Path:
    path = tmp_path / "spool"
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def test_only_one_launcher_reserves_a_shared_subject(spool_path: Path) -> None:
    contenders = 8
    start = Barrier(contenders, timeout=10)

    def reserve() -> bool:
        with open_spool(spool_path) as spool:
            start.wait()
            return spool.reserve(_SUBJECT)

    with ThreadPoolExecutor(max_workers=contenders) as pool:
        attempts = [pool.submit(reserve) for _ in range(contenders)]
        outcomes = [attempt.result(timeout=15) for attempt in attempts]

    assert sum(outcome is True for outcome in outcomes) == 1
    assert sum(outcome is False for outcome in outcomes) == contenders - 1
    with open_spool(spool_path) as spool:
        assert spool.reserved(_SUBJECT) is True
        assert spool.reserved(_OTHER) is False
        assert spool.reserve(_OTHER) is True
    with open_spool(spool_path) as spool:
        assert spool.reserved(_OTHER) is True
        assert spool.reserve(_OTHER) is False


def test_open_spool_stays_bound_to_its_directory(spool_path: Path) -> None:
    moved = spool_path.with_name("moved")
    with open_spool(spool_path) as spool:
        spool_path.rename(moved)
        spool_path.mkdir(mode=0o700)
        spool_path.chmod(0o700)
        assert spool.reserve(_SUBJECT) is True
        assert spool.reserved(_SUBJECT) is True
        with open_spool(spool_path) as replacement:
            assert replacement.reserved(_SUBJECT) is False
    with open_spool(moved) as original:
        assert original.reserved(_SUBJECT) is True


def test_interrupted_empty_record_blocks_retry(spool_path: Path) -> None:
    record = spool_path / _SUBJECT
    record.touch(mode=0o600)
    record.chmod(0o600)

    with open_spool(spool_path) as spool:
        assert spool.reserved(_SUBJECT) is True
        assert spool.reserve(_SUBJECT) is False

    assert record.read_bytes() == b""


def test_success_syncs_the_record_and_directory(
    spool_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fsync = os.fsync
    synced = set()

    def observe_sync(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        if stat.S_ISREG(mode):
            assert stat.S_IMODE(mode) & 0o077 == 0
        real_fsync(fd)
        synced.add(stat.S_IFMT(mode))

    monkeypatch.setattr(os, "fsync", observe_sync)
    with open_spool(spool_path) as spool:
        assert spool.reserve(_SUBJECT) is True
        assert {stat.S_IFREG, stat.S_IFDIR} <= synced


@pytest.mark.parametrize("failure_kind", [stat.S_IFREG, stat.S_IFDIR])
def test_sync_failure_leaves_a_claim_that_blocks_retry(
    spool_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: int
) -> None:
    real_fsync = os.fsync

    def fail_sync(fd: int) -> None:
        if stat.S_IFMT(os.fstat(fd).st_mode) == failure_kind:
            raise OSError(errno.EIO, "injected spool sync failure")
        real_fsync(fd)

    with open_spool(spool_path) as spool, monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail_sync)
        with pytest.raises(OSError, match="injected spool sync failure"):
            spool.reserve(_SUBJECT)
    with open_spool(spool_path) as reopened:
        assert reopened.reserved(_SUBJECT) is True
        assert reopened.reserve(_SUBJECT) is False


@pytest.mark.parametrize("kind", ["missing", "file", "symlink", "broad", "read-only"])
def test_open_rejects_unsafe_final_paths(
    spool_path: Path, tmp_path: Path, kind: str
) -> None:
    candidate = tmp_path / "candidate"
    if kind == "file":
        candidate.write_bytes(b"not a spool")
    elif kind == "symlink":
        candidate.symlink_to(spool_path, target_is_directory=True)
    elif kind in {"broad", "read-only"}:
        candidate.mkdir()
        candidate.chmod(0o770 if kind == "broad" else 0o500)

    before = None if kind == "missing" else candidate.lstat()
    with pytest.raises(_UNSAFE), open_spool(candidate):
        pass

    if before is None:
        assert not candidate.exists()
        assert not candidate.is_symlink()
    else:
        after = candidate.lstat()
        assert (after.st_ino, after.st_mode) == (before.st_ino, before.st_mode)
    if kind == "file":
        assert candidate.read_bytes() == b"not a spool"
    assert stat.S_IMODE(spool_path.stat().st_mode) == 0o700


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "broad"])
def test_unsafe_records_raise_without_changing_targets(
    spool_path: Path, tmp_path: Path, kind: str
) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside the spool")
    outside.chmod(0o600)
    record = spool_path / _SUBJECT
    if kind == "symlink":
        record.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, record)
    elif kind == "directory":
        record.mkdir(mode=0o700)
    else:
        record.write_bytes(b"unsafe record")
        record.chmod(0o640)
    record_before = record.lstat()
    outside_before = outside.stat()

    with open_spool(spool_path) as spool:
        with pytest.raises(_UNSAFE):
            spool.reserved(_SUBJECT)
        with pytest.raises(_UNSAFE):
            spool.reserve(_SUBJECT)

    record_after = record.lstat()
    assert (record_after.st_ino, record_after.st_mode, record_after.st_nlink) == (
        record_before.st_ino,
        record_before.st_mode,
        record_before.st_nlink,
    )
    if kind == "broad":
        assert record.read_bytes() == b"unsafe record"
    outside_after = outside.stat()
    assert (outside_after.st_ino, outside_after.st_mode, outside_after.st_nlink) == (
        outside_before.st_ino,
        outside_before.st_mode,
        outside_before.st_nlink,
    )
    assert outside.read_bytes() == b"outside the spool"


@pytest.mark.parametrize(
    "subject",
    ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "../outside", _SUBJECT + "\n"],
)
def test_malformed_subjects_are_refused(spool_path: Path, subject: str) -> None:
    with open_spool(spool_path) as spool:
        with pytest.raises(ValueError):
            spool.reserved(subject)
        with pytest.raises(ValueError):
            spool.reserve(subject)
