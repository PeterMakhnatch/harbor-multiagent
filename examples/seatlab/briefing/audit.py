from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import evidence, run_panel, seat_agent, team_report
from ..attention import audit as attention
from ..attention import protocol as attention_protocol
from ..export_capsules import reconstructed_capsules
from ..native_report import summarize_briefing as summarize
from ..visibility import native_pairs
from . import protocol

__all__ = ["inspect_trial", "summarize"]

_EPISODES = 6
_AGENT_IMPORT = "examples.seatlab.briefing.agent:BriefingSeat"
_AGENT_NAME = "seatlab-dispatch-briefing"
_SEAT_SCHEMA = "seatlab.native-briefing-seat/v1"
_SEAT_FIELDS = {
    "schema",
    "cell_id",
    "role",
    "stage",
    "view_sha256",
    "decision_sha256",
    "opened",
    "turns",
}
_TURN_FIELDS = {
    "stage",
    "run",
    "role",
    "status",
    "prompt_sha256",
    "response_sha256",
    "usage",
    "text",
}


def _seat_turns(
    record: dict[str, Any],
    capsule: dict[str, Any],
    world: dict[str, Any],
    role: str,
    cell_id: str,
    transport: str,
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    if role == "inventory":
        rows = [
            {"id": case["id"], "reports": case["reports"]} for case in world["cases"]
        ]
        stages, card_field = ("inventory", "push"), "brief_seen"
    else:
        rows = [{"id": case["id"], "bay": case["chosen"]} for case in world["cases"]]
        stages, card_field = ("brief", "decide"), "brief"
    metadata = attention._seat_metadata(
        record,
        capsule,
        role,
        cell_id,
        rows,
        transport,
        agent_name=_AGENT_NAME,
        seat_schema=_SEAT_SCHEMA,
    )
    if metadata.keys() != _SEAT_FIELDS | {card_field}:
        raise ValueError("unexpected native seat metadata fields")
    turns = metadata["turns"]
    if not isinstance(turns, list) or len(turns) != len(stages):
        raise ValueError("expected exactly two native model turns per seat")
    if any(not isinstance(turn, dict) or turn.keys() != _TURN_FIELDS for turn in turns):
        raise ValueError("unexpected native model turn fields")
    return metadata, [
        attention._turn(turn, stage, role, cell_id, transport)
        for turn, stage in zip(turns, stages, strict=True)
    ]


def _brief_correct(cases: list[dict[str, Any]], seed: int, rep: int, card: str) -> int:
    by_id = {case["id"]: case["utilities"] for case in cases}
    aliases = attention_protocol.aliases(cases, seed, rep)
    return sum(
        by_id[native_id][bay] == max(by_id[native_id].values())
        for native_id, bay in zip(aliases.values(), card, strict=True)
    )


def _reconcile_turns(
    records: dict[str, dict[str, Any]],
    world: dict[str, Any],
    capsules: dict[str, Any],
    cell: dict[str, Any],
    transport: str,
) -> tuple[list[str], list[dict[str, Any]], int]:
    cell_id, seed, rep = cell["cell_id"], cell["seed"], cell["repetition"]
    inventory, ((source_text, source_turn), (push_text, push_turn)) = _seat_turns(
        records["inventory"],
        capsules["inventory"],
        world,
        "inventory",
        cell_id,
        transport,
    )
    dispatch, ((brief_text, brief_turn), (decide_text, decide_turn)) = _seat_turns(
        records["dispatch"], capsules["dispatch"], world, "dispatch", cell_id, transport
    )
    attention._check_prompt(
        source_turn,
        native_pairs._prompt("inventory", capsules["inventory"]["view"]["cases"]),
    )
    source = seat_agent._json_object(source_text, "inventory model")
    seat_agent._checked_submission(source, "inventory", _EPISODES)
    if (
        native_pairs._decision_hash("inventory", source["rows"])
        != inventory["decision_sha256"]
    ):
        raise ValueError("inventory model response differs from accepted reports")

    cases = capsules["dispatch"]["view"]["cases"]
    attention._check_prompt(brief_turn, protocol.brief_prompt(cases, seed, rep))
    card = protocol.parse_brief(brief_text)
    released = card if cell["arm"] == "brief" else None
    if dispatch["brief"] != card or inventory["brief_seen"] != released:
        raise ValueError("brief response or delivery differs from the registered arm")

    attention._check_prompt(
        push_turn, protocol.push_prompt(source["rows"], seed, rep, released)
    )
    opened = protocol.parse_push(push_text)
    selected = list(opened)
    if inventory["opened"] != selected or dispatch["opened"] != selected:
        raise ValueError("push response differs from the recorded lookups")

    attention._check_prompt(
        decide_turn, attention_protocol.decide_prompt(cases, seed, rep, opened)
    )
    choices = attention_protocol.decide(decide_text, cases, seed, rep)
    seat_agent._checked_submission(choices, "dispatch", _EPISODES)
    if (
        native_pairs._decision_hash("dispatch", choices["rows"])
        != dispatch["decision_sha256"]
    ):
        raise ValueError("dispatch model response differs from accepted decisions")
    return (
        selected,
        [source_turn, brief_turn, push_turn, decide_turn],
        _brief_correct(cases, seed, rep, card),
    )


def inspect_trial(
    trial: Path, config: dict[str, Any], cell: dict[str, Any], transport: str
) -> dict[str, Any]:
    if transport not in ("direct", "commands") or not any(
        run_panel._config_matches(registered, cell) and registered.keys() == cell.keys()
        for registered in protocol.grid()
    ):
        raise ValueError("unregistered briefing cell or transport")
    try:
        trial = trial.resolve()
        native = attention._read_native(
            trial,
            config,
            cell["cell_id"],
            transport,
            agent_import=_AGENT_IMPORT,
            channel_id=trial.name,
        )
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
        capsules = reconstructed_capsules(world)
        opened, turns, brief_correct = _reconcile_turns(
            records, world, capsules, cell, transport
        )
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
            **attention._observation_metrics(
                world, capsules["dispatch"]["view"]["cases"], cell, opened
            ),
            "brief_correct": brief_correct,
            "brief_slots": _EPISODES,
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
            "native briefing evidence is incomplete or inconsistent"
        ) from None
