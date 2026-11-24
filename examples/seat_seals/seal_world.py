"""Shift Board world with a private calendar seal for the `main` container.

Seat routes run through the stock `World` and `WorldServer`; no HTTP route is
added. `SealBoard` subclasses the stock `Board` and only gates `Board.request`,
which `World.handle` calls under `World._lock`. BOARD_SEAL fixes the arm
(`continue` or `seal`) at startup; it never changes.

The board_world AF_UNIX channel at SOCKET_PATH (root-only 0700 directory) keeps
`MARK <32 lowercase hex>\\n` unchanged and adds `SEAL <32 lowercase hex>\\n`. A
SEAL takes `World._lock`, snapshots `{id, after_seq=len(board._events),
revision=board._rev}`, durably writes it as the LEDGER_PATH decision, durably
appends the same row as an ordinary seat-cut mark, releases the lock, and only
then answers `OK <id>\\n`. A second SEAL, a repeated ID, the shared MAX_MARKS
cap, a faulted or frozen board, and any write failure answer `NO\\n`. Once the
decision record is visible it stands, even if its acknowledgement is lost.

Both arms write the initial ledger `{schema, ticket, run, mode, decision=null,
refusals=[], truncated=false}` before the world serves. In the `seal` arm, after
the decision, an authenticated seat POST on a route that seat owns, on an
unfrozen board, is refused with `409 {"error":"sealed"}` without reaching the
board domain; the refusal row `{seat, path, after_seq, revision}` is durably
appended under the same lock before the reply. Unauthenticated (401), malformed
(400/411/413), non-owner (403) and frozen (409 frozen) requests keep their stock
replies, and every GET, including view events, stays stock. A refusal is never
a board event. Past MAX_REFUSALS rows, or after any ledger write fault, rows
stop and the ledger makes one rewrite with `truncated=true`; refusal itself never
depends on the ledger. Without BOARD_TICKET the channel and ledger stay closed
and seats play as in the stock world.
"""

import json
import logging
import os
import secrets
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Literal, cast, override

from board import MAX_EVENTS, REVISIONS, ROUTES, Board, Reply
from board_world import (
    CREATE_FLAGS,
    JOURNAL_PATH,
    MARK_ID,
    MAX_MARKS,
    REFUSED,
    REQUEST_TIMEOUT_SEC,
    SOCKET_PATH,
    Journal,
    Mark,
    MarkReceiver,
    open_listener,
    sync_directory,
)
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

Mode = Literal["continue", "seal"]
Refusal = dict[str, object]

MODES: tuple[Mode, ...] = ("continue", "seal")
LEDGER_PATH = Path("/logs/world/seat-seal.json")
LEDGER_SCHEMA = "harbor.seat-seal.world/v1"
LEDGER_FIELDS = ("schema", "ticket", "run", "mode", "decision", "refusals", "truncated")
DECISION_FIELDS = ("id", "after_seq", "revision")
REFUSAL_FIELDS = ("seat", "path", "after_seq", "revision")
MAX_REFUSALS = 128
MARK_VERB = b"MARK "
SEAL_VERB = b"SEAL "
VERB_BYTES = len(MARK_VERB)
REQUEST_BYTES = VERB_BYTES + 32 + 1
SEALED = "sealed"

assert len(SEAL_VERB) == VERB_BYTES

logger = logging.getLogger(__name__)


