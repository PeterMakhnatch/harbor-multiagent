import argparse
import re
import sys
from contextlib import suppress
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

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
    DIGEST,
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
NOP = "nop"
INFLIGHT_SCHEMA = "harbor.ship-inflight.study/v1"
INFLIGHT_CLAIM_SCHEMA = "harbor.ship-inflight.claim/v1"
INFLIGHT_TASK_NAME = "harbor/ship-inflight"
INFLIGHT_TICKET = re.compile(r"si-[0-9a-f]{16}")
INFLIGHT_PIN = (
    Path(__file__).resolve().parents[1] / "ship_inflight" / "samples" / "task-pin.json"
)
INFLIGHT_PIN_SCHEMA = "harbor.ship-inflight.source/v1"
INFLIGHT_PIN_FIELDS = frozenset({"schema", "question", "task", "task_files", "files"})
INFLIGHT_POLICIES = {
    "outgoing": POLICIES["outgoing"],
    "relief": ("replace", *POLICIES["relief"]),
}
FATES = ("settles", "fails")
MAX_PIN_BYTES = 1 << 16


class Spec(NamedTuple):
    world: str
    schema: str
    claim_schema: str
    task_name: str
    axis: str
    states: tuple[str, ...]
    env_name: str
    prefix: str
    ticket: re.Pattern[str]
    policies: MappingProxyType[str, tuple[str, ...]]
    task_dir: Path | None
    pin: Path | None

    def cell_fields(self) -> frozenset[str]:
        return frozenset({"ticket", "team", self.axis, "channel"})


STOCK = Spec(
    "stock",
    SCHEMA,
    CLAIM_SCHEMA,
    TASK_NAME,
    "prior",
    PRIORS,
    "SH_PRIOR",
    "sh-",
    TICKET,
    MappingProxyType(POLICIES),
    TASK_DIR,
    None,
)
INFLIGHT = Spec(
    "inflight",
    INFLIGHT_SCHEMA,
    INFLIGHT_CLAIM_SCHEMA,
    INFLIGHT_TASK_NAME,
    "fate",
    FATES,
    "SH_FATE",
    "si-",
    INFLIGHT_TICKET,
    MappingProxyType(INFLIGHT_POLICIES),
    None,
    INFLIGHT_PIN,
)
SPECS = {spec.world: spec for spec in (STOCK, INFLIGHT)}
assert STOCK.cell_fields() == CELL_FIELDS
assert set(INFLIGHT_POLICIES["relief"]) - set(POLICIES["relief"]) == {"replace"}
assert INFLIGHT_POLICIES["outgoing"] == POLICIES["outgoing"]
assert len({spec.schema for spec in SPECS.values()}) == len(SPECS)
assert len({spec.axis for spec in SPECS.values()}) == len(SPECS)
assert all(len(spec.states) == 2 for spec in SPECS.values())
assert all(spec.env_name == WORLD_PREFIX + spec.axis.upper() for spec in SPECS.values())
assert all(spec.ticket.pattern.startswith(spec.prefix) for spec in SPECS.values())
assert all((spec.task_dir is None) != (spec.pin is None) for spec in SPECS.values())


def spec_for(registration: object) -> Spec:
    schema = as_record(registration, "registration").get("schema")
    found = [spec for spec in SPECS.values() if spec.schema == schema]
    known = sorted(spec.schema for spec in SPECS.values())
    require(len(found) == 1, f"registration schema must be one of {known}")
    return found[0]


def pinned_task(spec: Spec) -> tuple[dict[str, Any], str] | None:
    if spec.pin is None:
        return None
    record = read_record(spec.pin, MAX_PIN_BYTES)
    require(record is not None, f"task pin {spec.pin} does not exist")
    pin = as_record(record, "task pin", INFLIGHT_PIN_FIELDS)
    require(
        pin["schema"] == INFLIGHT_PIN_SCHEMA,
        f"task pin schema must be {INFLIGHT_PIN_SCHEMA}",
    )
    task = check_task(pin["task"], spec.task_name)
    return task, as_text(pin["task_files"], "task pin files digest", DIGEST)


def task_dir_for(spec: Spec, task: str | None) -> Path:
    if task:
        return Path(task).resolve()
    require(
        spec.task_dir is not None,
        f"{spec.task_name} needs --task MATERIALIZED from "
        "python3 -B -m examples.ship_inflight.materialize OUT",
    )
    assert spec.task_dir is not None
    return spec.task_dir


def require_pinned_files(spec: Spec, task_dir: Path, files_digest: str) -> None:
    pin = pinned_task(spec)
    require(
        pin is None or pin[1] == files_digest,
        f"task {task_dir} is not the pinned {spec.task_name} package",
    )


def _check_env(value: object, where: str, role: str, oracle: bool, spec: Spec) -> None:
    env = as_record(value, f"{where} env")
    require(0 < len(env) <= MAX_ENV, f"{where} env must hold 1..{MAX_ENV} entries")
    if oracle:
        require(set(env) == {POLICY}, f"{where} oracle env sets only {POLICY}")
        as_choice(env[POLICY], f"{where} {role} {POLICY}", spec.policies[role])
        return
    for key, reference in env.items():
        as_text(key, f"{where} env name", KEY_NAME)
        require(CREDENTIAL.search(key) is not None, f"{where} env {key} is no key")
        require(
            not key.startswith(WORLD_PREFIX) and reference == "${" + key + "}",
            f"{where} env {key} must be the bare reference ${{{key}}}",
        )


