import copy
import json
import os
import re
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

from ...native_study import AGENT_NAME, API_FORMAT
from . import arm_runtime, arm_sources, seat_stream
from .arm_runtime import ENV_NAME, MAX_BYTES, MAX_ENV, Runtime
from .arm_sources import Stamp
from .seat_stream import MAX_SEATS, SEAT

MAX_PATH_BYTES = 4096
MAX_TRIAL_NAME = 255
IMPORT_PATH = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]{0,127}(?:\.[A-Za-z_][A-Za-z0-9_]{0,127}){0,31}"
    r":[A-Za-z_][A-Za-z0-9_]{0,127}"
)
MODEL_NAME = re.compile(r"[!-~]{1,256}")
TOP_FIELDS = frozenset(
    {
        "schema_version",
        "task",
        "trial_name",
        "trials_dir",
        "environment",
        "participants",
    }
)
ENVIRONMENT_FIELDS = frozenset({"type", "delete", "env"})
AGENT_LABELS = ("name", "import_path")
PARTICIPANT_FIELDS = frozenset(
    {
        "seat",
        *AGENT_LABELS,
        "model_name",
        "skip_model_parsing",
        "model_api_format",
        "kwargs",
        "env",
        "skills",
        "resume_trajectory",
        "extra_allowed_hosts",
        "mcp_servers",
    }
)
TRIAL_WIDE_FIELDS = (
    "skills",
    "resume_trajectory",
    "extra_allowed_hosts",
    "mcp_servers",
)
PARTICIPANT_DEFAULTS: dict[str, Any] = {
    "skip_model_parsing": False,
    "skills": [],
    "resume_trajectory": False,
    "extra_allowed_hosts": [],
    "kwargs": {},
    "env": {},
    "mcp_servers": [],
}
ENVIRONMENT_DEFAULTS: dict[str, Any] = {
    "type": "docker",
    "force_build": False,
    "delete": True,
    "stream": False,
    "cpu_enforcement_policy": "auto",
    "memory_enforcement_policy": "auto",
    "extra_docker_compose": [],
    "env": {},
    "kwargs": {},
    "extra_allowed_hosts": [],
}

UNREADABLE = "config is missing or unreadable"
MOVED = "config or task changed while it was inspected"
NOT_JSON = "config is not strict UTF-8 JSON"
NOT_OBJECT = "config is not a JSON object"
UNSUPPORTED = "config sets a field outside the local trial profile"
BAD_VERSION = "config schema_version is not 1"
BAD_BOOKKEEPING = "config trial_name or trials_dir is not bounded text"
BAD_TASK = "config task is not one absolute local task path"
WRONG_TASK = "config task path is not the supplied task spelled without symlinks"
BAD_ENVIRONMENT = "config environment is not a docker world with only delete and env"
BAD_ENV = "config env is not a bounded mapping of names to text"
HIDDEN_ENV = "config env holds a host template or a value native Harbor masks"
BAD_TEAM = "config participants are not a bounded list of distinct seats"
BAD_PARTICIPANT = "config participant sets a field outside the plain profile"
BAD_SEAT = "config participant seat is not a valid seat name"
BAD_AGENT = "config participant does not name exactly one agent label"
TRIAL_WIDE = "config participant sets a non-default trial-wide field"
NATIVE_REFUSED = "config fails native trial-config validation"
NATIVE_CHANGED = "native Harbor records the config differently"
LOCK_REFUSED = "native Harbor cannot lock the config"


def _fail(reason: str) -> NoReturn:
    raise ValueError(reason)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        _fail(reason)


def _native() -> SimpleNamespace:
    from harbor.models.job.lock import build_trial_lock
    from harbor.models.trial.config import TrialConfig
    from harbor.utils.env import is_env_template, is_sensitive_env_key

    return SimpleNamespace(
        build_trial_lock=build_trial_lock,
        TrialConfig=TrialConfig,
        is_env_template=is_env_template,
        is_sensitive_env_key=is_sensitive_env_key,
    )


def _stamp(path: Path) -> Stamp:
    found = os.lstat(path)
    return (
        found.st_dev,
        found.st_ino,
        found.st_mode,
        found.st_size,
        found.st_mtime_ns,
        found.st_ctime_ns,
    )


