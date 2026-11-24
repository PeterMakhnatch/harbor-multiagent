import hashlib
import json
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from . import seat_stream
from .seat_stream import MAX_SEATS, MIB, SEAT

SCHEMA = "seatlab.workbench.arm-runtime/v1"
SCHEMA_VERSION = 2
MAX_BYTES = 4 * MIB
MAX_ENV = 1024
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
TASK_BOUND = ("path", "name", "digest")
GROUPS = (
    "task",
    "seat_order",
    "agent",
    "skills",
    "instructions",
    "user",
    "environment",
    "verifier",
    "timeouts",
    "harness",
    "other",
)
TOP_GROUPS = {
    "schema_version": "harness",
    "install_only": "harness",
    "artifacts": "harness",
    "override_error_policy": "harness",
    "source_trial": "harness",
    "timeout_multiplier": "timeouts",
    "agent_timeout_multiplier": "timeouts",
    "verifier_timeout_multiplier": "timeouts",
    "agent_setup_timeout_multiplier": "timeouts",
    "environment_build_timeout_multiplier": "timeouts",
    "extra_instructions": "instructions",
    "agent": "agent",
    "skills": "skills",
    "user_agent": "user",
    "user_persona": "user",
    "user_prompt_template": "user",
    "bridge_prompt": "user",
    "bridge_inputs": "user",
    "environment": "environment",
    "extra_docker_compose": "environment",
    "verifier": "verifier",
}
OWN_FIELDS = frozenset({"task", "participants"})
JOB_FIELDS = frozenset({"trials", "n_concurrent_trials", "retry", "created_at"})

UNREADABLE = "lock is missing or unreadable"
NOT_REGULAR = "lock is not a regular non-symlink file"
TOO_LARGE = "lock exceeds 4 MiB"
MOVED = "lock changed while it was read"
NOT_JSON = "lock is not strict UTF-8 JSON"
NOT_OBJECT = "lock is not a JSON object"
JOB_LOCK = "lock is a job lock, not a trial lock"
VERSION = "lock is not a trial-lock v2"
NATIVE = "lock fails native trial-lock validation"
REGRADE = "lock regrades a source trial; no live team runs"
INSTALL_ONLY = "lock is install-only; no live team runs"
NO_VERIFIER = "lock disables the verifier; no live team runs"
NO_TEAM = "lock has no participants"
TOO_MANY = "lock has too many participants"
SEAT_NAME = "lock participant seat is not a valid seat name"
DUPLICATE_SEAT = "lock repeats a participant seat"
UNRESOLVED = "lock participant names no agent"
ENV_SHAPE = "lock environment env is not a bounded mapping of names to text"
TASK_SHAPE = "lock task lacks a text name, type or digest"


@dataclass(frozen=True)
class Runtime:
    task: dict[str, Any]
    seats: tuple[str, ...]
    order: tuple[str, ...]
    participants: dict[str, str]
    env: dict[str, str]
    groups: dict[str, str]
    record_key: str


def _fail(reason: str) -> NoReturn:
    raise ValueError(reason)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        _fail(reason)


def _stamp(path: Path) -> tuple[int, ...]:
    found = os.lstat(path)
    return (
        found.st_dev,
        found.st_ino,
        found.st_mode,
        found.st_size,
        found.st_mtime_ns,
        found.st_ctime_ns,
    )


def _load(path: Path) -> tuple[bytes, dict[str, Any]]:
    reason: str | None = None
    before: tuple[int, ...] = ()
    after: tuple[int, ...] = ()
    data, document = b"", None
    try:
        before = _stamp(path)
        data = seat_stream._read(str(path), MAX_BYTES, "lock")
        after = _stamp(path)
        document = seat_stream._strict_json(data, NOT_JSON)
    except seat_stream.Malformed as error:
        reason = str(error)
    except (OSError, ValueError):
        reason = UNREADABLE
    if reason is not None:
        _fail(reason)
    _require(before == after and len(data) == before[3], MOVED)
    if type(document) is not dict:
        _fail(NOT_OBJECT)
    return data, document


def _validated(model: Any, data: bytes) -> Any:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            return model.model_validate_json(data)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, Warning):
        return None


