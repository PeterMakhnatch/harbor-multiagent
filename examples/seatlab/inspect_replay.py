"""Replay public SeatLab facts with a user-selected Inspect model.

From the repository root:
    inspect eval examples/seatlab/inspect_replay.py@seatlab_facts --model MODEL
"""

import sys
from contextvars import ContextVar
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessage, ChatMessageSystem, ChatMessageUser
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    NOANSWER,
    Score,
    Scorer,
    Target,
    scorer,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver

if __package__ == "examples.seatlab":
    from .committee import ballot_packet, packing
else:
    # Inspect loads a task file outside its package and changes into its directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from examples.seatlab.committee import ballot_packet, packing
    finally:
        sys.path.pop(0)

_SYSTEM = "Answer only with JSON."


class _Turn:
    def __init__(self, cell: packing.Cell, user_text: str) -> None:
        self.cell = cell
        self.user_text = user_text
        self.live = True


_active_turn: ContextVar[_Turn | None] = ContextVar("seatlab_active_turn", default=None)


def active_cell() -> tuple[packing.Cell, str] | None:
    """Return the host scope and sealed prompt only during this task's generation."""
    turn = _active_turn.get()
    if turn is None or not turn.live:
        return None
    return turn.cell, turn.user_text


def _source_args(
    packet_path: str | Path | None, expected_sha256: str | None
) -> tuple[Path, str]:
    if packet_path is None and expected_sha256 is None:
        return packing.PACKET, packing.PACKET_SHA256
    if packet_path in (None, "") or expected_sha256 is None:
        raise ValueError("packet_path and expected_sha256 must be provided together")
    return Path(packet_path).absolute(), expected_sha256


def _metadata(cell: packing.Cell, digest: str) -> dict[str, int | str]:
    return {
        "block": cell.block,
        "map_index": cell.map_index,
        "seat_index": cell.seat_index,
        "arm": cell.arm,
        "packet_sha256": digest,
    }


def _cell_for(
    state: TaskState, cells: dict[str, packing.Cell], digest: str
) -> packing.Cell:
    cell = cells.get(state.sample_id) if isinstance(state.sample_id, str) else None
    if cell is None:
        raise ValueError("unknown SeatLab sample ID")
    for key, expected in _metadata(cell, digest).items():
        actual = state.metadata.get(key)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError("SeatLab sample metadata does not match the sealed cell")
    return cell


def _matches(messages: str | list[ChatMessage], user_text: str) -> bool:
    return (
        isinstance(messages, list)
        and len(messages) == 2
        and isinstance(messages[0], ChatMessageSystem)
        and messages[0].content == _SYSTEM
        and messages[0].metadata is None
        and isinstance(messages[1], ChatMessageUser)
        and messages[1].content == user_text
        and messages[1].metadata is None
        and messages[1].tool_call_id is None
    )


@solver(name="seatlab_facts_turn")
def _scoped_turn(
    packet_path: str | None = None, expected_sha256: str | None = None
) -> Solver:
    path, digest = _source_args(packet_path, expected_sha256)
    packet = ballot_packet.read_packet(path, digest)
    cells = {cell.cell_id: cell for cell in packing.grid()}
    prompts = {key: packing.prompt(packet, cell) for key, cell in cells.items()}

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        cell = _cell_for(state, cells, digest)
        user_text = prompts[cell.cell_id]
        if not _matches(state.input, user_text) or not _matches(
            state.messages, user_text
        ):
            raise ValueError("SeatLab messages do not match the sealed cell")
        if state.tools or active_cell() is not None:
            raise ValueError("SeatLab requires a single generation without tools")
        state.tool_choice = "none"
        turn = _Turn(cell, user_text)
        token = _active_turn.set(turn)
        try:
            return await generate(
                state, tool_calls="none", cache=False, system_message=""
            )
        finally:
            # A child task may have copied the context before this turn ended.
            turn.live = False
            _active_turn.reset(token)

    return solve


@scorer(metrics=[], name="seatlab_facts_score")
def _facts_score(
    packet_path: str | None = None, expected_sha256: str | None = None
) -> Scorer:
    path, digest = _source_args(packet_path, expected_sha256)
    packet = ballot_packet.read_packet(path, digest)
    cells = {cell.cell_id: cell for cell in packing.grid()}

    async def score(state: TaskState, target: Target) -> Score:
        cell = _cell_for(state, cells, digest)
        if not _matches(state.input, packing.prompt(packet, cell)):
            raise ValueError("SeatLab input does not match the sealed cell")
        result = packing.score(packet, cell, state.output.completion)
        if result["status"] == "accepted":
            value = CORRECT if result["exact_totals"] else INCORRECT
        else:
            value = NOANSWER
        return Score(value=value, metadata=dict(result))

    return score


@task
def seatlab_facts(
    packet_path: str | Path | None = None, expected_sha256: str | None = None
) -> Task:
    """Replay both evidence formats without exposing the original ballots."""
    path, digest = _source_args(packet_path, expected_sha256)
    packet = ballot_packet.read_packet(path, digest)
    # Only the path and commitment, never the source ballots, enter Inspect's
    # registered solver and scorer arguments.
    overrides = (str(path), digest) if packet_path is not None else ()
    return Task(
        name="seatlab_facts",
        dataset=[
            Sample(
                id=cell.cell_id,
                input=[
                    ChatMessageSystem(content=_SYSTEM),
                    ChatMessageUser(content=packing.prompt(packet, cell)),
                ],
                metadata=_metadata(cell, digest),
            )
            for cell in packing.grid()
        ],
        solver=_scoped_turn(*overrides),
        scorer=_facts_score(*overrides),
        metrics=[],
    )
