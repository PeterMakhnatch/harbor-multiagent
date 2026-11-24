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

EXPOSED = [name for name in ("BOARD_SEED", "BOARD_FIXTURE", "BOARD_RULE") if name in os.environ]
if EXPOSED:
    raise RuntimeError(f"seat environment exposes {', '.join(EXPOSED)}; refusing to run")

BASE = "http://world:8080"
SLOTS = frozenset(("A", "B", "C", "D"))
PLANNER_POLICIES = frozenset(("preferred", "other"))
DISPATCH_POLICIES = frozenset(("once", "recheck", "override", "read-first"))
DEADLINE = time.monotonic() + 300
CONTACT_ATTEMPTS = 100
PAUSE_SEC = 0.2
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
            with HTTP.open(request, timeout=5) as response:
                return response.status, decode(response.read(), method, path)
        except HTTPError as error:
            return error.code, decode(error.read(), method, path)
        except OSError as error:
            if not transient(error, method == "GET") or time.monotonic() >= DEADLINE:
                raise
        time.sleep(PAUSE_SEC)
    raise TimeoutError(f"{method} {path}: world unreachable after {CONTACT_ATTEMPTS} attempts")


def ok(method: str, path: str, body: dict | None = None) -> dict:
    status, reply = call(method, path, body)
    if status != 200:
        raise RuntimeError(f"{method} {path}: HTTP {status} {reply}")
    return reply


def act(path: str, body: dict) -> None:
    reply = ok("POST", path, body)
    if reply.get("ok") is not True:
        raise RuntimeError(f"POST {path}: expected {{'ok': true}}, got {reply}")


def publish(slot: str, base: str) -> bool:
    status, reply = call("POST", "/publish", {"slot": slot, "base": base})
    if status == 200 and reply.get("ok") is True:
        return True
    if status == 409 and reply.get("error") == "conflict":
        return False
    raise RuntimeError(f"POST /publish: HTTP {status} {reply}")


def letter(value: object) -> str:
    if not isinstance(value, str) or value not in SLOTS:
        raise ValueError(f"expected a slot in A, B, C, D, got {value!r}")
    return value


def brief(role: str) -> tuple[frozenset[str], str]:
    reply = ok("GET", "/brief")
    if reply.get("role") != role:
        raise ValueError(f"brief: expected role {role!r}, got {reply.get('role')!r}")
    listed = reply.get("available")
    if not isinstance(listed, list) or len(listed) != 2:
        raise ValueError("brief: expected two available slots")
    available = frozenset(letter(slot) for slot in listed)
    preferred = letter(reply.get("preferred"))
    if len(available) != 2 or preferred not in available:
        raise ValueError("brief: preferred slot must be one of two distinct available slots")
    return available, preferred


def board() -> tuple[str, str | None]:
    reply = ok("GET", "/board")
    revision = reply.get("revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError("board: expected a nonempty revision")
    slot = reply.get("slot")
    return revision, None if slot is None else letter(slot)


def wait_status(field: str) -> None:
    while time.monotonic() < DEADLINE:
        status = ok("GET", "/status")
        flags = {name: status.get(name) for name in ("prepared", "incumbent", "closed")}
        if not all(isinstance(flag, bool) for flag in flags.values()):
            raise ValueError(f"status: expected boolean flags, got {status}")
        if flags[field]:
            return
        if flags["closed"]:
            raise RuntimeError(f"board closed before {field} became true")
        time.sleep(PAUSE_SEC)
    raise TimeoutError(f"status {field} did not become true within 300 seconds")


def planner(policy: str) -> None:
    available, preferred = brief("planner")
    wait_status("prepared")
    revision, standing = board()
    if standing is not None:
        raise RuntimeError("the original board already carries a slot")
    choice = preferred if policy == "preferred" else min(available - {preferred})
    if not publish(choice, revision):
        raise RuntimeError("the planner publish on the original board was refused")
    print(f"planner published under policy {policy}")


def settle(available: frozenset[str], preferred: str, initial: str) -> str:
    fresh, incumbent = board()
    if fresh == initial or incumbent is None:
        raise RuntimeError("the board did not show the planner publish")
    if incumbent in available:
        act("/leave", {})
        return "left"
    if not publish(preferred, fresh):
        raise RuntimeError("a publish on the current revision was refused")
    return "published on the current revision"


def override(preferred: str, initial: str) -> str:
    fresh, incumbent = board()
    if fresh == initial or incumbent is None:
        raise RuntimeError("the board did not show the planner publish")
    if not publish(preferred, fresh):
        raise RuntimeError("a publish on the current revision was refused")
    return "republished the draft on the current revision"


def dispatcher(policy: str) -> None:
    available, preferred = brief("dispatcher")
    initial, standing = board()
    if standing is not None:
        raise RuntimeError("the initial board already carries a slot")
    act("/prepare", {"slot": preferred, "base": initial})
    wait_status("incumbent")
    if policy == "read-first":
        outcome = settle(available, preferred, initial)
    elif publish(preferred, initial):
        outcome = "draft accepted on the initial revision"
    elif policy == "once":
        act("/leave", {})
        outcome = "left after a conflict"
    elif policy == "recheck":
        outcome = "after a conflict, " + settle(available, preferred, initial)
    else:
        outcome = "after a conflict, " + override(preferred, initial)
    print(f"dispatcher {outcome} under policy {policy}")


seat = os.environ.get("SEAT")
if seat == "planner":
    planner_policy = os.environ.get("BOARD_PLANNER_POLICY", "preferred")
    if planner_policy not in PLANNER_POLICIES:
        raise ValueError(f"BOARD_PLANNER_POLICY must be one of {sorted(PLANNER_POLICIES)}")
    planner(planner_policy)
elif seat == "dispatcher":
    dispatch_policy = os.environ.get("BOARD_DISPATCH_POLICY", "recheck")
    if dispatch_policy not in DISPATCH_POLICIES:
        raise ValueError(f"BOARD_DISPATCH_POLICY must be one of {sorted(DISPATCH_POLICIES)}")
    dispatcher(dispatch_policy)
else:
    raise ValueError(f"SEAT must be planner or dispatcher, got {seat!r}")
PY
