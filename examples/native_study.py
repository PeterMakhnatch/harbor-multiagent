import json
import os
import re
import warnings
from collections.abc import Collection
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

MAX_SHARDS = 64
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
CHECKSUM = re.compile(r"[0-9a-f]{64}")
SHARD = re.compile(r"(0|[1-9][0-9]?)/([1-9][0-9]?)")
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
KEY_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
API_FORMAT = re.compile(r"[a-z][a-z_]{0,63}")
NATIVE_DEFAULTS: dict[str, Any] = {
    "skills": [],
    "resume_trajectory": False,
    "extra_allowed_hosts": [],
    "kwargs": {},
    "mcp_servers": [],
}
TASK_FIELDS = frozenset({"name", "digest", "checksum"})


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def as_record(
    value: object, label: str, fields: frozenset[str] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} is not a JSON object")
    record = cast(dict[str, Any], value)
    if fields is not None and set(record) != fields:
        raise ValueError(f"{label} fields differ")
    return record


def as_list(value: object, label: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise ValueError(f"{label} is not a list of {low}..{high} items")
    return cast(list[Any], value)


def as_text(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} is malformed")
    return value


def as_choice(value: object, label: str, choices: Collection[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{label} is not one of {sorted(choices)}")
    return value


def as_int(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} is not an integer in {low}..{high}")
    return value


def check_native_defaults(member: dict[str, Any], where: str) -> None:
    for field, default in NATIVE_DEFAULTS.items():
        found = member[field]
        require(
            type(found) is type(default) and found == default, f"{where} sets {field}"
        )
    require(type(member["skip_model_parsing"]) is bool, f"{where} skip_model_parsing")


def check_task(value: object, name: str) -> dict[str, Any]:
    task = as_record(value, "task", TASK_FIELDS)
    require(task["name"] == name, f"task name must be {name}")
    as_text(task["digest"], "task digest", DIGEST)
    as_text(task["checksum"], "task checksum", CHECKSUM)
    return task


def parse_shard(text: str) -> tuple[int, int]:
    match = SHARD.fullmatch(text)
    if match is None:
        raise ValueError("--shard must be I/N")
    index, count = int(match[1]), int(match[2])
    require(
        1 <= count <= MAX_SHARDS and index < count,
        f"--shard needs 0 <= I < N <= {MAX_SHARDS}",
    )
    return index, count


def native() -> SimpleNamespace:
    try:
        from harbor.models.task.task import Task
        from harbor.models.trial.config import Participant, TrialConfig
        from harbor.publisher.packager import Packager
    except ImportError as error:
        raise SystemExit(
            f"register and emit need the installed Harbor fork: {error}"
        ) from error
    return SimpleNamespace(
        Task=Task, Participant=Participant, TrialConfig=TrialConfig, Packager=Packager
    )


def normalize(harbor: SimpleNamespace, raw: dict[str, Any]) -> dict[str, Any]:
    participant = harbor.Participant.model_validate(raw)
    return participant.model_dump(mode="json", exclude_none=True)


def require_normalized(harbor: SimpleNamespace, teams: dict[str, Any]) -> None:
    for label, members in teams.items():
        normalized = [normalize(harbor, member) for member in members]
        require(normalized == members, f"native normalization changes team {label!r}")


def task_identity(harbor: SimpleNamespace, task_dir: Path, name: str) -> dict[str, str]:
    require(task_dir.is_dir(), f"task directory {task_dir} does not exist")
    task = harbor.Task(task_dir)
    require(task.name == name, f"task at {task_dir} is {task.name!r}, not {name}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        checksum = task.checksum
    content_hash, _ = harbor.Packager.compute_content_hash(task_dir)
    identity = {"name": name, "digest": "sha256:" + content_hash, "checksum": checksum}
    check_task(identity, name)
    return identity


def trial_request(
    harbor: SimpleNamespace,
    task_dir: Path,
    trials: Path,
    ticket: str,
    team: list[dict[str, Any]],
    env: dict[str, str],
) -> dict[str, Any]:
    request = {
        "task": {"path": str(task_dir)},
        "trial_name": ticket,
        "trials_dir": str(trials),
        "environment": {"type": "docker", "delete": True, "env": env},
        "participants": team,
    }
    config = harbor.TrialConfig.model_validate(request)
    dumped = [p.model_dump(mode="json", exclude_none=True) for p in config.participants]
    require(dumped == team, f"native config changes the team of {ticket}")
    require(config.trial_name == ticket and config.trials_dir == trials, "trial path")
    require(config.environment.env == env, "native config changes the world env")
    return request


def write_exclusive(path: Path, data: dict[str, Any], mode: int) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with open(fd, "w", encoding="ascii") as handle:
            handle.write(text)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
