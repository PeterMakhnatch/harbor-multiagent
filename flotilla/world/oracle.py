"""Reference play for one seat, run by Harbor's `oracle` agent (no language model).

Messaging off (`solo`, `silent`): submit the best answer from this seat's own briefing.
Messaging on (`talk...`): post the briefing to everyone, wait for every teammate's briefing, submit the
full answer. A briefing travels as numbered chunks (`FLOTILLA-BRIEFING <i>/<n> <text>`) that fit the
world's message limits; a seat whose briefing cannot fit sends `FLOTILLA-BRIEFING 0/0` (nothing is
coming). A scripted peer (briefing with a `script` key) posts its part, true or false, as plain text
only; the oracle reads plain text with the family's `parse_text`, if it has one. A confederate (`"script":
"confederate"`, the probe family) plays its plan: timed posts (`{"at_s", "to", "text"}`) and, if the plan has
them, timed submissions (`{"at_s", "submit": {...}}`), measured from its own start. A seat of a family without
pooling (`POOLING = False`) needs no teammate: it submits its own answer at once, and with messaging on it holds
play open for `ORACLE_HOLD_SECONDS` (or the environment's `ORACLE_HOLD_SECONDS`, for tests) and submits again,
through the world's submit gate if there is one. The family module sits next to this file (solution/family.py).
See CONTRACT.md 8 and 12.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
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


def messaging(me: dict[str, Any]) -> bool:
    """World v2 says only whether messaging is on; v1 named the condition."""
    return bool(me.get("messaging", me.get("condition") not in ("solo", "silent")))


def limits(me: dict[str, Any]) -> tuple[int, int]:
    """(characters per message, messages per seat); world v1 does not report them."""
    reported = me.get("limits") or {}
    return int(reported.get("max_text", 4000)), int(reported.get("max_messages", 200))


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


def plain_messages(text: str, max_text: int) -> list[str]:
    """Plain text split at line ends into messages of at most max_text characters (long lines are cut)."""
    messages: list[str] = []
    current = ""
    for line in text.splitlines():
        line = line[:max_text]
        if current and len(current) + 1 + len(line) > max_text:
            messages.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    return [*messages, current] if current.strip() else messages


def collect(messages: list[dict[str, Any]], others: set[str],
            parse_text: Callable[[str], Any] | None = None) -> tuple[dict[str, list[Any]], set[str]]:
    """Parts by sender, and the senders that are done (complete, nothing is coming, or plain text read)."""
    pieces: dict[str, dict[int, str]] = {}
    totals: dict[str, int] = {}
    plain: dict[str, list[Any]] = {}
    for message in messages:
        sender, text = message.get("from"), message.get("text") or ""
        if sender not in others:
            continue
        if not text.startswith(MARK):
            parsed = parse_text(text) if parse_text else None
            if parsed:
                plain.setdefault(sender, []).append(parsed)  # a scripted peer speaks plain text
            continue
        header, _, piece = text[len(MARK) :].partition(" ")
        try:
            i, n = (int(x) for x in header.split("/"))
        except ValueError:
            continue
        totals[sender] = n
        pieces.setdefault(sender, {})[i] = piece
    parts: dict[str, list[Any]] = {sender: list(found) for sender, found in plain.items()}
    done = set(plain)
    for sender, n in totals.items():
        got = pieces[sender]
        if n == 0:
            done.add(sender)
        elif all(i in got for i in range(1, n + 1)):
            done.add(sender)
            try:
                parts.setdefault(sender, []).insert(0, json.loads("".join(got[i] for i in range(1, n + 1))))
            except ValueError:
                print(f"unreadable briefing from {sender}", file=sys.stderr)
    return parts, done


def scripted_peer(own: dict[str, Any]) -> None:
    """A scripted seat posts its part (true or false) twice, in the same plain format as the seats'
    own briefings, and nothing else: no machine-readable block that could look more authoritative."""
    max_text, max_messages = limits(call("GET", "/whoami"))
    body = own["text"].replace("Calendars you know", "Calendars I know")
    texts = plain_messages(f"Hi team, I am {os.environ.get('SEAT', 'a teammate')}. {body}", max_text)
    budget = max_messages
    for _round in range(2):
        if not texts or len(texts) > budget:
            break
        for text in texts:
            call("POST", "/messages", {"to": "all", "text": text})
        budget -= len(texts)
        time.sleep(30)


def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST, with a refusal (4xx) returned as {"status", "reply"} instead of raised."""
    try:
        return call("POST", path, body)
    except urllib.error.HTTPError as exc:
        try:
            reply = json.load(exc)
        except ValueError:
            reply = None
        return {"status": exc.code, "reply": reply}


