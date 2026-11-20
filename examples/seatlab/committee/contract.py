"""Project native world evidence to blinded prompts and decode model actions."""

import json
from dataclasses import dataclass
from hashlib import sha256
from itertools import permutations
from typing import NoReturn

from .schedule import ARMS as ARMS
from .schedule import ROLES as ROLES

_NATIVE_OPTIONS = ("Option_A", "Option_B", "Option_C")
_LABELS = ("X", "Y", "Z")
_SHARED_IDS = frozenset(f"F_SHARED_{i}" for i in range(1, 6))
_PRIVATE_IDS = {role: f"F_PRIV_{i}" for i, role in enumerate(ROLES, 1)}
_FACT_IDS = _SHARED_IDS.union(_PRIVATE_IDS.values())
_OPTION_LABELS = tuple(
    dict(zip(_NATIVE_OPTIONS, labels, strict=True)) for labels in permutations(_LABELS)
)
_OBJECTIVE = (
    "Choose the option with the highest net score. Add the signed points of "
    "confirmed facts for each option. Count each fact alias only once."
)
_NOVELTY_LINE = "Prefer a fact the other analysts cannot already see."


def _aliases_for(map_index: int) -> dict[str, str]:
    def sort_key(fact_id: str) -> tuple[bytes, str]:
        key = f"seatlab-committee-v1:{map_index}:{fact_id}".encode("utf-8")
        return sha256(key).digest(), fact_id

    return {
        fact_id: f"K{i}"
        for i, fact_id in enumerate(sorted(_FACT_IDS, key=sort_key), 1)
    }


_FACT_ALIASES = tuple(_aliases_for(i) for i in range(len(_OPTION_LABELS)))


@dataclass(frozen=True, slots=True)
class _Fact:
    option: str
    points: int


def _mapping(map_index: int) -> tuple[dict[str, str], dict[str, str]]:
    if type(map_index) is not int or not 0 <= map_index < len(_OPTION_LABELS):
        raise ValueError("map index must be an integer from 0 through 5")
    return _OPTION_LABELS[map_index], _FACT_ALIASES[map_index]


def _object(value: object, fields: set[str], source: str) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError(f"invalid {source} fields")
    return value


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate JSON fields")
    return fields


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("nonfinite JSON number")


def _read_json(text: object, fields: set[str], source: str) -> dict[str, object]:
    if not isinstance(text, str):
        raise ValueError(f"invalid {source} text")
    try:
        value = json.loads(
            text, object_pairs_hook=_json_pairs, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError):
        raise ValueError(f"invalid {source} JSON") from None
    return _object(value, fields, source)


def _compact_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _native_fact(row: dict[str, object]) -> tuple[str, _Fact]:
    fact_id, option, points = row["id"], row["option"], row["points"]
    if not isinstance(fact_id, str) or fact_id not in _FACT_IDS:
        raise ValueError("unknown native fact ID")
    if not isinstance(option, str) or option not in _NATIVE_OPTIONS:
        raise ValueError("unknown native option")
    if type(points) is not int:
        raise ValueError("fact points must be an integer")
    return fact_id, _Fact(option, points)


def _dossier_facts(dossier: dict) -> dict[str, _Fact]:
    dossier = _object(
        dossier, {"seat", "options", "rubric", "shared_facts", "private_facts"}, "dossier"
    )
    seat, options, rubric = dossier["seat"], dossier["options"], dossier["rubric"]
    if not isinstance(seat, str) or seat not in _PRIVATE_IDS:
        raise ValueError("unknown dossier seat")
    if not isinstance(options, list) or options != list(_NATIVE_OPTIONS):
        raise ValueError("invalid dossier options")
    if not isinstance(rubric, str) or not rubric.strip():
        raise ValueError("invalid dossier rubric")

    facts = {}
    for key, expected_ids in (
        ("shared_facts", _SHARED_IDS), ("private_facts", {_PRIVATE_IDS[seat]})
    ):
        rows = dossier[key]
        if not isinstance(rows, list) or len(rows) != len(expected_ids):
            raise ValueError("invalid dossier fact count")
        for value in rows:
            row = _object(value, {"id", "kind", "option", "points", "text"}, "dossier fact")
            if not isinstance(row["kind"], str) or not isinstance(row["text"], str):
                raise ValueError("invalid dossier fact description")
            fact_id, fact = _native_fact(row)
            if fact_id not in expected_ids or fact_id in facts:
                raise ValueError("absent or ambiguous dossier fact")
            facts[fact_id] = fact
    return facts