class Ledger:
    def __init__(self, path: Path, run: str, ticket: str, mode: Mode) -> None:
        assert str(uuid.UUID(run)) == run and uuid.UUID(run).version == 4
        assert TICKET.fullmatch(ticket) is not None
        assert mode in MODES
        self.path = path
        self.mode = mode
        self._staged = path.with_name(f".{path.name}.tmp")
        self._identity = (
            ("schema", LEDGER_SCHEMA),
            ("ticket", ticket),
            ("run", run),
            ("mode", mode),
        )
        self._decision: Mark | None = None
        self._refusals: tuple[Refusal, ...] = ()
        self._truncated = False
        self._created = False
        self._broken = False

    @property
    def sealed(self) -> bool:
        return self.mode == "seal" and self._decision is not None

    @property
    def undecided(self) -> bool:
        return self._created and not self._broken and self._decision is None

    def _encode(
        self, decision: Mark | None, refusals: tuple[Refusal, ...], truncated: bool
    ) -> bytes:
        assert decision is None or tuple(decision) == DECISION_FIELDS
        assert all(tuple(row) == REFUSAL_FIELDS for row in refusals)
        assert len(refusals) <= MAX_REFUSALS
        assert decision is not None or not refusals
        assert self.mode == "seal" or not refusals
        record: dict[str, object] = {
            **dict(self._identity),
            "decision": decision,
            "refusals": list(refusals),
            "truncated": truncated,
        }
        assert tuple(record) == LEDGER_FIELDS
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
                raise OSError("short write to the seat-seal ledger")
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            self._staged.unlink(missing_ok=True)
            raise
        os.close(fd)

    def _commit(
        self, decision: Mark | None, refusals: tuple[Refusal, ...], truncated: bool
    ) -> None:
        self._stage(self._encode(decision, refusals, truncated))
        try:
            os.replace(self._staged, self.path)
        except BaseException:
            self._staged.unlink(missing_ok=True)
            raise
        self._decision, self._refusals, self._truncated = decision, refusals, truncated
        sync_directory(self.path.parent)

    def _fault(self, operation: str) -> None:
        logger.exception("Seat-seal ledger %s failed; ledger rows stop", operation)
        self._broken = True
        try:
            self._commit(self._decision, self._refusals, True)
        except Exception:
            logger.exception("Seat-seal ledger could not record its truncation")

    def create(self) -> None:
        assert not self._created and self._decision is None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(self.path):
            raise FileExistsError(f"Seat-seal ledger already exists: {self.path}")
        self._commit(None, (), False)
        self._created = True

    def decide(self, mark: Mark) -> bool:
        assert tuple(mark) == DECISION_FIELDS
        if not self.undecided:
            return False
        try:
            self._commit(dict(mark), (), False)
        except Exception:
            self._fault("decision")
            return False
        assert self._decision == mark
        return True

    def refuse(self, seat: str, path: str, after_seq: int, revision: int) -> None:
        assert self.sealed
        assert 0 <= after_seq <= MAX_EVENTS and 0 <= revision < REVISIONS
        if self._broken or self._truncated:
            return
        if len(self._refusals) >= MAX_REFUSALS:
            try:
                self._commit(self._decision, self._refusals, True)
            except Exception:
                self._fault("truncation")
            return
        row: Refusal = {
            "seat": seat,
            "path": path,
            "after_seq": after_seq,
            "revision": revision,
        }
        try:
            self._commit(self._decision, (*self._refusals, row), False)
        except Exception:
            self._fault("refusal")


class SealBoard(Board):
    def __init__(
        self, *, seed: int, fixture: str, rule: str, run: str, ledger: Ledger | None
    ) -> None:
        super().__init__(seed=seed, fixture=fixture, rule=rule, run=run)
        self.ledger = ledger

    def _gated(self, seat: str, method: str, path: str) -> bool:
        if method != "POST" or not isinstance(path, str) or self._frozen:
            return False
        owners = ROUTES.get((method, path))
        return owners is not None and seat in owners

    @override
    def request(
        self, seat: str, method: str, path: str, body: dict[str, object]
    ) -> Reply:
        ledger = self.ledger
        if ledger is None or not ledger.sealed or not self._gated(seat, method, path):
            return super().request(seat, method, path, body)
        events = len(self._events)
        ledger.refuse(seat, path, after_seq=events, revision=self._rev)
        assert len(self._events) == events
        self._check()
        return 409, {"error": SEALED}


