import argparse
import json
import os
import stat
import sys
import types
from collections import Counter
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any, NamedTuple, cast

from ..native_evidence import (
    Incomplete,
    Invalid,
    Record,
    canonical_uuid,
    check_binding,
    native_reward,
    read_bytes,
    require,
    require_usage,
    same,
    seat_map,
    strict_object,
)
from ..native_study import TASK_FILES, registration_request, task_files_digest
from .study import (
    ROLES,
    TASK_DIR,
    load_registration,
    pinned_task,
    spec_for,
    task_dir_for,
    world_env,
)

SCHEMA = "harbor.ship-handover.collection/v1"
INFLIGHT_SCHEMA = "harbor.ship-inflight.collection/v1"
RETURNING_SCHEMA = "harbor.ship-handover.returning/v1"
VERIFIER = ("tests", "verify.py")
VERIFIER_NAMES = ("evaluate", "CutError", "CUT_SCHEMA")
INFLIGHT_DIR = Path(__file__).resolve().parents[1] / "ship_inflight"
INFLIGHT_SOURCE = ("inflight_verify.py",)
INFLIGHT_VERIFIER = ("tests", "inflight_verify.py")
INFLIGHT_NAMES = ("stock", "OBSERVATION_SCHEMA")
SKIP_PROVIDER = "harbor-skip-parsing"
STATUSES = (
    "no_result",
    "partial",
    "ambiguous",
    "duplicate",
    "native_error",
    "invalid",
    "zero",
    "positive",
)
INFLIGHT_STATUSES = (*STATUSES[:4], "unresolved", *STATUSES[4:])
DISPOSITIONS = ("returned", "raised", "cancelled", "stop_requested")
UNKNOWN = "unknown"
CUT_FIELDS = ("run", "prior", "channel", "events", "effects")
SUMMARY_FIELDS = (
    "outcome",
    "outgoing_handoff",
    "relief_action",
    "inbox_before_resolve",
    "reference_before_resolve",
    "effect_change",
)
INFLIGHT_SUMMARY_FIELDS = (
    "outcome",
    "outgoing_handoff",
    "relief_action",
    "replace",
    "settled",
    "drained",
    "grade_before_drain",
)
EFFECTS = ("original", "draft", "fresh")
MAX_WORKSPACES = 64
MAX_TRIAL_ENTRIES = 8192
MAX_CONFIG_ENTRIES = 4096
MAX_PATH_CHARS = 4096
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_VERIFIER_BYTES = 1024 * 1024
MAX_OBSERVATION_BYTES = 16384
MAX_SMALL_BYTES = 4096
MAX_STAMP_BYTES = 128
MAX_TIMESTAMP = 64
MAX_DIAGNOSTIC = 512
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
RESULT = ("result.json",)
CONFIG = ("config.json",)
LOCK = ("lock.json",)
OBSERVATION = ("verifier", "ship-handover.json")
INFLIGHT_OBSERVATION = ("verifier", "ship-inflight.json")
INFLIGHT_CUT = ("verifier", "ship-inflight-cut.json")
REWARD = ("verifier", "reward.json")

Claim = tuple[tuple[int, int], int, bool]
Verdict = tuple[str, int | None, Record | None, Record | None, Record | None]


class NativeError(Exception):
    def __init__(self, status: str, execution: Record, world: Record | None) -> None:
        assert status in ("native_error", "unresolved")
        super().__init__(f"native trial is {status}")
        self.status = status
        self.execution = execution
        self.world = world


class Team(NamedTuple):
    members: list[Record]
    seats: dict[str, Record]


class Origin(NamedTuple):
    task_path: str
    trials: str


class Binding(NamedTuple):
    task_path: str
    trials: str
    env: dict[str, str]


class Space(NamedTuple):
    path: Path
    origin: Origin | None
    configs: frozenset[str]
    stamped: bool


class Trusted(NamedTuple):
    files: str
    verifier: types.ModuleType
    axis: str
    inflight: bool
    strict: bool


