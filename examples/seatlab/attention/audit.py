from __future__ import annotations

from collections import Counter
from hashlib import sha256
from math import fsum
from pathlib import Path
from re import fullmatch
from runpy import run_path
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from .. import evidence, run_panel, seat_agent, team_report
from ..capsule import Capsule, view_hash
from ..export_capsules import reconstructed_capsules
from ..visibility import native_pairs
from . import confirmation, evidence_join, join_summary, protocol

_EPISODES = 6
_ROLES = ("inventory", "dispatch")
_ARMS = ("named", "count")
_AGENT_IMPORT = "examples.seatlab.attention.agent:AttentionSeat"
_SEAT_SCHEMA = "seatlab.native-attention-seat/v1"
_TOKENS = ("input_tokens", "cache_tokens", "output_tokens")
_TURN_STAGES = (
    ("inventory", "inventory"),
    ("select", "dispatch"),
    ("decide", "dispatch"),
)
_METRICS = (
    "opened_report_changes",
    "total_report_changes",
    "opened_stock_changes",
    "opened_sensitive",
    "source_copy_errors",
)
_JOIN_STATES = {
    "unclaimed",
    "reserved_without_trial",
    "claimed",
    "native_failed",
    "unverified",
    "complete",
}
_JOIN_OUTCOME_FIELDS = {
    "trial_id",
    "fingerprint",
    "native_score",
    "actions",
    "opened",
    "turns",
    *_METRICS,
}
_TURN_FIELDS = {
    "stage",
    "run",
    "role",
    "status",
    "prompt_sha256",
    "response_sha256",
    "usage",
}


def _agent_import(cell_id: str) -> str:
    if cell_id.startswith(evidence_join.RUN_PREFIX):
        return evidence_join.AGENT_IMPORT
    if cell_id.startswith(confirmation.RUN_PREFIX):
        return confirmation.AGENT_IMPORT
    return _AGENT_IMPORT


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _read_native(
    trial: Path, config: dict[str, Any], cell_id: str, transport: str
) -> dict[str, Any]:
    declared = native_pairs.DECLARED_MODEL if transport == "direct" else None
    for participant in evidence._by_role(
        config.get("participants"), "participants"
    ).values():
        kwargs = participant.get("kwargs")
        if (
            participant.get("name") is not None
            or participant.get("import_path") != _agent_import(cell_id)
            or participant.get("model_name") != declared
            or not isinstance(kwargs, dict)
            or not kwargs.keys() <= {"cell_id", "transport", "command_config"}
            or kwargs.get("cell_id") != cell_id
            or kwargs.get("transport") != transport
        ):
            raise ValueError("native participant differs from the registered seat")
        commands = kwargs.get("command_config")
        if transport == "direct" and commands is not None:
            raise ValueError("direct transport cannot use a command config")
        if transport == "commands" and (not isinstance(commands, str) or not commands):
            raise ValueError("command transport requires an explicit command config")

    name = config["trial_name"]
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError("invalid registered trial name")
    if trial != Path(config["trials_dir"]).resolve() / name:
        raise ValueError("native trial path differs from the registered path")
    result_path = trial / "result.json"
    config_path = trial / "config.json"
    dossier_path = trial / "artifacts/logs/world/handoff.json"
    if not all(
        team_report._within_trial(path, trial)
        for path in (result_path, config_path, dossier_path)
    ):
        raise ValueError("native outputs must remain inside the trial")
    native = team_report._read_object(result_path)
    if (
        native.get("trial_name") != name
        or native.get("trial_uri") != trial.as_uri()
        or native.get("task_id") != {"path": config["task"]["path"]}
    ):
        raise ValueError("native result belongs to a different trial")
    team_report._check_config(native.get("config"), config)
    team_report._check_config(team_report._read_object(config_path), config)
    return native


def _turn_identity(stage: str, role: str, cell_id: str) -> dict[str, str]:
    return {
        "stage": stage,
        "run": f"{cell_id}-{stage}",
        "role": role,
        "status": "received",
    }


