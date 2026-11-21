import json
import os
import re
import stat
from collections.abc import Collection
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any
from uuid import UUID

from .capsule import _reject_constant, _unique_object, encode_view, view_hash

SOURCE_SHA256 = "86eaf01d4a5306ed9961bd1d39c5a4e8feff113fdec6be795e23a6c28f217c40"
_ROOT = Path(__file__).resolve().parent
_RECEIPT = _ROOT / "results/live-confirmation.json"
_WORLD = _ROOT.parent / "tasks/dispatch-handoff/environment/seatworld.py"
_SEEDS = range(6801, 6809)
_ALIASES = tuple(f"Q{i}" for i in range(1, 7))
_BAYS = frozenset("ABCD")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_BYTES = 8 * 1024 * 1024
_AGENT = "examples.seatlab.attention.confirmation_agent:ConfirmationSeat"
_SEAT_INFO = {
    "name": "seatlab-revision-attention",
    "version": "1.0.0",
    "model_info": {"name": "deepseek-flash", "provider": "deepseek"},
}
_STAGES = {"inventory": ("inventory",), "dispatch": ("select", "decide")}
_SELECT_RULES = (
    "Choose 2 cases whose revision 2 reports would help you most to dispatch all 6 "
    "shipments. The goal is to choose the available bay with the highest utility "
    "in each case. Higher numbers mean greater utility. The change summary "
    "compares availability sets in the two submitted inventory reports. "
    'Reply with only a JSON object, for example {"open":["Q1","Q2"]}. '
    "Use exactly 2 distinct case aliases from the data."
)
_DECIDE_RULES = (
    "Choose the available bay with the highest utility in each case. Higher numbers "
    "mean greater utility. Use the latest inventory report shown for each case. "
    "Reply with only a JSON object of this form: "
    '{"choices":{"Q1":"A","Q2":"B","Q3":"C","Q4":"D","Q5":"A","Q6":"B"}}. '
    "Use each case alias exactly once and choose a bay A, B, C, or D for each case."
)


@dataclass(frozen=True, slots=True, repr=False)
class Source:
    seed: int
    source_uuid: str
    source_result_sha256: str
    shown: str
    masked: str
    opened: frozenset[str]
    eligible: dict[str, frozenset[str]]
    optimal: dict[str, str]


def _need(ok: bool) -> None:
    if not ok:
        raise ValueError("Invalid Dispatch payload evidence")


