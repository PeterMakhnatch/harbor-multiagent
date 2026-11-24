import argparse
import os
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..native_study import (
    AGENT_NAME,
    API_FORMAT,
    KEY_NAME,
    NATIVE_DEFAULTS,
    TASK_FILES,
    as_choice,
    as_int,
    as_list,
    as_record,
    as_text,
    check_native_defaults,
    check_task,
    native,
    parse_shard,
    record_task_files,
    require,
    require_normalized,
    task_files_digest,
    task_identity,
    trial_request,
    write_exclusive,
)
from ..seat_handoffs.native_records import read_record

SCHEMA = "harbor.joint-reopen.study/v1"
TASK_NAME = "harbor/joint-reopen"
TASK_DIR = Path(__file__).resolve().parents[1] / "tasks" / "joint-reopen"
SEATS = frozenset({"east", "west"})
FIXTURES = ("none", "east", "west", "both")
CHANNELS = ("deliver", "withhold")
CONTROLS = ("keep", "ack-keep", "switch", "recheck")
ORACLE = "oracle"
OWNER_IMPORT_PATH = "examples.joint_reopen.owner_seat:JointOwnerSeat"
MAX_SEED = 2147483647
MAX_CELLS = 2048
MAX_TEAMS = 32
MAX_REGISTRATION_BYTES = 1 << 20
MAX_ENV = 8
TEAM = re.compile(r"[a-z][a-z0-9-]{0,47}")
TICKET = re.compile(r"jr-[0-9a-f]{16}")
MODEL_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._:+-]|/(?!/)){0,255}")
OWNER_MODEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._:+-]|/(?!/)){0,127}")
CREDENTIAL = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH")
REQUIRED_FIELDS = frozenset({"seat", "name", "skip_model_parsing", *NATIVE_DEFAULTS})
MODEL_FIELDS = frozenset({"model_name", "model_api_key_name", "model_api_format"})
OWNER_FIELDS = frozenset(
    {"seat", "import_path", "model_name", "skip_model_parsing", *NATIVE_DEFAULTS}
)
CELL_FIELDS = frozenset({"ticket", "team", "seed", "fixture", "channel"})
REGISTRATION_FIELDS = frozenset({"schema", "task", "teams", "cells"})


def _check_env(value: object, where: str, oracle: bool) -> None:
    env = as_record(value, f"{where} env")
    require(0 < len(env) <= MAX_ENV, f"{where} env must hold 1..{MAX_ENV} entries")
    if oracle:
        require(set(env) == {"JR_POLICY"}, f"{where} oracle env sets only JR_POLICY")
        as_choice(env["JR_POLICY"], f"{where} JR_POLICY", CONTROLS)
        return
    for key, reference in env.items():
        as_text(key, f"{where} env name", KEY_NAME)
        require(CREDENTIAL.search(key) is not None, f"{where} env {key} is no key")
        require(
            not key.startswith("JR_") and reference == "${" + key + "}",
            f"{where} env {key} must be the bare reference ${{{key}}}",
        )


def check_owner_model(value: object, where: str) -> str:
    return as_text(value, f"{where} owner model_name", OWNER_MODEL)


def _check_owner(member: dict[str, Any], where: str) -> None:
    require("name" not in member, f"{where} sets both name and import_path")
    require(set(member) == OWNER_FIELDS, f"{where} owner seat fields differ")
    check_native_defaults(member, where)
    require(
        type(member["import_path"]) is str
        and member["import_path"] == OWNER_IMPORT_PATH,
        f"{where} import_path is not the joint owner seat",
    )
    require(member["skip_model_parsing"] is True, f"{where} owner skip_model_parsing")
    check_owner_model(member["model_name"], where)


def _check_participant(value: object, where: str) -> str:
    member = as_record(value, where)
    if "import_path" in member:
        _check_owner(member, where)
        return as_choice(member["seat"], f"{where} seat", SEATS)
    keys = set(member)
    require(keys >= REQUIRED_FIELDS, f"{where} lacks normalized native fields")
    require(
        keys <= REQUIRED_FIELDS | MODEL_FIELDS | {"env"},
        f"{where} has fields outside the v1 allowlist",
    )
    check_native_defaults(member, where)
    oracle = as_text(member["name"], f"{where} name", AGENT_NAME) == ORACLE
    if oracle:
        require(not keys & MODEL_FIELDS, f"{where} oracle names a model")
        require(member["skip_model_parsing"] is False, f"{where} skip_model_parsing")
    else:
        as_text(member.get("model_name"), f"{where} model_name", MODEL_NAME)
        if "model_api_key_name" in member:
            as_text(member["model_api_key_name"], f"{where} key name", KEY_NAME)
        if "model_api_format" in member:
            as_text(member["model_api_format"], f"{where} api format", API_FORMAT)
    if "env" in member:
        _check_env(member["env"], where, oracle)
    return as_choice(member["seat"], f"{where} seat", SEATS)


