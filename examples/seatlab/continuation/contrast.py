import argparse
import json
import sys
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any


def _index(report: dict[str, Any], view: str) -> dict[str, dict[str, Any]]:
    if (
        report.get("schema") != "seatlab.continuation-report/v1"
        or report.get("view") != view
    ):
        raise ValueError(f"expected the scored {view} view")
    rows = report["cohorts"]
    result = {row["key"]: row for row in rows}
    if len(rows) != 9 or len(result) != len(rows):
        raise ValueError("expected nine distinct cohorts")
    return result


def _company_data(row: dict[str, Any]) -> dict[str, tuple[str, int, float, float]]:
    return {
        run["run_id"]: (
            run["board_sha256"],
            run["public_posts"],
            run["mark60"],
            run["final"],
        )
        for run in row["runs"]
    }


def _selected(row: dict[str, Any]) -> Fraction:
    winners = row["plurality_run_ids"]
    finals = {r["run_id"]: Fraction(str(r["final"])) for r in row["runs"]}
    return sum((finals[r] for r in winners), Fraction()) / len(winners)


def compare(posts: dict[str, Any], activity: dict[str, Any]) -> dict[str, Any]:
    full, masked = _index(posts, "posts"), _index(activity, "activity")
    if (
        full.keys() != masked.keys()
        or posts["index_sha256"] != activity["index_sha256"]
    ):
        raise ValueError("different source population")
    rows, effects = [], []
    for key, p in sorted(full.items()):
        a = masked[key]
        po, ao = p["responses"], a["responses"]
        if _company_data(p) != _company_data(a) or [r["run_ids"] for r in po] != [
            r["run_ids"] for r in ao
        ]:
            raise ValueError(f"{key}: different sources or presentation order")
        complete = p["valid"] == a["valid"] == 3
        effect = _selected(p) - _selected(a) if complete else None
        if effect is not None:
            effects.append(effect)
        rows.append(
            {
                "cohort": key,
                "posts_valid": p["valid"],
                "activity_valid": a["valid"],
                "posts_winners": p["plurality_run_ids"],
                "activity_winners": a["plurality_run_ids"],
                "posts_minus_activity": None if effect is None else float(effect),
            }
        )
    positive, negative = sum(e > 0 for e in effects), sum(e < 0 for e in effects)
    n = positive + negative
    complete = len(effects) == len(rows)
    p_value = (
        Fraction(sum(comb(n, k) for k in range(positive, n + 1)), 2**n)
        if complete
        else None
    )
    mean = sum(effects, Fraction()) / len(effects) if effects else None
    passed = (
        complete
        and mean is not None
        and mean >= Fraction(3, 100)
        and p_value is not None
        and p_value <= Fraction(1, 20)
    )
    return {
        "schema": "seatlab.continuation-content/v1",
        "index_sha256": posts["index_sha256"],
        "posts_replies_sha256": posts["replies_sha256"],
        "activity_replies_sha256": activity["replies_sha256"],
        "cohorts": rows,
        "complete_pairs": len(effects),
        "positive": positive,
        "negative": negative,
        "ties": len(effects) - n,
        "mean_difference": None if mean is None else float(mean),
        "mean_difference_exact": None if mean is None else str(mean),
        "one_sided_sign_p": None if p_value is None else str(p_value),
        "verdict": "incomplete" if not complete else "pass" if passed else "fail",
    }


def _main() -> int:
    cli = argparse.ArgumentParser(
        description="Compare two trusted, scored continuation reports from the same source."
    )
    cli.add_argument("posts_report", type=Path)
    cli.add_argument("activity_report", type=Path)
    args = cli.parse_args()
    try:
        reports = []
        for path in (args.posts_report, args.activity_report):
            if path.stat().st_size > 2**20:
                raise ValueError("report exceeds 1 MiB")
            reports.append(json.loads(path.read_bytes()))
        result = compare(*reports)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        ZeroDivisionError,
        RecursionError,
    ) as exc:
        print(f"cannot compare: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
