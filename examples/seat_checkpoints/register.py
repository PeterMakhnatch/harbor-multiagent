import argparse
import secrets
from pathlib import Path
from typing import Any

from ..native_evidence import strict_object
from ..native_study import (
    native,
    normalize,
    require,
    task_files_digest,
    task_identity,
    write_exclusive,
)
from .collect import validate_registration
from .timeout_contract import (
    AGENT_TIMEOUT_MULTIPLIER,
    AGENT_TIMEOUT_SECONDS,
    SEED,
    TIMEOUTS,
    UNOBSERVED,
)

TASK = "harbor/shift-board-checkpoint"
SCHEMA = "harbor.seat-cut.study/v1"
ROLES = ("planner", "dispatcher")
FIXTURES = ("planner-fit", "dispatcher-fit")
CONTROL = "examples.seat_checkpoints.control_seat:ControlSeat"
QUESTION = Path(__file__).with_name("samples") / "question.json"
TIMEOUT_QUESTION = Path(__file__).with_name("samples") / "timeout-question.json"
TIMEOUT_SCHEMA = "harbor.seat-cut.timeout-question/v1"
GATES = {"planner": ["after_publish"], "dispatcher": ["before_decide", "after_decide"]}
NAMED_FIELDS = {
    "seat",
    "name",
    "model_name",
    "skip_model_parsing",
    "model_api_key_name",
    "model_api_format",
}


def trusted_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as source:
        data = source.read(1024 * 1024 + 1)
    require(len(data) <= 1024 * 1024, "input exceeds 1 MiB")
    return strict_object(data, path.name)


def control_team(arm: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "seat": role,
            "import_path": CONTROL,
            "kwargs": {
                "role": role,
                "policy": "publish"
                if role == "planner"
                else ("leave" if arm["dispatcher_policy"] == "leave" else "preferred"),
                "gates": arm[f"{role}_gates"],
            },
        }
        for role in ROLES
    ]


def cell(
    label: str, seed: int, fixture: str, target: str, observe: bool
) -> dict[str, Any]:
    return {
        "ticket": "sb-" + secrets.token_hex(8),
        "team": label,
        "seed": seed,
        "fixture": fixture,
        "rule": "observe",
        "target": target,
        "peer": ROLES[1 - ROLES.index(target)],
        "observe": observe,
    }


def arm_cells(
    arms: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    teams = {label: control_team(arm) for label, arm in arms.items()}
    cells = [
        cell(label, seed, fixture, "planner", arm["observe"])
        for label, arm in arms.items()
        for seed in arm["seeds"]
        for fixture in FIXTURES
    ]
    return teams, cells


def controls() -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    return arm_cells(trusted_json(QUESTION)["arms"])


def timeout_question() -> tuple[str, dict[str, dict[str, Any]]]:
    question = trusted_json(TIMEOUT_QUESTION)
    require(
        question.get("schema") == TIMEOUT_SCHEMA
        and question.get("fixtures") == list(FIXTURES)
        and question.get("target") == "planner"
        and question.get("peer") == "dispatcher"
        and question.get("rule") == "observe"
        and type(question.get("agent_timeout_multiplier")) is float
        and question["agent_timeout_multiplier"] == AGENT_TIMEOUT_MULTIPLIER
        and type(question.get("agent_timeout_seconds")) is float
        and question["agent_timeout_seconds"] == AGENT_TIMEOUT_SECONDS
        and isinstance(question.get("task_files"), str),
        "timeout question differs from the frozen native deadline",
    )
    arms = question.get("arms")
    if not isinstance(arms, dict) or list(arms) != list(TIMEOUTS):
        raise ValueError("timeout question does not name exactly the 5 deadline arms")
    for label, arm in arms.items():
        require(
            isinstance(arm, dict)
            and arm.get("seeds") == [SEED]
            and arm.get("observe") is (label not in UNOBSERVED)
            and arm.get("planner_gates") == GATES["planner"]
            and arm.get("dispatcher_gates") == GATES["dispatcher"]
            and arm.get("dispatcher_policy") == "publish"
            and arm.get("timeouts") == list(TIMEOUTS[label]),
            f"deadline arm {label!r} differs from its frozen schedule",
        )
    return question["task_files"], arms


def named_team(path: Path) -> list[dict[str, Any]]:
    raw = trusted_json(path)
    members = raw.get("participants")
    if (
        set(raw) != {"participants"}
        or not isinstance(members, list)
        or len(members) != 2
    ):
        raise ValueError("supply two named participants")
    for member in members:
        require(
            isinstance(member, dict)
            and set(member) <= NAMED_FIELDS
            and {"seat", "name", "model_name"} <= set(member),
            "named seats require a harness and model; no literal env or command",
        )
        require(
            all(
                isinstance(member[k], str) and member[k] for k in ("name", "model_name")
            ),
            "named harness and model must not be empty",
        )
    by_seat = {m["seat"]: m for m in members}
    require(set(by_seat) == set(ROLES), "need planner and dispatcher")
    return [by_seat[role] for role in ROLES]


def create(
    task: Path,
    out: Path,
    *,
    team: Path | None,
    seeds: list[int],
    target: str,
    timeout_controls: bool,
) -> None:
    harbor = native()
    task = task.resolve(strict=True)
    require(
        task.name == TASK.split("/")[1],
        "task directory must match its native lock name",
    )
    pinned: str | None = None
    if team is None:
        require(
            not seeds and target == "planner",
            "qualification controls use the frozen seeds and target",
        )
        if timeout_controls:
            pinned, arms = timeout_question()
            teams, cells = arm_cells(arms)
        else:
            teams, cells = controls()
    else:
        require(not timeout_controls, "a named team takes no native deadline")
        seeds = seeds or [17]
        require(
            1 <= len(seeds) <= 32
            and len(set(seeds)) == len(seeds)
            and all(0 <= x <= 2**31 - 1 for x in seeds),
            "invalid or duplicate seeds",
        )
        teams = {"operator": named_team(team)}
        cells = [
            cell("operator", seed, fixture, target, observe)
            for seed in seeds
            for fixture in FIXTURES
            for observe in (False, True)
        ]
    require(
        len({r["ticket"] for r in cells}) == len(cells),
        "ticket collision; nothing written",
    )
    identity = task_identity(harbor, task, TASK)
    files = task_files_digest(task, identity)
    require(
        pinned is None or files == pinned,
        "full local task tree differs from the timeout question",
    )
    study = {
        "schema": SCHEMA,
        "task": identity,
        "task_files": files,
        "teams": {
            name: [normalize(harbor, p) for p in members]
            for name, members in teams.items()
        },
        "cells": cells,
    }
    write_exclusive(out, validate_registration(study), 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pin a fresh checkpoint study without starting a trial."
    )
    parser.add_argument("task", type=Path)
    parser.add_argument("out", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--controls", action="store_true")
    group.add_argument(
        "--timeout-controls",
        action="store_true",
        help="the 5 frozen native-deadline schedules; separate from --controls",
    )
    group.add_argument(
        "--team",
        type=Path,
        help="trusted named-seat roster; nothing runs during registration",
    )
    parser.add_argument("--seed", type=int, action="append", default=[])
    parser.add_argument("--target", choices=ROLES, default="planner")
    args = parser.parse_args()
    create(
        args.task,
        args.out,
        team=args.team,
        seeds=args.seed,
        target=args.target,
        timeout_controls=args.timeout_controls,
    )


if __name__ == "__main__":
    main()
