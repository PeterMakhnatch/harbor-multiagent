from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any, NamedTuple

_SCHEMA = "seatlab.confirmation-native/v1"
_DEFAULT = Path(__file__).resolve().parent / "results" / "live-confirmation.json"
_STUDIES = {
    "notices": (range(6801, 6809), "count", "named", 3),
    "calendar_answers": (range(8), "withheld", "visible", 3),
    "dispatch_answers": (range(6901, 6909), "withheld", "visible", 4),
}
_TRIAL_REF = re.compile(r"[0-9a-f]{64}")
_ROW_FIELDS = {
    "world",
    "arm",
    "reward",
    "trial_ref",
    "complete",
    "settled_calls",
    "held_calls",
}
_ALPHA = Fraction(1, 20)
_SIXTH_ROUNDING = Fraction(1, 10**15)
_SIGN_POLICY = (
    "One-sided exact sign test on behavior-complete pairs. Omit ties. "
    "Use p=1 when there are no non-tied complete pairs. "
    "If any registered pair is incomplete, p is descriptive only."
)
_BOUNDS_POLICY = (
    "Full-register bounds, not a confidence interval. "
    "Keep all observed native rewards fixed. "
    "Each missing reward can be anywhere from 0 to 1."
)


class _Arm(NamedTuple):
    reward: Fraction | None
    trial_ref: str | None
    complete: bool
    settled_calls: int
    held_calls: int


def _score(value: object, study: str, where: str) -> Fraction | None:
    if value is None:
        return None
    if type(value) is int:
        decimal = Decimal(value)
    elif isinstance(value, (float, Decimal)):
        decimal = Decimal(str(value))
    else:
        raise ValueError(f"{where}: reward must be a number or null")
    if not decimal.is_finite() or not 0 <= decimal <= 1:
        raise ValueError(f"{where}: reward must be finite and in [0, 1]")
    if study == "calendar_answers" and decimal not in (0, 1):
        raise ValueError(f"{where}: calendar reward must be 0 or 1")
    score = Fraction(decimal)
    if study in ("notices", "dispatch_answers"):
        sixth = Fraction(round(score * 6), 6)
        if abs(score - sixth) > _SIXTH_ROUNDING:
            raise ValueError(f"{where}: reward must be a multiple of 1/6")
        return sixth
    return score


