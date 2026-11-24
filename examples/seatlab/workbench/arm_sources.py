import hashlib
import os
import stat
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...native_study import MAX_TASK_BYTES, PLAIN_FILE, _task_files

GROUPS = ("config", "environment", "verifier", "solution", "other", "executable_modes")
TREE_GROUPS = {
    "environment": "environment",
    "tests": "verifier",
    "solution": "solution",
}
CONFIG_FILE = "task.toml"
ROOT_CONFIG = frozenset({CONFIG_FILE, ".gitignore"})
NATIVE_IGNORES = frozenset({".gitignore", ".harborignore"})
EXECUTABLE = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
LOCK_FIELDS = frozenset(
    {"name", "version", "type", "digest", "source", "path", "git_url", "git_commit_id"}
)

NATIVE_MISSING = "arm source check needs the installed Harbor fork"
BAD_ROOT = "arm source must be an absolute local task directory"
BAD_TREE = "arm source is not a bounded tree of plain files"
HIDDEN = "arm source holds hidden files"
MOVED = "arm source changed while it was read"
NO_CONFIG = "arm source has no task.toml"
BAD_CONFIG = "arm source task.toml is not a valid native task config"
NO_ROSTER = "arm source declares no [agents] roster"
ALIASED = "arm source declares aliased roles"
MISSING_ROLE = "arm source lacks a declared role file"
NATIVE_REFUSED = "arm source fails native task validation"
DRIFT = "arm source native package disagrees with its local bytes"
BAD_LOCK = "arm lock task is not a native task lock"
NOT_LOCAL = "arm lock task is not a local task"
WRONG_NAME = "arm lock task name does not match the local task"
WRONG_VERSION = "arm lock task version does not match the local task"
WRONG_DIGEST = "arm lock task digest does not match the local task package"

type Stamp = tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class Source:
    seats: tuple[str, ...]
    roles: dict[str, str]
    effective_roles: dict[str, str]
    groups: dict[str, str]
    lock_covers_roles: bool
    native_digest: str
    unpackaged_files: int


def inspect_source(root: Path, task_lock: dict[str, Any]) -> Source:
    harbor = _native()
    task_dir = _task_root(root)
    files, before = _listing(task_dir)
    hashes, config_bytes = _hash_tree(files, before)
    config = _config(harbor, config_bytes)
    roles = _roster(config, files, before)
    effective = _native_roles(harbor, task_dir, roles)
    digest, packaged = _native_package(harbor, task_dir, hashes)
    _bind_lock(harbor, task_lock, task_dir, config, digest)
    require_unchanged(task_dir, before)
    role_paths = frozenset(roles.values())
    others = [rel for rel in hashes if rel not in role_paths]
    executable = [rel for rel in hashes if before[rel][2] & EXECUTABLE]
    return Source(
        seats=tuple(roles),
        roles={seat: hashes[rel] for seat, rel in roles.items()},
        effective_roles=effective,
        groups=_groups(hashes, others, executable),
        lock_covers_roles=role_paths <= packaged,
        native_digest=digest,
        unpackaged_files=sum(rel not in packaged for rel in others),
    )


def bounded_tree(root: Path) -> tuple[Path, dict[str, Stamp]]:
    task_dir = _task_root(root)
    files, stamps = _listing(task_dir)
    if CONFIG_FILE not in files:
        raise ValueError(NO_CONFIG)
    if any(not stat.S_ISREG(stamps[name][2]) for name in files):
        raise ValueError(BAD_TREE)
    if sum(stamps[name][3] for name in files) > MAX_TASK_BYTES:
        raise ValueError(BAD_TREE)
    return task_dir, stamps


def require_unchanged(task_dir: Path, stamps: dict[str, Stamp]) -> None:
    if _listing(task_dir)[1] != stamps:
        raise ValueError(MOVED)


def _native() -> SimpleNamespace:
    try:
        from harbor.models.job.lock import TaskLock
        from harbor.models.task.config import TaskConfig
        from harbor.models.task.task import Task
        from harbor.publisher.packager import Packager
    except ImportError:
        raise ValueError(NATIVE_MISSING) from None
    return SimpleNamespace(
        TaskLock=TaskLock, TaskConfig=TaskConfig, Task=Task, Packager=Packager
    )


def _task_root(root: Path) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise ValueError(BAD_ROOT)
    try:
        info = os.lstat(root)
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(BAD_ROOT) from None
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(BAD_ROOT)
    return resolved


def _stamp(path: Path) -> Stamp:
    try:
        info = os.lstat(path)
    except OSError:
        raise ValueError(MOVED) from None
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _hidden(rel: str) -> bool:
    parts = rel.split("/")
    if any(part.startswith(".") for part in parts[:-1]):
        return True
    return parts[-1].startswith(".") and parts[-1] not in NATIVE_IGNORES


def _listing(task_dir: Path) -> tuple[dict[str, Path], dict[str, Stamp]]:
    try:
        files = _task_files(task_dir)
    except (ValueError, OSError):
        raise ValueError(BAD_TREE) from None
    if not files:
        raise ValueError(NO_CONFIG)
    if any(_hidden(rel) for rel in files):
        raise ValueError(HIDDEN)
    stamps = {rel: _stamp(path) for rel, path in files.items()}
    directories = {""}
    for rel in files:
        parts = rel.split("/")[:-1]
        directories.update("/".join(parts[: depth + 1]) for depth in range(len(parts)))
    for directory in directories:
        stamps[directory + "/"] = _stamp(task_dir / directory)
    return files, stamps


