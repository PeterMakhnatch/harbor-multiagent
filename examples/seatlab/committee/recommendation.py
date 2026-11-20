"""Decode the native recommendation board and project phase-specific model views."""

from collections.abc import Collection
from dataclasses import dataclass
from typing import TypedDict

from . import contract, schedule

_ROLES = schedule.ROLES
_SEAT_ALIASES = {role: f"S{i}" for i, role in enumerate(_ROLES, 1)}
_PREFIX = {"proposal": "PROPOSAL ", "recommend": "RECOMMEND "}
_NATIVE_VOTE_FIELDS = {"choice", "cited_facts"}
_FACT_FIELDS = {"id", "option", "points"}
_MAX_POST_CHARS = 128


class _Ballot(TypedDict):
    choice: str
    cited: list[str]


@dataclass(frozen=True, slots=True)
class Board:
    facts: dict
    proposals: dict[str, _Ballot]
    recommendation: _Ballot | None
    _map_index: int
    _chair: str


def _checked_ballot(
    choice: object,
    cited: object,
    choices: Collection[str],
    fact_ids: Collection[str],
) -> tuple[str, list[str]]:
    if not isinstance(choice, str) or choice not in choices:
        raise ValueError("unknown ballot choice")
    if (
        not isinstance(cited, list)
        or not 1 <= len(cited) <= len(fact_ids)
        or any(not isinstance(item, str) or item not in fact_ids for item in cited)
        or len(cited) != len(set(cited))
    ):
        raise ValueError("ballot must cite distinct known facts")
    return choice, cited


def blind_vote(native_vote: object, map_index: int) -> _Ballot:
    """Translate a strict native vote body without changing its citation order."""
    options, aliases = contract._mapping(map_index)
    row = contract._object(native_vote, _NATIVE_VOTE_FIELDS, "native vote")
    choice, cited = _checked_ballot(row["choice"], row["cited_facts"], options, aliases)
    return {"choice": options[choice], "cited": [aliases[fact_id] for fact_id in cited]}


def post(phase: str, native_vote: object, map_index: int) -> dict[str, str]:
    """Build a native chat POST body using only blind aliases."""
    if phase not in ("proposal", "recommend"):
        raise ValueError("unknown ballot post phase")
    text = _PREFIX[phase] + contract._compact_json(blind_vote(native_vote, map_index))
    return {"text": text}


def _blind_ballot(value: object, aliases: Collection[str]) -> _Ballot:
    row = contract._object(value, contract._VOTE_FIELDS, "blind ballot")
    choice, cited = _checked_ballot(
        row["choice"], row["cited"], contract._LABELS, aliases
    )
    return {"choice": choice, "cited": cited}


