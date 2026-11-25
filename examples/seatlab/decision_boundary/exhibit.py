import json
from importlib import import_module
from itertools import combinations

from examples.seatlab import exhibit_authored as authored
from examples.seatlab.exhibit_options import _true_requirements, _true_score

from .core import World

POLICIES = (("faithful", "true"), ("heavy-only", "segmented"), ("empty", "empty"))
ARMS = (("shown", "authored"), ("masked", "author-masked"))


def studies(seed: int) -> dict[str, tuple[int, list[World]]]:
    authored.require(type(seed) is int and 0 <= seed < 2**63, "invalid seed")
    instruction = authored.INSTRUCTION.read_text(encoding="utf-8")
    make_case = import_module(f"{authored.WORLD}.cases").make_case
    result: dict[str, tuple[int, list[World]]] = {
        f"exhibit/{policy}/{arm}": (10, []) for policy, _ in POLICIES for arm, _ in ARMS
    }
    depot_base = None
    for layout in authored.LAYOUTS:
        case = make_case(seed, "coupling", layout=layout)
        baseline = authored.prefix(case, "authored", "true")
        ids = sorted(card["id"] for card in baseline["depot"]["cards"])
        authored.require(
            8 <= len(ids) <= 24 and len(ids) == len(set(ids)), "invalid archive"
        )
        authored.require(baseline["depot"]["quota"] == 2, "unexpected quota")
        authored.require(
            set(baseline["request"]["slips"])
            == {brief["id"] for brief in case["briefs"]}
            and len(case["briefs"]) == len(baseline["truth"]) == 4,
            "incomplete lottery prefix",
        )
        truth = _true_requirements(case, set(baseline["file_ids"]))
        authored.require(
            sum(weight for weight, _ in truth) == 10
            and all(len(items) == 2 and None not in items for _, items in truth),
            "incomplete registrar source coverage",
        )
        scores = {
            json.dumps(selection, separators=(",", ":")): _true_score(
                frozenset(selection), truth
            )[0]
            for size in range(3)
            for selection in combinations(ids, size)
        }
        if depot_base is None:
            depot_base = baseline["depot_base"]
        authored.require(
            depot_base == baseline["depot_base"], "twin depot base differs"
        )
        for policy, kind in POLICIES:
            shown, masked = (authored.prefix(case, mode, kind) for _, mode in ARMS)
            authored.compare(shown, masked, kind, instruction)
            for arm, prefix in zip(("shown", "masked"), (shown, masked), strict=True):
                authored.require(
                    all(
                        prefix[field] == baseline[field]
                        for field in (
                            "loader",
                            "request",
                            "registrar",
                            "file_ids",
                            "truth",
                            "depot_base",
                        )
                    ),
                    "authoring policy changed fixed prefix",
                )
                observation = authored.packet(instruction, prefix["depot"])
                result[f"exhibit/{policy}/{arm}"][1].append(
                    World(layout, 1, observation, scores)
                )
    for policy, _ in POLICIES:
        shown = result[f"exhibit/{policy}/shown"][1]
        masked = result[f"exhibit/{policy}/masked"][1]
        authored.require(
            shown[0].scores.keys() == shown[1].scores.keys()
            and all(a.scores == b.scores for a, b in zip(shown, masked, strict=True)),
            "arm or twin action space mismatch",
        )
    return result
