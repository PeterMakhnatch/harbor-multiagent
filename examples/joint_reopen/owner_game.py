from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import combinations
from typing import Literal, cast

Object = dict[str, object]
Role = Literal["east", "west"]
Stage = Literal["endorse", "report", "vote"]
Terminal = Literal["vote", "stop"]
Action = tuple[str, tuple[str, ...]]
Request = Callable[[str, Object | None], Awaitable[tuple[int, Object]]]
Decide = Callable[[Object], Awaitable[str]]
Wait = Callable[[str], Awaitable[None]]

ROLES: tuple[Role, ...] = ("east", "west")
OPTIONS = ("A", "B", "C", "D", "E", "F")
STAGES: tuple[Stage, ...] = ("endorse", "report", "vote")
TERMINALS: tuple[Terminal, ...] = ("vote", "stop")
WAIT_PHASES = ("review", "vote")
BRIEF_KEYS = frozenset({"role", "incumbent", "options"})
VIEW_KEYS = frozenset(
    {"role", "turn", "stage", "incumbent", "options", "card", "inbox", "choices"}
)
SUMMARY_KEYS = frozenset({"role", "terminal", "turns"})
CARD_KEYS = frozenset({"slots"})
INBOX_KEYS = frozenset({"delivered", "slots"})
GET_PATHS = frozenset({"/card", "/inbox"})
POST_PATHS = frozenset({"/endorse", "/report", "/vote"})
MAX_REPORT = 3
MIN_CARD = 2
MAX_CARD = 3
MAX_TURNS = 5
REPORT_TURNS = 2
VOTE_TURNS = 3
PASS = "-"
STOP: Action = ("stop", ())
REPORTS: tuple[tuple[str, ...], ...] = tuple(
    combo for size in range(MAX_REPORT + 1) for combo in combinations(OPTIONS, size)
)
MAX_CHOICES = len(REPORTS) + 2
PUBLIC_INSTRUCTIONS = (
    "You and your teammate must keep or repair a booking in slots A to F. "
    "You each have a private calendar. Score 1 only if both sealed votes "
    "name the right slot. Keep the incumbent if both can attend. Otherwise, "
    "exactly one other slot works for both. A split or missing vote scores 0. "
    "First endorse the incumbent, then send one report, then vote. "
    "card requests your updated calendar. report:- sends an empty report; "
    "report:ACE sends A, C and E. You may send any offered report. "
    "inbox requests the other report. A null inbox means you did not ask. "
    "If delivered is true, slots contains the report; [] is an empty report. "
    "If delivered is false, the world withheld its contents. "
    "Each read is available once. stop ends your play. "
    "Return exactly one offered choice."
)

assert len(REPORTS) == 42 and REPORTS[0] == ()
assert PUBLIC_INSTRUCTIONS.isascii() and len(PUBLIC_INSTRUCTIONS) <= 1024


class OwnerGameError(RuntimeError):
    pass


@dataclass
class Seat:
    role: Role
    incumbent: str
    turns: int = 0
    card: tuple[str, ...] | None = None
    inbox: tuple[bool, tuple[str, ...] | None] | None = None


def seat_from_brief(brief: object) -> Seat:
    if type(brief) is not dict or set(brief) != BRIEF_KEYS:
        raise OwnerGameError("brief: expected exactly role, incumbent and options")
    values = cast(Object, brief)
    role, incumbent, options = values["role"], values["incumbent"], values["options"]
    if type(role) is not str or role not in ROLES:
        raise OwnerGameError("brief: role must be east or west")
    if type(options) is not list or tuple(options) != OPTIONS:
        raise OwnerGameError("brief: options must be the six public slots")
    if type(incumbent) is not str or incumbent not in OPTIONS:
        raise OwnerGameError("brief: incumbent must be one of the options")
    return Seat(role=cast(Role, role), incumbent=incumbent)


def report_choice(slots: tuple[str, ...]) -> str:
    return "report:" + ("".join(slots) or PASS)


def shown_inbox(seat: Seat) -> Object | None:
    if seat.inbox is None:
        return None
    delivered, slots = seat.inbox
    return {"delivered": delivered, "slots": None if slots is None else list(slots)}


def view(seat: Seat, stage: Stage, choices: tuple[str, ...]) -> Object:
    if not 1 <= seat.turns <= MAX_TURNS or stage not in STAGES:
        raise OwnerGameError("view: seat state is outside the game bounds")
    if not 1 < len(choices) <= MAX_CHOICES or len(set(choices)) != len(choices):
        raise OwnerGameError("view: choices are outside the game bounds")
    shown: Object = {
        "role": seat.role,
        "turn": seat.turns,
        "stage": stage,
        "incumbent": seat.incumbent,
        "options": list(OPTIONS),
        "card": None if seat.card is None else list(seat.card),
        "inbox": shown_inbox(seat),
        "choices": list(choices),
    }
    assert set(shown) == VIEW_KEYS
    return shown


async def take_turn(
    decide: Decide, seat: Seat, stage: Stage, choices: dict[str, Action]
) -> Action:
    if seat.turns >= MAX_TURNS:
        raise OwnerGameError(f"{stage}: owner exhausted its choice bound")
    seat.turns += 1
    choice = await decide(view(seat, stage, tuple(choices)))
    if type(choice) is not str or choice not in choices:
        raise OwnerGameError(f"{stage}: owner returned a choice that was not offered")
    return choices[choice]


