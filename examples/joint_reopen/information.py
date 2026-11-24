import argparse
import json
import sys
from collections import Counter
from fractions import Fraction
from itertools import combinations, permutations
from typing import Any, Callable

SCHEMA = "harbor.joint-reopen.information/v1"
INCUMBENT = 5
LABELS = tuple(range(5))
MASKS = ("none", "east", "west", "both")
VETO = {
    "none": (False, False),
    "east": (True, False),
    "west": (False, True),
    "both": (True, True),
}
EDGES = tuple(combinations(LABELS, 2))
INDEX = {edge: index for index, edge in enumerate(EDGES)}
TRIPLES = tuple(permutations(LABELS, 3))
RULES = 1 << len(EDGES)
STATES = len(TRIPLES) * len(MASKS)
assert len(EDGES) == 10 and len(TRIPLES) == 60 and RULES == 1024 and STATES == 240

Policy = Callable[[frozenset[int], frozenset[int]], int]

ASSUMPTIONS = (
    "Representative incumbent 5; labels 0-4 form K5 with 10 edges. Every other incumbent is an exact relabeling.",
    "Given the incumbent, (shared, east_only, west_only) is uniform over 60 ordered triples; the 4 masks are uniform and independent: 240 equal-weight states.",
    "A seat sees only its role, its card ({shared, own_only} plus the incumbent unless vetoed) and the public incumbent: no content, timing, seed, key or cross-trial adaptation.",
    "Success: both votes equal the target, which is the incumbent in mask none and the shared label otherwise.",
    "Votes outside the own pair never score in a vetoed mask, so 1024 in-pair rules per seat cover the both-veto maximum.",
    "The pair bound covers shared and private randomness as mixtures of deterministic pairs. The same-rule bound covers one deterministic rule for both seats, drawn with shared randomness only.",
)
NOT_LIVE = "Theoretical per-team calibration only: not a live LLM effect and not a bound on Harbor model runs, where timing and other signals are not removed."


def edge(first: int, second: int) -> tuple[int, int]:
    assert first != second and INCUMBENT not in (first, second)
    return (first, second) if first < second else (second, first)


def vote(rule: int, pair: tuple[int, int]) -> int:
    assert 0 <= rule < RULES
    return pair[(rule >> INDEX[pair]) & 1]


def success_masks(rule: int) -> tuple[int, int]:
    east = west = 0
    for bit, (shared, east_only, west_only) in enumerate(TRIPLES):
        east |= (vote(rule, edge(shared, east_only)) == shared) << bit
        west |= (vote(rule, edge(shared, west_only)) == shared) << bit
    return east, west


def three_mask_cap() -> tuple[int, int, int]:
    degree = Counter(edge(shared, east_only) for shared, east_only, _ in TRIPLES)
    star = Counter(
        (edge(shared, east_only), shared) for shared, east_only, _ in TRIPLES
    )
    assert len(set(degree.values())) == 1 and len(set(star.values())) == 1
    neighbours, matches, size = max(degree.values()), max(star.values()), len(EDGES)
    cap = max(
        min(neighbours * x, neighbours * y)
        + matches * (size - x)
        + matches * (size - y)
        for x in range(size + 1)
        for y in range(size + 1)
    )
    return cap, neighbours, matches


def both_veto() -> tuple[int, tuple[int, int], int, int]:
    masks = [success_masks(rule) for rule in range(RULES)]
    wests = [west for _, west in masks]
    best, pair = -1, (0, 0)
    for east_rule, (east, _) in enumerate(masks):
        for west_rule, west in enumerate(wests):
            count = (east & west).bit_count()
            if count > best:
                best, pair = count, (east_rule, west_rule)
    same = max(
        range(RULES), key=lambda rule: (masks[rule][0] & masks[rule][1]).bit_count()
    )
    same_best = (masks[same][0] & masks[same][1]).bit_count()
    assert 0 <= same_best <= best <= len(TRIPLES)
    return best, pair, same, same_best


def card(pair: tuple[int, int], vetoed: bool) -> frozenset[int]:
    return frozenset(pair) if vetoed else frozenset((*pair, INCUMBENT))


def rule_policy(rule: int) -> Policy:
    def policy(own: frozenset[int], partner: frozenset[int]) -> int:
        assert partner and len(own) in (2, 3)
        return INCUMBENT if INCUMBENT in own else vote(rule, edge(*sorted(own)))

    return policy


