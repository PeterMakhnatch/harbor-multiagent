import argparse
import json
import os
import stat
from importlib import import_module
from pathlib import Path
from typing import Any

from ..native_study import record_bytes
from .exhibit_authored import WORLD, at, require
from .exhibit_enroll import target, write_private


def development_cut() -> dict[str, Any]:
    case = import_module(f"{WORLD}.cases").make_case(7, "coupling", layout="a")
    session = import_module(f"{WORLD}.engine").Session(case, "authored")
    loader = session.view("loader")
    session.apply(
        "loader", {"op": "request", "slips": [b["id"] for b in loader["briefs"]]}
    )
    registrar = session.view("registrar")
    heavy = sorted(
        (b for b in registrar["briefs"] if b["weight"] == 4), key=lambda b: b["id"]
    )
    require(len(heavy) == 2, "expected two heavy briefs")
    sources = [
        [at(registrar["cards"], item, b["epoch"]) for item in b["items"]] for b in heavy
    ]
    notes = [
        {
            "epoch": b["epoch"],
            "keys": sorted(row["value"] for row in pair),
            "weight": b["weight"],
        }
        for b, pair in zip(heavy, sources, strict=True)
    ]
    session.apply(
        "registrar",
        {"op": "file", "ids": [row["id"] for row in sources[0]], "notes": notes},
    )
    depot = session.view("depot")
    visible = {(row["key"], row["epoch"]) for row in depot["agenda"]}
    other = [
        row
        for row in depot["bundles"]
        if all((key, row["epoch"]) not in visible for key in row["keys"])
    ]
    require(
        len(other) == 1, "expected one equally weighted pair without a filed source"
    )
    row = other[0]
    session.apply(
        "depot",
        {
            "op": "file",
            "ids": [at(depot["cards"], key, row["epoch"])["id"] for key in row["keys"]],
        },
    )
    return session.snapshot()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a scripted development cut, never a native model trial."
    )
    parser.add_argument("new_cut", type=Path)
    args = parser.parse_args(argv)
    try:
        path = target(args.new_cut)
        info = path.parent.stat()
        require(
            info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "use a private directory you own",
        )
        write_private(path, record_bytes(development_cut()))
    except (OSError, ValueError):
        parser.exit(
            2,
            "Expected a new path outside all checkouts in a private directory you own.\n",
        )
    print(
        json.dumps(
            {
                "schema": "seatlab.exhibit-example/v1",
                "source": "scripted_development_fixture",
                "model_calls": 0,
                "native_team_runs": 0,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
