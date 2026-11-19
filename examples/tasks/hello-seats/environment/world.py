"""The world: notes that seats post, kept in memory and in /logs/world/notes.jsonl.

At start, the world writes a new random token to /tokens/<seat>/token for each
seat. Compose mounts each /tokens/<seat> volume in main and in that one seat
only, so a seat can read its own token and no other. POST /note must send the
token as "Authorization: Bearer <token>", and the world records the token's
seat as the note's author.
"""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

NOTES_PATH = Path("/logs/world/notes.jsonl")
TOKENS_DIR = Path("/tokens")
MAX_BODY_BYTES = 64 * 1024
NOTES: list[dict[str, str | None]] = []
TOKENS: dict[str, str] = {}  # seat -> token
LOCK = threading.Lock()


def issue_tokens() -> None:
    """Write a new token for every seat that has a directory in TOKENS_DIR."""
    for seat_dir in TOKENS_DIR.iterdir():
        TOKENS[seat_dir.name] = secrets.token_hex(16)
        (seat_dir / "token").write_text(TOKENS[seat_dir.name])


def seat_of(authorization: str) -> str | None:
    """The seat whose token an "Authorization: Bearer <token>" header holds."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    for seat, seat_token in TOKENS.items():
        if secrets.compare_digest(token.strip().encode(), seat_token.encode()):
            return seat
    return None


class World(BaseHTTPRequestHandler):
    def _reply(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, {"ok": True})
        elif self.path == "/notes":
            with LOCK:
                notes = list(NOTES)
            self._reply(200, notes)
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/note":
            self._reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers["Content-Length"])
            if not 0 <= length <= MAX_BODY_BYTES:
                raise ValueError(length)
            body = json.loads(self.rfile.read(length))
            text = str(body["text"])
        except (KeyError, TypeError, ValueError, RecursionError):
            self._reply(400, {"error": 'send <= 64 KiB of JSON {"text": ...}'})
            return
        author = seat_of(self.headers.get("Authorization", ""))
        if author is None:
            self._reply(401, {"error": "send Authorization: Bearer <your token>"})
            return
        if body.get("author", author) != author:
            self._reply(403, {"error": f"this token is {author}'s"})
            return
        reply_to = body.get("reply_to")
        with LOCK:
            ids = [note["id"] for note in NOTES]
        if reply_to is not None and reply_to not in ids:
            self._reply(400, {"error": f"no note has the id {reply_to!r}"})
            return
        note = {
            "id": secrets.token_hex(4),
            "author": author,
            "text": text,
            "reply_to": reply_to,
        }
        with LOCK:
            NOTES.append(note)
            with NOTES_PATH.open("a") as notes_file:
                notes_file.write(json.dumps(note) + "\n")
        self._reply(201, note)


if __name__ == "__main__":
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTES_PATH.touch()
    issue_tokens()
    ThreadingHTTPServer(("0.0.0.0", 8080), World).serve_forever()