def _source(root: Path, parts: tuple[str, ...]) -> bytes:
    try:
        return read_bytes(root, parts, MAX_VERIFIER_BYTES)
    except (Incomplete, Invalid) as error:
        raise ValueError(f"verifier {'/'.join(parts)}: {error}") from None


def load_trusted(registration: Record, task_dir: Path | None) -> Trusted:
    assert TASK_DIR.is_absolute() and INFLIGHT_DIR.is_absolute()
    task = registration["task"]
    spec = spec_for(registration)
    inflight = spec.world == "inflight"
    require_usage(spec.task_name == task["name"], "registration task is not its world")
    supplied = None if task_dir is None else str(task_dir)
    root = task_dir_for(spec, supplied).resolve(strict=True)
    before = task_files_digest(root, task)
    pin = pinned_task(spec)
    require_usage(
        pin is None or (same(pin[0], task) and pin[1] == before),
        "task package is not the pinned materialized task",
    )
    source = _source(TASK_DIR, VERIFIER)
    pinned = [(VERIFIER, source)]
    label = str(TASK_DIR.joinpath(*VERIFIER))
    if inflight:
        source = _source(INFLIGHT_DIR, INFLIGHT_SOURCE)
        pinned.append((INFLIGHT_VERIFIER, source))
        label = str(INFLIGHT_DIR.joinpath(*INFLIGHT_SOURCE))
    require_usage(
        all(_source(root, parts) == data for parts, data in pinned),
        "task verifier differs from the trusted repository verifier",
    )
    require_usage(
        task_files_digest(root, task) == before,
        "task package changed while its verifier was read",
    )
    module = types.ModuleType(
        "ship_inflight_trusted_verify" if inflight else "ship_handover_trusted_verify"
    )
    module.__file__ = label
    exec(compile(source, label, "exec"), module.__dict__)  # noqa: S102 - verified repository source
    names = (*VERIFIER_NAMES, *(INFLIGHT_NAMES if inflight else ()))
    missing = [name for name in names if not hasattr(module, name)]
    require_usage(not missing, f"trusted verifier lacks {', '.join(missing)}")
    require_usage(
        not inflight or Path(module.stock.__file__) == TASK_DIR.joinpath(*VERIFIER),
        "trusted inflight verifier does not reuse the repository stock verifier",
    )
    return Trusted(before, module, spec.axis, inflight, inflight)


def _stamped(workspace: Path, files: str) -> bool:
    try:
        data = read_bytes(workspace, (TASK_FILES,), MAX_STAMP_BYTES)
    except (Incomplete, Invalid):
        return False
    return data == f"{files}\n".encode("ascii")


def _trial(cell: Record, parts: tuple[str, ...]) -> tuple[str, ...]:
    return ("trials", cell["ticket"], *parts)


def _canonical(value: object, label: str) -> str:
    require(
        type(value) is str and 1 < len(value) <= MAX_PATH_CHARS,
        f"{label} is not a bounded path",
    )
    path = cast(str, value)
    require(
        path.isprintable()
        and path.startswith("/")
        and not path.startswith("//")
        and os.path.normpath(path) == path
        and all(part not in ("", ".", "..") for part in path.split("/")[1:]),
        f"{label} is not a canonical absolute path",
    )
    return path


def _config_origin(config: Record, cell: Record, team: Team) -> Origin:
    task = config.get("task")
    require(
        type(task) is dict and set(cast(Record, task)) == {"path"},
        "workspace config task is not one package path",
    )
    task_path = _canonical(cast(Record, task)["path"], "workspace config task path")
    trials = _canonical(config.get("trials_dir"), "workspace config trials_dir")
    require(
        os.path.basename(trials) == "trials",
        "workspace config trials_dir does not end in trials",
    )
    try:
        expected = registration_request(
            Path(task_path), Path(trials), cell["ticket"], team.members, world_env(cell)
        )
    except ValueError:
        raise Invalid("workspace config is not a native trial request") from None
    require(
        same(config, expected),
        "workspace config does not bind the ticket, team and world",
    )
    return Origin(task_path, trials)


