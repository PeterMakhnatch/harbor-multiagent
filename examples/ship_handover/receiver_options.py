import hashlib
import json
import sys
from collections.abc import Sequence
from itertools import product
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

from ..native_study import require
from ..seat_checkpoints.materialize import read_plain
from ..ship_inflight import inflight_verify as trusted
from ..ship_inflight.visibility import RUN, TOKENS, verified_world

USAGE = "usage: python3.12 -BS -m examples.ship_handover.receiver_options [--json]"
SCHEMA = "harbor.shipping.receiver-options/v1"
QUESTION_SCHEMA = "harbor.shipping.receiver-options-question/v1"
QUESTION_RELATIVE = "samples/receiver-options/question.json"
QUESTION_PATH = Path(__file__).resolve().parent / QUESTION_RELATIVE
MAX_QUESTION_BYTES = 16 * 1024
X = "0123456789abcdef" * 2
Y = "fedcba9876543210" * 2
OFFERS = ("original", "draft", "null")
CHANNELS = trusted.CHANNELS
KNOWLEDGE = {"opaque": OFFERS, "original_only": ("original",)}
VIEWS = ("received", "empty")
ACTIONS = ("stop", "close", "fresh", "resume_received", "replace_received")
HELD = ("resume_received", "replace_received")
READS = ("/brief", "/status", "/inbox")
MAX_TRIALS = 64
PREDICTED = {"stock": ["resume_received"], "inflight": ["replace_received"]}
VERDICTS = ("pass", "prediction_failed", "census_nonpass")
LIMITS = (
    "pinned role handlers and native verifiers called in process; HTTP framing, bearer checks, seat containers and models are not exercised",
    "hidden cases are enumerated, never sampled or weighted; a grade set is a range, not a rate",
    "the delivered value is one fixed public stand-in X; the draft case swaps the private identities, never the delivered bytes",
    "explicit null handoffs keep the unswapped identity order; the relief view carries no reference in those cases",
    "original_only is an assumed sender convention that filters hidden cases; it is not a signal in the relief view",
    "view digests cover only relief GET /brief, /status and /inbox before the decision; outgoing replies and later reads are excluded",
    "one accepted handoff and one irreversible relief move; not a bound on multi-turn team protocols",
    "archived native cuts are not read; whether their grades fall inside these sets is a separate report that needs eligible evidence",
    "a nonpass group is not assessed: its empty aggregate grades and guarantees are not proof of absence; per-case cells keep raw grades, null where no grade exists",
)

Record = dict[str, Any]

assert CHANNELS == ("deliver", "withhold") and X != Y
assert trusted.stock.PRIORS == ("committed", "dropped")
assert trusted.FATES == ("settles", "fails")


class Task(NamedTuple):
    name: str
    states: tuple[str, ...]
    actions: tuple[str, ...]


TASKS = (
    Task("stock", trusted.stock.PRIORS, ACTIONS[:4]),
    Task("inflight", trusted.FATES, ACTIONS),
)
assert tuple(task.name for task in TASKS) == tuple(PREDICTED)


class Cell(NamedTuple):
    state: str
    offer: str
    channel: str

    @property
    def label(self) -> str:
        return f"{self.state}/{self.offer}/{self.channel}"

    @property
    def view(self) -> str:
        delivered = self.channel == "deliver" and self.offer != "null"
        return "received" if delivered else "empty"


class Trial(NamedTuple):
    digest: str | None
    grade: int | None
    problem: str | None


def available(task: Task, view: str) -> tuple[str, ...]:
    assert view in VIEWS
    return tuple(act for act in task.actions if view == "received" or act not in HELD)


def cells(task: Task) -> list[Cell]:
    return [Cell(*key) for key in product(task.states, OFFERS, CHANNELS)]


def open_world(module: ModuleType, task: Task, cell: Cell) -> Any:
    request, draft = (Y, X) if cell.offer == "draft" else (X, Y)
    if task.name == "stock":
        shipment = module.Shipment(cell.state, cell.channel, RUN, request, draft)
        return module.World(shipment, dict(TOKENS), (request, draft))
    shipment = module.InflightShipment(cell.state, cell.channel, RUN, request, draft)
    return module.InflightWorld(shipment, dict(TOKENS), (request, draft))


def view_digest(replies: list[tuple[str, int, bytes]]) -> str:
    assert [path for path, _, _ in replies] == list(READS)
    frame = b"".join(
        f"GET {path} {status}\n".encode("ascii") + body + b"\n"
        for path, status, body in replies
    )
    return "sha256:" + hashlib.sha256(frame).hexdigest()


def resolve_body(action: str, received: object) -> dict[str, object]:
    assert action in ACTIONS and action != "stop"
    if action not in HELD:
        return {"action": action}
    assert isinstance(received, str)
    return {"action": action.removesuffix("_received"), "reference": received}


