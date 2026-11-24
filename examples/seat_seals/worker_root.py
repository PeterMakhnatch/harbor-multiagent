import os
import secrets
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ..native_evidence import Invalid, strict_object
from ..native_study import DIGEST, MAX_SHARDS, as_int, as_text, record_bytes, require

ROOT_SCHEMA = "harbor.seat-seal.worker-root/v2"
PRE_RELEASE_SCHEMA = "harbor.seat-seal.worker-root/v1"
ROOT_BINDING = ".seat-seal-workers.json"
BINDING_FIELDS = frozenset({"schema", "plan", "worker"})
PRE_RELEASE_FIELDS = frozenset({"schema", "plan"})
BINDINGS = ("unbound", "same", "other", "unavailable")
ROOT_MODE = 0o600
MAX_BINDING_BYTES = 4096
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
PLAIN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def open_root(stack: ExitStack, trials: Path) -> int:
    require(isinstance(trials, Path), "trials directory is not a Path")
    try:
        info = os.lstat(trials)
    except FileNotFoundError:
        raise ValueError("trials directory does not exist") from None
    except OSError as error:
        raise ValueError(f"trials directory cannot be read: {error.strerror}") from None
    require(not stat.S_ISLNK(info.st_mode), "trials directory is a symlink")
    require(stat.S_ISDIR(info.st_mode), "trials directory is not a directory")
    try:
        root = os.open(trials, DIRECTORY)
    except OSError as error:
        raise ValueError(
            f"trials directory cannot be opened: {error.strerror}"
        ) from None
    stack.callback(os.close, root)
    opened = os.fstat(root)
    require(
        (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino),
        "trials directory changed while it was opened",
    )
    return root


def binding_record(plan_digest: str, worker: int) -> dict[str, Any]:
    as_text(plan_digest, "worker plan digest", DIGEST)
    as_int(worker, "worker", 0, MAX_SHARDS - 1)
    return {"schema": ROOT_SCHEMA, "plan": plan_digest, "worker": worker}


def _read_binding(root: int) -> dict[str, Any] | str:
    try:
        fd = os.open(ROOT_BINDING, PLAIN_FILE, dir_fd=root)
    except FileNotFoundError:
        return "unbound"
    except OSError:
        return "unavailable"
    with open(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return "unavailable"
        data = handle.read(MAX_BINDING_BYTES + 1)
    if len(data) > MAX_BINDING_BYTES:
        return "unavailable"
    try:
        return strict_object(data, ROOT_BINDING)
    except Invalid:
        return "unavailable"


def _well_formed(found: dict[str, Any]) -> bool:
    if found.get("schema") == PRE_RELEASE_SCHEMA:
        fields = PRE_RELEASE_FIELDS
    elif found.get("schema") == ROOT_SCHEMA:
        fields = BINDING_FIELDS
    else:
        return False
    if set(found) != fields:
        return False
    plan = found["plan"]
    if not isinstance(plan, str) or DIGEST.fullmatch(plan) is None:
        return False
    worker = found.get("worker", 0)
    return type(worker) is int and 0 <= worker < MAX_SHARDS


def binding_state(root: int, plan_digest: str, worker: int) -> str:
    expected = binding_record(plan_digest, worker)
    found = _read_binding(root)
    if isinstance(found, str):
        state = found
    elif not _well_formed(found):
        state = "unavailable"
    else:
        state = "same" if found == expected else "other"
    assert state in BINDINGS
    assert state != "same" or found == expected
    return state


def root_binding(trials: Path, plan_digest: str, worker: int) -> str:
    with ExitStack() as stack:
        return binding_state(open_root(stack, trials), plan_digest, worker)


def bind(root: int, plan_digest: str, worker: int) -> None:
    data = record_bytes(binding_record(plan_digest, worker))
    assert len(data) <= MAX_BINDING_BYTES
    pending = f".seat-seal-workers.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(pending, flags, ROOT_MODE, dir_fd=root)
        try:
            with open(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(pending, ROOT_BINDING, src_dir_fd=root, dst_dir_fd=root)
            except FileExistsError:
                pass
        finally:
            os.unlink(pending, dir_fd=root)
    except OSError as error:
        raise ValueError(
            f"trials directory cannot be bound to the worker: {error.strerror}"
        ) from None
    state = binding_state(root, plan_digest, worker)
    require(
        state == "same",
        f"trials directory binding is {state}; no trial was started",
    )
