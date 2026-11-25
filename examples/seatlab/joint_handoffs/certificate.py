from fractions import Fraction
from itertools import product
from typing import Any

from ...native_study import require
from ..decision_boundary.core import World
from ..decision_boundary.joint import qualify
from ..exhibit_wire import encode_request, render_turn
from ..physical_dispatch.certificate import Session, _candidate, _enumerate, cli_view
from . import stage, world
from .filing_choice import selection_check
from .packet_policy import policy_values

_ROLES = ("loader", "registrar", "depot")
_PRIVATE = ("registrar", "depot")
Map = tuple[str, str]
Record = dict[str, Any]
History = list[tuple[str, Record]]
Packets = dict[str, dict[Map, bytes]]
_WORLDS = tuple((registry, bay) for registry, bay in product(world.MAPS, world.MAPS))
_ORIGINAL = ("original", "original")
_SHOWN = {
    "neither": (False, False),
    "registrar": (True, False),
    "depot": (False, True),
    "both": (True, True),
}
_ARMS = {"neither": "neither", "first": "registrar", "second": "depot", "both": "both"}
_EXPECTED = {
    "values": {"neither": "1/2", "first": "1/2", "second": "1/2", "both": "1"},
    "conditional_gains": {"first_given_second": "1/2", "second_given_first": "1/2"},
    "interaction": "1/2",
    "observations": {"neither": 1, "registrar": 2, "depot": 2, "both": 4},
}


def _name(key: tuple[str, str]) -> str:
    return f"registry-{key[0]}/bay-{key[1]}"


def _public(case: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in case.items() if key not in _PRIVATE}


def _select(base: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    selected = {key: world.select(base, *key) for key in _WORLDS}
    for (registry, bay), case in selected.items():
        require(_public(case) == _public(base), "map changed the public case")
        require(
            case["registrar"] == selected[(registry, "original")]["registrar"],
            "bay bit changed registrar records",
        )
        require(
            case["depot"] == selected[("original", bay)]["depot"],
            "registry bit changed depot records",
        )
    return selected


def _walk(
    case: dict[str, Any], prefix: list[dict[str, Any]]
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    session = Session(case, "linked")
    seen = []
    for role, action in zip(_ROLES, prefix, strict=True):
        seen.append((role, cli_view(session.view(role))))
        seen.append((role, cli_view(session.apply(role, action))))
    return seen, cli_view(session.view("loader"))


def _native(
    selected: dict[tuple[str, str], dict[str, Any]], instruction: str
) -> tuple[dict[Map, World], dict[Map, History], dict[Map, Record], list[Record], int]:
    rows, seen, finals, prefixes, joined = {}, {}, {}, [], 0
    for key, case in selected.items():
        row, count, prefix = _enumerate(case, "relevant", _name(key), instruction)
        rows[key] = row
        prefixes.append(prefix)
        seen[key], finals[key] = _walk(case, prefix)
        joined += count["follow_reward"]
    require(all(prefix == prefixes[0] for prefix in prefixes), "actions differ by map")
    return rows, seen, finals, prefixes[0], joined


def _isolate(seen: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]]) -> None:
    for (registry, bay), rows in seen.items():
        peers = {
            "loader": _ORIGINAL,
            "registrar": (registry, "original"),
            "depot": ("original", bay),
        }
        for index, (role, view) in enumerate(rows):
            require(
                seen[peers[role]][index] == (role, view),
                f"{role} view discloses another bit",
            )
    agendas = [
        view["agenda"]
        for rows in seen.values()
        for role, view in rows
        if role == "depot" and view["phase"] == "depot"
    ]
    require(len(agendas) == 4, "depot agenda views missing")
    require(all(agenda == agendas[0] for agenda in agendas), "depot agenda varies")


def _winners(rows: dict[tuple[str, str], World]) -> None:
    winners = {
        key: next(label for label, score in row.scores.items() if score == 1)
        for key, row in rows.items()
    }
    same, crossed = winners[_ORIGINAL], winners[("swapped", "original")]
    require(winners[("swapped", "swapped")] == same, "joint swap does not cancel")
    require(winners[("original", "swapped")] == crossed, "single swaps disagree")
    require(same != crossed, "a single swap does not move the winner")


def _initial(seen: dict[Map, History], instruction: str) -> int:
    views = [rows[0][1] for rows in seen.values()]
    packets = {
        encode_request(
            render_turn(instruction, cli_view(world.delivery(view, c)), None)
        )
        for view, c in product(views, world.CHANNELS)
    }
    require(len(packets) == 1, "initial loader packets differ")
    return len(packets.pop())


