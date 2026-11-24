#!/bin/bash
set -euo pipefail

exec python3 - <<'PY'
import json
import os
import re
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

EXPOSED = [name for name in ("SH_PRIOR", "SH_CHANNEL") if name in os.environ]
if EXPOSED:
    raise RuntimeError(f"seat environment exposes {', '.join(EXPOSED)}; refusing to run")

BASE = "http://world:8080"
ROLES = ("outgoing", "relief")
POLICIES = {
    "outgoing": ("original", "draft", "pass", "idle", "late"),
    "relief": ("resume", "fresh", "close", "blind-fresh", "blind-close", "idle", "late-read"),
}
DEFAULT_POLICY = {"outgoing": "original", "relief": "resume"}
REFERENCE = re.compile(r"[0-9a-f]{32}")
SYMBOL = re.compile(r"[a-z][a-z0-9_-]{0,47}")
PAUSE_SEC = 0.5
HANDOFF_WAIT_SEC = 180
SEAL_WAIT_SEC = 300
CONTACT_SEC = 120
CONTACT_ATTEMPTS = int(CONTACT_SEC / PAUSE_SEC) + 1
REQUEST_TIMEOUT_SEC = 5
STARTED = time.monotonic()
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
            if not transient(error, method == "GET"):
                raise
        time.sleep(PAUSE_SEC)
    raise TimeoutError(f"{method} {path}: world unreachable after {CONTACT_ATTEMPTS} attempts")


def symbol(reply: dict) -> str:
    code = reply.get("error")
    return code if isinstance(code, str) and SYMBOL.fullmatch(code) else "unrecognized"


def read(path: str) -> dict:
    status, reply = call("GET", path)
    if status != 200:
        raise RuntimeError(f"GET {path}: HTTP {status} {symbol(reply)}")
    return reply


def act(path: str, body: dict) -> None:
    status, reply = call("POST", path, body)
    if status != 200 or reply != {"ok": True}:
        raise RuntimeError(f"POST {path}: HTTP {status} {symbol(reply)}")


def reference(value: object, what: str) -> str:
    if not isinstance(value, str) or not REFERENCE.fullmatch(value):
        raise ValueError(f"{what}: expected a 32-character lowercase hex reference")
    return value


def flag(reply: dict, key: str) -> bool:
    value = reply.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key}: expected a boolean")
    return value


def brief() -> tuple[str, dict]:
    reply = read("/brief")
    role = reply.get("role")
    if role not in ROLES:
        raise ValueError("brief: unknown role")
    if reply.get("receipt") != "unconfirmed":
        raise ValueError("brief: expected an unconfirmed receipt")
    seat = os.environ.get("SEAT")
    if seat is not None and seat != role:
        raise ValueError("SEAT does not match the role in /brief")
    expected = {"role", "receipt", "request_ref", "draft_ref"} if role == "outgoing" else {"role", "receipt"}
    if set(reply) != expected:
        raise ValueError(f"brief: unexpected fields for role {role}")
    return role, reply


def status() -> tuple[bool, bool]:
    reply = read("/status")
    return flag(reply, "handoff_ready"), flag(reply, "relief_sealed")


def await_flag(index: int, limit_sec: float) -> bool:
    deadline = time.monotonic() + limit_sec
    for _ in range(int(limit_sec / PAUSE_SEC) + 2):
        if status()[index]:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(PAUSE_SEC)
    return False


def inbox() -> str | None:
    reply = read("/inbox")
    ready = flag(reply, "ready")
    served = reply.get("reference")
    if not ready:
        if served is not None:
            raise ValueError("inbox: a reference arrived before the handoff was ready")
        raise RuntimeError("inbox: not ready after status reported handoff_ready")
    return None if served is None else reference(served, "inbox reference")


def outgoing(policy: str, card: dict) -> str:
    original = reference(card.get("request_ref"), "brief request_ref")
    draft = reference(card.get("draft_ref"), "brief draft_ref")
    if original == draft:
        raise ValueError("brief: request_ref and draft_ref must differ")
    if policy == "idle":
        return "no handoff"
    if policy == "late":
        if not await_flag(1, SEAL_WAIT_SEC):
            print(f"relief not sealed within {SEAL_WAIT_SEC} seconds; handing off anyway")
        chosen = original
    else:
        chosen = {"original": original, "draft": draft, "pass": None}[policy]
    act("/handoff", {"reference": chosen})
    return "handoff " + ("none" if chosen is None else "original" if chosen == original else "draft")


def inspected() -> str | None:
    if await_flag(0, HANDOFF_WAIT_SEC):
        return inbox()
    print(f"no handoff within {HANDOFF_WAIT_SEC} seconds; deciding without it")
    return None


def relief(policy: str) -> str:
    if policy == "idle":
        return "no decision"
    if policy == "blind-fresh":
        act("/resolve", {"action": "fresh"})
        return "fresh"
    if policy in ("blind-close", "late-read"):
        act("/resolve", {"action": "close"})
        if policy == "late-read" and await_flag(0, HANDOFF_WAIT_SEC):
            inbox()
            return "close then read"
        return "close"
    served = inspected()
    if policy == "resume" and served is not None:
        act("/resolve", {"action": "resume", "reference": served})
        return "resume"
    action = "fresh" if policy == "fresh" else "close"
    act("/resolve", {"action": action})
    return action


requested = os.environ.get("SH_POLICY")
if requested is not None and not any(requested in allowed for allowed in POLICIES.values()):
    raise ValueError("SH_POLICY is not a known control policy")
role, card = brief()
chosen = DEFAULT_POLICY[role] if requested is None else requested
if chosen not in POLICIES[role]:
    raise ValueError(f"SH_POLICY for {role} must be one of {list(POLICIES[role])}")
outcome = outgoing(chosen, card) if role == "outgoing" else relief(chosen)
print(f"{role} finished under policy {chosen}: {outcome} after {round(time.monotonic() - STARTED)}s")
PY
