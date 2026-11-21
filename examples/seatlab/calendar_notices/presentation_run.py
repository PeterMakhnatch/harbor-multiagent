"""Replay the registered 24 decision contexts without starting a native team."""

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import protocol as p
from .presentation import contexts

_PREFIX = "cpr-"
_RECEIPT = Path(__file__).resolve().parents[1] / "results/calendar-notice-live.json"
_SOURCE_SHA256 = "d3963c4a972fec850f9846f91b7e1427ed59d7b0779adf91948f37b0e5088856"


def _plan(native: Path) -> list[tuple[dict[str, Any], str]]:
    source = _RECEIPT.read_bytes()
    if p.digest(source) != _SOURCE_SHA256:
        raise ValueError("original_receipt_changed")
    original = {row["run"]: row for row in json.loads(source)["cells"]}
    prompts = contexts(native)
    result = []
    for i, world in enumerate(p.WORLDS):
        for j, notice in enumerate(p.ARMS):
            cell = p.Cell(world, notice)
            raw, compact = prompts[cell.run]
            order = (("raw", raw), ("list", compact))
            for presentation, prompt in order if (i + j) % 2 == 0 else order[::-1]:
                result.append(
                    (
                        {
                            "run": f"{_PREFIX}{world}-{notice}-{presentation}",
                            "world": world,
                            "notice": notice,
                            "presentation": presentation,
                            "source_uuid": original[cell.run]["trial_uuid"],
                            "source_result_sha256": original[cell.run]["result_sha256"],
                            "prompt_sha256": p.digest(prompt),
                            "status": "unobserved",
                            "response_sha256": None,
                            "served_model": None,
                            "slot": None,
                        },
                        prompt,
                    )
                )
    if len(result) != 48 or len(original) != len(prompts) or len(prompts) != 24:
        raise ValueError("incomplete_panel")
    return result


def _collect(
    plan: list[tuple[dict[str, Any], str]], destination: Path, ledger: Path
) -> dict[str, Any]:
    from ..visibility import deepseek, spend

    if destination.parent.resolve(strict=True) != destination.parent:
        raise ValueError("expected_physical_study_parent")
    if destination.is_relative_to(Path(__file__).resolve().parents[3]):
        raise ValueError("keep_study_outside_the_checkout")
    if spend.report(ledger, run_prefix=_PREFIX)["calls"]:
        raise ValueError("replay_identity_already_used")
    destination.mkdir(mode=0o700, exist_ok=False)
    deepseek.bind_host_key()
    spend.bind_host_spend_dir()
    stopped = None
    with (destination / "attempts.jsonl").open("x", encoding="utf-8") as attempts:
        for row, prompt in plan:
            attempts.write(p.wire({"started": row["run"]}) + "\n")
            attempts.flush()
            try:
                turn = deepseek.turn(prompt, run_id=row["run"], role="cal_decide")
            except deepseek.SeatUnavailable as exc:
                stopped = exc.category
                break
            except (OSError, ValueError, sqlite3.Error):
                stopped = "direct_or_ledger_error"
                break
            row.update(
                served_model=turn.served_model, response_sha256=p.digest(turn.text)
            )
            try:
                row["slot"] = p.decode(turn.text, "choice")
                row["status"] = "accepted"
            except (ValueError, RecursionError):
                row["status"] = "invalid"
            attempts.write(p.wire({**row, "response_text": turn.text}) + "\n")
            attempts.flush()
    result = {
        "schema": "seatlab.calendar-presentation/v1",
        "original_receipt_sha256": _SOURCE_SHA256,
        "model": deepseek.MODEL,
        "stopped": stopped,
        "cells": [row for row, _ in plan],
        "meter": spend.report(ledger, run_prefix=_PREFIX),
    }
    with (destination / "receipt.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("plan", "direct"))
    parser.add_argument("native_root", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    plan = _plan(args.native_root)
    if args.operation == "plan":
        if args.destination is not None:
            parser.error("plan does not create a destination")
        print(p.wire([row for row, _ in plan]))
        return 0
    if args.destination is None or not os.environ.get("SEATLAB_SPEND_DIR"):
        parser.error("direct requires --destination and the existing SEATLAB_SPEND_DIR")
    result = _collect(
        plan, args.destination.absolute(), Path(os.environ["SEATLAB_SPEND_DIR"])
    )
    print(
        p.wire(
            {
                "settled_or_held_calls": len(result["meter"]["calls"]),
                "stopped": result["stopped"],
            }
        )
    )
    return int(result["stopped"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
