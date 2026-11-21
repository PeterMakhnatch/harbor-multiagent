from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..briefing import protocol as briefing
from . import inputs


def _native_sources(
    sources: dict[str, dict[str, Any]], native_study: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    from ..turn_fork import extract, stock

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
    totals = dict.fromkeys(("episodes", "optimal", "feasible"), 0)
    for source_id, source in sources.items():
        cell = brief[source["seed"], source["repetition"]]
        world, original = stock._load_source(root, plan, cell, source)
        worlds[source_id] = world
        for key in totals:
            totals[key] += original[key]
    return worlds, totals


def _observations(
    packet: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    board: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    observations = {}
    for observation in board["observations"]:
        source_id = observation["source_id"]
        obs_id = observation["obs_id"]
        if source_id not in sources or obs_id in observations:
            raise ValueError("observation does not join to one enrolled native source")
        prompt_sha256 = sha256(inputs.prompt(packet, observation).encode()).hexdigest()
        if (
            observation["prompt_sha256"] != prompt_sha256
            or obs_id != f"{source_id}-{prompt_sha256[:12]}"
        ):
            raise ValueError("observation does not match its dispatch prompt")
        observations[obs_id] = observation
    return observations


def _weights(
    observations: dict[str, dict[str, Any]], board: dict[str, Any]
) -> tuple[Counter[str], int]:
    weights: Counter[str] = Counter()
    excluded = 0
    for selection in board["selections"]:
        obs_id = selection["obs_id"]
        if obs_id is None:
            excluded += 2
            continue
        observation = observations.get(obs_id)
        if observation is None or observation["source_id"] != selection["source_id"]:
            raise ValueError("selection does not match its observation source")
        weights[obs_id] += 1
    if weights.keys() != observations.keys():
        raise ValueError("observation has no registered selection")
    return weights, excluded


def _draw_score(
    row: dict[str, Any],
    observation: dict[str, Any],
    source: dict[str, Any],
    world: dict[str, Any],
) -> dict[str, Any] | None:
    from ..turn_fork import stock

    if (
        row["source_id"] != observation["source_id"]
        or row["prompt_sha256"] != observation["prompt_sha256"]
        or (
            row["turn"] is not None
            and row["turn"]["prompt_sha256"] != row["prompt_sha256"]
        )
    ):
        raise ValueError("dispatch draw does not match its observation")
    if row["state"] != "complete":
        return None
    outcome = row["outcome"]
    if outcome["opened"] != observation["opened"]:
        raise ValueError("dispatch outcome does not match its opened reports")
    return stock._verify_outcome(
        world,
        {
            "outcome": outcome,
            "seed": source["seed"],
            "repetition": source["repetition"],
        },
    )


def compare(
    packet: dict[str, Any],
    board: dict[str, Any],
    report: dict[str, Any],
    native_study: Path,
) -> dict[str, Any]:
    sources = inputs.source_index(packet)
    worlds, original = _native_sources(sources, native_study)
    observations = _observations(packet, sources, board)
    weights, excluded = _weights(observations, board)
    unique = dict.fromkeys(("complete", "incomplete", "unclaimed"), 0)
    logical = dict(unique, registered=2 * len(board["selections"]), excluded=excluded)
    logical["assigned"] = logical["registered"] - excluded
    unique.update(scheduled=2 * len(observations), actions=0, optimal=0, feasible=0)
    seen: set[tuple[str, int]] = set()
    for row in report["rows"]:
        obs_id, draw = row["obs_id"], row["draw"]
        observation = observations.get(obs_id)
        if observation is None or draw not in (0, 1) or (obs_id, draw) in seen:
            raise ValueError("unexpected or repeated dispatch draw")
        seen.add((obs_id, draw))
        source_id = observation["source_id"]
        score = _draw_score(row, observation, sources[source_id], worlds[source_id])
        state = row["state"]
        unique[state] += 1
        logical[state] += weights[obs_id]
        if score is not None:
            for key, field in (
                ("actions", "episodes"),
                ("optimal", "optimal"),
                ("feasible", "feasible"),
            ):
                unique[key] += score[field]
    if len(seen) != 2 * len(observations):
        raise ValueError("report does not cover both registered dispatch draws")
    return {
        "schema": "seatlab.decision-cross-stock/v1",
        "measurement": "offline_rescore_not_new_native_rewards",
        "board_sha256": report["board_sha256"],
        "transport": report["transport"],
        "source_turns_bound": 2 * len(worlds),
        "original_prompts_bound": 2 * len(worlds),
        "original_actions_bound": original["episodes"],
        "original_optimal": original["optimal"],
        "original_feasible": original["feasible"],
        "unique_draws": unique,
        "logical_uses": logical,
        "observation_weights": dict(weights),
    }
