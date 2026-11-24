from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import unicodedata
from pathlib import Path
from typing import NoReturn, override

from .attention import MAX_PATH, MAX_SPOOL_BYTES, SPOOL

SCHEMA = "seatlab.workbench.instrument/v1"
PROG = "python3.12 -BS -m examples.seatlab.workbench instrument"
DESCRIPTION = (
    "Prepare one new private observer bundle and native plugin flags for one future local multi-seat job. "
    "Never creates the job and never starts Harbor, a seat or any process. Assumes trusted, stationary parent "
    "directories owned by the operator. Refuses an existing job or bundle path."
)
STARTS = "examples.seatlab.workbench.input_keeper:InputStartKeeper"
KEEPERS = (
    ("starts", STARTS, "archive_dir"),
    ("seats", "examples.seatlab.workbench.attempt_keeper:AttemptKeeper", "archive_dir"),
    (
        "accounts",
        "examples.seatlab.workbench.account_keeper:AccountKeeper",
        "account_dir",
    ),
)
HANDOFFS = (
    "handoffs",
    "examples.seat_handoffs.attempt_keeper:HandoffAttemptKeeper",
    "archive_dir",
)
MAX_ROOT = MAX_PATH - len("/handoffs")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
ERRORS = {
    "unsafe_path": "A path is too long, not plain UTF-8, has control characters, '..' parts or a job name ending in space.",
    "parent_refused": "A parent directory is missing, aliased, not a directory, not writable or not owned by this user.",
    "job_exists": "The job path already exists. Instrument only a future job.",
    "bundle_exists": "The bundle path already exists. Choose a new bundle path.",
    "nested": "The job and bundle paths nest in or alias each other.",
    "device": "The job and bundle parent directories are on different devices.",
    "unsupported": "This platform lacks linkat with directory descriptors.",
    "incomplete": "The bundle is incomplete and not ready. Leave it for inspection and choose a new bundle path.",
}


class Refused(Exception):
    def __init__(self, code: str) -> None:
        assert code in ERRORS
        super().__init__(code)
        self.code = code


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "Invalid instrument arguments. Use instrument --help.\n")


def _linkable() -> bool:
    return os.link in os.supports_dir_fd and os.link in os.supports_follow_symlinks


def _plain(value: Path) -> tuple[str, os.stat_result]:
    try:
        path = os.path.join(os.getcwd(), value)
        size = len(path.encode("utf-8"))
    except (UnicodeEncodeError, OSError):
        raise Refused("unsafe_path") from None
    parent, name = os.path.split(path)
    if (
        size > MAX_ROOT
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or os.path.normpath(path) != path
        or path.startswith("//")
        or name in ("", ".", "..")
        or len(name.encode("utf-8")) > 255
    ):
        raise Refused("unsafe_path")
    try:
        found = os.lstat(parent)
        real = os.path.realpath(parent, strict=True)
    except (OSError, ValueError):
        raise Refused("parent_refused") from None
    if (
        real != parent
        or not stat.S_ISDIR(found.st_mode)
        or found.st_uid != os.geteuid()
    ):
        raise Refused("parent_refused")
    return path, found


def _absent(path: str, code: str) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        pass
    raise Refused(code)


def _fold(path: str) -> str:
    return unicodedata.normalize("NFC", os.path.basename(path)).casefold()


def _check(job: Path, bundle: Path) -> tuple[str, str, int]:
    (job_path, job_parent), (bundle_path, bundle_parent) = _plain(job), _plain(bundle)
    if job_path.strip() != job_path:
        raise Refused("unsafe_path")
    if job_parent.st_dev != bundle_parent.st_dev:
        raise Refused("device")
    shared = job_parent.st_ino == bundle_parent.st_ino
    if (
        shared != (os.path.dirname(job_path) == os.path.dirname(bundle_path))
        or (shared and _fold(job_path) == _fold(bundle_path))
        or Path(job_path).is_relative_to(bundle_path)
        or Path(bundle_path).is_relative_to(job_path)
    ):
        raise Refused("nested")
    _absent(job_path, "job_exists")
    _absent(bundle_path, "bundle_exists")
    return job_path, bundle_path, bundle_parent.st_dev


