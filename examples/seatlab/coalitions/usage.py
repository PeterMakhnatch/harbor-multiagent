import argparse
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from ..committee.accounting import _direct_usage, _spend_totals
from .protocol import CELLS


def _object(path: Path) -> dict:
    if path.resolve() != path or not path.is_file():
        raise ValueError("expected original regular file")
    return json.loads(path.read_bytes())


def _tokens(call: dict) -> dict | None:
    if call["estimated_usd_high"] is None:
        if call["unsettled_hold_usd"] != "0.010000000":
            raise ValueError("unexpected unresolved hold")
        return None
    hit, miss, output = (call[k] for k in ("cache_hit", "cache_miss", "output"))
    if any(type(value) is not int or value < 0 for value in (hit, miss, output)):
        raise ValueError("missing provider token evidence")
    base = Decimal(3 * hit + 150 * miss + 600 * output) / Decimal(10**9)
    if (
        Decimal(call["estimated_usd_low"]) != base
        or Decimal(call["estimated_usd_high"]) not in (base, 2 * base)
        or Decimal(call["unsettled_hold_usd"]) != 0
    ):
        raise ValueError("provider estimate does not match the recorded tokens")
    return _direct_usage(call)


def _totals(calls: list[dict], registered: int) -> dict:
    totals = _spend_totals(calls)
    totals["no_reservation"] = registered - len(calls)
    if totals["no_reservation"]:
        totals["exact_list_price_usd"] = None
        totals["upper_usd_including_holds"] = None
    return totals


def _cell(root: Path, cell: dict, calls: dict, seen: set[UUID]) -> dict:
    name = cell["cell_id"]
    directory = root / "cells" / name / "trials" / name
    path = directory / "result.json"
    if path.is_file():
        native = _object(path)
        identity = UUID(native["id"])
        if (
            identity.version != 4
            or identity in seen
            or str(identity) != native["id"]
            or native["trial_name"] != name
            or native["trial_uri"] != directory.as_uri()
        ):
            raise ValueError("unexpected native result identity")
        seen.add(identity)
        seats = {
            seat["seat"]: seat.get("agent_result") for seat in native["seat_results"]
        }
    else:
        seats = {}
    rows = []
    present = []
    for position in ("proposer", "responder"):
        role = cell[position]
        for phase in (position, "ballot"):
            run = f"{name}-{phase}"
            call = calls.pop((run, role), None)
            match = None
            if call is not None:
                present.append(call)
                usage = _tokens(call)
                metadata = (seats.get(role) or {}).get("metadata") or {}
                receipt = metadata.get("turns", {}).get(phase)
                if usage is not None and receipt is not None:
                    match = (
                        metadata.get("role") == role
                        and metadata.get("run_id") == name
                        and receipt.get("ledger_run_id") == run
                        and receipt.get("ledger_role") == role
                        and receipt.get("served_model") == call["served_model"]
                        and all(receipt.get(k[2:]) == v for k, v in usage.items())
                    )
            rows.append(
                {"run": run, "role": role, "ledger": call, "native_tokens_match": match}
            )
    totals = _totals(present, len(rows))
    return {
        "cell": name,
        "registered_calls": len(rows),
        "ledger_entries": len(present),
        "totals": totals,
        "calls": rows,
    }


def join(study: Path, meter: Path) -> dict:
    evidence = _object(meter)
    if (
        evidence.get("schema") != "seatlab.direct-flash-cost/v1"
        or evidence.get("currency") != "USD"
    ):
        raise ValueError("use the scoped direct Flash ledger export")
    data = evidence["calls"]
    fields = {
        "run",
        "role",
        "served_model",
        "cache_hit",
        "cache_miss",
        "output",
        "estimated_usd_low",
        "estimated_usd_high",
        "unsettled_hold_usd",
    }
    if not isinstance(data, list) or any(
        not isinstance(row, dict) or row.keys() != fields for row in data
    ):
        raise ValueError("unexpected public provider usage fields")
    calls = {(row["run"], row["role"]): row for row in data}
    if len(calls) != len(data) or len(calls) > 16:
        raise ValueError("duplicate or unexpected reservation")
    seen: set[UUID] = set()
    cells = [_cell(study, cell, calls, seen) for cell in CELLS.values()]
    if calls:
        raise ValueError("ledger includes calls outside this pilot")
    return {
        "schema": "seatlab.coalition-usage/v1",
        "price_source": evidence["price_source"],
        "currency": "USD",
        "cells": cells,
        "study": _totals(data, 16),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Join original committee token receipts to a scoped ledger export."
    )
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--meter", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            join(args.study.resolve(strict=True), args.meter.resolve(strict=True)),
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
