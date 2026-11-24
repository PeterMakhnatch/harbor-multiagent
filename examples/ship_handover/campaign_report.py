import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ..native_evidence import Invalid
from ..native_study import as_choice, as_int, as_list, as_record, require
from ..seat_handoffs.native_records import read_record
from ..seat_seals.admission import ISSUED, SHIPPING_STATUS_SCHEMA, UNISSUED
from .campaign_plan import MAX_PATH_BYTES, bind, inventory, load_plan
from .study import MAX_CELLS
from .transfer import (
    INFLIGHT_WORLD,
    STATES,
    STOCK_WORLD,
    WORLDS,
    Claimed,
    Graded,
    World,
    _reason,
    _scrub,
    _text,
    _tsv,
    assemble,
    read_claims,
)
from .transfer_contract import GRADE_OF, GRADED, registration_digest

SCHEMA = "harbor.shipping.campaign-report/v1"
STATUS_FIELDS = frozenset({"schema", "plan", "registration", "counts", "tickets"})
ENTRY_FIELDS = frozenset({"ticket", "worker", "state"})
ADMISSIONS = (ISSUED, UNISSUED)
OUTCOMES = (
    "issued_eligible",
    "issued_unknown",
    "unissued_eligible",
    "unissued_unknown",
)
DISAGREEING = "unissued_eligible"
MAX_ENTRY_BYTES = 128
MAX_STATUS_BYTES = 1 << 20
assert ADMISSIONS == ("issued", "unissued") and DISAGREEING in OUTCOMES
assert 2 * MAX_CELLS * MAX_ENTRY_BYTES <= MAX_STATUS_BYTES
assert {world.name for world in WORLDS} == {"stock", "inflight"}

Record = dict[str, Any]

NOTES = {
    "issued": (
        "issued counts tickets whose ledger row records one grant; a grant permits"
        " one attempted native start and does not prove that the native process or"
        " any seat started; a lost grant spends the ticket and never authorizes a"
        " restart; unissued is not proof that no other launcher ran"
    ),
    "eligible": (
        "eligible counts received packet rows with status positive or zero"
        " (world grade 1 or 0, grade source native_agreed); receipt of a packet row"
        " is not a witnessed seat return"
    ),
    "unknown": (
        "unknown counts tickets with no received row or a declared non-graded"
        " status; unknown is never zero and never a failure"
    ),
    "disagreeing": (
        "unissued_eligible counts tickets whose ledger says unissued while a"
        " received row carries an eligible grade; the records disagree and the"
        " grade is kept in every transfer interval, never discarded"
    ),
    "physical": (
        "physical counts unresolved or native_error rows that still carry a world"
        " grade; they are reported by admission state and never enter an interval"
    ),
    "transfer": (
        "deltas and transfer intervals come from examples.ship_handover.transfer"
        " over every received packet regardless of admission state; admission"
        " never changes a grade, a count or a bound"
    ),
    "scope": (
        "aggregate counts only; no ticket, path, roster, hidden state or native"
        " identifier is reported; digests name content only and are not"
        " credentials, signatures or proof of a model response"
    ),
}


def _read_status(path: Path) -> Record:
    require(isinstance(path, Path), "admission status path is not a Path")
    size = len(os.fsencode(path))
    require(0 < size <= MAX_PATH_BYTES, "admission status path exceeds its bound")
    try:
        record = read_record(path, MAX_STATUS_BYTES)
    except OSError as error:
        raise ValueError(f"admission status {path} is not a plain file") from error
    except ValueError as error:
        raise ValueError(f"admission status {path}: {error}") from None
    require(record is not None, f"admission status {path} does not exist")
    return as_record(record, "admission status", STATUS_FIELDS)