def native_action(task: Task, cell: Cell, action: str) -> str:
    if action == "stop":
        return "absent"
    if action not in HELD:
        return action
    assert cell.offer in ("original", "draft")
    if action == "replace_received":
        return f"replace-{cell.offer}"
    return cell.offer if task.name == "stock" else trusted.JOINS[cell.offer]


def grade(task: Task, cell: Cell, action: str, data: bytes) -> int | None:
    handoff = "none" if cell.offer == "null" else cell.offer
    expected = (cell.state, cell.channel, handoff, native_action(task, cell, action))
    axis = "prior" if task.name == "stock" else "fate"
    evaluate = trusted.stock.evaluate if task.name == "stock" else trusted.evaluate
    try:
        seen = evaluate(trusted.stock.parse_cut(data))
    except trusted.CutError:
        return None
    found = (
        seen[axis],
        seen["channel"],
        seen["outgoing_handoff"],
        seen["relief_action"],
    )
    reward = seen["reward"]
    assert type(reward) is int and reward in (0, 1)
    return reward if found == expected else None


def trial(module: ModuleType, task: Task, cell: Cell, action: str) -> Trial:
    world = open_world(module, task, cell)
    offered = None if cell.offer == "null" else X
    if world.handle("outgoing", "POST", "/handoff", {"reference": offered})[0] != 200:
        return Trial(None, None, "handoff refused")
    replies = [(path, *world.handle("relief", "GET", path, {})) for path in READS]
    if any(status != 200 for _, status, _ in replies):
        return Trial(None, None, "relief read refused")
    digest = view_digest(replies)
    inbox = json.loads(replies[-1][2])
    shown = X if cell.view == "received" else None
    if inbox != {"ready": True, "reference": shown}:
        return Trial(digest, None, "inbox differs from the condition")
    if action != "stop":
        body = resolve_body(action, inbox["reference"])
        if world.handle("relief", "POST", "/resolve", body)[0] != 200:
            return Trial(digest, None, "resolve refused")
    first, second = world.freeze(), world.freeze()
    if first[0] != 200 or first != second or not world.healthy():
        return Trial(digest, None, "freeze unstable")
    graded = grade(task, cell, action, first[1])
    if graded is None:
        return Trial(digest, None, "native verifier did not confirm the cell")
    return Trial(digest, graded, None)


def census(module: ModuleType, task: Task) -> dict[tuple[Cell, str], Trial]:
    plan = [(cell, act) for cell in cells(task) for act in available(task, cell.view)]
    require(len(plan) <= MAX_TRIALS, f"{task.name} census exceeds {MAX_TRIALS} trials")
    return {(cell, act): trial(module, task, cell, act) for cell, act in plan}


def group(
    task: Task, trials: dict[tuple[Cell, str], Trial], knowledge: str, view: str
) -> Record:
    compatible = KNOWLEDGE[knowledge]
    members = [
        cell for cell in cells(task) if cell.view == view and cell.offer in compatible
    ]
    actions = available(task, view)
    picked = {(cell, act): trials[cell, act] for cell in members for act in actions}
    assert members and len(picked) == len(members) * len(actions)
    digests = sorted(
        {found.digest for found in picked.values() if found.digest is not None}
    )
    problems = sorted(
        f"{cell.label} {act}: {found.problem}"
        for (cell, act), found in picked.items()
        if found.problem is not None
    )
    valid = len(digests) == 1 and not problems
    options: Record = {}
    for act in actions:
        raw = {cell.label: picked[cell, act].grade for cell in members}
        graded = [grade for grade in raw.values() if grade is not None]
        assert not valid or len(graded) == len(raw)
        options[act] = {"grades": sorted(set(graded)) if valid else [], "cells": raw}
    guaranteed = (
        sorted(act for act in actions if options[act]["grades"] == [1]) if valid else []
    )
    return {
        "census": "valid" if valid else "nonpass",
        "cases": [cell.label for cell in members],
        "trials": len(picked),
        "views": digests,
        "problems": problems,
        "actions": options,
        "guaranteed": guaranteed,
    }


def frontier() -> Record:
    module = verified_world()
    worlds: Record = {}
    for task in TASKS:
        trials = census(module, task)
        worlds[task.name] = {
            knowledge: {view: group(task, trials, knowledge, view) for view in VIEWS}
            for knowledge in KNOWLEDGE
        }
    return {"schema": SCHEMA, "worlds": worlds, "limits": list(LIMITS)}


def load_question() -> tuple[str, list[str]]:
    data, _ = read_plain(QUESTION_PATH)
    require(0 < len(data) <= MAX_QUESTION_BYTES, "question size is out of bounds")
    question = json.loads(data)
    require(
        isinstance(question, dict) and question.get("schema") == QUESTION_SCHEMA,
        "question schema differs",
    )
    texts = question.get("registered_predictions")
    require(
        isinstance(texts, list) and len(texts) == 3, "question lacks three predictions"
    )
    require(
        all(isinstance(text, str) for text in texts),
        "registered predictions are not text",
    )
    return "sha256:" + hashlib.sha256(data).hexdigest(), texts


