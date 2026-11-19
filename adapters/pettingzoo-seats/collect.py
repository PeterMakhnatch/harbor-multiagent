"""Collect the frozen 8-cell PettingZoo study. Does not rerun Harbor."""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from seatlab.tape import TapeError, canonical, decode, digest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from export import project  # noqa: E402
from study import CELLS  # noqa: E402

_GAMES = {"rps_v2": "rps", "connect_four_v3": "connect-four"}
_STATUSES = ("unattempted", "invalid", "duplicate_uuid", "reference_failed", "matched")
_NUMS = (
    "completion",
    "world_steps",
    "decisions",
    "returns",
    "terminated",
    "truncated",
    "case_sha256",
    "reference_sha256",
)


def _uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return value if str(parsed) == value and parsed.int else None


def _plan(root: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("unreadable plan.json") from exc
    cells = raw.get("cells") if isinstance(raw, dict) else None
    bad = not isinstance(raw, dict) or raw.get("schema") != "seatlab.pz-plan/v1"
    if bad or not isinstance(cells, list):
        raise ValueError("plan.json must be seatlab.pz-plan/v1 with a cells array")
    by_id: dict[str, dict[str, Any]] = {}
    for item in cells:
        cid = item.get("id") if isinstance(item, dict) else None
        if not isinstance(cid, str) or cid in by_id:
            raise ValueError("plan cell missing id or repeats an id")
        by_id[cid] = item
    want = {cid: (game, {"p0": a, "p1": b}) for cid, game, _s, a, b in CELLS}
    if set(by_id) != set(want):
        raise ValueError("plan must contain the registered 8 cell ids")
    seen_names: set[str] = set()
    for cid, (game, policies) in want.items():
        name = by_id[cid].get("trial_name")
        mapped = (
            by_id[cid].get("game") == game and by_id[cid].get("policies") == policies
        )
        safe = isinstance(name, str) and name not in ("", ".", "..") and "/" not in name
        if not mapped or not safe or name in seen_names:
            raise ValueError(f"plan cell {cid} does not match the registered mapping")
        seen_names.add(name)
    return by_id


def _public(value: object) -> bool:
    if value is None or type(value) in (bool, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return not (value.startswith(("/", "\\")) or ".." in value or "\\" in value)
    if isinstance(value, list):
        return all(_public(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and "/" not in k and _public(v) for k, v in value.items()
        )
    return False


def _compare(trial: Path, game: str, policies: dict[str, str]) -> dict[str, Any] | None:
    report = trial / "artifacts" / "logs" / "world" / "pettingzoo.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(_HERE / "reference.py"),
            _GAMES[game],
            "--policies",
            f"{policies['p0']},{policies['p1']}",
            "--seed",
            "19",
            "--report",
            str(report),
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        return None
    try:
        value = decode(proc.stdout.decode("ascii"))
    except (UnicodeDecodeError, TapeError):
        return None
    ok = isinstance(value, dict) and value.get("schema") == "seatlab.pz-reference/v1"
    return value if ok and _public(value) else None


def _fill(folder: Path, row: dict[str, Any]) -> None:
    outcomes = json.loads((folder / "outcomes.json").read_text(encoding="utf-8"))
    steps = json.loads((folder / "order.json").read_text(encoding="utf-8"))["steps"]
    row.update(
        status="projected",
        reason=None,
        completion=outcomes["completion"],
        world_steps=len(steps),
        decisions=sum(len(group) for group in steps),
        returns=outcomes["returns"],
        terminated=outcomes["terminated"],
        truncated=outcomes["truncated"],
        case_sha256=outcomes["case_sha256"],
        native_trial_id=outcomes["native_trial_id"],
    )


def collect(native_root: Path, out_dir: Path) -> dict[str, Any]:
    if os.path.lexists(out_dir):
        raise FileExistsError(out_dir)
    if not out_dir.parent.is_dir():
        raise ValueError(f"output parent does not exist: {out_dir.parent}")
    plan = _plan(native_root)
    staging = Path(tempfile.mkdtemp(dir=out_dir.parent, prefix=".pz-census-"))
    try:
        (staging / "cells").mkdir()
        rows: list[dict[str, Any]] = []
        for cid, game, _spec, p0, p1 in CELLS:
            policies = {"p0": p0, "p1": p1}
            trial = native_root / "trials" / plan[cid]["trial_name"]
            claim = native_root / "attempts" / cid
            attempted = claim.is_dir() and not claim.is_symlink()
            row: dict[str, Any] = {
                "id": cid,
                "game": game,
                "policies": policies,
                "attempted": attempted,
                "native_trial_id": None,
                "status": "unattempted",
                "reason": "not_started",
                **dict.fromkeys(_NUMS),
            }
            if trial.is_symlink():
                row["status"], row["reason"] = "invalid", "trial_symlink"
            elif not trial.is_dir():
                if attempted:
                    row["status"], row["reason"] = "invalid", "missing_trial"
            elif not attempted:
                row["status"], row["reason"] = "invalid", "unclaimed_trial"
            else:
                result = trial / "result.json"
                if result.is_symlink():
                    row["status"], row["reason"] = "invalid", "result_symlink"
                elif not result.is_file():
                    row["status"], row["reason"] = "invalid", "missing_result"
                else:
                    try:
                        loaded = json.loads(result.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        loaded = None
                    if not isinstance(loaded, dict):
                        row["status"], row["reason"] = "invalid", "bad_result"
                    else:
                        row["native_trial_id"] = _uuid(loaded.get("id"))
                        folder = staging / "cells" / cid
                        try:
                            project(trial, folder)
                            _fill(folder, row)
                        except (ValueError, OSError, TapeError):
                            row["status"], row["reason"] = (
                                "invalid",
                                "projection_failed",
                            )
                            shutil.rmtree(folder, ignore_errors=True)
            rows.append(row)
        owners: dict[str, list[str]] = {}
        for row in rows:
            if row["native_trial_id"]:
                owners.setdefault(row["native_trial_id"], []).append(row["id"])
        (staging / "references").mkdir()
        for row in rows:
            names = owners.get(row["native_trial_id"], [])
            if row["native_trial_id"] and len(names) > 1:
                row.update(status="duplicate_uuid", reason="duplicate_uuid")
                row.update(dict.fromkeys(_NUMS))
                shutil.rmtree(staging / "cells" / row["id"], ignore_errors=True)
                continue
            if row["status"] != "projected":
                continue
            trial = native_root / "trials" / plan[row["id"]]["trial_name"]
            receipt = _compare(trial, row["game"], row["policies"])
            if receipt is None:
                row.update(
                    status="reference_failed",
                    reason="reference_failed",
                )
                continue
            (staging / "references" / f"{row['id']}.json").write_bytes(
                canonical(receipt) + b"\n"
            )
            row.update(status="matched", reason=None, reference_sha256=digest(receipt))
        counts = {
            name: sum(row["status"] == name for row in rows) for name in _STATUSES
        }
        measured = counts["matched"] + counts["reference_failed"]
        index = {
            "schema": "seatlab.pz-census/v1",
            "counts": {"registered": 8, "measured": measured, **counts},
            "cells": rows,
        }
        (staging / "index.json").write_bytes(canonical(index) + b"\n")
        os.rename(staging, out_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_root", type=Path, metavar="NATIVE_ROOT")
    parser.add_argument("--out", required=True, type=Path, metavar="NEW_DIR")
    args = parser.parse_args(argv)
    try:
        index = collect(args.native_root, args.out)
    except (ValueError, OSError, TapeError) as exc:
        print(f"collect.py: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"counts": index["counts"], "schema": index["schema"]}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