def _native(data: bytes) -> Any:
    from harbor.models.job.lock import TrialLock

    lock = _validated(TrialLock, data)
    _require(lock is not None, NATIVE)
    return lock


def _encode(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _canonical(document: Any) -> bytes | None:
    try:
        data = _encode(document)
        plain = json.loads(data) == document
    except (TypeError, ValueError, RecursionError):
        return None
    return data if plain else None


def _hash(label: str, data: bytes) -> str:
    hasher = hashlib.sha256(f"{SCHEMA}\0{label}\0".encode("ascii"))
    hasher.update(data)
    return f"sha256:{hasher.hexdigest()}"


def _digest(label: str, value: Any) -> str:
    return _hash(label, _encode(value))


def _participants(raw: Any) -> tuple[tuple[str, ...], dict[str, str]]:
    _require(type(raw) is list and len(raw) > 0, NO_TEAM)
    _require(len(raw) <= MAX_SEATS, TOO_MANY)
    order: list[str] = []
    hashes: dict[str, str] = {}
    for member in raw:
        _require(type(member) is dict, NATIVE)
        seat = member.get("seat")
        _require(type(seat) is str and SEAT.fullmatch(seat) is not None, SEAT_NAME)
        _require(seat not in hashes, DUPLICATE_SEAT)
        agents = (member.get("name"), member.get("import_path"))
        _require(any(type(agent) is str and agent for agent in agents), UNRESOLVED)
        order.append(seat)
        rest = {key: value for key, value in member.items() if key != "seat"}
        hashes[seat] = _digest("participant", rest)
    return tuple(order), hashes


def _environment(raw: Any) -> dict[str, str]:
    _require(type(raw) is dict, NATIVE)
    env = raw.get("env", {})
    _require(type(env) is dict and len(env) <= MAX_ENV, ENV_SHAPE)
    for name, value in env.items():
        _require(ENV_NAME.fullmatch(name) is not None and type(value) is str, ENV_SHAPE)
    return {name: _digest("env", value) for name, value in env.items()}


def _groups(document: dict[str, Any], order: tuple[str, ...]) -> dict[str, str]:
    members: dict[str, dict[str, Any]] = {group: {} for group in GROUPS}
    for key, value in document.items():
        if key not in OWN_FIELDS:
            members[TOP_GROUPS.get(key, "other")][key] = value
    environment = members["environment"].get("environment")
    if type(environment) is dict:
        members["environment"]["environment"] = {
            key: value for key, value in environment.items() if key != "env"
        }
    members["task"] = {
        key: value for key, value in document["task"].items() if key not in TASK_BOUND
    }
    members["seat_order"] = {"participants": list(order)}
    return {group: _digest(f"group:{group}", members[group]) for group in GROUPS}


def inspect_record(document: dict[str, Any]) -> Runtime:
    _require(type(document) is dict, NOT_OBJECT)
    data = _canonical(document)
    if data is None:
        _fail(NOT_JSON)
    _require(len(data) <= MAX_BYTES, TOO_LARGE)
    _require(JOB_FIELDS.isdisjoint(document), JOB_LOCK)
    version = document.get("schema_version")
    _require(type(version) is int and version == SCHEMA_VERSION, VERSION)
    lock = _native(data)
    _require(lock.source_trial is None, REGRADE)
    _require(lock.install_only is False, INSTALL_ONLY)
    _require(lock.verifier.disable is False, NO_VERIFIER)
    order, participants = _participants(document.get("participants"))
    _require(len(lock.participants) == len(order), NATIVE)
    task = {key: value for key, value in document["task"].items() if key != "path"}
    _require(
        all(type(task.get(field)) is str for field in ("name", "type", "digest")),
        TASK_SHAPE,
    )
    return Runtime(
        task=task,
        seats=tuple(sorted(order)),
        order=order,
        participants=participants,
        env=_environment(document.get("environment")),
        groups=_groups(document, order),
        record_key=_hash("record", data),
    )


def inspect_lock(path: Path) -> Runtime:
    _, document = _load(path)
    return inspect_record(document)
