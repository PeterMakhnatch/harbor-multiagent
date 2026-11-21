"""Project the original c1 talk calendars into an anonymous, frozen packet."""

import argparse
import json
import re
import stat
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, cast
from uuid import UUID

Table = tuple[tuple[int, ...], ...]
PACKET = Path(__file__).resolve().parents[1] / "results/flotilla-calendar-packet.json"
PACKET_SHA256 = "b7073bdb5c2bdd44e6e6b2445137b08d3856db6793bdf8d17ba2a0eb7af86de0"

_SCHEMA = "seatlab.calendar-packet/v1"
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
_SEATS = ("p1", "p2")
_N_WORLDS = 12
_PANEL = {
    "c1-ds-2seat": "openrouter/deepseek/deepseek-v4-flash",
    "c1-glm47-2seat": "openrouter/z-ai/glm-4.7-flash",
    "c1-gptoss-2seat": "openrouter/openai/gpt-oss-120b",
    "c1-qwen36-2seat": "openrouter/qwen/qwen3.6-35b-a3b",
}
_SOURCE_FILES = frozenset(
    {
        "environment/Dockerfile",
        "environment/docker-compose.yaml",
        "environment/family.py",
        "environment/player/Dockerfile",
        "environment/score.py",
        "environment/task.json",
        "environment/world.py",
        "instructions/p1.md",
        "instructions/p2.md",
        "solution/family.py",
        "solution/oracle.py",
        "solution/solve.sh",
        "task.toml",
        "tests/test.sh",
    }
)
_SOURCE_DIRS = frozenset(
    {"environment", "environment/player", "instructions", "solution", "tests"}
)
_CACHE_DIR = "environment/__pycache__"
_SHA256 = re.compile(r"[a-f0-9]{64}")
_MAX_BYTES = 1024 * 1024
_MAX_PACKET_BYTES = 8192


@dataclass(frozen=True)
class World:
    index: int
    task_checksum: str
    tables: tuple[Table, Table]


class _Invalid(ValueError):
    """A safe diagnostic that contains no original fields or private paths."""


def _object(value: object, *keys: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or any(not isinstance(key, str) for key in value)
        or (keys and value.keys() != set(keys))
    ):
        raise _Invalid("unexpected object fields")
    return cast(dict[str, Any], value)


