from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .observations import SalesWindow

PIN = "dbec43ff757c86c17077e61b37c7f584c880bdee"
PACKAGE = "tasks/seed-to-profit/task/tests/hidden/marketsim/marketsim"
SOURCE_SHA256 = {
    "__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "catalog.py": "42ca148286261e04f1ce3e72fc9dee3628917ca4ecd9c1e6a2d01c8cc363dcea",
    "countries.py": "92585e8a4f7b9249a75346fc359a42b60508740eb3f0acce34f23cd8969228f6",
    "live.py": "4253328d5e0a384a692b3110ed2b56aeedfa0259579352ce412a2dc94def29cd",
    "office.py": "f12c866eb3214e3941e87c6cc7791d58feebb819a245beae463a177e469acc88",
    "ops_runner.py": "91cbae9f4b7fb796455d463e6919a27f868b95ded1b5a481359d5d67b71bffe0",
    "plan.py": "29ad41502f3c77b5cca419bb8d8223c5406a2bcb4fa0b2adecf4a4f18b4019dc",
    "policies.py": "060ae4ad21e62dcea021cd5a7150c45a98c7460a79d1089b3d6257f3a183abf9",
    "rivals.py": "32415d7c7d65275051deaf9074fcf65dcd999d80a709e82b6f76c99a674dee51",
    "runner.py": "031d659748998897b69c3a24113ee3e51dbe675795198fe1751ee825815c445d",
    "scoring.py": "d1222e397054459a7d7bd1da53305c857262cdf172e4a820e45155143a52a696",
    "sim.py": "df371acafc32d0c309f5925e6eb88bc7441690feb6f41b121dd897b26460f4c2",
    "tricks.py": "888eb4e78995e959364d2cd7853ce7cee7225b6aebea3ca119ec7ab0272fab64",
    "truth.py": "dde46d41ff1034b878f7384b1415dd514f0bb5e198349accd82bfd93512547f1",
    "worlds.py": "b6e4a0377052ab177c85222af392ca0022e13ec8321230fc805d21cee34d3881",
}
NAMESPACE = "seatlab/market-handoff/v1"
PREFIX_SCHEMA = "seatlab.market-prefix/v1"
PAYOFF_SCHEMA = "seatlab.market-payoffs/v1"
ARMS = ("target", "donor", "smoke")
STAGES = ("prefix", "finish")
WORLDS = 12
SHAPES = ("mnl", "nested", "wom") * 3 + ("delayed_ads", "deal_seekers", "delayed_ads")
PREFIX_DAYS = 90
NATIVE_DAYS = 1095
PUBLIC_RANK = 10
CHOICES = ((1, 1.0), (2, 1.15), (3, 0.85))
RESEARCH_SPENT = 0.0
MAX_FILE_BYTES = 1 << 22
GIT_TIMEOUT_SECONDS = 60

assert len(SHAPES) == WORLDS and NATIVE_DAYS - PREFIX_DAYS == 1005


class SimulatorError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def encode(record: dict[str, Any]) -> bytes:
    return (canonical(record) + "\n").encode("utf-8")


def stock_seed(arm: str, world: int, kind: str) -> str:
    assert (
        arm in ARMS
        and type(world) is int
        and 0 <= world < WORLDS
        and kind in ("world", "truth")
    )
    return hashlib.sha256(
        f"{NAMESPACE}/{arm}/{world}/{kind}".encode()
    ).hexdigest()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )


def verify_source(root: Path) -> Path:
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head.returncode != 0 or head.stdout.strip() != PIN:
        raise SimulatorError(
            f"{root}: HEAD is {head.stdout.strip() or head.stderr.strip()!r}, not pinned {PIN}"
        )
    if (
        _git(root, "diff", "--quiet", "--no-ext-diff", PIN, "--", PACKAGE).returncode
        != 0
    ):
        raise SimulatorError(f"{root}: local edits to {PACKAGE} against {PIN}")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", PACKAGE)
    if untracked.returncode != 0 or untracked.stdout.strip():
        raise SimulatorError(
            f"{root}: untracked files in {PACKAGE}: {untracked.stdout.split()}"
        )
    package = (root / PACKAGE).resolve(strict=True)
    names = sorted(
        entry.name for entry in package.iterdir() if entry.name != "__pycache__"
    )
    if names != sorted(SOURCE_SHA256):
        raise SimulatorError(
            f"{package}: files {names} differ from the pinned stock package"
        )
    for name, want in SOURCE_SHA256.items():
        path = package / name
        if (
            path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != want
        ):
            raise SimulatorError(f"{path}: content differs from pinned {PIN}")
    return package


