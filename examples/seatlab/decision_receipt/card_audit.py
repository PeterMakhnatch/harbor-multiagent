from typing import Any

from . import card_inputs

SCHEMA = "harbor.shipping.decision-card-pairs/v1"


def _pair(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    a, b = left["card"], right["card"]
    fixed_actions = a["events"] == b["events"] and a["citation"] == b["citation"]
    peers = (
        fixed_actions
        and a["world"] == b["world"] == "inflight"
        and a["channel"] == b["channel"]
        and {a["state"], b["state"]} == {"fails", "settles"}
    )
    grade_matches = ledger_matches = grade_flips = 0
    if peers:
        for source, observed in ((a, right), (b, left)):
            twin = source["twin"]
            other = observed["card"]
            grade_matches += int(
                twin["grade"] == other["final"]["grade"] == observed["native_grade"]
            )
            ledger_matches += int(
                twin["state"] == other["state"]
                and twin["parcels"] == other["final"]["parcels"]
                and twin["outcome"] == other["final"]["outcome"]
            )
            grade_flips += int(twin["grade"] != source["final"]["grade"])
    return {
        "tickets": [left["ticket"], right["ticket"]],
        "same_accepted_actions": fixed_actions,
        "directions_checked": 2 if peers else 0,
        "grade_matches": grade_matches,
        "ledger_matches": ledger_matches,
        "grade_flips": grade_flips,
    }


def audit() -> dict[str, Any]:
    rows = [_pair(left, right) for left, right in card_inputs.observed_pairs()]
    checks = sum(row["directions_checked"] for row in rows)
    matches = sum(row["grade_matches"] for row in rows)
    ledgers = sum(row["ledger_matches"] for row in rows)
    flips = sum(row["grade_flips"] for row in rows)
    assert (
        0
        <= min(matches, ledgers, flips)
        <= max(matches, ledgers, flips)
        <= checks
        <= 16
    )
    return {
        "schema": SCHEMA,
        "pairs": len(rows),
        "directions_checked": checks,
        "grade_matches": matches,
        "ledger_matches": ledgers,
        "grade_flips": flips,
        "grade_stable": checks - flips,
        "paired_tickets": rows,
        "verdict": "matched"
        if len(rows) == 8 and matches == ledgers == checks == 16
        else "mismatch",
        "scope": "8 pairs of stored scripted controls. Two directions per pair. The calculated grades are not additional trials.",
    }
