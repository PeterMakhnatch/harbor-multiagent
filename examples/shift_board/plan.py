import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from ..native_study import (
    AGENT_NAME,
    API_FORMAT,
    DIGEST,
    KEY_NAME,
    NATIVE_DEFAULTS,
    as_choice,
    as_int,
    as_list,
    as_record,
    as_text,
    check_native_defaults,
    check_task,
    native,
    normalize,
    parse_shard,
    require,
    require_normalized,
    task_identity,
    trial_request,
    write_exclusive,
)
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
MAX_MANIFEST_BYTES = 1 << 20
MAX_PARTICIPANTS_BYTES = 64 << 10
MAX_ID_DRAWS = 4 * MAX_CELLS
TICKET = re.compile(r"sb-[0-9a-f]{16}")
PAIR = re.compile(r"pair-[0-9a-f]{16}")
DECIMAL = re.compile(r"0|[1-9][0-9]{0,9}")
TASK_ENTRY = re.compile(r"[A-Za-z0-9._-]{1,255}")
TASK_FILES = "task.sha256"
MAX_TASK_ENTRIES = 256
MAX_TASK_DEPTH = 8
MAX_TASK_BYTES = 16 << 20
PLAIN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
IMPORT_PATH = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,15}:[A-Za-z_][A-Za-z0-9_]*"
)
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")
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
REQUIRED_FIELDS = frozenset({"seat", "skip_model_parsing", *NATIVE_DEFAULTS})
CELL_FIELDS = frozenset({"ticket", "pair", "seed", "fixture", "rule", "team", "repeat"})
MANIFEST_FIELDS = frozenset({"schema", "task", "teams", "cells"})
Block = tuple[str, int, str, int]


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
    member = as_record(value, where)
    keys = set(member)
    require(keys >= REQUIRED_FIELDS, f"{where} lacks normalized native fields")
    require(
        keys <= CUSTOM_FIELDS | REQUIRED_FIELDS | {"env"},
        f"{where} has fields outside the public allowlist",
    )
    require(member["seat"] == seat, f"{where} names another seat")
    check_native_defaults(member, where)
    require(
        ("name" in member) != ("import_path" in member),
        f"{where} needs name xor import_path",
    )
    for field, pattern in PATTERNS:
        if field in member:
            as_text(member[field], f"{where} {field}", pattern)
    require(
        "base_url" not in member or _public_url(member["base_url"]),
        f"{where} base_url",
    )
    env = _expected_env(team, seat)
    if env is None:
        require("env" not in member, f"{where} must not set env")
    else:
        require(member.get("env") == env, f"{where} env must be exactly {env}")
    if team != CUSTOM:
        scripted = REQUIRED_FIELDS | {"name"} | ({"env"} if env else set())
        require(keys == scripted, f"{where} scripted control has extra fields")
        require(member["name"] == "oracle", f"{where} scripted control must be oracle")
        require(member["skip_model_parsing"] is False, f"{where} skip_model_parsing")


def _check_teams(value: object) -> dict[str, Any]:
    teams = as_record(value, "teams")
    labels = set(teams)
    require(
        labels == {CUSTOM} or (0 < len(labels) and labels <= set(CONTROLS)),
        "team labels are not one nonempty kind",
    )
    for label, members in teams.items():
        team = as_list(members, f"team {label!r}", 2, 2)
        for seat, member in zip(SEATS, team, strict=True):
            _check_participant(member, label, seat)
    return teams


def _check_cell(value: object, teams: dict[str, Any]) -> tuple[str, str, Block, str]:
    cell = as_record(value, "cell", CELL_FIELDS)
    block = (
        as_choice(cell["team"], "cell team", teams),
        as_int(cell["seed"], "cell seed", 0, MAX_SEED),
        as_choice(cell["fixture"], "cell fixture", FIXTURES),
        as_int(cell["repeat"], "cell repeat", 1, MAX_CELLS),
    )
    return (
        as_text(cell["ticket"], "cell ticket", TICKET),
        as_text(cell["pair"], "cell pair", PAIR),
        block,
        as_choice(cell["rule"], "cell rule", RULES),
    )


