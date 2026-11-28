import argparse
import json
import os
import socket
import sys
import time
from typing import Any

SOCKET = os.environ.get("RECALL_SEAT_SOCKET", "") or "/run/recall-seat/rpc.sock"
LIMIT = 65536
TURNS = ("stage", "report", "commit", "close", "done")
WAIT_SECONDS = 300
POLL_SECONDS = 0.25
POLL_LIMIT = int(WAIT_SECONDS / POLL_SECONDS) + 1


def rpc(action: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(action, separators=(",", ":")).encode() + b"\n"
    if len(payload) > LIMIT:
        raise ValueError("request too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(3)
        conn.connect(SOCKET)
        conn.sendall(payload)
        with conn.makefile("rb") as reader:
            line = reader.readline(LIMIT + 1)
    if not line.endswith(b"\n") or len(line) > LIMIT:
        raise ValueError("invalid relay response framing")
    reply = json.loads(line)
    if not isinstance(reply, dict):
        raise TypeError("invalid relay response")
    if reply.get("ok") is False:
        raise ValueError(str(reply.get("error", "request rejected"))[:256])
    if reply.get("ok") is not True or not isinstance(reply.get("data"), dict):
        raise ValueError("invalid relay response")
    return reply["data"]


def wait(turn: str) -> dict[str, Any]:
    if turn not in TURNS:
        raise ValueError(f"unknown turn {turn}")
    deadline = time.monotonic() + WAIT_SECONDS
    current: object = None
    for _ in range(POLL_LIMIT):
        view = rpc({"op": "view"})
        current = view.get("turn")
        if current == turn or current == "done":
            return view
        if current not in TURNS:
            raise ValueError("invalid relay turn")
        if TURNS.index(current) > TURNS.index(turn):
            raise ValueError(f"turn {turn} already passed; current turn {current}")
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    raise ValueError(f"timed out waiting for {turn}; current turn {current}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="recall.py")
    sub = root.add_subparsers(dest="op", required=True)
    sub.add_parser("view")
    sub.add_parser("wait").add_argument("turn", choices=TURNS)
    sub.add_parser("stage").add_argument("bay")
    report = sub.add_parser("report").add_mutually_exclusive_group(required=True)
    report.add_argument("bay", nargs="?")
    report.add_argument("--silent", action="store_true")
    commit = sub.add_parser("commit")
    commit.add_argument("bay")
    commit.add_argument("ticket")
    sub.add_parser("close").add_argument("ticket")
    return root


def request(args: argparse.Namespace) -> dict[str, Any]:
    if args.op == "stage":
        return {"op": "stage", "bay": args.bay}
    if args.op == "report":
        return {"op": "report", "bay": None if args.silent else args.bay}
    if args.op == "commit":
        return {"op": "commit", "bay": args.bay, "ticket": args.ticket}
    if args.op == "close":
        return {"op": "close", "ticket": args.ticket}
    return {"op": "view"}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        data = wait(args.turn) if args.op == "wait" else rpc(request(args))
        print(json.dumps(data, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"recall: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
