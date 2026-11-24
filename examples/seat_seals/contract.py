from pathlib import Path
from typing import Any

from ..native_evidence import Record, strict_object
from ..native_study import (
    DIGEST,
    as_choice,
    as_int,
    as_list,
    as_record,
    as_text,
    check_task,
    require,
)
from ..seat_checkpoints.collect import _check_teams
from ..seat_handoffs.native_records import read_record
from ..shift_board.collect import _environment
from ..shift_board.plan import FIXTURES, MAX_SEED, TICKET

SCHEMA = "harbor.seat-seal.study/v1"
QUESTION_SCHEMA = "harbor.seat-seal.question/v1"
TASK_NAME = "harbor/shift-board-seal"
LOCK_TASK_NAME = "shift-board-seal"
QUESTION = Path(__file__).resolve().with_name("samples") / "question.json"
CONTROL_IMPORT = "examples.seat_checkpoints.control_seat:ControlSeat"
SEATS = ("planner", "dispatcher")
TARGET = "planner"
PEER = "dispatcher"
RULE = "observe"
MODES = ("continue", "seal")
SCRIPTED = ("held-early", "held-late", "held-window", "held-leave")
NATURAL = "natural"
WINDOW = "held-window"
SCHEDULES = (*SCRIPTED, NATURAL)
CONTROL_TEAMS = {"control-preferred": "preferred", "control-leave": "leave"}
NAMED = "named"
PLANNER_GATES = ("after_publish",)
DISPATCHER_GATES = ("before_decide", "after_decide")
REGISTRATION_FIELDS = frozenset({"schema", "task", "task_files", "teams", "cells"})
CELL_FIELDS = frozenset(
    {
        "ticket",
        "team",
        "seed",
        "fixture",
        "rule",
        "target",
        "peer",
        "observe",
        "schedule",
        "mode",
    }
)
SCHEDULE_FIELDS = frozenset({"seeds", "fixtures", "policy"})
CONTROL_BLOCKS = 9
CONTROL_CELLS = CONTROL_BLOCKS * len(MODES)
MAX_NAMED_SEEDS = 16
MAX_CELLS = 2 * len(FIXTURES) * len(MODES) * MAX_NAMED_SEEDS
MAX_QUESTION_BYTES = 64 << 10
MAX_REGISTRATION_BYTES = 1 << 20

Block = tuple[str, str, int, str]


def control_team(policy: str) -> list[dict[str, Any]]:
    assert policy in CONTROL_TEAMS.values()
    return [
        {
            "seat": "planner",
            "import_path": CONTROL_IMPORT,
            "kwargs": {
                "role": "planner",
                "policy": "publish",
                "gates": list(PLANNER_GATES),
            },
        },
        {
            "seat": "dispatcher",
            "import_path": CONTROL_IMPORT,
            "kwargs": {
                "role": "dispatcher",
                "policy": policy,
                "gates": list(DISPATCHER_GATES),
            },
        },
    ]


def team_for(policy: str) -> str:
    labels = [label for label, found in CONTROL_TEAMS.items() if found == policy]
    require(len(labels) == 1, f"no scripted team runs policy {policy!r}")
    return labels[0]


def _check_schedule(value: object, name: str) -> Record:
    where = f"question schedule {name!r}"
    record = as_record(value, where, SCHEDULE_FIELDS)
    seeds = as_list(record["seeds"], f"{where} seeds", 1, MAX_NAMED_SEEDS)
    for seed in seeds:
        as_int(seed, f"{where} seed", 0, MAX_SEED)
    require(len(set(seeds)) == len(seeds), f"{where} repeats a seed")
    fixtures = as_list(record["fixtures"], f"{where} fixtures", 1, len(FIXTURES))
    for fixture in fixtures:
        as_choice(fixture, f"{where} fixture", FIXTURES)
    require(len(set(fixtures)) == len(fixtures), f"{where} repeats a fixture")
    as_choice(record["policy"], f"{where} policy", CONTROL_TEAMS.values())
    return record


def question_blocks(question: Record) -> list[Block]:
    require(question.get("schema") == QUESTION_SCHEMA, "question schema differs")
    require(question.get("task") == TASK_NAME, "question names another task")
    require(
        question.get("target") == TARGET
        and question.get("peer") == PEER
        and question.get("rule") == RULE,
        "question target, peer or rule differs",
    )
    require(question.get("modes") == list(MODES), "question modes differ")
    schedules = as_record(question.get("schedules"), "question schedules")
    require(list(schedules) == list(SCRIPTED), "question schedules differ")
    blocks: list[Block] = []
    for name in SCRIPTED:
        record = _check_schedule(schedules[name], name)
        team = team_for(record["policy"])
        blocks.extend(
            (team, name, seed, fixture)
            for seed in record["seeds"]
            for fixture in record["fixtures"]
        )
    require(len(blocks) == CONTROL_BLOCKS, f"question is not {CONTROL_BLOCKS} blocks")
    require(len(set(blocks)) == len(blocks), "question repeats a block")
    return blocks


