"""The world: notes that seats post, kept in memory for one session.

Each start of the world is a new session. It writes a new random token to
/tokens/<seat>/token for each seat and starts with no notes, so a token from
an earlier start stops working and no note outlives its session. Compose
mounts each /tokens/<seat> volume in main and in that one seat only. POST
/note must send the token as "Authorization: Bearer <token>", and the world
records the token's seat as the note's author.

After play ends, the task's collect hook runs `world.py save <path>`, which
writes the notes, as GET /notes returns them, to <path>. The verifier grades
that file, and Harbor saves the same file as the trial's artifact.
"""

import json
import secrets
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKENS_DIR = Path("/tokens")
ISSUED_SECRETS = Path("/logs/artifacts/.harbor-secrets")  # read and deleted by Harbor
NOTES_URL = "http://127.0.0.1:8080/notes"
MAX_BODY_BYTES = 64 * 1024
NOTES: list[dict[str, str | None]] = []
TOKENS: dict[str, str] = {}  # seat -> token
LOCK = threading.Lock()


def write_whole(path: Path, text: str) -> None:
    """Replace path with text in one step, so that no reader sees part of it."""
    partial = path.with_name(f".{path.name}.partial")
    partial.write_text(text)
    partial.replace(path)


def start_session() -> None:
    """Forget every note, and write a new token for every seat that has a
    directory in TOKENS_DIR."""
    with LOCK:
        NOTES.clear()
        TOKENS.clear()
        for seat_dir in sorted(TOKENS_DIR.iterdir()):
            if seat_dir.is_dir():
                TOKENS[seat_dir.name] = secrets.token_hex(16)
                write_whole(seat_dir / "token", TOKENS[seat_dir.name])
        # Harbor reads this file, removes these tokens from every saved file, and deletes it. Appended, so
        # that the tokens of an earlier session (before a restart) are removed too.
        if ISSUED_SECRETS.parent.is_dir():
            with ISSUED_SECRETS.open("a") as issued:
                issued.write("".join(f"{token}\n" for token in TOKENS.values()))


def seat_of(authorization: str) -> str | None:
    """The seat whose token an "Authorization: Bearer <token>" header holds."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    for seat, seat_token in TOKENS.items():
        if secrets.compare_digest(token.strip().encode(), seat_token.encode()):
            return seat
    return None


def save_notes(path: Path, url: str = NOTES_URL) -> None:
    """Write the running world's notes, as GET /notes returns them, to path."""
    with urllib.request.urlopen(url, timeout=10) as response:
        notes = json.load(response)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_whole(path, json.dumps(notes, indent=2) + "\n")


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
        note = {
            "id": secrets.token_hex(4),
            "author": author,
            "text": text,
            "reply_to": reply_to,
        }
        with LOCK:
            known = reply_to is None or any(n["id"] == reply_to for n in NOTES)
            if known:
                NOTES.append(note)
        if not known:
            self._reply(400, {"error": f"no note has the id {reply_to!r}"})
            return
        self._reply(201, note)


if __name__ == "__main__":
    match sys.argv[1:]:
        case []:
            start_session()
            ThreadingHTTPServer(("0.0.0.0", 8080), World).serve_forever()
        case ["save", path]:
            save_notes(Path(path))
        case _:
            sys.exit("usage: world.py [save PATH]")