def _turn(
    record: object, stage: str, role: str, cell_id: str, transport: str
) -> tuple[str, dict[str, Any]]:
    identity = _turn_identity(stage, role, cell_id)
    if not isinstance(record, dict) or not run_panel._config_matches(record, identity):
        raise ValueError("native model turn identity or stage mismatch")
    record = cast(dict[str, Any], record)
    text = record["text"]
    response_hash = record["response_sha256"]
    if not isinstance(text, str) or response_hash != _digest(text):
        raise ValueError("native model response hash mismatch")
    usage = record["usage"]
    _check_usage(usage, transport)
    return text, {
        **identity,
        "prompt_sha256": record["prompt_sha256"],
        "response_sha256": response_hash,
        "usage": usage,
    }


def _check_usage(usage: object, transport: str) -> None:
    if transport != "direct":
        if usage is not None:
            raise ValueError("command model usage is not attested")
        return
    if not isinstance(usage, dict) or usage.keys() != {"served_model", *_TOKENS}:
        raise ValueError("invalid direct model usage")
    usage = cast(dict[str, Any], usage)
    served_model = usage["served_model"]
    input_tokens, cache_tokens, output_tokens = (usage[key] for key in _TOKENS)
    if (
        not isinstance(served_model, str)
        or served_model.casefold() not in native_pairs._SERVED_ENGINES
        or type(input_tokens) is not int
        or type(cache_tokens) is not int
        or type(output_tokens) is not int
        or not 0 <= cache_tokens <= input_tokens
        or output_tokens < 0
    ):
        raise ValueError("invalid direct model usage")


def _seat_metadata(
    record: dict[str, Any],
    capsule: Capsule,
    role: str,
    cell_id: str,
    rows: list[dict[str, Any]],
    transport: str,
) -> dict[str, Any]:
    model = {"name": "deepseek-flash", "provider": "deepseek"}
    if record.get("agent_info") != {
        "name": "seatlab-revision-attention",
        "version": "1.0.0",
        "model_info": model if transport == "direct" else None,
    }:
        raise ValueError("unexpected native seat identity")
    metadata = record["agent_result"]["metadata"]
    if capsule["basis"] != "bound" or not run_panel._config_matches(
        metadata,
        {
            "schema": _SEAT_SCHEMA,
            "cell_id": cell_id,
            "role": role,
            "stage": "complete",
            "view_sha256": capsule["sha256"],
            "decision_sha256": native_pairs._decision_hash(role, rows),
        },
    ):
        raise ValueError("native seat did not attest a complete bound submission")
    return metadata


def _capsule_cases(capsule: Capsule) -> list[dict[str, Any]]:
    return cast(dict[str, Any], capsule["view"])["cases"]


def _check_prompt(turn: dict[str, Any], prompt: str) -> None:
    if turn["prompt_sha256"] != _digest(prompt):
        raise ValueError("native model prompt hash mismatch")


