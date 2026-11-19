"""Seat actor. Checks the request digest, then writes one action file."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from wire import canonical_bytes, canonical_digest, read_json, write_json_atomic

POLL = 0.05
POLICIES = ("low", "high", "rps-copy", "rps-beat-last")
REQUEST_KEYS = frozenset({"step", "view", "view_sha256"})


def choose(policy: str, view: dict[str, Any]) -> int:
    legal = view.get("legal_actions")
    if (
        not isinstance(legal, list)
        or not legal
        or any(type(item) is not int for item in legal)
    ):
        raise ValueError("legal_actions must be a nonempty list of ints")
    obs = view.get("observation")
    move = obs if type(obs) is int and 0 <= obs <= 2 else None
    if policy == "low":
        action = legal[0]
    elif policy == "high":
        action = legal[-1]
    elif policy == "rps-copy":
        action = 0 if move is None else move
    elif policy == "rps-beat-last":
        action = 0 if move is None else (move + 1) % 3
    else:
        raise ValueError(f"unknown policy {policy!r}")
    if action not in legal:
        raise ValueError("policy action is not legal")
    return action


def run(
    policy: str,
    inbox: str | Path,
    outbox: str | Path,
    audit: str | Path | None,
    timeout: float,
) -> None:
    """Answer until done.json exists. A bad request digest raises ValueError."""
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}")
    if (
        isinstance(timeout, bool)
        or type(timeout) not in (int, float)
        or not timeout > 0
    ):
        raise ValueError("timeout must be a positive number")
    inbox, outbox = Path(inbox), Path(outbox)
    audit_path = None if audit is None else Path(audit)
    deadline, answered = time.monotonic() + float(timeout), 0
    while time.monotonic() < deadline:
        if (inbox / "done.json").exists():
            return
        try:
            msg = read_json(inbox / "request.json")
        except FileNotFoundError:
            time.sleep(POLL)
            continue
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid request: {exc}") from exc
        view = msg.get("view") if isinstance(msg, dict) else None
        if (
            not isinstance(msg, dict)
            or set(msg) != REQUEST_KEYS
            or type(msg["step"]) is not int
        ):
            raise ValueError("request must contain step, view, and view_sha256")
        if (
            msg["step"] < 1
            or not isinstance(view, dict)
            or not isinstance(msg["view_sha256"], str)
        ):
            raise ValueError("request view is invalid")
        if msg["step"] <= answered:
            time.sleep(POLL)
            continue
        digest = canonical_digest(view)
        if digest != msg["view_sha256"]:
            raise ValueError(f"view digest mismatch at step {msg['step']}")
        record = {
            "step": msg["step"],
            "view_sha256": digest,
            "action": choose(policy, view),
        }
        write_json_atomic(outbox / "action.json", record)
        if audit_path is not None:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            with audit_path.open("ab") as handle:
                handle.write(canonical_bytes(record) + b"\n")
                handle.flush()
        answered = msg["step"]
    raise TimeoutError("timed out waiting for a request or done")


def main() -> int:
    try:
        run(
            os.environ.get("POLICY", "low"),
            os.environ.get("INBOX", "/in"),
            os.environ.get("OUTBOX", "/out"),
            os.environ.get("AUDIT", "/logs/agent/pz_actions.jsonl"),
            float(os.environ.get("TIMEOUT", "600")),
        )
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