def truthful(own: frozenset[int], partner: frozenset[int]) -> int:
    if INCUMBENT in own and INCUMBENT in partner:
        return INCUMBENT
    shared = (own & partner) - {INCUMBENT}
    assert len(shared) == 1
    return min(shared)


def play(east_policy: Policy, west_policy: Policy) -> dict[str, int]:
    counts = dict.fromkeys(MASKS, 0)
    for shared, east_only, west_only in TRIPLES:
        for mask in MASKS:
            east_vetoed, west_vetoed = VETO[mask]
            east = card(edge(shared, east_only), east_vetoed)
            west = card(edge(shared, west_only), west_vetoed)
            target = INCUMBENT if mask == "none" else shared
            counts[mask] += east_policy(east, west) == target == west_policy(west, east)
    return counts


def rate(count: int, total: int) -> dict[str, Any]:
    assert 0 <= count <= total and total > 0
    reduced = Fraction(count, total)
    return {
        "count": count,
        "of": total,
        "fraction": f"{reduced.numerator}/{reduced.denominator}",
    }


def certificate() -> dict[str, Any]:
    cap, neighbours, matches = three_mask_cap()
    best, (east_rule, west_rule), same, same_best = both_veto()
    witness = play(rule_policy(east_rule), rule_policy(west_rule))
    blind = play(rule_policy(same), rule_policy(same))
    content = play(truthful, truthful)
    third = len(TRIPLES)
    assert witness == {"none": third, "east": 0, "west": 0, "both": best}
    assert blind == {"none": third, "east": 0, "west": 0, "both": same_best}
    assert content == dict.fromkeys(MASKS, third)
    assert (
        sum(witness.values()) == cap + best and sum(blind.values()) == cap + same_best
    )
    return {
        "schema": SCHEMA,
        "kind": "exact finite enumeration",
        "assumptions": list(ASSUMPTIONS),
        "not_live_harbor_bound": NOT_LIVE,
        "domain": {
            "edges": len(EDGES),
            "triples": third,
            "masks": len(MASKS),
            "states": STATES,
            "rules": RULES,
            "rule_pairs": RULES * RULES,
        },
        "three_mask_cap": {
            "formula": f"min({neighbours}x,{neighbours}y)+{matches}({len(EDGES)}-x)+{matches}({len(EDGES)}-y)",
            "grid_points": (len(EDGES) + 1) ** 2,
            "cap": rate(cap, 3 * third),
        },
        "both_veto": {
            "pair_max": rate(best, third),
            "same_rule_max": rate(same_best, third),
        },
        "optimum": {
            "rules": [east_rule, west_rule],
            "successes": witness,
            "witness": rate(sum(witness.values()), STATES),
            "upper": rate(cap + best, STATES),
        },
        "same_rule": {
            "rule": same,
            "successes": blind,
            "witness": rate(sum(blind.values()), STATES),
            "upper": rate(cap + same_best, STATES),
        },
        "truthful_card": {
            "successes": content,
            "witness": rate(sum(content.values()), STATES),
        },
    }


def render(result: dict[str, Any]) -> str:
    def line(name: str, record: dict[str, Any]) -> str:
        counts = "/".join(str(record["successes"][mask]) for mask in MASKS)
        bound = (
            f" = upper {record['upper']['count']}/{STATES}" if "upper" in record else ""
        )
        return f"{name}: {counts} -> {record['witness']['count']}/{STATES} ({record['witness']['fraction']}){bound}"

    cap, veto = result["three_mask_cap"], result["both_veto"]
    return "\n".join(
        (
            f"{result['schema']}: {result['kind']}, {RULES * RULES} rule pairs, {STATES} states (masks none/east/west/both)",
            f"three-mask cap {cap['cap']['count']} from {cap['formula']} over {cap['grid_points']} points; both-veto pair max {veto['pair_max']['count']}/60, same-rule max {veto['same_rule_max']['count']}/60",
            line("optimum", result["optimum"]),
            line("same rule", result["same_rule"]),
            line("truthful card", result["truthful_card"]),
            result["not_live_harbor_bound"],
        )
    )


def main(argv: list[str] | None = None) -> int:
    root = argparse.ArgumentParser(
        prog="python3 -BS -m examples.joint_reopen.information"
    )
    root.add_argument("--json", action="store_true")
    args = root.parse_args(argv)
    result = certificate()
    sys.stdout.write((json.dumps(result) if args.json else render(result)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
