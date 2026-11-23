from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import simulator

SCHEMA = "seatlab.private-prices/v1"
MODES = ("baseline", "exact")
ARMS = ("target", "donor")
REGISTERED = {("target", 1), ("target", 7), ("donor", 9), ("donor", 11)}
CHARGES = {"C": 0, "T": 60_000}
RETURN_ALLOWANCE = 0.1
LOW, HIGH = 0.85, 1.15
LINES = 10
MAX_ROW_BYTES = 1 << 14

assert LOW < 1.0 < HIGH and set(CHARGES) == {"C", "T"}


def stored_payoffs(
    data: Path, arm: str, world: int, fingerprint: str
) -> tuple[dict[str, Any], bytes]:
    raw = simulator.read_own(data / f"{arm}-{world:02d}.payoffs.json")
    record = json.loads(raw.decode("utf-8"))
    ok = (
        isinstance(record, dict)
        and record.get("schema") == simulator.PAYOFF_SCHEMA
        and record.get("source") == simulator.PIN
        and record.get("world") == world
        and record.get("prefix_fingerprint") == fingerprint
        and isinstance(record.get("options"), list)
        and all(isinstance(item, dict) for item in record["options"])
        and [item.get("pick") for item in record["options"]]
        == [pick for pick, _ in simulator.CHOICES]
    )
    if not ok:
        raise simulator.SimulatorError(
            f"{arm}-{world:02d}: published payoffs do not match the rebuilt prefix"
        )
    return record["options"][0], raw


