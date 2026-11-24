from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from ..market_handoff.timing_messages import REASON_MAX_CHARS, read_sales
from . import panel


def explain(rows: list[dict[str, Any]], target: int) -> dict[str, Any]:
    lengths: list[int] = []
    rejected: list[dict[str, int]] = []
    other = Counter[str]()
    matched = decisions = receipt_count = 0
    for row in rows:
        if row["target"] != target:
            continue
        sales = row["sales"]
        if not sales or not sales["verified"]:
            continue
        receipt_count += 1
        try:
            parsed = read_sales(sales["reply"])
        except ValueError as error:
            if str(error) == "reason_length":
                reason = json.loads(sales["reply"])["reason"]
                if isinstance(reason, str) and len(reason) > REASON_MAX_CHARS:
                    rejected.append({"world": row["world"], "chars": len(reason)})
                    continue
            other[type(error).__name__] += 1
            continue
        if row["sales_status"] != "accepted":
            continue
        lengths.append(len(parsed["reason"]))
        if row["delivered"] is True and row["finance_pick"] is not None:
            decisions += 1
            matched += parsed["pick"] == row["finance_pick"]
    return {
        "target": target,
        "verified_sales_replies": receipt_count,
        "accepted_reports": len(lengths),
        "accepted_reason_chars_min_median_max": (
            [min(lengths), median(lengths), max(lengths)] if lengths else None
        ),
        "accepted_above_prompt_target": sum(n > target for n in lengths),
        "over_wire_limit": sorted(rejected, key=lambda item: item["world"]),
        "other_parse_failures": dict(other),
        "finance_choices_with_delivered_advice": decisions,
        "finance_choices_matching_delivered_advice": matched,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trace original verified reports from generation to the finance choice."
    )
    parser.add_argument("rows", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.rows.read_bytes())
    panel.summarize(rows)
    result = {
        "schema": "seatlab.report-budget-diagnosis/v1",
        "scope": "Descriptive. Agreement is not decision accuracy or profit.",
        "arms": [explain(rows, target) for target in (100, 160)],
    }
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