def _config_names(workspace: Path, position: int) -> list[str] | None:
    try:
        configs = os.open(workspace / "configs", DIRECTORY)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(
            f"workspace {position} configs is not a plain directory"
        ) from None
    try:
        with os.scandir(configs) as listing:
            names = [entry.name for entry in islice(listing, MAX_CONFIG_ENTRIES + 1)]
    finally:
        os.close(configs)
    require_usage(
        len(names) <= MAX_CONFIG_ENTRIES,
        f"workspace {position} holds over {MAX_CONFIG_ENTRIES} configs",
    )
    return names


def _survey(
    workspace: Path,
    position: int,
    cells: dict[str, Record],
    teams: dict[str, Team],
    files: str,
) -> Space:
    names = _config_names(workspace, position)
    stamped = _stamped(workspace, files)
    if names is None:
        return Space(workspace, None, frozenset(), stamped)
    origins: set[Origin] = set()
    bound: set[str] = set()
    consistent = True
    for name in names:
        ticket = name.removesuffix(".json")
        try:
            require(ticket != name and ticket in cells, "config is not registered")
            data = read_bytes(workspace, ("configs", name), MAX_CONFIG_BYTES)
            cell = cells[ticket]
            config = strict_object(data, "workspace config")
            origins.add(_config_origin(config, cell, teams[cell["team"]]))
            bound.add(ticket)
        except (Incomplete, Invalid):
            consistent = False
    if not consistent or len(origins) != 1:
        return Space(workspace, None, frozenset(), stamped)
    return Space(workspace, origins.pop(), frozenset(bound), stamped)


def _check_config(
    config: object, cell: Record, team: Team, binding: Binding, label: str
) -> None:
    require(
        type(config) is dict
        and cast(Record, config).get("trial_name") == cell["ticket"],
        f"{label} trial_name is not the ticket",
    )
    record = cast(Record, config)
    task = record.get("task")
    require(
        type(task) is dict and cast(Record, task).get("path") == binding.task_path,
        f"{label} task is not the recorded task package",
    )
    require(
        record.get("trials_dir") == binding.trials,
        f"{label} trials_dir is not the recorded trials",
    )
    check_binding(record, binding.env, team.seats, ROLES, label)


def _check_lock(lock: Record, team: Team, task: Record, binding: Binding) -> None:
    version = lock.get("schema_version")
    require(type(version) is int and version == 2, "lock.json is not schema version 2")
    pinned = lock.get("task")
    require(type(pinned) is dict, "lock.json has no task")
    pin = cast(Record, pinned)
    require(pin.get("type") == "local", "lock.json task is not local")
    require(
        pin.get("digest") == task["digest"],
        "lock.json digest differs from the registration",
    )
    require(
        pin.get("path") == binding.task_path
        and pin.get("name") == os.path.basename(binding.task_path),
        "lock.json task is not the recorded task package",
    )
    check_binding(lock, binding.env, team.seats, ROLES, "lock.json")


def _model_bound(recorded: object, model: str, skip: bool) -> bool:
    if type(recorded) is not dict or set(cast(Record, recorded)) != {
        "name",
        "provider",
    }:
        return False
    name, provider = cast(Record, recorded)["name"], cast(Record, recorded)["provider"]
    if type(name) is not str or not (provider is None or type(provider) is str):
        return False
    if skip:
        return name == model and provider == SKIP_PROVIDER
    if provider == SKIP_PROVIDER or provider == "":
        return False
    return name == model or (provider is not None and f"{provider}/{name}" == model)


def _check_runner(info: object, member: Record) -> None:
    require(
        type(info) is dict and cast(Record, info).get("name") == member["name"],
        "native seat runner is not the registered agent",
    )
    recorded = cast(Record, info).get("model_info")
    model = member.get("model_name")
    if model is None:
        require(recorded is None, "native seat runner reports an unregistered model")
        return
    skip = member.get("skip_model_parsing") is True
    require(
        _model_bound(recorded, cast(str, model), skip),
        "native seat runner model is not the registered model",
    )