def _read(path: Path, stamp: Stamp, budget: int) -> bytes:
    assert budget >= 0
    try:
        with open(os.open(path, PLAIN_FILE), "rb") as handle:
            info = os.fstat(handle.fileno())
            data = handle.read(budget + 1)
    except OSError:
        raise ValueError(MOVED) from None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(BAD_TREE)
    seen = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)
    if seen != stamp[:5]:
        raise ValueError(MOVED)
    if len(data) > budget:
        raise ValueError(BAD_TREE)
    if len(data) != info.st_size:
        raise ValueError(MOVED)
    return data


def _hash_tree(
    files: dict[str, Path], stamps: dict[str, Stamp]
) -> tuple[dict[str, str], bytes]:
    if CONFIG_FILE not in files:
        raise ValueError(NO_CONFIG)
    hashes: dict[str, str] = {}
    config = b""
    budget = MAX_TASK_BYTES
    for rel in sorted(files):
        data = _read(files[rel], stamps[rel], budget)
        budget -= len(data)
        hashes[rel] = hashlib.sha256(data).hexdigest()
        if rel == CONFIG_FILE:
            config = data
    assert 0 <= budget <= MAX_TASK_BYTES and len(hashes) == len(files)
    return hashes, config


def _config(harbor: SimpleNamespace, data: bytes) -> Any:
    try:
        raw = tomllib.loads(data.decode("utf-8"))
        return harbor.TaskConfig.model_validate(raw)
    except (ValueError, TypeError):
        raise ValueError(BAD_CONFIG) from None


def _roster(
    config: Any, files: dict[str, Path], stamps: dict[str, Stamp]
) -> dict[str, str]:
    seats = tuple(sorted(config.agents))
    if not seats:
        raise ValueError(NO_ROSTER)
    if len({seat.casefold() for seat in seats}) != len(seats):
        raise ValueError(ALIASED)
    roles = {seat: f"instructions/{seat}.md" for seat in seats}
    if any(rel not in files for rel in roles.values()):
        raise ValueError(MISSING_ROLE)
    inodes = {stamps[rel][:2] for rel in roles.values()}
    if len(inodes) != len(roles):
        raise ValueError(ALIASED)
    return roles


def _native_roles(
    harbor: SimpleNamespace, task_dir: Path, roles: dict[str, str]
) -> dict[str, str]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            task = harbor.Task(task_dir)
            texts = {seat: task.seat_instruction(seat) for seat in roles}
            effective = {
                seat: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for seat, text in texts.items()
            }
        except (OSError, ValueError, RuntimeError):
            raise ValueError(NATIVE_REFUSED) from None
    if tuple(sorted(task.config.agents)) != tuple(roles):
        raise ValueError(DRIFT)
    return effective


def _native_package(
    harbor: SimpleNamespace, task_dir: Path, hashes: dict[str, str]
) -> tuple[str, frozenset[str]]:
    try:
        content_hash, packaged = harbor.Packager.compute_content_hash(task_dir)
    except (OSError, ValueError, RuntimeError):
        raise ValueError(NATIVE_REFUSED) from None
    try:
        paths = [path.relative_to(task_dir).as_posix() for path in packaged]
    except ValueError:
        raise ValueError(DRIFT) from None
    if len(set(paths)) != len(paths) or CONFIG_FILE not in paths:
        raise ValueError(DRIFT)
    if any(rel not in hashes for rel in paths):
        raise ValueError(DRIFT)
    outer = hashlib.sha256()
    for rel in paths:
        outer.update(f"{rel}\0{hashes[rel]}\n".encode())
    if outer.hexdigest() != content_hash:
        raise ValueError(DRIFT)
    return "sha256:" + content_hash, frozenset(paths)


def _bind_lock(
    harbor: SimpleNamespace,
    task_lock: dict[str, Any],
    task_dir: Path,
    config: Any,
    digest: str,
) -> None:
    if not isinstance(task_lock, dict) or not set(task_lock) <= LOCK_FIELDS:
        raise ValueError(BAD_LOCK)
    try:
        lock = harbor.TaskLock.model_validate(task_lock)
    except (ValueError, TypeError):
        raise ValueError(BAD_LOCK) from None
    if lock.type != "local" or lock.git_url is not None:
        raise ValueError(NOT_LOCAL)
    if lock.git_commit_id is not None:
        raise ValueError(NOT_LOCAL)
    if lock.name != task_dir.name:
        raise ValueError(WRONG_NAME)
    version = config.task.version if config.task is not None else None
    if lock.version != version:
        raise ValueError(WRONG_VERSION)
    if lock.digest != digest:
        raise ValueError(WRONG_DIGEST)


def _group(rel: str) -> str:
    if rel in ROOT_CONFIG or rel.rsplit("/", 1)[-1] == ".harborignore":
        return "config"
    head, separator, _ = rel.partition("/")
    return TREE_GROUPS.get(head, "other") if separator else "other"


def _groups(
    hashes: dict[str, str], others: list[str], executable: list[str]
) -> dict[str, str]:
    digests = {group: hashlib.sha256() for group in GROUPS}
    for rel in sorted(others):
        digests[_group(rel)].update(f"{rel}\0{hashes[rel]}\n".encode())
    for rel in sorted(executable):
        digests["executable_modes"].update(f"{rel}\n".encode())
    return {group: digest.hexdigest() for group, digest in digests.items()}
