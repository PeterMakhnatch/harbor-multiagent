"""Summarize the registered 12-block Calendar Feedback replica from native evidence."""

from __future__ import annotations

import argparse
import json
import stat
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from . import calendar_protocol, calendar_replica_report

_WORLDS = (0, 4, 8)
_ARMS = ("blind", "peer")
_SCORED = {"complete", "model_invalid_scored_zero"}
_Row = dict[str, Any]


class _LayoutError(ValueError):
    pass


def blocks() -> tuple[calendar_protocol.Block, ...]:
    """Return the registered replica blocks in repetition/world/direction order."""
    return tuple(
        block for block in calendar_protocol.blocks() if block.world in _WORLDS
    )


def _planned(block: calendar_protocol.Block) -> _Row:
    return {
        "block": f"rx-w{block.world:02d}-d{block.direction}-r{block.repetition}",
        "world": block.world,
        "direction": block.direction,
        "repetition": block.repetition,
        "status": "not_started",
        "report": None,
        "peer_minus_blind": None,
    }


def _directory(path: Path) -> bool:
    if path.resolve() != path:
        raise _LayoutError("nonphysical_directory")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode):
        raise _LayoutError("not_directory")
    return True


def _entries(path: Path, allowed: set[str]) -> set[str]:
    entries: set[str] = (
        {entry.name for entry in path.iterdir()} if _directory(path) else set()
    )
    if not entries <= allowed:
        raise _LayoutError("unregistered_entries")
    return entries


def _reject(row: _Row, reason: str) -> None:
    row.update(
        status="invalid_evidence", reason=reason, report=None, peer_minus_blind=None
    )


def _public_report(report: _Row) -> _Row:
    # Do not forward the adapter usage fields or any future private fields.
    result = {
        key: report[key]
        for key in (
            "schema",
            "world",
            "direction",
            "repetition",
            "status",
            "native_sha256",
            "semantics_sha256",
            "equal_final_prompt",
            "equal_choice",
            "reason",
        )
        if key in report
    }
    result["arms"] = {
        name: {
            key: arm[key]
            for key in (
                "status",
                "reason",
                "reward",
                "slot",
                "sender_prompt_sha256",
                "receiver_prompt_sha256",
                "trial_id_sha256",
                "native_timeline_verified",
            )
            if key in arm
        }
        for name, arm in report["arms"].items()
        if name in _ARMS
    }
    result["seeds"] = {
        seat: {"status": report["seeds"][seat]["status"]} for seat in ("p1", "p2")
    }
    result["turns"] = {
        turn: {key: hashes[key] for key in ("prompt_sha256", "response_sha256")}
        for turn, hashes in report["turns"].items()
    }
    result["counted_turns"] = len(result["turns"])
    return result


def _reward(row: _Row, name: str) -> int | None:
    report = row["report"]
    if report is None:
        return None
    arm = report["arms"][name]
    reward = arm["reward"]
    if (
        arm["status"] not in _SCORED
        or arm["native_timeline_verified"] is not True
        or type(reward) is not int
        or reward not in (0, 1)
        or (arm["status"] == "model_invalid_scored_zero" and reward != 0)
    ):
        return None
    return reward


def _replay(slot: Path, row: _Row) -> None:
    try:
        # The optional independent meter is not part of this evidence plane.
        if "block" not in _entries(slot, {"block", "meter"}):
            return
        out = slot / "block"
        if not _directory(out):
            return
    except _LayoutError as exc:
        _reject(row, str(exc))
        return
    except (OSError, RuntimeError, ValueError):
        _reject(row, "unreadable_archive")
        return

    report = calendar_replica_report.analyze(out)
    keys = ("world", "direction", "repetition")
    coords = tuple(report.get(key) for key in keys)
    if coords == (None, None, None) and report["status"] == "not_started":
        return
    if any(
        type(value) is not int or value != row[key]
        for key, value in zip(keys, coords, strict=True)
    ):
        _reject(
            row,
            "unbound_block" if coords == (None, None, None) else "coordinate_mismatch",
        )
        return

    row.update(status=report["status"], report=_public_report(report))
    blind, peer = (_reward(row, name) for name in _ARMS)
    if report["status"] == "complete" and blind is not None and peer is not None:
        row["peer_minus_blind"] = peer - blind


def _reject_identity_conflicts(rows: list[_Row]) -> None:
    # Generated task checksums should repeat within a world. Native trial IDs
    # should not repeat across slots, even when a result was only partial.
    sources: dict[int, set[tuple[str, str]]] = defaultdict(set)
    trials: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if (report := row["report"]) is None:
            continue
        native, semantics = report["native_sha256"], report["semantics_sha256"]
        if native is not None and semantics is not None:
            sources[row["world"]].add((native, semantics))
        for arm in report["arms"].values():
            if (digest := arm["trial_id_sha256"]) is not None:
                trials[digest].add(row["block"])

    repeated = {block for slots in trials.values() if len(slots) > 1 for block in slots}
    for row in rows:
        if (report := row["report"]) is None:
            continue
        if row["block"] in repeated:
            _reject(row, "reused_trial_id")
        elif len(sources[row["world"]]) > 1 and report["native_sha256"] is not None:
            _reject(row, "mixed_world_source_identity")


