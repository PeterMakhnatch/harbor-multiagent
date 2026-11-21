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
    plan: list[tuple[dict[str, Any], str]],
    destination: Path,
    ledger: Path,
    *,
    run_prefix: str = _PREFIX,
    schema: str = "seatlab.calendar-presentation/v1",
    original_receipt_sha256: str = _SOURCE_SHA256,
) -> dict[str, Any]:
    import re
    from contextlib import ExitStack

    from ..visibility import deepseek, spend

    bound = os.environ.get("SEATLAB_SPEND_DIR")
    if not bound or not Path(bound).is_absolute() or ledger.absolute() != Path(bound):
        raise ValueError("spend_directory_mismatch")
    ledger = Path(bound)
    runs = [row.get("run") for row, _ in plan]
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9_-]*-", run_prefix)
        or any(
            not isinstance(run, str)
            or not run.startswith(run_prefix)
            or run == run_prefix
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", run)
            for run in runs
        )
        or len(set(runs)) != len(runs)
    ):
        raise ValueError("invalid_replay_identity")

    if destination.parent.resolve(strict=True) != destination.parent:
        raise ValueError("expected_physical_study_parent")
    if destination.is_relative_to(Path(__file__).resolve().parents[3]):
        raise ValueError("keep_study_outside_the_checkout")
    if spend.report(ledger, run_prefix=run_prefix)["calls"]:
        raise ValueError("replay_identity_already_used")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        parent = os.open(destination.parent, flags)
        stack.callback(os.close, parent)
        os.mkdir(destination.name, mode=0o700, dir_fd=parent)
        directory = os.open(destination.name, flags, dir_fd=parent)
        stack.callback(os.close, directory)
        if os.fstat(directory).st_uid != os.geteuid():
            raise ValueError("private_study_directory_required")
        os.fchmod(directory, 0o700)
        os.fsync(parent)

        def create(name: str):
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            return os.fdopen(fd, "w", encoding="utf-8")

        deepseek.bind_host_key()
        spend.bind_host_spend_dir()
        stopped = None
        with create("attempts.jsonl") as attempts:
            os.fsync(directory)
            for row, prompt in plan:
                attempts.write(p.wire({"started": row["run"]}) + "\n")
                attempts.flush()
                os.fsync(attempts.fileno())
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
                os.fsync(attempts.fileno())
        result = {
            "schema": schema,
            "original_receipt_sha256": original_receipt_sha256,
            "model": deepseek.MODEL,
            "stopped": stopped,
            "cells": [row for row, _ in plan],
            "meter": spend.report(ledger, run_prefix=run_prefix),
        }
        temporary = ".receipt.json.tmp"
        with create(temporary) as output:
            output.write(json.dumps(result, sort_keys=True, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(
            temporary,
            "receipt.json",
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
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