def load_status(path: Path, plan: Record, rows: list[tuple[str, int]]) -> Record:
    status = _read_status(path)
    require(
        status["schema"] == SHIPPING_STATUS_SCHEMA,
        f"admission status schema must be {SHIPPING_STATUS_SCHEMA}",
    )
    require(
        status["plan"] == registration_digest(plan),
        "admission status plan differs from the campaign plan",
    )
    require(
        status["registration"] == plan["registration"],
        "admission status registration differs from the campaign plan",
    )
    total = len(rows)
    entries = as_list(status["tickets"], "admission status tickets", total, total)
    found: list[tuple[str, int]] = []
    states: dict[str, str] = {}
    top = plan["workers"] - 1
    for value in entries:
        entry = as_record(value, "admission status ticket", ENTRY_FIELDS)
        ticket = entry["ticket"]
        require(isinstance(ticket, str), "admission status ticket id is not text")
        worker = as_int(entry["worker"], "admission status worker", 0, top)
        found.append((ticket, worker))
        states[ticket] = as_choice(entry["state"], "admission status state", ADMISSIONS)
    require(found == rows, "admission status tickets, order or owners differ")
    assert len(states) == total
    fields = frozenset(ADMISSIONS)
    counts = as_record(status["counts"], "admission status counts", fields)
    tally = Counter(states.values())
    for admitted in ADMISSIONS:
        claimed = as_int(counts[admitted], f"admission status {admitted}", 0, total)
        require(claimed == tally[admitted], "admission status counts differ")
    return {"digest": registration_digest(status), "states": states}


def _blank() -> Record:
    return {
        "admission": {state: 0 for state in ADMISSIONS},
        "outcomes": {outcome: 0 for outcome in OUTCOMES},
        "positive": {state: 0 for state in ADMISSIONS},
        "reported": {state: 0 for state in ADMISSIONS},
        "physical": {state: {"known": 0, "positive": 0} for state in ADMISSIONS},
    }


def _count(side: Record, admitted: str, claim: Graded | None) -> None:
    side["admission"][admitted] += 1
    if claim is None:
        side["outcomes"][f"{admitted}_unknown"] += 1
        return
    status, physical = claim
    side["reported"][admitted] += 1
    eligible = status in GRADED
    side["outcomes"][f"{admitted}_{'eligible' if eligible else 'unknown'}"] += 1
    if eligible:
        side["positive"][admitted] += GRADE_OF[status]
    if physical is not None:
        side["physical"][admitted]["known"] += 1
        side["physical"][admitted]["positive"] += physical


def _check_side(side: Record, tickets: list[str], counted: Record) -> None:
    outcomes, admission = side["outcomes"], side["admission"]
    assert sum(outcomes.values()) == sum(admission.values()) == len(tickets)
    for admitted in ADMISSIONS:
        known = outcomes[f"{admitted}_eligible"]
        assert known + outcomes[f"{admitted}_unknown"] == admission[admitted]
        assert 0 <= side["positive"][admitted] <= known
        assert known <= side["reported"][admitted] <= admission[admitted]
    totals = counted["totals"]
    eligible = outcomes["issued_eligible"] + outcomes[DISAGREEING]
    positive = sum(stratum["positive"] for stratum in counted["strata"])
    physical = sum(side["physical"][state]["known"] for state in ADMISSIONS)
    assert (
        totals["registered"] == len(tickets)
        and totals["eligible"] == eligible
        and positive == sum(side["positive"].values())
        and totals["reported"] == sum(side["reported"].values())
        and totals["physical_known"] == physical
    )


def _world(
    world: World,
    tickets: list[str],
    states: dict[str, str],
    claims: dict[str, Graded],
) -> Record:
    assert set(claims) <= set(tickets)
    side = _blank()
    for ticket in tickets:
        _count(side, states[ticket], claims.get(ticket))
    return side


def _sum(sides: list[Record]) -> Record:
    total = _blank()
    for side in sides:
        for field in ("admission", "outcomes", "positive", "reported"):
            for key in total[field]:
                total[field][key] += side[field][key]
        for admitted in ADMISSIONS:
            for key in ("known", "positive"):
                total["physical"][admitted][key] += side["physical"][admitted][key]
    return total


def _workers(
    rows: list[tuple[str, int]], states: dict[str, str], count: int
) -> list[Record]:
    found = [{"worker": worker, ISSUED: 0, UNISSUED: 0} for worker in range(count)]
    for ticket, worker in rows:
        found[worker][states[ticket]] += 1
    assert sum(entry[ISSUED] + entry[UNISSUED] for entry in found) == len(rows)
    return found


def _distinct_claims(
    stock: Record, inflight: Record, packets: dict[str, list[Path]]
) -> Claimed:
    claimed = read_claims(stock, inflight, packets)
    for world in WORLDS:
        digests = claimed[world.name][0]
        require(
            len(set(digests)) == len(digests),
            f"a {world.name} packet is given more than once",
        )
    return claimed


