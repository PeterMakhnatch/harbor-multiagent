from hashlib import sha256
from pathlib import Path
from typing import Any

from .. import evidence, native_panel
from ..attention import protocol as attention
from ..briefing import audit
from ..briefing import protocol as briefing
from ..briefing.__main__ import experiment
from . import extract, protocol

_ARMS = ("aligned", "shifted", "repeat")


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _score_choices(
    world: dict[str, Any], actions: list[dict[str, Any]]
) -> dict[str, Any]:
    cases = world["cases"]
    if len(cases) != len(actions):
        raise ValueError("actions do not cover the native cases")
    chosen = []
    for case, action in zip(cases, actions, strict=True):
        if type(action["id"]) is not int or action["id"] != case["id"]:
            raise ValueError("actions do not match the native case IDs")
        chosen.append(case | {"chosen": action["choice"]})
    return evidence._score(world | {"cases": chosen})


def _original_score(
    source: dict[str, Any],
    native: dict[str, Any],
    observed: dict[str, Any],
    world: dict[str, Any],
) -> dict[str, Any]:
    seats = evidence._by_role(native["seat_results"], "seat_results")
    push, decide = (
        seats[role]["agent_result"]["metadata"]["turns"][1]
        for role in ("inventory", "dispatch")
    )
    opened = protocol.parse_push(push["text"])
    prompts = (
        protocol.push_prompt(source, "aligned"),
        protocol.decide_prompt(source, opened),
    )
    for prompt, raw, turn in zip(
        prompts, (push, decide), observed["turns"][2:], strict=True
    ):
        if (
            _hash(prompt) != turn["prompt_sha256"]
            or raw["prompt_sha256"] != turn["prompt_sha256"]
            or _hash(raw["text"]) != turn["response_sha256"]
        ):
            raise ValueError(
                "aligned prompts or responses differ from the original turns"
            )
    choices = attention.decide(
        decide["text"], world["cases"], source["seed"], source["repetition"]
    )["rows"]
    actions = [{"id": row["id"], "choice": row["bay"]} for row in choices]
    if actions != [
        {"id": case["id"], "choice": case["chosen"]} for case in world["cases"]
    ]:
        raise ValueError("original dispatch response differs from the native actions")
    return _score_choices(world, actions)


def _load_source(
    root: Path,
    plan: dict[str, Any],
    cell: dict[str, Any],
    source: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    directory = native_panel._cell_dir(root, cell)
    teams = native_panel.teams
    teams._check_path(directory, 0o700, directory=True)
    config = native_panel._config(experiment(), root, cell, "direct")
    for name, expected in (
        ("attempt.json", cell),
        ("config.json", config),
        ("outcome.json", {"returncode": 0, "launch_error": None}),
    ):
        if extract._sealed(directory / name) != teams._bytes(expected):
            raise ValueError("native source does not match its sealed registration")
    extract._task_inputs(directory / "dispatch-handoff", cell, plan)
    trial = extract._trial(config)
    if trial is None:
        raise ValueError("registered native source trial is absent")
    native = extract._native_json(trial / "result.json")
    extract._native_json(trial / "config.json")
    bounded_world = extract._native_json(trial / "artifacts/logs/world/handoff.json")
    receipt, world = evidence.read_trial_with_world(trial)
    if (
        receipt["trial_id"] != source["trial_id"]
        or receipt["fingerprint"] != source["world_sha256"]
        or world != bounded_world
    ):
        raise ValueError("native source trial or world differs from the packet")
    observed = audit.inspect_trial(trial, config, cell, "direct")
    reconstructed = extract._source(cell, (observed, native, world))
    if extract._canonical(reconstructed) != extract._canonical(source):
        raise ValueError(
            "native source inputs or upstream hashes differ from the packet"
        )
    original = _original_score(source, native, observed, world)
    if original != receipt["score"]:
        raise ValueError("original stock score differs from the native receipt")
    return world, original


def _sensitive_count(world: dict[str, Any], row: dict[str, Any]) -> int:
    cases = world["cases"]
    aliases = attention.aliases(cases, row["seed"], row["repetition"])
    opened = attention._open_pair(row["outcome"]["opened"])
    count = 0
    for alias in opened:
        case = cases[aliases[alias]]
        ranks = case["utilities"].__getitem__
        previous, recent = case["reports"]
        count += max(map(ranks, previous["available"])) != max(
            map(ranks, recent["available"])
        )
    return count


def _verify_outcome(world: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    outcome = row["outcome"]
    actions = outcome["actions"]
    score = _score_choices(world, actions)
    for actual, expected in zip(actions, score["rows"], strict=True):
        if any(actual[key] is not expected[key] for key in ("optimal", "feasible")):
            raise ValueError("fork action grades differ from the native stock score")
    for field, expected in (
        ("optimal", score["optimal"]),
        ("feasible", score["feasible"]),
        ("opened_sensitive", _sensitive_count(world, row)),
    ):
        if type(outcome[field]) is not int or outcome[field] != expected:
            raise ValueError("fork outcome totals differ from the native stock score")
    return score


def compare(
    packet: dict[str, Any], report: dict[str, Any], native_study: Path
) -> dict[str, Any]:
    root = native_study.absolute()
    if root != root.resolve(strict=True):
        raise ValueError("use the physical path of the original native study")
    cells = list(briefing.grid())
    plan = extract._study_plan(root, cells)
    brief = {
        (cell["seed"], cell["repetition"]): cell
        for cell in cells
        if cell["arm"] == "brief"
    }
    worlds = {}
    original_actions = original_optimal = original_feasible = 0
    for source in packet["sources"]:
        cell = brief[source["seed"], source["repetition"]]
        world, original = _load_source(root, plan, cell, source)
        worlds[source["source_id"]] = world
        original_actions += original["episodes"]
        original_optimal += original["optimal"]
        original_feasible += original["feasible"]
    arms = {
        arm: dict.fromkeys(
            (
                "complete",
                "incomplete",
                "unclaimed",
                "no_source",
                "actions",
                "optimal",
                "feasible",
            ),
            0,
        )
        for arm in _ARMS
    }
    for row in report["rows"]:
        state = row["state"]
        counts = arms[row["arm"]]
        counts[state] += 1
        if state == "complete":
            score = _verify_outcome(worlds[row["source_id"]], row)
            counts["actions"] += score["episodes"]
            counts["optimal"] += score["optimal"]
            counts["feasible"] += score["feasible"]
    return {
        "schema": "seatlab.turn-fork-stock/v1",
        "measurement": "offline_rescore_not_new_native_rewards",
        "source_turns_bound": 2 * len(worlds),
        "original_prompts_bound": 2 * len(worlds),
        "original_actions_bound": original_actions,
        "original_optimal": original_optimal,
        "original_feasible": original_feasible,
        "arms": arms,
        "mismatches": 0,
    }