def rebuild(
    stock: SimpleNamespace, data: Path, arm: str, world: int
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    checkpoint, receipt = simulator.run_prefix(stock, arm, world)
    encoded = simulator.encode(receipt)
    if simulator.read_own(data / f"{arm}-{world:02d}.prefix.json") != encoded:
        raise simulator.SimulatorError(
            f"{arm}-{world:02d}: rebuilt prefix differs from the published bytes"
        )
    pick_one, payoffs = stored_payoffs(data, arm, world, receipt["prefix_fingerprint"])
    common = {
        "schema": SCHEMA,
        "arm": arm,
        "world": world,
        "source": simulator.PIN,
        "numpy_version": stock.numpy_version,
        "prefix_fingerprint": receipt["prefix_fingerprint"],
        "prefix_sha256": hashlib.sha256(encoded).hexdigest(),
        "payoffs_sha256": hashlib.sha256(payoffs).hexdigest(),
    }
    return checkpoint, receipt, {"common": common, "pick_one": pick_one}


def finish(checkpoint: Any, receipt: dict[str, Any], policy: Any) -> dict[str, Any]:
    if receipt["terminal"]:
        return checkpoint.result()
    return simulator.continue_with(checkpoint, policy)


def baseline(
    stock: SimpleNamespace, data: Path, arm: str, world: int
) -> dict[str, Any]:
    if (arm, world) not in REGISTERED:
        raise simulator.SimulatorError(
            f"{arm}-{world:02d}: not a registered baseline replay"
        )
    checkpoint, receipt, built = rebuild(stock, data, arm, world)
    pick, price = simulator.CHOICES[0]
    assert pick == 1 and price == 1.0
    result = finish(checkpoint, receipt, simulator.continuation_policy(stock, price))
    k = simulator.scores(stock, result)
    if simulator.option(stock, pick, result) != built["pick_one"]:
        raise simulator.SimulatorError(
            f"{arm}-{world:02d}: rebuilt K payoff {k} differs from the published pick 1"
        )
    if simulator.fingerprint(checkpoint) != receipt["prefix_fingerprint"]:
        raise simulator.SimulatorError(
            f"{arm}-{world:02d}: K continuation mutated the prefix"
        )
    return {**built["common"], "mode": "baseline", "k": k}


def active_lines(stock: SimpleNamespace, checkpoint: Any) -> list[str]:
    ranked = stock.policies.public_rank(simulator.PUBLIC_RANK)
    live = sorted(pid for pid, line in checkpoint.lines.items() if line.active)
    if not set(live) <= set(ranked):
        raise simulator.SimulatorError(
            f"active lines {live} fall outside the stock plan {ranked}"
        )
    return live


def cost_term(stock: SimpleNamespace, pid: str) -> float:
    catalog = stock.policies
    typical = catalog.PRODUCTS[pid]["price"]
    size = catalog.fees(pid)
    cost = (
        catalog.landed(pid)
        + size["mp_fulfill"]
        + RETURN_ALLOWANCE * size["return_ship"]
    )
    share = 1.0 - catalog.RULES["mp_referral"] - RETURN_ALLOWANCE
    assert typical > 0 and share > 0 and cost > 0
    return cost / (share * typical)


def demand_term(truth: dict[str, Any], stock: SimpleNamespace, pid: str) -> float:
    beta = float(truth["products"][pid]["beta"])
    typical = stock.policies.PRODUCTS[pid]["price"]
    assert beta > 0 and typical > 0
    return 1.0 / (beta * typical)


def multipliers(terms: dict[str, float]) -> tuple[dict[str, float], int]:
    assert terms
    mean = sum(terms.values()) / len(terms)
    raw = {pid: term - mean + 1.0 for pid, term in terms.items()}
    clipped = sum(1 for value in raw.values() if not LOW <= value <= HIGH)
    return {pid: min(HIGH, max(LOW, value)) for pid, value in raw.items()}, clipped


def priced_policy(stock: SimpleNamespace, prices: dict[str, float]) -> Any:
    base = stock.policies.plain_rules()
    assert list(base.prm["products"]) == stock.policies.public_rank(
        simulator.PUBLIC_RANK
    )
    assert all(
        knobs == simulator.PLAIN_PRODUCT for knobs in base.prm["products"].values()
    )
    assert set(prices) <= set(base.prm["products"])
    params = copy.deepcopy(base.prm)
    for pid, price in prices.items():
        params["products"][pid]["price"] = price
    policy = stock.policies.RulePolicy(params, base.name)
    assert policy.plan == base.plan
    assert {k: v for k, v in params.items() if k != "products"} == simulator.PLAIN_RULES
    for pid, knobs in params["products"].items():
        assert {k: v for k, v in knobs.items() if k != "price"} == {
            k: v for k, v in simulator.PLAIN_PRODUCT.items() if k != "price"
        }
        assert knobs["price"] == prices.get(pid, simulator.PLAIN_PRODUCT["price"])
    return policy


def grade(
    stock: SimpleNamespace, result: dict[str, Any], charge: int
) -> dict[str, float]:
    return {
        "world_score": float(stock.scoring.world_score(result, charge)),
        "multiple": float(stock.scoring.multiple(result["value"], charge)),
    }


def outcome(
    stock: SimpleNamespace,
    result: dict[str, Any],
    charge: int,
    prices: dict[str, float],
    clipped: int,
) -> dict[str, Any]:
    values = list(prices.values())
    day = result["bankrupt_day"]
    return {
        "charge": charge,
        "value": float(result["value"]),
        "bankrupt": bool(result["bankrupt"]),
        "bankrupt_day": None if day is None else int(day),
        "final_day": int(result["days"]),
        "gross": grade(stock, result, 0),
        "charged": grade(stock, result, charge),
        "multipliers": {
            "n": len(values),
            "mean": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
            "clipped": clipped,
            "sha256": simulator.digest(prices),
        },
    }


def exact(stock: SimpleNamespace, data: Path, arm: str, world: int) -> dict[str, Any]:
    checkpoint, receipt, built = rebuild(stock, data, arm, world)
    if receipt["terminal"]:
        lines = sorted(stock.policies.public_rank(simulator.PUBLIC_RANK))
    else:
        lines = active_lines(stock, checkpoint)
    if len(lines) != LINES:
        raise simulator.SimulatorError(
            f"{arm}-{world:02d}: {len(lines)} active lines, expected {LINES}"
        )
    truth = stock.truth.run_truth(simulator.stock_seed(arm, world, "truth"))
    costs = {pid: cost_term(stock, pid) for pid in lines}
    rules = {
        "C": costs,
        "T": {pid: costs[pid] + demand_term(truth, stock, pid) for pid in lines},
    }
    policies = {}
    for name, terms in rules.items():
        prices, clipped = multipliers(terms)
        result = finish(checkpoint, receipt, priced_policy(stock, prices))
        policies[name] = outcome(stock, result, CHARGES[name], prices, clipped)
        if simulator.fingerprint(checkpoint) != receipt["prefix_fingerprint"]:
            raise simulator.SimulatorError(
                f"{arm}-{world:02d}: {name} continuation mutated the prefix"
            )
    return {**built["common"], "mode": "exact", "policies": policies}


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -B -m examples.seatlab.market_handoff.private_prices"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("data", type=Path)
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("arm", choices=ARMS)
    parser.add_argument("world", type=int, choices=range(simulator.WORLDS))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    try:
        stock = simulator.load_stock(args.source.resolve(strict=True))
        run = baseline if args.mode == "baseline" else exact
        row = simulator.canonical(run(stock, args.data, args.arm, args.world))
    except (simulator.SimulatorError, OSError, UnicodeDecodeError, ValueError) as error:
        raise SystemExit(f"market_handoff.private_prices: {error}") from error
    if len(row.encode("utf-8")) > MAX_ROW_BYTES:
        raise SystemExit(
            f"market_handoff.private_prices: row exceeds {MAX_ROW_BYTES} bytes"
        )
    print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
