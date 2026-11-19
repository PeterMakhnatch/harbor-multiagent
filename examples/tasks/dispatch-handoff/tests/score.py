"""Independently verify and score a completed Dispatch Handoff dossier."""

import argparse
import json
from pathlib import Path
from typing import Any, TypedDict, cast

_BAYS = frozenset(("A", "B", "C", "D"))
_DELIVERY = {
    "silent": (),
    "stale": (1,),
    "fresh": (2,),
    "reordered": (2, 1),
    "pooled": (),
}
_MISSING = object()


class RowScore(TypedDict):
    id: int
    optimal: bool
    feasible: bool
    changed: bool


class Score(TypedDict):
    episodes: int
    optimal: int
    feasible: int
    changed: int
    rows: list[RowScore]


def _mask(value: object, field: str) -> set[str]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field}: expected two distinct bays in canonical order")
    first, second = value
    if (
        not isinstance(first, str)
        or not isinstance(second, str)
        or first not in _BAYS
        or second not in _BAYS
        or first >= second
    ):
        raise ValueError(f"{field}: expected two distinct bays in canonical order")
    return {first, second}


def _report(value: Any, revision: int, field: str) -> None:
    if (
        not isinstance(value, dict)
        or value.keys() != {"revision", "available"}
        or type(value["revision"]) is not int
        or value["revision"] != revision
    ):
        raise ValueError(f"{field}: expected a revision {revision} report")
    _mask(value["available"], field)


def score(evidence: dict[str, Any]) -> Score:
    """Reject inconsistent evidence; score choices against true final availability."""
    if not isinstance(evidence, dict) or evidence.get("schema") != "seatlab.world/v1":
        raise ValueError("expected a seatlab.world/v1 dossier")
    if evidence.get("complete") is not True:
        raise ValueError("the world dossier is incomplete")

    settings = evidence.get("settings")
    if (
        not isinstance(settings, dict)
        or settings.get("schema") != "seatlab.scenario/v1"
    ):
        raise ValueError("expected seatlab.scenario/v1 settings")
    condition = settings.get("condition")
    if not isinstance(condition, str) or condition not in _DELIVERY:
        raise ValueError("unknown condition")
    if type(settings.get("seed")) is not int:
        raise ValueError("expected an integer seed")
    episodes = settings.get("episodes")
    if type(episodes) is not int or not 1 <= episodes <= 256:
        raise ValueError("expected 1 to 256 episodes")
    cases = evidence.get("cases")
    if not isinstance(cases, list) or len(cases) != episodes:
        raise ValueError("case count does not match settings")

    rows: list[RowScore] = []
    for case_id, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case {case_id}: expected an object")
        reported_id = case.get("id")
        if type(reported_id) is not int or reported_id != case_id:
            raise ValueError(f"case {case_id}: missing or out of order")

        utilities = case.get("utilities")
        if (
            not isinstance(utilities, dict)
            or utilities.keys() != _BAYS
            or any(type(value) is not int for value in utilities.values())
            or set(utilities.values()) != {1, 2, 3, 4}
        ):
            raise ValueError(f"case {case_id}: utilities must permute 1 to 4")
        ranks = cast("dict[str, int]", utilities)
        before = _mask(case.get("before"), f"case {case_id}: before")
        after = _mask(case.get("after"), f"case {case_id}: after")
        if len(before & after) != 2 - (case_id % 2):
            raise ValueError(
                f"case {case_id}: availability does not match change split"
            )

        if condition == "pooled":
            if case.get("reports", _MISSING) is not None:
                raise ValueError(f"case {case_id}: pooled must have null reports")
            reports = []
        else:
            reports = case.get("reports")
            if not isinstance(reports, list) or len(reports) != 2:
                raise ValueError(f"case {case_id}: expected two accepted reports")
            for revision, report in enumerate(reports, 1):
                _report(report, revision, f"case {case_id}: accepted report")
        delivery = _DELIVERY[condition]
        visible = case.get("visible")
        if not isinstance(visible, list) or len(visible) != len(delivery):
            raise ValueError(
                f"case {case_id}: visible bulletin count does not match delivery"
            )
        for revision, bulletin in zip(delivery, visible, strict=True):
            _report(bulletin, revision, f"case {case_id}: visible bulletin")
            if bulletin != reports[revision - 1]:
                raise ValueError(
                    f"case {case_id}: visible bulletin differs from accepted report"
                )

        chosen = case.get("chosen")
        if not isinstance(chosen, str) or chosen not in _BAYS:
            raise ValueError(f"case {case_id}: missing or invalid bay choice")
        rows.append(
            {
                "id": case_id,
                "optimal": chosen == max(after, key=ranks.__getitem__),
                "feasible": chosen in after,
                "changed": before != after,
            }
        )

    return {
        "episodes": episodes,
        "optimal": sum(row["optimal"] for row in rows),
        "feasible": sum(row["feasible"] for row in rows),
        "changed": sum(row["changed"] for row in rows),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dossier", type=Path)
    args = parser.parse_args()
    try:
        result = score(json.loads(args.dossier.read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{args.dossier}: {exc}\n")
    print(json.dumps({"reward": result["optimal"] / result["episodes"]}))


if __name__ == "__main__":
    main()
