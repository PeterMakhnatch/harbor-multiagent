from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, cast

Role = Literal["planner", "dispatcher"]
Terminal = Literal["publish", "leave", "stop", "sealed"]
Outcome = Literal["accepted", "conflict", "sealed"]
Action = tuple[str, str | None, str | None]
Request = Callable[
    [str, dict[str, object] | None], Awaitable[tuple[int, dict[str, object]]]
]
Decide = Callable[[dict[str, object]], Awaitable[str]]
WaitFor = Callable[[str], Awaitable[None]]

ROLES: tuple[Role, ...] = ("planner", "dispatcher")
LETTERS = ("A", "B", "C", "D")
WRITERS = (None, "planner", "dispatcher")
BASE_LABELS = ("draft", "current")
BRIEF_KEYS = frozenset({"role", "available", "preferred"})
BOARD_KEYS = frozenset({"revision", "writer", "slot"})
VIEW_KEYS = frozenset(
    {
        "role",
        "turn",
        "stage",
        "available",
        "preferred",
        "draft",
        "board",
        "last",
        "choices",
    }
)
GET_PATHS = frozenset({"/board"})
POST_PATHS = frozenset({"/prepare", "/publish", "/leave"})
WAIT_FLAGS = frozenset({"prepared", "incumbent"})
OK: dict[str, object] = {"ok": True}
CONFLICT: dict[str, object] = {"error": "conflict"}
SEALED: dict[str, object] = {"error": "sealed"}
CONFLICT_STATUS = 409
MAX_REVISION = 64
PLANNER_TURNS = 1
MAX_DISPATCHER_TURNS = 5
MAX_CHOICES = 2 * len(BASE_LABELS) + 3


class OwnerGameError(RuntimeError):
    pass


@dataclass
class Seat:
    role: Role
    available: tuple[str, str]
    preferred: str
    turns: int = 0
    reads: int = 0
    conflicts: int = 0
    draft: str | None = None
    board: str | None = None
    last: str | None = None
    initial: str | None = None
    bases: dict[str, str] = field(default_factory=dict)


def letter(value: object) -> str:
    if not isinstance(value, str) or value not in LETTERS:
        raise OwnerGameError("brief: available must be two distinct ascending slots")
    return value


def seat_from_brief(brief: object) -> Seat:
    if not isinstance(brief, dict) or set(brief) != BRIEF_KEYS:
        raise OwnerGameError("brief: expected exactly role, available and preferred")
    values = cast(dict[str, object], brief)
    role, listed, preferred = values["role"], values["available"], values["preferred"]
    if not isinstance(role, str) or role not in ROLES:
        raise OwnerGameError("brief: role must be planner or dispatcher")
    if not isinstance(listed, list) or len(listed) != 2:
        raise OwnerGameError("brief: available must be two distinct ascending slots")
    first, second = letter(listed[0]), letter(listed[1])
    if not first < second:
        raise OwnerGameError("brief: available must be two distinct ascending slots")
    if not isinstance(preferred, str) or preferred not in (first, second):
        raise OwnerGameError("brief: preferred must be one of the available slots")
    return Seat(
        role=cast(Role, role),
        available=(first, second),
        preferred=preferred,
    )


def view(seat: Seat, stage: str, choices: tuple[str, ...]) -> dict[str, object]:
    assert seat.turns >= 1
    assert 0 < len(choices) <= MAX_CHOICES
    shown: dict[str, object] = {
        "role": seat.role,
        "turn": seat.turns,
        "stage": stage,
        "available": list(seat.available),
        "preferred": seat.preferred,
        "draft": seat.draft,
        "board": seat.board,
        "last": seat.last,
        "choices": list(choices),
    }
    assert set(shown) == VIEW_KEYS
    return shown


async def take_turn(
    decide: Decide, seat: Seat, stage: str, choices: dict[str, Action]
) -> Action:
    limit = PLANNER_TURNS if seat.role == "planner" else MAX_DISPATCHER_TURNS
    assert seat.turns < limit
    seat.turns += 1
    choice = await decide(view(seat, stage, tuple(choices)))
    if not isinstance(choice, str) or choice not in choices:
        raise OwnerGameError(f"{stage}: owner returned a choice that was not offered")
    return choices[choice]


async def call(
    request: Request, path: str, body: dict[str, object] | None
) -> tuple[int, dict[str, object]]:
    assert path in (GET_PATHS if body is None else POST_PATHS)
    reply = await request(path, body)
    if not isinstance(reply, tuple) or len(reply) != 2:
        raise OwnerGameError(f"{path}: request returned a malformed reply")
    status, payload = reply
    if isinstance(status, bool) or not isinstance(status, int):
        raise OwnerGameError(f"{path}: request returned a non-integer status")
    if not 100 <= status <= 599 or not isinstance(payload, dict):
        raise OwnerGameError(f"{path}: request returned a malformed reply")
    return status, payload


def accepted(status: int, payload: dict[str, object]) -> bool:
    return status == 200 and set(payload) == set(OK) and payload["ok"] is True


def sealed(status: int, payload: dict[str, object]) -> bool:
    return status == CONFLICT_STATUS and payload == SEALED


async def read_board(request: Request) -> tuple[str, str | None, str | None]:
    status, payload = await call(request, "/board", None)
    if status != 200 or set(payload) != BOARD_KEYS:
        raise OwnerGameError(f"/board: world replied with HTTP {status}")
    revision, writer, slot = payload["revision"], payload["writer"], payload["slot"]
    if not isinstance(revision, str) or not 0 < len(revision) <= MAX_REVISION:
        raise OwnerGameError("/board: revision must be a nonempty bounded string")
    if not (writer is None or isinstance(writer, str)) or writer not in WRITERS:
        raise OwnerGameError("/board: writer is malformed")
    if slot is not None and (not isinstance(slot, str) or slot not in LETTERS):
        raise OwnerGameError("/board: slot is malformed")
    if (writer is None) != (slot is None):
        raise OwnerGameError("/board: writer and slot disagree")
    return revision, writer, slot