def _load(path: Path) -> tuple[Stamp, dict[str, Any]]:
    _require(isinstance(path, Path), UNREADABLE)
    try:
        before = _stamp(path)
    except (OSError, ValueError):
        _fail(UNREADABLE)
    try:
        data = seat_stream._read(str(path), MAX_BYTES, "config")
        document = seat_stream._strict_json(data, NOT_JSON)
    except seat_stream.Malformed as error:
        _fail(str(error))
    try:
        after = _stamp(path)
    except (OSError, ValueError):
        _fail(MOVED)
    _require(before == after and len(data) == before[3], MOVED)
    _require(type(document) is dict, NOT_OBJECT)
    try:
        json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, RecursionError):
        _fail(NOT_JSON)
    return before, document


def _text(value: Any, pattern: re.Pattern[str], reason: str) -> str:
    _require(type(value) is str and pattern.fullmatch(value) is not None, reason)
    return value


def _bounded(value: Any, limit: int, reason: str) -> str:
    _require(type(value) is str and len(value.encode("utf-8")) <= limit, reason)
    return value


def _plain(value: Any) -> bytes:
    return arm_runtime._encode(value)


def _env(raw: Any, harbor: SimpleNamespace) -> None:
    _require(type(raw) is dict and len(raw) <= MAX_ENV, BAD_ENV)
    for name, value in raw.items():
        _require(ENV_NAME.fullmatch(name) is not None and type(value) is str, BAD_ENV)
        _require(not harbor.is_env_template(value), HIDDEN_ENV)
        _require(value == "" or not harbor.is_sensitive_env_key(name), HIDDEN_ENV)


def _environment(raw: Any, harbor: SimpleNamespace) -> None:
    _require(type(raw) is dict and set(raw) <= ENVIRONMENT_FIELDS, BAD_ENVIRONMENT)
    if "type" in raw:
        _require(type(raw["type"]) is str and raw["type"] == "docker", BAD_ENVIRONMENT)
    if "delete" in raw:
        _require(type(raw["delete"]) is bool, BAD_ENVIRONMENT)
    if "env" in raw:
        _env(raw["env"], harbor)


def _participant(member: Any, harbor: SimpleNamespace) -> str:
    _require(
        type(member) is dict and set(member) <= PARTICIPANT_FIELDS, BAD_PARTICIPANT
    )
    seat = _text(member.get("seat"), SEAT, BAD_SEAT)
    labels = [label for label in AGENT_LABELS if label in member]
    _require(len(labels) == 1, BAD_AGENT)
    pattern = AGENT_NAME if labels[0] == "name" else IMPORT_PATH
    _text(member[labels[0]], pattern, BAD_AGENT)
    if "model_name" in member:
        _text(member["model_name"], MODEL_NAME, BAD_PARTICIPANT)
    if "model_api_format" in member:
        _text(member["model_api_format"], API_FORMAT, BAD_PARTICIPANT)
    if "skip_model_parsing" in member:
        _require(type(member["skip_model_parsing"]) is bool, BAD_PARTICIPANT)
    if "kwargs" in member:
        _require(type(member["kwargs"]) is dict, BAD_PARTICIPANT)
    if "env" in member:
        _env(member["env"], harbor)
    for field in TRIAL_WIDE_FIELDS:
        if field in member:
            default = PARTICIPANT_DEFAULTS[field]
            _require(_plain(member[field]) == _plain(default), TRIAL_WIDE)
    return seat


def _profile(document: dict[str, Any], harbor: SimpleNamespace) -> str:
    _require(set(document) <= TOP_FIELDS, UNSUPPORTED)
    if "schema_version" in document:
        version = document["schema_version"]
        _require(type(version) is int and version == 1, BAD_VERSION)
    if "trial_name" in document:
        _bounded(document["trial_name"], MAX_TRIAL_NAME, BAD_BOOKKEEPING)
    if "trials_dir" in document:
        _bounded(document["trials_dir"], MAX_PATH_BYTES, BAD_BOOKKEEPING)
    task = document.get("task")
    if type(task) is not dict or set(task) != {"path"}:
        _fail(BAD_TASK)
    text = _bounded(task["path"], MAX_PATH_BYTES, BAD_TASK)
    if "environment" in document:
        _environment(document["environment"], harbor)
    team = document.get("participants")
    if type(team) is not list or not 0 < len(team) <= MAX_SEATS:
        _fail(BAD_TEAM)
    seats = [_participant(member, harbor) for member in team]
    _require(len(set(seats)) == len(seats), BAD_TEAM)
    return text


