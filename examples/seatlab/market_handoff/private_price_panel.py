from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

from examples.seatlab.market_handoff import decisions

Record = dict[str, Any]
Company = tuple[str, int]
ROW_SCHEMA = "seatlab.private-prices/v1"
BASELINE: tuple[Company, ...] = (
    ("target", 1),
    ("target", 7),
    ("donor", 9),
    ("donor", 11),
)
CHARGES = {"C": 0, "T": 60_000}
SEED = 2_000_000.0
TOP = 32.0
FINAL_DAY = 1095
LINES = 10
CLIP = (0.85, 1.15)
MIN_GAIN = Fraction(3, 100)
MIN_NET_WINS = 3
COMMON = {
    "schema",
    "mode",
    "arm",
    "world",
    "source",
    "numpy_version",
    "prefix_fingerprint",
    "prefix_sha256",
    "payoffs_sha256",
}
BINDINGS = (
    "source",
    "numpy_version",
    "prefix_fingerprint",
    "prefix_sha256",
    "payoffs_sha256",
)
GRADE_KEYS = {"world_score", "multiple"}
POLICY_KEYS = {
    "charge",
    "value",
    "bankrupt",
    "bankrupt_day",
    "final_day",
    "gross",
    "charged",
    "multipliers",
}
MULTIPLIER_KEYS = {"n", "mean", "min", "max", "clipped", "sha256"}
ROW_NAME = re.compile(r"(baseline|exact)-(target|donor)-(\d{2})\.json")
COMPANIES: tuple[Company, ...] = tuple(
    (arm, world) for arm in decisions.ARMS for world in range(decisions.WORLDS)
)

assert set(BASELINE) <= set(COMPANIES) and len(COMPANIES) == 24


def finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def hex64(value: Any) -> bool:
    return isinstance(value, str) and decisions.HEX64.fullmatch(value) is not None


def load(path: Path) -> tuple[Any, str]:
    if path.is_symlink() or not path.is_file():
        decisions.fail(f"{path}: missing or not a regular file")
    data = path.read_bytes()
    if len(data) > decisions.MAX_FILE_BYTES:
        decisions.fail(f"{path}: larger than {decisions.MAX_FILE_BYTES} bytes")
    return decisions.decode(data.decode("utf-8")), hashlib.sha256(data).hexdigest()


def grade(value: float, bankrupt: bool, charge: int) -> Record:
    m = value / SEED - charge / SEED
    score = (
        0.0 if bankrupt or m <= 0 else min(1.0, max(0.0, math.log(m) / math.log(TOP)))
    )
    return {"world_score": score, "multiple": m}


def graded(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and item.keys() == GRADE_KEYS
        and all(finite(item[key]) for key in GRADE_KEYS)
        and 0 <= item["world_score"] <= 1
    )


def published(root: Path, arm: str, world: int, cell: Record) -> Record:
    path = root / f"{arm}-{world:02d}.payoffs.json"
    record, digest = load(path)
    if not isinstance(record, dict) or record.keys() != decisions.PAYOFF_KEYS:
        decisions.fail(f"{path}: expected exactly {sorted(decisions.PAYOFF_KEYS)}")
    options = record["options"] if isinstance(record["options"], list) else []
    decisions.require(
        path,
        {
            "schema": record["schema"] == "seatlab.market-payoffs/v1",
            "source": record["source"] == decisions.PINNED_SOURCE,
            "world": type(record["world"]) is int and record["world"] == world,
            "prefix_fingerprint": record["prefix_fingerprint"]
            == cell["prefix_fingerprint"],
            "options": [
                item.get("pick") if isinstance(item, dict) else None for item in options
            ]
            == list(decisions.PICKS)
            and all(
                item.keys() == decisions.OPTION_KEYS
                and graded({key: item[key] for key in GRADE_KEYS})
                for item in options
            ),
        },
    )
    _, prefix_digest = load(root / f"{arm}-{world:02d}.prefix.json")
    k = {key: options[0][key] for key in GRADE_KEYS}
    return {
        "cell": cell,
        "prefix_sha256": prefix_digest,
        "payoffs_sha256": digest,
        "k": k,
    }


def cohort(root: Path) -> dict[Company, Record]:
    cells = decisions.load_cells(root)
    if len({cell["prefix_fingerprint"] for cell in cells.values()}) != len(COMPANIES):
        decisions.fail("registered prefix fingerprints repeat across companies")
    return {company: published(root, *company, cells[company]) for company in COMPANIES}


