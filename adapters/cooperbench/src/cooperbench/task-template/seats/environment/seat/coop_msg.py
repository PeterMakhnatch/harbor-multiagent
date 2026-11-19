#!/usr/bin/env python3
"""Messaging commands for a CooperBench seat.

The commands and output of CooperBench's messaging tool for CLI agents
(upstream ``cooperbench.agents._coop.coop_msg``), talking to the message
service in main instead of Redis. The service takes the sender from this
seat's token, so there is no sender to set.

Usage:
    coop-send <recipient> [content]   # send to one agent (content from stdin if omitted)
    coop-broadcast [content]          # send to every other agent
    coop-recv                         # drain this agent's inbox (JSON list)
    coop-peek                         # count unread messages
    coop-agents                       # list all agent ids

Config is read from environment variables (set by the task's Compose file):
    COOP_URL         the message service (default http://main:8080)
    COOP_TOKEN_FILE  this seat's token (default /run/cooperbench/token)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = os.environ.get("COOP_URL", "http://main:8080").rstrip("/") + path
    token_file = os.environ.get("COOP_TOKEN_FILE", "/run/cooperbench/token")
    try:
        with open(token_file) as fh:
            token = fh.read().strip()
    except OSError as exc:
        raise SystemExit(f"coop: cannot read this seat's token at {token_file}: {exc}")
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read()).get("error", exc.reason)
        except ValueError:
            error = exc.reason
        raise SystemExit(f"coop: {error} (HTTP {exc.code})")
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"coop: cannot reach the message service at {url}: {exc}")


def _agents() -> tuple[list[str], str]:
    reply = _call("GET", "/agents")
    return reply["agents"], reply["you"]


def _send(recipient: str, content: str) -> None:
    _call("POST", "/send", {"to": recipient, "content": content})


def cmd_send(args: argparse.Namespace) -> int:
    content = args.content if args.content is not None else sys.stdin.read()
    _send(args.recipient, content)
    print(f"sent to {args.recipient}", file=sys.stderr)
    return 0


def cmd_broadcast(args: argparse.Namespace) -> int:
    content = args.content if args.content is not None else sys.stdin.read()
    agents, me = _agents()
    for agent in agents:
        if agent != me:
            _send(agent, content)
    return 0


def cmd_recv(_args: argparse.Namespace) -> int:
    print(json.dumps(_call("POST", "/recv", {})["messages"], indent=2))
    return 0


def cmd_peek(_args: argparse.Namespace) -> int:
    print(_call("GET", "/peek")["unread"])
    return 0


def cmd_agents(_args: argparse.Namespace) -> int:
    for agent in _agents()[0]:
        print(agent)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coop-msg")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_send = sub.add_parser("send")
    p_send.add_argument("recipient")
    p_send.add_argument("content", nargs="?", default=None)
    p_send.set_defaults(func=cmd_send)

    p_bcast = sub.add_parser("broadcast")
    p_bcast.add_argument("content", nargs="?", default=None)
    p_bcast.set_defaults(func=cmd_broadcast)

    sub.add_parser("recv").set_defaults(func=cmd_recv)
    sub.add_parser("peek").set_defaults(func=cmd_peek)
    sub.add_parser("agents").set_defaults(func=cmd_agents)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
