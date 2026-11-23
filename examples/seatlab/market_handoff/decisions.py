from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

NAMESPACE = "seatlab/market-handoff/v1"
PINNED_SOURCE = "dbec43ff757c86c17077e61b37c7f584c880bdee"
WORLDS = 12
PREFIX_DAYS = 90
SHAPES = ("mnl", "nested", "wom") * 3 + ("delayed_ads", "deal_seekers", "delayed_ads")
ARMS = ("target", "donor")
GROUPS = ("own", "donor")
PICKS = (1, 2, 3)
MIN_DELTA = 0.03
ALPHA = 0.05
MAX_FILE_BYTES = 1 << 22
PREFIX_KEYS = {"schema", "arm", "world", "shape", "completed_days", "terminal", "sales", "finance",
               "source", "numpy_version", "observation_sha256", "prefix_fingerprint"}
PAYOFF_KEYS = {"schema", "world", "prefix_fingerprint", "source", "options"}
OPTION_KEYS = {"pick", "world_score", "multiple"}
REPLY_KEYS = {"id", "prompt_sha256", "reply"}
HEX64 = re.compile(r"[0-9a-f]{64}")
GOAL = (
    "You are the {role} of an online retail company in a simulated market. The company aims to "
    "end its three-year run worth as many times its seed money as possible, without going "
    "bankrupt; its score is the base-32 logarithm of that multiple, clipped to 0..1.\n"
    "The company has run {days} days under a fixed public pricing rule. The same rule continues "
    "for the rest of the run, and the team must choose one price level for all products:\n"
    "1 = keep prices (1.00x)\n2 = raise all prices by 15% (1.15x)\n"
    "3 = lower all prices by 15% (0.85x)\n"
    "Product range, advertising and credit stay unchanged.\nYour {view} view (JSON):\n{data}\n"
)
SALES_TASK = "Your recommendation goes to the finance director, who makes the final choice. "
FINANCE_TASK = "The sales analyst recommends option {advice}. You make the final choice. "
REPLY_RULE = ('Use no tools and add no notes. Reply with only the JSON object {"pick": N}, '
              "where N is 1, 2, or 3.")

Key = tuple[int, str]
Record = dict[str, Any]
Picks = dict[Key, int | None]
Tally = dict[str, int]
Cells = dict[tuple[str, int], Record]
Plan = dict[Key, dict[str, str]]


def fail(message: str) -> NoReturn:
    raise ValueError(message)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unique(pairs: list[tuple[str, Any]]) -> Record:
    return dict(pairs) if len({key for key, _ in pairs}) == len(pairs) else fail("duplicate key")


def decode(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_unique, parse_constant=fail)