def read_command(conn: socket.socket) -> tuple[bytes, str] | None:
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
    if len(data) != REQUEST_BYTES or not data.endswith(b"\n"):
        return None
    verb = data[:VERB_BYTES]
    if verb not in (MARK_VERB, SEAL_VERB):
        return None
    raw = data[VERB_BYTES:-1]
    if MARK_ID.fullmatch(raw) is None:
        return None
    return verb, raw.decode("ascii")


class SealReceiver(MarkReceiver):
    def __init__(
        self,
        world: World,
        board: SealBoard,
        journal: Journal,
        ledger: Ledger,
        listener: socket.socket,
    ) -> None:
        super().__init__(world, board, journal, listener)
        assert board.ledger is ledger
        self._seal_board = board
        self._ledger = ledger

    def _locked_snapshot(self, mark_id: str) -> Mark | None:
        if self._world._fault or self._seal_board._frozen:
            return None
        after_seq = len(self._seal_board._events)
        revision = self._seal_board._rev
        if not 0 <= after_seq <= MAX_EVENTS or not 0 <= revision < REVISIONS:
            return None
        return {"id": mark_id, "after_seq": after_seq, "revision": revision}

    def seal(self, mark_id: str) -> bool:
        if self._broken or self._journal.count >= MAX_MARKS:
            return False
        if self._journal.holds(mark_id) or not self._ledger.undecided:
            return False
        with self._world._lock:
            mark = self._locked_snapshot(mark_id)
            if mark is None or not self._ledger.decide(mark):
                return False
            try:
                self._journal.append(dict(mark))
            except Exception:
                logger.exception("Seat-cut journal write failed after a decision")
                self._broken = True
                return False
        return True

    @override
    def _answer(self, conn: socket.socket) -> None:
        command = read_command(conn)
        reply = REFUSED
        if command is not None:
            verb, mark_id = command
            acked = self.seal(mark_id) if verb == SEAL_VERB else self.mark(mark_id)
            reply = f"OK {mark_id}\n".encode("ascii") if acked else REFUSED
        conn.settimeout(REQUEST_TIMEOUT_SEC)
        conn.sendall(reply)


def mode_setting(environ: dict[str, str]) -> Mode:
    mode = environ.get("BOARD_SEAL", "")
    if mode not in MODES:
        raise ValueError(f"BOARD_SEAL must be one of {list(MODES)}")
    return cast(Mode, mode)


def start_seals(world: World, board: SealBoard, run: str, ticket: str | None) -> bool:
    ledger = board.ledger
    if ticket is None or ledger is None:
        logger.warning("BOARD_TICKET is unset; seat-seal channel stays closed")
        return False
    try:
        ledger.create()
        journal = Journal(JOURNAL_PATH, run, ticket)
        journal.create()
        listener = open_listener(SOCKET_PATH)
    except Exception:
        logger.exception("Seat-seal channel stays closed")
        return False
    receiver = SealReceiver(world, board, journal, ledger, listener)
    threading.Thread(target=receiver.serve, name="seat-seal", daemon=True).start()
    return True


def main() -> None:
    environ = dict(os.environ)
    seed, fixture, rule = settings(environ)
    ticket = ticket_setting(environ)
    mode = mode_setting(environ)
    run = str(uuid.uuid4())
    assert str(uuid.UUID(run)) == run
    ledger = None if ticket is None else Ledger(LEDGER_PATH, run, ticket, mode)
    board = SealBoard(seed=seed, fixture=fixture, rule=rule, run=run, ledger=ledger)
    write_identity(ID_PATH, run, ticket)
    world = World(board, {role: secrets.token_urlsafe(32) for role in ROLES})
    publish_tokens(world.tokens, TOKEN_ROOT)
    start_seals(world, board, run, ticket)
    with WorldServer(world, ("0.0.0.0", PORT)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
