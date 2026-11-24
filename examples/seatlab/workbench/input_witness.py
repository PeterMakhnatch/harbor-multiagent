import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, NamedTuple

from .seat_stream import MIB, UUID, Record, _constant, _unique

SCHEMA = "seatlab.native-input-start/v1"
SUFFIX = ".start.json"
MAX_ROWS = 8192
MAX_RECEIPT_BYTES = 4096
MAX_TOTAL_BYTES = 16 * MIB
MAX_BINDING_BYTES = 4 * MIB
MAX_TASK_PATH = 4096
HEX = re.compile(r"[0-9a-f]{64}")
PIN = re.compile(r"sha256:[0-9a-f]{64}")
NAME = re.compile(
    rf"({UUID.pattern})\.([0-9a-f]{{64}})\.({UUID.pattern}){re.escape(SUFFIX)}"
)
FIELDS = frozenset({"schema", "job", "slot", "trial", "input", "binding"})
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

NOT_WITNESS = "start witness fields are not canonical keys and pins"
NOT_JOB = "job id is not a canonical lowercase UUID"
NOT_PATH = "start archive is not a filesystem path"
NOT_DIRECTORY = "start archive is not a real non-symlink directory"
NOT_PRIVATE_DIR = "start archive is not a directory owned by the current user with no group or public access"
UNREADABLE = "start archive is unreadable"
TOO_MANY = "start archive holds more than 8192 entries"
UNRELATED = "start archive holds an entry that is not a start receipt"
NOT_PRIVATE = "start receipt is not a private regular file of at most 4 KiB"
TOTAL = "start receipts exceed 16 MiB in total"
MOVED = "start archive changed while it was read"
NOT_RECEIPT = "start receipt body is not the canonical receipt its name declares"
DUPLICATE = "two start receipts name one trial"

Stamp = tuple[int, ...]


class Witness(NamedTuple):
    job: str
    slot: str
    trial: str
    input: str
    binding: str