def _canonical_dir(text: str) -> Path:
    spelled = Path(text)
    try:
        resolved = spelled.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail(WRONG_TASK)
    _require(resolved == spelled, WRONG_TASK)
    return spelled


def _task_dir(task: Path, text: str) -> Path:
    _require("\0" not in text, BAD_TASK)
    spelled = Path(text)
    _require(spelled.is_absolute() and str(spelled) == text, BAD_TASK)
    _require(isinstance(task, Path) and task.is_absolute(), WRONG_TASK)
    _require(str(task) == text, WRONG_TASK)
    return _canonical_dir(text)


def _native_config(harbor: SimpleNamespace, document: dict[str, Any]) -> Any:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            return harbor.TrialConfig.model_validate(copy.deepcopy(document))
    except (ValueError, TypeError, RecursionError, Warning):
        _fail(NATIVE_REFUSED)


def _dump(model: Any) -> dict[str, Any]:
    try:
        dumped = model.model_dump(mode="json", exclude_none=True)
    except (ValueError, TypeError, RecursionError):
        _fail(NATIVE_CHANGED)
    _require(type(dumped) is dict, NATIVE_CHANGED)
    return dumped


def _matches(
    request: dict[str, Any], dumped: dict[str, Any], defaults: dict[str, Any]
) -> bool:
    for key in request.keys() | dumped.keys():
        if (key not in request or key not in dumped) and key not in defaults:
            return False
        want = request.get(key, defaults.get(key))
        got = dumped.get(key, defaults.get(key))
        if _plain(want) != _plain(got):
            return False
    return True


def _faithful(config: Any, document: dict[str, Any]) -> None:
    team = document["participants"]
    _require(len(config.participants) == len(team), NATIVE_CHANGED)
    for member, native in zip(team, config.participants, strict=True):
        _require(_matches(member, _dump(native), PARTICIPANT_DEFAULTS), NATIVE_CHANGED)
    world = document.get("environment", {})
    native_world = _dump(config.environment)
    _require(_matches(world, native_world, ENVIRONMENT_DEFAULTS), NATIVE_CHANGED)
    _require(str(config.task.path) == document["task"]["path"], NATIVE_CHANGED)


def _lock(harbor: SimpleNamespace, config: Any, task_dir: Path) -> dict[str, Any]:
    resolved = SimpleNamespace(
        path=task_dir, content_hash=None, resolved_git_commit_id=None
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = harbor.build_trial_lock(
                trial_config=config, task_download_result=resolved
            )
            record = model.model_dump(mode="json", exclude_none=True)
    except (OSError, ValueError, TypeError, RuntimeError, RecursionError):
        _fail(LOCK_REFUSED)
    _require(type(record) is dict and type(record.get("task")) is dict, LOCK_REFUSED)
    task = record["task"]
    _require(task.get("type") == "local", NATIVE_CHANGED)
    _require(task.get("path") == str(task_dir), NATIVE_CHANGED)
    return record


def _settled(
    config_path: Path,
    stamp: Stamp,
    text: str,
    task_dir: Path,
    tree: dict[str, Stamp],
) -> None:
    arm_sources.require_unchanged(task_dir, tree)
    try:
        still = _stamp(config_path)
    except (OSError, ValueError):
        _fail(MOVED)
    _require(still == stamp, MOVED)
    _require(_canonical_dir(text) == task_dir, MOVED)


def inspect_config(task: Path, config_path: Path) -> Runtime:
    harbor = _native()
    stamp, document = _load(config_path)
    text = _profile(document, harbor)
    spelled = _task_dir(task, text)
    task_dir, tree = arm_sources.bounded_tree(spelled)
    _require(task_dir == spelled, WRONG_TASK)
    config = _native_config(harbor, document)
    _faithful(config, document)
    record = _lock(harbor, config, task_dir)
    _settled(config_path, stamp, text, task_dir, tree)
    return arm_runtime.inspect_record(record)