def _disposition(seat_row: Record, strict: bool) -> str:
    value = seat_row.get("execution_disposition")
    if not strict or value is None:
        return UNKNOWN
    require(value in DISPOSITIONS, "native seat disposition is not known")
    return cast(str, value)


def _seat_errors(result: Record, team: Team, strict: bool) -> Record:
    rows = result.get("seat_results")
    require(
        type(rows) is list and len(cast(list[object], rows)) == len(ROLES),
        "native seat results are not one per role",
    )
    seats: Record = {}
    for row in cast(list[object], rows):
        require(type(row) is dict, "native seat result is not an object")
        seat_row = cast(Record, row)
        seat = seat_row.get("seat")
        require(
            type(seat) is str and seat in team.seats and seat not in seats,
            "native seat result repeats or names an unregistered seat",
        )
        require(seat_row.get("under_test") is True, "native seat is not under test")
        _check_runner(seat_row.get("agent_info"), team.seats[cast(str, seat)])
        require("exception_info" in seat_row, "native seat error record is absent")
        seats[cast(str, seat)] = {
            "disposition": _disposition(seat_row, strict),
            "exception": seat_row["exception_info"] is not None,
        }
    require(set(seats) == set(ROLES), "native seat results do not cover every role")
    return {role: seats[role] for role in ROLES}


def _execution(result: Record, team: Team, strict: bool) -> Record:
    require("exception_info" in result, "native trial error record is absent")
    trial_error = result["exception_info"] is not None
    try:
        seats: Record | None = _seat_errors(result, team, strict)
    except Invalid:
        if not trial_error:
            raise
        seats = None
    return {"trial_error": trial_error, "seats": seats}


def _failed(execution: Record) -> bool:
    if execution["trial_error"]:
        return True
    return any(
        seat["exception"] or seat["disposition"] not in ("returned", UNKNOWN)
        for seat in execution["seats"].values()
    )


def _aware_timestamp(value: object) -> bool:
    if type(value) is not str or len(value) > MAX_TIMESTAMP:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _check_result(
    result: Record,
    cell: Record,
    context: tuple[Team, Record, Binding],
    strict: bool,
) -> tuple[str, Record, str | None]:
    team, task, binding = context
    version = result.get("schema_version")
    require(
        type(version) is int and version == 1, "result.json is not schema version 1"
    )
    native_id = result.get("id")
    require(canonical_uuid(native_id), "result.json id is not a canonical UUID")
    require(
        result.get("trial_name") == cell["ticket"],
        "result.json trial_name is not the ticket",
    )
    require(
        result.get("task_name") == task["name"]
        and result.get("task_checksum") == task["checksum"],
        "result.json task differs from the registration",
    )
    _check_config(result.get("config"), cell, team, binding, "result.json config")
    finished = result.get("finished_at")
    unfinished = finished is None or finished == ""
    require(
        unfinished or _aware_timestamp(finished),
        "result.json finished_at is not an offset-aware ISO timestamp",
    )
    execution = _execution(result, team, strict)
    if _failed(execution):
        return cast(str, native_id), execution, "native_error"
    if unfinished:
        raise Incomplete("native trial has not finished")
    valid = result.get("reward_valid")
    require(type(valid) is bool, "native reward_valid is not a boolean")
    if not valid:
        return cast(str, native_id), execution, "native_error"
    seats = execution["seats"].values()
    if strict and any(seat["disposition"] == UNKNOWN for seat in seats):
        return cast(str, native_id), execution, "unresolved"
    return cast(str, native_id), execution, None


def _replay(verifier: types.ModuleType, workspace: Path, cell: Record) -> Record:
    data = read_bytes(workspace, _trial(cell, OBSERVATION), MAX_OBSERVATION_BYTES)
    published = strict_object(data, "observation")
    cut: Record = {"schema": verifier.CUT_SCHEMA}
    cut.update((key, published.get(key)) for key in CUT_FIELDS)
    try:
        expected = verifier.evaluate(cut)
    except verifier.CutError:
        raise Invalid(
            "observation does not replay under the trusted verifier"
        ) from None
    require(same(expected, published), "published observation differs from the replay")
    return expected