def _pin(dir_fd: int | None, name: str, device: int) -> int:
    fd = os.open(name, DIR_FLAGS, dir_fd=dir_fd)
    try:
        found = os.fstat(fd)
        if (
            not stat.S_ISDIR(found.st_mode)
            or found.st_uid != os.geteuid()
            or found.st_dev != device
        ):
            raise Refused("incomplete")
        os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _publish(dir_fd: int, name: str, value: object) -> str:
    data = (
        json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    ).encode("ascii")
    assert 0 < len(data) <= MAX_SPOOL_BYTES
    temp = f".{name}.tmp"
    fd = os.open(temp, FILE_FLAGS, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        for _ in range(len(data)):
            view = view[os.write(fd, view) :]
            if not view:
                break
        if view:
            raise Refused("incomplete")
        os.fsync(fd)
    finally:
        os.close(fd)
    os.link(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
    os.unlink(temp, dir_fd=dir_fd)
    os.fsync(dir_fd)
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _fill(
    bundle_fd: int,
    job: str,
    bundle: str,
    keepers: tuple[tuple[str, str, str], ...],
    device: int,
) -> dict[str, object]:
    flags: list[str] = []
    for name, plugin, key in keepers:
        os.mkdir(name, 0o700, dir_fd=bundle_fd)
        os.close(_pin(bundle_fd, name, device))
        flags += [
            "--plugin",
            plugin,
            "--plugin-kwarg",
            f"{plugin}.{key}={os.path.join(bundle, name)}",
        ]
        if plugin == STARTS:
            flags += ["--plugin-kwarg", f"{plugin}.expected_job_dir={job}"]
    _absent(job, "incomplete")
    row = {"job": job} | {name: os.path.join(bundle, name) for name, _, _ in keepers}
    spool = _publish(bundle_fd, "spool.json", {"schema": SPOOL, "jobs": [row]})
    return {
        "schema": SCHEMA,
        "status": "prepared",
        "capabilities": [name for name, _, _ in keepers],
        "spool_sha256": spool,
        "flags_sha256": _publish(bundle_fd, "flags.json", flags),
    }


def prepare(job: Path, bundle: Path, *, handoffs: bool) -> dict[str, object]:
    if (
        not isinstance(job, Path)
        or not isinstance(bundle, Path)
        or type(handoffs) is not bool
    ):
        raise TypeError("prepare needs two paths and a boolean handoffs option")
    if not _linkable():
        raise Refused("unsupported")
    job_path, bundle_path, device = _check(job, bundle)
    try:
        os.mkdir(bundle_path, 0o700)
    except FileExistsError:
        raise Refused("bundle_exists") from None
    except OSError:
        raise Refused("parent_refused") from None
    try:
        bundle_fd = _pin(None, bundle_path, device)
    except (OSError, Refused):
        raise Refused("incomplete") from None
    try:
        return _fill(
            bundle_fd,
            job_path,
            bundle_path,
            KEEPERS + ((HANDOFFS,) if handoffs else ()),
            device,
        )
    except (OSError, ValueError, Refused):
        raise Refused("incomplete") from None
    finally:
        os.close(bundle_fd)


def main(argv: list[str] | None = None) -> int:
    parser = Parser(prog=PROG, description=DESCRIPTION, allow_abbrev=False)
    parser.add_argument("job", metavar="JOB_DIR")
    parser.add_argument("bundle", metavar="NEW_BUNDLE")
    parser.add_argument("--handoffs", action="store_true")
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        summary = prepare(
            Path(options.job), Path(options.bundle), handoffs=options.handoffs
        )
    except Refused as refused:
        rejected = {
            "schema": SCHEMA,
            "status": "rejected",
            "error": refused.code,
            "message": ERRORS[refused.code],
        }
        sys.stderr.write(json.dumps(rejected, sort_keys=True) + "\n")
        return 2
    sys.stdout.write(json.dumps(summary, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
