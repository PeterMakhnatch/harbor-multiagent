from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_SUBJECT = re.compile(r"[0-9a-f]{64}")


def _validate_subject(subject: str) -> None:
    if not isinstance(subject, str) or _SUBJECT.fullmatch(subject) is None:
        raise ValueError("reservation subject must be lowercase SHA-256 hex")


def _validate_claim(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & ~0o600
    ):
        raise ValueError("unsafe reservation record")


class _Spool:
    def __init__(self, directory_fd: int) -> None:
        self._fd = directory_fd

    def _has_claim(self, subject: str) -> bool:
        try:
            info = os.stat(subject, dir_fd=self._fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as error:
            raise OSError(error.errno, error.strerror) from None
        _validate_claim(info)
        return True

    def reserved(self, subject: str) -> bool:
        _validate_subject(subject)
        return self._has_claim(subject)

    def reserve(self, subject: str) -> bool:
        _validate_subject(subject)
        try:
            claim_fd = os.open(
                subject,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=self._fd,
            )
        except FileExistsError:
            pass
        except OSError as error:
            raise OSError(error.errno, error.strerror) from None
        else:
            try:
                _validate_claim(os.fstat(claim_fd))
                os.fsync(claim_fd)
                os.fsync(self._fd)
            finally:
                os.close(claim_fd)
            return True
        if not self._has_claim(subject):
            raise RuntimeError(
                "reservation record disappeared after exclusive create failed"
            )
        return False


@contextmanager
def open_spool(path: Path) -> Iterator[_Spool]:
    try:
        directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise OSError(error.errno, error.strerror) from None
    try:
        info = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("reservation spool must be an owned mode-0700 directory")
        spool = _Spool(directory_fd)
        try:
            yield spool
        finally:
            spool._fd = -1
    finally:
        os.close(directory_fd)
