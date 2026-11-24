"""Shift Board world with a private seat-cut marker receiver for the `main` container.

Seat routes run through the stock `Board`, `World` and `WorldServer` unchanged; no
HTTP route is added. A second channel listens on the AF_UNIX socket SOCKET_PATH
inside a root-only 0700 directory that no seat volume shares. Each connection may
send one fixed request `MARK <32 lowercase hex>\\n`. Under the same `World._lock`
that guards `Board.request` and `Board.freeze`, the receiver takes the shadow mark
`{id, after_seq=len(board._events), revision=board._rev}`, appends it to the
JOURNAL_PATH record `{schema, run, ticket, marks}`, makes the record durable, and
only then answers `OK <id>\\n`. A malformed or repeated ID, more than MAX_MARKS
marks, a faulted or frozen board, and any journal failure answer `NO\\n`. The
journal starts empty, its identity matches `shift-board-id.json` and never
changes, and a failed write refuses every later mark. Marker failures never change
seat play, and the channel has no control, stop or enforcement operation. Without
BOARD_TICKET the channel stays closed and seats play as in the stock world.
"""

import json
import logging
import os
import re
import secrets
import socket
import stat
import threading
import time
import uuid
from pathlib import Path

from board import MAX_EVENTS, REVISIONS, Board
from world import (
    ID_PATH,
    PORT,
    ROLES,
    TICKET,
    TOKEN_ROOT,
    World,
    WorldServer,
    publish_tokens,
    settings,
    ticket_setting,
    write_identity,
)

JOURNAL_PATH = Path("/logs/world/seat-cut.json")
JOURNAL_SCHEMA = "harbor.seat-cut.world/v1"
JOURNAL_FIELDS = ("schema", "run", "ticket", "marks")
SOCKET_DIR = Path("/run/seat-cut")
SOCKET_PATH = SOCKET_DIR / "mark.sock"
MARK_ID = re.compile(rb"[0-9a-f]{32}")
REQUEST_PREFIX = b"MARK "
REQUEST_BYTES = len(REQUEST_PREFIX) + 32 + 1
REFUSED = b"NO\n"
MAX_MARKS = 32
MAX_CONNECTIONS = 4 * MAX_MARKS
LISTEN_BACKLOG = 2
REQUEST_TIMEOUT_SEC = 2.0
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW

Mark = dict[str, object]

logger = logging.getLogger(__name__)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    def __init__(self, path: Path, run: str, ticket: str) -> None:
        assert str(uuid.UUID(run)) == run and uuid.UUID(run).version == 4
        assert TICKET.fullmatch(ticket) is not None
        self.path = path
        self._staged = path.with_name(f".{path.name}.tmp")
        self._identity = (("schema", JOURNAL_SCHEMA), ("run", run), ("ticket", ticket))
        self._marks: tuple[Mark, ...] = ()
        self._ids: frozenset[object] = frozenset()

    @property
    def count(self) -> int:
        return len(self._marks)

    def holds(self, mark_id: str) -> bool:
        return mark_id in self._ids

    def _encode(self, marks: tuple[Mark, ...]) -> bytes:
        record: dict[str, object] = {**dict(self._identity), "marks": list(marks)}
        assert tuple(record) == JOURNAL_FIELDS
        assert len(marks) <= MAX_MARKS
        text = json.dumps(record, separators=(",", ":"), allow_nan=False)
        return text.encode("ascii") + b"\n"

    def _stage(self, data: bytes) -> None:
        assert data.endswith(b"\n")
        fd = os.open(self._staged, CREATE_FLAGS, 0o644)
        try:
            view = memoryview(data)
            for _ in range(len(data)):
                if not view:
                    break
                view = view[os.write(fd, view) :]
            if view:
                raise OSError("short write to the seat-cut journal")
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            self._staged.unlink(missing_ok=True)
            raise
        os.close(fd)

    def _commit(self, data: bytes) -> None:
        self._stage(data)
        try:
            os.replace(self._staged, self.path)
        except BaseException:
            self._staged.unlink(missing_ok=True)
            raise
        sync_directory(self.path.parent)

    def create(self) -> None:
        assert not self._marks
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(self.path):
            raise FileExistsError(f"Seat-cut journal already exists: {self.path}")
        self._commit(self._encode(()))

    def append(self, mark: Mark) -> None:
        assert tuple(mark) == ("id", "after_seq", "revision")
        assert len(self._marks) < MAX_MARKS
        assert mark["id"] not in self._ids
        marks = (*self._marks, mark)
        self._commit(self._encode(marks))
        self._marks = marks
        self._ids = self._ids | {mark["id"]}