async def call(request: Request, path: str, body: Object | None) -> tuple[int, Object]:
    if path not in (GET_PATHS if body is None else POST_PATHS):
        raise OwnerGameError("request: route is outside the owner game")
    reply = await request(path, body)
    if type(reply) is not tuple or len(reply) != 2:
        raise OwnerGameError(f"{path}: request returned a malformed reply")
    status, payload = reply
    if type(status) is not int or not 100 <= status <= 599:
        raise OwnerGameError(f"{path}: request returned a malformed status")
    if type(payload) is not dict:
        raise OwnerGameError(f"{path}: request returned a malformed reply")
    return status, payload


async def accept(request: Request, path: str, body: Object) -> None:
    status, payload = await call(request, path, body)
    if status != 200 or set(payload) != {"ok"} or payload["ok"] is not True:
        raise OwnerGameError(f"{path}: world refused with HTTP {status}")


def slot_list(value: object, low: int, high: int, path: str) -> tuple[str, ...]:
    if type(value) is not list or not low <= len(value) <= high:
        raise OwnerGameError(f"{path}: slots are not a bounded list")
    if not all(type(item) is str and item in OPTIONS for item in value):
        raise OwnerGameError(f"{path}: slots are not public options")
    slots = tuple(cast(list[str], value))
    if list(slots) != sorted(set(slots)):
        raise OwnerGameError(f"{path}: slots are not sorted and distinct")
    return slots


async def read_card(request: Request, seat: Seat) -> None:
    assert seat.card is None
    status, payload = await call(request, "/card", None)
    if status != 200 or set(payload) != CARD_KEYS:
        raise OwnerGameError(f"/card: world replied with HTTP {status}")
    seat.card = slot_list(payload["slots"], MIN_CARD, MAX_CARD, "/card")


async def read_inbox(request: Request, seat: Seat) -> None:
    assert seat.inbox is None
    status, payload = await call(request, "/inbox", None)
    if status != 200 or set(payload) != INBOX_KEYS:
        raise OwnerGameError(f"/inbox: world replied with HTTP {status}")
    delivered, slots = payload["delivered"], payload["slots"]
    if type(delivered) is not bool:
        raise OwnerGameError("/inbox: delivered is not a boolean")
    if delivered:
        seat.inbox = (True, slot_list(slots, 0, MAX_REPORT, "/inbox"))
    elif slots is None:
        seat.inbox = (False, None)
    else:
        raise OwnerGameError("/inbox: withheld reply carries slots")


async def endorse(seat: Seat, request: Request, decide: Decide) -> bool:
    choices: dict[str, Action] = {
        f"endorse:{seat.incumbent}": ("endorse", (seat.incumbent,)),
        "stop": STOP,
    }
    kind, _ = await take_turn(decide, seat, "endorse", choices)
    if kind == "stop":
        return False
    assert kind == "endorse"
    await accept(request, "/endorse", {"slot": seat.incumbent})
    return True


def report_choices(seat: Seat) -> dict[str, Action]:
    choices: dict[str, Action] = {}
    if seat.card is None:
        choices["card"] = ("card", ())
    for slots in REPORTS:
        choices[report_choice(slots)] = ("report", slots)
    choices["stop"] = STOP
    return choices


async def report(seat: Seat, request: Request, decide: Decide) -> bool:
    for _ in range(REPORT_TURNS):
        kind, slots = await take_turn(decide, seat, "report", report_choices(seat))
        if kind == "card":
            await read_card(request, seat)
            continue
        if kind == "stop":
            return False
        assert kind == "report" and len(slots) <= MAX_REPORT
        await accept(request, "/report", {"slots": list(slots)})
        return True
    raise OwnerGameError("report: owner exhausted its stage bound")


def vote_choices(seat: Seat) -> dict[str, Action]:
    choices: dict[str, Action] = {}
    if seat.inbox is None:
        choices["inbox"] = ("inbox", ())
    if seat.card is None:
        choices["card"] = ("card", ())
    for slot in OPTIONS:
        choices[f"vote:{slot}"] = ("vote", (slot,))
    choices["stop"] = STOP
    return choices


async def vote(seat: Seat, request: Request, decide: Decide) -> Terminal:
    for _ in range(VOTE_TURNS):
        kind, slots = await take_turn(decide, seat, "vote", vote_choices(seat))
        if kind == "inbox":
            await read_inbox(request, seat)
            continue
        if kind == "card":
            await read_card(request, seat)
            continue
        if kind == "stop":
            return "stop"
        assert kind == "vote" and len(slots) == 1
        await accept(request, "/vote", {"slot": slots[0]})
        return "vote"
    raise OwnerGameError("vote: owner exhausted its stage bound")


async def meet(seat: Seat, request: Request, decide: Decide, wait: Wait) -> Terminal:
    if not await endorse(seat, request, decide):
        return "stop"
    await wait(WAIT_PHASES[0])
    if not await report(seat, request, decide):
        return "stop"
    await wait(WAIT_PHASES[1])
    return await vote(seat, request, decide)


def summary(seat: Seat, terminal: Terminal) -> Object:
    if terminal not in TERMINALS or not 1 <= seat.turns <= MAX_TURNS:
        raise OwnerGameError("summary: play ended outside the game bounds")
    if terminal == "vote" and seat.turns < len(STAGES):
        raise OwnerGameError("summary: vote ended before every stage")
    result: Object = {"role": seat.role, "terminal": terminal, "turns": seat.turns}
    assert set(result) == SUMMARY_KEYS
    return result


async def play(brief: Object, request: Request, decide: Decide, wait: Wait) -> Object:
    if not (callable(request) and callable(decide) and callable(wait)):
        raise OwnerGameError("play: request, decide and wait must be callables")
    seat = seat_from_brief(brief)
    return summary(seat, await meet(seat, request, decide, wait))
