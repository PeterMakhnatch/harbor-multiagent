"""Example external seat owner. It answers one delivered seat turn from its prompt alone and records nothing."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

if __package__:
    from . import control
else:
    _spec = importlib.util.spec_from_file_location(
        "seatlab_fanin_control", Path(__file__).resolve().with_name("control.py")
    )
    if _spec is None or _spec.loader is None:
        raise ImportError("missing sibling control.py")
    control = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(control)

protocol = control.protocol
MODES = ("correct", "wrong", "decline")
_FAILED = 2
_DECLINED = 3


def _validated(prompt: str) -> dict[str, Any]:
    view = control._view(prompt)
    if set(view) == {"own"} and protocol.report_prompt(view["own"]) == prompt:
        return view
    if set(view) == {"own", "reports"}:
        own, reports = view["own"], view["reports"]
        if protocol.book_prompt(own, reports) == prompt or (
            protocol.book_prompt(own, reports, canonical_order=True) == prompt
        ):
            return view
    raise ValueError("not a known seat prompt")


def _delivered_slot(view: dict[str, Any]) -> int:
    remaining = set(view["own"])
    for report in view["reports"]:
        if report["starts"] is not None:
            remaining.intersection_update(report["starts"])
    if len(remaining) != 1:
        raise ValueError("delivered reports do not fix one start")
    return min(remaining)


def reply(mode: str, prompt: str) -> bytes | None:
    if mode not in MODES:
        raise ValueError("unknown mode")
    view = _validated(prompt)
    if mode == "decline":
        return None
    if "reports" not in view:
        body: dict[str, Any] = {"starts": view["own"]}
    else:
        slot = _delivered_slot(view)
        if mode == "wrong":
            slot = min(set(range(protocol.SLOTS)).difference({slot}))
        body = {"slot": slot}
    return control.line({"text": json.dumps(body, separators=(",", ":"))})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(control.MAX_TURN + 1)
        line = reply(args.mode, control._turn(raw))
        if line is None:
            return _DECLINED
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print("example owner did not accept this turn", file=sys.stderr)
        return _FAILED


if __name__ == "__main__":
    raise SystemExit(main())
