#!/bin/bash
set -euo pipefail

exec python3 - <<'PY'
import json
import os
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

BASE = "http://world:8080"
SLOTS = frozenset(("A", "B", "C"))
POLICIES = frozenset(("fresh", "stale", "late", "ignore", "unconfirmed"))
DEADLINE = time.monotonic() + 300
HTTP = build_opener(ProxyHandler({}))
TOKEN = Path("/run/role/token").read_text(encoding="utf-8").strip()


def call(method: str, path: str, body: dict[str, str] | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(BASE + path, data=data, method=method)
    request.add_unredirected_header("Authorization", f"Bearer {TOKEN}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with HTTP.open(request, timeout=5) as response:
        reply = json.load(response)
    if not isinstance(reply, dict):
        raise ValueError(f"{method} {path}: expected a JSON object")
    return reply


def slot(value: object) -> str:
    if not isinstance(value, str) or value not in SLOTS:
        raise ValueError(f"expected a slot in A, B, C, got {value!r}")
    return value


def poll(read, ready, what: str) -> dict:
    while time.monotonic() < DEADLINE:
        reply = read()
        if ready(reply):
            return reply
        time.sleep(0.2)
    raise TimeoutError(f"{what} did not become ready within 300 seconds")


def reports(inbox: dict) -> list[dict]:
    listed = inbox.get("reports")
    if not isinstance(listed, list) or not all(isinstance(r, dict) for r in listed):
        raise ValueError("inbox: expected a list of report objects")
    return listed


def report_slot(inbox: dict, phase: str) -> str | None:
    for report in reports(inbox):
        if report.get("phase") == phase:
            return slot(report.get("slot"))
    return None


def reporter() -> None:
    reported: set[str] = set()
    while True:
        brief = poll(
            lambda: call("GET", "/brief"),
            lambda reply: reply.get("phase") != "waiting",
            "GET /brief",
        )
        phase = brief.get("phase")
        if phase == "done":
            print(f"reporter sent {sorted(reported)}")
            return
        if phase not in ("initial", "revision") or phase in reported:
            raise ValueError(f"brief: unexpected phase {phase!r}")
        call("POST", "/report", {"phase": phase, "slot": slot(brief.get("slot"))})
        reported.add(phase)


def has(phase: str):
    return lambda inbox: report_slot(inbox, phase) is not None


class Booker:
    def __init__(self, policy: str) -> None:
        self.policy = policy
        self.acknowledged: set[str] = set()

    def read_inbox(self) -> dict:
        inbox = call("GET", "/inbox")
        for report in reports(inbox):
            phase = report.get("phase")
            if phase in self.acknowledged:
                continue
            if self.policy == "unconfirmed" and phase == "revision":
                continue
            receipt = report.get("receipt")
            if phase not in ("initial", "revision") or not isinstance(receipt, str):
                raise ValueError("inbox: expected a phase and a receipt")
            call("POST", "/ack", {"phase": phase, "receipt": receipt})
            self.acknowledged.add(phase)
        return inbox

    def wait_for(self, phase: str) -> str:
        inbox = poll(self.read_inbox, has(phase), f"the {phase} report")
        found = report_slot(inbox, phase)
        if found is None:
            raise RuntimeError(f"the {phase} report vanished")
        return found

    def play(self) -> None:
        initial = self.wait_for("initial")
        call("POST", "/draft", {"slot": initial})
        if self.policy in ("stale", "late"):
            call("POST", "/commit", {"slot": initial})
            if self.policy == "late":
                self.wait_for("revision")
            print(f"booker committed the initial report under policy {self.policy}")
            return
        revision = self.wait_for("revision")
        final = initial if self.policy == "ignore" else revision
        call("POST", "/commit", {"slot": final})
        print(f"booker committed after reading the revision under policy {self.policy}")


seat = os.environ.get("SEAT")
if seat == "reporter":
    reporter()
elif seat == "booker":
    policy = os.environ.get("HANDOFF_POLICY", "fresh")
    if policy not in POLICIES:
        raise ValueError(f"HANDOFF_POLICY must be one of {sorted(POLICIES)}")
    Booker(policy).play()
else:
    raise ValueError(f"SEAT must be reporter or booker, got {seat!r}")
PY
