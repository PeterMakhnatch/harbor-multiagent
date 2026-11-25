import sys
from fractions import Fraction
from importlib import import_module
from itertools import product
from pathlib import Path
from typing import Any

from ...native_study import require
from ..decision_boundary.core import World, analyze
from ..decision_boundary.pair import compare
from ..exhibit_wire import encode_request, render_turn
from . import policies, worlds

_TASK = "examples.tasks.exhibit-relay.environment.exhibit_relay"
_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    make_case = import_module(f"{_TASK}.cases").make_case
    Session = import_module(f"{_TASK}.engine").Session
    grade = import_module(f"{_TASK}.grading").grade
    diagnostics = import_module(f"{_TASK}.diagnostics")
    observation = diagnostics.observation
    case_commitment = diagnostics.case_commitment
finally:
    sys.dont_write_bytecode = _bytecode
_INSTRUCTION = (
    Path(__file__).resolve().parents[2] / "tasks/exhibit-relay/instructions/loader.md"
)
_ROLES = ("loader", "registrar", "depot")
_MAPS = ("original", "swapped")
_POLICIES = ("other", "relevant")


def native_pair(seed: int) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    require(type(seed) is int and 0 <= seed < 2**63, "invalid fixed seed")
    base = make_case(seed, "fork", layout="draw")
    return base, {name: worlds.select(base, name) for name in _MAPS}


def _candidate(start: int) -> tuple[int, int, dict[str, Any], dict[str, Any]]:
    if type(start) is not int or not 0 <= start < 2**63:
        raise ValueError("invalid development seed")
    for seed in range(start, min(start + 128, 2**63)):
        case = make_case(seed, "fork", layout="draw")
        focal = min(case["briefs"], key=lambda brief: brief["id"])
        if case["realized"] == focal["id"]:
            return seed, seed - start + 1, case, focal
    raise ValueError("no naturally focal draw within bounded development seed search")


