"""Describe each Dispatch Handoff choice from verified native trial evidence.

The alternatives are optima under specific inputs, not estimates of what the
agent read or used. Pair records by seed, fingerprint, and episode ID.
"""

import argparse
import json
from pathlib import Path
from typing import Any, TypedDict

if __package__:
    from .evidence import TrialEvidence, read_trial_with_world
else:
    from evidence import TrialEvidence, read_trial_with_world


class ReportDecision(TypedDict):
    revision: int
    best: str
    report_matches_revision: bool
    choice_matches_best: bool


class Decision(TypedDict):
    trial_id: str
    condition: str
    seed: int
    id: int
    fingerprint: str
    truth_optimal: str
    chosen: str
    changed: bool
    is_optimal: bool
    highest_revision: ReportDecision | None
    last_arrival: ReportDecision | None
    direct_best: str | None
    report_rules_agree: bool | None
    nonoptimal_with_correct_current_report: bool | None
    choice_matches_revision_1: bool | None


def _report_decision(case: dict[str, Any], report: dict[str, Any]) -> ReportDecision:
    revision = report["revision"]
    available = report["available"]
    truth = case["before" if revision == 1 else "after"]
    best = max(available, key=case["utilities"].__getitem__)
    return {
        "revision": revision,
        "best": best,
        "report_matches_revision": available == truth,
        "choice_matches_best": case["chosen"] == best,
    }


def describe_decisions(trial: TrialEvidence, world: dict[str, Any]) -> list[Decision]:
    """Compare choices using the receipt and exact dossier from the trial reader.

    Null report comparisons mean the corresponding input is not visible. In
    pooled trials, direct_best describes the world's direct availability instead.
    Agreement between report rules does not identify which rule the agent used.
    """
    condition = trial["condition"]
    decisions: list[Decision] = []
    for case, outcome in zip(world["cases"], trial["score"]["rows"], strict=True):
        truth_optimal = max(case["after"], key=case["utilities"].__getitem__)
        highest = last = None
        visible = case["visible"]
        if visible:
            newest = max(visible, key=lambda report: report["revision"])
            highest = _report_decision(case, newest)
            arrival = visible[-1]
            last = highest if newest is arrival else _report_decision(case, arrival)

        decisions.append(
            {
                "trial_id": trial["trial_id"],
                "condition": condition,
                "seed": trial["seed"],
                "id": outcome["id"],
                "fingerprint": trial["fingerprint"],
                "truth_optimal": truth_optimal,
                "chosen": case["chosen"],
                "changed": outcome["changed"],
                "is_optimal": outcome["optimal"],
                "highest_revision": highest,
                "last_arrival": last,
                "direct_best": truth_optimal if condition == "pooled" else None,
                "report_rules_agree": (
                    highest["best"] == last["best"]
                    if highest is not None and last is not None
                    else None
                ),
                "nonoptimal_with_correct_current_report": (
                    highest["report_matches_revision"] and not outcome["optimal"]
                    if highest is not None and highest["revision"] == 2
                    else None
                ),
                "choice_matches_revision_1": (
                    last["choice_matches_best"]
                    if last is not None and last["revision"] == 1
                    else None
                ),
            }
        )
    return decisions


def read_decisions(path: Path | str) -> list[Decision]:
    """Read one native trial and return only the decision comparisons."""
    return describe_decisions(*read_trial_with_world(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial_dirs", metavar="TRIAL", type=Path, nargs="+")
    args = parser.parse_args()
    decisions = []
    seen = set()
    for position, root in enumerate(args.trial_dirs, 1):
        try:
            rows = read_decisions(root)
        except (OSError, ValueError):
            parser.exit(
                1, f"trial {position}: invalid or unreadable native trial evidence\n"
            )
        trial_id = rows[0]["trial_id"]
        if trial_id in seen:
            parser.exit(1, f"trial {position}: duplicate native trial\n")
        seen.add(trial_id)
        decisions.extend(rows)
    for row in decisions:
        print(json.dumps(row, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
