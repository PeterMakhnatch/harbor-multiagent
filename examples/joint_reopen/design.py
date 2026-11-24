import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..native_study import (
    NATIVE_DEFAULTS,
    as_list,
    as_record,
    native,
    normalize,
    require,
    require_normalized,
    task_files_digest,
    task_identity,
    trial_request,
    write_exclusive,
)
from ..seat_handoffs.native_records import read_record
from . import support
from .study import (
    CHANNELS,
    CONTROLS,
    CREDENTIAL,
    FIXTURES,
    MAX_SEED,
    MODEL_FIELDS,
    ORACLE,
    OWNER_FIELDS,
    OWNER_IMPORT_PATH,
    REQUIRED_FIELDS,
    SCHEMA,
    TASK_DIR,
    TASK_NAME,
    check_owner_model,
    load_registration,
    validate_registration,
    world_env,
)

TEAM_LABEL = "team"
OPTIONS = support.OPTIONS
TRIPLES = support.TRIPLES
CELLS = TRIPLES * len(FIXTURES) * len(CHANNELS)
MAX_DRAWS = 4
MAX_TICKET_DRAWS = 4 * CELLS
MAX_PARTICIPANTS_BYTES = 1 << 16
RAW_FIELDS = REQUIRED_FIELDS | MODEL_FIELDS | {"env"}
PARTICIPANTS_FIELDS = frozenset({"participants"})
CONTROL_ENV = "JR_POLICY"
PRIVATE_MODE = 0o600
FAILED_EXIT = 1
INVALID_EXIT = 2


class InputError(ValueError):
    pass


def _fresh_target(text: str) -> Path:
    out = Path(text).absolute()
    require(out.name not in ("", ".", ".."), "output names no new file")
    try:
        parent = out.parent.resolve(strict=True)
    except OSError as error:
        raise ValueError("output directory does not exist") from error
    require(parent.is_dir(), "output parent is not a directory")
    target = parent / out.name
    require(not os.path.lexists(target), "output already exists")
    return target


def _check_control(raw: dict[str, Any], where: str) -> None:
    require(not set(raw) & MODEL_FIELDS, f"{where} control names a model")
    require(
        raw.get("skip_model_parsing", False) is False,
        f"{where} control sets skip_model_parsing",
    )
    if "env" not in raw:
        return
    env = as_record(raw["env"], f"{where} control env")
    require(set(env) == {CONTROL_ENV}, f"{where} control env is not only the policy")
    require(env[CONTROL_ENV] in CONTROLS, f"{where} control policy is unknown")


def _check_model(raw: dict[str, Any], where: str) -> None:
    require(isinstance(raw.get("model_name"), str), f"{where} names no model")
    require(
        type(raw.get("skip_model_parsing", False)) is bool,
        f"{where} skip_model_parsing is not a boolean",
    )
    if "env" not in raw:
        return
    env = as_record(raw["env"], f"{where} env")
    for key, reference in env.items():
        require(not key.startswith("JR_"), f"{where} env sets a world control")
        require(CREDENTIAL.search(key) is not None, f"{where} env is not a key name")
        require(reference == "${" + key + "}", f"{where} env holds an inline value")


def _check_owner(raw: dict[str, Any], where: str) -> None:
    require("name" not in raw, f"{where} sets both name and import_path")
    require(set(raw) <= OWNER_FIELDS, f"{where} owner seat has fields outside v1")
    require(
        raw["import_path"] == OWNER_IMPORT_PATH,
        f"{where} import_path is not the joint owner seat",
    )
    require(
        raw.get("skip_model_parsing") is True,
        f"{where} owner seat must set skip_model_parsing",
    )
    check_owner_model(raw.get("model_name"), where)


def _check_raw_participant(value: object, where: str) -> dict[str, Any]:
    raw = as_record(value, where)
    owner = "import_path" in raw
    if owner:
        _check_owner(raw, where)
    else:
        require(set(raw) <= RAW_FIELDS, f"{where} has fields outside the v1 allowlist")
        require(isinstance(raw.get("name"), str), f"{where} names no agent")
    for field, default in NATIVE_DEFAULTS.items():
        found = raw.get(field, default)
        require(
            type(found) is type(default) and found == default,
            f"{where} changes a native default",
        )
    if owner:
        return raw
    if raw["name"] == ORACLE:
        _check_control(raw, where)
    else:
        _check_model(raw, where)
    return raw


def _read_participants(path: Path) -> list[dict[str, Any]]:
    try:
        record = read_record(path, MAX_PARTICIPANTS_BYTES)
    except OSError as error:
        raise ValueError("participants file is unreadable") from error
    require(record is not None, "participants file does not exist")
    roster = as_record(record, "participants file", PARTICIPANTS_FIELDS)
    members = as_list(roster["participants"], "participants", 2, 2)
    raws = [
        _check_raw_participant(member, f"participant {position}")
        for position, member in enumerate(members)
    ]
    assert len(raws) == 2
    return raws


