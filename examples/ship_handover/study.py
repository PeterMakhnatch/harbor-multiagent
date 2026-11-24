import argparse
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..joint_reopen.study import (
    CREDENTIAL,
    MAX_ENV,
    MAX_TEAMS,
    MODEL_FIELDS,
    MODEL_NAME,
    ORACLE,
    REQUIRED_FIELDS,
    TEAM,
    fresh_workspace,
)
from ..native_study import (
    AGENT_NAME,
    API_FORMAT,
    KEY_NAME,
    TASK_FILES,
    as_choice,
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

SCHEMA = "harbor.ship-handover.study/v1"
CLAIM_SCHEMA = "harbor.ship-handover.claim/v1"
TASK_NAME = "harbor/ship-handover"
TASK_DIR = Path(__file__).resolve().parents[1] / "tasks" / "ship-handover"
POLICIES = {
    "outgoing": ("original", "draft", "pass", "idle", "late"),
    "relief": (
        "resume",
        "fresh",
        "close",
        "blind-fresh",
        "blind-close",
        "idle",
        "late-read",
    ),
}
ROLES = ("outgoing", "relief")
assert set(ROLES) == set(POLICIES)
PRIORS = ("committed", "dropped")
CHANNELS = ("deliver", "withhold")
WORLD_PREFIX = "SH_"
POLICY = "SH_POLICY"
MAX_CELLS = 2048
MAX_REGISTRATION_BYTES = 1 << 20
TICKET = re.compile(r"sh-[0-9a-f]{16}")
CELL_FIELDS = frozenset({"ticket", "team", "prior", "channel"})
REGISTRATION_FIELDS = frozenset({"schema", "task", "teams", "cells"})


def _check_env(value: object, where: str, role: str, oracle: bool) -> None:
    env = as_record(value, f"{where} env")
    require(0 < len(env) <= MAX_ENV, f"{where} env must hold 1..{MAX_ENV} entries")
    if oracle:
        require(set(env) == {POLICY}, f"{where} oracle env sets only {POLICY}")
        as_choice(env[POLICY], f"{where} {role} {POLICY}", POLICIES[role])
        return
    for key, reference in env.items():
        as_text(key, f"{where} env name", KEY_NAME)
        require(CREDENTIAL.search(key) is not None, f"{where} env {key} is no key")
        require(
            not key.startswith(WORLD_PREFIX) and reference == "${" + key + "}",
            f"{where} env {key} must be the bare reference ${{{key}}}",
        )


def _check_participant(value: object, where: str) -> str:
    member = as_record(value, where)
    keys = set(member)
    require(keys >= REQUIRED_FIELDS, f"{where} lacks normalized native fields")
    require(
        keys <= REQUIRED_FIELDS | MODEL_FIELDS | {"env"},
        f"{where} has fields outside the v1 allowlist",
    )
    check_native_defaults(member, where)
    role = as_choice(member["seat"], f"{where} seat", ROLES)
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
        _check_env(member["env"], where, role, oracle)
    return role


def _check_teams(value: object) -> dict[str, Any]:
    teams = as_record(value, "teams")
    require(0 < len(teams) <= MAX_TEAMS, f"registration needs 1..{MAX_TEAMS} teams")
    for label, members in teams.items():
        as_text(label, "team name", TEAM)
        team = as_list(members, f"team {label!r}", 2, 2)
        roles = {
            _check_participant(member, f"team {label!r} member {position}")
            for position, member in enumerate(team)
        }
        require(roles == set(ROLES), f"team {label!r} needs outgoing and relief")
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
        as_choice(cell["prior"], f"{where} prior", PRIORS)
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
    return {"SH_PRIOR": cell["prior"], "SH_CHANNEL": cell["channel"]}


WORLD = (
    SCHEMA,
    "registration",
    CLAIM_SCHEMA,
    TASK_NAME,
    TICKET,
    MAX_CELLS,
    load_registration,
    world_env,
)


def _requests(
    args: argparse.Namespace, trials: Path
) -> tuple[Path, str, list[tuple[str, dict[str, Any]]]]:
    index, count = parse_shard(args.shard)
    registration = load_registration(Path(args.registration))
    selected = [c for i, c in enumerate(registration["cells"]) if i % count == index]
    require(len(selected) > 0, f"shard {index}/{count} selects no cells")
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
    requests = [
        (
            c["ticket"],
            trial_request(
                harbor, task_dir, trials, c["ticket"], teams[c["team"]], world_env(c)
            ),
        )
        for c in selected
    ]
    return task_dir, files_digest, requests


def emit(args: argparse.Namespace) -> list[Path]:
    workspace = fresh_workspace(args.out)
    configs, trials = workspace / "configs", workspace / "trials"
    _, files_digest, requests = _requests(args, trials)
    workspace.mkdir()
    written: list[Path] = []
    created = False
    try:
        created = record_task_files(workspace / TASK_FILES, files_digest)
        require(created, f"another writer bound {workspace} first")
        configs.mkdir()
        for ticket, request in requests:
            target = configs / f"{ticket}.json"
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
    root = argparse.ArgumentParser(prog="python -m examples.ship_handover.study")
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
