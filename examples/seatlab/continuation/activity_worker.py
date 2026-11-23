from __future__ import annotations

import hashlib
import json
import re
import sys

COUNT_LINE = re.compile(
    r"^Option ([12])\nPublic posts in the final hour: ([0-9]{1,6})$", re.MULTILINE
)
ROWS = 36
LINE_BYTES = 48 * 1024


def respond(raw: bytes, seen: set[str]) -> dict[str, str]:
    if len(raw) > LINE_BYTES or not raw.endswith(b"\n"):
        raise ValueError("expected a bounded JSONL request ending in a newline")
    request = json.loads(raw)
    if not isinstance(request, dict) or request.keys() != {
        "id",
        "prompt_sha256",
        "prompt",
    }:
        raise ValueError("expected only a request id, prompt digest and prompt")
    key, digest, prompt = request["id"], request["prompt_sha256"], request["prompt"]
    if (
        not isinstance(key, str)
        or re.fullmatch(r"[0-9a-f]{64}", key) is None
        or key in seen
    ):
        raise ValueError("invalid or duplicate request id")
    if (
        not isinstance(prompt, str)
        or hashlib.sha256(prompt.encode()).hexdigest() != digest
    ):
        raise ValueError("incorrect prompt digest")
    counts = COUNT_LINE.findall(prompt)
    if len(counts) != 2 or [label for label, _ in counts] != ["1", "2"]:
        raise ValueError("expected one header count for each option")
    seen.add(key)
    pick = 1 if int(counts[0][1]) >= int(counts[1][1]) else 2
    return {"id": key, "prompt_sha256": digest, "reply": json.dumps({"pick": pick})}


def main() -> int:
    replies: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        for _ in range(ROWS + 1):
            raw = sys.stdin.buffer.readline(LINE_BYTES + 1)
            if not raw:
                break
            if len(replies) == ROWS:
                raise ValueError("input exceeds the registered 36 decisions")
            replies.append(respond(raw, seen))
    except (ValueError, TypeError, OSError, RecursionError) as exc:
        print(f"no activity replies: {exc}", file=sys.stderr)
        return 2
    for item in replies:
        print(json.dumps(item, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
