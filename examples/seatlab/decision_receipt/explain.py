"""Explain a saved shipping team's accepted actions beside the hidden supplier state."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from . import card_render

PROG = "python3.12 -BS -m examples.seatlab.workbench decision"
USAGE = (
    f"usage: {PROG} --ticket T [--ticket T] [--json]\n"
    f"       {PROG} --cut PATH [--cut PATH] [--json]\n"
    f"       {PROG} --check-twins [--json]\n"
)
HELP = USAGE + (
    "\n"
    "Show the accepted world events, citation status, supplier state and derived twin of one\n"
    "public scripted ticket or saved cut file. With two selections, also show the first\n"
    "accepted event where they differ. --check-twins replays the registered observed pairs\n"
    "against their derived twins. Nothing starts a team, a world or a provider.\n"
)
MODES = {"--ticket": "tickets", "--cut": "cuts"}
FLAGS = ("--check-twins", "--json")
MAX_SELECTED = 2
MAX_ARGS = 2 * MAX_SELECTED + 1
MAX_ARG_BYTES = 4096


def _refuse(message: str) -> int:
    print(f"decision: {message} Use decision --help.", file=sys.stderr)
    return 2


def _parse(args: list[str]) -> tuple[str, list[str], bool] | str:
    if len(args) > MAX_ARGS or any(
        len(os.fsencode(arg)) > MAX_ARG_BYTES for arg in args
    ):
        return "Too many or oversized arguments."
    mode: str | None = None
    values: list[str] = []
    seen: set[str] = set()
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in FLAGS:
            if arg in seen:
                return "An option is repeated."
            seen.add(arg)
            if arg == "--check-twins":
                mode = mode or "twins"
                if mode != "twins":
                    return "Choose tickets, cut files or --check-twins, not a mix."
            index += 1
            continue
        if arg not in MODES:
            return "Unknown argument."
        if (
            index + 1 >= len(args)
            or args[index + 1].startswith("-")
            or not args[index + 1]
        ):
            return "An option needs a value."
        mode = mode or MODES[arg]
        if mode != MODES[arg]:
            return "Choose tickets, cut files or --check-twins, not a mix."
        values.append(args[index + 1])
        index += 2
    if mode is None:
        return "Select one or two tickets, one or two cut files, or --check-twins."
    if len(values) > MAX_SELECTED or len(set(values)) != len(values):
        return "Select one or two different tickets or cut files."
    return mode, values, "--json" in seen


def _envelopes(mode: str, values: list[str]) -> list[dict[str, Any]]:
    from . import card_inputs

    if mode == "tickets":
        return card_inputs.tickets(list(values))
    return card_inputs.files([Path(value) for value in values])


def _twins(as_json: bool) -> int:
    from .card_audit import audit

    try:
        result = audit()
    except (ValueError, OSError):
        print("decision: The registered twin check cannot be read.", file=sys.stderr)
        return 2
    print(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if as_json
        else card_render.audit_text(result)
    )
    return 0 if result["verdict"] == "matched" else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print(HELP, end="")
        return 0
    parsed = _parse(args)
    if isinstance(parsed, str):
        return _refuse(parsed)
    mode, values, as_json = parsed
    if mode == "twins":
        return _twins(as_json)
    try:
        result = card_render.report(_envelopes(mode, values))
    except (ValueError, OSError):
        subject = "tickets" if mode == "tickets" else "cut files"
        print(
            f"decision: Cannot check the selected {subject}. No report.",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if as_json
        else card_render.text(result)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