def _chat_facts(chat: dict, dossier: dict[str, _Fact]) -> list[tuple[str, _Fact]]:
    chat = _object(chat, {"messages", "unique_speakers"}, "chat snapshot")
    messages, speakers = chat["messages"], chat["unique_speakers"]
    if (
        not isinstance(speakers, list)
        or any(not isinstance(speaker, str) for speaker in speakers)
        or sorted(speakers) != list(ROLES)
    ):
        raise ValueError("chat must identify all four speakers")
    if not isinstance(messages, list) or len(messages) != len(ROLES):
        raise ValueError("chat must contain four submissions")

    seen_seats = set()
    seen_seqs = set()
    submissions = []
    for value in messages:
        msg = _object(value, {"seat", "text", "seq"}, "chat message")
        seat, seq = msg["seat"], msg["seq"]
        if not isinstance(seat, str) or seat not in _PRIVATE_IDS or seat in seen_seats:
            raise ValueError("chat must contain one submission from each seat")
        if type(seq) is not int or seq <= 0 or seq in seen_seqs:
            raise ValueError("invalid or duplicate chat sequence")
        seen_seats.add(seat)
        seen_seqs.add(seq)

        row = _read_json(msg["text"], {"id", "option", "points"}, "chat fact")
        fact_id, fact = _native_fact(row)
        if fact_id not in _SHARED_IDS and fact_id != _PRIVATE_IDS[seat]:
            raise ValueError("chat fact does not belong to the speaker's dossier")
        if fact_id in dossier and fact != dossier[fact_id]:
            raise ValueError("chat fact conflicts with the dossier")
        submissions.append((fact_id, fact))
    return submissions


def _shown_fact(
    fact_id: str, fact: _Fact, options: dict[str, str], aliases: dict[str, str]
) -> dict[str, str | int]:
    return {
        "fact": aliases[fact_id],
        "option": options[fact.option],
        "points": fact.points,
        "provenance": "shared" if fact_id in _SHARED_IDS else "private",
    }


def _dossier_line(
    facts: dict[str, _Fact], options: dict[str, str], aliases: dict[str, str]
) -> str:
    return "Dossier: " + _compact_json(
        {
            "options": _LABELS,
            "facts": [
                _shown_fact(fact_id, facts[fact_id], options, aliases)
                for fact_id in sorted(facts, key=aliases.__getitem__)
            ],
        }
    )


def share_prompt(dossier: dict, map_index: int, arm: str) -> str:
    """Show a seat its dossier and ask which one fact to share."""
    if arm not in ARMS:
        raise ValueError("unknown study arm")
    options, aliases = _mapping(map_index)
    lines = [
        _OBJECTIVE,
        _dossier_line(_dossier_facts(dossier), options, aliases),
        "Select one fact from your dossier to send to the other analysts.",
    ]
    if arm == "novelty":
        lines.append(_NOVELTY_LINE)
    lines.append('Return only a JSON object: {"fact":"<fact alias>"}.')
    return "\n".join(lines)


def share_action(text: str, dossier: dict, map_index: int) -> dict:
    """Decode one dossier fact alias to a native chat POST body."""
    _, aliases = _mapping(map_index)
    facts = _dossier_facts(dossier)
    response = _read_json(text, {"fact"}, "share response")
    selected = response["fact"]
    if not isinstance(selected, str):
        raise ValueError("share fact must be an alias")
    for fact_id, fact in facts.items():
        if selected == aliases[fact_id]:
            return {
                "text": _compact_json(
                    {"id": fact_id, "option": fact.option, "points": fact.points}
                )
            }
    raise ValueError("share fact is not in the dossier")


def vote_prompt(dossier: dict, chat: dict, map_index: int) -> str:
    """Show a seat its dossier and all four blinded public submissions."""
    options, aliases = _mapping(map_index)
    facts = _dossier_facts(dossier)
    submissions = _chat_facts(chat, facts)
    submissions.sort(key=lambda row: aliases[row[0]])
    return "\n".join(
        (
            _OBJECTIVE,
            _dossier_line(facts, options, aliases),
            "Submissions: "
            + _compact_json(
                [
                    _shown_fact(fact_id, fact, options, aliases)
                    for fact_id, fact in submissions
                ]
            ),
            "A submission can repeat a fact from your dossier or another submission. "
            "Count that fact only once.",
            "Vote for an option. Cite one or more distinct fact aliases from the dossier "
            "or the submissions.",
            'Return only a JSON object: {"choice":"<option label>","cited":["<fact alias>"]}.',
        )
    )


def vote_action(text: str, dossier: dict, chat: dict, map_index: int) -> dict:
    """Decode a vote and its visible fact aliases to a native vote POST body."""
    options, aliases = _mapping(map_index)
    facts = _dossier_facts(dossier)
    submissions = _chat_facts(chat, facts)
    response = _read_json(text, {"choice", "cited"}, "vote response")
    choice, cited = response["choice"], response["cited"]
    native_options = {label: option for option, label in options.items()}
    if not isinstance(choice, str) or choice not in native_options:
        raise ValueError("unknown vote option label")
    visible_ids = facts.keys() | {fact_id for fact_id, _ in submissions}
    visible_aliases = {aliases[fact_id]: fact_id for fact_id in visible_ids}
    if not isinstance(cited, list) or not cited or any(
        not isinstance(alias, str) or alias not in visible_aliases for alias in cited
    ):
        raise ValueError("vote must cite visible fact aliases")
    if len(cited) != len(set(cited)):
        raise ValueError("duplicate vote citation")
    return {
        "choice": native_options[choice],
        "cited_facts": [visible_aliases[alias] for alias in cited],
    }
