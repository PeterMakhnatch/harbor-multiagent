"""Reference play for one seat, run by Harbor's `oracle` agent (no language model).

`solo` and `silent`: submit the best answer from this seat's own briefing.
`talk`: post the briefing to everyone, wait for every teammate's briefing, submit the full answer.
A briefing travels as numbered chunks (`FLOTILLA-BRIEFING <i>/<n> <text>`) that fit the world's
message limits; a seat whose briefing cannot fit sends `FLOTILLA-BRIEFING 0/0` (nothing is coming).
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
HEADERS = {"Authorization": f"Bearer {os.environ.get('SEAT_TOKEN', '')}", "Content-Type": "application/json"}
MARK = "FLOTILLA-BRIEFING "
NOTHING = MARK + "0/0"
WAIT_SEC = 300


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


def can_signal(max_text: int, max_messages: int) -> bool:
    """Whether a seat can send at least the `nothing is coming` message under these limits."""
    return max_messages >= 1 and len(NOTHING) <= max_text


def briefing_messages(payload: str, max_text: int, max_messages: int) -> list[str]:
    """The message texts that carry the payload within the limits."""
    room = max_text - len(MARK) - 2 * len(str(max_messages)) - 2  # room for "<i>/<n> "
    pieces = [payload[k : k + room] for k in range(0, len(payload), room)] if room > 0 else []
    if pieces and len(pieces) <= max_messages:
        return [f"{MARK}{i}/{len(pieces)} {piece}" for i, piece in enumerate(pieces, 1)]
    return [NOTHING] if can_signal(max_text, max_messages) else []


def collect(messages: list[dict[str, Any]], others: set[str]) -> tuple[dict[str, Any], set[str]]:
    """Complete briefings by sender, and the senders that are done (complete, or nothing is coming)."""
    pieces: dict[str, dict[int, str]] = {}
    totals: dict[str, int] = {}
    for message in messages:
        sender, text = message.get("from"), message.get("text") or ""
        if sender not in others or not text.startswith(MARK):
            continue
        header, _, piece = text[len(MARK) :].partition(" ")
        try:
            i, n = (int(x) for x in header.split("/"))
        except ValueError:
            continue
        totals[sender] = n
        pieces.setdefault(sender, {})[i] = piece
    parts: dict[str, Any] = {}
    done: set[str] = set()
    for sender, n in totals.items():
        got = pieces[sender]
        if n == 0:
            done.add(sender)
        elif all(i in got for i in range(1, n + 1)):
            done.add(sender)
            try:
                parts[sender] = json.loads("".join(got[i] for i in range(1, n + 1)))
            except ValueError:
                print(f"unreadable briefing from {sender}", file=sys.stderr)
    return parts, done


def main() -> None:
    family = family_module()
    me = call("GET", "/whoami")
    own = json.loads(os.environ["SEAT_BRIEFING"])
    parts: dict[str, Any] = {}
    if me["condition"] not in ("solo", "silent"):
        max_text, max_messages = int(me.get("max_text", 4000)), int(me.get("max_messages", 200))
        payload = json.dumps(own, sort_keys=True, separators=(",", ":"))
        for text in briefing_messages(payload, max_text, max_messages):
            call("POST", "/messages", {"to": "all", "text": text})
        others = {seat for seat in me["seats"] if seat != me["seat"]}
        done: set[str] = set()
        deadline = time.monotonic() + WAIT_SEC
        while can_signal(max_text, max_messages) and not others <= done and time.monotonic() < deadline:
            parts, done = collect(call("GET", "/messages")["messages"], others)
            if not others <= done:
                time.sleep(1)
        if set(parts) != others:
            print(f"no briefing from {sorted(others - set(parts))}", file=sys.stderr)
    answer = family.oracle_answer([own, *(parts[seat] for seat in sorted(parts))])
    print(json.dumps(call("POST", "/submit", {"answer": answer})))


if __name__ == "__main__":
    main()