def load_question() -> Record:
    with QUESTION.open("rb") as source:
        data = source.read(MAX_QUESTION_BYTES + 1)
    require(len(data) <= MAX_QUESTION_BYTES, "question exceeds its size bound")
    return strict_object(data, QUESTION.name)


def public_seeds(question: Record) -> frozenset[int]:
    return frozenset(seed for _, _, seed, _ in question_blocks(question))


def block(cell: Record) -> Block:
    return (cell["team"], cell["schedule"], cell["seed"], cell["fixture"])


def pairs(cells: list[Record]) -> dict[Block, dict[str, Record]]:
    assert len(cells) <= MAX_CELLS
    grouped: dict[Block, dict[str, Record]] = {}
    for cell in cells:
        arms = grouped.setdefault(block(cell), {})
        require(cell["mode"] not in arms, f"block {block(cell)} repeats a mode")
        arms[cell["mode"]] = cell
    for key, arms in grouped.items():
        require(set(arms) == set(MODES), f"block {key} is not one matched pair")
    assert sum(len(arms) for arms in grouped.values()) == len(cells)
    return grouped


def _check_cell(value: object, teams: Record, tickets: set[str]) -> Record:
    cell = as_record(value, "cell", CELL_FIELDS)
    ticket = as_text(cell["ticket"], "cell ticket", TICKET)
    require(ticket not in tickets, "duplicate ticket")
    tickets.add(ticket)
    team = as_choice(cell["team"], "cell team", teams)
    as_int(cell["seed"], "cell seed", 0, MAX_SEED)
    as_choice(cell["fixture"], "cell fixture", FIXTURES)
    require(
        cell["rule"] == RULE and cell["target"] == TARGET and cell["peer"] == PEER,
        "cell rule, target or peer differs from the question",
    )
    require(cell["observe"] is True, "every cell observes the target return")
    schedule = as_choice(cell["schedule"], "cell schedule", SCHEDULES)
    as_choice(cell["mode"], "cell mode", MODES)
    require(
        (schedule == NATURAL) == (team == NAMED),
        "only named rosters run the natural schedule",
    )
    return cell


def _check_controls(teams: Record, cells: list[Record]) -> None:
    require(set(teams) == set(CONTROL_TEAMS), "control teams differ")
    for label, policy in CONTROL_TEAMS.items():
        found = {member["seat"]: member["kwargs"] for member in teams[label]}
        expected = {member["seat"]: member["kwargs"] for member in control_team(policy)}
        require(
            all(member.get("import_path") == CONTROL_IMPORT for member in teams[label])
            and found == expected,
            f"control team {label!r} differs from the published roster",
        )
    blocks = question_blocks(load_question())
    require(len(cells) == CONTROL_CELLS, f"controls are not {CONTROL_CELLS} cells")
    require(
        sorted(pairs(cells)) == sorted(blocks),
        "control blocks differ from the published question",
    )


def _check_named(teams: Record, cells: list[Record]) -> None:
    require(set(teams) == {NAMED}, "a named registration binds one named roster")
    require(
        all("name" in member for member in teams[NAMED]),
        "named roster includes a scripted seat",
    )
    grouped = pairs(cells)
    seeds = {seed for _, _, seed, _ in grouped}
    require(1 <= len(seeds) <= MAX_NAMED_SEEDS, "named seed count is out of range")
    require(
        not seeds & public_seeds(load_question()),
        "named seeds must be fresh, not the published control seeds",
    )
    require(
        set(grouped)
        == {(NAMED, NATURAL, seed, fixture) for seed in seeds for fixture in FIXTURES},
        "named blocks do not cross every seed with both fixtures",
    )


def validate_registration(value: object) -> Record:
    registration = as_record(value, "registration", REGISTRATION_FIELDS)
    require(registration["schema"] == SCHEMA, "registration schema differs")
    check_task(registration["task"], TASK_NAME)
    as_text(registration["task_files"], "full task files", DIGEST)
    teams = _check_teams(registration["teams"])
    raw = as_list(registration["cells"], "cells", 2, MAX_CELLS)
    tickets: set[str] = set()
    cells = [_check_cell(cell, teams, tickets) for cell in raw]
    assert len(tickets) == len(cells)
    if NAMED in teams:
        _check_named(teams, cells)
    else:
        _check_controls(teams, cells)
    return registration


def load_registration(path: Path) -> Record:
    record = read_record(path, MAX_REGISTRATION_BYTES)
    require(record is not None, f"registration {path} does not exist")
    return validate_registration(record)


def is_control(registration: Record) -> bool:
    return NAMED not in registration["teams"]


def environment(cell: Record) -> dict[str, str]:
    mode = as_choice(cell["mode"], "cell mode", MODES)
    env = {**_environment(cell), "BOARD_SEAL": mode}
    assert len(env) == 5
    return env