def _reconcile_turns(
    records: dict[str, dict[str, Any]],
    world: dict[str, Any],
    cell: dict[str, Any],
    transport: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    cell_id, seed, rep = cell["cell_id"], cell["seed"], cell["repetition"]
    capsules = reconstructed_capsules(world)
    inventory_rows = [
        {"id": case["id"], "reports": case["reports"]} for case in world["cases"]
    ]
    dispatch_rows = [
        {"id": case["id"], "bay": case["chosen"]} for case in world["cases"]
    ]
    inventory = _seat_metadata(
        records["inventory"],
        capsules["inventory"],
        "inventory",
        cell_id,
        inventory_rows,
        transport,
    )
    dispatch = _seat_metadata(
        records["dispatch"],
        capsules["dispatch"],
        "dispatch",
        cell_id,
        dispatch_rows,
        transport,
    )
    if (
        not isinstance(inventory["turns"], list)
        or len(inventory["turns"]) != 1
        or inventory["opened"] not in (None, [])
        or not isinstance(dispatch["turns"], list)
        or len(dispatch["turns"]) != 2
    ):
        raise ValueError("expected exactly one inventory and two dispatch turns")

    inventory_text, inv_turn = _turn(
        inventory["turns"][0], "inventory", "inventory", cell_id, transport
    )
    _check_prompt(
        inv_turn,
        native_pairs._prompt("inventory", _capsule_cases(capsules["inventory"])),
    )
    submitted = seat_agent._json_object(inventory_text, "inventory model")
    seat_agent._checked_submission(submitted, "inventory", _EPISODES)
    if (
        native_pairs._decision_hash("inventory", submitted["rows"])
        != inventory["decision_sha256"]
    ):
        raise ValueError("inventory model response differs from accepted reports")

    cases = _capsule_cases(capsules["dispatch"])
    select_text, select_turn = _turn(
        dispatch["turns"][0], "select", "dispatch", cell_id, transport
    )
    join_cohort = cell_id.startswith(evidence_join.RUN_PREFIX)
    selector_arm = "named" if join_cohort else cell["arm"]
    _check_prompt(select_turn, protocol.select_prompt(cases, seed, rep, selector_arm))
    opened = protocol.select(select_text)
    if list(opened) != dispatch["opened"]:
        raise ValueError("selector response differs from the recorded lookups")

    decide_text, decide_turn = _turn(
        dispatch["turns"][1], "decide", "dispatch", cell_id, transport
    )
    prompt = protocol.decide_prompt(cases, seed, rep, opened)
    if join_cohort:
        prompt = evidence_join.project(prompt, cell["arm"])
    _check_prompt(decide_turn, prompt)
    submitted = protocol.decide(decide_text, cases, seed, rep)
    seat_agent._checked_submission(submitted, "dispatch", _EPISODES)
    if (
        native_pairs._decision_hash("dispatch", submitted["rows"])
        != dispatch["decision_sha256"]
    ):
        raise ValueError("dispatch model response differs from accepted decisions")
    return list(opened), [inv_turn, select_turn, decide_turn]


def _observation_metrics(
    world: dict[str, Any],
    dispatch_cases: list[dict[str, Any]],
    cell: dict[str, Any],
    opened: list[str],
) -> dict[str, int]:
    aliases = protocol.aliases(dispatch_cases, cell["seed"], cell["repetition"])
    opened_ids = {aliases[alias] for alias in opened}
    counts = dict.fromkeys(_METRICS, 0)
    for case in world["cases"]:
        sent_before, sent_after = (
            set(report["available"]) for report in case["reports"]
        )
        before, after = set(case["before"]), set(case["after"])
        report_changed = sent_before != sent_after
        counts["total_report_changes"] += report_changed
        counts["source_copy_errors"] += sent_before != before or sent_after != after
        if case["id"] in opened_ids:
            utility = case["utilities"].__getitem__
            counts["opened_report_changes"] += report_changed
            counts["opened_stock_changes"] += before != after
            counts["opened_sensitive"] += max(before, key=utility) != max(
                after, key=utility
            )
    return counts


def inspect_trial(
    trial: Path, config: dict[str, Any], cell: dict[str, Any], transport: str
) -> dict[str, Any]:
    cell_id = cell.get("cell_id")
    if not isinstance(cell_id, str):
        raise TypeError("attention cell id must be a string")
    confirmation_cohort = cell_id.startswith(confirmation.RUN_PREFIX)
    if cell_id.startswith(evidence_join.RUN_PREFIX):
        cells = evidence_join.grid()
    else:
        cells = confirmation.grid() if confirmation_cohort else protocol.grid()
    transports = ("direct",) if confirmation_cohort else ("direct", "commands")
    if transport not in transports or not any(
        run_panel._config_matches(registered, cell) and registered.keys() == cell.keys()
        for registered in cells
    ):
        raise ValueError("unregistered attention cell or transport")
    try:
        trial = trial.resolve()
        native = _read_native(trial, config, cell_id, transport)
        dossier = team_report._read_object(trial / "artifacts/logs/world/handoff.json")
        receipt, world = evidence.read_trial_with_world(trial)
        score = receipt["score"]
        if (
            world != dossier
            or receipt["trial_id"] != native["id"]
            or receipt["seed"] != cell["seed"]
            or receipt["condition"] != cell["condition"]
            or score["episodes"] != _EPISODES
            or receipt["fingerprint"]
            != team_report._registered_fingerprint(cell["seed"], _EPISODES)
        ):
            raise ValueError("native world differs from the registered world")
        records = evidence._by_role(native.get("seat_results"), "seat_results")
        opened, turns = _reconcile_turns(records, world, cell, transport)
        trial_id = receipt["trial_id"]
        if confirmation_cohort:
            alias = uuid5(NAMESPACE_URL, f"{confirmation.REPORT_SCHEMA}:{trial_id}")
            identity = {"trial_alias": str(alias)}
        else:
            identity = {"trial_id": trial_id}
        return {
            **identity,
            "fingerprint": receipt["fingerprint"],
            "native_score": score["optimal"] / _EPISODES,
            "actions": [
                {
                    "id": grade["id"],
                    "choice": case["chosen"],
                    "optimal": grade["optimal"],
                    "feasible": grade["feasible"],
                }
                for grade, case in zip(score["rows"], world["cases"], strict=True)
            ],
            "opened": opened,
            **_observation_metrics(
                world,
                _capsule_cases(reconstructed_capsules(world)["dispatch"]),
                cell,
                opened,
            ),
            "turns": turns,
        }
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        RecursionError,
        seat_agent._Unmeasured,
    ):
        raise ValueError(
            "native attention evidence is incomplete or inconsistent"
        ) from None


