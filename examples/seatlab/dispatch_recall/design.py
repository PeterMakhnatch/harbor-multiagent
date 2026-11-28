from __future__ import annotations

import hashlib
import hmac
import json
from copy import deepcopy
from fractions import Fraction
from math import comb
from typing import Any

from .blind_reference import blind_reference
from .native import Session, diagnose

SCHEMA = "harbor.dispatch-recall.design/v1"
ASSESSMENT_SCHEMA = "harbor.dispatch-recall.assessment/v1"
DESIGN = "free-two-seat-team"
WORLDS = ("stay", "move")
TIMINGS = ("immediate", "deferred", "withheld")
STATUSES = ("correct", "wrong", "uncommitted", "absent")
METRICS = ("team_reward", "physical_reward")
ALPHA = Fraction(1, 20)
MAX_SEEDS = 4096
MIN_SEEDS_FOR_ALPHA = next(n for n in range(1, 64) if Fraction(2, 2**n) <= ALPHA)
ORDER_RULE = (
    "key = order_seed as 8 big-endian bytes; blocks are seeds sorted by "
    "HMAC-SHA256(key, 'block:<seed>'); within a block the 6 cells are sorted by "
    "HMAC-SHA256(key, 'cell:<seed>:<transition>:<timing>'); job_id = 'dr-' + first "
    "24 hex of HMAC-SHA256(key, 'job:<seed>:<transition>:<timing>')"
)
CONTRASTS = {
    "primary": {
        "kind": "primary",
        "treated": "immediate",
        "control": "deferred",
        "pass": (
            "grade treated_higher needs both conditions under the allocation that sets "
            "every unknown immediate job to 0 and every unknown deferred job to 1: "
            "seed_direction treated_higher (more positive than negative seed scores, "
            "exact two-sided sign p <= 1/20) and sample_mean positive (lower "
            "mean_difference_bounds strictly above 0); control_higher is the converse"
        ),
    },
    "negative_control": {
        "kind": "negative_control",
        "treated": "deferred",
        "control": "withheld",
        "pass": (
            "status no_violation_detected: every planned deferred and withheld job is "
            "known and the grade is not_established; a treated_higher or control_higher "
            "grade is a violation; a pass is not evidence of equality"
        ),
    },
}
ANALYSIS = {
    "estimand": (
        "intent-to-treat effect of assigned notice timing on a free two-seat team: "
        "the direction of the seed score for a typical non-tied seed"
    ),
    "primary_outcome": (
        "team_reward: native Harbor team reward of the frozen cut; correct 1, wrong 0, "
        "uncommitted 0; absent (no cut) is unknown and bounded at 0 and 1"
    ),
    "secondary_outcome": (
        "physical_reward: committed-bay correctness; uncommitted is an abstention, not "
        "a wrong bay; uncommitted and absent are unknown and bounded; descriptive only"
    ),
    "unit": "seed",
    "score": "sum over both worlds of treated value minus control value",
    "seed_direction": (
        "inference: exact two-sided sign test over nonzero seed scores, alpha 1/20"
    ),
    "sample_mean": (
        "descriptive gate: mean treated minus control value per planned world pair of "
        "these jobs; positive or negative only if the conservative bound excludes 0; "
        "not an inference about a population mean"
    ),
    "grade_rule": (
        "a direction is graded only if seed_direction and sample_mean agree with it, "
        "both computed with every unknown allocated against it; a zero or "
        "opposite-signed sample mean is not_established; complete-case tests are "
        "descriptive only"
    ),
    "alpha": {"numerator": ALPHA.numerator, "denominator": ALPHA.denominator},
    "min_seeds_for_alpha": MIN_SEEDS_FOR_ALPHA,
    "verdict_rule": (
        "uses team_reward contrasts, plus the physical_reward primary grade: "
        "no_outcomes if no job is known; negative_control_violated if the negative "
        "control is violated; primary_reversed if the primary grade is control_higher; "
        "primary_not_established unless it is treated_higher; "
        "primary_supported_abstention_sensitive if the physical_reward primary grade "
        "is not also treated_higher; primary_supported if the negative control is "
        "no_violation_detected; otherwise primary_supported_negative_control_incomplete"
    ),
    "blind_reference": (
        "blind_reference.blind_reference(seeds, cells) on team_reward; if any blinded "
        "arm is exceeded the verdict is blind_reference_exceeded and the comparison "
        "verdict stays in comparison_verdict"
    ),
    "truthful_report_cells": (
        "descriptive per world and timing: jobs whose depot report action named the "
        "current bay, with team_reward and physical_reward among them and the total "
        "assigned; for comparing the free team with a protocol that conditions on an "
        "honest depot"
    ),
}
LIMITS = (
    (
        "Cut validation is diagnose plus the seeded layout; it shows internal consistency "
        "and holds only if each cut comes from a trusted verifier freeze; a cut alone does "
        "not show model behavior."
    ),
    (
        "The contrasts are intent-to-treat effects of assigned timing; a redeemed ticket "
        "shows a window was requested, not that a seat read, believed, or used a notice."
    ),
    "Results cover this free two-seat team population on these seeds only.",
    (
        "seed_direction is an inference about the typical non-tied seed; sample_mean only "
        "describes these jobs and gives no confidence interval for a population mean."
    ),
    (
        "A not_established grade or a negative-control pass does not show equal "
        "performance or equivalence."
    ),
    "Depot reports are free choices; their accuracy is measured, not enforced.",
    (
        "Participant independence is a plan instruction; cuts carry no participant "
        "identity, so reuse cannot be detected here."
    ),
    (
        "team_reward counts an uncommitted cut as 0, which mixes abstention with error; "
        "physical_reward keeps them apart but is not the registered outcome; a primary "
        "support that physical_reward does not repeat is labeled abstention_sensitive."
    ),
    (
        "truthful_report_cells select on a depot choice made after assignment; they are "
        "not causal subgroup effects and count only jobs where a report action occurred "
        "and named the current bay."
    ),
    (
        "blind_reference_exceeded means a blinded arm scored above its no-information "
        "reference; it flags the whole design for review and does not show a leak; the "
        "comparison fields are still reported."
    ),
    "The cause of an absent cut is not identified.",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _mac(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode("ascii"), hashlib.sha256).hexdigest()


def _frac(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("plan is not plain JSON") from error


def _block(key: bytes, seed: int) -> list[dict[str, Any]]:
    cells = sorted(
        ((world, timing) for world in WORLDS for timing in TIMINGS),
        key=lambda cell: _mac(key, f"cell:{seed}:{cell[0]}:{cell[1]}"),
    )
    jobs = []
    for transition, timing in cells:
        job_id = "dr-" + _mac(key, f"job:{seed}:{transition}:{timing}")[:24]
        jobs.append(
            {
                "job_id": job_id,
                "block": seed,
                "session": {"seed": seed, "transition": transition, "timing": timing},
                "participants": {
                    "loader": f"{job_id}-loader",
                    "depot": f"{job_id}-depot",
                },
            }
        )
    return jobs


def design(seeds: list[int], order_seed: int) -> dict[str, Any]:
    _require(
        type(seeds) is list and len(seeds) <= MAX_SEEDS, "seeds must be a bounded list"
    )
    _require(all(type(s) is int and 0 <= s < 2**63 for s in seeds), "invalid seed")
    _require(len(set(seeds)) == len(seeds), "duplicate seed")
    _require(type(order_seed) is int and 0 <= order_seed < 2**63, "invalid order seed")
    _require(order_seed not in seeds, "order seed must differ from every layout seed")
    key = order_seed.to_bytes(8, "big")
    jobs = []
    for seed in sorted(seeds, key=lambda s: _mac(key, f"block:{s}")):
        jobs.extend(_block(key, seed))
    for position, job in enumerate(jobs):
        job["position"] = position
    _require(len({job["job_id"] for job in jobs}) == len(jobs), "job id collision")
    return {
        "schema": SCHEMA,
        "design": DESIGN,
        "seats": {"loader": "free", "depot": "free"},
        "seeds": sorted(seeds),
        "order_seed": order_seed,
        "order_rule": ORDER_RULE,
        "worlds": list(WORLDS),
        "timings": list(TIMINGS),
        "contrasts": deepcopy(CONTRASTS),
        "analysis": deepcopy(ANALYSIS),
        "jobs": jobs,
    }


def check_plan(plan: Any) -> None:
    _require(type(plan) is dict and plan.get("schema") == SCHEMA, "not a recall plan")
    expected = design(plan.get("seeds"), plan.get("order_seed"))
    _require(
        _canonical(plan) == _canonical(expected),
        "plan differs from its re-derived design",
    )


def _record(job: dict[str, Any], cut: Any) -> dict[str, Any]:
    session = job["session"]
    record = {
        "job_id": job["job_id"],
        "position": job["position"],
        "session": dict(session),
        "status": "absent",
        "team_reward": None,
        "physical_reward": None,
        "turn": None,
        "sender": None,
        "precommit": None,
    }
    if cut is None:
        return record
    diagnosis = diagnose(cut)
    _require(
        cut["seed"] == session["seed"]
        and diagnosis["condition"]
        == {"transition": session["transition"], "timing": session["timing"]},
        f"cut seed or condition differs from the assignment of {job['job_id']}",
    )
    layout = Session(session["seed"], session["transition"], session["timing"])
    seeded = layout.snapshot()
    _require(
        list(cut["bays"]) == seeded["bays"]
        and cut["initial_bay"] == seeded["initial_bay"],
        f"cut layout differs from the seeded layout of {job['job_id']}",
    )
    reward = diagnosis["reward"]
    if reward is None:
        status = "uncommitted"
    else:
        status = "correct" if reward == 1 else "wrong"
    record.update(
        status=status,
        team_reward=0 if reward is None else reward,
        physical_reward=reward,
        turn=diagnosis["progress"]["turn"],
        sender=diagnosis["sender"],
        precommit=diagnosis["delivery"]["precommit"],
    )
    return record


def _interval(low: int, high: int, planned: int) -> dict[str, Any] | None:
    if not planned:
        return None
    return {
        "lower": _frac(Fraction(low, planned)),
        "upper": _frac(Fraction(high, planned)),
    }


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = dict.fromkeys(STATUSES, 0)
    sender = dict.fromkeys(("pending", "silence", "correct", "incorrect"), 0)
    precommit = dict.fromkeys(
        ("not_reached", "unrequested", "requested", "redeemed"), 0
    )
    for record in records:
        counts[record["status"]] += 1
        if record["sender"] is not None:
            action = record["sender"]["action"]
            sender[
                record["sender"]["correctness"] if action == "report" else action
            ] += 1
            precommit[record["precommit"]["window"]] += 1
    planned = len(records)
    correct, committed = counts["correct"], counts["correct"] + counts["wrong"]
    return {
        "planned": planned,
        **counts,
        "team_reward": _interval(correct, correct + counts["absent"], planned),
        "physical_reward": _interval(correct, planned - counts["wrong"], planned),
        "committed_accuracy": _frac(Fraction(correct, committed))
        if committed
        else None,
        "sender": sender,
        "precommit_window": precommit,
    }


def _sign(scores: list[int]) -> dict[str, Any]:
    wins = sum(score > 0 for score in scores)
    losses = sum(score < 0 for score in scores)
    discordant = wins + losses
    tail = sum(comb(discordant, k) for k in range(min(wins, losses) + 1))
    p = min(Fraction(1), Fraction(2 * tail, 2**discordant))
    return {
        "wins": wins,
        "ties": len(scores) - discordant,
        "losses": losses,
        "two_sided_p": _frac(p),
    }


def _significant(test: dict[str, Any], direction: int) -> bool:
    p = Fraction(test["two_sided_p"]["numerator"], test["two_sided_p"]["denominator"])
    return (test["wins"] - test["losses"]) * direction > 0 and p <= ALPHA


def _contrast(
    seeds: list[int],
    cells: dict[tuple[int, str, str], dict[str, Any]],
    spec: dict[str, str],
    metric: str,
) -> dict[str, Any]:
    lower, upper, complete = [], [], []
    known = 0
    for seed in seeds:
        treated = [cells[seed, world, spec["treated"]][metric] for world in WORLDS]
        control = [cells[seed, world, spec["control"]][metric] for world in WORLDS]
        known += sum(value is not None for value in treated + control)
        low_t = sum(0 if t is None else t for t in treated)
        high_t = sum(1 if t is None else t for t in treated)
        low_c = sum(0 if c is None else c for c in control)
        high_c = sum(1 if c is None else c for c in control)
        lower.append(low_t - high_c)
        upper.append(high_t - low_c)
        if None not in treated and None not in control:
            complete.append(low_t - low_c)
    against_treated, against_control = _sign(lower), _sign(upper)
    if _significant(against_treated, 1):
        seed_direction = "treated_higher"
    elif _significant(against_control, -1):
        seed_direction = "control_higher"
    else:
        seed_direction = "not_established"
    if seeds and sum(lower) > 0:
        sample_mean = "positive"
    elif seeds and sum(upper) < 0:
        sample_mean = "negative"
    else:
        sample_mean = "not_signed"
    if known == 0:
        grade = "no_outcomes"
    elif (seed_direction, sample_mean) == ("treated_higher", "positive"):
        grade = "treated_higher"
    elif (seed_direction, sample_mean) == ("control_higher", "negative"):
        grade = "control_higher"
    else:
        grade = "not_established"
    pairs = len(seeds) * len(WORLDS)
    result = {
        **spec,
        "metric": metric,
        "planned_seeds": len(seeds),
        "complete_seeds": len(complete),
        "known_jobs": known,
        "mean_difference_bounds": _interval(sum(lower), sum(upper), pairs),
        "worst_case_against_treated": against_treated,
        "worst_case_against_control": against_control,
        "complete_case": _sign(complete) if complete else None,
        "alpha_reachable": len(seeds) >= MIN_SEEDS_FOR_ALPHA,
        "seed_direction": seed_direction,
        "sample_mean": sample_mean,
        "grade": grade,
    }
    if spec["kind"] == "negative_control":
        if grade in ("treated_higher", "control_higher"):
            result["status"] = "violated"
        elif grade == "not_established" and known == 2 * pairs:
            result["status"] = "no_violation_detected"
        else:
            result["status"] = "incomplete"
    return result


def _verdict(
    known: int, primary: dict[str, dict[str, Any]], secondary: dict[str, dict[str, Any]]
) -> str:
    if known == 0:
        return "no_outcomes"
    if primary["negative_control"]["status"] == "violated":
        return "negative_control_violated"
    grade = primary["primary"]["grade"]
    if grade == "control_higher":
        return "primary_reversed"
    if grade != "treated_higher":
        return "primary_not_established"
    if secondary["primary"]["grade"] != "treated_higher":
        return "primary_supported_abstention_sensitive"
    if primary["negative_control"]["status"] == "no_violation_detected":
        return "primary_supported"
    return "primary_supported_negative_control_incomplete"


def _truthful(records: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        r
        for r in records
        if r["sender"] is not None
        and r["sender"]["action"] == "report"
        and r["sender"]["correctness"] == "correct"
    ]
    summary = _summary(selected)
    return {
        "assigned": len(records),
        "selected": len(selected),
        **{key: summary[key] for key in ("correct", "wrong", "uncommitted")},
        "team_reward": summary["team_reward"],
        "physical_reward": summary["physical_reward"],
        "committed_accuracy": summary["committed_accuracy"],
    }


def assess(plan: dict[str, Any], outcomes: dict[str, Any]) -> dict[str, Any]:
    check_plan(plan)
    _require(type(outcomes) is dict, "outcomes must map job ids to cuts")
    _require(all(type(key) is str for key in outcomes), "outcome keys must be job ids")
    unknown = sorted(set(outcomes) - {job["job_id"] for job in plan["jobs"]})
    _require(not unknown, f"outcomes name unplanned job ids: {unknown[:3]}")
    records = [_record(job, outcomes.get(job["job_id"])) for job in plan["jobs"]]
    cells = {
        (r["session"]["seed"], r["session"]["transition"], r["session"]["timing"]): r
        for r in records
    }

    def contrasts(metric: str) -> dict[str, dict[str, Any]]:
        return {
            name: _contrast(plan["seeds"], cells, spec, metric)
            for name, spec in CONTRASTS.items()
        }

    def select(world: str | None, timing: str) -> list[dict[str, Any]]:
        return [
            r
            for r in records
            if r["session"]["timing"] == timing
            and world in (None, r["session"]["transition"])
        ]

    primary = contrasts("team_reward")
    secondary = contrasts("physical_reward")
    known = sum(r["team_reward"] is not None for r in records)
    reference = blind_reference(list(plan["seeds"]), cells)
    _require(type(reference.get("exceeded")) is bool, "invalid blind reference")
    verdict = _verdict(known, primary, secondary)
    return {
        "schema": ASSESSMENT_SCHEMA,
        "design": DESIGN,
        "planned_jobs": len(records),
        "supplied_cuts": known,
        "committed_cuts": sum(r["physical_reward"] is not None for r in records),
        "assignment": records,
        "timings": {timing: _summary(select(None, timing)) for timing in TIMINGS},
        "cells": {
            f"{world}:{timing}": _summary(select(world, timing))
            for world in WORLDS
            for timing in TIMINGS
        },
        "truthful_report_cells": {
            f"{world}:{timing}": _truthful(select(world, timing))
            for world in WORLDS
            for timing in TIMINGS
        },
        "contrasts": primary,
        "secondary_contrasts": secondary,
        "primary_direction": primary["primary"]["grade"],
        "negative_control": primary["negative_control"]["status"],
        "abstention_sensitive": primary["primary"]["grade"] == "treated_higher"
        and secondary["primary"]["grade"] != "treated_higher",
        "blind_reference": reference,
        "comparison_verdict": verdict,
        "verdict": "blind_reference_exceeded"
        if known and reference["exceeded"]
        else verdict,
        "interpretation_limits": list(LIMITS),
    }