def _regrade(verifier: types.ModuleType, workspace: Path, cell: Record) -> Record:
    cut = read_bytes(workspace, _trial(cell, INFLIGHT_CUT), MAX_SMALL_BYTES)
    parts = _trial(cell, INFLIGHT_OBSERVATION)
    published = strict_object(
        read_bytes(workspace, parts, MAX_OBSERVATION_BYTES), "observation"
    )
    try:
        expected = verifier.evaluate(verifier.stock.parse_cut(cut))
    except verifier.CutError:
        raise Invalid("published cut does not regrade") from None
    require(same(expected, published), "published observation differs from the regrade")
    require(expected["schema"] == verifier.OBSERVATION_SCHEMA, "observation schema")
    return expected


def _observe(trusted: Trusted, workspace: Path, cell: Record) -> Record:
    replay = _regrade if trusted.inflight else _replay
    expected = replay(trusted.verifier, workspace, cell)
    assert type(expected) is dict and expected["reward"] in (0, 1)
    require(
        expected[trusted.axis] == cell[trusted.axis]
        and expected["channel"] == cell["channel"],
        "observation world is not the registered cell",
    )
    require(canonical_uuid(expected["run"], 4), "world run is not a canonical UUID4")
    return expected


def _verified(
    workspace: Path,
    cell: Record,
    team: Team,
    task: Record,
    binding: Binding,
    trusted: Trusted,
) -> Record:
    lock = read_bytes(workspace, _trial(cell, LOCK), MAX_CONFIG_BYTES)
    _check_lock(strict_object(lock, "lock.json"), team, task, binding)
    observation = _observe(trusted, workspace, cell)
    recorded = read_bytes(workspace, _trial(cell, REWARD), MAX_SMALL_BYTES)
    require(
        same(
            strict_object(recorded, "verifier/reward.json"),
            {"reward": observation["reward"]},
        ),
        "verifier/reward.json differs from the observation",
    )
    return observation


def _grade_source(result: Record, observation: Record) -> str:
    if result.get("verifier_result") is None:
        return "world_only"
    require(
        native_reward(result) == observation["reward"],
        "native reward differs from the regrade",
    )
    return "native_agreed" if result.get("reward_valid") is True else "world_only"


def _world_evidence(
    workspace: Path,
    cell: Record,
    context: tuple[Team, Record, Binding, Trusted],
    result: Record,
) -> Record | None:
    team, task, binding, trusted = context
    try:
        observation = _verified(workspace, cell, team, task, binding, trusted)
        require(result["id"] != observation["run"], "native id repeats the world run")
        source = _grade_source(result, observation)
    except (Incomplete, Invalid):
        return None
    if not _stamped(workspace, trusted.files):
        return None
    return {
        "grade": observation["reward"],
        "grade_source": source,
        "observation": _summary(observation, trusted),
    }


def _evaluate(
    space: Space,
    cell: Record,
    team: Team,
    task: Record,
    trusted: Trusted,
) -> tuple[int, Record, Record]:
    require(space.origin is not None, "workspace configs establish no single origin")
    require(cell["ticket"] in space.configs, "trial has no config in its workspace")
    origin = cast(Origin, space.origin)
    binding = Binding(origin.task_path, origin.trials, world_env(cell))
    workspace = space.path
    config = read_bytes(workspace, _trial(cell, CONFIG), MAX_CONFIG_BYTES)
    _check_config(
        strict_object(config, "config.json"), cell, team, binding, "config.json"
    )
    data = read_bytes(workspace, _trial(cell, RESULT), MAX_RESULT_BYTES)
    result = strict_object(data, "result.json")
    native_id, execution, failure = _check_result(
        result, cell, (team, task, binding), trusted.strict
    )
    if failure is not None:
        context = (team, task, binding, trusted)
        world = (
            _world_evidence(workspace, cell, context, result)
            if trusted.strict
            else None
        )
        raise NativeError(failure, execution, world)
    reward = native_reward(result)
    observation = _verified(workspace, cell, team, task, binding, trusted)
    require(
        observation["reward"] == reward, "native reward differs from the observation"
    )
    require(native_id != observation["run"], "native id repeats the world run")
    return reward, observation, execution