def read_request(conn: socket.socket) -> str | None:
    deadline = time.monotonic() + REQUEST_TIMEOUT_SEC
    data = b""
    for _ in range(REQUEST_BYTES + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        conn.settimeout(remaining)
        chunk = conn.recv(REQUEST_BYTES + 1 - len(data))
        if not chunk:
            break
        data += chunk
        if data.endswith(b"\n") or len(data) > REQUEST_BYTES:
            break
    if len(data) != REQUEST_BYTES or not data.startswith(REQUEST_PREFIX):
        return None
    if not data.endswith(b"\n"):
        return None
    raw = data[len(REQUEST_PREFIX) : -1]
    if MARK_ID.fullmatch(raw) is None:
        return None
    return raw.decode("ascii")


class MarkReceiver:
    def __init__(
        self, world: World, board: Board, journal: Journal, listener: socket.socket
    ) -> None:
        assert world._board is board
        assert listener.family == socket.AF_UNIX
        self._world = world
        self._board = board
        self._journal = journal
        self._listener = listener
        self._broken = False

    def _snapshot(self, mark_id: str) -> Mark | None:
        with self._world._lock:
            if self._world._fault or self._board._frozen:
                return None
            mark: Mark = {
                "id": mark_id,
                "after_seq": len(self._board._events),
                "revision": self._board._rev,
            }
        after_seq, revision = mark["after_seq"], mark["revision"]
        if not isinstance(after_seq, int) or not 0 <= after_seq <= MAX_EVENTS:
            return None
        if type(revision) is not int or not 0 <= revision < REVISIONS:
            return None
        return mark

    def mark(self, mark_id: str) -> bool:
        if self._broken or self._journal.count >= MAX_MARKS:
            return False
        if self._journal.holds(mark_id):
            return False
        mark = self._snapshot(mark_id)
        if mark is None:
            return False
        try:
            self._journal.append(mark)
        except Exception:
            logger.exception("Seat-cut journal write failed; refusing further marks")
            self._broken = True
            return False
        return True

    def _answer(self, conn: socket.socket) -> None:
        mark_id = read_request(conn)
        acked = mark_id is not None and self.mark(mark_id)
        reply = f"OK {mark_id}\n".encode("ascii") if acked else REFUSED
        conn.settimeout(REQUEST_TIMEOUT_SEC)
        conn.sendall(reply)

    def serve(self) -> None:
        try:
            for _ in range(MAX_CONNECTIONS):
                conn, _ = self._listener.accept()
                with conn:
                    try:
                        self._answer(conn)
                    except OSError as error:
                        logger.warning(
                            "Seat-cut request dropped: %s", type(error).__name__
                        )
        except Exception:
            logger.exception("Seat-cut receiver stopped")
        finally:
            self._listener.close()


def open_listener(path: Path) -> socket.socket:
    os.mkdir(path.parent, 0o700)
    os.chmod(path.parent, 0o700)
    info = os.lstat(path.parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise PermissionError(f"{path.parent} is not a private directory")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError(f"{path.parent} is not mode 0700")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(LISTEN_BACKLOG)
    except BaseException:
        listener.close()
        raise
    return listener


def start_marks(world: World, board: Board, run: str, ticket: str | None) -> bool:
    if ticket is None:
        logger.warning("BOARD_TICKET is unset; seat-cut marks stay closed")
        return False
    try:
        journal = Journal(JOURNAL_PATH, run, ticket)
        journal.create()
        listener = open_listener(SOCKET_PATH)
    except Exception:
        logger.exception("Seat-cut marks stay closed")
        return False
    receiver = MarkReceiver(world, board, journal, listener)
    threading.Thread(target=receiver.serve, name="seat-cut", daemon=True).start()
    return True


def main() -> None:
    environ = dict(os.environ)
    seed, fixture, rule = settings(environ)
    ticket = ticket_setting(environ)
    run = str(uuid.uuid4())
    assert str(uuid.UUID(run)) == run
    board = Board(seed=seed, fixture=fixture, rule=rule, run=run)
    write_identity(ID_PATH, run, ticket)
    world = World(board, {role: secrets.token_urlsafe(32) for role in ROLES})
    publish_tokens(world.tokens, TOKEN_ROOT)
    start_marks(world, board, run, ticket)
    with WorldServer(world, ("0.0.0.0", PORT)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
