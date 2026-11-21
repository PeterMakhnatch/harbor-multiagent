import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from ..visibility import calendar_packet, calendar_protocol

WORLDS = tuple(f"w{i:02d}" for i in range(8))
SEATS = ("p1", "p2")
ARMS = ("visible", "withheld")
PHASES = ("question", "answer", "choice")
LEDGER_ROLES = dict(
    zip(PHASES, ("cal_tool_pick", "cal_tool_reply", "cal_decide"), strict=True)
)
MODEL = "deepseek/deepseek-flash"
AGENT_NAME = "seatlab-native-calendar-answers"
AGENT_VERSION = "1.0.0"
AGENT_IMPORT = "examples.seatlab.live_answers.seat:AnswerSeat"
TURN_SCHEMA = "seatlab.native-calendar-answer-turn/v1"
SEAT_SCHEMA = "seatlab.native-calendar-answer-seat/v1"
POLICY = "directed-flotilla-messages; gate-only-final-prompt"


@dataclass(frozen=True, slots=True)
class Cell:
    world: str
    arm: str
    control: bool = False

    def __post_init__(self) -> None:
        if (
            self.world not in WORLDS
            or self.arm not in ARMS
            or type(self.control) is not bool
        ):
            raise ValueError("invalid_cell")

    @property
    def index(self) -> int:
        return WORLDS.index(self.world)

    @property
    def receiver(self) -> str:
        return SEATS[self.index % 2]

    @property
    def peer(self) -> str:
        return SEATS[1 - self.index % 2]

    @property
    def first(self) -> str:
        return ARMS[self.index % 2]

    @property
    def run(self) -> str:
        mode = "control-" if self.control else ""
        return f"nanswer-{mode}{self.world}-{self.arm}"

    @property
    def task_name(self) -> str:
        return f"schedule-{self.index:03d}-talk"

    def actor(self, phase: str) -> str:
        return self.peer if phase == "answer" else self.receiver


def grids(*, control: bool = False) -> tuple[Cell, ...]:
    return tuple(
        Cell(world, ARMS[(i + offset) % 2], control)
        for i, world in enumerate(WORLDS)
        for offset in range(len(ARMS))
    )


def wire(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


def digest(text: str | bytes) -> str:
    return sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def decode(text: str, phase: str) -> Any:
    fields = {"question": "slots", "answer": "available", "choice": "slot"}
    field = fields[phase]
    value = calendar_packet._json(text)
    if not isinstance(value, dict) or value.keys() != {field}:
        raise ValueError(f"invalid_{phase}")
    content = value[field]
    if phase == "choice":
        return calendar_protocol._checked_slot(content)
    if not isinstance(content, list) or len(content) != 2:
        raise ValueError(f"invalid_{phase}")
    if phase == "question":
        first, second = (calendar_protocol._checked_slot(slot) for slot in content)
        if first == second:
            raise ValueError("duplicate_question_slots")
    elif any(type(bit) is not bool for bit in content):
        raise ValueError("invalid_answer_bits")
    return content


def question_wire(slots: list[int]) -> str:
    return wire({"slots": slots})


def answer_wire(bits: list[bool]) -> str:
    return wire({"available": bits})


def question_prompt(table: calendar_packet.Table) -> str:
    return calendar_protocol._view(table) + (
        "Use your own calendar to pick exactly 2 distinct slot IDs that give you "
        "the best chance of finding a joint start. Prefer slots that fit your "
        "calendar. Ask about 2 distinct legal slots even if fewer than 2 fit. "
        'Return only {"slots":[first_id,second_id]} in your preferred order.\n'
    )


def answer_prompt(table: calendar_packet.Table, question: str) -> str:
    return calendar_protocol._view(table) + (
        f"The other seat asks about these two starts: {question}\n"
        "For each slot ID, say whether the two-hour meeting fits every attendee "
        "on your calendar. Return exactly one JSON Boolean per slot in the same "
        'order: {"available":[first_boolean,second_boolean]}. No other fields.\n'
    )


def choice_prompt(
    table: calendar_packet.Table, question: str, bits: list[bool] | None
) -> str:
    return calendar_protocol._view(table) + (
        f"Your question to the other seat: {question}\n"
        "Peer availability is one Boolean per asked slot in the order shown, "
        "or null when availability is not supplied.\n"
        f"Peer availability: {wire(bits)}\n"
        "Choose one start that best fits both seats. Recheck your own calendar. "
        "You may choose any legal slot ID, including one that was not requested. "
        'Return only {"slot":chosen_id}.\n'
    )


def control_reply(
    phase: str,
    table: calendar_packet.Table,
    slots: list[int] | None,
    bits: list[bool] | None,
) -> str:
    own = calendar_packet.starts(table)
    if phase == "question":
        candidates = (*own, *(slot for slot in range(35) if slot not in own))
        return question_wire(list(candidates[:2]))
    if phase == "answer" and slots is not None:
        return answer_wire([slot in own for slot in slots])
    if phase == "choice" and slots is not None:
        if bits is not None:
            for slot, available in zip(slots, bits, strict=True):
                if available and slot in own:
                    return wire({"slot": slot})
        return wire({"slot": slots[0]})
    raise ValueError("invalid_control_input")