def policy(path: Path, name: str, item: Any) -> Record:
    if not isinstance(item, dict) or item.keys() != POLICY_KEYS:
        decisions.fail(f"{path}: policy {name} needs exactly {sorted(POLICY_KEYS)}")
    lines, bankrupt, day = item["multipliers"], item["bankrupt"], item["bankrupt_day"]
    shape = (
        isinstance(lines, dict)
        and lines.keys() == MULTIPLIER_KEYS
        and all(finite(lines[key]) for key in ("mean", "min", "max"))
        and lines["n"] == LINES
    )
    decisions.require(
        path,
        {
            f"{name}.charge": type(item["charge"]) is int
            and item["charge"] == CHARGES[name],
            f"{name}.value": finite(item["value"]),
            f"{name}.bankrupt": type(bankrupt) is bool
            and (
                day is None
                if not bankrupt
                else type(day) is int and 0 <= day <= FINAL_DAY
            ),
            f"{name}.final_day": type(item["final_day"]) is int
            and 0 <= item["final_day"] <= FINAL_DAY,
            f"{name}.grades": graded(item["gross"]) and graded(item["charged"]),
            f"{name}.multipliers": shape
            and type(lines["n"]) is int
            and type(lines["clipped"]) is int
            and 0 <= lines["clipped"] <= LINES
            and hex64(lines["sha256"])
            and CLIP[0] <= lines["min"] <= lines["max"] <= CLIP[1]
            and lines["min"] - 1e-12 <= lines["mean"] <= lines["max"] + 1e-12,
        },
    )
    decisions.require(
        path,
        {
            f"{name}.gross_is_stock_score": item["gross"]
            == grade(item["value"], bankrupt, 0),
            f"{name}.charged_is_stock_score": item["charged"]
            == grade(item["value"], bankrupt, item["charge"]),
        },
    )
    return item


def row(
    path: Path, mode: str, company: Company, registered: Record
) -> tuple[Record, list[str]]:
    record, _ = load(path)
    extra = {"k"} if mode == "baseline" else {"policies"}
    if not isinstance(record, dict) or record.keys() != COMMON | extra:
        decisions.fail(f"{path}: expected exactly {sorted(COMMON | extra)}")
    decisions.require(
        path,
        {
            "schema": record["schema"] == ROW_SCHEMA,
            "mode": record["mode"] == mode,
            "arm": record["arm"] == company[0],
            "world": type(record["world"]) is int and record["world"] == company[1],
            "numpy_version": isinstance(record["numpy_version"], str),
            "source": isinstance(record["source"], str),
            "hashes": all(hex64(record[key]) for key in BINDINGS[2:]),
        },
    )
    cell = registered["cell"]
    want = {
        "source": decisions.PINNED_SOURCE,
        "numpy_version": cell["numpy_version"],
        "prefix_fingerprint": cell["prefix_fingerprint"],
        "prefix_sha256": registered["prefix_sha256"],
        "payoffs_sha256": registered["payoffs_sha256"],
    }
    unbound = [key for key in BINDINGS if record[key] != want[key]]
    if mode == "baseline":
        decisions.require(path, {"k": graded(record["k"])})
        unbound += [] if record["k"] == registered["k"] else ["k_payoff"]
        return record, unbound
    policies = record["policies"]
    if not isinstance(policies, dict) or policies.keys() != CHARGES.keys():
        decisions.fail(f"{path}: policies must be exactly {sorted(CHARGES)}")
    return {name: policy(path, name, policies[name]) for name in CHARGES}, unbound


def collect(
    rowdir: Path, registered: dict[Company, Record]
) -> tuple[Record, Record, Record]:
    found: dict[str, dict[Company, Record]] = {"baseline": {}, "exact": {}}
    unbound: dict[str, dict[str, list[str]]] = {"baseline": {}, "exact": {}}
    digests: Record = {}
    for path in sorted(rowdir.iterdir()):
        match = ROW_NAME.fullmatch(path.name)
        company = (match.group(2), int(match.group(3))) if match else None
        mode = match.group(1) if match else ""
        if company not in (BASELINE if mode == "baseline" else COMPANIES):
            decisions.fail(f"{path}: not a registered row name")
        assert company is not None and company not in found[mode]
        found[mode][company], bad = row(path, mode, company, registered[company])
        if bad:
            unbound[mode][path.name] = bad
        digests[path.name] = load(path)[1]
    return found, unbound, digests


def stage(
    found: dict[Company, Record],
    unbound: dict[str, list[str]],
    expected: tuple[Company, ...],
) -> Record:
    missing = [
        f"{arm}-{world:02d}" for arm, world in expected if (arm, world) not in found
    ]
    return {
        "required": len(expected),
        "present": len(found),
        "missing": missing,
        "unbound": unbound,
        "complete": not missing and not unbound,
    }


def scored(
    registered: dict[Company, Record], exact: dict[Company, Record]
) -> list[Record]:
    cells = []
    for arm, world in COMPANIES:
        cell, policies = registered[(arm, world)]["cell"], exact[(arm, world)]
        k = registered[(arm, world)]["k"]
        cells.append(
            {
                "arm": arm,
                "world": world,
                "shape": cell["shape"],
                "terminal": cell["terminal"],
                "K": {"gross": k, "charged": k, "bankrupt": None, "multipliers": None},
                **{
                    name: {
                        key: item[key]
                        for key in ("gross", "charged", "bankrupt", "multipliers")
                    }
                    for name, item in policies.items()
                },
            }
        )
    return cells


