import argparse
import json
import re
import sys
from collections.abc import Sequence
from functools import lru_cache
from hashlib import sha256
from itertools import product
from pathlib import Path
from runpy import run_path
from typing import Any
from uuid import UUID

from ..capsule import _reject_constant, _unique_object, encode_view, view_hash
from . import evidence_join

SCHEMA = "seatlab.dispatch-frozen-final/v1"
_ARMS = ("nested", "joined")
_WORLDS = tuple(range(14521, 14529))
_EPISODES = 6
_SLOTS = frozenset(product(_WORLDS, (0, 1), _ARMS))
_PERMUTATIONS = 1 << len(_WORLDS)
_SEATLAB = Path(__file__).resolve().parents[1]
_ORIGINAL = _SEATLAB / "results" / "dispatch-evidence-join-live.json"
_WORLD = _SEATLAB.parent / "tasks/dispatch-handoff/environment/seatworld.py"
_SOURCE_KEYS = {
    "source_cell",
    "world",
    "trial_id_sha256",
    "source_format",
    "stock_world_sha256",
    "prompt_sha256",
}
_ROW_KEYS = {
    "run",
    "world",
    "rep",
    "arm",
    "prompt_sha256",
    "reply_sha256",
    "actions",
    "optimal",
    "feasible",
    "cost_nanousd",
    "state",
}
_LIMIT = (
    "The public stock actions use synthetic bays A through D, not prompt text. "
    "The source loader attests the joined prompt digest. "
    "This verifier does not rebuild private prompts, parse private replies, "
    "or verify provider charges."
)


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@lru_cache(maxsize=len(_WORLDS))
def _stock(world: int) -> tuple[str, dict[int, tuple[str, list[str]]], tuple[str, ...]]:
    model = run_path(str(_WORLD))
    state = model["World"](
        {
            "schema": "seatlab.scenario/v1",
            "seed": world,
            "episodes": _EPISODES,
            "condition": "reordered",
        }
    ).evidence()
    cases = [
        {key: case[key] for key in ("id", "utilities", "before", "after")}
        for case in state["cases"]
    ]
    choices = {
        case["id"]: (
            max(case["after"], key=case["utilities"].__getitem__),
            case["after"],
        )
        for case in cases
    }
    return view_hash(cases), choices, model["BAYS"]


