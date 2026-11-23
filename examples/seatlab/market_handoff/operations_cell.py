from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import simulator
from .simulator import SimulatorError

SCHEMA = "seatlab.market-operations/v1"
NAMESPACE = "seatlab/market-roles/v2"
SPLITS = ("calibrate", "holdout")
NUMPY_VERSION = "2.1.3"
ADS = (("usual", 30.0), ("off", 0.0), ("push", 120.0))
STOCK = (("usual", 30, 60), ("lean", 15, 30), ("deep", 60, 120))
OPTIONS = tuple(
    (ads, sponsored, level, cover, batch)
    for ads, sponsored in ADS
    for level, cover, batch in STOCK
)

assert len(OPTIONS) == len(ADS) * len(STOCK) == 9
assert ADS[0][1] == simulator.PLAIN_PRODUCT["sponsored"]
assert STOCK[0][1:] == (
    simulator.PLAIN_RULES["cover"],
    simulator.PLAIN_RULES["batch"],
)


def stock_seed(split: str, world: int, purpose: str) -> str:
    assert (
        split in SPLITS
        and type(world) is int
        and 0 <= world < simulator.WORLDS
        and purpose in ("world", "truth")
    )
    return hashlib.sha256(
        f"{NAMESPACE}/{split}/{world}/{purpose}".encode()
    ).hexdigest()


def filename(split: str, world: int) -> str:
    assert split in SPLITS and type(world) is int and 0 <= world < simulator.WORLDS
    return f"{split}-{world:02d}.operations.json"


def run_cell(stock: SimpleNamespace, split: str, world: int) -> dict[str, Any]:
    if stock.numpy_version != NUMPY_VERSION:
        raise SimulatorError(
            f"NumPy {stock.numpy_version} differs from registered {NUMPY_VERSION}"
        )
    checkpoint = simulator.start_market(
        stock,
        stock_seed(split, world, "world"),
        stock_seed(split, world, "truth"),
        simulator.SHAPES[world],
    )
    simulator.advance_prefix(checkpoint, None)
    pristine = simulator.fingerprint(checkpoint)
    terminal = bool(checkpoint.ended)
    options = []
    for ads, sponsored, level, cover, batch in OPTIONS:
        if terminal:
            result = checkpoint.result()
        else:
            policy = simulator.stock_policy(
                stock, {"sponsored": sponsored}, {"cover": cover, "batch": batch}
            )
            result = simulator.continue_with(checkpoint, policy)
        options.append({"ads": ads, "stock": level, **simulator.scores(stock, result)})
    assert simulator.fingerprint(checkpoint) == pristine
    assert [(item["ads"], item["stock"]) for item in options] == [
        (ads, level) for ads, _, level, _, _ in OPTIONS
    ]
    return {
        "schema": SCHEMA,
        "split": split,
        "world": world,
        "shape": simulator.SHAPES[world],
        "source": simulator.PIN,
        "numpy_version": stock.numpy_version,
        "completed_days": checkpoint.next_day,
        "terminal": terminal,
        "prefix_fingerprint": pristine,
        "options": options,
    }


def require_one_split(output: Path, split: str) -> None:
    for other in SPLITS:
        if other != split and next(output.glob(f"{other}-*.operations.json"), None):
            raise SimulatorError(
                f"{split} cells must not share an output root with {other} cells"
            )


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.market_handoff.operations_cell"
    )
    parser.add_argument("upstream_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--split", required=True, choices=SPLITS)
    parser.add_argument(
        "--world", required=True, type=int, choices=range(simulator.WORLDS)
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    root, output = simulator.outside_upstream(args.upstream_root, args.output)
    name = filename(args.split, args.world)
    try:
        stock = simulator.load_stock(root)
        output.mkdir(parents=True, exist_ok=True)
        require_one_split(output, args.split)
        data = simulator.encode(run_cell(stock, args.split, args.world))
        status = simulator.write_once(output / name, data)
    except SimulatorError as error:
        raise SystemExit(f"market_handoff.operations_cell: {error}") from error
    print(
        simulator.canonical(
            {
                "split": args.split,
                "world": args.world,
                "file": name,
                "status": status,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