def board(snapshot: object, map_index: int, chair: str) -> Board:
    """Read one ordered native GET snapshot, which can include faster peers' posts."""
    _, aliases = contract._mapping(map_index)
    if chair not in _ROLES:
        raise ValueError("unknown chair")
    chat = contract._object(snapshot, {"messages", "unique_speakers"}, "chat snapshot")
    messages = chat["messages"]
    if not isinstance(messages, list) or len(messages) > 2 * len(_ROLES) + 1:
        raise ValueError("invalid board message count")

    fact_messages = []
    fact_seats: set[str] = set()
    public_facts: dict[str, contract._Fact] = {}
    proposals: dict[str, _Ballot] = {}
    recommendation = None
    last_seq = 0
    for value in messages:
        msg = contract._object(value, {"seat", "text", "seq"}, "chat message")
        seat, text, seq = msg["seat"], msg["text"], msg["seq"]
        if not isinstance(seat, str) or seat not in _ROLES:
            raise ValueError("unknown chat speaker")
        if type(seq) is not int or seq <= last_seq:
            raise ValueError("chat sequence must strictly increase")
        last_seq = seq
        if not isinstance(text, str) or len(text) > _MAX_POST_CHARS:
            raise ValueError("invalid board message text")

        prefix, _, payload = text.partition(" ")
        if prefix in ("PROPOSAL", "RECOMMEND"):
            if len(fact_seats) != len(_ROLES):
                raise ValueError("ballot posted before all facts")
            if prefix == "PROPOSAL":
                if seat in proposals:
                    raise ValueError("duplicate proposal")
            elif (
                seat != chair
                or len(proposals) != len(_ROLES)
                or recommendation is not None
            ):
                raise ValueError(
                    "recommendation must follow all proposals and come from the chair"
                )
            visible_ids = (
                public_facts.keys()
                | contract._SHARED_IDS
                | {contract._PRIVATE_IDS[seat]}
            )
            ballot = _blind_ballot(
                contract._read_json(payload, contract._VOTE_FIELDS, "board ballot"),
                {aliases[fact_id] for fact_id in visible_ids},
            )
            if text != prefix + " " + contract._compact_json(ballot):
                raise ValueError("noncanonical board ballot")
            if prefix == "PROPOSAL":
                proposals[seat] = ballot
            else:
                recommendation = ballot
        else:
            if seat in fact_seats:
                raise ValueError("duplicate fact submission")
            row = contract._read_json(text, _FACT_FIELDS, "chat fact")
            fact_id, fact = contract._native_fact(row)
            if (
                fact_id not in contract._SHARED_IDS
                and fact_id != contract._PRIVATE_IDS[seat]
            ):
                raise ValueError("fact does not belong to the speaker's dossier")
            if fact_id in public_facts and fact != public_facts[fact_id]:
                raise ValueError("conflicting public facts")
            canonical = contract._compact_json(
                {"id": fact_id, "option": fact.option, "points": fact.points}
            )
            if text != canonical:
                raise ValueError("noncanonical board fact")
            public_facts[fact_id] = fact
            fact_seats.add(seat)
            fact_messages.append({"seat": seat, "text": canonical, "seq": seq})

    speakers = sorted(fact_seats)
    if chat["unique_speakers"] != speakers:
        raise ValueError("chat speakers do not match the native board")
    return Board(
        {"messages": fact_messages, "unique_speakers": speakers},
        proposals,
        recommendation,
        map_index,
        chair,
    )


def turn_prompt(
    phase: str,
    dossier: dict,
    board: Board,
    map_index: int,
    role: str,
    chair: str,
    arm: str,
) -> str:
    """Render the chair recommendation or the final vote from a complete phase."""
    if (
        role not in _ROLES
        or chair != board._chair
        or map_index != board._map_index
        or arm not in schedule.RECOMMENDATION_ARMS
        or not isinstance(dossier, dict)
        or dossier.get("seat") != role
        or board.proposals.keys() != set(_ROLES)
    ):
        raise ValueError("invalid recommendation prompt view")
    if phase == "recommend":
        if role != chair or board.recommendation is not None:
            raise ValueError("only the chair can make a new recommendation")
    elif phase == "vote":
        if board.recommendation is None:
            raise ValueError("final vote requires a chair recommendation")
    else:
        raise ValueError("unknown recommendation prompt phase")

    view = contract._vote_view(dossier, board.facts, map_index)
    aliases = view.aliases.values()
    proposals = {seat: _blind_ballot(board.proposals[seat], aliases) for seat in _ROLES}
    submissions = sorted(view.submissions, key=lambda row: view.aliases[row[0]])
    lines = [
        contract._OBJECTIVE,
        contract._dossier_line(view.facts, view.options, view.aliases),
        "Submissions: "
        + contract._compact_json(
            [
                contract._shown_fact(fact_id, fact, view.options, view.aliases)
                for fact_id, fact in submissions
            ]
        ),
        "A submission can repeat a fact from your dossier or another submission. "
        "Count that fact only once.",
        "Proposals: "
        + contract._compact_json(
            [
                {"seat": _SEAT_ALIASES[seat], **proposal}
                for seat, proposal in proposals.items()
            ]
        ),
        "Your first ballot: " + contract._compact_json(proposals[role]),
    ]
    if phase == "recommend":
        lines.append(
            "Make one recommendation for the committee. This is not your final vote."
        )
    else:
        actual = _blind_ballot(board.recommendation, aliases)
        shown = actual if arm == "shown" or role == chair else None
        lines.extend(
            (
                "Recommendation: " + contract._compact_json(shown),
                "Cast your final vote.",
            )
        )
    lines.extend(
        (
            "Cite one or more distinct fact aliases from the dossier or the submissions.",
            contract._PLAIN_VOTE_FORMAT,
        )
    )
    return "\n".join(lines)
