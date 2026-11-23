from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from examples.seatlab.market_handoff import decisions, native_receipt, native_stage

SCHEMA = "seatlab.market-delivery/v1"
ROLES = native_stage.ROLES
SEAT_FIELDS = ("pick", "prompt_sha256", "view_sha256")
Record = dict[str, Any]


class Refused(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def guarded(category: str, call: Callable[..., Any], *args: Any) -> Any:
    try:
        return call(*args)
    except (OSError, ValueError, RecursionError) as exc:
        raise Refused(category) from exc


def role_replies(
    path: Path, plan: decisions.Plan, other: decisions.Plan, key: decisions.Key
) -> tuple[decisions.Picks, str]:
    picks, tally = decisions.outcomes(plan, decisions.read_replies(path, plan, other))
    if tally["malformed"] or picks[key] is None:
        decisions.fail("a supplied reply is malformed or the target reply is missing")
    return picks, decisions.sha(decisions.read_text(path))


def finance_envelopes(
    cells: decisions.Cells, advice: decisions.Picks
) -> decisions.Plan:
    plan: decisions.Plan = {}
    for world in range(decisions.WORLDS):
        for group in decisions.GROUPS:
            pick = advice[(world, native_stage.SALES_SOURCE[group])]
            if pick is not None:
                task = decisions.FINANCE_TASK.format(advice=pick)
                plan[(world, group)] = decisions.envelope(
                    "finance", world, group, cells[("target", world)], task
                )
    return plan


def view_sha(role: str, cell: Record, advice: int | None) -> str:
    view: Record = {
        "role": role,
        "completed_days": cell["completed_days"],
        "observation": cell[role],
    }
    if advice is not None:
        view["advice"] = advice
    return native_stage.digest(native_stage.canonical(view))


def stock(data: Path, world: int, target: Record) -> tuple[list[Record], str]:
    options = decisions.payoffs(data, world, target)
    path = data / f"target-{world:02d}.payoffs.json"
    return options, native_stage.digest(native_receipt.read_bytes(path))


def expected(
    data: Path, world: int, arm: str, sales_path: Path, finance_path: Path
) -> Record:
    cells = guarded("dataset", decisions.load_cells, data)
    target = cells[("target", world)]
    source = cells[(native_stage.SALES_SOURCE[arm], world)]
    options, payoffs_sha = guarded("dataset", stock, data, world, target)
    sales_plan, sales_key = (
        decisions.sales_plan(cells),
        (world, native_stage.SALES_SOURCE[arm]),
    )
    every = decisions.finance_plan(
        cells, {key: decisions.PICKS[0] for key in sales_plan}
    )
    advice, sales_sha = guarded(
        "sales_replies", role_replies, sales_path, sales_plan, every, sales_key
    )
    finance_plan = finance_envelopes(cells, advice)
    decided, finance_sha = guarded(
        "finance_replies",
        role_replies,
        finance_path,
        finance_plan,
        sales_plan,
        (world, arm),
    )
    sales, finance = advice[sales_key], decided[(world, arm)]
    assert sales is not None and finance is not None
    envelopes = {"sales": sales_plan[sales_key], "finance": finance_plan[(world, arm)]}
    seats = {
        "sales": {"pick": sales, "view_sha256": view_sha("sales", source, None)},
        "finance": {"pick": finance, "view_sha256": view_sha("finance", target, sales)},
    }
    for role in ROLES:
        seats[role]["prompt_sha256"] = envelopes[role]["prompt_sha256"]
        seats[role]["case"] = envelopes[role]["id"]
    return {
        "seats": seats,
        "stock_grade": options[finance - 1]["world_score"],
        "prefix_fingerprint": target["prefix_fingerprint"],
        "inputs": {
            "prefixes_sha256": decisions.sha(
                decisions.canonical([cells[key] for key in sorted(cells)])
            ),
            "payoffs_sha256": payoffs_sha,
            "sales_replies_sha256": sales_sha,
            "finance_replies_sha256": finance_sha,
        },
    }


def role_row(observed: Record, wanted: Record) -> Record:
    seen = {field: observed[field] for field in SEAT_FIELDS}
    want = {field: wanted[field] for field in SEAT_FIELDS}
    return {
        "case": wanted["case"],
        "agree": seen == want,
        "observed": seen,
        "expected": want,
    }


def deliver(data: Path, stage: Path, sales_path: Path, finance_path: Path) -> Record:
    native = guarded("native_receipt", native_receipt.receipt, stage)
    want = expected(
        data.expanduser().resolve(strict=True),
        native["world"],
        native["arm"],
        sales_path,
        finance_path,
    )
    roles = {
        role: role_row(native["seats"][role], want["seats"][role]) for role in ROLES
    }
    grade = {
        "native": native["grade"],
        "stock": want["stock_grade"],
        "agree": native["grade"] == want["stock_grade"],
    }
    dataset = {
        "binding": "operator_supplied_dataset",
        "declared_source": decisions.PINNED_SOURCE,
        "prefix_fingerprint": want["prefix_fingerprint"],
        "prefix_agree": native["prefix_fingerprint"] == want["prefix_fingerprint"],
        "payoffs_agree": native["sha256"]["payoffs"]
        == want["inputs"]["payoffs_sha256"],
    }
    agree = (
        all(row["agree"] for row in roles.values())
        and grade["agree"]
        and dataset["prefix_agree"]
        and dataset["payoffs_agree"]
    )
    return {
        "schema": SCHEMA,
        "status": "matched" if agree else "mismatch",
        "comparison": "offline_receipt_against_operator_replies",
        "native": {
            "trial_id": native["trial_id"],
            "trial_name": native["trial_name"],
            "seat_source": native["kind"],
            "world": native["world"],
            "arm": native["arm"],
        },
        "roles": roles,
        "grade": grade,
        "dataset": dataset,
        "inputs": {**want["inputs"], "native": native["sha256"]},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.market_handoff.delivery"
    )
    parser.add_argument("data", type=Path)
    parser.add_argument("stage", type=Path)
    parser.add_argument("--sales-replies", type=Path, required=True)
    parser.add_argument("--finance-replies", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = deliver(
            args.data, args.stage, args.sales_replies, args.finance_replies
        )
    except Refused as exc:
        sys.stderr.write(f"error: {exc.category}\n")
        return 2
    except (OSError, ValueError, RecursionError):
        sys.stderr.write("error: dataset\n")
        return 2
    sys.stdout.write(native_stage.canonical(report).decode("ascii") + "\n")
    return 0 if report["status"] == "matched" else 1


if __name__ == "__main__":
    raise SystemExit(main())
