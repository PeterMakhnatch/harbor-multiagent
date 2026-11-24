#!/usr/bin/env python3
"""A no-model example of the host decision interface, not an assistant or a judge."""

import json
import sys
from typing import Any


PREFIX = "operator/"
POLICIES = ("inspect-stale-control", "recheck-control", "stop-control")
LIMIT = 16 * 1024


def decide(label: str, view: dict[str, Any]) -> str:
    role, stage, preferred = view["role"], view["stage"], view["preferred"]
    if role == "planner" and stage == "plan" and label == "preferred-control":
        return f"publish:{preferred}@draft"
    if role != "dispatcher" or label not in POLICIES:
        raise ValueError("unsupported scripted participant")
    if stage == "prepare":
        return f"prepare:{preferred}"
    if stage != "decide":
        raise ValueError("unsupported dispatcher stage")
    if label == "stop-control":
        return "stop"
    if label == "inspect-stale-control" and view["board"] is None:
        return "read"
    if view["last"] is None:
        return f"publish:{view['draft']}@draft"
    if view["board"] is None:
        return "read"
    if view["board"] in view["available"]:
        return "leave"
    return f"publish:{view['draft']}@current"


def main() -> int:
    data = sys.stdin.buffer.read(LIMIT + 1)
    if not data or len(data) > LIMIT:
        return 2
    try:
        request = json.loads(data)
        if set(request) != {"schema", "requested", "instructions", "view"}:
            return 2
        if request["schema"] != "harbor.shift-board.turn/v1":
            return 2
        name = request["requested"]
        if not isinstance(name, str) or not name.startswith(PREFIX):
            return 2
        view = request["view"]
        choice = decide(name.removeprefix(PREFIX), view)
        if choice not in view["choices"]:
            return 2
    except (TypeError, ValueError, KeyError, RecursionError):
        return 2
    print(
        json.dumps(
            {
                "schema": "harbor.shift-board.choice/v1",
                "requested": name,
                "choice": choice,
                "served_model": None,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
