from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from ..calendar_notices import heldout, transfer_registration
from ..visibility import calendar_packet as packet
from . import protocol

PUBLISHED_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "results/wait-for-peer/calendar-registration.json"
)
PUBLISHED_SHA256 = "bdf26d746f16a27e3b5120cf94c8af48a03f7df8ec56ab43c8431badcdf18bbc"
REPOSITORY = Path(__file__).resolve().parents[3]
GENERATOR_TREE = "flotilla"
REASONS = ("origin_unavailable", "origin_mismatch")
GIT_SECONDS = 60
GENERATOR_SECONDS = 300
MAX_ARCHIVE_BYTES = 4 << 20
MAX_ARCHIVE_MEMBERS = 256
MAX_TASK_BYTES = 32 << 20
MAX_TASK_ENTRIES = 4096
Rows = tuple[tuple[str, str], ...]


class Unverified(ValueError):
    def __init__(self, reason: str) -> None:
        if reason not in REASONS:
            raise ValueError("unknown cohort origin failure")
        super().__init__(reason)
        self.reason = reason


def load(
    path: Path, digest: str, *, sealed: bool = False
) -> tuple[heldout._Cohort, Rows]:
    if (
        not isinstance(path, Path)
        or type(digest) is not str
        or type(sealed) is not bool
    ):
        raise ValueError("invalid wait-peer cohort request")
    cohort, rows = transfer_registration._load(path, digest, sealed=sealed)
    if cohort.last - cohort.first + 1 != protocol.COUNT or len(rows) != protocol.COUNT:
        raise ValueError("manifest is not an 8-world wait-peer cohort")
    return cohort, rows


def _generator(work: Path) -> Path:
    git = shutil.which("git", path=os.defpath)
    if git is None:
        raise Unverified("origin_unavailable")
    env = {
        "PATH": os.defpath,
        "HOME": str(work),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    argv = [git, "-C", str(REPOSITORY), "archive", "--format=tar"]
    done = subprocess.run(
        [*argv, heldout.GENERATOR_COMMIT, GENERATOR_TREE],
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=GIT_SECONDS,
        check=False,
    )
    if done.returncode != 0 or not 0 < len(done.stdout) <= MAX_ARCHIVE_BYTES:
        raise Unverified("origin_unavailable")
    with tarfile.open(fileobj=io.BytesIO(done.stdout), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS or any(
            Path(member.name).parts[:1] != (GENERATOR_TREE,) for member in members
        ):
            raise Unverified("origin_mismatch")
        archive.extractall(work / "generator", filter="data")
    return work / "generator" / GENERATOR_TREE / "build_tasks.py"


def _bounded(root: Path) -> None:
    entries = size = 0
    for directory, names, files in os.walk(root):
        for name in (*names, *files):
            info = os.lstat(os.path.join(directory, name))
            entries += 1
            size += info.st_size
            if (
                entries > MAX_TASK_ENTRIES
                or size > MAX_TASK_BYTES
                or stat.S_ISLNK(info.st_mode)
            ):
                raise Unverified("origin_mismatch")


def _regenerated(work: Path, study: heldout._Cohort) -> Path:
    out = work / "tasks"
    argv = [sys.executable, "-IB", str(_generator(work)), "--family", "schedule"]
    argv += ["--instances", f"{study.first}-{study.last}", "--seats", "2"]
    argv += ["--difficulty", "easy", "--seed", str(study.stock_limits[0])]
    argv += ["--conditions", "talk", "--play-timeout", "600", "--out", str(out)]
    done = subprocess.run(
        argv,
        cwd=work,
        env={"PATH": os.defpath, "LC_ALL": "C"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=GENERATOR_SECONDS,
        check=False,
    )
    if done.returncode != 0:
        raise Unverified("origin_unavailable")
    _bounded(out)
    return out / "schedule"


def verify_origin(study: heldout._Cohort, digest: str) -> None:
    expected = packet._check_digest(digest)
    if (
        type(study) is not heldout._Cohort
        or study.last - study.first + 1 != protocol.COUNT
    ):
        raise Unverified("origin_mismatch")
    if expected == PUBLISHED_SHA256:
        if study != load(PUBLISHED_MANIFEST, PUBLISHED_SHA256)[0]:
            raise Unverified("origin_mismatch")
        return
    try:
        with tempfile.TemporaryDirectory(
            prefix="swp-origin-", ignore_cleanup_errors=True
        ) as raw:
            schedule = _regenerated(Path(raw).resolve(strict=True), study)
            frozen = transfer_registration._freeze(
                schedule,
                start=study.first,
                count=protocol.COUNT,
                seed=study.stock_limits[0],
            )
    except Unverified:
        raise
    except (subprocess.SubprocessError, OSError, tarfile.TarError):
        raise Unverified("origin_unavailable") from None
    except (ValueError, RecursionError):
        raise Unverified("origin_mismatch") from None
    if packet._digest(frozen) != expected:
        raise Unverified("origin_mismatch")
