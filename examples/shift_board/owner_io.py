import re
from typing import Any

from ..native_owner_process import call_owner

REQUEST_SCHEMA = "harbor.shift-board.turn/v1"
REPLY_SCHEMA = "harbor.shift-board.choice/v1"
RECEIPT_SCHEMA = "harbor.shift-board.owner-receipt/v1"
METADATA_KEY = "shift_board_owner"
OWNER_VARIABLES = {
    "planner": "SHIFT_BOARD_PLANNER_OWNER",
    "dispatcher": "SHIFT_BOARD_DISPATCHER_OWNER",
}
VIEW_FIELDS = frozenset(
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
STAGES = frozenset({"plan", "prepare", "decide"})
KINDS = frozenset({"prepare", "publish", "read", "leave", "stop"})
CHOICE = re.compile(r"prepare:[A-D]|publish:[A-D]@(?:draft|current)|read|leave|stop")
MAX_TURN = 5
MAX_CHOICES = 16
PUBLIC_INSTRUCTIONS = (
    "Two seats, planner and dispatcher, share one shift board. "
    "Score: 1 only if the final published shift fits both calendars, otherwise 0. "
    "Preferences do not count toward the score. "
    "Each seat sees only its own calendar; the partner calendar is private. "
    "The dispatcher prepares a draft, then the planner publishes. "
    "publish:X@draft writes against the original empty-board revision; "
    "publish:X@current writes against the revision returned by an explicit read. "
    "A stale write may succeed or be refused; a refusal does not finish play. "
    "leave and stop preserve the standing answer. "
    "Reply with exactly one of the offered choices."
)

assert PUBLIC_INSTRUCTIONS.isascii() and len(PUBLIC_INSTRUCTIONS) <= 700


def _check_view(view: dict[str, Any]) -> str:
    if type(view) is not dict or set(view) != VIEW_FIELDS:
        raise ValueError("owner view fields differ")
    role = view["role"]
    if role not in OWNER_VARIABLES:
        raise ValueError("owner view role is unknown")
    turn = view["turn"]
    if type(turn) is not int or not 1 <= turn <= MAX_TURN:
        raise ValueError("owner view turn is out of range")
    if view["stage"] not in STAGES:
        raise ValueError("owner view stage is unknown")
    choices = view["choices"]
    if type(choices) is not list or not 1 <= len(choices) <= MAX_CHOICES:
        raise ValueError("owner view choices are not a bounded list")
    if len(set(choices)) != len(choices) or not all(
        type(choice) is str and CHOICE.fullmatch(choice) for choice in choices
    ):
        raise ValueError("owner view choices are malformed")
    return role


async def choose(view: dict[str, Any], requested: str) -> tuple[str, dict[str, Any]]:
    role = _check_view(view)
    choice, served = await call_owner(
        role=role,
        variable=OWNER_VARIABLES[role],
        requested=requested,
        request_schema=REQUEST_SCHEMA,
        reply_schema=REPLY_SCHEMA,
        instructions=PUBLIC_INSTRUCTIONS,
        view=view,
    )
    kind = choice.split(":", 1)[0]
    assert kind in KINDS
    receipt = {
        "turn": view["turn"],
        "stage": view["stage"],
        "kind": kind,
        "served_model": served,
    }
    return choice, receipt