def _normalize_team(
    harbor: SimpleNamespace, raws: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    assert len(raws) == 2
    team: list[dict[str, Any]] = []
    for position, raw in enumerate(raws):
        try:
            team.append(normalize(harbor, raw))
        except (ValueError, TypeError) as error:
            raise ValueError(
                f"participant {position} fails native Participant validation"
            ) from error
    require_normalized(harbor, {TEAM_LABEL: team})
    return team


def _verified_task(harbor: SimpleNamespace) -> dict[str, str]:
    frozen = support.TASK
    require(frozen["name"] == TASK_NAME, "support.TASK names another task")
    try:
        before = task_files_digest(TASK_DIR, frozen)
        found = task_identity(harbor, TASK_DIR, TASK_NAME)
        after = task_files_digest(TASK_DIR, frozen)
    except (ValueError, OSError) as error:
        raise ValueError("native Task/Packager cannot load the frozen task") from error
    require(found == frozen, "native task identity differs from support.TASK")
    require(before == after, "task changed while it was checked")
    return dict(found)


def _draw_seeds(incumbent: str) -> list[int]:
    assert incumbent in OPTIONS
    for _ in range(MAX_DRAWS):
        start = secrets.randbelow(MAX_SEED + 1)
        stride = 2 * secrets.randbelow((MAX_SEED + 1) // 2) + 1
        assert 0 <= start <= MAX_SEED and stride % 2 == 1 and stride <= MAX_SEED
        try:
            seeds = support.balanced_seeds(incumbent, start, stride)
        except ValueError:
            continue
        require(len(set(seeds)) == len(seeds) == TRIPLES, "seed plan is incomplete")
        return seeds
    raise ValueError(f"no balanced seed plan in {MAX_DRAWS} fresh draws")


def _draw_tickets(count: int) -> list[str]:
    assert 0 < count <= CELLS
    tickets: set[str] = set()
    for _ in range(MAX_TICKET_DRAWS):
        if len(tickets) == count:
            break
        tickets.add("jr-" + secrets.token_hex(8))
    require(len(tickets) == count, "ticket draws kept colliding")
    ordered = sorted(tickets)
    secrets.SystemRandom().shuffle(ordered)
    return ordered


def _cells(seeds: list[int]) -> list[dict[str, Any]]:
    assert len(seeds) == TRIPLES
    plan = [
        (seed, fixture, channel)
        for seed in seeds
        for fixture in FIXTURES
        for channel in CHANNELS
    ]
    secrets.SystemRandom().shuffle(plan)
    tickets = _draw_tickets(len(plan))
    cells = [
        {
            "ticket": ticket,
            "team": TEAM_LABEL,
            "seed": seed,
            "fixture": fixture,
            "channel": channel,
        }
        for ticket, (seed, fixture, channel) in zip(tickets, plan, strict=True)
    ]
    assert len(cells) == CELLS
    return cells


def _check_balance(registration: dict[str, Any]) -> dict[str, Any]:
    try:
        report = support.audit(registration)
    except ValueError as error:
        raise ValueError("design audit refuses the generated plan") from error
    require(report.get("balanced_paired") is True, "design audit finds no balance")
    require(report.get("rows") == CELLS, "design audit counts other rows")
    require(report.get("teams") == 1, "design audit counts other teams")
    require(report.get("task_registered") is True, "design audit rejects the task")
    return report


def register(args: argparse.Namespace) -> dict[str, Any]:
    try:
        target = _fresh_target(args.out)
        raws = _read_participants(Path(args.participants))
    except ValueError as error:
        raise InputError(str(error)) from error
    harbor = native()
    try:
        team = _normalize_team(harbor, raws)
    except ValueError as error:
        raise InputError(str(error)) from error
    task = _verified_task(harbor)
    cells = _cells(_draw_seeds(args.incumbent))
    registration = {
        "schema": SCHEMA,
        "task": task,
        "teams": {TEAM_LABEL: team},
        "cells": cells,
    }
    probe = cells[0]
    try:
        validate_registration(registration)
        trial_request(
            harbor, TASK_DIR, target.parent, probe["ticket"], team, world_env(probe)
        )
    except (ValueError, TypeError) as error:
        raise InputError(
            "team fails the joint study or TrialConfig contract"
        ) from error
    report = _check_balance(registration)
    write_exclusive(target, registration, PRIVATE_MODE)
    return {
        "status": "registered",
        "task_verified": True,
        "teams": 1,
        "seats": 2,
        "incumbent": args.incumbent,
        "seeds": TRIPLES,
        "fixtures": len(FIXTURES),
        "channels": len(CHANNELS),
        "cells": len(cells),
        "balanced_paired": report["balanced_paired"],
    }


def audit(args: argparse.Namespace) -> dict[str, Any]:
    try:
        registration = load_registration(Path(args.registration))
        report = support.audit(registration)
    except (ValueError, OSError) as error:
        raise InputError("registration is invalid") from error
    require(type(report.get("balanced_paired")) is bool, "design audit has no verdict")
    return {"status": "valid", **report}


def _render(summary: dict[str, Any], as_json: bool) -> str:
    if as_json:
        return json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    lines = [
        f"{key}: {json.dumps(value, sort_keys=True, allow_nan=False)}"
        for key, value in summary.items()
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m examples.joint_reopen.design")
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("register")
    plan.add_argument("--participants", required=True)
    plan.add_argument("--incumbent", default="A", choices=OPTIONS)
    plan.add_argument("--out", required=True)
    plan.add_argument("--json", action="store_true")
    check = commands.add_parser("audit")
    check.add_argument("registration")
    check.add_argument("--json", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    run = audit if args.command == "audit" else register
    try:
        summary = run(args)
    except InputError as error:
        print(f"error: invalid input: {error}", file=sys.stderr)
        return INVALID_EXIT
    except ValueError as error:
        print(f"error: {args.command} failed: {error}", file=sys.stderr)
        return FAILED_EXIT
    except OSError as error:
        print(f"error: {args.command} failed: {type(error).__name__}", file=sys.stderr)
        return FAILED_EXIT
    print(_render(summary, args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
