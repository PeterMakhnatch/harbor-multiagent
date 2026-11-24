from typing import Any, NamedTuple, NoReturn

from . import arm_runtime
from .seat_stream import MAX_RESULT_BYTES

_LOCK_SELECTORS = ("git_url", "git_commit_id")
_CONFIG_SELECTORS = ("git_url", "git_commit_id", "name", "ref")

NOT_LOCAL = "lock task is not a local task with an explicit path"
SOLO = "lock has fewer than two participants"


class Cell(NamedTuple):
    key: str
    view: str


def _fail(reason: str) -> NoReturn:
    raise ValueError(reason)


def _local(task: Any, selectors: tuple[str, ...]) -> str | None:
    if type(task) is not dict:
        return None
    path = task.get("path")
    if type(path) is not str or not path:
        return None
    if any(task.get(selector) is not None for selector in selectors):
        return None
    return path


def _view(path: str, participants: list[Any], env: dict[str, str] | None) -> str:
    from harbor.models.trial.config import Participant

    known = frozenset(Participant.model_fields)
    members = [
        {
            name: value
            for name, value in member.items()
            if value is not None or name not in known
        }
        for member in participants
    ]
    return arm_runtime._digest(
        "input-view", {"task": path, "participants": members, "env": env}
    )


def record_key(document: dict[str, Any]) -> str:
    return arm_runtime.inspect_record(document).record_key


def cell(document: dict[str, Any]) -> Cell:
    runtime = arm_runtime.inspect_record(document)
    if len(runtime.order) < 2:
        _fail(SOLO)
    task = document["task"]
    path = _local(task, _LOCK_SELECTORS)
    if path is None or task.get("type") != "local":
        _fail(NOT_LOCAL)
    environment = document["environment"]
    view = _view(path, document["participants"], environment.get("env"))
    return Cell(key=runtime.record_key, view=view)


def _team(config: dict[Any, Any]) -> tuple[list[Any], dict[str, str] | None] | None:
    participants = config.get("participants")
    environment = config.get("environment")
    if type(participants) is not list or len(participants) < 2:
        return None
    if type(environment) is not dict:
        return None
    try:
        arm_runtime._participants(participants)
        arm_runtime._environment(environment)
    except ValueError:
        return None
    return participants, environment.get("env")


def declared_view(document: object) -> str | None:
    if type(document) is not dict:
        return None
    config, task_id = document.get("config"), document.get("task_id")
    if type(config) is not dict or type(task_id) is not dict:
        return None
    path = _local(config.get("task"), _CONFIG_SELECTORS)
    if path is None or task_id.get("path") != path:
        return None
    if any(value is not None for name, value in task_id.items() if name != "path"):
        return None
    name = config.get("trial_name")
    if type(name) is not str or not name:
        return None
    if config.get("source_trial") is not None or config.get("install_only"):
        return None
    team = _team(config)
    if team is None:
        return None
    return _view(path, *team)


def result_view(document: object, *, raw: bytes | None = None) -> str | None:
    from harbor.models.trial.result import TrialResult

    data = arm_runtime._canonical(document) if raw is None else raw
    if data is None or len(data) > MAX_RESULT_BYTES:
        return None
    view = declared_view(document)
    if view is None or arm_runtime._validated(TrialResult, data) is None:
        return None
    return view
