import argparse
import json
import re
import sys
from fractions import Fraction
from typing import Any

from .core import BoundaryError, World, analyze
from .inputs import census, read_input
from .pair import partition

SCHEMA = "seatlab.read-budget/v1"
SCOPE = (
    "Finite-state decision value under declared worlds, priors and legal action "
    "rules. The optional reply adds to the prior observation. The hypothetical "
    "price is in normalized physical reward units, not a tool fee. No spending "
    "or agent effects are measured. This report does not establish that the read "
    "is actually offered before irreversible commit."
)
_PRICE = re.compile(r"(?:0|[1-9][0-9]{0,18})(?:/[1-9][0-9]{0,18})?")


def _validate_price(price: Fraction) -> None:
    if type(price) is not Fraction or not 0 <= price <= 1:
        raise BoundaryError("expected exact price between zero and one")


def _parse_price(text: str) -> Fraction:
    if len(text) > 39 or _PRICE.fullmatch(text) is None:
        raise BoundaryError("invalid rational price")
    price = Fraction(text)
    _validate_price(price)
    return price


def _classes(
    before: list[World], replies: list[World]
) -> dict[bytes, tuple[list[World], list[World]]]:
    reply_by_name = {world.name: world for world in replies}
    classes: dict[bytes, tuple[list[World], list[World]]] = {}
    for world in before:
        if world.observation not in classes:
            classes[world.observation] = ([], [])
        prior, additional = classes[world.observation]
        prior.append(world)
        additional.append(reply_by_name[world.name])
    return classes


def _metrics(reward: Fraction, reads: Fraction, price: Fraction) -> dict[str, str]:
    return {
        "reward": str(reward),
        "reads": str(reads),
        "utility": str(reward - price * reads),
    }


def evaluate(
    before: list[World],
    scale: int,
    replies: list[World],
    reply_scale: int,
    price: Fraction,
) -> dict[str, Any]:
    _validate_price(price)
    before_analysis = analyze(before, scale)
    analyze(replies, reply_scale)
    if scale != reply_scale:
        raise BoundaryError("scale mismatch")
    partition(before, replies)

    mass = int(before_analysis["mass"])
    never_reward = Fraction(before_analysis["observed"])
    always_reward = never_reward
    adaptive_reward = never_reward
    adaptive_reads = Fraction(0)
    thresholds: dict[Fraction, Fraction] = {}
    for prior, additional in _classes(before, replies).values():
        prior_analysis = analyze(prior, scale)
        reply_analysis = analyze(additional, scale)
        gain = Fraction(reply_analysis["observed"]) - Fraction(
            prior_analysis["observed"]
        )
        weight = Fraction(int(prior_analysis["mass"]), mass)
        assert 0 <= gain <= 1 and 0 < weight <= 1
        thresholds[gain] = thresholds.get(gain, Fraction(0)) + weight
        always_reward += weight * gain
        if gain > price:
            adaptive_reward += weight * gain
            adaptive_reads += weight

    advantage = (
        adaptive_reward
        - price * adaptive_reads
        - max(never_reward, always_reward - price)
    )
    assert sum(thresholds.values()) == 1
    assert 0 <= never_reward <= adaptive_reward <= always_reward <= 1
    assert 0 <= adaptive_reads <= 1 and advantage >= 0
    return {
        "schema": SCHEMA,
        "scope": SCOPE,
        "price": str(price),
        "never": _metrics(never_reward, Fraction(0), price),
        "always": _metrics(always_reward, Fraction(1), price),
        "adaptive": _metrics(adaptive_reward, adaptive_reads, price),
        "selective_advantage": str(advantage),
        "selective_reading_pattern": advantage > 0,
        "thresholds": [
            {"gain": str(gain), "prior_mass": str(weight)}
            for gain, weight in sorted(thresholds.items())
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the finite value of one optional pre-commit read.",
        allow_abbrev=False,
    )
    parser.add_argument("before")
    parser.add_argument("reply")
    parser.add_argument("--price", required=True)
    parser.add_argument("--expect-selective", choices=("yes", "no"))
    args = parser.parse_args(argv)
    try:
        if args.before == args.reply == "-":
            raise BoundaryError("stdin can supply only one census")
        price = _parse_price(args.price)
        scale, before = census(read_input(args.before))
        reply_scale, replies = census(read_input(args.reply))
        report = evaluate(before, scale, replies, reply_scale, price)
    except BoundaryError as exc:
        print(f"read-budget: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError, AssertionError, TypeError):
        print("read-budget: invalid or inaccessible census", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, allow_nan=False, separators=(",", ":")))
    matches = args.expect_selective is None or report["selective_reading_pattern"] == (
        args.expect_selective == "yes"
    )
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