def _trial_ref(value: object, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _TRIAL_REF.fullmatch(value) is None:
        raise ValueError(f"{where}: trial_ref must be lowercase SHA-256 or null")
    return value


def _call_counts(
    settled: object, held: object, scheduled: int, complete: bool, where: str
) -> tuple[int, int]:
    if type(settled) is not int or type(held) is not int or settled < 0 or held < 0:
        raise ValueError(
            f"{where}: settled_calls and held_calls must be nonnegative integers"
        )
    if settled + held > scheduled:
        raise ValueError(f"{where}: settled_calls + held_calls exceeds {scheduled}")
    if complete and settled != scheduled:
        raise ValueError(
            f"{where}: a complete arm requires {scheduled} settled calls and no holds"
        )
    return settled, held


def _registered(
    rows: object, study: str, seen: set[str]
) -> dict[tuple[int, str], _Arm]:
    worlds, control, treatment, scheduled = _STUDIES[study]
    if not isinstance(rows, list) or len(rows) != 2 * len(worlds):
        raise ValueError(f"{study}: expected exactly {2 * len(worlds)} rows")
    cells: dict[tuple[int, str], _Arm] = {}
    for index, row in enumerate(rows):
        where = f"{study}[{index}]"
        if not isinstance(row, dict) or not _ROW_FIELDS <= row.keys():
            raise ValueError(f"{where}: expected {', '.join(sorted(_ROW_FIELDS))}")
        world, arm = row["world"], row["arm"]
        if (
            type(world) is not int
            or world not in worlds
            or arm not in (control, treatment)
        ):
            raise ValueError(f"{where}: invalid world or arm")
        key = (world, arm)
        if key in cells:
            raise ValueError(f"{where}: duplicate world and arm {key}")
        complete = row["complete"]
        if type(complete) is not bool:
            raise ValueError(f"{where}: complete must be a boolean")
        trial_ref = _trial_ref(row["trial_ref"], where)
        if trial_ref is not None:
            if trial_ref in seen:
                raise ValueError(f"{where}: duplicate trial_ref {trial_ref}")
            seen.add(trial_ref)
        reward = _score(row["reward"], study, where)
        if reward is not None and trial_ref is None:
            raise ValueError(f"{where}: an observed reward requires a trial_ref")
        if complete and reward is None:
            raise ValueError(f"{where}: a complete arm requires a reward and trial_ref")
        settled, held = _call_counts(
            row["settled_calls"], row["held_calls"], scheduled, complete, where
        )
        cells[key] = _Arm(reward, trial_ref, complete, settled, held)
    return cells


def _exact(value: Fraction | None) -> str | None:
    return None if value is None else str(value)


def _bound(reward: Fraction | None, absent: int) -> Fraction:
    return Fraction(absent) if reward is None else reward


def _sign_p(wins: int, losses: int) -> Fraction:
    non_ties = wins + losses
    tail = sum(comb(non_ties, k) for k in range(wins, non_ties + 1))
    return Fraction(tail, 2**non_ties)


def _arm_result(arm: _Arm) -> dict[str, Any]:
    return {
        "reward": _exact(arm.reward),
        "complete": arm.complete,
        "settled_calls": arm.settled_calls,
        "held_calls": arm.held_calls,
    }


def _panel(study: str, cells: dict[tuple[int, str], _Arm]) -> dict[str, Any]:
    worlds, control, treatment, _ = _STUDIES[study]
    low = high = Fraction(0)
    known: list[Fraction] = []
    wins = losses = ties = 0
    results = []
    for world in worlds:
        base, treated = cells[world, control], cells[world, treatment]
        control_reward, treatment_reward = base.reward, treated.reward
        complete = base.complete and treated.complete
        difference = None
        if control_reward is not None and treatment_reward is not None:
            difference = treatment_reward - control_reward
            known.append(difference)
            if complete:
                wins += difference > 0
                losses += difference < 0
                ties += difference == 0
        low += _bound(treatment_reward, 0) - _bound(control_reward, 1)
        high += _bound(treatment_reward, 1) - _bound(control_reward, 0)
        results.append(
            {
                "world": world,
                "arms": {control: _arm_result(base), treatment: _arm_result(treated)},
                "difference": _exact(difference),
                "complete": complete,
            }
        )

    complete_pairs = wins + losses + ties
    known_mean = sum(known, Fraction(0)) / len(known) if known else None
    mean = known_mean if complete_pairs == len(worlds) else None
    p_value = _sign_p(wins, losses)
    if mean is None:
        outcome = "inconclusive"
    elif mean > 0 and p_value <= _ALPHA:
        outcome = "pass"
    else:
        outcome = "not_established"
    return {
        "control": control,
        "treatment": treatment,
        "outcome": outcome,
        "coverage": {
            "registered_worlds": len(worlds),
            "registered_teams": len(cells),
            "teams_with_settled_calls": sum(
                arm.settled_calls > 0 for arm in cells.values()
            ),
            "teams_with_reservations_only": sum(
                arm.settled_calls == 0 and arm.held_calls > 0 for arm in cells.values()
            ),
            "native_results": sum(arm.trial_ref is not None for arm in cells.values()),
            "observed_native_rewards": sum(
                arm.reward is not None for arm in cells.values()
            ),
            "complete_arms": sum(arm.complete for arm in cells.values()),
            "observed_pairs": len(known),
            "complete_pairs": complete_pairs,
        },
        "known_paired_mean": _exact(known_mean),
        "mean": _exact(mean),
        "full_register_bounds": {
            "low": str(low / len(worlds)),
            "high": str(high / len(worlds)),
        },
        "sign_test": {
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "p_value": str(p_value),
        },
        "worlds": results,
    }


def summarize(receipt: object) -> dict[str, Any]:
    if not isinstance(receipt, dict) or receipt.get("schema") != _SCHEMA:
        raise ValueError(f"expected schema {_SCHEMA}")
    studies = receipt.get("studies")
    if not isinstance(studies, dict) or studies.keys() != _STUDIES.keys():
        raise ValueError(f"expected studies: {', '.join(_STUDIES)}")
    seen: set[str] = set()
    panels = {}
    for study in _STUDIES:
        cells = _registered(studies[study], study, seen)
        panels[study] = _panel(study, cells)
    return {
        "schema": "seatlab.confirmation-summary/v1",
        "sign_test_policy": _SIGN_POLICY,
        "bounds_policy": _BOUNDS_POLICY,
        "alpha": str(_ALPHA),
        "studies": panels,
        "compound_answer_pass": all(
            panels[study]["outcome"] == "pass"
            for study in ("calendar_answers", "dispatch_answers")
        ),
    }


def _show(value: str | None) -> str:
    return "unknown" if value is None else value


def _print(result: dict[str, Any]) -> None:
    print(result["sign_test_policy"])
    print(result["bounds_policy"])
    for name, panel in result["studies"].items():
        stats, coverage = panel["sign_test"], panel["coverage"]
        bounds = panel["full_register_bounds"]
        control, treatment = panel["control"], panel["treatment"]
        registered = coverage["registered_teams"]
        print(f"\n{name} ({treatment} minus {control}): {panel['outcome']}")
        print(
            f"  Teams: {registered} registered, "
            f"{coverage['teams_with_settled_calls']} with a verified settled response, "
            f"{coverage['teams_with_reservations_only']} with reservations only"
        )
        print(
            f"  Evidence: {coverage['native_results']} native results, "
            f"{coverage['observed_native_rewards']} native rewards, "
            f"{coverage['complete_arms']} complete arms"
        )
        print(
            f"  Pairs: {coverage['observed_pairs']} observed, "
            f"{coverage['complete_pairs']}/{coverage['registered_worlds']} complete"
        )
        print(
            f"  Mean: {_show(panel['mean'])}; "
            f"known paired mean: {_show(panel['known_paired_mean'])}; "
            f"full-register bounds: [{bounds['low']}, {bounds['high']}]"
        )
        print(
            f"  Complete pair wins/losses/ties: "
            f"{stats['wins']}/{stats['losses']}/{stats['ties']}; "
            f"p={stats['p_value']} (alpha={result['alpha']})"
        )
        for row in panel["worlds"]:
            arms = row["arms"]
            scores = " ".join(
                f"{arm}={_show(arms[arm]['reward'])}" for arm in (control, treatment)
            )
            status = "complete" if row["complete"] else "incomplete"
            print(
                f"    {row['world']}: {scores}; "
                f"difference={_show(row['difference'])} ({status})"
            )
    compound = "pass" if result["compound_answer_pass"] else "no pass"
    print(f"\nCompound answer claim: {compound}")


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _invalid_constant(token: str) -> None:
    raise ValueError(f"invalid JSON number: {token}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize the preregistered SeatLab confirmation studies."
    )
    parser.add_argument("receipt", type=Path, nargs="?", default=_DEFAULT)
    parser.add_argument(
        "--json", action="store_true", help="emit JSON with exact rational strings"
    )
    args = parser.parse_args(argv)
    try:
        receipt = json.loads(
            args.receipt.read_text(encoding="utf-8"),
            parse_float=Decimal,
            parse_constant=_invalid_constant,
            object_pairs_hook=_unique_object,
        )
        result = summarize(receipt)
    except (OSError, ValueError) as exc:
        parser.error(f"{args.receipt}: {exc}")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