def _sources(sources: Sequence[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    if not isinstance(sources, (list, tuple)) or len(sources) != len(_WORLDS):
        raise ValueError("expected 8 registered nested sources")
    indexed = {}
    identities = set()
    for source in sources:
        if not isinstance(source, dict) or source.keys() != _SOURCE_KEYS:
            raise ValueError("invalid source metadata fields")
        world = source["world"]
        identity = source["trial_id_sha256"]
        prompts = source["prompt_sha256"]
        if (
            type(world) is not int
            or world not in _WORLDS
            or world in indexed
            or source["source_cell"] != f"dvj-s{world}-r0-nested"
            or source["source_format"] != "nested"
            or not _digest(identity)
            or identity in identities
            or source["stock_world_sha256"] != _stock(world)[0]
            or not isinstance(prompts, dict)
            or prompts.keys() != set(_ARMS)
            or not all(_digest(value) for value in prompts.values())
            or prompts["nested"] == prompts["joined"]
        ):
            raise ValueError("invalid registered source, identity, or prompt digests")
        indexed[world] = source
        identities.add(identity)
    return indexed


def _grade(actions: object, world: int) -> tuple[int, int]:
    _, choices, bays = _stock(world)
    if not isinstance(actions, list) or len(actions) != len(choices):
        raise ValueError("expected 6 accepted stock actions")
    seen = set()
    optimal = feasible = 0
    for action in actions:
        if not isinstance(action, dict) or action.keys() != {"id", "bay"}:
            raise ValueError("expected stock action fields id and bay")
        case_id, bay = action.get("id"), action.get("bay")
        if (
            type(case_id) is not int
            or case_id not in choices
            or case_id in seen
            or not isinstance(bay, str)
            or bay not in bays
        ):
            raise ValueError("expected distinct stock case IDs and bays A through D")
        seen.add(case_id)
        best, available = choices[case_id]
        optimal += bay == best
        feasible += bay in available
    return optimal, feasible


def _check_outcome(row: dict[str, Any]) -> None:
    run, state = row["run"], row["state"]
    reply, actions = row["reply_sha256"], row["actions"]
    optimal, feasible, cost = row["optimal"], row["feasible"], row["cost_nanousd"]
    if cost is not None and (type(cost) is not int or cost < 0):
        raise ValueError(f"{run}: invalid cost in nanoUSD")
    if state not in ("valid", "invalid", "missing"):
        raise ValueError(f"{run}: invalid first-reply state")
    if (state == "missing" and reply is not None) or (
        state != "missing" and not _digest(reply)
    ):
        raise ValueError(f"{run}: first-reply digest does not match its state")
    if state != "valid":
        if (actions, optimal, feasible) != (None, None, None):
            raise ValueError(f"{run}: no stock actions or scores for this state")
        return
    if (
        type(optimal) is not int
        or type(feasible) is not int
        or (optimal, feasible) != _grade(actions, row["world"])
    ):
        raise ValueError(f"{run}: receipt scores do not match stock actions")


def _grid(
    rows: Sequence[dict[str, Any]], sources: dict[int, dict[str, Any]]
) -> dict[tuple[int, int, str], dict[str, Any]]:
    if not isinstance(rows, (list, tuple)) or len(rows) != len(_SLOTS):
        raise ValueError("expected exactly 32 registered rows, including missing ones")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or row.keys() != _ROW_KEYS:
            raise ValueError("invalid replay row fields")
        world, rep, arm = row["world"], row["rep"], row["arm"]
        slot = world, rep, arm
        if (
            type(world) is not int
            or type(rep) is not int
            or not isinstance(arm, str)
            or slot not in _SLOTS
            or slot in indexed
            or row["run"] != f"dvf-s{world}-r{rep}-{arm}"
        ):
            raise ValueError("invalid or duplicate replay slot")
        if row["prompt_sha256"] != sources[world]["prompt_sha256"][arm]:
            raise ValueError(f"{row['run']}: prompt digest differs from its source")
        _check_outcome(row)
        indexed[slot] = row
    return indexed


def _sum_known(values: Sequence[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _totals(rows: list[dict[str, Any]]) -> dict[str, int | None]:
    return {
        **{
            state: sum(row["state"] == state for row in rows)
            for state in ("valid", "invalid", "missing")
        },
        **{
            key: _sum_known([row[key] for row in rows])
            for key in ("optimal", "feasible", "cost_nanousd")
        },
        "unpriced": sum(row["cost_nanousd"] is None for row in rows),
    }


def _world_delta(
    grid: dict[tuple[int, int, str], dict[str, Any]], world: int
) -> dict[str, int | None]:
    nested = [grid[world, rep, "nested"] for rep in (0, 1)]
    joined = [grid[world, rep, "joined"] for rep in (0, 1)]
    if any(row["state"] != "valid" for row in (*nested, *joined)):
        return {"world": world, "optimal": None, "feasible": None}
    return {
        "world": world,
        **{
            key: sum(row[key] for row in joined) - sum(row[key] for row in nested)
            for key in ("optimal", "feasible")
        },
    }


def summarize(
    rows: Sequence[dict[str, Any]], sources: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    grid = _grid(rows, _sources(sources))
    worlds = [_world_delta(grid, world) for world in _WORLDS]
    deltas = [row["optimal"] for row in worlds if row["optimal"] is not None]
    net_optimal = sum(deltas) if deltas else None
    net_feasible = _sum_known([row["feasible"] for row in worlds])
    tail = p_value = None
    verdict = "inconclusive"
    if (
        len(deltas) == len(_WORLDS)
        and net_optimal is not None
        and net_feasible is not None
    ):
        tail = sum(
            abs(sum(sign * delta for sign, delta in zip(signs, deltas, strict=True)))
            >= abs(net_optimal)
            for signs in product((-1, 1), repeat=len(_WORLDS))
        )
        p_value = tail / _PERMUTATIONS
        direction = 1 if net_optimal > 0 else -1
        verdict = (
            "established"
            if abs(net_optimal) >= 8
            and p_value <= 0.05
            and direction * net_feasible >= 0
            else "not_established"
        )
    return {
        "arms": {
            arm: _totals([row for row in grid.values() if row["arm"] == arm])
            for arm in _ARMS
        },
        "world_deltas": worlds,
        "tail": tail,
        "permutations": _PERMUTATIONS,
        "p_value": p_value,
        "net_optimal": net_optimal,
        "net_feasible": net_feasible,
        "matched_worlds": len(deltas),
        "verdict": verdict,
    }


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise TypeError(f"{path.name}: expected a JSON object")
    return value


def _original_rows() -> dict[str, dict[str, Any]]:
    original = _read_object(_ORIGINAL)
    cells = evidence_join.grid()
    if (
        original["schema"] != evidence_join.REPORT_SCHEMA
        or original["plan"]["schema"] != evidence_join.PLAN_SCHEMA
        or encode_view(original["plan"]["cells"]) != encode_view(cells)
        or len(original["rows"]) != len(cells)
    ):
        raise ValueError("invalid original native report or plan grid")
    planned = {cell["cell_id"]: cell for cell in cells}
    indexed = {}
    for row in original["rows"]:
        cell_id = row["cell_id"]
        if cell_id in indexed or cell_id not in planned:
            raise ValueError("invalid or duplicate original native slot")
        cell = planned[cell_id]
        if encode_view({key: row[key] for key in cell}) != encode_view(cell):
            raise ValueError("original native row does not match its plan")
        indexed[cell_id] = row
    return indexed


def _join_original(sources: Sequence[dict[str, Any]]) -> None:
    original = _original_rows()
    for source in sources:
        cell_id = source["source_cell"]
        row = original[cell_id]
        if row["state"] != "complete":
            raise ValueError(f"{cell_id}: original nested trial is not complete")
        outcome = row["outcome"]
        trial_id = outcome["trial_id"]
        parsed = UUID(trial_id)
        turns = [turn for turn in outcome["turns"] if turn["stage"] == "decide"]
        if (
            str(parsed) != trial_id
            or parsed.int == 0
            or sha256(trial_id.encode("ascii")).hexdigest() != source["trial_id_sha256"]
            or outcome["fingerprint"] != source["stock_world_sha256"]
            or len(turns) != 1
            or turns[0]["role"] != "dispatch"
            or turns[0]["run"] != f"{cell_id}-decide"
            or turns[0]["status"] != "received"
            or turns[0]["prompt_sha256"] != source["prompt_sha256"]["nested"]
        ):
            raise ValueError(
                f"{cell_id}: source does not match the original nested trial"
            )


def verify(path: Path) -> dict[str, Any]:
    receipt = _read_object(path)
    if (
        receipt.keys() != {"schema", "sources", "rows", "summary"}
        or receipt["schema"] != SCHEMA
    ):
        raise ValueError("invalid frozen final receipt schema")
    summary = summarize(receipt["rows"], receipt["sources"])
    _join_original(receipt["sources"])
    if encode_view(receipt["summary"]) != encode_view(summary):
        raise ValueError("receipt summary does not match the verified summary")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False, epilog=_LIMIT)
    commands = parser.add_subparsers(dest="command", required=True)
    verify_parser = commands.add_parser("verify", allow_abbrev=False, epilog=_LIMIT)
    verify_parser.add_argument("result", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = verify(args.result)
    except (
        AttributeError,
        KeyError,
        OSError,
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:
        parser.exit(1, f"{args.result}: {exc}\n")
    print(json.dumps(summary, sort_keys=True))
    print(_LIMIT, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