class _Refused(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Refused(reason)


def _encode(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _canonical(value: Any) -> bytes | None:
    try:
        data = _encode(value)
        if len(data) > MAX_BINDING_BYTES:
            return None
        decoded = json.loads(data)
        stable = decoded == value and _encode(decoded) == data
    except (TypeError, ValueError, RecursionError):
        return None
    return data if stable else None


def _local_task(task_id: Any) -> bool:
    if type(task_id) is not dict or task_id.keys() != {"path"}:
        return False
    path = task_id["path"]
    return type(path) is str and 0 < len(path) <= MAX_TASK_PATH and "\0" not in path


def binding(document: Record) -> str | None:
    if type(document) is not dict:
        return None
    config = document.get("config")
    task_id = document.get("task_id")
    checksum = document.get("task_checksum")
    if not (
        type(config) is dict
        and _local_task(task_id)
        and type(checksum) is str
        and HEX.fullmatch(checksum) is not None
    ):
        return None
    data = _canonical({"config": config, "task_checksum": checksum, "task_id": task_id})
    if data is None:
        return None
    hasher = hashlib.sha256(f"{SCHEMA}\0binding\0".encode("ascii"))
    hasher.update(data)
    return f"sha256:{hasher.hexdigest()}"


def _valid(witness: Any) -> bool:
    return (
        type(witness) is Witness
        and all(type(part) is str for part in witness)
        and UUID.fullmatch(witness.job) is not None
        and HEX.fullmatch(witness.slot) is not None
        and UUID.fullmatch(witness.trial) is not None
        and PIN.fullmatch(witness.input) is not None
        and PIN.fullmatch(witness.binding) is not None
    )


def encode(witness: Witness) -> tuple[str, bytes]:
    if not _valid(witness):
        raise ValueError(NOT_WITNESS)
    name = f"{witness.job}.{witness.slot}.{witness.trial}{SUFFIX}"
    body = _encode({"schema": SCHEMA, **witness._asdict()}) + b"\n"
    assert NAME.fullmatch(name) is not None and len(body) <= MAX_RECEIPT_BYTES
    return name, body


def _stamp(info: os.stat_result) -> Stamp:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _private(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o600
        and 0 < info.st_size <= MAX_RECEIPT_BYTES
    )


def _snapshot(dir_fd: int, name: str) -> tuple[bytes, Stamp]:
    before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    _require(_private(before), NOT_PRIVATE)
    fd = os.open(name, FILE_FLAGS, dir_fd=dir_fd)
    chunks: list[bytes] = []
    size = 0
    try:
        _require(_stamp(os.fstat(fd)) == _stamp(before), MOVED)
        for _ in range(MAX_RECEIPT_BYTES + 2):
            chunk = os.read(fd, MAX_RECEIPT_BYTES + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            _require(size <= MAX_RECEIPT_BYTES, NOT_PRIVATE)
        _require(_stamp(os.fstat(fd)) == _stamp(before), MOVED)
    finally:
        os.close(fd)
    _require(size == before.st_size, MOVED)
    return b"".join(chunks), _stamp(before)


def _decode(data: bytes) -> Witness | None:
    try:
        document = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique,
            parse_constant=_constant,
        )
    except (ValueError, RecursionError):
        return None
    if type(document) is not dict or document.keys() != FIELDS:
        return None
    if document["schema"] != SCHEMA:
        return None
    witness = Witness(
        job=document["job"],
        slot=document["slot"],
        trial=document["trial"],
        input=document["input"],
        binding=document["binding"],
    )
    return witness if _valid(witness) else None


def _listing(dir_fd: int) -> list[str]:
    names: list[str] = []
    with os.scandir(dir_fd) as entries:
        for entry in entries:
            _require(len(names) < MAX_ROWS, TOO_MANY)
            names.append(entry.name)
    return sorted(names)


def _starts(dir_fd: int, job: str) -> dict[str, Witness]:
    opened = _stamp(os.fstat(dir_fd))
    found: dict[str, Witness] = {}
    watched: list[tuple[str, Stamp]] = []
    total = 0
    for name in _listing(dir_fd):
        match = NAME.fullmatch(name)
        _require(match is not None, UNRELATED)
        assert match is not None
        if match.group(1) != job:
            continue
        data, stamp = _snapshot(dir_fd, name)
        total += len(data)
        _require(total <= MAX_TOTAL_BYTES, TOTAL)
        witness = _decode(data)
        _require(
            witness is not None
            and witness.job == job
            and encode(witness) == (name, data),
            NOT_RECEIPT,
        )
        assert witness is not None
        _require(witness.trial not in found, DUPLICATE)
        found[witness.trial] = witness
        watched.append((name, stamp))
    for name, stamp in watched:
        after = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        _require(_stamp(after) == stamp, MOVED)
    _require(_stamp(os.fstat(dir_fd)) == opened, MOVED)
    return found


def _read_starts(directory: Path, job: str) -> dict[str, Witness]:
    try:
        before = os.lstat(directory)
    except FileNotFoundError:
        return {}
    _require(stat.S_ISDIR(before.st_mode), NOT_DIRECTORY)
    _require(
        before.st_uid == os.geteuid() and not before.st_mode & 0o077, NOT_PRIVATE_DIR
    )
    dir_fd = os.open(directory, DIRECTORY_FLAGS)
    try:
        opened = os.fstat(dir_fd)
        _require(_stamp(opened) == _stamp(before), MOVED)
        receipts = _starts(dir_fd, job)
        _require(_stamp(os.lstat(directory)) == _stamp(opened), MOVED)
        return receipts
    finally:
        os.close(dir_fd)


def read_starts(directory: Path, job: str) -> dict[str, Witness]:
    if not isinstance(directory, Path):
        raise TypeError(NOT_PATH)
    if type(job) is not str or UUID.fullmatch(job) is None:
        raise ValueError(NOT_JOB)
    reason = UNREADABLE
    try:
        return _read_starts(directory, job)
    except _Refused as error:
        reason = str(error)
    except (OSError, ValueError, RecursionError):
        reason = UNREADABLE
    raise ValueError(reason)
