"""Collect, verify, and optionally export native Hanabi cross-play Harbor trials."""

from __future__ import annotations

import argparse
import errno
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from uuid import UUID

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from study import PAIRINGS, digest  # noqa: E402

_SEATS = ("p0", "p1")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TURN_FIELDS = frozenset({"turn", "seat", "view", "view_sha256", "action"})
_MATCHUPS = (("ll-to-lr", "LL", "LR", "p0"), ("ll-to-rl", "LL", "RL", "p1"))
_STATUSES = (
    "matched",
    "valid_prediction_differs",
    "unattempted",
    "incomplete",
    "failed",
    "invalid",
)


def _uuid(val: object) -> str | None:
    if not isinstance(val, str) or len(val) != 36:
        return None
    try:
        u = UUID(val)
    except ValueError:
        return None
    return val if str(u) == val and u.int != 0 else None


def _finite(val: object) -> float | None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    num = float(val)
    return num if math.isfinite(num) else None


def _check_hanabi(path: Path, deck_id: int) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "missing artifacts/logs/world/hanabi.json"
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "unreadable or malformed hanabi.json"
    if not isinstance(raw, dict):
        return None, "invalid hanabi.json schema"
    data = cast(dict[str, Any], raw)
    if data.get("schema") != "seatlab.hanabi-native/v1":
        return None, "invalid hanabi.json schema"
    if type(data.get("deck_id")) is not int or data["deck_id"] != deck_id:
        return (
            None,
            f"deck_id mismatch: got {data.get('deck_id')!r}, expected {deck_id}",
        )
    deck_sha, scores, turns = (
        data.get("deck_sha256"),
        data.get("scores"),
        data.get("turns"),
    )
    if not isinstance(deck_sha, str) or _SHA256.fullmatch(deck_sha) is None:
        return None, "invalid deck_sha256"
    if not isinstance(scores, list) or len(scores) != 2:
        return None, f"invalid terminal scores {scores!r}"
    s0, s1 = cast(list[object], scores)
    if type(s0) is not int or type(s1) is not int or s0 != s1 or not 0 <= s0 <= 25:
        return None, f"invalid terminal scores {scores!r}"
    if not isinstance(turns, list) or not turns:
        return None, "turns must be a non-empty list"
    history: list[list[Any]] = []
    checked_turns: list[dict[str, Any]] = []
    for idx, raw_row in enumerate(cast(list[object], turns), 1):
        if not isinstance(raw_row, dict) or not _TURN_FIELDS.issubset(raw_row):
            return None, f"turn {idx} missing required fields"
        row = cast(dict[str, Any], raw_row)
        turn_no = row["turn"]
        if type(turn_no) is not int or turn_no != idx:
            return None, f"turn ordering mismatch at index {idx}: got {turn_no!r}"
        seat, view, vsha, action = (
            row["seat"],
            row["view"],
            row["view_sha256"],
            row["action"],
        )
        if seat not in _SEATS or not isinstance(view, dict) or type(action) is not int:
            return None, f"turn {idx} invalid seat/view/action"
        if (
            not isinstance(vsha, str)
            or _SHA256.fullmatch(vsha) is None
            or digest(view) != vsha
        ):
            return None, f"turn {idx} view_sha256 mismatch"
        history.append([idx, seat, vsha, action])
        checked_turns.append(row)
    return {
        "deck_sha256": deck_sha,
        "history_sha256": digest(history),
        "decisions": len(checked_turns),
        "scores": [s0, s1],
        "turns": checked_turns,
    }, None


def _check_seat_audits(
    trial_dir: Path, pairing: str, turns: list[dict[str, Any]]
) -> str | None:
    for idx, seat in enumerate(_SEATS):
        expected_conv = pairing[idx]
        audit_path = trial_dir / "seats" / seat / "agent" / "policy_audit.jsonl"
        if not audit_path.is_file():
            return f"missing seats/{seat}/agent/policy_audit.jsonl"
        try:
            raw_lines = audit_path.read_text(encoding="utf-8").splitlines()
            parsed: list[object] = [
                json.loads(line) for line in raw_lines if line.strip()
            ]
        except (OSError, ValueError):
            return f"unreadable or malformed seats/{seat}/agent/policy_audit.jsonl"
        if len(parsed) != len(raw_lines) or any(
            not isinstance(obj, dict) for obj in parsed
        ):
            return f"malformed seats/{seat}/agent/policy_audit.jsonl"
        entries = [cast(dict[str, Any], obj) for obj in parsed]
        if any(
            type(e.get("turn")) is not int or type(e.get("action")) is not int
            for e in entries
        ):
            return f"invalid types in seats/{seat}/agent/policy_audit.jsonl"
        expected = [
            (r["turn"], seat, expected_conv, r["view_sha256"], r["action"])
            for r in turns
            if r["seat"] == seat
        ]
        observed = [
            (
                e.get("turn"),
                e.get("seat"),
                e.get("convention"),
                e.get("view_sha256"),
                e.get("action"),
            )
            for e in entries
        ]
        if observed != expected:
            return f"seat {seat} policy_audit.jsonl disagrees with accepted turns or convention"
    return None


