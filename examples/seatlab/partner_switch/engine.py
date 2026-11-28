import json
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from ..coupled_retention import native

ROLES = ("east", "west")
OPTIONS = ("A", "B", "C", "D", "E", "F")
SCHEMA = "seatlab.partner-switch.role/v1"
REPORT_INSTRUCTION = (
    "Both seats endorsed the incumbent, which is now unavailable to both. "
    "Each seat has two available slots among the three public active alternatives. "
    "Send exactly one of your own available slots to the other seat. "
    "Both seats will receive the other's report before voting for a common available slot. "
    'Reply only with {"slot":"LABEL"}.'
)
VOTE_INSTRUCTION = (
    "Both seats endorsed the incumbent, which is now unavailable to both. "
    "Each seat has two available slots among the three public active alternatives. "
    "You sent one of your available slots and received one of the other seat's available slots. "
    "Both seats must vote for the same mutually available slot to succeed. "
    "You may vote for any of the six options. "
    'Reply only with {"slot":"LABEL"}.'
)


@dataclass(frozen=True)
class Case:
    seed: int
    active: tuple[str, ...]
    reports: tuple[bytes, bytes]


@dataclass(frozen=True)
class Branch:
    votes: tuple[bytes, bytes]
    reports: tuple[str, str]
    _world: native._Meeting = field(repr=False, compare=False)


def _call(
    world: native._Meeting,
    role: str,
    method: str,
    path: str,
    body: dict[str, object],
) -> dict[str, object]:
    status, reply = world.handle(role, method, path, body)
    if status != 200:
        raise RuntimeError("native meeting rejected a protocol operation")
    return reply


def _encode(fields: dict[str, object]) -> bytes:
    return json.dumps(fields, separators=(",", ":"), allow_nan=False).encode("ascii")


def _open(
    seed: int, active: tuple[str, ...]
) -> tuple[native._Meeting, tuple[bytes, bytes], dict[str, tuple[str, ...]]]:
    world = native.Meeting(seed, "both", "deliver", str(UUID(int=seed)))
    briefs = {role: _call(world, role, "GET", "/brief", {}) for role in ROLES}
    incumbent = briefs["east"]["incumbent"]
    if (
        len(active) != 3
        or tuple(sorted(set(active))) != active
        or not set(active) <= set(OPTIONS)
        or incumbent not in OPTIONS
        or incumbent in active
    ):
        raise RuntimeError("unexpected public alternatives")
    for role in ROLES:
        if briefs[role] != {
            "role": role,
            "incumbent": incumbent,
            "options": list(OPTIONS),
        }:
            raise RuntimeError("unexpected native brief")
        _call(world, role, "POST", "/endorse", {"slot": incumbent})
    cards: dict[str, tuple[str, ...]] = {}
    packets: list[bytes] = []
    for role in ROLES:
        card = _call(world, role, "GET", "/card", {})
        slots = card.get("slots")
        if (
            not isinstance(slots, list)
            or len(slots) != 2
            or not all(isinstance(slot, str) and slot in active for slot in slots)
            or cast(str, slots[0]) >= cast(str, slots[1])
        ):
            raise RuntimeError("unexpected native card")
        cards[role] = tuple(cast(list[str], slots))
        packets.append(
            _encode(
                {
                    "schema": SCHEMA,
                    "stage": "report",
                    "role": role,
                    "instruction": REPORT_INSTRUCTION,
                    "incumbent": incumbent,
                    "options": OPTIONS,
                    "active": active,
                    "own": cards[role],
                }
            )
        )
    if set(cards["east"]) | set(cards["west"]) != set(active):
        raise RuntimeError("native cards do not match the public alternatives")
    return world, (packets[0], packets[1]), cards


def cases(start: int) -> tuple[Case, ...]:
    seeds = native.balanced_seeds(start)
    active = tuple(sorted(native.permutation(start)[1:4]))
    cohort: list[Case] = []
    for seed in seeds:
        _, reports, _ = _open(seed, active)
        cohort.append(Case(seed, active, reports))
    return tuple(cohort)


def exchange(case: Case, east: str, west: str) -> Branch:
    world, reports, cards = _open(case.seed, case.active)
    if reports != case.reports:
        raise ValueError("native report observations changed")
    outgoing = {"east": east, "west": west}
    if any(outgoing[role] not in cards[role] for role in ROLES):
        raise ValueError("report must contain one own available slot")
    for role in ROLES:
        _call(world, role, "POST", "/report", {"slots": [outgoing[role]]})
    inboxes = {role: _call(world, role, "GET", "/inbox", {}) for role in ROLES}
    votes: list[bytes] = []
    for index, role in enumerate(ROLES):
        inbox = inboxes[role]
        received = inbox.get("slots")
        if inbox.get("delivered") is not True or received != [
            outgoing[ROLES[1 - index]]
        ]:
            raise RuntimeError("native peer report was not delivered")
        peer = cast(list[str], received)[0]
        fields = cast(dict[str, object], json.loads(reports[index]))
        fields.update(
            stage="vote", instruction=VOTE_INSTRUCTION, sent=outgoing[role], peer=peer
        )
        votes.append(_encode(fields))
    return Branch((votes[0], votes[1]), (east, west), world)


def grade(branch: Branch, east: str, west: str) -> int:
    if east not in OPTIONS or west not in OPTIONS:
        raise ValueError("vote must contain one legal slot")
    return native.grade_pair(branch._world, east, west)
