"""Seat HTTP actor loop: polls its view, chooses via /actor/policy.py, posts acked action."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from policy import choose

POLL_SEC, TIMEOUT_SEC = 0.05, 10.0


def canonical_digest(value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def _http_json(
    url: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = (
        None
        if payload is None
        else json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "ascii"
        )
    )
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": raw}


def main() -> int:
    seat = os.environ.get("SEAT", "")
    convention = os.environ.get("CONVENTION", "")
    world_url = os.environ.get("WORLD_URL", "http://world:8080").rstrip("/")
    token_path = Path(os.environ.get("SEAT_TOKEN_FILE", "/run/seat-token/token"))
    audit_path = Path(
        os.environ.get("ACTOR_AUDIT_LOG", "/logs/agent/policy_audit.jsonl")
    )
    if seat not in ("p0", "p1") or convention not in ("L", "R"):
        print(f"invalid SEAT={seat!r} or CONVENTION={convention!r}", file=sys.stderr)
        return 2
    try:
        token = token_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        print(f"failed to read seat token file {token_path}: {exc}", file=sys.stderr)
        return 2
    if not token:
        print(f"empty seat token file {token_path}", file=sys.stderr)
        return 2
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    decisions = 0
    while True:
        status, body = _http_json(f"{world_url}/view/{seat}", token=token)
        if status != 200:
            print(f"GET /view/{seat} failed ({status}): {body}", file=sys.stderr)
            return 1
        state = body.get("status")
        if state == "done":
            print(
                json.dumps(
                    {
                        "seat": seat,
                        "convention": convention,
                        "decisions": decisions,
                        "scores": body.get("scores"),
                    },
                    ensure_ascii=True,
                )
            )
            return 0
        if state == "pending":
            time.sleep(POLL_SEC)
            continue
        if state != "turn":
            print(f"unexpected view status: {body}", file=sys.stderr)
            return 1
        turn, view = body["turn"], body["view"]
        local_digest = canonical_digest(view)
        if local_digest != body.get("view_sha256"):
            print(f"view digest mismatch on turn {turn}", file=sys.stderr)
            return 1
        action = int(choose(view, convention))
        post_status, post_body = _http_json(
            f"{world_url}/action/{seat}",
            token=token,
            method="POST",
            payload={"turn": turn, "action": action, "view_sha256": local_digest},
        )
        if post_status != 200:
            print(
                f"POST /action/{seat} failed ({post_status}): {post_body}",
                file=sys.stderr,
            )
            return 1
        decisions += 1
        record = {
            "turn": turn,
            "seat": seat,
            "convention": convention,
            "view_sha256": local_digest,
            "action": action,
        }
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    record, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
                + "\n"
            )


if __name__ == "__main__":
    raise SystemExit(main())
