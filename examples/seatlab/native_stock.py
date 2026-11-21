"""Compare an exported SeatLab team panel with original trials using stock Eval Lab."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

from . import evidence, team_driver
from .handoff.external_audit import _report


def compare(report: dict[str, Any], study: Path) -> dict[str, Any]:
    # Optional consumer dependency. No Eval Lab database or provider is opened.
    from evallab.results import load_trial

    if not study.is_absolute() or study != study.resolve(strict=True):
        raise ValueError("use the physical absolute path of the original study")
    register = json.loads(team_driver._read_sealed(study / "register.json"))
    if team_driver._bytes(register["plan"]) != team_driver._bytes(report["plan"]):
        raise ValueError("public register differs from the original native study")
    cells = report["plan"]["cells"]
    rows = report["rows"]
    if (
        not isinstance(cells, list)
        or not cells
        or not isinstance(rows, list)
        or len(cells) != len(rows)
    ):
        raise ValueError("expected one outcome slot per registered cell")
    used: set[str] = set()
    trial_ids: set[str] = set()
    worlds: set[str] = set()
    actions = optimal = 0
    seats = Counter[str]()
    with_tokens = Counter[str]()
    for cell, row in zip(cells, rows, strict=True):
        if not isinstance(cell, dict) or not isinstance(row, dict):
            raise ValueError("malformed public cell or outcome row")
        key = cell["cell_id"]
        if (
            not isinstance(key, str)
            or not key.isascii()
            or not key
            or len(key) > 128
            or any(not (ch.isalnum() or ch == "-") for ch in key)
            or key in used
            or any(row.get(k) != v for k, v in cell.items())
        ):
            raise ValueError("unregistered or duplicate cell in public report")
        used.add(key)
        out = row["outcome"]
        if (row["state"] == "complete") != (out is not None):
            raise ValueError("public completion state differs from outcome")
        if out is None:
            continue
        trial = team_driver._trial_dir(study, cast(team_driver.Cell, cell))
        if trial != trial.resolve(strict=True):
            raise ValueError("native source no longer under the original trial path")
        receipt, world = evidence.read_trial_with_world(trial)
        native = load_trial(trial)
        if (
            native.id != out["trial_id"]
            or native.id != receipt["trial_id"]
            or native.id in trial_ids
            or native.primary_reward != out["native_score"]
            or receipt["seed"] != cell["seed"]
            or receipt["condition"] != cell["condition"]
            or receipt["fingerprint"] != out["fingerprint"]
            or native.result.get("trial_uri") != trial.as_uri()
        ):
            raise ValueError("Eval Lab trial identity or native reward differs")
        trial_ids.add(native.id)
        worlds.add(receipt["fingerprint"])
        grades = receipt["score"]["rows"]
        accepted = [
            {
                "id": g["id"],
                "choice": item["chosen"],
                "optimal": g["optimal"],
                "feasible": g["feasible"],
            }
            for g, item in zip(grades, world["cases"], strict=True)
        ]
        if team_driver._bytes(accepted) != team_driver._bytes(out["actions"]):
            raise ValueError(
                "independently scored native action differs from public row"
            )
        actions += len(accepted)
        optimal += sum(g["optimal"] for g in grades)
        for role, raw in evidence._by_role(
            native.result["seat_results"], "seat_results"
        ).items():
            seats[role] += 1
            if all(
                type(raw["agent_result"].get(k)) is int
                for k in ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
            ):
                with_tokens[role] += 1
    return {
        "schema": "seatlab.native-stock-join/v1",
        "panel_schema": report["schema"],
        "scheduled": len(cells),
        "matched_native_trials": len(trial_ids),
        "matched_native_worlds": len(worlds),
        "matched_native_actions": actions,
        "optimal": optimal,
        "matched_seats": dict(seats),
        "with_native_token_counts": {k: with_tokens[k] for k in seats},
        "mismatches": 0,
    }


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("report", type=Path)
    cli.add_argument("--study", type=Path, required=True)
    args = cli.parse_args(argv)
    try:
        result = compare(_report(args.report), args.study)
    except ModuleNotFoundError as e:
        if e.name != "evallab":
            raise
        print(
            "Run with a Python environment that has stock Eval Lab installed.",
            file=sys.stderr,
        )
        return 1
    except (ValueError, OSError, TypeError, KeyError, RecursionError):
        print("Native evidence differs or cannot be read.", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