def load_stock(root: Path) -> SimpleNamespace:
    package = verify_source(root)
    if any(
        name == "marketsim" or name.startswith("marketsim.") for name in sys.modules
    ):
        raise SimulatorError("a marketsim package is already imported")
    import numpy

    sys.dont_write_bytecode = True
    prefix, sys.pycache_prefix = (
        sys.pycache_prefix,
        tempfile.mkdtemp(prefix="market-handoff-nopyc-"),
    )
    sys.path.insert(0, str(package.parent))
    try:
        from marketsim import policies, scoring, sim, truth, worlds
    finally:
        sys.path.remove(str(package.parent))
        os.rmdir(sys.pycache_prefix)
        sys.pycache_prefix = prefix
    for name, module in list(sys.modules.items()):
        if name == "marketsim" or name.startswith("marketsim."):
            if module.__file__ is None:
                raise SimulatorError(f"{name} has no source file")
            path = Path(module.__file__).resolve()
            if path.parent != package or path.name not in SOURCE_SHA256:
                raise SimulatorError(
                    f"{name} imported from {path}, outside the pinned package"
                )
    if truth.np is not numpy or verify_source(root) != package:
        raise SimulatorError("stock imports changed while loading")
    return SimpleNamespace(
        policies=policies,
        scoring=scoring,
        sim=sim,
        truth=truth,
        worlds=worlds,
        numpy_version=str(numpy.__version__),
    )


def _require_running(market: Any) -> None:
    if market.software_frozen:
        last = market.notes[-1] if market.notes else {}
        raise SimulatorError(
            f"stock RulePolicy failed on day {market.day}: {last.get('note', last)}"
        )


def fingerprint(market: Any) -> str:
    return digest(
        {
            "next_day": market.next_day,
            "day": market.day,
            "ended": market.ended,
            "result": market.result(),
            "observe": market.observe(),
        }
    )


def run_prefix(
    stock: SimpleNamespace, arm: str, world: int
) -> tuple[Any, dict[str, Any]]:
    policy = stock.policies.plain_rules()
    market = stock.sim.Sim(
        policy.plan,
        stock.truth.run_truth(stock_seed(arm, world, "truth")),
        stock.worlds.make_world(stock_seed(arm, world, "world"), SHAPES[world]),
        policy,
    )
    assert market.days == NATIVE_DAYS and market.next_day == 0
    window = SalesWindow()
    market.start()
    _require_running(market)
    for _ in range(PREFIX_DAYS):
        before = market.next_day
        alive = market.advance(1)
        _require_running(market)
        assert market.next_day in (before, before + 1)
        if market.next_day > before:
            window.add(market.observe())
        if not alive:
            break
    market.stop_software()
    assert market.ended or market.next_day == PREFIX_DAYS
    views = window.finish()
    assert views.keys() == {"sales", "finance"}
    assert isinstance(views["sales"], dict) and isinstance(views["finance"], dict)
    receipt = {
        "schema": PREFIX_SCHEMA,
        "arm": arm,
        "world": world,
        "shape": SHAPES[world],
        "completed_days": market.next_day,
        "terminal": bool(market.ended),
        "sales": views["sales"],
        "finance": views["finance"],
        "source": PIN,
        "numpy_version": stock.numpy_version,
        "observation_sha256": digest(views),
        "prefix_fingerprint": fingerprint(market),
    }
    return market, receipt