def _check_participant(value: object, where: str, spec: Spec) -> str:
    member = as_record(value, where)
    keys = set(member)
    require(keys >= REQUIRED_FIELDS, f"{where} lacks normalized native fields")
    require(
        keys <= REQUIRED_FIELDS | MODEL_FIELDS | {"env"},
        f"{where} has fields outside the v1 allowlist",
    )
    check_native_defaults(member, where)
    role = as_choice(member["seat"], f"{where} seat", ROLES)
    name = as_text(member["name"], f"{where} name", AGENT_NAME)
    if name in (ORACLE, NOP):
        require(not keys & MODEL_FIELDS, f"{where} {name} names a model")
        require(member["skip_model_parsing"] is False, f"{where} skip_model_parsing")
        require(name == ORACLE or "env" not in member, f"{where} {name} sets env")
    else:
        as_text(member.get("model_name"), f"{where} model_name", MODEL_NAME)
        if "model_api_key_name" in member:
            key = as_text(member["model_api_key_name"], f"{where} key name", KEY_NAME)
            require(
                not key.startswith(WORLD_PREFIX), f"{where} key names a world field"
            )
        if "model_api_format" in member:
            as_text(member["model_api_format"], f"{where} api format", API_FORMAT)
    if "env" in member:
        _check_env(member["env"], where, role, name == ORACLE, spec)
    return role


def _check_teams(value: object, spec: Spec) -> dict[str, Any]:
    teams = as_record(value, "teams")
    require(0 < len(teams) <= MAX_TEAMS, f"registration needs 1..{MAX_TEAMS} teams")
    for label, members in teams.items():
        as_text(label, "team name", TEAM)
        team = as_list(members, f"team {label!r}", 2, 2)
        roles = {
            _check_participant(member, f"team {label!r} member {position}", spec)
            for position, member in enumerate(team)
        }
        require(roles == set(ROLES), f"team {label!r} needs outgoing and relief")
    return teams


def _check_cells(value: object, teams: dict[str, Any], spec: Spec) -> None:
    tickets: set[str] = set()
    for position, item in enumerate(as_list(value, "cells", 1, MAX_CELLS)):
        where = f"cell {position}"
        cell = as_record(item, where, spec.cell_fields())
        ticket = as_text(cell["ticket"], f"{where} ticket", spec.ticket)
        require(ticket not in tickets, f"{where} repeats ticket {ticket}")
        tickets.add(ticket)
        as_choice(cell["team"], f"{where} team", teams)
        as_choice(cell[spec.axis], f"{where} {spec.axis}", spec.states)
        as_choice(cell["channel"], f"{where} channel", CHANNELS)


def validate_registration(value: object) -> dict[str, Any]:
    registration = as_record(value, "registration", REGISTRATION_FIELDS)
    spec = spec_for(registration)
    task = check_task(registration["task"], spec.task_name)
    pin = pinned_task(spec)
    require(
        pin is None or task == pin[0],
        f"registration does not bind the pinned {spec.task_name} package",
    )
    teams = _check_teams(registration["teams"], spec)
    _check_cells(registration["cells"], teams, spec)
    return registration


def load_registration(path: Path) -> dict[str, Any]:
    record = read_record(path, MAX_REGISTRATION_BYTES)
    require(record is not None, f"registration {path} does not exist")
    return validate_registration(record)


def world_env(cell: dict[str, Any]) -> dict[str, str]:
    found = [spec for spec in SPECS.values() if spec.axis in cell]
    require(len(found) == 1, "cell must name exactly one of prior or fate")
    spec = found[0]
    state = as_choice(cell[spec.axis], f"cell {spec.axis}", spec.states)
    channel = as_choice(cell["channel"], "cell channel", CHANNELS)
    return {spec.env_name: state, "SH_CHANNEL": channel}


def _world(spec: Spec) -> tuple[Any, ...]:
    return (
        spec.schema,
        "registration",
        spec.claim_schema,
        spec.task_name,
        spec.ticket,
        MAX_CELLS,
        load_registration,
        world_env,
    )


WORLD = _world(STOCK)
INFLIGHT_WORLD = _world(INFLIGHT)
assert len(WORLD) == len(INFLIGHT_WORLD) == 8


def _requests(
    args: argparse.Namespace, trials: Path
) -> tuple[Path, str, list[tuple[str, dict[str, Any]]]]:
    index, count = parse_shard(args.shard)
    registration = load_registration(Path(args.registration))
    spec = spec_for(registration)
    selected = [c for i, c in enumerate(registration["cells"]) if i % count == index]
    require(len(selected) > 0, f"shard {index}/{count} selects no cells")
    task_dir = task_dir_for(spec, args.task)
    harbor = native()
    identity = registration["task"]
    files_digest = task_files_digest(task_dir, identity)
    found = task_identity(harbor, task_dir, spec.task_name)
    require(found == identity, f"task {task_dir} is unregistered: {found}")
    require(
        task_files_digest(task_dir, identity) == files_digest,
        "task changed while it was checked",
    )
    require_pinned_files(spec, task_dir, files_digest)
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
    shard.add_argument(
        "--task",
        help=f"task package directory (stock default {TASK_DIR}; inflight: required)",
    )
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
