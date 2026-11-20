"""Use a recorded native prefix offline, or launch a fresh continuation team."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import contract, driver, prefix_packet, prefix_schedule, recommendation, schedule
from .recommendation_report import _dossier


def config(directory: Path, task: Path, cell: schedule.Cell) -> dict[str, Any]:
    return driver._trial_config(
        directory, task, cell, agent_import=driver._PREFIX_IMPORT
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "plan", help="verify the public cut and show the fixed trial order"
    )
    run = commands.add_parser("run", help="claim and launch one eligible native team")
    prompt = commands.add_parser(
        "prompt", help="write just one voter's exact user text"
    )
    decode = commands.add_parser(
        "decode", help="validate an external voter's JSON from stdin"
    )
    for cmd in (prompt, decode):
        cmd.add_argument("--map", type=int, choices=range(6), required=True)
        cmd.add_argument("--role", choices=schedule.ROLES, required=True)
    prompt.add_argument("--arm", choices=schedule.RECOMMENDATION_ARMS, required=True)
    cells = {c["cell_id"]: c for c in prefix_schedule.grid()}
    run.add_argument("--cell", choices=cells, required=True)
    run.add_argument("--task", type=Path, required=True)
    run.add_argument("--harbor", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        cuts = prefix_packet.load(prefix_schedule.PACKET, prefix_schedule.PACKET_SHA256)
    except (OSError, ValueError, RecursionError):
        print("prefix: registered_packet_unavailable", file=sys.stderr)
        return 1
    if args.command == "plan":
        print(
            json.dumps(
                {
                    "schema": "seatlab.native-prefix-plan/v1",
                    "packet_sha256": prefix_schedule.PACKET_SHA256,
                    "task_git_tree": schedule.TASK_TREE,
                    "task_checksum": schedule.TASK_CHECKSUM,
                    "roles": schedule.ROLES,
                    "model": driver._MODEL,
                    "cells": [
                        {**cell, "eligible": cuts[cell["map_index"]] is not None}
                        for cell in cells.values()
                    ],
                },
                indent=2,
            )
        )
        return 0

    map_index = cells[args.cell]["map_index"] if args.command == "run" else args.map
    cut = cuts[map_index]
    if cut is None:
        print("prefix: named_source_ineligible", file=sys.stderr)
        return 1
    if args.command in {"prompt", "decode"}:
        try:
            dossier = _dossier(args.role)
            board = recommendation.board(cut.board, map_index, cut.chair)
            if args.command == "prompt":
                sys.stdout.write(
                    recommendation.turn_prompt(
                        "vote",
                        dossier,
                        board,
                        map_index,
                        args.role,
                        cut.chair,
                        args.arm,
                    )
                )
            else:
                response = sys.stdin.buffer.read(8193)
                if len(response) > 8192:
                    raise ValueError("response too large")
                actual = contract.vote_action(
                    response.decode("utf-8"), dossier, board.facts, map_index
                )
                print(json.dumps(recommendation.blind_vote(actual, map_index)))
        except (ValueError, RecursionError):
            print("prefix: invalid_seat_view_or_ballot", file=sys.stderr)
            return 1
        return 0

    cell = cells[args.cell]
    try:
        error = driver._run(
            args.out,
            cell,
            args.task,
            args.harbor,
            agent_import=driver._PREFIX_IMPORT,
            checkpoint=(prefix_schedule.PACKET_SHA256, cut.prefix_sha256),
        )
    except driver._Rejected as exc:
        error = str(exc)
    except (OSError, ValueError, RecursionError):
        error = "local_setup_failed"
    if error is not None:
        print(f"prefix: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
