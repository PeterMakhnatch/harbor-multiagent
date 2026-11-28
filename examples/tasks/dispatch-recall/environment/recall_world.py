"""Model the dispatch-recall bays, role views, timed notices, and private cuts."""

import hmac
import secrets
import threading
from dataclasses import dataclass
from hashlib import sha256

SCHEMA = "harbor.dispatch-recall.cut/v1"
MAX_SEED = 2**63 - 1
ROLES = ("loader", "depot")
TRANSITIONS = ("stay", "move")
TIMINGS = ("immediate", "deferred", "withheld")
WINDOWS = ("commit", "close")
ELIGIBLE: dict[str, frozenset[str]] = {
    "immediate": frozenset({"commit", "close"}),
    "deferred": frozenset({"close"}),
    "withheld": frozenset(),
}
NEXT_TURN = {"stage": "report", "report": "commit", "commit": "close", "close": "done"}
OWNER = {"stage": "loader", "report": "depot", "commit": "loader", "close": "loader"}
REQUEST_KEYS: dict[str, frozenset[str]] = {
    "stage": frozenset({"op", "bay"}),
    "report": frozenset({"op", "bay"}),
    "commit": frozenset({"op", "bay", "ticket"}),
    "close": frozenset({"op", "ticket"}),
}
SILENT = "--"
BAY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
BAY_SPACE = len(BAY_ALPHABET) ** 2
TICKET_HEX = frozenset("0123456789abcdef")
TICKET_LENGTH = 32
TICKET_ATTEMPTS = 8


