import argparse
import secrets
from pathlib import Path
from typing import Any

from ..native_evidence import strict_object
from ..native_study import native, normalize, require, task_files_digest, task_identity, write_exclusive
from .collect import validate_registration

TASK = "harbor/shift-board-checkpoint"
SCHEMA = "harbor.seat-cut.study/v1"
ROLES = ("planner", "dispatcher")
FIXTURES = ("planner-fit", "dispatcher-fit")
CONTROL = "examples.seat_checkpoints.control_seat:ControlSeat"
QUESTION = Path(__file__).with_name("samples") / "question.json"
NAMED_FIELDS = {
    "seat", "name", "model_name", "skip_model_parsing", "model_api_key_name", "model_api_format"
}


def trusted_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    require(len(data) <= 1024 * 1024, "input exceeds 1 MiB")
    return strict_object(data, path.name)


def control_team(arm: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"seat": role, "import_path": CONTROL, "kwargs": {
            "role": role,
            "policy": "publish" if role == "planner" else (
                "leave" if arm["dispatcher_policy"] == "leave" else "preferred"
            ),
            "gates": arm[f"{role}_gates"],
        }} for role in ROLES
    ]


def cell(label: str, seed: int, fixture: str, target: str, observe: bool) -> dict[str, Any]:
    return {
        "ticket": "sb-" + secrets.token_hex(8), "team": label, "seed": seed,
        "fixture": fixture, "rule": "observe", "target": target,
        "peer": ROLES[1 - ROLES.index(target)], "observe": observe,
    }


def controls() -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    question = trusted_json(QUESTION)
    teams = {label: control_team(arm) for label, arm in question["arms"].items()}
    cells = [cell(label, seed, fixture, "planner", arm["observe"])
             for label, arm in question["arms"].items() for seed in arm["seeds"]
             for fixture in FIXTURES]
    return teams, cells


def named_team(path: Path) -> list[dict[str, Any]]:
    raw = trusted_json(path)
    members = raw.get("participants")
    require(set(raw) == {"participants"} and isinstance(members, list)
            and len(members) == 2, "supply two named participants")
    for member in members:
        require(isinstance(member, dict) and set(member) <= NAMED_FIELDS
                and {"seat", "name", "model_name"} <= set(member),
                "named seats require a harness and model; no literal env or command")
        require(all(isinstance(member[k], str) and member[k] for k in ("name", "model_name")),
                "named harness and model must not be empty")
    by_seat = {m["seat"]: m for m in members}
    require(set(by_seat) == set(ROLES), "need planner and dispatcher")
    return [by_seat[role] for role in ROLES]


def create(task: Path, out: Path, *, team: Path | None, seeds: list[int], target: str) -> None:
    harbor = native()
    task = task.resolve(strict=True)
    require(task.name == TASK.split("/")[1], "task directory must match its native lock name")
    if team is None:
        require(not seeds and target == "planner", "qualification controls use the frozen seeds and target")
        teams, cells = controls()
    else:
        seeds = seeds or [17]
        require(1 <= len(seeds) <= 32 and len(set(seeds)) == len(seeds)
                and all(0 <= x <= 2**31 - 1 for x in seeds), "invalid or duplicate seeds")
        teams = {"operator": named_team(team)}
        cells = [cell("operator", seed, fixture, target, observe) for seed in seeds
                 for fixture in FIXTURES for observe in (False, True)]
    require(len({r["ticket"] for r in cells}) == len(cells), "ticket collision; nothing written")
    identity = task_identity(harbor, task, TASK)
    study = {"schema": SCHEMA, "task": identity, "task_files": task_files_digest(task, identity),
             "teams": {name: [normalize(harbor, p) for p in members]
                       for name, members in teams.items()}, "cells": cells}
    write_exclusive(out, validate_registration(study), 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pin a fresh checkpoint study without starting a trial.")
    parser.add_argument("task", type=Path)
    parser.add_argument("out", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--controls", action="store_true")
    group.add_argument("--team", type=Path, help="trusted named-seat roster; nothing runs during registration")
    parser.add_argument("--seed", type=int, action="append", default=[])
    parser.add_argument("--target", choices=ROLES, default="planner")
    args = parser.parse_args()
    create(args.task, args.out, team=args.team, seeds=args.seed, target=args.target)


if __name__ == "__main__":
    main()