def contest(cells: list[Record], comparator: str) -> tuple[Record, Fraction]:
    pairs = [
        (
            Fraction(cell["T"]["charged"]["world_score"]),
            Fraction(cell[comparator]["charged"]["world_score"]),
        )
        for cell in cells
    ]
    diffs = [t - f for t, f in pairs]
    wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    gain = sum(diffs, Fraction(0)) / len(diffs)
    return {
        "n": len(diffs),
        "comparator": comparator,
        "mean_diff": float(gain),
        "wins": wins,
        "losses": losses,
        "ties": len(diffs) - wins - losses,
        "net_wins": wins - losses,
        "n0": sum(t == 0 and f == 0 for t, f in pairs),
    }, gain


def averages(cells: list[Record]) -> Record:
    def mean(name: str, side: str, key: str) -> float:
        return float(
            sum((Fraction(cell[name][side][key]) for cell in cells), Fraction(0))
            / len(cells)
        )

    return {
        name: {
            side: {key: mean(name, side, key) for key in sorted(GRADE_KEYS)}
            for side in ("gross", "charged")
        }
        | {
            "zero_world_score": sum(
                cell[name]["charged"]["world_score"] == 0 for cell in cells
            )
        }
        | (
            {}
            if name == "K"
            else {"bankrupt": sum(cell[name]["bankrupt"] for cell in cells)}
        )
        for name in ("K", "C", "T")
    }


def clips(cells: list[Record]) -> Record:
    return {
        name: {
            "lines": LINES * len(cells),
            "clipped_lines": sum(
                cell[name]["multipliers"]["clipped"] for cell in cells
            ),
            "companies_clipped": sum(
                cell[name]["multipliers"]["clipped"] > 0 for cell in cells
            ),
            "min": min(cell[name]["multipliers"]["min"] for cell in cells),
            "max": max(cell[name]["multipliers"]["max"] for cell in cells),
            "mean_of_means": sum(cell[name]["multipliers"]["mean"] for cell in cells)
            / len(cells),
        }
        for name in CHARGES
    }


def measure(registered: dict[Company, Record], exact: dict[Company, Record]) -> Record:
    cells = scored(registered, exact)
    free = {
        name: sum(
            (Fraction(cell[name]["charged"]["world_score"]) for cell in cells),
            Fraction(0),
        )
        for name in ("K", "C")
    }
    comparator = "C" if free["C"] > free["K"] else "K"
    primary, gain = contest(cells, comparator)
    passed = gain >= MIN_GAIN and primary["net_wins"] >= MIN_NET_WINS
    return {
        "comparator": comparator,
        "comparator_rule": "higher_panel_mean_of_K_C_K_on_tie",
        "primary": primary
        | {
            "min_mean_diff": float(MIN_GAIN),
            "min_net_wins": MIN_NET_WINS,
            "passed": passed,
        },
        "averages": averages(cells),
        "clips": clips(cells),
        "secondary_by_arm": {
            arm: contest([c for c in cells if c["arm"] == arm], comparator)[0]
            for arm in decisions.ARMS
        },
        "companies": cells,
    }


def panel(data: Path, rowdir: Path) -> Record:
    registered = cohort(data)
    found, unbound, digests = collect(rowdir, registered)
    baseline = stage(found["baseline"], unbound["baseline"], BASELINE)
    exact = stage(found["exact"], unbound["exact"], COMPANIES)
    report: Record = {
        "schema": "seatlab.private-price-panel/v1",
        "source": decisions.PINNED_SOURCE,
        "stage": "exact_C_T_before_S",
        "s_run": False,
        "baseline": baseline,
        "exact": exact,
        "rows_sha256": digests,
        "registered_sha256": decisions.sha(
            decisions.canonical(
                {
                    f"{a}-{w:02d}": [
                        registered[(a, w)]["prefix_sha256"],
                        registered[(a, w)]["payoffs_sha256"],
                    ]
                    for a, w in COMPANIES
                }
            )
        ),
    }
    if not baseline["complete"]:
        return report | {"decision": "blocked_baseline_replay", "measured": None}
    if not exact["complete"]:
        return report | {"decision": "incomplete_exact_panel", "measured": None}
    measured = measure(registered, found["exact"])
    go = measured["primary"]["passed"]
    return report | {
        "decision": "run_S_next" if go else "stop_before_S",
        "measured": measured,
        "reading": "T clears this owner-only screen on 24 reused companies; "
        "S is next, no agent effect is measured"
        if go
        else "This T rule misses the screen on 24 reused companies; not an upper "
        "bound on pricing policies and not an agent experiment; do not run S",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -BS -m examples.seatlab.market_handoff.private_price_panel"
    )
    parser.add_argument("data", type=Path)
    parser.add_argument("rowdir", type=Path)
    args = parser.parse_args(argv)
    try:
        report = panel(args.data, args.rowdir)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    print(decisions.canonical(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