def _arm_totals(rows: list[_Row], name: str) -> _Row:
    statuses: Counter[str] = Counter()
    wins = valid_choice_losses = model_invalid = verified_timelines = 0
    for row in rows:
        report = row["report"]
        arm = report["arms"][name] if report is not None else None
        status = arm["status"] if arm is not None else row["status"]
        statuses[status] += 1
        if arm is not None and arm["native_timeline_verified"] is True:
            verified_timelines += 1
        reward = _reward(row, name)
        if reward == 1:
            wins += 1
        elif reward == 0 and status == "model_invalid_scored_zero":
            model_invalid += 1
        elif reward == 0:
            valid_choice_losses += 1
    scored = wins + valid_choice_losses + model_invalid
    return {
        "status_counts": dict(statuses),
        "scored": scored,
        "wins": wins,
        "valid_choice_losses": valid_choice_losses,
        "model_invalid_scored_zero": model_invalid,
        "unknown": len(rows) - scored,
        "verified_native_timelines": verified_timelines,
        "native_game_sends_delivered": 2 * verified_timelines,
        "planned_native_game_sends": 2 * len(rows),
    }


def _summary(rows: list[_Row]) -> _Row:
    outcomes: Counter[tuple[int, int]] = Counter()
    comparisons: Counter[tuple[bool, bool | None]] = Counter()
    deltas: list[int] = []
    receipts = 0
    for row in rows:
        report = row["report"]
        if report is None:
            continue
        receipts += report["counted_turns"]
        if (delta := row["peer_minus_blind"]) is not None:
            deltas.append(delta)
            blind, peer = (_reward(row, name) for name in _ARMS)
            if blind is not None and peer is not None:
                outcomes[blind, peer] += 1
        if all(
            report["arms"][name]["native_timeline_verified"] is True for name in _ARMS
        ):
            same_input = report["equal_final_prompt"]
            if type(same_input) is bool:
                same_choice = report["equal_choice"]
                comparisons[
                    same_input, same_choice if type(same_choice) is bool else None
                ] += 1

    equal_input = sum(comparisons[True, choice] for choice in (False, True, None))
    different_input = sum(comparisons[False, choice] for choice in (False, True, None))
    return {
        "planned_pairs": len(rows),
        "complete_pairs": len(deltas),
        "all_pairs_complete": len(deltas) == len(rows),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "bound_relay_receipts": receipts,
        "arms": {name: _arm_totals(rows, name) for name in _ARMS},
        "paired": {
            "peer_only_win": outcomes[0, 1],
            "blind_only_win": outcomes[1, 0],
            "both_win": outcomes[1, 1],
            "both_zero": outcomes[0, 0],
            "mean_peer_minus_blind": mean(deltas) if deltas else None,
        },
        "final_input": {
            "comparable_input_pairs": equal_input + different_input,
            "equal_input_pairs": equal_input,
            "different_input_pairs": different_input,
            "equal_input_comparable_choice_pairs": comparisons[True, False]
            + comparisons[True, True],
            "equal_input_different_choice_pairs": comparisons[True, False],
            "equal_input_same_choice_pairs": comparisons[True, True],
        },
    }


def analyze(study: Path) -> _Row:
    """Return public, raw-free outcomes without provider usage or billing claims."""
    study = study.absolute()
    rows = [_planned(block) for block in blocks()]
    reason = None
    try:
        entries = _entries(study, {row["block"] for row in rows})
    except _LayoutError as exc:
        reason = str(exc)
    except (OSError, RuntimeError, ValueError):
        reason = "unreadable_archive"
    else:
        for row in rows:
            if row["block"] in entries:
                _replay(study / row["block"], row)
        _reject_identity_conflicts(rows)
    if reason is not None:
        for row in rows:
            _reject(row, reason)

    summary = _summary(rows)
    for key, values in (
        ("world", _WORLDS),
        ("direction", (0, 1)),
        ("repetition", (0, 1)),
    ):
        summary[f"by_{key}"] = {
            str(value): _summary([row for row in rows if row[key] == value])
            for value in values
        }
    world_means = {
        world: summary["by_world"][str(world)]["paired"]["mean_peer_minus_blind"]
        for world in _WORLDS
    }
    supported = {
        world: value for world, value in world_means.items() if value is not None
    }
    summary["supported_worlds"] = list(supported)
    summary["equal_world_mean_peer_minus_blind"] = (
        mean(supported.values()) if len(supported) == len(_WORLDS) else None
    )
    statuses = summary["status_counts"]
    if "invalid_evidence" in statuses:
        status = "invalid_evidence"
    elif summary["all_pairs_complete"]:
        status = "complete"
    elif statuses.keys() == {"not_started"}:
        status = "not_started"
    else:
        status = "partial"
    return {
        "schema": "seatlab.calendar-replica-series/v1",
        "status": status,
        "rows": rows,
        "summary": summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(analyze(args.study), allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
