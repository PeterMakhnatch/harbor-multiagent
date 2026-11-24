import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import sys
import warnings
from collections.abc import Collection
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlsplit

from ..seat_handoffs.native_records import read_record

SCHEMA = "harbor.shift-board.registration/v1"
TASK_NAME = "harbor/shift-board"
TASK_DIR = Path(__file__).resolve().parents[1] / "tasks" / "shift-board"
SEATS = ("planner", "dispatcher")
FIXTURES = ("planner-fit", "dispatcher-fit")
RULES = ("observe", "guard")
CONTROLS = ("once", "recheck", "override", "read-first")
CUSTOM = "custom"
MAX_SEED = 2147483647
MAX_CELLS = 512
MAX_SHARDS = 64
MAX_MANIFEST_BYTES = 1 << 20
MAX_PARTICIPANTS_BYTES = 64 << 10
MAX_ID_DRAWS = 4 * MAX_CELLS
TICKET = re.compile(r"sb-[0-9a-f]{16}")
PAIR = re.compile(r"pair-[0-9a-f]{16}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
CHECKSUM = re.compile(r"[0-9a-f]{64}")
DECIMAL = re.compile(r"0|[1-9][0-9]{0,9}")
SHARD = re.compile(r"(0|[1-9][0-9]?)/([1-9][0-9]?)")
TASK_ENTRY = re.compile(r"[A-Za-z0-9._-]{1,255}")
TASK_FILES = "task.sha256"
MAX_TASK_ENTRIES = 256
MAX_TASK_DEPTH = 8
MAX_TASK_BYTES = 16 << 20
PLAIN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
IMPORT_PATH = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}:[A-Za-z_][A-Za-z0-9_]*"
)
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")
KEY_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
API_FORMAT = re.compile(r"[a-z][a-z_]{0,63}")
HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?){1,15}"
)
PATTERNS = (
    ("name", AGENT_NAME),
    ("import_path", IMPORT_PATH),
    ("model_name", MODEL_NAME),
    ("model_api_key_name", KEY_NAME),
    ("model_api_format", API_FORMAT),
)
CUSTOM_FIELDS = frozenset(
    {
        "seat",
        "name",
        "import_path",
        "model_name",
        "skip_model_parsing",
        "model_api_key_name",
        "model_api_format",
        "base_url",
    }
)
NATIVE_DEFAULTS: dict[str, Any] = {
    "skills": [],
    "resume_trajectory": False,
    "extra_allowed_hosts": [],
    "kwargs": {},
    "mcp_servers": [],
}
REQUIRED_FIELDS = frozenset({"seat", "skip_model_parsing", *NATIVE_DEFAULTS})
CELL_FIELDS = frozenset({"ticket", "pair", "seed", "fixture", "rule", "team", "repeat"})
MANIFEST_FIELDS = frozenset({"schema", "task", "teams", "cells"})
Block = tuple[str, int, str, int]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _as_record(
    value: object, label: str, fields: frozenset[str] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} is not a JSON object")
    record = cast(dict[str, Any], value)
    if fields is not None and set(record) != fields:
        raise ValueError(f"{label} fields differ")
    return record