def _identifiers(workspace: Path, cell: Record, trusted: Trusted) -> set[str]:
    found: set[str] = set()
    observation = INFLIGHT_OBSERVATION if trusted.inflight else OBSERVATION
    for parts, limit, key in (
        (RESULT, MAX_RESULT_BYTES, "id"),
        (observation, MAX_OBSERVATION_BYTES, "run"),
    ):
        try:
            data = read_bytes(workspace, _trial(cell, parts), limit)
            value = strict_object(data, "/".join(parts)).get(key)
        except (Incomplete, Invalid):
            continue
        if canonical_uuid(value):
            found.add(cast(str, value))
    return found


def _workspaces(paths: list[Path]) -> list[Path]:
    require_usage(
        1 <= len(paths) <= MAX_WORKSPACES, f"give 1 to {MAX_WORKSPACES} workspaces"
    )
    seen: set[tuple[int, int]] = set()
    resolved: list[Path] = []
    for position, path in enumerate(paths, start=1):
        info = os.lstat(path)
        require_usage(
            stat.S_ISDIR(info.st_mode), f"workspace {position} is not a plain directory"
        )
        key = (info.st_dev, info.st_ino)
        require_usage(key not in seen, f"workspace {position} repeats a workspace")
        seen.add(key)
        resolved.append(path.resolve(strict=True))
    assert len(resolved) == len(paths)
    return resolved


def _trial_entries(
    workspace: Path, position: int
) -> list[tuple[str, tuple[int, int], bool]] | None:
    try:
        trials = os.open(workspace / "trials", DIRECTORY)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(
            f"workspace {position} trials is not a plain directory"
        ) from None
    entries: list[tuple[str, tuple[int, int], bool]] = []
    try:
        with os.scandir(trials) as listing:
            for entry in listing:
                require_usage(
                    len(entries) < MAX_TRIAL_ENTRIES,
                    f"workspace {position} holds over {MAX_TRIAL_ENTRIES} trials",
                )
                info = entry.stat(follow_symlinks=False)
                key = (info.st_dev, info.st_ino)
                entries.append((entry.name, key, stat.S_ISDIR(info.st_mode)))
    finally:
        os.close(trials)
    return entries


def _claims(
    workspaces: list[Path], tickets: frozenset[str]
) -> tuple[dict[str, list[Claim]], Record]:
    claims: dict[str, list[Claim]] = {}
    counts = {
        "workspaces": len(workspaces),
        "workspaces_with_trials": 0,
        "claimed_entries": 0,
        "unregistered_entries": 0,
    }
    for index, workspace in enumerate(workspaces):
        entries = _trial_entries(workspace, index + 1)
        if entries is None:
            continue
        counts["workspaces_with_trials"] += 1
        for name, key, plain in entries:
            if name in tickets:
                counts["claimed_entries"] += 1
                claims.setdefault(name, []).append((key, index, plain))
            else:
                counts["unregistered_entries"] += 1
    assert counts["claimed_entries"] == sum(len(found) for found in claims.values())
    return claims, counts


def _spaces(
    workspaces: list[Path],
    cells: dict[str, Record],
    teams: dict[str, Team],
    files: str,
) -> tuple[list[Space], Record]:
    spaces = [
        _survey(workspace, position, cells, teams, files)
        for position, workspace in enumerate(workspaces, start=1)
    ]
    relocated = sum(
        space.origin is not None
        and os.path.dirname(space.origin.trials) != str(space.path)
        for space in spaces
    )
    counts = {
        "relocated_workspaces": relocated,
        "unanchored_workspaces": sum(space.origin is None for space in spaces),
        "unstamped_workspaces": sum(not space.stamped for space in spaces),
    }
    return spaces, counts