def _prefix(
    case: dict[str, Any], policy: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session = Session(case, "linked")
    initial = policies.nominate(session.view("loader"))
    session.apply("loader", initial)
    registration = policies.registrar(session.view("registrar"))
    session.apply("registrar", registration)
    depot_view = session.view("depot")
    filing = policies.depot(depot_view, policy)
    session.apply("depot", filing)
    view = session.view("loader")
    require(view["phase"] == "dispatch", "native dispatch phase differs")
    require(len(initial["slips"]) == 1, "initial nomination is not focal")
    require(
        len(registration["ids"]) == len(filing["ids"]) == 2,
        "upstream filing sizes differ",
    )
    require(len(depot_view["agenda"]) == 2, "depot did not receive two agenda entries")
    epoch = view["order"]["epoch"]
    agenda = {entry["key"] for entry in depot_view["agenda"]}
    require(
        all(entry["epoch"] == epoch for entry in depot_view["agenda"]),
        "agenda epoch differs from target",
    )
    expected = (
        sorted(agenda)
        if policy == "relevant"
        else sorted(set(view["crates"]) - agenda)[:2]
    )
    require(
        sorted(card["key"] for card in view["depot_file"]) == expected,
        "filed keys differ from declared policy",
    )
    require(
        all(card["start"] <= epoch <= card["end"] for card in view["depot_file"]),
        "depot filed an inactive record",
    )
    return [initial, registration, filing], view


def _commit(
    case: dict[str, Any], prefix: list[dict[str, Any]], action: dict[str, Any]
) -> dict[str, Any]:
    session = Session(case, "linked")
    for role, previous in zip(_ROLES, prefix, strict=True):
        session.apply(role, previous)
    session.apply("loader", action)
    return session.snapshot()


def _enumerate(
    case: dict[str, Any], policy: str, name: str, instruction: str
) -> tuple[World, dict[str, Any], list[dict[str, Any]]]:
    prefix, view = _prefix(case, policy)
    items = sorted(view["order"]["items"])
    bays = sorted(view["bays"])
    require(
        len(items) == 2 and len(bays) == 8, "expected the full 8-by-8 dispatch menu"
    )
    require(
        prefix[0]["slips"] == [min(brief["id"] for brief in view["briefs"])],
        "loader prior action differs from its public rule",
    )
    packet = encode_request(render_turn(instruction, view, prefix[0]))
    followed = policies.follow(view)
    scores: dict[str, int] = {}
    follow_reward = None
    expected_support = 2 if policy == "relevant" else 0
    for left, right in product(range(8), repeat=2):
        label = f"{left}:{right}"
        action = {
            "op": "commit",
            "routes": dict(zip(items, (bays[left], bays[right]), strict=True)),
        }
        cut = _commit(case, prefix, action)
        result = grade(cut)
        require(result["status"] == "complete", "native verifier did not complete")
        reward = result["reward"]
        require(
            type(reward) is int and reward in (0, 1), "invalid native physical reward"
        )
        require(result["registrar_supported"] == 2, "registrar support changed")
        require(
            result["depot_supported"] == result["chains_supported"] == expected_support,
            "depot support differs from fixed policy",
        )
        require(
            reward == int(result["correct_items"] == 2),
            "native route count and reward disagree",
        )
        scores[label] = reward
        if followed == action:
            follow_reward = reward
        if reward == 1:
            diagnostic = observation(cut)
            require(
                diagnostic["reward"] == 1 and diagnostic["correct_items"] == 2,
                "native diagnostics disagree on reward",
            )
            require(
                diagnostic["dispatch_supported"] == expected_support,
                "native diagnostics disagree on support",
            )
            require(
                diagnostic["handoff"]["stage"] == "done",
                "native diagnostics lack terminal closure",
            )
    require(
        len(scores) == 64 and sum(scores.values()) == 1,
        "native dispatch does not have exactly one winner",
    )
    require(
        follow_reward == (1 if policy == "relevant" else None),
        "view-only chain following differs",
    )
    return (
        World(name, 1, packet, scores),
        {
            "legal_actions": len(scores),
            "successful_actions": sum(scores.values()),
            "follow_reward": follow_reward,
            "chains_supported": expected_support,
        },
        prefix,
    )


def certify(start: int) -> dict[str, Any]:
    seed, checked, base, focal = _candidate(start)
    instruction = _INSTRUCTION.read_text(encoding="utf-8")
    selected = {name: worlds.select(base, name) for name in _MAPS}
    first_inputs = []
    for case in selected.values():
        require(
            {key: value for key, value in case.items() if key != "depot"}
            == {key: value for key, value in base.items() if key != "depot"},
            "private map changed a common native case field",
        )
        initial_view = Session(case, "linked").view("loader")
        first_inputs.append(
            encode_request(render_turn(instruction, initial_view, None))
        )
    require(
        first_inputs[0] == first_inputs[1], "initial loader inputs differ across maps"
    )
    arms: dict[str, dict[str, Any]] = {}
    matrices: dict[str, list[World]] = {}
    native_commits = 0
    for policy in _POLICIES:
        rows: list[World] = []
        counts = {}
        prefixes = []
        for name in _MAPS:
            case = selected[name]
            require(
                case["briefs"] == base["briefs"]
                and case["realized"] == base["realized"],
                "public case or lottery changed",
            )
            row, count, prefix = _enumerate(case, policy, name, instruction)
            rows.append(row)
            counts[name] = count
            prefixes.append(prefix)
            native_commits += count["legal_actions"]
        require(
            prefixes[0] == prefixes[1],
            "pre-decision action IDs disclose the hidden map",
        )
        require(
            not any(
                rows[0].scores[key] and rows[1].scores[key] for key in rows[0].scores
            ),
            "physical optima are not disjoint",
        )
        matrices[policy] = rows
        arms[policy] = {"analysis": analyze(rows, 1), "counts": counts}
    comparison = compare(matrices["other"], 1, matrices["relevant"], 1)
    require(
        comparison["relation"] == "refinement",
        "comparison is not an observation refinement",
    )
    require(
        comparison["before_observed"] == "1/2" and comparison["after_observed"] == "1",
        "native finite physical optima differ from control",
    )
    require(
        comparison["before_observations"] == 1
        and comparison["after_observations"] == 2,
        "physical request classes differ from control",
    )
    require(native_commits == 256, "incomplete dispatch enumeration")
    sizes = {len(row.observation) for rows in matrices.values() for row in rows}
    require(len(sizes) == 1, "native final loader input sizes differ")
    total = sum(brief["weight"] for brief in base["briefs"])
    return {
        "schema": "seatlab.physical-dispatch.certificate/v1",
        "objective": "both_native_dispatch_destinations_correct",
        "initial_loader_inputs_identical": True,
        "final_owner_request_bytes": sizes.pop(),
        "case_commitments": {
            name: case_commitment(case) for name, case in selected.items()
        },
        "model_calls": 0,
        "harbor_trials": 0,
        "development_seed": seed,
        "candidates_checked": checked,
        "focal_weight": focal["weight"],
        "total_weight": total,
        "focal_probability": str(Fraction(focal["weight"], total)),
        "scope": "Conditional development certificate only: stock fork draw, naturally realized public focal brief, two equally weighted bay maps; not an unconditional lottery or live evaluation result.",
        "native_commits": native_commits,
        "comparison": comparison,
        "arms": arms,
    }