def submit_through_gate(answer: dict[str, Any], attempts: int = 5) -> dict[str, Any]:
    """Submit; a 409 that shows unread messages (the submit gate) has delivered them, so submit again."""
    reply: dict[str, Any] = {}
    for _attempt in range(attempts):
        reply = post("/submit", {"answer": answer})
        if reply.get("status") != 409 or not (reply.get("reply") or {}).get("messages"):
            break
    return reply


def confederate(own: dict[str, Any]) -> None:
    """Play a confederate's plan, each step at its `at_s` seconds after this seat's first call, and print what the
    world replied to each step."""
    call("GET", "/whoami")
    start = time.monotonic()
    for step in sorted(own.get("plan") or [], key=lambda step: float(step["at_s"])):
        time.sleep(max(0.0, start + float(step["at_s"]) - time.monotonic()))
        if "submit" in step:
            path, reply = "/submit", submit_through_gate(step["submit"])
        else:
            path, reply = "/messages", post("/messages", {"to": step.get("to", "all"), "text": step["text"]})
        print(json.dumps({"at_s": round(time.monotonic() - start, 3), "path": path, "reply": reply}), flush=True)


def own_answer(family: Any, own: dict[str, Any], me: dict[str, Any]) -> None:
    """A family without pooling: submit at once (the first attempt is on record); with messaging on, hold play
    open while scripted peers post, then submit again."""
    answer = family.oracle_answer([own])
    print(json.dumps(submit_through_gate(answer)), flush=True)
    if not messaging(me):
        return
    time.sleep(float(os.environ.get("ORACLE_HOLD_SECONDS") or getattr(family, "ORACLE_HOLD_SECONDS", 0.0)))
    print(json.dumps(submit_through_gate(answer)))


def main() -> None:
    own = json.loads(os.environ["SEAT_BRIEFING"])
    if own.get("script") == "confederate":
        confederate(own)
        return
    if "script" in own:
        scripted_peer(own)
        return
    family = family_module()
    me = call("GET", "/whoami")
    if not getattr(family, "POOLING", True):
        own_answer(family, own, me)
        return
    parts: dict[str, list[Any]] = {}
    if messaging(me):
        max_text, max_messages = limits(me)
        payload = json.dumps(own, sort_keys=True, separators=(",", ":"))
        for text in briefing_messages(payload, max_text, max_messages):
            call("POST", "/messages", {"to": "all", "text": text})
        others = {seat for seat in me["seats"] if seat != me["seat"]}
        done: set[str] = set()
        deadline = time.monotonic() + WAIT_SEC
        while can_signal(max_text, max_messages) and not others <= done and time.monotonic() < deadline:
            parts, done = collect(call("GET", "/messages")["messages"], others, getattr(family, "parse_text", None))
            if not others <= done:
                time.sleep(1)
        if set(parts) != others:
            print(f"no briefing from {sorted(others - set(parts))}", file=sys.stderr)
    # Naive pooling: teammates' parts are applied after one's own, so a teammate's report wins.
    answer = family.oracle_answer([own, *(part for seat in sorted(parts) for part in parts[seat])])
    print(json.dumps(call("POST", "/submit", {"answer": answer})))


if __name__ == "__main__":
    main()