def _duplicated(
    claims: dict[str, list[Claim]],
    workspaces: list[Path],
    cells: dict[str, Record],
    trusted: Trusted,
) -> frozenset[str]:
    owners: dict[str, set[tuple[str, tuple[int, int]]]] = {}
    for ticket, found in claims.items():
        for key, index, plain in found:
            identifiers = (
                _identifiers(workspaces[index], cells[ticket], trusted)
                if plain
                else set()
            )
            for identifier in identifiers:
                owners.setdefault(identifier, set()).add((ticket, key))
    return frozenset(
        ticket for copies in owners.values() if len(copies) > 1 for ticket, _ in copies
    )


def _empty(workspace: Path, cell: Record) -> bool:
    try:
        with os.scandir(workspace / "trials" / cell["ticket"]) as entries:
            return next(entries, None) is None
    except OSError:
        return False


def _judge(
    space: Space,
    cell: Record,
    team: Team,
    task: Record,
    trusted: Trusted,
) -> Verdict:
    unknown: Verdict = ("partial", None, None, None, None)
    if _empty(space.path, cell):
        return unknown
    try:
        reward, observation, execution = _evaluate(space, cell, team, task, trusted)
    except Incomplete:
        return unknown
    except NativeError as error:
        return error.status, None, None, error.execution, error.world
    except Invalid:
        return "invalid", None, None, None, None
    if not _stamped(space.path, trusted.files):
        return "invalid", None, None, None, None
    return ("positive" if reward else "zero"), reward, observation, execution, None


def _inflight_summary(observation: Record) -> Record:
    events = {event["kind"]: event for event in observation["events"]}
    inbox, resolve = events.get("inbox"), events.get("resolve")
    before = None
    if resolve is not None:
        before = inbox is not None and inbox["seq"] < resolve["seq"]
    summary = {field: observation[field] for field in INFLIGHT_SUMMARY_FIELDS}
    summary["inherited"] = observation["reward"] == 1 and not observation["settled"]
    summary["inbox_before_resolve"] = before
    served = cast(Record, inbox)["choice"] if before else None
    summary["served_before_resolve"] = served
    return summary


def _summary(observation: Record, trusted: Trusted) -> Record:
    timeline = [event["kind"] for event in observation["events"]]
    if trusted.inflight:
        return {**_inflight_summary(observation), "timeline": timeline}
    effects = Counter(observation["effects"])
    assert set(effects) <= set(EFFECTS)
    summary = {field: observation[field] for field in SUMMARY_FIELDS}
    summary["effects"] = {name: effects[name] for name in EFFECTS}
    summary["timeline"] = timeline
    return summary


def _row(
    cell: Record,
    found: list[Claim],
    duplicated: frozenset[str],
    spaces: list[Space],
    context: tuple[Team, Record, Trusted],
) -> Record:
    trusted = context[2]
    fields = ("ticket", "team", trusted.axis, "channel")
    row: Record = {field: cell[field] for field in fields}
    grade: int | None = None
    observation: Record | None = None
    execution: Record | None = None
    world: Record | None = None
    if not found:
        status = "no_result"
    elif len(found) > 1:
        status = "ambiguous"
    elif cell["ticket"] in duplicated:
        status = "duplicate"
    elif not found[0][2]:
        status = "invalid"
    else:
        team, task, _ = context
        status, grade, observation, execution, world = _judge(
            spaces[found[0][1]], cell, team, task, trusted
        )
    known = INFLIGHT_STATUSES if trusted.strict else STATUSES
    assert status in known
    assert (status in ("zero", "positive")) == (observation is not None)
    assert world is None or status in ("native_error", "unresolved")
    row["status"] = status
    row["grade"] = grade
    row["observation"] = None if observation is None else _summary(observation, trusted)
    if trusted.strict:
        row["grade_source"] = None if grade is None else "native_agreed"
        row["execution"] = execution
        row["world_evidence"] = world
    return row


def _teams(registration: Record) -> dict[str, Team]:
    teams: dict[str, Team] = {}
    for label, members in registration["teams"].items():
        try:
            seats = seat_map(members, f"team {label}", ROLES)
        except Invalid as error:
            raise ValueError(f"registration {error}") from None
        for role in ROLES:
            require_usage(
                type(seats[role].get("name")) is str,
                f"registration team {label} seat {role} is not a named agent",
            )
        teams[label] = Team(members, seats)
    return teams