def _packets(
    finals: dict[Map, Record],
    rows: dict[Map, World],
    prefix: list[Record],
    instruction: str,
) -> Packets:
    packets: Packets = {channel: {} for channel in world.CHANNELS}
    for channel, key in product(world.CHANNELS, _WORLDS):
        delivered = cli_view(world.delivery(finals[key], channel))
        packet = encode_request(render_turn(instruction, delivered, prefix[0]))
        packets[channel][key] = packet
    require(
        all(packets["both"][key] == rows[key].observation for key in _WORLDS),
        "joined request differs from the graded native view",
    )
    sizes = {len(p) for channel in packets.values() for p in channel.values()}
    require(len(sizes) == 1, "presented final request byte lengths differ")
    for channel, (registry, bay) in _SHOWN.items():
        for left, right in product(_WORLDS, repeat=2):
            same = (not registry or left[0] == right[0]) and (
                not bay or left[1] == right[1]
            )
            require(
                (packets[channel][left] == packets[channel][right]) == same,
                f"{channel} classes differ",
            )
    return packets


def _tables(packets: Packets, rows: dict[Map, World]) -> dict[str, list[World]]:
    return {
        arm: [
            World(_name(key), 1, packets[channel][key], rows[key].scores)
            for key in _WORLDS
        ]
        for arm, channel in _ARMS.items()
    }


def _qualify(arms: dict[str, list[World]]) -> Record:
    result = qualify(arms, 1)
    require(result["values"] == _EXPECTED["values"], "joint values differ")
    require(
        result["conditional_gains"] == _EXPECTED["conditional_gains"],
        "leave-one-out costs differ",
    )
    require(result["interaction"] == _EXPECTED["interaction"], "interaction differs")
    require(
        {channel: result["arms"][arm]["observations"] for arm, channel in _ARMS.items()}
        == _EXPECTED["observations"],
        "observation classes differ",
    )
    return result


def _construct(start: int) -> tuple[Record, dict[str, list[World]]]:
    seed, checked, base, focal = _candidate(start)
    _, files = stage.expected()
    instruction = files["instructions/loader.md"][0].decode("utf-8")
    selected = _select(base)
    rows, seen, finals, prefix, joined = _native(selected, instruction)
    _isolate(seen)
    _winners(rows)
    packets = _packets(finals, rows, prefix, instruction)
    arms = _tables(packets, rows)
    qualification = _qualify(arms)
    native_commits = sum(len(row.scores) for row in rows.values())
    require(native_commits == 256 and joined == 4, "incomplete native enumeration")
    total = sum(brief["weight"] for brief in base["briefs"])
    return {
        "schema": "seatlab.joint-handoffs.certificate/v1",
        "objective": "both_native_dispatch_destinations_correct",
        "development_seed": seed,
        "candidates_checked": checked,
        "rejected_candidates": checked - 1,
        "model_calls": 0,
        "harbor_trials": 0,
        "worlds": len(_WORLDS),
        "world_mass": 1,
        "channels": list(world.CHANNELS),
        "method_arms": dict(_ARMS),
        "initial_loader_inputs_identical": True,
        "initial_request_bytes": _initial(seen, instruction),
        "pre_decision_actions": len(prefix),
        "pre_decision_actions_identical": True,
        "final_requests": sum(len(channel) for channel in packets.values()),
        "final_request_bytes": len(packets["both"][_ORIGINAL]),
        "native_commits": native_commits,
        "legal_actions_per_map": 64,
        "winning_actions_per_map": 1,
        "joined_rule_wins": joined,
        "classes": {
            channel: len(set(packets[channel].values())) for channel in world.CHANNELS
        },
        "focal_probability": str(Fraction(focal["weight"], total)),
        "scope": "Conditional development certificate only: stock fork draw, naturally realized public focal brief, four equally weighted registry/bay maps, fixed upstream filings; not an unconditional lottery or live evaluation result.",
        "value_basis": "Optimal decisions with knowledge of this four-map distribution and payoff table. The participant does not receive the table.",
        "selection_check": selection_check(selected, instruction),
        "packet_policy": policy_values(finals, rows),
        "qualification": qualification,
    }, arms


def certify(start: int) -> Record:
    report, _ = _construct(start)
    return report


def author_tables(start: int) -> tuple[int, dict[str, list[World]]]:
    report, arms = _construct(start)
    return report["development_seed"], arms