def _check_cells(value: object, teams: dict[str, Any]) -> None:
    cells = as_list(value, "cells", 1, MAX_CELLS)
    tickets: set[str] = set()
    arms: set[tuple[Block, str]] = set()
    pairs: dict[str, list[tuple[Block, str]]] = {}
    for cell in cells:
        ticket, pair, block, rule = _check_cell(cell, teams)
        require(ticket not in tickets, "duplicate ticket")
        require(
            (block, rule) not in arms, "duplicate (team, seed, fixture, repeat, rule)"
        )
        tickets.add(ticket)
        arms.add((block, rule))
        pairs.setdefault(pair, []).append((block, rule))
    blocks: set[Block] = set()
    for pair, members in pairs.items():
        require(len(members) == 2, f"pair {pair} does not hold exactly two cells")
        require(
            {rule for _, rule in members} == set(RULES), f"pair {pair} lacks a rule"
        )
        block = members[0][0]
        require(block == members[1][0], f"pair {pair} differs beyond rule")
        require(block not in blocks, f"pair {pair} repeats a registered block")
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
    require(
        blocks == expected,
        "cells are not the complete teams x seeds x fixtures x repeats grid",
    )


def validate_manifest(value: object) -> dict[str, Any]:
    manifest = as_record(value, "manifest", MANIFEST_FIELDS)
    require(manifest["schema"] == SCHEMA, f"manifest schema must be {SCHEMA}")
    check_task(manifest["task"], TASK_NAME)
    _check_cells(manifest["cells"], _check_teams(manifest["teams"]))
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    record = read_record(path, MAX_MANIFEST_BYTES)
    require(record is not None, f"manifest {path} does not exist")
    return validate_manifest(record)


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
                require(
                    entries <= MAX_TASK_ENTRIES,
                    f"task package has over {MAX_TASK_ENTRIES} entries",
                )
                relative = prefix + as_text(entry.name, "task entry name", TASK_ENTRY)
                if entry.is_dir(follow_symlinks=False):
                    require(depth < MAX_TASK_DEPTH, f"task entry {relative} is deep")
                    pending.append((relative + "/", depth + 1))
                    continue
                require(
                    entry.is_file(follow_symlinks=False),
                    f"task entry {relative} is not a plain file or directory",
                )
                files[relative] = Path(entry.path)
    return files


def _task_file_sha(path: Path, budget: int) -> tuple[str, int]:
    assert budget >= 0
    with open(os.open(path, PLAIN_FILE), "rb") as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode), f"task file {path} is not a regular file")
        data = handle.read(budget + 1)
    require(len(data) <= budget, f"task files exceed {MAX_TASK_BYTES} bytes")
    return hashlib.sha256(data).hexdigest(), len(data)


def task_files_digest(task_dir: Path, task: dict[str, Any]) -> str:
    check_task(task, TASK_NAME)
    header = f"{task['name']}\0{task['digest']}\0{task['checksum']}\n"
    outer = hashlib.sha256(header.encode("ascii"))
    files = _task_files(task_dir)
    require(len(files) > 0, f"task package {task_dir} holds no files")
    budget = MAX_TASK_BYTES
    for relative in sorted(files):
        digest, size = _task_file_sha(files[relative], budget)
        budget -= size
        outer.update(f"{relative}\0{digest}\n".encode("ascii"))
    assert 0 <= budget <= MAX_TASK_BYTES
    return as_text("sha256:" + outer.hexdigest(), "task files digest", DIGEST)


def scripted_team(harbor: SimpleNamespace, policy: str) -> list[dict[str, Any]]:
    raw = [
        {"seat": "planner", "name": "oracle"},
        {
            "seat": "dispatcher",
            "name": "oracle",
            "env": {"BOARD_DISPATCH_POLICY": policy},
        },
    ]
    team = [normalize(harbor, member) for member in raw]
    _check_teams({policy: team})
    return team