def _groups(rows: list[Record], axis: str) -> list[Record]:
    fields = ("team", axis, "channel")
    groups: dict[tuple[Any, ...], Record] = {}
    for row in rows:
        key = tuple(row[field] for field in fields)
        group = groups.setdefault(
            key,
            {**dict(zip(fields, key)), "cells": 0, "valid": 0, "positive": 0},
        )
        group["cells"] += 1
        if row["grade"] is not None:
            group["valid"] += 1
            group["positive"] += row["grade"]
    return [groups[key] for key in sorted(groups)]


def collect_registered(
    registration: Record, workspaces: list[Path], trusted: Trusted
) -> Record:
    assert trusted.strict or not trusted.inflight
    task = registration["task"]
    cells = {cell["ticket"]: cell for cell in registration["cells"]}
    assert len(cells) == len(registration["cells"])
    teams = _teams(registration)
    resolved = _workspaces(workspaces)
    claims, directories = _claims(resolved, frozenset(cells))
    spaces, anchors = _spaces(resolved, cells, teams, trusted.files)
    duplicated = _duplicated(claims, resolved, cells, trusted)
    rows = [
        _row(
            cell,
            claims.get(ticket, []),
            duplicated,
            spaces,
            (teams[cell["team"]], task, trusted),
        )
        for ticket, cell in cells.items()
    ]
    assert len(rows) == len(cells)
    statuses = Counter(row["status"] for row in rows)
    schema = SCHEMA if not trusted.strict else RETURNING_SCHEMA
    return {
        "schema": INFLIGHT_SCHEMA if trusted.inflight else schema,
        "task": {key: task[key] for key in ("name", "digest", "checksum")},
        "registered": len(rows),
        "valid": statuses["zero"] + statuses["positive"],
        "statuses": {
            status: statuses[status]
            for status in (INFLIGHT_STATUSES if trusted.strict else STATUSES)
        },
        "directories": {**directories, **anchors},
        "groups": _groups(rows, trusted.axis),
        "cells": rows,
    }


def collect(
    registration_path: Path, workspaces: list[Path], task_dir: Path | None
) -> Record:
    registration = load_registration(registration_path)
    trusted = load_trusted(registration, task_dir)
    return collect_registered(registration, workspaces, trusted)


def collect_returning(
    registration_path: Path, workspaces: list[Path], task_dir: Path | None
) -> Record:
    registration = load_registration(registration_path)
    trusted = load_trusted(registration, task_dir)._replace(strict=True)
    return collect_registered(registration, workspaces, trusted)


def _tsv(rows: list[Record]) -> list[str]:
    assert rows
    header = list(rows[0])
    lines = ["\t".join(header)]
    lines.extend(
        "\t".join("-" if row[key] is None else str(row[key]) for key in header)
        for row in rows
    )
    return lines


def render(report: Record) -> str:
    totals = {
        "registered": report["registered"],
        "valid": report["valid"],
        **report["statuses"],
        **report["directories"],
    }
    sections = [_tsv([totals]), _tsv(report["groups"])]
    return "\n\n".join("\n".join(section) for section in sections)


def _safe(text: str) -> str:
    clean = "".join(char if " " <= char <= "~" else "?" for char in text)
    return clean[:MAX_DIAGNOSTIC]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -BS -m examples.ship_handover.collect"
    )
    parser.add_argument("registration", type=Path)
    parser.add_argument("workspaces", type=Path, nargs="+", metavar="WORKSPACE")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--task", type=Path, metavar="MATERIALIZED")
    args = parser.parse_args(argv)
    try:
        report = collect(args.registration, args.workspaces, args.task)
    except OSError as error:
        print(f"collect: {_safe(error.strerror or 'storage failed')}", file=sys.stderr)
        return 2
    except (ValueError, Invalid) as error:
        print(f"collect: {_safe(str(error))}", file=sys.stderr)
        return 2
    text = (
        json.dumps(report, indent=2, ensure_ascii=True) if args.json else render(report)
    )
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
