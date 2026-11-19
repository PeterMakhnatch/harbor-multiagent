"""Predict the opponent's next move from the supplied actor window only.

stdin is one seatlab.actor-window/v1 object. stdout is {"action": int}.
The program is identical in every cell. It keeps no state outside that object.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

SCHEMA = "seatlab.actor-window/v1"
# rps_v2 encodes "no action yet" as num_actions. The frozen game uses 3.
START_SENTINEL = 3


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


def _observation(view: object) -> int:
    if type(view) is not dict:
        raise SystemExit("view must be an object")
    seen = view.get("observation")
    if type(seen) is not int or seen not in (0, 1, 2, START_SENTINEL):
        raise SystemExit("observation is not the start sentinel or a move")
    return seen


def _window(value: object) -> tuple[dict[str, Any], list[Any]]:
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
    for item in history:
        if (
            type(item) is not dict
            or set(item) != {"view", "action"}
            or type(item["view"]) is not dict
            or item["view"].get("schema") == SCHEMA
            or type(item["action"]) is not int
        ):
            raise SystemExit("history item must be a raw view and native action")
        _observation(item["view"])
    return current, history


def decide(current: dict[str, Any], history: list[Any]) -> int:
    seen = _observation(current)
    if seen == START_SENTINEL or seen in (1, 2):
        prediction = 0
    else:
        prior = _observation(history[-1]["view"]) if history else None
        prediction = 2 if prior == 1 else 1
    return (prediction + 1) % 3


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    current, history = _window(_load())
    action = decide(current, history)
    sys.stdout.write(
        json.dumps({"action": action}, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