def _bay_name(index: int) -> str:
    if not 0 <= index < BAY_SPACE:
        raise RuntimeError(f"Bay index out of range: {index}")
    size = len(BAY_ALPHABET)
    return BAY_ALPHABET[index // size] + BAY_ALPHABET[index % size]


def layout(seed: int) -> tuple[tuple[str, str], str]:
    if type(seed) is not int or not 0 <= seed <= MAX_SEED:
        raise ValueError(f"seed must be an int in [0, {MAX_SEED}]")
    digest = sha256(
        b"harbor.dispatch-recall.layout/v1:" + seed.to_bytes(8, "big")
    ).digest()
    first = int.from_bytes(digest[0:8], "big") % BAY_SPACE
    second = int.from_bytes(digest[8:16], "big") % (BAY_SPACE - 1)
    if second >= first:
        second += 1
    low, high = sorted((_bay_name(first), _bay_name(second)))
    initial = (low, high)[digest[16] & 1]
    if low == high or SILENT in (low, high):
        raise RuntimeError(f"Seed {seed} produced an invalid layout")
    return (low, high), initial


def _is_ticket(value: object) -> bool:
    return (
        type(value) is str and len(value) == TICKET_LENGTH and set(value) <= TICKET_HEX
    )


def _injected_tickets(tickets: object) -> tuple[str, str] | None:
    if tickets is None:
        return None
    if type(tickets) is not tuple or len(tickets) != 2:
        raise ValueError("tickets must be None or a tuple of two tickets")
    commit, close = tickets
    if not _is_ticket(commit) or not _is_ticket(close):
        raise ValueError(f"tickets must be {TICKET_LENGTH} lowercase hex characters")
    if commit == close:
        raise ValueError("commit and close tickets must differ")
    return commit, close


def _choice(value: object, choices: tuple[str, ...], name: str) -> str:
    if type(value) is not str or value not in choices:
        raise ValueError(f"{name} must be one of {list(choices)}")
    return value


@dataclass
class _Window:
    notice: str
    ticket: str
    redeemed: bool = False


class Session:
    def __init__(
        self,
        seed: int,
        transition: str,
        timing: str,
        *,
        tickets: tuple[str, str] | None = None,
    ) -> None:
        self._bays, self._initial = layout(seed)
        self._seed = seed
        self._transition = _choice(transition, TRANSITIONS, "transition")
        self._timing = _choice(timing, TIMINGS, "timing")
        self._tickets = _injected_tickets(tickets)
        self._other = self._bays[1] if self._initial == self._bays[0] else self._bays[0]
        self._turn = "stage"
        self._staged = False
        self._report: str | None = None
        self._commit: str | None = None
        self._actions: list[tuple[str, str, str | None]] = []
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def _current_bay(self) -> str:
        if self._staged and self._transition == "move":
            return self._other
        return self._initial

    def _fresh_ticket(self, window: str) -> str:
        if self._tickets is not None:
            return self._tickets[WINDOWS.index(window)]
        issued = {opened.ticket for opened in self._windows.values()}
        for _ in range(TICKET_ATTEMPTS):
            ticket = secrets.token_hex(TICKET_LENGTH // 2)
            if ticket not in issued:
                return ticket
        raise RuntimeError("Could not draw a distinct ticket")

    def _open(self, window: str) -> _Window:
        if window not in WINDOWS or self._turn != window:
            raise RuntimeError(f"Window {window!r} is not open on turn {self._turn!r}")
        opened = self._windows.get(window)
        if opened is None:
            filed = self._report if window in ELIGIBLE[self._timing] else None
            opened = _Window(
                notice=SILENT if filed is None else filed,
                ticket=self._fresh_ticket(window),
            )
            self._windows[window] = opened
        return opened

    def view(self, role: str) -> dict[str, object]:
        role = _choice(role, ROLES, "role")
        with self._lock:
            turn = self._turn
            seen: dict[str, object] = {
                "role": role,
                "turn": turn,
                "active": OWNER.get(turn) == role,
                "bays": list(self._bays),
            }
            if role == "depot":
                if self._staged:
                    seen["current_bay"] = self._current_bay()
                return seen
            seen["initial_bay"] = self._initial
            if turn in WINDOWS:
                opened = self._open(turn)
                seen["notice"] = opened.notice
                seen["ticket"] = opened.ticket
            return seen

    def _parse(self, role: str, request: object) -> tuple[str, str | None]:
        if type(request) is not dict:
            raise ValueError("request must be a JSON object")
        op = _choice(request.get("op"), tuple(OWNER), "op")
        if set(request) != REQUEST_KEYS[op]:
            raise ValueError(f"{op} takes exactly the keys {sorted(REQUEST_KEYS[op])}")
        if OWNER[op] != role:
            raise ValueError(f"{op} belongs to the {OWNER[op]}")
        if self._turn != op:
            raise ValueError(f"{op} is not accepted on turn {self._turn!r}")
        bay: str | None = None
        if "bay" in request:
            raw_bay = request["bay"]
            if not (op == "report" and raw_bay is None):
                bay = _choice(raw_bay, self._bays, "bay")
        if op in WINDOWS:
            ticket = request["ticket"]
            opened = self._windows.get(op)
            if opened is None:
                raise ValueError(f"view the {op} window before redeeming its ticket")
            if not _is_ticket(ticket) or not hmac.compare_digest(
                str(ticket), opened.ticket
            ):
                raise ValueError(f"ticket does not match the {op} window")
            if opened.redeemed:
                raise RuntimeError(f"{op} ticket was already redeemed")
        return op, bay

    def apply(self, role: str, request: object) -> dict[str, object]:
        role = _choice(role, ROLES, "role")
        with self._lock:
            op, bay = self._parse(role, request)
            if op == "stage":
                self._staged = True
            elif op == "report":
                self._report = bay
            elif op == "commit":
                self._commit = bay
            if op in WINDOWS:
                self._windows[op].redeemed = True
            self._actions.append((role, op, bay))
            self._turn = NEXT_TURN[op]
            return {"op": op, "turn": self._turn}

    def _action_record(self, action: tuple[str, str, str | None]) -> dict[str, object]:
        role, op, bay = action
        record: dict[str, object] = {"role": role, "op": op}
        if op != "close":
            record["bay"] = bay
        return record

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            current = self._current_bay()
            reward = None if self._commit is None else int(self._commit == current)
            return {
                "schema": SCHEMA,
                "seed": self._seed,
                "transition": self._transition,
                "timing": self._timing,
                "bays": list(self._bays),
                "initial_bay": self._initial,
                "current_bay": current,
                "actions": [self._action_record(action) for action in self._actions],
                "windows": {
                    window: None
                    if window not in self._windows
                    else {
                        "notice": self._windows[window].notice,
                        "redeemed": self._windows[window].redeemed,
                    }
                    for window in WINDOWS
                },
                "reward": reward,
            }