def continuation_policy(stock: SimpleNamespace, price: float) -> Any:
    base = stock.policies.plain_rules()
    assert list(base.prm["products"]) == stock.policies.public_rank(PUBLIC_RANK)
    assert all(knobs["price"] == 1.0 for knobs in base.prm["products"].values())
    params = copy.deepcopy(base.prm)
    for knobs in params["products"].values():
        knobs["price"] = price
    policy = stock.policies.RulePolicy(params, base.name)
    assert policy.plan == base.plan
    assert {key: value for key, value in params.items() if key != "products"} == {
        key: value for key, value in base.prm.items() if key != "products"
    }
    return policy


def option(stock: SimpleNamespace, pick: int, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "pick": pick,
        "world_score": float(stock.scoring.world_score(result, RESEARCH_SPENT)),
        "multiple": float(stock.scoring.multiple(result["value"], RESEARCH_SPENT)),
    }


def continue_choice(
    stock: SimpleNamespace, checkpoint: Any, pick: int, price: float
) -> dict[str, Any]:
    branch = copy.deepcopy(checkpoint)
    branch.policy = continuation_policy(stock, price)
    assert branch.policy.plan == branch.plan
    branch.start_software(None)
    _require_running(branch)
    try:
        branch.advance(NATIVE_DAYS - branch.next_day)
    finally:
        branch.stop_software()
    _require_running(branch)
    assert branch.ended and (
        branch.bankrupt_day is not None or branch.next_day == NATIVE_DAYS
    )
    return option(stock, pick, branch.result())


def run_finish(
    stock: SimpleNamespace, arm: str, world: int, stored: bytes
) -> dict[str, Any]:
    checkpoint, receipt = run_prefix(stock, arm, world)
    if stored != encode(receipt):
        raise SimulatorError(
            f"{arm}-{world:02d}: prefix replay does not match its registered receipt"
        )
    if receipt["terminal"]:
        options = [option(stock, pick, checkpoint.result()) for pick, _ in CHOICES]
    else:
        options = [
            continue_choice(stock, checkpoint, pick, price) for pick, price in CHOICES
        ]
    assert fingerprint(checkpoint) == receipt["prefix_fingerprint"]
    assert [item["pick"] for item in options] == [pick for pick, _ in CHOICES]
    return {
        "schema": PAYOFF_SCHEMA,
        "world": world,
        "prefix_fingerprint": receipt["prefix_fingerprint"],
        "source": PIN,
        "options": options,
    }


def read_own(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise SimulatorError(f"{path}: missing or not a regular file")
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise SimulatorError(f"{path}: larger than {MAX_FILE_BYTES} bytes")
    return data


def write_once(path: Path, data: bytes) -> str:
    if path.is_symlink() or path.exists():
        if read_own(path) == data:
            return "reused"
        raise SimulatorError(
            f"{path}: exists with different content; refusing to overwrite"
        )
    handle, staging = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.link(staging, path)
    finally:
        os.unlink(staging)
    return "written"


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.market_handoff.simulator"
    )
    parser.add_argument("upstream_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--world", required=True, type=int, choices=range(WORLDS))
    parser.add_argument("--stage", default="prefix", choices=STAGES)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    root, output = args.upstream_root.resolve(strict=True), args.output.resolve()
    if output == root or root in output.parents:
        raise SystemExit(
            f"{output}: output must stay outside the read-only upstream root"
        )
    try:
        stock = load_stock(root)
        output.mkdir(parents=True, exist_ok=True)
        prefix_path = output / f"{args.arm}-{args.world:02d}.prefix.json"
        if args.stage == "prefix":
            path, data = prefix_path, encode(run_prefix(stock, args.arm, args.world)[1])
        else:
            path = output / f"{args.arm}-{args.world:02d}.payoffs.json"
            data = encode(
                run_finish(stock, args.arm, args.world, read_own(prefix_path))
            )
        status = write_once(path, data)
    except SimulatorError as error:
        raise SystemExit(f"market_handoff.simulator: {error}") from error
    print(
        canonical(
            {
                "stage": args.stage,
                "path": str(path),
                "status": status,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