def read_text(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        fail(f"{path}: larger than {MAX_FILE_BYTES} bytes")
    return data.decode("utf-8")


def require(path: Path, checks: dict[str, bool]) -> None:
    bad = [name for name, ok in checks.items() if not ok]
    if bad:
        fail(f"{path}: invalid {', '.join(bad)}")


def load_cells(root: Path) -> Cells:
    cells: Cells = {}
    for world, arm in ((world, arm) for world in range(WORLDS) for arm in ARMS):
        path = root / f"{arm}-{world:02d}.prefix.json"
        record = decode(read_text(path))
        if not isinstance(record, dict) or record.keys() != PREFIX_KEYS:
            fail(f"{path}: expected exactly {sorted(PREFIX_KEYS)}")
        days, views = record["completed_days"], {key: record[key] for key in ("sales", "finance")}
        require(path, {
            "schema": record["schema"] == "seatlab.market-prefix/v1",
            "arm": record["arm"] == arm,
            "world": type(record["world"]) is int and record["world"] == world,
            "shape": record["shape"] == SHAPES[world],
            "completed_days": type(days) is int and 0 <= days <= PREFIX_DAYS,
            "terminal": type(record["terminal"]) is bool
            and (days == PREFIX_DAYS or record["terminal"]),
            "views": all(isinstance(view, dict) for view in views.values()),
            "source": record["source"] == PINNED_SOURCE,
            "numpy_version": isinstance(record["numpy_version"], str),
            "observation_sha256": record["observation_sha256"] == sha(canonical(views)),
            "prefix_fingerprint": isinstance(record["prefix_fingerprint"], str)
            and HEX64.fullmatch(record["prefix_fingerprint"]) is not None,
        })
        cells[(arm, world)] = record
    if len({cell["numpy_version"] for cell in cells.values()}) != 1:
        fail("prefix cells disagree on numpy_version")
    return cells


def envelope(stage: str, world: int, role: str, cell: Record, task: str) -> dict[str, str]:
    assert stage in ("sales", "finance") and 0 <= world < WORLDS and role in ARMS + GROUPS
    persona = "sales analyst" if stage == "sales" else "finance director"
    prompt = GOAL.format(role=persona, view=stage, days=cell["completed_days"],
                         data=canonical(cell[stage])) + task + REPLY_RULE
    case = "cont-" + sha(f"{NAMESPACE}/{stage}/{world}/{role}")[:24]
    return {"id": case, "prompt_sha256": sha(prompt), "prompt": prompt}


def sales_plan(cells: Cells) -> Plan:
    return {(world, arm): envelope("sales", world, arm, cells[(arm, world)], SALES_TASK)
            for world in range(WORLDS) for arm in ARMS}


def finance_plan(cells: Cells, advice: Picks) -> Plan:
    plan: Plan = {}
    for world in range(WORLDS):
        own, donor = advice[(world, "target")], advice[(world, "donor")]
        if own is None or donor is None:
            continue
        for group, pick in (("own", own), ("donor", donor)):
            task = FINANCE_TASK.format(advice=pick)
            plan[(world, group)] = envelope("finance", world, group, cells[("target", world)], task)
    return plan


def read_replies(path: Path, plan: Plan, other: Plan) -> dict[str, str]:
    expected = {item["id"]: item["prompt_sha256"] for item in plan.values()}
    other_ids = {item["id"] for item in other.values()}
    replies: dict[str, str] = {}
    for number, line in enumerate(read_text(path).splitlines(), 1):
        where = f"{path}:{number}"
        try:
            record = decode(line)
        except (ValueError, RecursionError) as exc:
            fail(f"{where}: {exc}")
        if not isinstance(record, dict) or record.keys() != REPLY_KEYS or not all(
                isinstance(value, str) for value in record.values()):
            fail(f"{where}: expected exactly string id, prompt_sha256, and reply")
        case = record["id"]
        if case in other_ids:
            fail(f"{where}: reply {case} belongs to the other role")
        if case not in expected or case in replies:
            fail(f"{where}: reply {case} is duplicate or matches no emitted request")
        if record["prompt_sha256"] != expected[case]:
            fail(f"{where}: stale reply {case} answers a different prompt")
        replies[case] = record["reply"]
    return replies


def outcomes(plan: Plan, replies: dict[str, str]) -> tuple[Picks, Tally]:
    picks: Picks = {}
    for key, item in plan.items():
        try:
            value = decode(replies[item["id"]].strip()) if item["id"] in replies else None
        except (ValueError, RecursionError):
            value = None
        pick = value.get("pick") if isinstance(value, dict) and value.keys() == {"pick"} else None
        picks[key] = pick if type(pick) is int and pick in PICKS else None
    present = sum(item["id"] in replies for item in plan.values())
    valid = sum(pick is not None for pick in picks.values())
    return picks, {"emitted": len(plan), "valid": valid, "malformed": present - valid,
                   "missing": len(plan) - present}


def payoffs(root: Path, world: int, cell: Record) -> list[Record]:
    path = root / f"target-{world:02d}.payoffs.json"
    record = decode(read_text(path))
    if not isinstance(record, dict) or record.keys() != PAYOFF_KEYS:
        fail(f"{path}: expected exactly {sorted(PAYOFF_KEYS)}")
    options = record["options"] if isinstance(record["options"], list) else []
    ok = [item.get("pick") if isinstance(item, dict) else None for item in options] == list(PICKS)
    require(path, {
        "schema": record["schema"] == "seatlab.market-payoffs/v1",
        "source": record["source"] == PINNED_SOURCE,
        "world": type(record["world"]) is int and record["world"] == world,
        "prefix_fingerprint": record["prefix_fingerprint"] == cell["prefix_fingerprint"],
        "options": ok and all(item.keys() == OPTION_KEYS and all(
            type(item[field]) in (int, float) and math.isfinite(item[field])
            for field in ("multiple", "world_score")) and 0 <= item["world_score"] <= 1
            for item in options),
    })
    return options


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def group_row(world: int, cell: Record, options: list[Record], advice: Picks,
              decision: Picks) -> Record:
    own, donor = decision.get((world, "own")), decision.get((world, "donor"))
    row: Record = {"world": world, "shape": cell["shape"], "terminal": cell["terminal"],
                   "prefix_fingerprint": cell["prefix_fingerprint"],
                   "advice": {"own": advice[(world, "target")], "donor": advice[(world, "donor")]},
                   "decision": {"own": own, "donor": donor},
                   "complete": own is not None and donor is not None}
    if own is not None and donor is not None:
        for field in ("world_score", "multiple"):
            row[field] = {"own": options[own - 1][field], "donor": options[donor - 1][field]}
        row["delta"] = options[own - 1]["world_score"] - options[donor - 1]["world_score"]
    return row


def summary(done: list[Record], options: dict[int, list[Record]]) -> Record:
    deltas: list[float] = [row["delta"] for row in done]
    wins, losses, n = sum(d > 0 for d in deltas), sum(d < 0 for d in deltas), len(deltas)
    non_tied = wins + losses
    p = sum(math.comb(non_tied, k) for k in range(wins, non_tied + 1)) / 2**non_tied
    gain = mean(deltas)
    passed = n == WORLDS and gain is not None and gain >= MIN_DELTA and p <= ALPHA
    shared = [row["advice"]["own"] == row["advice"]["donor"] for row in done]
    scores = [[item["world_score"] for item in listed] for listed in options.values()]
    return {"primary": {
        "complete": n, "required": WORLDS, "mean_delta": gain, "wins": wins, "losses": losses,
        "ties": n - non_tied, "ties_rule": "excluded_from_sign_test", "p_one_sided": p,
        "min_delta": MIN_DELTA, "alpha": ALPHA, "passed": passed,
    }, "descriptive": {
        "own_mean": mean([row["world_score"]["own"] for row in done]),
        "donor_mean": mean([row["world_score"]["donor"] for row in done]),
        "oracle_mean": mean([max(values) for values in scores]),
        "uniform_mean": mean([sum(values) / len(values) for values in scores]),
        "hold_mean": mean([values[0] for values in scores]),
        "shared_advice": {"n": sum(shared), "mean_delta": mean(
            [delta for delta, same in zip(deltas, shared) if same])},
        "different_advice": {"n": n - sum(shared), "mean_delta": mean(
            [delta for delta, same in zip(deltas, shared) if not same])},
        "agreement": {group: mean([float(row["decision"][group] == row["advice"][group])
                                   for row in done]) for group in GROUPS},
        "multiple_gain": mean([row["multiple"]["own"] - row["multiple"]["donor"] for row in done]),
    }}


def advise(root: Path, sales_path: Path) -> tuple[Cells, Picks, Tally]:
    cells = load_cells(root)
    every: Picks = {(world, arm): PICKS[0] for world in range(WORLDS) for arm in ARMS}
    plan, every_finance = sales_plan(cells), finance_plan(cells, every)
    advice, tally = outcomes(plan, read_replies(sales_path, plan, every_finance))
    return cells, advice, tally


def score(root: Path, sales_path: Path, finance_path: Path) -> Record:
    cells, advice, sales_tally = advise(root, sales_path)
    plan = finance_plan(cells, advice)
    decision, finance_tally = outcomes(plan, read_replies(finance_path, plan, sales_plan(cells)))
    finance_tally["skipped_pairs"] = WORLDS - len(plan) // 2
    options = {world: payoffs(root, world, cells[("target", world)]) for world in range(WORLDS)}
    rows = [group_row(world, cells[("target", world)], options[world], advice, decision)
            for world in range(WORLDS)]
    inputs = {"prefixes_sha256": sha(canonical([cells[key] for key in sorted(cells)])),
              "sales_replies_sha256": sha(read_text(sales_path)),
              "finance_replies_sha256": sha(read_text(finance_path))}
    return {"schema": "seatlab.market-handoff-score/v1", "source": PINNED_SOURCE,
            "numpy_version": cells[("target", 0)]["numpy_version"], "inputs": inputs,
            "sales": sales_tally, "finance": finance_tally, "groups": rows,
            **summary([row for row in rows if row["complete"]], options)}


def emit(args: argparse.Namespace) -> None:
    sealed = sorted(path.name for path in args.dir.glob("*.payoffs.json"))
    if sealed:
        fail(f"payoffs exist before decisions were sealed: {sealed}")
    extra: Record = {}
    if args.command == "emit-sales":
        stage, roles, plan = "sales", ARMS, sales_plan(load_cells(args.dir))
    else:
        cells, advice, extra["sales"] = advise(args.dir, args.sales_replies)
        stage, roles, plan = "finance", GROUPS, finance_plan(cells, advice)
        extra["skipped_worlds"] = [world for world in range(WORLDS) if (world, "own") not in plan]
    ranked = sorted(range(WORLDS), key=lambda world: sha(f"{NAMESPACE}/order/{stage}/{world}"))
    for world in range(WORLDS):
        for role in roles[::-1] if world in ranked[WORLDS // 2:] else roles:
            if (world, role) in plan:
                sys.stdout.write(canonical(plan[(world, role)]) + "\n")
    sys.stderr.write(canonical({"stage": stage, "requests": len(plan)} | extra) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.seatlab.market_handoff.decisions")
    commands = parser.add_subparsers(dest="command", required=True)
    for command, names in (("emit-sales", ("dir",)), ("emit-finance", ("dir", "sales_replies")),
                           ("score", ("dir", "sales_replies", "finance_replies"))):
        sub = commands.add_parser(command)
        for name in names:
            sub.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "score":
            print(canonical(score(args.dir, args.sales_replies, args.finance_replies)))
        else:
            emit(args)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
