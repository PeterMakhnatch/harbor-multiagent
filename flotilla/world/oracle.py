"""Reference play for one seat, run by Harbor's `oracle` agent (no language model).

`solo` and `silent`: submit the best answer from this seat's own briefing.
`talk`: post the briefing to everyone, wait for every teammate's briefing, submit the full answer.
The family module sits next to this file (solution/family.py). See CONTRACT.md 8.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

WORLD = os.environ.get("WORLD_URL", "http://world:8080")
HEADERS = {"Authorization": f"Bearer {os.environ['SEAT_TOKEN']}", "Content-Type": "application/json"}
MARK = "FLOTILLA-BRIEFING "


def call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(WORLD + path, method=method, data=data, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def family_module() -> Any:
    path = Path(__file__).with_name("family.py")
    spec = importlib.util.spec_from_file_location("family", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    family = family_module()
    me = call("GET", "/whoami")
    own = json.loads(os.environ["SEAT_BRIEFING"])
    if me["condition"] in ("solo", "silent"):
        answer = family.oracle_answer([own])
    else:
        call("POST", "/messages", {"to": "all", "text": MARK + json.dumps(own, sort_keys=True)})
        others = {seat for seat in me["seats"] if seat != me["seat"]}
        parts: dict[str, Any] = {}
        deadline = time.monotonic() + 300
        while set(parts) < others and time.monotonic() < deadline:
            for message in call("GET", "/messages")["messages"]:
                if message["from"] in others and message["text"].startswith(MARK):
                    parts[message["from"]] = json.loads(message["text"][len(MARK) :])
            time.sleep(1)
        if set(parts) < others:
            print(f"missing briefings from {sorted(others - set(parts))}", file=sys.stderr)
        answer = family.oracle_answer([own, *(parts[seat] for seat in sorted(parts))])
    print(json.dumps(call("POST", "/submit", {"answer": answer})))


if __name__ == "__main__":
    main()
