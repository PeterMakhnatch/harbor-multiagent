#!/bin/bash
set -eu

export PYTHONPATH=/app
exec python3 - "$@" <<'PY'
import os
import sys
import time

from world_client import RPCError, rpc

SEATS = ("n0", "n1", "n2", "n3")
FIRST_OUTBOX = {
    "n0": {"Sean": ""},
    "n1": {"Hannah": "known non-neighbor"},
    "n2": {"Bradley": "round 1"},
    "n3": {},
}
SECOND_INBOX = {
    "n0": [("Sean", None), ("Hannah", "round 1"), ("Kathryn", None)],
    "n1": [("Bradley", "")],
    "n2": [("Bradley", None), ("Kathryn", None)],
    "n3": [("Bradley", None), ("Hannah", None)],
}


def expect_denied(path, status, reason, payload=None, *, authenticate=True):
    try:
        rpc(path, payload, authenticate=authenticate)
    except RPCError as error:
        if (error.status, error.reason) == (status, reason):
            print(f"denied {path}: {status} {reason}", flush=True)
            return
        raise
    raise ValueError(f"Expected {reason} rejection for {path}")


def oracle_messages(seat, observed):
    turn = observed["round"]
    inbox = [(item["name"], item["message"]) for item in observed["received"]]
    if turn == 1:
        if inbox:
            raise ValueError("Bootstrap received a message")
        return FIRST_OUTBOX[seat]
    if turn == 2 and inbox != SECOND_INBOX[seat]:
        raise ValueError("Round 2 inbox does not match next-round neighbor delivery")
    if turn == 4 and seat == "n3":
        return {"Hannah": "round 4"}
    return {}


def play(mode):
    seat = os.environ["SEAT"]
    index = SEATS.index(seat)
    probe = mode == "oracle" and seat == "n0"
    if probe:
        expect_denied("/observation", 401, "unauthorized", authenticate=False)
        expect_denied("/messages", 401, "unauthorized", {"round": 1, "messages": {}}, authenticate=False)
        expect_denied("/observation?seat=n1", 403, "cross_role")
        expect_denied("/messages", 403, "cross_role", {"seat": "n1", "round": 1, "messages": {}})

    deadline = time.monotonic() + 45
    observed = rpc("/observation")
    for _ in range(500):
        if time.monotonic() >= deadline:
            raise TimeoutError("World did not finish in 45 seconds")
        if observed["seat"] != seat:
            raise ValueError("Capability did not resolve to this seat")
        turn, phase = observed["round"], observed["phase"]
        if phase == "done":
            if probe:
                expect_denied("/answer", 409, "terminal", {"round": 5, "answer": "Group 4"})
                expect_denied("/messages", 409, "terminal", {"round": 4, "messages": {}})
                if rpc("/observation") != observed:
                    raise ValueError("Terminal observation changed after a denied mutation")
            print(f"{seat} finished the {mode} control", flush=True)
            return
        if observed["submitted"]:
            time.sleep(0.1)
            observed = rpc("/observation")
        elif phase == "messages":
            if probe and turn == 2:
                expect_denied("/messages", 409, "stale_round", {"round": 1, "messages": {}})
            messages = oracle_messages(seat, observed) if mode == "oracle" else {}
            observed = rpc("/messages", {"round": turn, "messages": messages})
            if turn == 1 and observed["round"] == 1 and observed["received"]:
                raise ValueError("World delivered same-round text")
        elif phase == "final":
            if mode == "oracle" and seat == "n2" and observed["received"] != [
                {"name": "Bradley", "message": None},
                {"name": "Kathryn", "message": "round 4"},
            ]:
                raise ValueError("Final inbox does not match round 4")
            color = (1, 1, 2, 3)[index] if mode == "conflict" else index + 1
            observed = rpc("/answer", {"round": turn, "answer": f"Group {color}"})
        else:
            raise ValueError("Invalid world phase")
    raise TimeoutError("World did not finish within 500 observations")


args = sys.argv[1:]
if args not in ([], ["oracle"], ["conflict"]):
    raise SystemExit("Usage: solve.sh [oracle|conflict]")
play(args[0] if args else "oracle")
PY