def custom_team(harbor: SimpleNamespace, path: Path) -> list[dict[str, Any]]:
    record = read_record(path, MAX_PARTICIPANTS_BYTES)
    if record is None:
        raise ValueError(f"participants file {path} does not exist")
    document = as_record(record, "participants file", frozenset({"participants"}))
    members = as_list(document["participants"], "participants", 2, 2)
    by_seat: dict[str, dict[str, Any]] = {}
    for value in members:
        raw = as_record(value, "participant")
        require(
            set(raw) <= CUSTOM_FIELDS, "participant has fields outside the allowlist"
        )
        require(None not in raw.values(), "participant has null fields")
        require(
            ("name" in raw) != ("import_path" in raw),
            "participant needs name xor import_path",
        )
        seat = as_choice(raw.get("seat"), "participant seat", SEATS)
        require(seat not in by_seat, "participant seats repeat")
        by_seat[seat] = raw
    team = [normalize(harbor, by_seat[seat]) for seat in SEATS]
    _check_teams({CUSTOM: team})
    return team


def _fresh_ids(prefix: str, count: int) -> list[str]:
    found: dict[str, None] = {}
    for _ in range(MAX_ID_DRAWS):
        if len(found) == count:
            break
        found[prefix + secrets.token_hex(8)] = None
    require(len(found) == count, "could not draw distinct identifiers")
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
    require(1 <= 2 * len(blocks) <= MAX_CELLS, f"design must hold 1..{MAX_CELLS} cells")
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


def _record_task_files(path: Path, digest: str) -> bool:
    data = (as_text(digest, "task files digest", DIGEST) + "\n").encode("ascii")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    except FileExistsError:
        with open(os.open(path, PLAIN_FILE), "rb") as handle:
            same = handle.read(len(data) + 1) == data
        require(same, f"{path} binds another task package")
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
    require(len(set(seeds)) == len(seeds), "duplicate --seed")
    controls: list[str] = args.control or []
    require(len(set(controls)) == len(controls), "duplicate --control")
    harbor = native()
    task_dir = Path(args.task).resolve() if args.task else TASK_DIR
    task = task_identity(harbor, task_dir, TASK_NAME)
    if args.participants:
        teams = {CUSTOM: custom_team(harbor, Path(args.participants))}
    else:
        teams = {
            policy: scripted_team(harbor, policy) for policy in controls or ["recheck"]
        }
    cells = plan_cells(list(teams), seeds, args.repeats)
    manifest = validate_manifest(
        {"schema": SCHEMA, "task": task, "teams": teams, "cells": cells}
    )
    path = Path(args.manifest)
    write_exclusive(path, manifest, 0o444)
    summary = {"manifest": str(path), "teams": list(teams), "cells": len(cells)}
    print(json.dumps(summary, sort_keys=True))


def board_env(cell: dict[str, Any]) -> dict[str, str]:
    return {
        "BOARD_SEED": str(cell["seed"]),
        "BOARD_FIXTURE": cell["fixture"],
        "BOARD_RULE": cell["rule"],
        "BOARD_TICKET": cell["ticket"],
    }


def emit(args: argparse.Namespace) -> None:
    index, count = parse_shard(args.shard)
    manifest = load_manifest(Path(args.manifest))
    harbor = native()
    task_dir = Path(args.task).resolve() if args.task else TASK_DIR
    identity = manifest["task"]
    files_digest = task_files_digest(task_dir, identity)
    require(
        task_identity(harbor, task_dir, TASK_NAME) == identity,
        "task differs from registration",
    )
    require(
        task_files_digest(task_dir, identity) == files_digest,
        "task changed while it was checked",
    )
    teams = manifest["teams"]
    require_normalized(harbor, teams)
    selected = [
        cell
        for position, cell in enumerate(manifest["cells"])
        if position % count == index
    ]
    require(len(selected) > 0, f"shard {index}/{count} selects no cells")
    workspace = Path(args.out).resolve()
    configs, trials = workspace / "configs", workspace / "trials"
    require(
        not configs.is_symlink() and not trials.is_symlink(), "workspace holds symlinks"
    )
    targets = [configs / f"{cell['ticket']}.json" for cell in selected]
    for target, cell in zip(targets, selected, strict=True):
        require(not os.path.lexists(target), f"{target} already exists")
        require(
            not os.path.lexists(trials / cell["ticket"]),
            f"trial {cell['ticket']} exists",
        )
    requests = [
        trial_request(
            harbor, task_dir, trials, c["ticket"], teams[c["team"]], board_env(c)
        )
        for c in selected
    ]
    configs.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    created = False
    try:
        created = _record_task_files(workspace / TASK_FILES, files_digest)
        for target, request in zip(targets, requests, strict=True):
            write_exclusive(target, request, 0o644)
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