def _index(
    registered: dict[str, dict[str, Any]],
    entries: list[dict[str, Any]],
    *,
    outcomes: bool,
) -> dict[str, dict[str, Any]]:
    if len(entries) != len(registered):
        raise ValueError("expected exactly the registered attention cells")
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        cell_id = entry.get("cell_id") if isinstance(entry, dict) else None
        if (
            not isinstance(cell_id, str)
            or cell_id not in registered
            or cell_id in indexed
            or not run_panel._config_matches(entry, registered[cell_id])
        ):
            raise ValueError("unknown, duplicate, or mislabelled attention cell")
        if outcomes and (
            not isinstance(entry.get("state"), str)
            or not entry["state"]
            or "outcome" not in entry
            or (entry["outcome"] is not None and not isinstance(entry["outcome"], dict))
        ):
            raise ValueError(
                "each cell requires a state and an explicit outcome or null"
            )
        indexed[cell_id] = entry
    return indexed


def _score_index(
    rows: dict[str, dict[str, Any]], *, confirmation_cohort: bool
) -> dict[tuple[int, int, str], float | None]:
    scores: dict[tuple[int, int, str], float | None] = {}
    fingerprints: dict[int, str] = {}
    trial_ids: set[str] = set()
    identity_key = "trial_alias" if confirmation_cohort else "trial_id"
    for cell_id, row in rows.items():
        seed = row["seed"]
        outcome = row["outcome"]
        score = None
        if outcome is not None:
            score = outcome["native_score"]
            trial_id = evidence._trial_id({"id": outcome[identity_key]})
            fingerprint = outcome["fingerprint"]
            if confirmation_cohort and (
                "trial_id" in outcome
                or fingerprint != team_report._registered_fingerprint(seed, _EPISODES)
            ):
                raise ValueError("outcome does not identify the registered world")
            if (
                type(score) not in (int, float)
                or not 0 <= score <= 1
                or trial_id in trial_ids
                or fingerprints.setdefault(seed, fingerprint) != fingerprint
                or [turn["run"] for turn in outcome["turns"]]
                != [f"{cell_id}-{stage}" for stage in ("inventory", "select", "decide")]
            ):
                raise ValueError(
                    "outcome does not identify an independent registered trial"
                )
            trial_ids.add(trial_id)
        scores[seed, row["repetition"], row["arm"]] = score
    return scores


