import argparse
import secrets
from pathlib import Path
from typing import Any

from ..native_evidence import Invalid
from ..native_study import (
    native,
    normalize,
    require,
    task_files_digest,
    task_identity,
    write_exclusive,
)
from ..seat_checkpoints.register import named_team
from ..shift_board.plan import FIXTURES, MAX_SEED
from .contract import (
    CONTROL_TEAMS,
    LOCK_TASK_NAME,
    MAX_CELLS,
    MAX_NAMED_SEEDS,
    MODES,
    NAMED,
    NATURAL,
    PEER,
    RULE,
    SCHEMA,
    TARGET,
    TASK_NAME,
    Block,
    control_team,
    load_question,
    public_seeds,
    question_blocks,
    validate_registration,
)

MAX_DRAWS = 64


def draw_tickets(count: int) -> list[str]:
    assert 0 < count <= MAX_CELLS
    tickets: set[str] = set()
    for _ in range(4 * count):
        if len(tickets) == count:
            break
        tickets.add("sb-" + secrets.token_hex(8))
    require(len(tickets) == count, "ticket draws kept colliding; nothing written")
    ordered = sorted(tickets)
    secrets.SystemRandom().shuffle(ordered)
    return ordered


def draw_seeds(count: int, taken: frozenset[int]) -> list[int]:
    require(1 <= count <= MAX_NAMED_SEEDS, f"--seeds must be 1..{MAX_NAMED_SEEDS}")
    seeds: list[int] = []
    for _ in range(MAX_DRAWS):
        if len(seeds) == count:
            break
        seed = secrets.randbelow(MAX_SEED + 1)
        if seed not in taken and seed not in seeds:
            seeds.append(seed)
    require(len(seeds) == count, "seed draws kept colliding; nothing written")
    return seeds


def cells(blocks: list[Block]) -> list[dict[str, Any]]:
    assert blocks and len(set(blocks)) == len(blocks)
    tickets = draw_tickets(len(blocks) * len(MODES))
    order = [(found, mode) for found in blocks for mode in MODES]
    rows = [
        {
            "ticket": ticket,
            "team": team,
            "seed": seed,
            "fixture": fixture,
            "rule": RULE,
            "target": TARGET,
            "peer": PEER,
            "observe": True,
            "schedule": schedule,
            "mode": mode,
        }
        for ticket, ((team, schedule, seed, fixture), mode) in zip(
            tickets, order, strict=True
        )
    ]
    return sorted(rows, key=lambda row: row["ticket"])


def plan(
    team: Path | None, seeds: int | None
) -> tuple[dict[str, list[dict[str, Any]]], list[Block]]:
    question = load_question()
    if team is None:
        require(seeds is None, "the controls use the published seeds")
        teams = {label: control_team(policy) for label, policy in CONTROL_TEAMS.items()}
        return teams, question_blocks(question)
    drawn = draw_seeds(1 if seeds is None else seeds, public_seeds(question))
    blocks = [(NAMED, NATURAL, seed, fixture) for seed in drawn for fixture in FIXTURES]
    return {NAMED: named_team(team)}, blocks


def create(task: Path, out: Path, *, team: Path | None, seeds: int | None) -> None:
    harbor = native()
    task = task.resolve(strict=True)
    require(task.name == LOCK_TASK_NAME, "task directory must match its lock name")
    teams, blocks = plan(team, seeds)
    identity = task_identity(harbor, task, TASK_NAME)
    files = task_files_digest(task, identity)
    require(
        task_identity(harbor, task, TASK_NAME) == identity
        and task_files_digest(task, identity) == files,
        "task changed while it was registered",
    )
    study = {
        "schema": SCHEMA,
        "task": identity,
        "task_files": files,
        "teams": {
            label: [normalize(harbor, member) for member in members]
            for label, members in teams.items()
        },
        "cells": cells(blocks),
    }
    write_exclusive(out, validate_registration(study), 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pin a fresh matched seal study without starting a trial."
    )
    parser.add_argument("task", type=Path)
    parser.add_argument("out", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--controls", action="store_true")
    group.add_argument(
        "--team",
        type=Path,
        help="trusted named-seat roster; nothing runs during registration",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        help=f"fresh secret seeds for a named roster, 1..{MAX_NAMED_SEEDS}",
    )
    args = parser.parse_args()
    try:
        create(args.task, args.out, team=args.team, seeds=args.seeds)
    except (FileExistsError, ValueError, Invalid) as error:
        parser.exit(2, f"register: {error}\n")


if __name__ == "__main__":
    main()
