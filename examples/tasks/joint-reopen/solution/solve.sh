#!/bin/bash
set -euo pipefail

exec python3 - <<'PY'
import json
import os
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

EXPOSED = [name for name in ("JR_SEED", "JR_FIXTURE", "JR_CHANNEL") if name in os.environ]
if EXPOSED:
    raise RuntimeError(f"seat environment exposes {', '.join(EXPOSED)}; refusing to run")

BASE = "http://world:8080"
ROLES = ("east", "west")
OPTIONS = ("A", "B", "C", "D", "E", "F")
PHASES = ("endorse", "review", "vote", "complete")
POLICIES = frozenset(("keep", "ack-keep", "switch", "recheck"))
WAIT_SEC = 300
PAUSE_SEC = 0.25
MAX_POLLS = int(WAIT_SEC / PAUSE_SEC) + 1
CONTACT_ATTEMPTS = 100
REQUEST_TIMEOUT_SEC = 5
DEADLINE = time.monotonic() + WAIT_SEC
HTTP = build_opener(ProxyHandler({}))
TOKEN = Path("/run/role/token").read_text(encoding="utf-8").strip()
if not TOKEN:
    raise RuntimeError("/run/role/token is empty")


def transient(error: OSError, idempotent: bool) -> bool:
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
        return True
    return idempotent and isinstance(reason, (ConnectionError, TimeoutError))


def decode(raw: bytes, method: str, path: str) -> dict:
    reply = json.loads(raw.decode("utf-8"))
    if not isinstance(reply, dict):
        raise ValueError(f"{method} {path}: expected a JSON object")
    return reply


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    for _ in range(CONTACT_ATTEMPTS):
        request = Request(BASE + path, data=data, method=method)
        request.add_unredirected_header("Authorization", f"Bearer {TOKEN}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with HTTP.open(request, timeout=REQUEST_TIMEOUT_SEC) as response:
                return response.status, decode(response.read(), method, path)
        except HTTPError as error:
            return error.code, decode(error.read(), method, path)
        except OSError as error:
            if not transient(error, method == "GET") or time.monotonic() >= DEADLINE:
                raise
        time.sleep(PAUSE_SEC)
    raise TimeoutError(f"{method} {path}: world unreachable after {CONTACT_ATTEMPTS} attempts")


def read(path: str) -> dict:
    status, reply = call("GET", path)
    if status != 200:
        raise RuntimeError(f"GET {path}: HTTP {status} {reply.get('error')!r}")
    return reply


def act(path: str, body: dict) -> None:
    status, reply = call("POST", path, body)
    if status != 200 or reply.get("ok") is not True:
        raise RuntimeError(f"POST {path}: HTTP {status} {reply.get('error')!r}")


def label(value: object, what: str) -> str:
    if not isinstance(value, str) or value not in OPTIONS:
        raise ValueError(f"{what}: expected one of the public options")
    return value


def labels(value: object, what: str) -> frozenset[str]:
    if not isinstance(value, list) or len(value) > len(OPTIONS):
        raise ValueError(f"{what}: expected a bounded list of options")
    found = frozenset(label(item, what) for item in value)
    if len(found) != len(value):
        raise ValueError(f"{what}: expected distinct options")
    return found


def brief(role: str) -> str:
    reply = read("/brief")
    if reply.get("role") != role:
        raise ValueError(f"brief: expected role {role!r}")
    if reply.get("options") != list(OPTIONS):
        raise ValueError("brief: expected the canonical options A through F")
    return label(reply.get("incumbent"), "brief incumbent")


def phase() -> str:
    current = read("/status").get("phase")
    if current not in PHASES:
        raise ValueError("status: unknown phase")
    return current


def reach(target: str) -> str:
    goal = PHASES.index(target)
    for _ in range(MAX_POLLS):
        current = phase()
        if PHASES.index(current) >= goal:
            return current
        if time.monotonic() >= DEADLINE:
            break
        time.sleep(PAUSE_SEC)
    raise TimeoutError(f"phase {target} was not reached within {WAIT_SEC} seconds")


def ensure(target: str, observed: str) -> None:
    if observed != target:
        raise RuntimeError(f"expected phase {target}, observed {observed}")


def card() -> frozenset[str]:
    return labels(read("/card").get("slots"), "card")


def inbox() -> frozenset[str] | None:
    reply = read("/inbox")
    delivered = reply.get("delivered")
    if delivered is True:
        return labels(reply.get("slots"), "inbox")
    if delivered is False and reply.get("slots") is None:
        return None
    raise ValueError("inbox: expected delivered slots or a withheld null")


def own_alternative(own: frozenset[str], incumbent: str) -> str:
    others = sorted(own - {incumbent})
    if not others:
        raise RuntimeError("own card offers no alternative to the provisional booking")
    return others[0]


def fallback(own: frozenset[str], incumbent: str) -> str:
    return incumbent if incumbent in own else own_alternative(own, incumbent)


def switch_choice(own: frozenset[str], peer: frozenset[str] | None, incumbent: str) -> str:
    shared = sorted((own & peer) - {incumbent}) if peer is not None else []
    return shared[0] if shared else own_alternative(own, incumbent)


def recheck_choice(own: frozenset[str], peer: frozenset[str] | None, incumbent: str) -> str:
    if peer is None:
        return fallback(own, incumbent)
    shared = own & peer
    if incumbent in shared:
        return incumbent
    return min(shared) if shared else fallback(own, incumbent)


def play(role: str, policy: str) -> None:
    incumbent = brief(role)
    ensure("endorse", phase())
    act("/endorse", {"slot": incumbent})
    ensure("review", reach("review"))
    if policy == "keep":
        act("/report", {"slots": []})
        ensure("vote", reach("vote"))
        act("/vote", {"slot": incumbent})
        return
    own = card()
    act("/report", {"slots": sorted(own)})
    ensure("vote", reach("vote"))
    peer = inbox()
    if policy == "ack-keep":
        choice = incumbent
    elif policy == "switch":
        choice = switch_choice(own, peer, incumbent)
    else:
        choice = recheck_choice(own, peer, incumbent)
    act("/vote", {"slot": label(choice, "vote")})


seat = os.environ.get("SEAT")
if seat not in ROLES:
    raise ValueError(f"SEAT must be east or west, got {seat!r}")
chosen = os.environ.get("JR_POLICY", "recheck")
if chosen not in POLICIES:
    raise ValueError(f"JR_POLICY must be one of {sorted(POLICIES)}")
play(seat, chosen)
print(f"{seat} voted under policy {chosen}")
PY