def _as_list(value: object, label: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise ValueError(f"{label} is not a list of {low}..{high} items")
    return cast(list[Any], value)


def _as_text(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} is malformed")
    return value


def _as_choice(value: object, label: str, choices: Collection[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{label} is not one of {sorted(choices)}")
    return value


def _as_int(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} is not an integer in {low}..{high}")
    return value


def _public_url(value: object) -> bool:
    if not isinstance(value, str) or not value.isascii() or len(value) > 2048:
        return False
    if any(mark in value for mark in "?#@\\") or any(c.isspace() for c in value):
        return False
    try:
        parts = urlsplit(value)
        host, _ = parts.hostname, parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not host or parts.query or parts.fragment:
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return HOST.fullmatch(host) is not None and not host.endswith(".localhost")


def _expected_env(team: str, seat: str) -> dict[str, str] | None:
    if team == CUSTOM or seat == "planner":
        return None
    return {"BOARD_DISPATCH_POLICY": team}


def _check_participant(value: object, team: str, seat: str) -> None:
    where = f"team {team!r} seat {seat!r}"
    member = _as_record(value, where)
    keys = set(member)
    _require(keys >= REQUIRED_FIELDS, f"{where} lacks normalized native fields")
    _require(
        keys <= CUSTOM_FIELDS | REQUIRED_FIELDS | {"env"},
        f"{where} has fields outside the public allowlist",
    )
    _require(member["seat"] == seat, f"{where} names another seat")
    for field, default in NATIVE_DEFAULTS.items():
        found = member[field]
        _require(
            type(found) is type(default) and found == default, f"{where} sets {field}"
        )
    _require(type(member["skip_model_parsing"]) is bool, f"{where} skip_model_parsing")
    _require(
        ("name" in member) != ("import_path" in member),
        f"{where} needs name xor import_path",
    )
    for field, pattern in PATTERNS:
        if field in member:
            _as_text(member[field], f"{where} {field}", pattern)
    _require(
        "base_url" not in member or _public_url(member["base_url"]),
        f"{where} base_url",
    )
    env = _expected_env(team, seat)
    if env is None:
        _require("env" not in member, f"{where} must not set env")
    else:
        _require(member.get("env") == env, f"{where} env must be exactly {env}")
    if team != CUSTOM:
        scripted = REQUIRED_FIELDS | {"name"} | ({"env"} if env else set())
        _require(keys == scripted, f"{where} scripted control has extra fields")
        _require(member["name"] == "oracle", f"{where} scripted control must be oracle")
        _require(member["skip_model_parsing"] is False, f"{where} skip_model_parsing")


def _check_task(value: object) -> None:
    task = _as_record(value, "task", frozenset({"name", "digest", "checksum"}))
    _require(task["name"] == TASK_NAME, f"task name must be {TASK_NAME}")
    _as_text(task["digest"], "task digest", DIGEST)
    _as_text(task["checksum"], "task checksum", CHECKSUM)


def _check_teams(value: object) -> dict[str, Any]:
    teams = _as_record(value, "teams")
    labels = set(teams)
    _require(
        labels == {CUSTOM} or (0 < len(labels) and labels <= set(CONTROLS)),
        "team labels are not one nonempty kind",
    )
    for label, members in teams.items():
        team = _as_list(members, f"team {label!r}", 2, 2)
        for seat, member in zip(SEATS, team, strict=True):
            _check_participant(member, label, seat)
    return teams


def _check_cell(value: object, teams: dict[str, Any]) -> tuple[str, str, Block, str]:
    cell = _as_record(value, "cell", CELL_FIELDS)
    block = (
        _as_choice(cell["team"], "cell team", teams),
        _as_int(cell["seed"], "cell seed", 0, MAX_SEED),
        _as_choice(cell["fixture"], "cell fixture", FIXTURES),
        _as_int(cell["repeat"], "cell repeat", 1, MAX_CELLS),
    )
    return (
        _as_text(cell["ticket"], "cell ticket", TICKET),
        _as_text(cell["pair"], "cell pair", PAIR),
        block,
        _as_choice(cell["rule"], "cell rule", RULES),
    )


def _check_cells(value: object, teams: dict[str, Any]) -> None:
    cells = _as_list(value, "cells", 1, MAX_CELLS)
    tickets: set[str] = set()
    arms: set[tuple[Block, str]] = set()
    pairs: dict[str, list[tuple[Block, str]]] = {}
    for cell in cells:
        ticket, pair, block, rule = _check_cell(cell, teams)
        _require(ticket not in tickets, "duplicate ticket")
        _require(
            (block, rule) not in arms, "duplicate (team, seed, fixture, repeat, rule)"
        )
        tickets.add(ticket)
        arms.add((block, rule))
        pairs.setdefault(pair, []).append((block, rule))
    blocks: set[Block] = set()
    for pair, members in pairs.items():
        _require(len(members) == 2, f"pair {pair} does not hold exactly two cells")
        _require(
            {rule for _, rule in members} == set(RULES), f"pair {pair} lacks a rule"
        )
        block = members[0][0]
        _require(block == members[1][0], f"pair {pair} differs beyond rule")
        _require(block not in blocks, f"pair {pair} repeats a registered block")
        blocks.add(block)
    seeds = {block[1] for block in blocks}
    repeats = max(block[3] for block in blocks)
    expected = {
        (team, seed, fixture, repeat)
        for team in teams
        for seed in seeds
        for fixture in FIXTURES
        for repeat in range(1, repeats + 1)
    }
    _require(
        blocks == expected,
        "cells are not the complete teams x seeds x fixtures x repeats grid",
    )


def validate_manifest(value: object) -> dict[str, Any]:
    manifest = _as_record(value, "manifest", MANIFEST_FIELDS)
    _require(manifest["schema"] == SCHEMA, f"manifest schema must be {SCHEMA}")
    _check_task(manifest["task"])
    _check_cells(manifest["cells"], _check_teams(manifest["teams"]))
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    record = read_record(path, MAX_MANIFEST_BYTES)
    _require(record is not None, f"manifest {path} does not exist")
    return validate_manifest(record)


def _native() -> SimpleNamespace:
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


def _normalize(native: SimpleNamespace, raw: dict[str, Any]) -> dict[str, Any]:
    participant = native.Participant.model_validate(raw)
    return participant.model_dump(mode="json", exclude_none=True)


def task_identity(native: SimpleNamespace, task_dir: Path) -> dict[str, str]:
    _require(task_dir.is_dir(), f"task directory {task_dir} does not exist")
    task = native.Task(task_dir)
    _require(
        task.name == TASK_NAME, f"task at {task_dir} is {task.name!r}, not {TASK_NAME}"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        checksum = task.checksum
    content_hash, _ = native.Packager.compute_content_hash(task_dir)
    identity = {
        "name": TASK_NAME,
        "digest": "sha256:" + content_hash,
        "checksum": checksum,
    }
    _check_task(identity)
    return identity


def _task_files(task_dir: Path) -> dict[str, Path]:
    assert task_dir.is_absolute()
    pending: list[tuple[str, int]] = [("", 0)]
    files: dict[str, Path] = {}
    entries = 0
    while pending:
        assert len(pending) <= MAX_TASK_ENTRIES + 1
        prefix, depth = pending.pop()
        with os.scandir(task_dir / prefix) as listing:
            for entry in listing:
                entries += 1
                _require(
                    entries <= MAX_TASK_ENTRIES,
                    f"task package has over {MAX_TASK_ENTRIES} entries",
                )
                relative = prefix + _as_text(entry.name, "task entry name", TASK_ENTRY)
                if entry.is_dir(follow_symlinks=False):
                    _require(depth < MAX_TASK_DEPTH, f"task entry {relative} is deep")
                    pending.append((relative + "/", depth + 1))
                    continue
                _require(
                    entry.is_file(follow_symlinks=False),
                    f"task entry {relative} is not a plain file or directory",
                )
                files[relative] = Path(entry.path)
    return files


def _task_file_sha(path: Path, budget: int) -> tuple[str, int]:
    assert budget >= 0
    with open(os.open(path, PLAIN_FILE), "rb") as handle:
        info = os.fstat(handle.fileno())
        _require(stat.S_ISREG(info.st_mode), f"task file {path} is not a regular file")
        data = handle.read(budget + 1)
    _require(len(data) <= budget, f"task files exceed {MAX_TASK_BYTES} bytes")
    return hashlib.sha256(data).hexdigest(), len(data)


def task_files_digest(task_dir: Path, task: dict[str, Any]) -> str:
    _check_task(task)
    header = f"{task['name']}\0{task['digest']}\0{task['checksum']}\n"
    outer = hashlib.sha256(header.encode("ascii"))
    files = _task_files(task_dir)
    _require(len(files) > 0, f"task package {task_dir} holds no files")
    budget = MAX_TASK_BYTES
    for relative in sorted(files):
        digest, size = _task_file_sha(files[relative], budget)
        budget -= size
        outer.update(f"{relative}\0{digest}\n".encode("ascii"))
    assert 0 <= budget <= MAX_TASK_BYTES
    return _as_text("sha256:" + outer.hexdigest(), "task files digest", DIGEST)


def scripted_team(native: SimpleNamespace, policy: str) -> list[dict[str, Any]]:
    raw = [
        {"seat": "planner", "name": "oracle"},
        {
            "seat": "dispatcher",
            "name": "oracle",
            "env": {"BOARD_DISPATCH_POLICY": policy},
        },
    ]
    team = [_normalize(native, member) for member in raw]
    _check_teams({policy: team})
    return team


def custom_team(native: SimpleNamespace, path: Path) -> list[dict[str, Any]]:
    record = read_record(path, MAX_PARTICIPANTS_BYTES)
    if record is None:
        raise ValueError(f"participants file {path} does not exist")
    document = _as_record(record, "participants file", frozenset({"participants"}))
    members = _as_list(document["participants"], "participants", 2, 2)
    by_seat: dict[str, dict[str, Any]] = {}
    for value in members:
        raw = _as_record(value, "participant")
        _require(
            set(raw) <= CUSTOM_FIELDS, "participant has fields outside the allowlist"
        )
        _require(None not in raw.values(), "participant has null fields")
        _require(
            ("name" in raw) != ("import_path" in raw),
            "participant needs name xor import_path",
        )
        seat = _as_choice(raw.get("seat"), "participant seat", SEATS)
        _require(seat not in by_seat, "participant seats repeat")
        by_seat[seat] = raw
    team = [_normalize(native, by_seat[seat]) for seat in SEATS]
    _check_teams({CUSTOM: team})
    return team


def _fresh_ids(prefix: str, count: int) -> list[str]:
    found: dict[str, None] = {}
    for _ in range(MAX_ID_DRAWS):
        if len(found) == count:
            break
        found[prefix + secrets.token_hex(8)] = None
    _require(len(found) == count, "could not draw distinct identifiers")
    return list(found)


def plan_cells(
    teams: list[str], seeds: list[int], repeats: int
) -> list[dict[str, Any]]:
    blocks = [
        (team, seed, fixture, repeat)
        for team in teams
        for seed in seeds
        for fixture in FIXTURES
        for repeat in range(1, repeats + 1)
    ]
    _require(
        1 <= 2 * len(blocks) <= MAX_CELLS, f"design must hold 1..{MAX_CELLS} cells"
    )
    tickets = iter(_fresh_ids("sb-", 2 * len(blocks)))
    cells = [
        {
            "ticket": next(tickets),
            "pair": pair,
            "seed": seed,
            "fixture": fixture,
            "rule": rule,
            "team": team,
            "repeat": repeat,
        }
        for (team, seed, fixture, repeat), pair in zip(
            blocks, _fresh_ids("pair-", len(blocks)), strict=True
        )
        for rule in RULES
    ]
    secrets.SystemRandom().shuffle(cells)
    return cells


def _write_exclusive(path: Path, data: dict[str, Any], mode: int) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with open(fd, "w", encoding="ascii") as handle:
            handle.write(text)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _record_task_files(path: Path, digest: str) -> bool:
    data = (_as_text(digest, "task files digest", DIGEST) + "\n").encode("ascii")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    except FileExistsError:
        with open(os.open(path, PLAIN_FILE), "rb") as handle:
            same = handle.read(len(data) + 1) == data
        _require(same, f"{path} binds another task package")
        return False
    try:
        with open(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True


def register(args: argparse.Namespace) -> None:
    seeds: list[int] = args.seed
    _require(len(set(seeds)) == len(seeds), "duplicate --seed")
    controls: list[str] = args.control or []
    _require(len(set(controls)) == len(controls), "duplicate --control")
    native = _native()
    task_dir = Path(args.task).resolve() if args.task else TASK_DIR
    task = task_identity(native, task_dir)
    if args.participants:
        teams = {CUSTOM: custom_team(native, Path(args.participants))}
    else:
        teams = {
            policy: scripted_team(native, policy) for policy in controls or ["recheck"]
        }
    cells = plan_cells(list(teams), seeds, args.repeats)
    manifest = validate_manifest(
        {"schema": SCHEMA, "task": task, "teams": teams, "cells": cells}
    )
    path = Path(args.manifest)
    _write_exclusive(path, manifest, 0o444)
    summary = {"manifest": str(path), "teams": list(teams), "cells": len(cells)}
    print(json.dumps(summary, sort_keys=True))


def parse_shard(text: str) -> tuple[int, int]:
    match = SHARD.fullmatch(text)
    if match is None:
        raise ValueError("--shard must be I/N")
    index, count = int(match[1]), int(match[2])
    _require(
        1 <= count <= MAX_SHARDS and index < count,
        f"--shard needs 0 <= I < N <= {MAX_SHARDS}",
    )
    return index, count


def trial_request(
    native: SimpleNamespace,
    task_dir: Path,
    trials: Path,
    team: list[dict[str, Any]],
    cell: dict[str, Any],
) -> dict[str, Any]:
    env = {
        "BOARD_SEED": str(cell["seed"]),
        "BOARD_FIXTURE": cell["fixture"],
        "BOARD_RULE": cell["rule"],
        "BOARD_TICKET": cell["ticket"],
    }
    request = {
        "task": {"path": str(task_dir)},
        "trial_name": cell["ticket"],
        "trials_dir": str(trials),
        "environment": {"type": "docker", "delete": True, "env": env},
        "participants": team,
    }
    config = native.TrialConfig.model_validate(request)
    dumped = [p.model_dump(mode="json", exclude_none=True) for p in config.participants]
    _require(dumped == team, f"native config changes team {cell['team']!r}")
    _require(
        config.trial_name == cell["ticket"] and config.trials_dir == trials,
        "trial path",
    )
    _require(config.environment.env == env, "native config changes the world env")
    return request


def emit(args: argparse.Namespace) -> None:
    index, count = parse_shard(args.shard)
    manifest = load_manifest(Path(args.manifest))
    native = _native()
    task_dir = Path(args.task).resolve() if args.task else TASK_DIR
    identity = manifest["task"]
    files_digest = task_files_digest(task_dir, identity)
    _require(
        task_identity(native, task_dir) == identity, "task differs from registration"
    )
    _require(
        task_files_digest(task_dir, identity) == files_digest,
        "task changed while it was checked",
    )
    teams = manifest["teams"]
    for label, members in teams.items():
        normalized = [_normalize(native, member) for member in members]
        _require(normalized == members, f"native normalization changes team {label!r}")
    selected = [
        cell
        for position, cell in enumerate(manifest["cells"])
        if position % count == index
    ]
    _require(len(selected) > 0, f"shard {index}/{count} selects no cells")
    workspace = Path(args.out).resolve()
    configs, trials = workspace / "configs", workspace / "trials"
    _require(
        not configs.is_symlink() and not trials.is_symlink(), "workspace holds symlinks"
    )
    targets = [configs / f"{cell['ticket']}.json" for cell in selected]
    for target, cell in zip(targets, selected, strict=True):
        _require(not os.path.lexists(target), f"{target} already exists")
        _require(
            not os.path.lexists(trials / cell["ticket"]),
            f"trial {cell['ticket']} exists",
        )
    requests = [
        trial_request(native, task_dir, trials, teams[c["team"]], c) for c in selected
    ]
    configs.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    created = False
    try:
        created = _record_task_files(workspace / TASK_FILES, files_digest)
        for target, request in zip(targets, requests, strict=True):
            _write_exclusive(target, request, 0o644)
            written.append(target)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        if created:
            (workspace / TASK_FILES).unlink(missing_ok=True)
        raise
    for target in targets:
        print(target)


def _seed(text: str) -> int:
    if DECIMAL.fullmatch(text) is None or int(text) > MAX_SEED:
        raise argparse.ArgumentTypeError(f"seed must be decimal 0..{MAX_SEED}")
    return int(text)


def _repeats(text: str) -> int:
    if DECIMAL.fullmatch(text) is None or not 1 <= int(text) <= MAX_CELLS // 4:
        raise argparse.ArgumentTypeError(f"repeats must be 1..{MAX_CELLS // 4}")
    return int(text)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m examples.shift_board.plan")
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("register")
    plan.add_argument("manifest")
    plan.add_argument("--seed", action="append", required=True, type=_seed)
    team = plan.add_mutually_exclusive_group()
    team.add_argument("--control", action="extend", nargs="+", choices=CONTROLS)
    team.add_argument("--participants")
    plan.add_argument("--repeats", type=_repeats, default=1)
    plan.add_argument("--task")
    shard = commands.add_parser("emit")
    shard.add_argument("manifest")
    shard.add_argument("--shard", default="0/1")
    shard.add_argument("--out", required=True)
    shard.add_argument("--task")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "register":
            register(args)
        else:
            emit(args)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
