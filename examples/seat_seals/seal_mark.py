"""Seat-seal decision client for `main`: `python3 /app/seal_mark.py <32 lowercase hex>`.

It sends `SEAL <id>\\n` to the fixed seal_world socket and prints exactly
`OK <id>` with exit code 0 only when the world acknowledges a durable decision
and its matching seat-cut mark. Every other outcome prints `FAIL` with exit
code 1 and never relays world data. FAIL does not prove that no decision exists.
"""

import re
import socket
import sys
import time

SOCKET_PATH = "/run/seat-cut/mark.sock"
MARK_ID = re.compile(r"[0-9a-f]{32}")
TIMEOUT_SEC = 5.0


def request(mark_id: str) -> bool:
    assert MARK_ID.fullmatch(mark_id) is not None
    expected = f"OK {mark_id}\n".encode("ascii")
    deadline = time.monotonic() + TIMEOUT_SEC
    reply = b""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(TIMEOUT_SEC)
        conn.connect(SOCKET_PATH)
        conn.sendall(f"SEAL {mark_id}\n".encode("ascii"))
        for _ in range(len(expected) + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            conn.settimeout(remaining)
            chunk = conn.recv(len(expected) + 1 - len(reply))
            if not chunk:
                break
            reply += chunk
            if reply.endswith(b"\n") or len(reply) > len(expected):
                break
    return reply == expected


def main(argv: list[str]) -> int:
    acked = False
    if len(argv) == 1 and MARK_ID.fullmatch(argv[0]) is not None:
        try:
            acked = request(argv[0])
        except OSError:
            acked = False
    if acked:
        sys.stdout.write(f"OK {argv[0]}\n")
        return 0
    sys.stdout.write("FAIL\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
