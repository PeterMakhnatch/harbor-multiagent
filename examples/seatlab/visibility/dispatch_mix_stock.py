"""Compare a mixed-roster report to original native files using unmodified Eval Lab."""

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import UUID

from .. import team_driver, team_report
from . import dispatch_mix_plan as plan
from . import dispatch_mix_run as runner


def _require(check: bool, reason: str) -> None:
    if not check:
        raise ValueError(reason)


def _safe_result(trial: Path) -> bool:
    _require(trial.resolve() == trial, "nonphysical_trial_directory")
    for name in ("result.json", "config.json", "lock.json", "artifacts/manifest.json"):
        path = trial / name
        _require(path.resolve() == path, "nonphysical_trial_file")
        if path.exists():
            _require(path.is_file(), "invalid_trial_file")
    return (trial / "result.json").is_file()


def _reward(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("invalid_native_reward")
    _require(math.isfinite(value) and 0 <= value <= 1, "invalid_native_reward")
    return float(value)


def _check_configs(
    file: dict[str, Any], result: dict[str, Any], registered: dict[str, Any]
) -> None:
    for value in (file, result):
        team_report._check_config(value, registered)
        # Native result serialization adds an unused default agent to a seat
        # trial. Its standalone config can omit it. Never allow a third actor.
        if "agent" not in value:
            continue
        default = value["agent"]
        _require(
            isinstance(default, dict)
            and default.get("name") == "oracle"
            and all(
                setting is None or setting is False or setting == {} or setting == []
                for key, setting in default.items()
                if key != "name"
            ),
            "unexpected_top_level_agent",
        )
    team_report._check_config(file, {k: v for k, v in result.items() if k != "agent"})


def _trial(
    study: Path,
    cell: plan.Cell,
    public: dict[str, Any],
    load: Callable[[Path], Any],
    seen: set[str],
) -> dict[str, Any]:
    trial = team_driver._trial_dir(study, cell)
    out: dict[str, Any] = {
        "cell_id": cell["cell_id"],
        "stock_score": None,
        "trial_sha256": None,
        "seats": {},
    }
    if not _safe_result(trial):
        _require(
            public.get("native_score") is None and public.get("status") != "valid",
            "reported_without_stock_result",
        )
        return {**out, "status": "no_stock_result"}
    record = load(trial)
    raw = record.result
    _require(
        record.path == trial
        and raw["trial_uri"] == trial.as_uri()
        and raw["trial_name"] == trial.name
        and raw["task_id"]
        == {"path": str(study / "cells" / cell["cell_id"] / "dispatch-handoff")},
        "wrong_native_trial",
    )
    uid = record.id
    _require(
        isinstance(uid, str)
        and str(UUID(uid)) == uid
        and UUID(uid).version == 4
        and raw["id"] == uid
        and uid not in seen,
        "invalid_or_reused_trial_uuid",
    )
    seen.add(uid)
    _require(public.get("trial_id") in (None, uid), "public_uuid_mismatch")
    if public.get("status") == "valid":
        _require(public.get("trial_id") == uid, "unbound_public_score")
    cfg = raw["config"]
    _check_configs(record.config, cfg, runner.config(study, cell))
    participants = cfg["participants"]
    _require(
        len(participants) == 2 and {p["seat"] for p in participants} == set(plan.ROLES),
        "wrong_native_roster",
    )
    for p in participants:
        _require(
            p["model_name"] == plan.model_for(cell, p["seat"])
            and p["kwargs"] == {"run_id": cell["cell_id"]},
            "wrong_native_assignment",
        )
    score = _reward(record.primary_reward)
    _require(_reward(public.get("native_score")) == score, "stock_reward_mismatch")
    seats = raw.get("seat_results") or []
    _require(
        len(seats) <= 2 and len({s["seat"] for s in seats}) == len(seats),
        "duplicate_stock_seat",
    )
    for seat in seats:
        role = seat["seat"]
        _require(role in plan.ROLES, "foreign_stock_seat")
        provider, name = plan.model_for(cell, role).split("/", 1)
        info = (seat.get("agent_info") or {}).get("model_info")
        if info is not None:
            _require(
                info == {"name": name, "provider": provider},
                "stock_seat_model_mismatch",
            )
        context = seat.get("agent_result") or {}
        counts = {}
        for field in ("n_input_tokens", "n_cache_tokens", "n_output_tokens"):
            val = context.get(field)
            _require(
                val is None or type(val) is int and val >= 0, "invalid_stock_tokens"
            )
            counts[field] = val
        out["seats"][role] = {
            "declared_model": plan.model_for(cell, role) if info is not None else None,
            **counts,
        }
    if public.get("status") == "valid":
        _require(
            len(out["seats"]) == 2
            and all(s["declared_model"] for s in out["seats"].values()),
            "missing_native_identity",
        )
    return {
        **out,
        "status": "verified",
        "stock_score": score,
        "trial_sha256": sha256(uid.encode("ascii")).hexdigest(),
    }


def crossread(
    study: Path, report: dict[str, Any], load: Callable[[Path], Any]
) -> dict[str, Any]:
    root = study.resolve(strict=True)
    cells = plan.grid()
    rows = report["cells"]
    by_id = {row["cell_id"]: row for row in rows}
    _require(
        isinstance(rows, list)
        and len(by_id) == len(rows) == len(cells)
        and set(by_id) == {c["cell_id"] for c in cells},
        "wrong_public_register",
    )
    seen: set[str] = set()
    checks = []
    for c in cells:
        row = by_id[c["cell_id"]]
        _require(
            all(type(row.get(k)) is type(v) and row[k] == v for k, v in c.items()),
            "wrong_public_roster",
        )
        checks.append(_trial(root, c, row, load, seen))
    seats = [seat for row in checks for seat in row["seats"].values()]
    return {
        "schema": "seatlab.dispatch-mix-stock/v1",
        "scope": "Stock Eval Lab reads native scores, declared identities and nullable native tokens. It does not verify provider billing.",
        "registered_cells": len(cells),
        "status": dict(Counter(c["status"] for c in checks)),
        "unique_native_trials": len(seen),
        "native_scores": sum(row["stock_score"] is not None for row in checks),
        "seats": len(seats),
        "seats_with_stock_input_tokens": sum(
            s["n_input_tokens"] is not None for s in seats
        ),
        "cells": checks,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    try:
        load_trial = import_module("evallab.results").load_trial
    except ImportError:
        print("Use the Python environment that provides Eval Lab.", file=sys.stderr)
        return 2
    try:
        result = crossread(args.study, json.loads(args.report.read_bytes()), load_trial)
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        print(
            "Cannot match the public register to the native results.", file=sys.stderr
        )
        return 1
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
