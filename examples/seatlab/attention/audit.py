from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from .. import evidence, run_panel, seat_agent, team_report
from ..capsule import Capsule
from ..export_capsules import reconstructed_capsules
from ..native_report import summarize_attention as summarize
from ..visibility import native_pairs
from . import protocol

__all__ = ["inspect_trial", "summarize"]

_EPISODES = 6
_ROLES = ("inventory", "dispatch")
_AGENT_IMPORT = "examples.seatlab.attention.agent:AttentionSeat"
_SEAT_SCHEMA = "seatlab.native-attention-seat/v1"
_TOKENS = ("input_tokens", "cache_tokens", "output_tokens")
_METRICS = (
    "opened_report_changes",
    "total_report_changes",
    "opened_stock_changes",
    "opened_sensitive",
    "source_copy_errors",
)


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _read_native(
    trial: Path,
    config: dict[str, Any],
    cell_id: str,
    transport: str,
    *,
    agent_import: str = _AGENT_IMPORT,
    channel_id: str | None = None,
) -> dict[str, Any]:
    declared = native_pairs.DECLARED_MODEL if transport == "direct" else None
    allowed = {"cell_id", "transport", "command_config"}
    if channel_id is not None:
        if channel_id != trial.name:
            raise ValueError("declared channel differs from this trial")
        allowed.add("channel_id")
    for participant in evidence._by_role(
        config.get("participants"), "participants"
    ).values():
        kwargs = participant.get("kwargs")
        if (
            participant.get("name") is not None
            or participant.get("import_path") != agent_import
            or participant.get("model_name") != declared
            or not isinstance(kwargs, dict)
            or not kwargs.keys() <= allowed
            or kwargs.get("channel_id") != channel_id
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


def _turn(
    raw: object, stage: str, role: str, cell_id: str, transport: str
) -> tuple[str, dict[str, Any]]:
    identity = {
        "stage": stage,
        "run": f"{cell_id}-{stage}",
        "role": role,
        "status": "received",
    }
    if not isinstance(raw, dict) or not run_panel._config_matches(raw, identity):
        raise ValueError("native model turn identity or stage mismatch")
    record = cast(dict[str, Any], raw)
    if not {"text", "response_sha256", "usage", "prompt_sha256"} <= record.keys():
        raise ValueError("missing native model turn fields")
    text = record["text"]
    response_hash = record["response_sha256"]
    if not isinstance(text, str) or response_hash != _digest(text):
        raise ValueError("native model response hash mismatch")
    usage = record["usage"]
    if transport == "direct":
        if (
            not isinstance(usage, dict)
            or usage.keys() != {"served_model", *_TOKENS}
            or not isinstance(usage["served_model"], str)
            or usage["served_model"].casefold() not in native_pairs._SERVED_ENGINES
            or any(type(usage[key]) is not int or usage[key] < 0 for key in _TOKENS)
            or usage["cache_tokens"] > usage["input_tokens"]
        ):
            raise ValueError("invalid direct model usage")
    elif usage is not None:
        raise ValueError("command model usage is not attested")
    return text, {
        **identity,
        "prompt_sha256": record["prompt_sha256"],
        "response_sha256": response_hash,
        "usage": usage,
    }


def _seat_metadata(
    record: dict[str, Any],
    capsule: Mapping[str, object],
    role: str,
    cell_id: str,
    rows: list[dict[str, Any]],
    transport: str,
    *,
    agent_name: str = "seatlab-revision-attention",
    seat_schema: str = _SEAT_SCHEMA,
) -> dict[str, Any]:
    model = {"name": "deepseek-flash", "provider": "deepseek"}
    if record.get("agent_info") != {
        "name": agent_name,
        "version": "1.0.0",
        "model_info": model if transport == "direct" else None,
    }:
        raise ValueError("unexpected native seat identity")
    metadata = record["agent_result"]["metadata"]
    if capsule["basis"] != "bound" or not run_panel._config_matches(
        metadata,
        {
            "schema": seat_schema,
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
    view = capsule["view"]
    if not isinstance(view, dict):
        raise ValueError("invalid native capsule view")
    cases = view.get("cases")
    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases):
        raise ValueError("invalid native capsule cases")
    return cast(list[dict[str, Any]], cases)


def _check_prompt(turn: dict[str, Any], prompt: str) -> None:
    if turn["prompt_sha256"] != _digest(prompt):
        raise ValueError("native model prompt hash mismatch")


def _reconcile_turns(
    records: dict[str, dict[str, Any]],
    world: dict[str, Any],
    cell: dict[str, Any],
    transport: str,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
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
    _check_prompt(select_turn, protocol.select_prompt(cases, seed, rep, cell["arm"]))
    opened = protocol.select(select_text)
    if list(opened) != dispatch["opened"]:
        raise ValueError("selector response differs from the recorded lookups")

    decide_text, decide_turn = _turn(
        dispatch["turns"][1], "decide", "dispatch", cell_id, transport
    )
    _check_prompt(decide_turn, protocol.decide_prompt(cases, seed, rep, opened))
    submitted = protocol.decide(decide_text, cases, seed, rep)
    seat_agent._checked_submission(submitted, "dispatch", _EPISODES)
    if (
        native_pairs._decision_hash("dispatch", submitted["rows"])
        != dispatch["decision_sha256"]
    ):
        raise ValueError("dispatch model response differs from accepted decisions")
    return list(opened), [inv_turn, select_turn, decide_turn], cases


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
    if transport not in ("direct", "commands") or not any(
        run_panel._config_matches(registered, cell) and registered.keys() == cell.keys()
        for registered in protocol.grid()
    ):
        raise ValueError("unregistered attention cell or transport")
    try:
        trial = trial.resolve()
        native = _read_native(trial, config, cell["cell_id"], transport)
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
        opened, turns, cases = _reconcile_turns(records, world, cell, transport)
        return {
            "trial_id": receipt["trial_id"],
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
            **_observation_metrics(world, cases, cell, opened),
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