def empty_options(worlds: Record) -> dict[str, Record]:
    found = {}
    for name, knowledge in product(worlds, KNOWLEDGE):
        entry = worlds[name][knowledge]["empty"]
        earns = any(1 in row["grades"] for row in entry["actions"].values())
        found[f"{name}/{knowledge}"] = {
            "guaranteed": entry["guaranteed"],
            "some_action_earns_1": earns,
        }
    return found


def predictions(worlds: Record, texts: list[str]) -> list[Record]:
    opaque = {name: worlds[name]["opaque"]["received"] for name in worlds}
    convention = {name: worlds[name]["original_only"]["received"] for name in worlds}
    blind = {name: [] for name in worlds}
    unseen = {
        name: opaque[name]["views"] == convention[name]["views"] for name in worlds
    }
    open_ended = {"guaranteed": [], "some_action_earns_1": True}
    first = {name: opaque[name]["guaranteed"] for name in worlds}
    second = {
        "guaranteed": {name: convention[name]["guaranteed"] for name in worlds},
        "convention_views_match_opaque": unseen,
    }
    third = empty_options(worlds)
    checks = (
        (blind, first, first == blind),
        (
            {
                "guaranteed": PREDICTED,
                "convention_views_match_opaque": {name: True for name in worlds},
            },
            second,
            second["guaranteed"] == PREDICTED and all(unseen.values()),
        ),
        (
            {key: open_ended for key in third},
            third,
            all(row == open_ended for row in third.values()),
        ),
    )
    return [
        {
            "id": index,
            "text": text,
            "expected": expected,
            "found": found,
            "holds": holds,
        }
        for index, (text, (expected, found, holds)) in enumerate(
            zip(texts, checks, strict=True), start=1
        )
    ]


def report() -> Record:
    question, texts = load_question()
    found = frontier()
    worlds = found["worlds"]
    groups = [
        worlds[name][knowledge][view]
        for name, knowledge, view in product(worlds, KNOWLEDGE, VIEWS)
    ]
    valid = all(entry["census"] == "valid" for entry in groups)
    checks = predictions(worlds, texts)
    holds = all(check["holds"] for check in checks)
    verdict = VERDICTS[2] if not valid else VERDICTS[0] if holds else VERDICTS[1]
    trials = {
        name: sum(worlds[name]["opaque"][view]["trials"] for view in VIEWS)
        for name in worlds
    }
    return {
        **found,
        "question": {"path": QUESTION_RELATIVE, "sha256": question},
        "trials": trials,
        "census": "valid" if valid else "nonpass",
        "predictions": checks
        if valid
        else [{**check, "holds": None} for check in checks],
        "verdict": verdict,
    }


def table(found: Record) -> list[str]:
    head = ("world", "knowledge", "view", "census", "views", "cases")
    rows = [(*head, "action", "grades", "guaranteed")]
    problems = []
    for name, knowledge, view in product(found["worlds"], KNOWLEDGE, VIEWS):
        entry = found["worlds"][name][knowledge][view]
        shown = ",".join(
            digest.removeprefix("sha256:")[:12] for digest in entry["views"]
        )
        prefix = (
            name,
            knowledge,
            view,
            entry["census"],
            shown,
            str(len(entry["cases"])),
        )
        for act, row in entry["actions"].items():
            grades = "{" + ",".join(str(value) for value in row["grades"]) + "}"
            mark = "yes" if act in entry["guaranteed"] else "no"
            rows.append((*prefix, act, grades, mark))
        problems.extend(
            f"problem {name}/{knowledge}/{view} {text}" for text in entry["problems"]
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in rows
    ]
    return [line.rstrip() for line in lines] + problems


def render(found: Record) -> str:
    counts = " ".join(f"{name}={count}" for name, count in found["trials"].items())
    holds = {True: "holds", False: "fails", None: "not assessed"}
    lines = [
        *table(found),
        "",
        f"question {found['question']['sha256']}",
        f"trials {counts}",
        f"census: {found['census']}",
        *(
            f"prediction {check['id']}: {holds[check['holds']]}"
            for check in found["predictions"]
        ),
        f"verdict: {found['verdict']}",
        *(f"limit: {limit}" for limit in found["limits"]),
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str]) -> int:
    if list(argv) not in ([], ["--json"]):
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        found = report()
    except (ValueError, OSError):
        sys.stderr.write("receiver_options: pinned source or question did not verify\n")
        return 2
    sys.stdout.write(json.dumps(found, indent=2) + "\n" if argv else render(found))
    return 0 if found["verdict"] == VERDICTS[0] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