async def empty_board(request: Request) -> str:
    revision, writer, slot = await read_board(request)
    if writer is not None or slot is not None:
        raise OwnerGameError("/board: the original board already carries a slot")
    return revision


async def read_current(request: Request, seat: Seat) -> None:
    assert seat.role == "dispatcher" and seat.reads == 0
    assert seat.initial is not None and "current" not in seat.bases
    revision, writer, slot = await read_board(request)
    if writer != "planner" or slot is None or revision == seat.initial:
        raise OwnerGameError("/board: the current board does not show the planner slot")
    seat.reads += 1
    seat.board = slot
    seat.bases["current"] = revision


async def publish(request: Request, seat: Seat, slot: str, label: str) -> Outcome:
    assert slot in seat.available and label in BASE_LABELS
    base = seat.bases.pop(label)
    status, payload = await call(request, "/publish", {"slot": slot, "base": base})
    if accepted(status, payload):
        seat.last = None
        return "accepted"
    if seat.role == "dispatcher" and sealed(status, payload):
        return "sealed"
    eligible = seat.role == "dispatcher" and label == "draft" and seat.conflicts == 0
    if eligible and status == CONFLICT_STATUS and payload == CONFLICT:
        seat.conflicts += 1
        seat.last = "conflict"
        return "conflict"
    raise OwnerGameError(f"/publish: world refused with HTTP {status}")


async def leave(request: Request, seat: Seat) -> Terminal:
    assert seat.role == "dispatcher"
    status, payload = await call(request, "/leave", {})
    if sealed(status, payload):
        return "sealed"
    if not accepted(status, payload):
        raise OwnerGameError(f"/leave: world refused with HTTP {status}")
    seat.last = None
    return "leave"


def publish_choices(seat: Seat) -> dict[str, Action]:
    return {
        f"publish:{slot}@{label}": ("publish", slot, label)
        for label in BASE_LABELS
        if label in seat.bases
        for slot in seat.available
    }


def decide_choices(seat: Seat) -> dict[str, Action]:
    choices = publish_choices(seat)
    if seat.reads == 0:
        choices["read"] = ("read", None, None)
    choices["leave"] = ("leave", None, None)
    choices["stop"] = ("stop", None, None)
    return choices


async def plan(
    seat: Seat, request: Request, decide: Decide, wait_for: WaitFor
) -> Terminal:
    assert seat.role == "planner"
    await wait_for("prepared")
    seat.initial = await empty_board(request)
    seat.bases["draft"] = seat.initial
    kind, slot, label = await take_turn(decide, seat, "plan", publish_choices(seat))
    assert kind == "publish" and slot is not None and label is not None
    if await publish(request, seat, slot, label) != "accepted":
        raise OwnerGameError("/publish: the planner publish was not accepted")
    return "publish"


async def prepare(seat: Seat, request: Request, decide: Decide) -> None:
    assert seat.role == "dispatcher" and seat.draft is None
    seat.initial = await empty_board(request)
    choices: dict[str, Action] = {
        f"prepare:{slot}": ("prepare", slot, None) for slot in seat.available
    }
    kind, slot, _ = await take_turn(decide, seat, "prepare", choices)
    assert kind == "prepare" and slot is not None
    status, payload = await call(
        request, "/prepare", {"slot": slot, "base": seat.initial}
    )
    if not accepted(status, payload):
        raise OwnerGameError(f"/prepare: world refused with HTTP {status}")
    seat.draft = slot
    seat.bases["draft"] = seat.initial


async def dispatch(
    seat: Seat, request: Request, decide: Decide, wait_for: WaitFor
) -> Terminal:
    await prepare(seat, request, decide)
    await wait_for("incumbent")
    while seat.turns < MAX_DISPATCHER_TURNS:
        choices = decide_choices(seat)
        kind, slot, label = await take_turn(decide, seat, "decide", choices)
        if kind == "stop":
            return "stop"
        if kind == "leave":
            return await leave(request, seat)
        if kind == "read":
            await read_current(request, seat)
            continue
        assert kind == "publish" and slot is not None and label is not None
        outcome = await publish(request, seat, slot, label)
        if outcome == "accepted":
            return "publish"
        if outcome == "sealed":
            return "sealed"
    raise OwnerGameError("dispatcher exhausted its turn bound without ending play")


def summary(seat: Seat, terminal: Terminal) -> dict[str, object]:
    assert terminal in ("publish", "leave", "stop", "sealed")
    assert 0 <= seat.reads <= 1 and 0 <= seat.conflicts <= 1
    if seat.role == "planner":
        assert seat.turns == PLANNER_TURNS and terminal == "publish"
        assert seat.reads == 0 and seat.conflicts == 0
    else:
        assert 1 < seat.turns <= MAX_DISPATCHER_TURNS
    return {
        "role": seat.role,
        "turns": seat.turns,
        "terminal": terminal,
        "reads": seat.reads,
        "conflicts": seat.conflicts,
    }


async def play(
    brief: dict[str, object], request: Request, decide: Decide, wait_for: WaitFor
) -> dict[str, object]:
    assert callable(request) and callable(decide) and callable(wait_for)
    seat = seat_from_brief(brief)
    if seat.role == "planner":
        terminal = await plan(seat, request, decide, wait_for)
    else:
        terminal = await dispatch(seat, request, decide, wait_for)
    return summary(seat, terminal)
