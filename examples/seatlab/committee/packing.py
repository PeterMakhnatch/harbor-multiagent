"""Replay committed committee facts with two equivalent evidence layouts."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from . import analysis, ballot_packet, contract
from .schedule import ROLES

PACKET = Path(__file__).resolve().parents[1] / "results" / "peer-ballot-packet.json"
PACKET_SHA256 = "c8f743895f9e2fbecdf8b58201100bdb602186672a482c5545549408c5857095"
_ARMS = ("separate", "indexed")
_FIRST_ORDER = ["totals", "choice", "cited"]

type _JsonObject = dict[str, object]


class ScoreResult(TypedDict):
    status: Literal["accepted", "invalid_response"]
    choice: str | None
    cited: list[str] | None
    totals: dict[str, int] | None
    exact_totals: bool | None
    correct_choice: bool | None
    correct_visible_choice: bool | None
    order_followed: bool | None


@dataclass(frozen=True, slots=True)
class Cell:
    block: int
    map_index: int
    seat_index: int
    arm: str

    @property
    def cell_id(self) -> str:
        return f"ix-b{self.block}-m{self.map_index}-s{self.seat_index + 1}-{self.arm}"

    @property
    def role(self) -> str:
        return ROLES[self.seat_index]


def grid() -> tuple[Cell, ...]:
    """Order both layouts for each source seat in two counterbalanced blocks."""
    return tuple(
        Cell(block, map_index, seat_index, arm)
        for block in range(2)
        for map_index in range(6)
        for seat_index in range(len(ROLES))
        for arm in (_ARMS[::-1] if (block + map_index + seat_index) % 2 else _ARMS)
    )


def _evidence(packet: _JsonObject, cell: Cell) -> tuple[_JsonObject, _JsonObject]:
    if type(cell.block) is not int or not 0 <= cell.block < 2:
        raise ValueError("invalid packing block")
    if cell.arm not in _ARMS:
        raise ValueError("invalid packing arm")
    team = ballot_packet._team(packet, cell.map_index, cell.seat_index)
    return ballot_packet._dossier(cell.role), ballot_packet._chat(team)


def _visible_facts(view: contract._VoteView) -> dict[str, contract._Fact]:
    return view.facts | dict(view.submissions)


def _index_line(view: contract._VoteView) -> str:
    posts = Counter(fact_id for fact_id, _ in view.submissions)
    visible = _visible_facts(view)
    rows = [
        {
            **contract._shown_fact(
                fact_id, visible[fact_id], view.options, view.aliases
            ),
            "in_dossier": fact_id in view.facts,
            "post_count": posts[fact_id],
        }
        for fact_id in sorted(visible, key=view.aliases.__getitem__)
    ]
    return "Fact index: " + contract._compact_json(
        {"options": contract._LABELS, "facts": rows}
    )


def prompt(packet: _JsonObject, cell: Cell) -> str:
    """Render only the source dossier and posts from a verified ballot packet."""
    dossier, chat = _evidence(packet, cell)
    separate = contract.tally_prompt(dossier, chat, cell.map_index, "first")
    if cell.arm == "separate":
        return separate

    view = contract._vote_view(dossier, chat, cell.map_index)
    objective, _, _, instructions = separate.split("\n", 3)
    return "\n".join((objective, _index_line(view), instructions))


def _signed_totals(
    facts: Iterable[tuple[str, int]], options: dict[str, str]
) -> dict[str, int]:
    totals = dict.fromkeys(options.values(), 0)
    for option, points in facts:
        totals[options[option]] += points
    return totals


def score(packet: _JsonObject, cell: Cell, text: str | None) -> ScoreResult:
    """Score a fresh response against verified evidence, not the source ballots."""
    dossier, chat = _evidence(packet, cell)
    try:
        action, readout = contract.tally_action(
            text if text is not None else "", dossier, chat, cell.map_index, "first"
        )
    except ValueError:
        return ScoreResult(
            status="invalid_response",
            choice=None,
            cited=None,
            totals=None,
            exact_totals=None,
            correct_choice=None,
            correct_visible_choice=None,
            order_followed=None,
        )

    view = contract._vote_view(dossier, chat, cell.map_index)
    visible_totals = _signed_totals(
        ((fact.option, fact.points) for fact in _visible_facts(view).values()),
        view.options,
    )
    # Score the full stock world separately, including facts not visible to this seat.
    native_totals = _signed_totals(analysis._FACTS.values(), view.options)
    ballot = ballot_packet._blinded(action, cell.map_index)
    choice, totals = ballot["choice"], readout["totals"]
    return ScoreResult(
        status="accepted",
        choice=choice,
        cited=ballot["cited"],
        totals=totals,
        exact_totals=totals == visible_totals,
        correct_choice=native_totals[choice] == max(native_totals.values()),
        correct_visible_choice=visible_totals[choice] == max(visible_totals.values()),
        order_followed=readout["field_order"] == _FIRST_ORDER,
    )