def _object(data: str | bytes) -> dict[str, Any]:
    value = json.loads(
        data, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object")
    return value


def _reply(text: str, key: str) -> Any:
    if not isinstance(text, str):
        raise TypeError("Expected a text reply")
    try:
        value = _object(text)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Invalid JSON reply") from None
    if value.keys() != {key}:
        raise ValueError(f"Expected one JSON field: {key}")
    return value[key]


def decode_choices(text: str) -> dict[str, str]:
    choices = _reply(text, "choices")
    if (
        not isinstance(choices, dict)
        or choices.keys() != set(_ALIASES)
        or any(not isinstance(bay, str) or bay not in _BAYS for bay in choices.values())
    ):
        raise ValueError("Expected one bay A, B, C, or D for each alias Q1 through Q6")
    return choices


def aliases(seed: int) -> dict[str, int]:
    if type(seed) is not int:
        raise ValueError("Expected an integer seed")
    ordered = sorted(
        range(len(_ALIASES)),
        key=lambda case_id: sha256(
            f"seatlab-attn-v1:{seed}:0:{case_id}".encode()
        ).digest(),
    )
    return dict(zip(_ALIASES, ordered, strict=True))


def _open_aliases(opened: Collection[str]) -> frozenset[str]:
    if isinstance(opened, (list, tuple, set, frozenset)) and len(opened) == 2:
        first, second = opened
        if first in _ALIASES and second in _ALIASES and first != second:
            return frozenset((first, second))
    raise ValueError("Expected 2 distinct registered aliases")


def _dispatch_rows(cases: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    _need(
        isinstance(cases, list)
        and len(cases) == len(_ALIASES)
        and all(
            isinstance(case, dict) and {"id", "utilities", "messages"} <= case.keys()
            for case in cases
        )
    )
    ids = [case["id"] for case in cases]
    _need(
        all(type(case_id) is int for case_id in ids)
        and set(ids) == set(range(len(_ALIASES)))
    )
    by_id = dict(zip(ids, cases, strict=True))
    rows = []
    for alias, case_id in aliases(seed).items():
        case = by_id[case_id]
        utilities, reports = case["utilities"], case["messages"]
        _need(
            isinstance(utilities, dict)
            and utilities.keys() == _BAYS
            and all(type(rank) is int for rank in utilities.values())
            and set(utilities.values()) == {1, 2, 3, 4}
            and isinstance(reports, list)
            and len(reports) == 2
        )
        row = {"alias": alias, "utilities": utilities}
        for revision, report in zip((2, 1), reports, strict=True):
            _need(
                isinstance(report, dict)
                and report.keys() == {"revision", "available"}
                and type(report["revision"]) is int
                and report["revision"] == revision
            )
            bays = report["available"]
            _need(
                isinstance(bays, list)
                and len(bays) == 2
                and all(isinstance(bay, str) and bay in _BAYS for bay in bays)
                and bays[0] != bays[1]
            )
            row[f"revision_{revision}"] = bays
        rows.append(row)
    return rows


def _prompt(rules: str, payload: dict[str, Any]) -> str:
    return rules + "\n" + encode_view(payload).decode("ascii")


def _select_prompt(rows: list[dict[str, Any]]) -> str:
    changed = [
        row["alias"] for row in rows if set(row["revision_1"]) != set(row["revision_2"])
    ]
    return _prompt(
        _SELECT_RULES,
        {
            "cases": [
                {k: v for k, v in row.items() if k != "revision_2"} for row in rows
            ],
            "changed_count": len(changed),
            "changed_aliases": changed,
        },
    )


def _project(rows: list[dict[str, Any]], opened: frozenset[str]) -> tuple[str, str]:
    shown = []
    masked = []
    for complete in rows:
        row = {
            k: v
            for k, v in complete.items()
            if k != "revision_2" or complete["alias"] in opened
        }
        shown.append(row)
        masked.append(
            {
                k: ["?"] * len(v) if k in ("revision_1", "revision_2") else v
                for k, v in row.items()
            }
        )
    return (
        _prompt(_DECIDE_RULES, {"cases": shown}),
        _prompt(_DECIDE_RULES, {"cases": masked}),
    )


def project(
    cases: list[dict[str, Any]], seed: int, opened: Collection[str]
) -> tuple[str, str]:
    return _project(_dispatch_rows(cases, seed), _open_aliases(opened))


def _read(path: Path) -> bytes:
    _need(path.resolve() == path)
    _need(stat.S_ISREG(path.lstat().st_mode))
    with path.open("rb") as stream:
        data = stream.read(_MAX_BYTES + 1)
    _need(len(data) <= _MAX_BYTES)
    return data


def _roster() -> dict[int, dict[str, Any]]:
    from . import confirmation_summary as summary

    data = _read(_RECEIPT)
    _need(sha256(data).hexdigest() == SOURCE_SHA256)
    report = _object(data)
    _need(report["schema"] == summary._SCHEMA)
    rows = report["studies"]["notices"]
    summary._registered(rows, "notices", set())
    named = {row["world"]: row for row in rows if row["arm"] == "named"}
    _need(named.keys() == set(_SEEDS))
    hashes = set()
    for row in named.values():
        _need(
            all(
                row[flag] is True
                for flag in (
                    "complete",
                    "behavior_confirmed",
                    "aggregate_usage_verified",
                    "turn_usage_verified",
                )
            )
            and _DIGEST.fullmatch(row["result_sha256"]) is not None
            and row["result_sha256"] not in hashes
            and isinstance(row["calls"], list)
            and len(row["calls"]) == 3
        )
        hashes.add(row["result_sha256"])
    return named


def _native_result(trial: Path, cell: str, row: dict[str, Any]) -> dict[str, Any]:
    from .confirmation_custody import _same_team_config
    from .evidence import _by_role

    data = _read(trial / "result.json")
    _need(sha256(data).hexdigest() == row["result_sha256"])
    result = _object(data)
    native_id = UUID(result["id"])
    config = result["config"]
    task = Path(config["task"]["path"])
    _need(
        native_id.version == 4
        and str(native_id) == result["id"]
        and sha256(f"seatlab-confirmation:v1:{native_id}".encode()).hexdigest()
        == row["trial_ref"]
        and type(result["schema_version"]) is int
        and result["schema_version"] == 1
        and result["trial_name"] == config["trial_name"] == trial.name
        and result["trial_uri"] == trial.as_uri()
        and config["trials_dir"] == str(trial.parent)
        and task.is_absolute()
        and task.name == "dispatch-handoff"
        and result["task_name"] == "harbor/dispatch-handoff"
        and result["task_id"] == {"path": str(task)}
        and _DIGEST.fullmatch(result["task_checksum"]) is not None
        and _same_team_config(config, _object(_read(trial / "config.json")))
    )
    for participant in _by_role(config["participants"], "participants").values():
        _need(
            participant["name"] is None
            and participant["import_path"] == _AGENT
            and participant["model_name"] == "deepseek/deepseek-flash"
            and participant["kwargs"]
            == {"cell_id": cell, "transport": "direct", "command_config": None}
        )
    return result


def _verified_world(
    trial: Path,
    seed: int,
    expected_fingerprint: str,
    reward: float,
) -> tuple[dict[str, Any], str]:
    from .evidence import read_trial_with_world

    world = _object(_read(trial / "artifacts/logs/world/handoff.json"))
    artifact = _object(_read(trial / "verifier/reward.json"))
    evidence, scored_world = read_trial_with_world(trial)
    _need(
        world == scored_world
        and evidence["seed"] == seed
        and evidence["condition"] == "reordered"
        and evidence["score"]["episodes"] == len(_ALIASES)
        and evidence["fingerprint"] == expected_fingerprint
        and artifact.keys() == {"reward"}
        and type(artifact["reward"]) in (int, float)
        and artifact["reward"] == reward == evidence["score"]["optimal"] / len(_ALIASES)
    )
    return world, evidence["trial_id"]


def _native_turns(
    result: dict[str, Any],
    cell: str,
    row: dict[str, Any],
    capsules: dict[str, Any],
    accepted: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    from .evidence import _by_role

    seats = _by_role(result["seat_results"], "seat_results")
    calls = iter(row["calls"])
    turns = {}
    metadata = {}
    for role, stages in _STAGES.items():
        record = seats[role]
        capsule = capsules[role]
        context = record["agent_result"]
        state = context["metadata"]
        metadata[role] = state
        _need(
            record["agent_info"] == _SEAT_INFO
            and capsule["basis"] == "bound"
            and state["schema"] == "seatlab.native-attention-seat/v1"
            and state["cell_id"] == cell
            and state["role"] == role
            and state["stage"] == "complete"
            and state["view_sha256"] == capsule["sha256"]
            and state["decision_sha256"] == view_hash(accepted[role])
            and isinstance(state["turns"], list)
            and len(state["turns"]) == len(stages)
        )
        for stage, turn in zip(stages, state["turns"], strict=True):
            call = next(calls)
            usage = {
                "served_model": call["served_model"],
                "input_tokens": call["cache_hit"] + call["cache_miss"],
                "cache_tokens": call["cache_hit"],
                "output_tokens": call["output_tokens"],
            }
            _need(
                turn["stage"] == stage
                and turn["run"] == call["run"] == f"{cell}-{stage}"
                and turn["role"] == call["role"] == role
                and turn["status"] == "received"
                and call["status"] == "settled"
                and usage["served_model"]
                in ("deepseek-flash", "deepseek-v4.1-flash-1017")
                and turn["usage"] == usage
                and isinstance(turn["text"], str)
                and turn["response_sha256"] == sha256(turn["text"].encode()).hexdigest()
            )
            turns[stage] = turn
    return metadata, turns


def _inventory_reply(
    turn: dict[str, Any],
    cases: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
) -> None:
    from . import seat_agent

    prompt = _prompt(
        seat_agent._RULES["inventory"]
        + " Output only a JSON object. Do not include explanations.",
        {"role": "inventory", "cases": cases},
    )
    _need(turn["prompt_sha256"] == sha256(prompt.encode()).hexdigest())
    try:
        reply = seat_agent._json_object(turn["text"], "inventory model")
        seat_agent._checked_submission(reply, "inventory", len(_ALIASES))
    except seat_agent._Unmeasured:
        raise ValueError("Invalid native inventory reply") from None
    canonical = [
        {
            "id": row["id"],
            "reports": [
                {
                    "revision": report["revision"],
                    "available": sorted(report["available"]),
                }
                for report in sorted(
                    row["reports"], key=lambda report: report["revision"]
                )
            ],
        }
        for row in sorted(reply["rows"], key=lambda row: row["id"])
    ]
    _need(canonical == accepted)


def _view_cases(view: Any) -> list[dict[str, Any]]:
    if not isinstance(view, dict) or not isinstance(view.get("cases"), list):
        raise TypeError("Expected native cases")
    cases = view["cases"]
    _need(len(cases) == 6 and all(isinstance(case, dict) for case in cases))
    return cases


def _source(native: Path, seed: int, row: dict[str, Any], fingerprint: str) -> Source:
    from .export_capsules import reconstructed_capsules

    cell = f"attnc-s{seed}-r0-named"
    directory = native / "cells" / cell
    tag = sha256(os.fsencode(directory)).hexdigest()[:24]
    trial = directory / "trials" / f"seatlab-team-{tag}"
    result = _native_result(trial, cell, row)
    world, native_id = _verified_world(trial, seed, fingerprint, row["reward"])
    _need(native_id == result["id"])
    _need(sha256(_read(trial / "result.json")).hexdigest() == row["result_sha256"])
    cases = world["cases"]
    accepted = {
        "inventory": [{"id": case["id"], "reports": case["reports"]} for case in cases],
        "dispatch": [{"id": case["id"], "bay": case["chosen"]} for case in cases],
    }
    capsules = reconstructed_capsules(world)
    metadata, turns = _native_turns(result, cell, row, capsules, accepted)
    _need(metadata["inventory"]["opened"] == [])
    inventory = _view_cases(capsules["inventory"]["view"])
    dispatch = _view_cases(capsules["dispatch"]["view"])
    _inventory_reply(
        turns["inventory"],
        inventory,
        accepted["inventory"],
    )

    rows = _dispatch_rows(dispatch, seed)
    selector = turns["select"]
    _need(
        selector["prompt_sha256"] == sha256(_select_prompt(rows).encode()).hexdigest()
    )
    selected = _reply(selector["text"], "open")
    _need(isinstance(selected, list) and selected == metadata["dispatch"]["opened"])
    opened = _open_aliases(selected)
    shown, masked = _project(rows, opened)
    decide = turns["decide"]
    _need(decide["prompt_sha256"] == sha256(shown.encode()).hexdigest())
    mapped = {alias: cases[case_id] for alias, case_id in aliases(seed).items()}
    _need(
        decode_choices(decide["text"])
        == {alias: case["chosen"] for alias, case in mapped.items()}
    )
    eligible = {alias: frozenset(case["after"]) for alias, case in mapped.items()}
    optimal = {
        alias: max(eligible[alias], key=case["utilities"].__getitem__)
        for alias, case in mapped.items()
    }
    return Source(
        seed, native_id, row["result_sha256"], shown, masked, opened, eligible, optimal
    )


def sources(native: Path) -> dict[int, Source]:
    roster = _roster()
    native = native.expanduser().resolve(strict=True)
    _need(native.is_dir())
    world_type = run_path(str(_WORLD))["World"]
    found = {}
    for seed in _SEEDS:
        registered = world_type(
            {
                "schema": "seatlab.scenario/v1",
                "seed": seed,
                "episodes": len(_ALIASES),
                "condition": "fresh",
            }
        ).evidence()["cases"]
        fingerprint = view_hash(
            [
                {k: case[k] for k in ("id", "utilities", "before", "after")}
                for case in registered
            ]
        )
        try:
            found[seed] = _source(native, seed, roster[seed], fingerprint)
        except (
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            IndexError,
            RecursionError,
            NotADirectoryError,
        ):
            raise ValueError(
                f"Invalid Dispatch payload evidence for source {seed}"
            ) from None
    _need(len({source.source_uuid for source in found.values()}) == len(_SEEDS))
    return found
