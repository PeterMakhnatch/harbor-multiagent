import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from ..native_study import (
    DIGEST,
    MAX_SHARDS,
    as_int,
    as_list,
    as_record,
    as_text,
    record_bytes,
    require,
    write_exclusive,
)
from ..seat_handoffs.native_records import read_record
from .study import CHANNELS, INFLIGHT, MAX_CELLS, SPECS, STOCK, Spec
from .transfer_contract import load_pair, registration_digest

SCHEMA = "harbor.shipping.campaign-plan/v1"
PLAN_FIELDS = frozenset(
    {"schema", "registration", "registrations", "workers", "tickets"}
)
WORLDS = (STOCK, INFLIGHT)
WORLD_FIELDS = frozenset(spec.world for spec in WORLDS)
STRATA = len(STOCK.states) * len(CHANNELS)
MAX_WORKERS = MAX_SHARDS
MAX_PLAN_BYTES = 512 << 10
MAX_PATH_BYTES = 4096
MAX_DIAGNOSTIC = 512
PLAN_MODE = 0o644
WORKERS_TEXT = re.compile(r"[1-9][0-9]?")
assert WORLD_FIELDS == set(SPECS)
assert all(len(spec.states) * len(CHANNELS) == STRATA for spec in WORLDS)
assert MAX_CELLS % STRATA == 0 and STRATA <= MAX_CELLS
assert STOCK.prefix < INFLIGHT.prefix

Plan = dict[str, Any]
Inventory = list[tuple[str, int]]


def _check_path(path: object, label: str) -> Path:
    require(isinstance(path, Path), f"{label} path is not a Path")
    assert isinstance(path, Path)
    size = len(os.fsencode(path))
    require(0 < size <= MAX_PATH_BYTES, f"{label} path exceeds {MAX_PATH_BYTES} bytes")
    return path


def _tickets(value: object, spec: Spec, seen: set[str]) -> list[str]:
    where = f"plan {spec.world} tickets"
    tickets = as_list(value, where, STRATA, MAX_CELLS)
    require(len(tickets) % STRATA == 0, f"{where} do not fill whole strata")
    for position, ticket in enumerate(tickets):
        as_text(ticket, f"{where} item {position}", spec.ticket)
        require(ticket not in seen, f"{where} item {position} repeats a ticket")
        seen.add(ticket)
    return list(tickets)


def validate_plan(value: object) -> Plan:
    plan = as_record(value, "campaign plan", PLAN_FIELDS)
    require(plan["schema"] == SCHEMA, f"campaign plan schema must be {SCHEMA}")
    found = as_record(plan["registrations"], "plan registrations", WORLD_FIELDS)
    registrations = {
        spec.world: as_text(found[spec.world], f"plan {spec.world} digest", DIGEST)
        for spec in WORLDS
    }
    require(
        registrations[STOCK.world] != registrations[INFLIGHT.world],
        "plan names one registration for both worlds",
    )
    registration = as_text(plan["registration"], "plan registration digest", DIGEST)
    require(
        registration == registration_digest(registrations),
        "plan registration digest does not name its world registrations",
    )
    listed = as_record(plan["tickets"], "plan tickets", WORLD_FIELDS)
    seen: set[str] = set()
    tickets = {spec.world: _tickets(listed[spec.world], spec, seen) for spec in WORLDS}
    count = len(tickets[STOCK.world])
    require(
        len(tickets[INFLIGHT.world]) == count,
        "plan worlds differ in ticket count",
    )
    workers = as_int(plan["workers"], "plan workers", 1, min(count, MAX_WORKERS))
    assert len(seen) == len(WORLDS) * count
    frozen = {
        "schema": SCHEMA,
        "registration": registration,
        "registrations": registrations,
        "workers": workers,
        "tickets": tickets,
    }
    require(
        len(record_bytes(frozen)) <= MAX_PLAN_BYTES, "campaign plan exceeds its bound"
    )
    assert frozen == plan
    return frozen


def digest(plan: Plan) -> str:
    return registration_digest(validate_plan(plan))


