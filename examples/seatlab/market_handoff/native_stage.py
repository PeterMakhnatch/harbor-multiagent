from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, NoReturn

from examples.seatlab.market_handoff import decisions

TEMPLATE = Path(__file__).resolve().parent / "native_task"
TASK_FILES = (
    "task.toml",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/player/Dockerfile",
    "environment/serve.py",
    "instructions/sales.md",
    "instructions/finance.md",
    "tests/test.sh",
    "tests/score.py",
)
SCENARIO = "environment/scenario.json"
PAYOFFS = "tests/payoffs.json"
SCENARIO_SCHEMA = "seatlab.market-native-scenario/v1"
STAGE_SCHEMA = "seatlab.market-native-stage/v1"
SEAT_IMPORT = "examples.seatlab.market_handoff.native_seat:MarketSeat"
DECLARED_MODEL = "deepseek/deepseek-flash"
ROLES = ("sales", "finance")
ARMS = ("own", "donor")
MODES = ("replay", "deepseek", "command")
SALES_SOURCE = {"own": "target", "donor": "donor"}
MAX_TEMPLATE_BYTES = 1 << 20
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def trial_name(world: int, arm: str, mode: str) -> str:
    assert 0 <= world < decisions.WORLDS and arm in ARMS and mode in MODES
    return f"market-{world:02d}-{arm}-{mode}"


def replay_picks(data: Path, world: int, arm: str, sales_path: Path,
                 finance_path: Path) -> dict[str, int]:
    cells, advice, _ = decisions.advise(data, sales_path)
    plan = decisions.finance_plan(cells, advice)
    replies = decisions.read_replies(finance_path, plan, decisions.sales_plan(cells))
    decision, _ = decisions.outcomes(plan, replies)
    sales = advice[(world, SALES_SOURCE[arm])]
    finance = decision.get((world, arm))
    if sales is None:
        fail(f"world {world} arm {arm}: official sales reply is missing or invalid")
    if finance is None:
        fail(f"world {world} arm {arm}: official finance reply is missing or invalid")
    assert sales in decisions.PICKS and finance in decisions.PICKS
    return {"sales": sales, "finance": finance}


def scenario(cells: decisions.Cells, world: int, arm: str) -> dict[str, Any]:
    source, target = cells[(SALES_SOURCE[arm], world)], cells[("target", world)]
    days = {"sales": source["completed_days"], "finance": target["completed_days"]}
    assert all(type(value) is int and 0 <= value <= decisions.PREFIX_DAYS
               for value in days.values())
    return {"schema": SCENARIO_SCHEMA, "completed_days": days,
            "sales": source["sales"], "finance": target["finance"]}


def participants(mode: str, name: str, picks: dict[str, int] | None) -> list[dict[str, Any]]:
    assert mode in MODES and (mode == "replay") == (picks is not None)
    rows = []
    for role in ROLES:
        kwargs: dict[str, Any] = {"source": mode, "run_id": name}
        if picks is not None:
            kwargs["pick"] = picks[role]
        rows.append({"seat": role, "name": None, "import_path": SEAT_IMPORT,
                     "model_name": DECLARED_MODEL if mode == "deepseek" else None,
                     "kwargs": kwargs})
    return rows


def trial_config(out: Path, name: str, mode: str,
                 picks: dict[str, int] | None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task": {"path": str(out / "task"), "git_url": None, "name": None},
        "trial_name": name,
        "trials_dir": str(out / "trials"),
        "agent_timeout_multiplier": 2.0,
        "environment": {"type": "docker", "force_build": True, "delete": True,
                        "stream": False},
        "verifier": {"disable": False},
        "participants": participants(mode, name, picks),
    }


def template_bytes(name: str) -> bytes:
    path = TEMPLATE / name
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_TEMPLATE_BYTES:
        fail(f"{path}: template file must be a regular file under {MAX_TEMPLATE_BYTES} bytes")
    data = path.read_bytes()
    assert len(data) == details.st_size
    return data


def create(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with os.fdopen(os.open(path, CREATE_FLAGS, mode), "wb") as output:
        os.fchmod(output.fileno(), mode)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def empty_out(out: Path) -> Path:
    out = out.expanduser().absolute()
    if out.exists() or out.is_symlink():
        if out.is_symlink() or not out.is_dir() or any(out.iterdir()):
            fail(f"{out}: output must be a new or empty real directory")
    else:
        out.mkdir(mode=0o755)
    return out.resolve(strict=True)


def write_task(task: Path, files: dict[str, bytes]) -> None:
    task.mkdir(mode=0o755)
    for name, data in files.items():
        create(task / name, data, 0o755 if name.endswith(".sh") else 0o644)
    task.chmod(0o755)
    for directory in task.rglob("*"):
        if directory.is_dir() and not directory.is_symlink():
            directory.chmod(0o755)


def stage(args: argparse.Namespace) -> dict[str, Any]:
    replies = (args.sales_replies, args.finance_replies)
    if args.mode not in MODES:
        fail(f"unknown market mode: {args.mode!r}")
    if args.mode != "replay" and any(path is not None for path in replies):
        fail(f"{args.mode} mode refuses replay replies")
    if args.mode == "replay" and any(path is None for path in replies):
        fail("replay mode requires --sales-replies and --finance-replies")
    data = args.data.expanduser().resolve(strict=True)
    cells = decisions.load_cells(data)
    target = cells[("target", args.world)]
    decisions.payoffs(data, args.world, target)
    payoff_bytes = decisions.read_text(
        data / f"target-{args.world:02d}.payoffs.json").encode("utf-8")
    picks = (replay_picks(data, args.world, args.arm, *replies)
             if args.mode == "replay" else None)
    scenario_bytes = canonical(scenario(cells, args.world, args.arm))
    files = {name: template_bytes(name) for name in TASK_FILES}
    files[SCENARIO], files[PAYOFFS] = scenario_bytes, payoff_bytes
    out = empty_out(args.out)
    name = trial_name(args.world, args.arm, args.mode)
    config = trial_config(out, name, args.mode, picks)
    write_task(out / "task", files)
    config_bytes = canonical(config)
    create(out / "config.json", config_bytes, 0o644)
    return {
        "schema": STAGE_SCHEMA, "world": args.world, "arm": args.arm, "mode": args.mode,
        "trial_name": name, "config": str(out / "config.json"), "task": str(out / "task"),
        "trials_dir": str(out / "trials"),
        "trial_dir": str(out / "trials" / name),
        "result": str(out / "trials" / name / "result.json"),
        "receipt": str(out / "trials" / name / "artifacts/logs/world/market.json"),
        "reward": str(out / "trials" / name / "verifier/reward.json"),
        "picks": picks, "prefix_fingerprint": target["prefix_fingerprint"],
        "sha256": {"config": digest(config_bytes), "scenario": digest(scenario_bytes),
                   "payoffs": digest(payoff_bytes)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.seatlab.market_handoff.native_stage")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("stage")
    sub.add_argument("data", type=Path)
    sub.add_argument("out", type=Path)
    sub.add_argument("--world", type=int, required=True, choices=range(decisions.WORLDS))
    sub.add_argument("--arm", required=True, choices=ARMS)
    sub.add_argument("--mode", required=True, choices=MODES)
    sub.add_argument("--sales-replies", type=Path)
    sub.add_argument("--finance-replies", type=Path)
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(canonical(stage(args)).decode("ascii") + "\n")
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
