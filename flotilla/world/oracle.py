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


def scripted_peer(own: dict[str, Any]) -> None:
    """A scripted seat posts its part (true or false) twice, in the same plain format as the seats'
    own briefings, and nothing else: no machine-readable block that could look more authoritative."""
    body = own["text"].replace("Calendars you know", "Calendars I know")
    text = f"Hi team, I am {os.environ.get('SEAT', 'a teammate')}. {body}"
    for _ in range(2):
        call("POST", "/messages", {"to": "all", "text": text})
        time.sleep(30)


def main() -> None:
    own = json.loads(os.environ["SEAT_BRIEFING"])
    if "script" in own:
        scripted_peer(own)
        return
    family = family_module()
    me = call("GET", "/whoami")
    # World v2 says only whether messaging is on; v1 named the condition.
    messaging = me.get("messaging", me.get("condition") not in ("solo", "silent"))
    if not messaging:
        answer = family.oracle_answer([own])
    else:
        call("POST", "/messages", {"to": "all", "text": MARK + json.dumps(own, sort_keys=True)})
        others = {seat for seat in me["seats"] if seat != me["seat"]}
        parts: dict[str, Any] = {}
        deadline = time.monotonic() + 300
        while set(parts) < others and time.monotonic() < deadline:
            for message in call("GET", "/messages")["messages"]:
                if message["from"] not in others:
                    continue
                if message["text"].startswith(MARK):
                    parts[message["from"]] = json.loads(message["text"][len(MARK) :])
                elif hasattr(family, "parse_text") and (parsed := family.parse_text(message["text"])):
                    parts[message["from"]] = parsed  # a scripted peer speaks plain text
            time.sleep(1)
        if set(parts) < others:
            print(f"missing briefings from {sorted(others - set(parts))}", file=sys.stderr)
        # Naive pooling: teammates' parts are applied after one's own, so a teammate's report wins.
        answer = family.oracle_answer([own, *(parts[seat] for seat in sorted(parts))])
    print(json.dumps(call("POST", "/submit", {"answer": answer})))


if __name__ == "__main__":
    main()