def _check_teams(value: object) -> dict[str, Any]:
    teams = as_record(value, "teams")
    require(0 < len(teams) <= MAX_TEAMS, f"registration needs 1..{MAX_TEAMS} teams")
    for label, members in teams.items():
        as_text(label, "team name", TEAM)
        team = as_list(members, f"team {label!r}", 2, 2)
        seats = {
            _check_participant(member, f"team {label!r} member {position}")
            for position, member in enumerate(team)
        }
        require(seats == SEATS, f"team {label!r} needs one east and one west seat")
    return teams


def _check_cells(value: object, teams: dict[str, Any]) -> None:
    tickets: set[str] = set()
    for position, item in enumerate(as_list(value, "cells", 1, MAX_CELLS)):
        where = f"cell {position}"
        cell = as_record(item, where, CELL_FIELDS)
        ticket = as_text(cell["ticket"], f"{where} ticket", TICKET)
        require(ticket not in tickets, f"{where} repeats ticket {ticket}")
        tickets.add(ticket)
        as_choice(cell["team"], f"{where} team", teams)
        as_int(cell["seed"], f"{where} seed", 0, MAX_SEED)
        as_choice(cell["fixture"], f"{where} fixture", FIXTURES)
        as_choice(cell["channel"], f"{where} channel", CHANNELS)


def validate_registration(value: object) -> dict[str, Any]:
    registration = as_record(value, "registration", REGISTRATION_FIELDS)
    require(registration["schema"] == SCHEMA, f"registration schema must be {SCHEMA}")
    check_task(registration["task"], TASK_NAME)
    _check_cells(registration["cells"], _check_teams(registration["teams"]))
    return registration


def load_registration(path: Path) -> dict[str, Any]:
    record = read_record(path, MAX_REGISTRATION_BYTES)
    require(record is not None, f"registration {path} does not exist")
    return validate_registration(record)


def world_env(cell: dict[str, Any]) -> dict[str, str]:
    return {
        "JR_SEED": str(cell["seed"]),
        "JR_FIXTURE": cell["fixture"],
        "JR_CHANNEL": cell["channel"],
    }


def fresh_workspace(text: str) -> Path:
    out = Path(text).absolute()
    require(out.name not in ("", ".", ".."), f"--out {text} names no new directory")
    workspace = out.parent.resolve(strict=True) / out.name
    require(not os.path.lexists(workspace), f"workspace {workspace} already exists")
    return workspace


def emit(args: argparse.Namespace) -> list[Path]:
    index, count = parse_shard(args.shard)
    registration = load_registration(Path(args.registration))
    selected = [c for i, c in enumerate(registration["cells"]) if i % count == index]
    require(len(selected) > 0, f"shard {index}/{count} selects no cells")
    workspace = fresh_workspace(args.out)
    harbor = native()
    task_dir = Path(args.task).resolve() if args.task else TASK_DIR
    identity = registration["task"]
    files_digest = task_files_digest(task_dir, identity)
    found = task_identity(harbor, task_dir, TASK_NAME)
    require(found == identity, f"task {task_dir} is unregistered: {found}")
    require(
        task_files_digest(task_dir, identity) == files_digest,
        "task changed while it was checked",
    )
    teams = registration["teams"]
    require_normalized(harbor, teams)
    configs, trials = workspace / "configs", workspace / "trials"
    requests = [
        trial_request(
            harbor, task_dir, trials, c["ticket"], teams[c["team"]], world_env(c)
        )
        for c in selected
    ]
    workspace.mkdir()
    written: list[Path] = []
    created = False
    try:
        created = record_task_files(workspace / TASK_FILES, files_digest)
        require(created, f"another writer bound {workspace} first")
        configs.mkdir()
        for cell, request in zip(selected, requests, strict=True):
            target = configs / f"{cell['ticket']}.json"
            write_exclusive(target, request, 0o644)
            written.append(target)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        if created:
            (workspace / TASK_FILES).unlink(missing_ok=True)
        with suppress(OSError):
            configs.rmdir()
        with suppress(OSError):
            workspace.rmdir()
        raise
    return written


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m examples.joint_reopen.study")
    commands = root.add_subparsers(dest="command", required=True)
    shard = commands.add_parser("emit")
    shard.add_argument("registration")
    shard.add_argument("--out", required=True)
    shard.add_argument("--task")
    shard.add_argument("--shard", default="0/1")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        written = emit(args)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for path in written:
        print(path)
    print(f"emitted {len(written)} configs for shard {args.shard}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