def _primary(scores: dict[tuple[int, int, str], float | None]) -> dict[str, Any]:
    seeds = sorted({seed for seed, _, _ in scores})
    repetitions = sorted({rep for _, rep, _ in scores})
    seed_blocks = []
    lower, upper = [], []
    for seed in seeds:
        deltas = []
        for rep in repetitions:
            named, count = (scores[seed, rep, arm] for arm in _ARMS)
            if named is not None and count is not None:
                deltas.append(named - count)
            lower.append(
                (0.0 if named is None else named) - (1.0 if count is None else count)
            )
            upper.append(
                (1.0 if named is None else named) - (0.0 if count is None else count)
            )
        seed_blocks.append(
            {
                "seed": seed,
                "matched_pairs": len(deltas),
                "mean": fsum(deltas) / len(deltas) if deltas else None,
            }
        )
    means = [block["mean"] for block in seed_blocks if block["mean"] is not None]
    pair_count = len(lower)
    return {
        "contrast": "named_minus_count_native_score",
        "estimate": fsum(means) / len(seeds) if len(means) == len(seeds) else None,
        "matched_pairs": sum(block["matched_pairs"] for block in seed_blocks),
        "registered_pairs": pair_count,
        "registered_seeds": len(seeds),
        "paired_seeds": [
            block["seed"] for block in seed_blocks if block["matched_pairs"]
        ],
        "seed_blocks": seed_blocks,
        "registered_bounds": {
            "lower": fsum(lower) / pair_count,
            "upper": fsum(upper) / pair_count,
        },
    }


def _secondary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = dict.fromkeys(_METRICS, 0)
    slots = optimal = feasible = cases = unreported = 0
    source_models: Counter[str] = Counter()
    for outcome in rows:
        for field in counts:
            counts[field] += outcome[field]
        slots += len(outcome["opened"])
        cases += len(outcome["actions"])
        optimal += sum(action["optimal"] for action in outcome["actions"])
        feasible += sum(action["feasible"] for action in outcome["actions"])
        usage = outcome["turns"][0]["usage"]
        if usage is None:
            unreported += 1
        else:
            source_models[usage["served_model"]] += 1
    return {
        "observed_cells": len(rows),
        "opened_slots": slots,
        **counts,
        "opened_report_change_rate": counts["opened_report_changes"] / slots
        if slots
        else None,
        "cases": cases,
        "optimal": optimal,
        "feasible": feasible,
        "inventory_served_models": dict(source_models),
        "inventory_model_unreported": unreported,
    }


def summarize(
    cells: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    confirmation_cohort: bool = False,
    evidence_join_cohort: bool = False,
) -> dict[str, Any]:
    if evidence_join_cohort:
        if confirmation_cohort:
            raise ValueError("cannot combine registered attention cohorts")
        return _summarize_join(cells, rows)

    grid = confirmation.grid() if confirmation_cohort else protocol.grid()
    registered = {cell["cell_id"]: cell for cell in grid}
    _index(registered, cells, outcomes=False)
    indexed = _index(registered, rows, outcomes=True)
    scores = _score_index(indexed, confirmation_cohort=confirmation_cohort)
    observed = sum(score is not None for score in scores.values())
    return {
        "registered_cells": len(registered),
        "observed_cells": observed,
        "missing_cells": len(registered) - observed,
        "primary": _primary(scores),
        "secondary": {
            arm: _secondary(
                [
                    row["outcome"]
                    for row in indexed.values()
                    if row["arm"] == arm and row["outcome"] is not None
                ]
            )
            for arm in _ARMS
        },
    }