def report(
    stock: Path,
    inflight: Path,
    plan_path: Path,
    status_path: Path,
    expected: str,
    packets: dict[str, list[Path]],
) -> Record:
    plan = load_plan(plan_path, expected)
    stock_reg, inflight_reg = bind(plan, stock, inflight)
    regs = {STOCK_WORLD.name: stock_reg, INFLIGHT_WORLD.name: inflight_reg}
    planned: dict[str, str] = plan["registrations"]
    for world in WORLDS:
        require(
            registration_digest(regs[world.name]) == planned[world.name],
            f"{world.name} registration differs from the campaign plan",
        )
    rows = inventory(plan)
    status = load_status(status_path, plan, rows)
    states: dict[str, str] = status["states"]
    read = _distinct_claims(stock_reg, inflight_reg, packets)
    effects = assemble(stock_reg, inflight_reg, read)
    worlds: Record = {}
    for world in WORLDS:
        claims = read[world.name][1]
        counted = effects["worlds"][world.name]
        assert counted["registration_sha256"] == planned[world.name]
        tickets = list(plan["tickets"][world.name])
        side = _world(world, tickets, states, claims)
        _check_side(side, tickets, counted)
        worlds[world.name] = {**side, "delta": counted["delta"]}
    return {
        "schema": SCHEMA,
        "sources": {
            "plan": registration_digest(plan),
            "registration": plan["registration"],
            "registrations": {world.name: planned[world.name] for world in WORLDS},
            "status": status["digest"],
            "status_schema": SHIPPING_STATUS_SCHEMA,
            "transfer_schema": effects["schema"],
            "packets": {world.name: read[world.name][0] for world in WORLDS},
        },
        "workers": _workers(rows, states, plan["workers"]),
        "worlds": worlds,
        "total": _sum([worlds[world.name] for world in WORLDS]),
        "transfer": effects["transfer"],
        "notes": NOTES,
    }


def _row(name: str, side: Record) -> Record:
    physical = side["physical"]
    return {
        "world": name,
        **side["admission"],
        **side["outcomes"],
        **{f"positive_{key}": count for key, count in side["positive"].items()},
        **{f"reported_{key}": count for key, count in side["reported"].items()},
        **{
            f"physical_{admitted}": "{known}/{positive}".format(**physical[admitted])
            for admitted in ADMISSIONS
        },
    }


def _transfer_row(found: Record, state: str) -> Record:
    return {
        "state": state,
        **{
            f"{world.name}_delta": _text(found["worlds"][world.name]["delta"][state])
            for world in WORLDS
        },
        "transfer": _text(found["transfer"][state]["interval"]),
        "comparison": found["transfer"][state]["comparison"],
    }


def render(found: Record) -> str:
    sources = found["sources"]
    worlds = [_row(world.name, found["worlds"][world.name]) for world in WORLDS]
    lines = [f"{key}: {sources[key]}" for key in ("plan", "registration", "status")]
    packets = sources["packets"]
    lines += [f"{world.name}_packets: {len(packets[world.name])}" for world in WORLDS]
    sections = [
        "\n".join(lines),
        _tsv([*worlds, _row("total", found["total"])]),
        _tsv(found["workers"]),
        _tsv([_transfer_row(found, state) for state in STATES]),
        "\n".join(f"{key}: {text}" for key, text in found["notes"].items()),
    ]
    return "\n\n".join(sections)


def parser() -> argparse.ArgumentParser:
    found = argparse.ArgumentParser(
        prog="python3.12 -BS -m examples.ship_handover.campaign_report",
        description="aggregate a shipping campaign ledger status with its packets",
    )
    found.add_argument("stock", type=Path, metavar="STOCK_REG")
    found.add_argument("inflight", type=Path, metavar="INFLIGHT_REG")
    found.add_argument("plan", type=Path, metavar="PLAN")
    found.add_argument("status", type=Path, metavar="STATUS")
    found.add_argument("--expected-plan", required=True, metavar="DIG")
    for world in WORLDS:
        found.add_argument(
            f"--{world.name}-packet",
            dest=f"{world.name}_packets",
            type=Path,
            action="append",
            default=[],
            metavar="P",
        )
    found.add_argument("--json", action="store_true")
    return found


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    packets = {world.name: getattr(args, f"{world.name}_packets") for world in WORLDS}
    inputs = [args.stock, args.inflight, args.plan, args.status]
    inputs += [path for paths in packets.values() for path in paths]
    try:
        found = report(
            args.stock,
            args.inflight,
            args.plan,
            args.status,
            args.expected_plan,
            packets,
        )
    except (OSError, ValueError, Invalid) as error:
        print(f"campaign_report: {_scrub(_reason(error), inputs)}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(found, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print(render(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
