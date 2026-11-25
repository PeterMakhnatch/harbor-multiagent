import argparse
import json
import socket
import sys
import time
from typing import Any

SOCKET = "/run/relay-seat/rpc.sock"
LIMIT = 65536
PHASES = ("request", "registrar", "depot", "dispatch", "done")


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


def wait(phase: str) -> dict[str, Any]:
    deadline = time.monotonic() + 300
    current: object = None
    for _ in range(1201):
        view = rpc({"op": "view"})
        current = view.get("phase")
        if current == phase or current == "done":
            return view
        if current not in PHASES:
            raise ValueError("invalid relay phase")
        if PHASES.index(current) > PHASES.index(phase):
            raise ValueError(f"phase {phase} already passed; current phase {current}")
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    raise ValueError(f"timed out waiting for {phase}; current phase {current}")


def parse_routes(pairs: list[str]) -> dict[str, str]:
    routes = {}
    for pair in pairs:
        exhibit, sep, bay = pair.partition("=")
        if not sep or not exhibit or not bay or "=" in bay or exhibit in routes:
            raise ValueError("use distinct exhibit=bay arguments")
        routes[exhibit] = bay
    return routes


def parse_notes(raw: str) -> list[Any]:
    if len(raw) > 4096:
        raise ValueError("notes too large")
    try:
        rows = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("invalid notes JSON") from None
    if not isinstance(rows, list) or len(rows) > 4:
        raise ValueError("expected at most four note rows")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="op", required=True)
    sub.add_parser("view")
    sub.add_parser("wait").add_argument("phase", choices=PHASES)
    sub.add_parser("request").add_argument("slips", nargs="*")
    sub.add_parser("file").add_argument("ids", nargs="*")
    authored = sub.add_parser("file-notes")
    authored.add_argument("notes")
    authored.add_argument("ids", nargs="*")
    sub.add_parser("commit").add_argument("pairs", nargs="+")
    args = parser.parse_args()
    try:
        if args.op == "wait":
            view = wait(args.phase)
        else:
            action = {"op": args.op}
            if args.op == "request":
                action["slips"] = args.slips
            elif args.op == "file":
                action["ids"] = args.ids
            elif args.op == "file-notes":
                action = {
                    "op": "file",
                    "ids": args.ids,
                    "notes": parse_notes(args.notes),
                }
            elif args.op == "commit":
                action["routes"] = parse_routes(args.pairs)
            view = rpc(action)
        print(json.dumps(view, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"relay: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