def _inspect_trial(
    trial_dir: Path, cell: dict[str, Any], forecast: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    deck_id: int = cell["deck_id"]
    pairing: str = cell["pairing"]
    trial_name: str = cell["trial_name"]
    row: dict[str, Any] = {
        "deck_id": deck_id,
        "pairing": pairing,
        "trial_name": trial_name,
        "status": "unattempted",
        "native_trial_id": None,
        "deck_sha256": None,
        "history_sha256": None,
        "decisions": None,
        "scores": None,
        "reward": None,
        "reason": None,
        "expected": {
            k: forecast[k]
            for k in ("deck_sha256", "history_sha256", "decisions", "scores")
        },
    }

    def stop(status: str, reason: str) -> tuple[dict[str, Any], None]:
        row["status"], row["reason"] = status, reason
        return row, None

    if not trial_dir.is_dir():
        return stop("unattempted", "trial directory absent")
    result_path = trial_dir / "result.json"
    if not result_path.is_file():
        return stop("incomplete", "missing result.json")
    try:
        raw_res: object = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return stop("invalid", "unreadable or malformed result.json")
    if not isinstance(raw_res, dict):
        return stop("invalid", "result.json is not an object")
    res = cast(dict[str, Any], raw_res)

    tid = _uuid(res.get("id"))
    row["native_trial_id"] = tid
    seats_raw = res.get("seat_results")
    seat_entries = (
        cast(list[object], seats_raw) if isinstance(seats_raw, list) else None
    )
    if res.get("exception_info") is not None or (
        seat_entries is not None
        and any(
            isinstance(e, dict)
            and cast(dict[str, Any], e).get("exception_info") is not None
            for e in seat_entries
        )
    ):
        return stop("failed", "native trial or seat recorded exception_info")
    if not isinstance(res.get("finished_at"), str) or not res["finished_at"]:
        return stop("incomplete", "missing result.json finished_at")
    if (
        tid is None
        or res.get("trial_name") != trial_name
        or res.get("reward_valid") is not True
    ):
        return stop("invalid", "invalid trial id, trial_name, or reward_valid")
    if seat_entries is None or len(seat_entries) != 2:
        return stop("invalid", "seat_results must contain p0 and p1")
    seen: set[str] = set()
    for raw_entry in seat_entries:
        if not isinstance(raw_entry, dict):
            return stop("invalid", "invalid or duplicate seat_results entry")
        entry = cast(dict[str, Any], raw_entry)
        seat = entry.get("seat")
        if not isinstance(seat, str) or seat not in _SEATS or seat in seen:
            return stop("invalid", "invalid or duplicate seat_results entry")
        seen.add(seat)
        info, ex = entry.get("agent_info"), entry.get("agent_execution")
        if entry.get("under_test") is not True or not (
            isinstance(info, dict)
            and cast(dict[str, Any], info).get("name") == "oracle"
        ):
            return stop(
                "invalid", f"seat {seat} requires under_test=true and oracle agent"
            )
        if not (
            isinstance(ex, dict)
            and isinstance(cast(dict[str, Any], ex).get("finished_at"), str)
            and cast(dict[str, Any], ex)["finished_at"]
        ):
            return stop(
                "incomplete", f"seat {seat} missing agent_execution.finished_at"
            )

    verifier = res.get("verifier_result")
    rewards = (
        cast(dict[str, Any], verifier).get("rewards")
        if isinstance(verifier, dict)
        else None
    )
    reward = (
        _finite(cast(dict[str, Any], rewards).get("reward"))
        if isinstance(rewards, dict)
        else None
    )
    reward_txt = trial_dir / "verifier" / "reward.txt"
    if reward is None or not reward_txt.is_file():
        return stop("invalid", "missing verifier reward or verifier/reward.txt")
    try:
        file_reward = _finite(float(reward_txt.read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        file_reward = None
    if file_reward is None or not math.isclose(
        file_reward, reward, rel_tol=0.0, abs_tol=1e-12
    ):
        return stop("invalid", "verifier/reward.txt disagrees with result.json reward")

    world, err = _check_hanabi(
        trial_dir / "artifacts" / "logs" / "world" / "hanabi.json", deck_id
    )
    if err is not None or world is None:
        return stop("invalid", err or "invalid hanabi.json")
    if not math.isclose(reward, world["scores"][0] / 25.0, rel_tol=0.0, abs_tol=1e-12):
        return stop("invalid", f"reward {reward} disagrees with score/25")

    turns = cast(list[dict[str, Any]], world["turns"])
    audit_err = _check_seat_audits(trial_dir, pairing, turns)
    if audit_err is not None:
        return stop("invalid", audit_err)

    row.update(
        deck_sha256=world["deck_sha256"],
        history_sha256=world["history_sha256"],
        decisions=world["decisions"],
        scores=world["scores"],
        reward=reward,
    )
    diffs = [
        k
        for k in ("deck_sha256", "history_sha256", "decisions", "scores")
        if world[k] != forecast[k]
    ]
    if not math.isclose(
        reward, forecast["scores"][0] / 25.0, rel_tol=0.0, abs_tol=1e-12
    ):
        diffs.append("reward")
    if diffs:
        row["status"], row["reason"] = (
            "valid_prediction_differs",
            "differs: " + ", ".join(diffs),
        )
    else:
        row["status"] = "matched"
    return row, turns


def _write_json(path: Path, value: object) -> None:
    if os.path.lexists(path):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
    data = (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        + b"\n"
    )
    with path.open("xb") as fh:
        fh.write(data)


def _export_episodes(
    out_dir: Path,
    deck_ids: list[int],
    cells: list[dict[str, Any]],
    turns_by_cell: dict[tuple[int, str], list[dict[str, Any]]],
    forecast_by_cell: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any]:
    from seatlab.author import accepted_frame, episode_from_frames
    from seatlab.tape import write_lines

    if os.path.lexists(out_dir):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), out_dir)
    if not out_dir.parent.is_dir():
        raise ValueError("episode export parent directory does not exist")

    eps_map: dict[tuple[int, str, str], Mapping[str, Any]] = {}
    all_eps: list[Mapping[str, Any]] = []
    for cell in cells:
        if cell["status"] not in ("matched", "valid_prediction_differs"):
            continue
        turns = turns_by_cell[(cell["deck_id"], cell["pairing"])]
        for seat in _SEATS:
            seat_rows = [r for r in turns if r["seat"] == seat]
            if not seat_rows:
                continue
            frames = [
                accepted_frame(
                    turn=i,
                    view=cast(dict[str, object], r["view"]),
                    acknowledged_sha256=str(r["view_sha256"]),
                    action=int(r["action"]),
                )
                for i, r in enumerate(seat_rows, 1)
            ]
            ep: Mapping[str, Any] = episode_from_frames(
                id=f"hanabi:d{cell['deck_id']:02}:{cell['pairing']}:{seat}",
                case_sha256=str(cell["deck_sha256"]),
                role=seat,
                native_trial_id=str(cell["native_trial_id"]),
                closed=True,
                frames=frames,
            )
            eps_map[(cell["deck_id"], cell["pairing"], seat)] = ep
            all_eps.append(ep)

    def receipt(ep: Mapping[str, Any] | None) -> dict[str, str] | None:
        if ep is None:
            return None
        return {
            "native_trial_id": str(ep["native_trial_id"]),
            "episode_sha256": str(ep["episode_sha256"]),
        }

    staging = Path(tempfile.mkdtemp(dir=out_dir.parent, prefix=".hanabi-episodes-"))
    try:
        write_lines(staging / "episodes.jsonl", all_eps)
        matchups: dict[str, dict[str, Any]] = {}
        for slug, src_p, tgt_p, role in _MATCHUPS:
            src_eps: list[Mapping[str, Any]] = []
            tgt_eps: list[Mapping[str, Any]] = []
            pairs: list[dict[str, Any]] = []
            used_cases: set[str] = set()
            for deck_id in deck_ids:
                src_ep = eps_map.get((deck_id, src_p, role))
                tgt_ep = eps_map.get((deck_id, tgt_p, role))
                if src_ep and tgt_ep and src_ep["case_sha256"] != tgt_ep["case_sha256"]:
                    src_ep = tgt_ep = None
                case_sha = str(
                    src_ep["case_sha256"]
                    if src_ep
                    else tgt_ep["case_sha256"]
                    if tgt_ep
                    else forecast_by_cell[(deck_id, src_p)]["deck_sha256"]
                )
                if case_sha in used_cases:
                    src_ep = tgt_ep = None
                    case_sha = str(forecast_by_cell[(deck_id, src_p)]["deck_sha256"])
                used_cases.add(case_sha)
                if src_ep:
                    src_eps.append(src_ep)
                if tgt_ep:
                    tgt_eps.append(tgt_ep)
                pairs.append(
                    {
                        "id": f"hanabi:{slug}:d{deck_id:02}:{role}",
                        "case_sha256": case_sha,
                        "role": role,
                        "source": receipt(src_ep),
                        "target": receipt(tgt_ep),
                    }
                )
            src_name, tgt_name, pairs_name = (
                f"{slug}-source.jsonl",
                f"{slug}-target.jsonl",
                f"{slug}-pairs.json",
            )
            write_lines(staging / src_name, src_eps)
            write_lines(staging / tgt_name, tgt_eps)
            _write_json(
                staging / pairs_name,
                {"schema": "seatlab.episode-pairs/v1", "pairs": pairs},
            )
            matchups[slug] = {
                "source_pairing": src_p,
                "target_pairing": tgt_p,
                "unchanged_role": role,
                "source_episodes": src_name,
                "target_episodes": tgt_name,
                "pairs_manifest": pairs_name,
                "registered_pairs": len(pairs),
                "complete_pairs": sum(
                    p["source"] is not None and p["target"] is not None for p in pairs
                ),
            }
        os.rename(staging, out_dir)
    except Exception:
        for child in staging.iterdir():
            child.unlink(missing_ok=True)
        staging.rmdir()
        raise
    return {
        "all_episodes": "episodes.jsonl",
        "episode_count": len(all_eps),
        "matchups": matchups,
    }


def collect(
    native_root: Path, forecast_path: Path, episodes_dir: Path | None = None
) -> dict[str, Any]:
    raw_plan: object = json.loads(
        (native_root / "plan.json").read_text(encoding="utf-8")
    )
    if (
        not isinstance(raw_plan, dict)
        or raw_plan.get("schema") != "seatlab.hanabi-plan/v1"
    ):
        raise ValueError("plan.json must have schema seatlab.hanabi-plan/v1")
    plan = cast(dict[str, Any], raw_plan)
    raw_forecast: object = json.loads(forecast_path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw_forecast, dict)
        or raw_forecast.get("schema") != "seatlab.hanabi-predictions/v1"
    ):
        raise ValueError("forecast must have schema seatlab.hanabi-predictions/v1")
    forecast_doc = cast(dict[str, Any], raw_forecast)

    forecast_by_cell: dict[tuple[int, str], dict[str, Any]] = {
        (c["deck_id"], c["pairing"]): c for c in forecast_doc["cells"]
    }
    results: list[dict[str, Any]] = []
    turns_by_cell: dict[tuple[int, str], list[dict[str, Any]]] = {}
    uuid_cells: dict[str, list[str]] = {}
    for cell in plan["cells"]:
        key = (cell["deck_id"], cell["pairing"])
        if cell["pairing"] not in PAIRINGS or key not in forecast_by_cell:
            raise ValueError(f"registered cell {key!r} missing from forecast")
        row, turns = _inspect_trial(
            native_root / "trials" / cell["trial_name"], cell, forecast_by_cell[key]
        )
        results.append(row)
        if turns is not None:
            turns_by_cell[key] = turns
        if row["native_trial_id"] is not None:
            uuid_cells.setdefault(row["native_trial_id"], []).append(cell["trial_name"])

    dup_uuids = {uid: names for uid, names in uuid_cells.items() if len(names) > 1}
    for row in results:
        if row["native_trial_id"] in dup_uuids:
            row["status"], row["reason"] = (
                "invalid",
                f"duplicate native_trial_id {row['native_trial_id']}",
            )
            row["scores"] = row["reward"] = row["decisions"] = None
            row["deck_sha256"] = row["history_sha256"] = None
            turns_by_cell.pop((row["deck_id"], row["pairing"]), None)

    counts = {s: sum(r["status"] == s for r in results) for s in _STATUSES}
    valid, total = counts["matched"] + counts["valid_prediction_differs"], len(results)
    report: dict[str, Any] = {
        "schema": "seatlab.hanabi-collect/v1",
        "deck_ids": list(plan["deck_ids"]),
        "counts": {
            "registered": total,
            "valid": valid,
            **counts,
            "duplicate_uuids": len(dup_uuids),
        },
        "rates": {
            "matched_of_registered": counts["matched"] / total if total else 0.0,
            "matched_of_valid": counts["matched"] / valid if valid else None,
            "valid_of_registered": valid / total if total else 0.0,
        },
        "duplicate_uuids": dup_uuids,
        "cells": results,
    }
    if episodes_dir is not None:
        report["episodes"] = _export_episodes(
            episodes_dir,
            list(plan["deck_ids"]),
            results,
            turns_by_cell,
            forecast_by_cell,
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "native_root", type=Path, help="directory with plan.json and trials/"
    )
    parser.add_argument(
        "forecast", type=Path, help="frozen seatlab.hanabi-predictions/v1 JSON"
    )
    parser.add_argument("--episodes", type=Path, default=None, metavar="NEW_DIRECTORY")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            collect(args.native_root, args.forecast, args.episodes),
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