def _list(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise _Invalid("expected an array")
    return value


def _check_digest(value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise _Invalid("invalid SHA-256 digest")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _Invalid("duplicate JSON field")
    return result


def _reject_constant(_value: str) -> None:
    raise _Invalid("invalid JSON constant")


def _json(raw: bytes | str) -> Any:
    try:
        return json.loads(
            raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except _Invalid:
        raise
    except (ValueError, RecursionError):
        raise _Invalid("invalid JSON") from None


def _directory(path: Path) -> None:
    try:
        if (
            ".." not in path.parts
            and "\0" not in str(path)
            and stat.S_ISDIR(path.lstat().st_mode)
        ):
            return
    except OSError:
        pass
    raise _Invalid("missing or unsafe directory")


def _files(path: Path) -> list[Path]:
    try:
        return list(path.iterdir())
    except OSError:
        raise _Invalid("cannot list source directory") from None


def _read(path: Path, limit: int = _MAX_BYTES) -> bytes:
    try:
        if (
            ".." in path.parts
            or "\0" in str(path)
            or not stat.S_ISREG(path.lstat().st_mode)
        ):
            raise _Invalid("unsafe source file")
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        raise _Invalid("missing or unreadable source file") from None
    if len(data) > limit:
        raise _Invalid("source file exceeds size limit")
    return data


def _text(path: Path) -> str:
    try:
        return _read(path).decode("utf-8")
    except UnicodeError:
        raise _Invalid("invalid source encoding") from None


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _calendars(value: object) -> dict[str, Any]:
    def valid_name(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    calendars = _object(_object(value, "calendars")["calendars"])
    if len(calendars) != 3 or not all(valid_name(name) for name in calendars):
        raise _Invalid("expected 3 distinct attendees")
    return calendars


def normalize_briefing(value: object) -> Table:
    """Discard attendee names and encode BUSY hours in stable attendee/day order."""
    calendars = _calendars(value)
    table = []
    for name in sorted(calendars):
        week = _object(calendars[name], *_DAYS)
        row = []
        for day in _DAYS:
            mask = 0
            for hour in _list(week[day]):
                if type(hour) is not int or not 9 <= hour <= 16:
                    raise _Invalid("invalid busy hour")
                bit = 1 << (hour - 9)
                if mask & bit:
                    raise _Invalid("duplicate busy hour")
                mask |= bit
            row.append(mask)
        table.append(tuple(row))
    return tuple(table)


def starts(table: Table) -> tuple[int, ...]:
    """Enumerate the legal two-hour starts for one seat."""
    return tuple(
        day * 7 + offset
        for day, (a, b, c) in enumerate(zip(*table, strict=True))
        for offset in range(7)
        if not (a | b | c) & (3 << offset)
    )


def _shared(tables: tuple[Table, Table]) -> int:
    shared = set(starts(tables[0])).intersection(starts(tables[1]))
    if len(shared) != 1:
        raise _Invalid("expected exactly one shared start")
    return shared.pop()


def _table(value: object) -> Table:
    rows = _list(value)
    if len(rows) != 3:
        raise _Invalid("expected 3 calendar rows")
    table = []
    for row in rows:
        cells = _list(row)
        if len(cells) != 5 or any(
            type(mask) is not int or not 0 <= mask <= 255 for mask in cells
        ):
            raise _Invalid("expected 5 one-byte busy masks")
        table.append(tuple(cast("list[int]", cells)))
    return tuple(table)


def _canonical(worlds: tuple[World, ...]) -> bytes:
    value = {
        "schema": _SCHEMA,
        "worlds": [
            {
                "index": world.index,
                "task_checksum": world.task_checksum,
                "tables": world.tables,
            }
            for world in worlds
        ],
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return (encoded + "\n").encode("ascii")


def load(path: Path, expected_sha256: str) -> tuple[World, ...]:
    """Load the canonical numeric projection with its independently pinned digest."""
    expected = _check_digest(expected_sha256)
    raw = _read(path, _MAX_PACKET_BYTES)
    if _digest(raw) != expected:
        raise _Invalid("packet does not match the expected SHA-256")
    packet = _object(_json(raw), "schema", "worlds")
    records = _list(packet["worlds"])
    if packet["schema"] != _SCHEMA or len(records) != _N_WORLDS:
        raise _Invalid("wrong calendar packet schema or world count")
    worlds = []
    for index, record in enumerate(records):
        world = _object(record, "index", "task_checksum", "tables")
        if type(world["index"]) is not int or world["index"] != index:
            raise _Invalid("unexpected world coordinate")
        pair = _list(world["tables"])
        if len(pair) != 2:
            raise _Invalid("expected two seat tables")
        tables = (_table(pair[0]), _table(pair[1]))
        _shared(tables)
        worlds.append(World(index, _check_digest(world["task_checksum"]), tables))
    decoded = tuple(worlds)
    if (
        len({world.task_checksum for world in decoded}) != _N_WORLDS
        or _canonical(decoded) != raw
    ):
        raise _Invalid("repeated checksum or noncanonical packet")
    return decoded


def _compose_map(
    lines: list[str], indentation: int
) -> dict[str, tuple[str, list[str]]]:
    """Read the fixed generated Compose block syntax, not general YAML."""
    result: dict[str, tuple[str, list[str]]] = {}
    children: list[str] | None = None
    for line in lines:
        prefix = len(line) - len(line.lstrip(" "))
        if prefix > indentation and children is not None:
            children.append(line)
            continue
        match = re.fullmatch(r"([\w-]+|<<):(?: (.+))?", line[indentation:], re.ASCII)
        if prefix != indentation or not match or match[1] in result:
            raise _Invalid("unexpected generated Compose mapping")
        children = []
        result[match[1]] = (match[2] or "", children)
    return result


def _compose_block(
    mapping: dict[str, tuple[str, list[str]]], key: str, indentation: int
) -> dict[str, tuple[str, list[str]]]:
    scalar, children = mapping.get(key, ("", []))
    if scalar or not children:
        raise _Invalid("missing generated Compose block")
    return _compose_map(children, indentation)


def _compose_scalar(mapping: dict[str, tuple[str, list[str]]], key: str) -> str:
    scalar, children = mapping.get(key, ("", []))
    if not scalar or children:
        raise _Invalid("missing generated Compose scalar")
    return scalar


def _compose_briefings(text: str, payload: dict[str, Any]) -> tuple[object, object]:
    top = _compose_map([line for line in text.splitlines() if line.strip()], 0)
    if (
        top.keys() != {"x-player", "services", "networks"}
        or top["x-player"][0] != "&player"
    ):
        raise _Invalid("unexpected generated Compose layout")
    services = _compose_block(top, "services", 2)
    if services.keys() != {"main", *_SEATS}:
        raise _Invalid("unexpected generated Compose services")
    main = _compose_block(services, "main", 4)
    env = _compose_block(main, "environment", 6)
    if any(
        _json(_compose_scalar(env, key)) != expected
        for key, expected in (
            ("FLOTILLA_SEATS", "p1,p2"),
            ("FLOTILLA_CONDITION", "talk"),
            ("FLOTILLA_MAX_TEXT", str(payload["max_text"])),
            ("FLOTILLA_MAX_MESSAGES", str(payload["max_messages"])),
        )
    ):
        raise _Invalid("Compose world does not match task payload")
    briefings = []
    for seat in _SEATS:
        service = _compose_block(services, seat, 4)
        if (
            service.keys() != {"<<", "networks", "environment"}
            or _compose_scalar(service, "<<") != "*player"
            or _compose_scalar(service, "networks") != f"[seat-{seat}]"
        ):
            raise _Invalid("unexpected Compose seat service")
        env = _compose_block(service, "environment", 6)
        if (
            env.keys() != {"SEAT", "SEAT_TOKEN", "SEAT_BRIEFING", "WORLD_URL"}
            or _json(_compose_scalar(env, "SEAT")) != seat
        ):
            raise _Invalid("Compose seat identity does not match service")
        encoded = _json(_compose_scalar(env, "SEAT_BRIEFING"))
        if not isinstance(encoded, str):
            raise _Invalid("expected a JSON string for seat briefing")
        briefings.append(_json(encoded))
    return briefings[0], briefings[1]


def _task_name(index: int) -> str:
    if type(index) is not int or not 0 <= index < _N_WORLDS:
        raise _Invalid("invalid calendar world coordinate")
    return f"schedule-{index:03d}-talk"


def source_tables(task: Path, index: int) -> tuple[Table, Table]:
    """Read an original stock task's p1/p2 tables; never return its stored key."""
    return _checked_source_tables(task, index, _task_name(index))


def _checked_source_tables(
    task: Path,
    index: int,
    name: str,
    *,
    stock_limits: tuple[int, int, int, float] | None = None,
) -> tuple[Table, Table]:
    _directory(task)
    environment = task / "environment"
    _directory(environment)
    raw_config = _text(task / "task.toml")
    try:
        config = tomllib.loads(raw_config)
    except (ValueError, RecursionError):
        raise _Invalid("invalid task TOML") from None
    meta = _object(config.get("metadata"))
    if (
        config.get("schema_version") != "1.4"
        or _object(config.get("task")).get("name") != f"flotilla/{name}"
        or config.get("agents") != {"p1": {}, "p2": {}}
        or meta.get("n_seats") != 2
        or meta.get("team_seats") != 2
    ):
        raise _Invalid("unexpected stock calendar task configuration")
    payload = _object(
        _json(_read(environment / "task.json")),
        "answer_key",
        "condition",
        "difficulty",
        "family",
        "instance",
        "max_messages",
        "max_text",
        "seats",
        "seed",
        "variant",
        "world_version",
    )
    markers = {
        "family": "schedule",
        "condition": "talk",
        "difficulty": "easy",
        "variant": "",
    }
    counts = ("seed", "max_text", "max_messages")
    if (
        any(payload[k] != value for k, value in markers.items())
        or payload["seats"] != list(_SEATS)
        or type(payload["instance"]) is not int
        or payload["instance"] != index
        or type(payload["world_version"]) is not int
        or payload["world_version"] != 2
        or any(type(payload[k]) is not int or payload[k] < 0 for k in counts)
        or any(meta.get(k) != payload[k] for k in (*markers, "instance", *counts))
    ):
        raise _Invalid("task payload does not match calendar world metadata")
    if stock_limits is not None and (
        (*(payload[k] for k in counts), meta.get("play_timeout_sec")) != stock_limits
        or _object(config.get("agent")).get("timeout_sec") != stock_limits[3]
    ):
        raise _Invalid("task does not match pinned stock limits")
    p1, p2 = _compose_briefings(_text(environment / "docker-compose.yaml"), payload)
    if _calendars(p1).keys() & _calendars(p2).keys():
        raise _Invalid("seat briefing attendee sets overlap")
    tables = (normalize_briefing(p1), normalize_briefing(p2))
    _check_answer_key(tables, payload["answer_key"])
    return tables


def _check_answer_key(tables: tuple[Table, Table], value: object) -> None:
    key = _object(value, "day", "start")
    key_time = key["start"]
    match = (
        re.fullmatch(r"(09|1[0-5]):00", key_time) if isinstance(key_time, str) else None
    )
    if not match or key["day"] not in _DAYS:
        raise _Invalid("stored key is not a legal start")
    if _shared(tables) != _DAYS.index(key["day"]) * 7 + int(match[1]) - 9:
        raise _Invalid("shared start does not match the stored key")


def _source_hashes(task: Path) -> tuple[str, str, str]:
    """Hash the original regular tree both with and without generated bytecode."""
    files: dict[str, str] = {}

    def walk(directory: Path) -> tuple[str | None, str | None]:
        _directory(directory)
        raw, clean = [], []
        for path in _files(directory):
            relative = path.relative_to(task).as_posix()
            if relative in _SOURCE_DIRS or relative == _CACHE_DIR:
                raw_dir, clean_dir = walk(path)
                if raw_dir is not None:
                    raw.append(f"dirhash:{raw_dir}\0name:{path.name}")
                if clean_dir is not None and relative != _CACHE_DIR:
                    clean.append(f"dirhash:{clean_dir}\0name:{path.name}")
                continue
            if relative not in _SOURCE_FILES and not (
                path.parent.relative_to(task).as_posix() == _CACHE_DIR
                and re.fullmatch(r"[\w.-]+\.pyc", path.name, re.ASCII)
            ):
                raise _Invalid("unexpected file in stock task tree")
            checksum = _digest(_read(path))
            descriptor = f"data:{checksum}\0name:{path.name}"
            raw.append(descriptor)
            if relative in _SOURCE_FILES:
                clean.append(descriptor)
                files[relative] = checksum
        # Harbor's legacy Task.checksum uses this dirhash protocol for regular files.
        return (
            _digest("\0\0".join(sorted(raw)).encode()) if raw else None,
            _digest("\0\0".join(sorted(clean)).encode()) if clean else None,
        )

    raw, clean = walk(task)
    if files.keys() != _SOURCE_FILES or raw is None or clean is None:
        raise _Invalid("stock task source tree is incomplete")
    # TrialLock uses Packager's path/file digest protocol. It omits seat instructions.
    package = "".join(
        f"{name}\0{files[name]}\n"
        for name in sorted(files)
        if not name.startswith("instructions/")
    )
    return raw, clean, _digest(package.encode())


def require_native_task_checksum(task: Path, expected_sha256: str) -> None:
    """Fail before launch unless Harbor's unfiltered checksum matches the packet."""
    expected = _check_digest(expected_sha256)
    raw, clean, _ = _source_hashes(task)
    if raw != expected:
        if clean == expected:
            raise _Invalid("generated bytecode changes the native task checksum")
        raise _Invalid("native task checksum differs from the original result")


def _stamp(value: object, *, aware: bool = True) -> datetime:
    if isinstance(value, str) and "T" in value:
        try:
            stamp = datetime.fromisoformat(value)
            if (stamp.utcoffset() is not None) == aware:
                return stamp
        except ValueError:
            pass
    raise _Invalid("missing or invalid evidence timestamp")


def _record_id(value: object, seen: set[UUID]) -> None:
    if not isinstance(value, str):
        raise _Invalid("missing native record identity")
    try:
        record = UUID(value)
    except ValueError:
        raise _Invalid("invalid native record identity") from None
    if record in seen or record.int == 0:
        raise _Invalid("repeated or invalid native record identity")
    seen.add(record)


def _participants(value: object, model: str) -> None:
    participants = [_object(p) for p in _list(value)]
    if [p.get("seat") for p in participants] != list(_SEATS) or any(
        p.get("name") != "terminus-2" or p.get("model_name") != model
        for p in participants
    ):
        raise _Invalid("original panel participant configuration differs")


def _recorded_path(value: object, name: str) -> str:
    if isinstance(value, str) and "\0" not in value:
        # Read the historical path as data: the archive can move after the job.
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            and ".." not in path.parts
            and path.as_posix() == value
            and path.parts[-5:] == (".flotilla-local", "tasks", "c1", "schedule", name)
        ):
            return value
    raise _Invalid("native record does not name the original c1 talk task path")


def _record(path: Path) -> dict[str, Any]:
    return _object(_json(_read(path)))


def _panel_checksums(
    job: Path, label: str, model: str, seen: set[UUID]
) -> tuple[tuple[str, str], ...]:
    _directory(job)
    config = _record(job / "config.json")
    lock = _record(job / "lock.json")
    result = _record(job / "result.json")
    _record_id(result.get("id"), seen)
    _participants(config.get("participants"), model)
    # This archive records job time in local time and trial time with an offset.
    start = _stamp(result.get("started_at"), aware=False)
    finish = _stamp(result.get("finished_at"), aware=False)
    tasks = [_object(row) for row in _list(config.get("tasks"))]
    locks = [_object(row) for row in _list(lock.get("trials"))]
    stats = _object(result.get("stats"))
    evals = _object(stats.get("evals"))
    if (
        config.get("job_name") != label
        or config.get("install_only") is not False
        or result.get("n_total_trials") != 24
        or stats.get("n_completed_trials") != 24
        or any(
            stats.get(f"n_{status}_trials") != 0
            for status in ("running", "pending", "cancelled")
        )
        or len(locks) != 24
        or len(evals) != 1
        or start > finish
    ):
        raise _Invalid("missing or incomplete original panel job")
    evaluation = _object(next(iter(evals.values())))
    rewards = _object(
        _object(evaluation.get("reward_stats")).get("reward"), "0.0", "1.0"
    )
    exceptions = _object(evaluation.get("exception_stats"))
    entries = _files(job)
    checksums = []
    for index in range(_N_WORLDS):
        name = _task_name(index)
        matches = [path for path in entries if path.name.startswith(f"{name}__")]
        if len(matches) != 1 or not re.fullmatch(
            rf"{name}__[A-Za-z0-9]{{7}}", matches[0].name
        ):
            raise _Invalid(f"w{index:02d}: missing or ambiguous native talk trial")
        trial = matches[0]
        try:
            checksums.append(
                _trial_checksums(
                    trial, name, model, seen, tasks, locks, rewards, exceptions
                )
            )
        except _Invalid as exc:
            raise _Invalid(f"w{index:02d}: {exc}") from None
    return tuple(checksums)


def _trial_checksums(
    trial: Path,
    name: str,
    model: str,
    seen: set[UUID],
    job_tasks: list[dict[str, Any]],
    job_locks: list[dict[str, Any]],
    rewards: dict[str, Any],
    exceptions: dict[str, Any],
) -> tuple[str, str]:
    _directory(trial)
    config = _record(trial / "config.json")
    lock = _record(trial / "lock.json")
    result = _record(trial / "result.json")
    _record_id(result.get("id"), seen)
    result_config = _object(result.get("config"))
    locked_task = _object(lock.get("task"))
    path = _recorded_path(locked_task.get("path"), name)
    if (
        job_locks.count(lock) != 1
        or sum(task.get("path") == path for task in job_tasks) != 1
        or locked_task.get("name") != name
        or locked_task.get("type") != "local"
        or _object(result.get("task_id")).get("path") != path
        or result.get("task_name") != f"flotilla/{name}"
        or result.get("trial_name") != trial.name
        or lock.get("install_only") is not False
    ):
        raise _Invalid("native result and job manifest refer to different trials")
    for cfg in (config, result_config):
        if (
            _object(cfg.get("task")).get("path") != path
            or cfg.get("trial_name") != trial.name
            or cfg.get("install_only") is not False
        ):
            raise _Invalid("native trial configuration does not match its record")
    for cfg in (config, result_config, lock):
        _participants(cfg.get("participants"), model)
    start, finish = _stamp(result.get("started_at")), _stamp(result.get("finished_at"))
    if start > finish:
        raise _Invalid("native talk trial is not completed")
    seats = [_object(row) for row in _list(result.get("seat_results"))]
    if [seat.get("seat") for seat in seats] != list(_SEATS):
        raise _Invalid("native result does not contain both original seats")
    provider, model_name = model.split("/", 1)
    for seat in seats:
        agent = _object(seat.get("agent_info"))
        execution = _object(seat.get("agent_execution"))
        if (
            seat.get("under_test") is not True
            or agent.get("name") != "terminus-2"
            or agent.get("model_info") != {"provider": provider, "name": model_name}
            or not start <= _stamp(execution.get("finished_at")) <= finish
        ):
            raise _Invalid("native seat result does not match the original panel")
    verifier = _object(result.get("verifier_result"))
    reward = _object(verifier.get("rewards"), "reward")["reward"]
    if (
        type(reward) not in (int, float)
        or reward not in (0, 1)
        or any(
            _list(group).count(trial.name) != (group_value == str(float(reward)))
            for group_value, group in rewards.items()
        )
    ):
        raise _Invalid("native talk reward does not match the completed job")
    if "exception_info" not in result:
        raise _Invalid("native outcome is missing its terminal exception field")
    error = result["exception_info"]
    error_type = _object(error).get("exception_type") if error is not None else None
    # An agent error is not missing evidence if the job closed and still recorded the verifier.
    if error is not None and (
        not isinstance(error_type, str) or error_type not in exceptions
    ):
        raise _Invalid("native exception is absent from the completed job")
    if any(
        _list(group).count(trial.name) != (group_type == error_type)
        for group_type, group in exceptions.items()
    ):
        raise _Invalid("native exception status does not match the completed job")
    package = locked_task.get("digest")
    if not isinstance(package, str) or not package.startswith("sha256:"):
        raise _Invalid("missing native task lock digest")
    return _check_digest(result.get("task_checksum")), _check_digest(package[7:])


def _project(schedule_root: Path, jobs_root: Path) -> tuple[bytes, tuple[int, ...]]:
    _directory(schedule_root)
    _directory(jobs_root)
    seen: set[UUID] = set()
    panels = []
    for label, model in _PANEL.items():
        try:
            panels.append(_panel_checksums(jobs_root / label, label, model, seen))
        except _Invalid as exc:
            raise _Invalid(f"{label}: {exc}") from None
    worlds = []
    cache = []
    for index, evidence in enumerate(zip(*panels, strict=True)):
        task = schedule_root / _task_name(index)
        try:
            if len(set(evidence)) != 1:
                raise _Invalid("original panel task checksums disagree")
            recorded, locked = evidence[0]
            raw, clean, package = _source_hashes(task)
            if clean != recorded or package != locked:
                raise _Invalid(
                    "stock task differs from the original recorded checksums"
                )
            if raw != recorded:
                cache.append(index)
            worlds.append(World(index, recorded, source_tables(task, index)))
        except _Invalid as exc:
            raise _Invalid(f"w{index:02d}: {exc}") from None
    if len({world.task_checksum for world in worlds}) != _N_WORLDS:
        raise _Invalid("original panel repeats a task checksum across worlds")
    return _canonical(tuple(worlds)), tuple(cache)


def _compare_tables(schedule_root: Path, worlds: tuple[World, ...]) -> None:
    _directory(schedule_root)
    for world in worlds:
        try:
            tables = source_tables(schedule_root / _task_name(world.index), world.index)
        except _Invalid as exc:
            raise _Invalid(f"w{world.index:02d}: {exc}") from None
        for seat, actual, expected in zip(_SEATS, tables, world.tables, strict=True):
            if actual != expected:
                raise _Invalid(f"w{world.index:02d} {seat}: seat table differs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    export = subcommands.add_parser(
        "export", help="write the frozen numeric projection to stdout"
    )
    export.add_argument("schedule_root", type=Path)
    export.add_argument("jobs_root", type=Path)
    verify = subcommands.add_parser("verify", help="verify the pinned calendar packet")
    verify.add_argument("packet", type=Path, nargs="?", default=PACKET)
    compare = subcommands.add_parser(
        "compare-tables", help="compare source model inputs, not native task identity"
    )
    compare.add_argument("schedule_root", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            raw, cache = _project(args.schedule_root, args.jobs_root)
            if _digest(raw) != PACKET_SHA256:
                raise _Invalid("projection does not match the pinned calendar packet")
            for index in cache:
                print(
                    f"w{index:02d}: generated bytecode changes the native checksum; "
                    "do not launch from this directory",
                    file=sys.stderr,
                )
            sys.stdout.buffer.write(raw)
        elif args.command == "verify":
            load(args.packet, PACKET_SHA256)
            print(PACKET_SHA256)
        else:
            worlds = load(PACKET, PACKET_SHA256)
            _compare_tables(args.schedule_root, worlds)
            print(f"{len(worlds)} worlds / {len(worlds) * len(_SEATS)} roles match.")
            print("This checks model inputs, not native task identity.")
    except _Invalid as exc:
        print(f"calendar_packet: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
