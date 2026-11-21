import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from ..visibility import calendar_packet, calendar_protocol

WORLDS = tuple(f"w{i:02d}" for i in range(8))
SEATS = ("p1", "p2")
ARMS = ("visible", "withheld")
REPAIR_ARMS = ("model-report", "local-report")
QUESTION_ARMS = ("raw-question", "local-question")
_ARMS = ARMS + REPAIR_ARMS + QUESTION_ARMS
_ANSWER_FIRST = tuple(i % 2 for i in range(len(WORLDS)))
_BLOCK_FIRST = (0, 0, 1, 1, 1, 1, 0, 0)
_QUESTION_FIRST = tuple(1 - prior for prior in _BLOCK_FIRST)
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
            or self.arm not in _ARMS
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
        if self.arm in QUESTION_ARMS:
            return QUESTION_ARMS[_QUESTION_FIRST[self.index]]
        if self.arm in REPAIR_ARMS:
            return REPAIR_ARMS[_BLOCK_FIRST[self.index]]
        return ARMS[_ANSWER_FIRST[self.index]]

    @property
    def run(self) -> str:
        mode = "control-" if self.control else ""
        if self.arm in QUESTION_ARMS:
            family = "naquestion"
        else:
            family = "narepair" if self.arm in REPAIR_ARMS else "nanswer"
        return f"{family}-{mode}{self.world}-{self.arm}"

    @property
    def task_name(self) -> str:
        return f"schedule-{self.index:03d}-talk"

    def actor(self, phase: str) -> str:
        return self.peer if phase == "answer" else self.receiver

    @property
    def shows_answer(self) -> bool:
        return self.arm != "withheld"

    def question_prompt(self, table: calendar_packet.Table) -> str:
        if self.arm != "local-question":
            return question_prompt(table)
        header = calendar_protocol._RULES.rsplit("\n\n", 1)[0]
        starts = wire(_validated_starts(table))
        return (
            f"{header}\n\n"
            "Your calendar is represented by the slot IDs that fit every attendee "
            "at your seat.\n"
            f"{starts}\n\nTask:\n" + _QUESTION_INSTRUCTION
        )

    def control_question(self, table: calendar_packet.Table) -> str:
        if not self.control:
            raise ValueError("invalid_control_input")
        if self.arm != "local-question":
            return control_reply("question", table, None, None)
        starts = _validated_starts(table)
        if len(starts) < 2:
            raise ValueError("insufficient_local_control_starts")
        return question_wire(list(starts[:2]))

    def sent_bits(
        self, table: calendar_packet.Table, slots: list[int], raw_bits: list[bool]
    ) -> list[bool]:
        if self.arm == "local-report" or self.arm in QUESTION_ARMS:
            return _local_bits(table, slots)
        return raw_bits

    def draft_control_bits(
        self, table: calendar_packet.Table, slots: list[int]
    ) -> list[bool]:
        if not self.control:
            raise ValueError("invalid_control_input")
        bits = _local_bits(table, slots)
        if self.arm in REPAIR_ARMS or self.arm in QUESTION_ARMS:
            return [not bit for bit in bits]
        return bits


def _grids(
    arms: tuple[str, ...], order: tuple[int, ...], *, control: bool
) -> tuple[Cell, ...]:
    return tuple(
        Cell(world, arms[(first + offset) % len(arms)], control)
        for world, first in zip(WORLDS, order, strict=True)
        for offset in range(len(arms))
    )


def grids(*, control: bool = False) -> tuple[Cell, ...]:
    return _grids(ARMS, _ANSWER_FIRST, control=control)


def repair_grids(*, control: bool = False) -> tuple[Cell, ...]:
    return _grids(REPAIR_ARMS, _BLOCK_FIRST, control=control)


def question_grids(*, control: bool = False) -> tuple[Cell, ...]:
    return _grids(QUESTION_ARMS, _QUESTION_FIRST, control=control)


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


_QUESTION_INSTRUCTION = (
    "Use your own calendar to pick exactly 2 distinct slot IDs that give you "
    "the best chance of finding a joint start. Prefer slots that fit your "
    "calendar. Ask about 2 distinct legal slots even if fewer than 2 fit. "
    'Return only {"slots":[first_id,second_id]} in your preferred order.\n'
)


def question_prompt(table: calendar_packet.Table) -> str:
    return calendar_protocol._view(table) + _QUESTION_INSTRUCTION


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


def _validated_starts(table: calendar_packet.Table) -> tuple[int, ...]:
    return calendar_protocol._checked_starts(calendar_packet.starts(table))


def _local_bits(table: calendar_packet.Table, slots: list[int]) -> list[bool]:
    own = calendar_packet.starts(table)
    return [slot in own for slot in slots]


def control_reply(
    phase: str,
    table: calendar_packet.Table,
    slots: list[int] | None,
    bits: list[bool] | None,
) -> str:
    if phase == "answer" and slots is not None:
        return answer_wire(_local_bits(table, slots))
    own = calendar_packet.starts(table)
    if phase == "question":
        candidates = (*own, *(slot for slot in range(35) if slot not in own))
        return question_wire(list(candidates[:2]))
    if phase == "choice" and slots is not None:
        if bits is not None:
            for slot, available in zip(slots, bits, strict=True):
                if available and slot in own:
                    return wire({"slot": slot})
        return wire({"slot": slots[0]})
    raise ValueError("invalid_control_input")
