"""Play the registered cycle from past actions in the supplied window only.

python cycle.py 1
python cycle.py 2

1 repeats 0,1,0,2. 2 repeats 0,2,0,1. stdin is one seatlab.actor-window/v1
object. stdout is {"action": int}. The current view is not a move source.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

SCHEMA = "seatlab.actor-window/v1"


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in items:
        if key in out:
            raise ValueError(f"duplicate JSON key: {key!r}")
        out[key] = item
    return out


def _reject_constant(token: str) -> Any:
    raise ValueError(token)


def _load() -> Any:
    try:
        return json.loads(
            sys.stdin.read(),
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit("invalid policy input") from exc


def _window(value: object) -> list[int]:
    if (
        type(value) is not dict
        or set(value) != {"schema", "current", "history"}
        or value["schema"] != SCHEMA
    ):
        raise SystemExit("policy input must be a seatlab.actor-window/v1 object")
    current, history = value["current"], value["history"]
    if type(current) is not dict or current.get("schema") == SCHEMA:
        raise SystemExit("current must be the raw private view")
    if type(history) is not list or len(history) > 16:
        raise SystemExit("history must be the last 0 to 16 records")
    actions: list[int] = []
    for item in history:
        if type(item) is not dict or set(item) != {"view", "action"}:
            raise SystemExit("history item must contain view and action")
        action, view = item["action"], item["view"]
        if (
            type(view) is not dict
            or view.get("schema") == SCHEMA
            or type(action) is not int
            or action not in (0, 1, 2)
        ):
            raise SystemExit("history action must be a past move")
        actions.append(action)
    return actions


def decide(first: int, actions: list[int]) -> int:
    if not actions or actions[-1] != 0:
        return 0
    previous = next((action for action in reversed(actions[:-1]) if action != 0), None)
    if previous is None:
        return first
    return (3 - first) if previous == first else first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "first",
        choices=("1", "2"),
        help="first nonzero move: 1 ascending, 2 mirrored",
    )
    first = int(parser.parse_args().first)
    action = decide(first, _window(_load()))
    sys.stdout.write(
        json.dumps({"action": action}, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
