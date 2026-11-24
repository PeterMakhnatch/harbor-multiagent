"""Preview a hypothetical USD cost ceiling for a frozen shipping campaign plan."""

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from ...native_study import require
from ...ship_handover.campaign_plan import WORLDS, Plan, bind, inventory, load_plan
from ...ship_handover.study import ROLES
from ...ship_handover.transfer_contract import team_label
from .team_prices import (
    CONDITIONS,
    CURRENCY,
    EXACT,
    NOT_CLAIMED,
    PREVIEW,
    ROUNDING,
    ZERO,
    Seat,
    cents,
    declared_usd,
    exact,
    load_costs,
    new_parser,
    run,
    seat_rows,
    team_ceiling,
    times,
    verdict,
)

SCHEMA = "harbor.shipping.cost-ceiling-preview/v1"


def participants(registration: dict[str, Any]) -> dict[str, dict[str, Any]]:
    members = registration["teams"][team_label(registration)]
    found = {member["seat"]: member for member in members}
    require(
        set(found) == set(ROLES) and len(found) == len(members),
        "registered team needs exactly one participant per seat",
    )
    return {role: found[role] for role in ROLES}


def assignments(plan: Plan) -> list[dict[str, list[str]]]:
    world_of = {
        ticket: spec.world for spec in WORLDS for ticket in plan["tickets"][spec.world]
    }
    found: list[dict[str, list[str]]] = [
        {spec.world: [] for spec in WORLDS} for _ in range(plan["workers"])
    ]
    for ticket, owner in inventory(plan):
        found[owner][world_of[ticket]].append(ticket)
    assert sum(len(t) for worker in found for t in worker.values()) == len(world_of)
    assert all(t == sorted(t) for worker in found for t in worker.values())
    return found


def _workers(
    plan: Plan, attempts: int, per_seat: dict[str, Decimal], team: Decimal
) -> tuple[list[dict[str, Any]], int, Decimal]:
    rows: list[dict[str, Any]] = []
    runs_total = 0
    total = ZERO
    for index, tickets in enumerate(assignments(plan)):
        runs = attempts * sum(len(found) for found in tickets.values())
        cost = times(runs, team)
        rows.append(
            {
                "worker": index,
                "tickets": tickets,
                "team_attempts": runs,
                "seats": {
                    role: cents(times(runs, attempt))
                    for role, attempt in per_seat.items()
                },
                "ceiling_usd_exact": exact(cost),
                "ceiling_usd": cents(cost),
            }
        )
        runs_total += runs
        total = EXACT.add(total, cost)
    assert total == times(runs_total, team)
    return rows, runs_total, total


def preview(
    plan: Plan, expected: str, attempts: int, seats: list[Seat], declared: Decimal
) -> dict[str, Any]:
    per_seat, team = team_ceiling(seats)
    workers, runs, total = _workers(plan, attempts, per_seat, team)
    tickets = sum(len(plan["tickets"][spec.world]) for spec in WORLDS)
    assert runs == attempts * tickets
    return {
        "schema": SCHEMA,
        "preview": PREVIEW,
        "plan": expected,
        "registration": plan["registration"],
        "currency": CURRENCY,
        "rounding": ROUNDING,
        "attempts_per_ticket": attempts,
        "tickets": tickets,
        "team_attempts": runs,
        "seats": seat_rows(seats, per_seat, runs),
        "workers": workers,
        "team_attempt_ceiling_usd_exact": exact(team),
        **verdict(total, declared),
        "conditions": list(CONDITIONS),
        "not": list(NOT_CLAIMED),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    declared = declared_usd(args.declared_usd)
    plan = load_plan(args.plan, args.expected_plan)
    stock, _ = bind(plan, args.stock, args.inflight)
    attempts, seats, digests = load_costs(args, participants(stock))
    found = preview(plan, args.expected_plan, attempts, seats, declared)
    assert not set(digests) & set(found)
    return {**found, **digests}


def parser() -> argparse.ArgumentParser:
    found = new_parser(
        "python3.12 -BS -m examples.seatlab.workbench.cost_plan",
        "Preview a hypothetical USD cost ceiling for a frozen shipping campaign "
        "plan. It reads files only. It is not a spend cap and not permission to "
        "start a trial. Exit 0: at or below the declared USD. Exit 1: above. "
        "Exit 2: malformed or incomplete input.",
    )
    found.add_argument("stock", type=Path, metavar="STOCK_REG")
    found.add_argument("inflight", type=Path, metavar="INFLIGHT_REG")
    found.add_argument("plan", type=Path, metavar="PLAN")
    found.add_argument("--expected-plan", required=True, metavar="DIGEST")
    return found


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    inputs = [args.stock, args.inflight, args.plan, args.rates, args.limits]
    return run(build, args, inputs, "cost_plan")


if __name__ == "__main__":
    sys.exit(main())