def _derive(stock: dict[str, Any], inflight: dict[str, Any], workers: int) -> Plan:
    pair = {STOCK.world: stock, INFLIGHT.world: inflight}
    registrations = {world: registration_digest(reg) for world, reg in pair.items()}
    tickets = {
        world: [cell["ticket"] for cell in reg["cells"]] for world, reg in pair.items()
    }
    count = len(tickets[STOCK.world])
    as_int(workers, "workers", 1, min(count, MAX_WORKERS))
    return validate_plan(
        {
            "schema": SCHEMA,
            "registration": registration_digest(registrations),
            "registrations": registrations,
            "workers": workers,
            "tickets": tickets,
        }
    )


def build_plan(stock: Path, inflight: Path, workers: int) -> Plan:
    as_int(workers, "workers", 1, MAX_WORKERS)
    return _derive(*load_pair(stock, inflight), workers)


def bind(
    plan: Plan, stock: Path, inflight: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = validate_plan(plan)
    pair = load_pair(stock, inflight)
    require(
        _derive(*pair, plan["workers"]) == plan,
        "campaign plan differs from the plan derived from its registrations",
    )
    return pair


def load_plan(path: Path, expected: str) -> Plan:
    path = _check_path(path, "campaign plan")
    as_text(expected, "expected plan digest", DIGEST)
    try:
        record = read_record(path, MAX_PLAN_BYTES)
    except OSError as error:
        raise ValueError(
            f"campaign plan is not a readable regular non-symlink file "
            f"({error.strerror})"
        ) from None
    require(record is not None, "campaign plan does not exist")
    plan = validate_plan(record)
    require(
        registration_digest(plan) == expected,
        "campaign plan digest differs from the expected digest",
    )
    return plan


def inventory(plan: Plan) -> Inventory:
    plan = validate_plan(plan)
    workers = plan["workers"]
    rows: Inventory = []
    loads = {spec.world: [0] * workers for spec in WORLDS}
    for spec in WORLDS:
        for index, ticket in enumerate(plan["tickets"][spec.world]):
            owner = index % workers
            loads[spec.world][owner] += 1
            rows.append((ticket, owner))
    rows.sort()
    assert len(rows) == len({ticket for ticket, _ in rows})
    assert all(0 <= owner < workers for _, owner in rows)
    assert loads[STOCK.world] == loads[INFLIGHT.world]
    assert all(load > 0 for load in loads[STOCK.world])
    return rows


def write_plan(path: Path, plan: Plan) -> str:
    path = _check_path(path, "campaign plan")
    expected = digest(plan)
    write_exclusive(path, plan, PLAN_MODE)
    require(
        load_plan(path, expected) == plan,
        "written campaign plan does not reload to the built plan",
    )
    return expected


def _receipt(plan: Plan, expected: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "plan": expected,
        "registration": plan["registration"],
        "registrations": plan["registrations"],
        "workers": plan["workers"],
        "tickets": {spec.world: len(plan["tickets"][spec.world]) for spec in WORLDS},
    }


def _scrub(text: str, paths: list[Path]) -> str:
    forms = {
        form
        for path in paths
        for form in (str(path), os.path.abspath(path), os.path.realpath(path))
        if form
    }
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, "<input>")
    for spec in WORLDS:
        text = spec.ticket.sub("<ticket>", text)
    clean = "".join(char if " " <= char <= "~" else "?" for char in text)
    return clean[:MAX_DIAGNOSTIC]


def _workers(text: str) -> int:
    if WORKERS_TEXT.fullmatch(text) is None:
        raise argparse.ArgumentTypeError(f"--workers must be 1..{MAX_WORKERS}")
    return int(text)


def parser() -> argparse.ArgumentParser:
    found = argparse.ArgumentParser(
        prog="python3.12 -BS -m examples.ship_handover.campaign_plan",
        description="Freeze the public paired shipping campaign plan.",
    )
    found.add_argument("stock", type=Path, metavar="STOCK_REG")
    found.add_argument("inflight", type=Path, metavar="INFLIGHT_REG")
    found.add_argument("--workers", type=_workers, required=True, metavar="N")
    found.add_argument("--out", type=Path, required=True, metavar="PLAN")
    return found


def _reason(error: BaseException) -> str:
    if isinstance(error, OSError):
        return error.strerror or "storage failed"
    return str(error)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    inputs = [args.stock, args.inflight, args.out]
    try:
        plan = build_plan(args.stock, args.inflight, args.workers)
        expected = write_plan(args.out, plan)
    except (OSError, ValueError) as error:
        print(f"campaign_plan: {_scrub(_reason(error), inputs)}", file=sys.stderr)
        return 2
    receipt = _receipt(plan, expected)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
