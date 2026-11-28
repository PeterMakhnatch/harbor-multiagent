import argparse
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..convention_crossplay.records import team_name
from . import control, engine, panel, planio

Rules = dict[tuple[str, str], dict[str, str]]
Inputs = dict[tuple[str, str], set[bytes]]


def _lookup(rules: Rules, name: str, role: str, packet: bytes) -> str:
    try:
        return rules[name, role][hashlib.sha256(packet).hexdigest()]
    except KeyError:
        raise ValueError("incomplete role coverage") from None


def _support(plan: planio.Plan, packets: Inputs) -> None:
    if plan.decks.keys() != packets.keys() or any(
        deck.packets != packets[key] for key, deck in plan.decks.items()
    ):
        raise ValueError("planned role views do not match the native world")


def _enroll(names: list[str]) -> tuple[str, ...]:
    if not 2 <= len(names) <= 6 or len(names) != len(set(names)):
        raise ValueError("expected 2 to 6 different registered participants")
    return tuple(team_name(name) for name in names)


def _stage(
    args: argparse.Namespace,
) -> tuple[planio.Plan, panel.Schedule, dict[str, str]]:
    plan = planio.load(args.report_plan, args.report_pin)
    if plan.stage != "report" or plan.parents:
        raise ValueError("expected a report plan")
    cases = engine.cases(plan.start)
    _support(plan, panel.report_inputs(cases, plan.names))
    rules, pin = planio.recover(plan, args.report_captures)
    schedule = panel.deliver(cases, plan.names, lambda n, r, p: _lookup(rules, n, r, p))
    return (
        plan,
        schedule,
        {"report_plan_sha256": plan.pin, "report_captures_sha256": pin},
    )


def _result(
    stats: dict[str, Any], source: str, ancestry: dict[str, str]
) -> dict[str, Any]:
    return {
        "schema": "seatlab.partner-switch-result/v1",
        "source": source,
        "evidence": ancestry,
        "panel": stats,
        "model_calls_by_scorer": 0,
        "live_harbor_trials": 0,
        "scope": "Native two-turn branches with one frozen response per exact role view. The frozen-home comparison did not observe the new partner. Not independent live teams or model performance.",
    }


def _gate(stats: dict[str, Any], sign: str | None, matched: bool) -> int:
    value = Fraction(stats["mixed_overestimate"])
    actual = "positive" if value > 0 else "negative" if value < 0 else "zero"
    return int(
        (sign is not None and sign != actual)
        or (matched and not stats["matched_report_histograms"])
    )


def command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.command == "qualify":
        cases = engine.cases(args.start)
        names = ("alpha", "beta")

        def choice(name: str, role: str, packet: bytes) -> str:
            return control.choose(
                -1 if name == "beta" and args.control == "different" else 1,
                role,
                packet,
            )

        stats = panel.score(panel.deliver(cases, names, choice), choice)
        mixed = "1/2" if args.control == "different" else "1"
        ok = (
            stats["native"]["matrix"] == [["1", mixed], [mixed, "1"]]
            and stats["frozen_home_vote"]["matrix"] == [["1", "1"], ["1", "1"]]
            and stats["matched_report_histograms"]
        )
        return int(not ok), _result(stats, "scripted_" + args.control, {})
    if args.command == "status":
        from . import coverage

        plan = planio.load(args.plan, args.pin)
        report = coverage.inspect(plan, args.captures, args.workers, args.worker)
        selected = report["selected"]
        return int(selected["complete"] != selected["expected"]), report
    if args.command == "begin":
        names = _enroll(args.team)
        inputs = panel.report_inputs(engine.cases(args.start), names)
        return 0, planio.write(args.out, "report", args.start, names, {}, inputs)
    report_plan, schedule, parents = _stage(args)
    inputs = panel.vote_inputs(schedule)
    if args.command == "advance":
        return 0, planio.write(
            args.out, "vote", report_plan.start, report_plan.names, parents, inputs
        )
    vote_plan = planio.load(args.vote_plan, args.vote_pin)
    if (
        vote_plan.stage != "vote"
        or vote_plan.parents != parents
        or vote_plan.start != report_plan.start
        or vote_plan.names != report_plan.names
    ):
        raise ValueError("vote plan is not the child of these report captures")
    _support(vote_plan, inputs)
    votes, vote_pin = planio.recover(vote_plan, args.vote_captures)
    stats = panel.score(schedule, lambda n, r, p: _lookup(votes, n, r, p))
    evidence = {
        **parents,
        "vote_plan_sha256": vote_plan.pin,
        "vote_captures_sha256": vote_pin,
    }
    return _gate(
        stats, args.expect_overestimate, args.require_matched_reports
    ), _result(stats, "submitted_role_captures", evidence)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Resolve partner-dependent reports before measuring a switch. Never calls a provider."
    )
    cmds = p.add_subparsers(dest="command", required=True)
    qualify = cmds.add_parser("qualify")
    qualify.add_argument("--start", type=int, required=True)
    qualify.add_argument("--control", choices=("same", "different"), required=True)
    status = cmds.add_parser("status")
    status.add_argument("--plan", type=Path, required=True)
    status.add_argument("--pin", required=True)
    status.add_argument("--captures", type=Path, required=True)
    status.add_argument("--workers", type=int, default=1)
    status.add_argument("--worker", type=int)
    begin = cmds.add_parser("begin")
    begin.add_argument("--team", action="append", required=True)
    begin.add_argument("--start", type=int, required=True)
    begin.add_argument("--out", type=Path, required=True)
    advance = cmds.add_parser("advance")
    score = cmds.add_parser("score")
    for c in (advance, score):
        c.add_argument("--report-plan", type=Path, required=True)
        c.add_argument("--report-pin", required=True)
        c.add_argument("--report-captures", type=Path, required=True)
    advance.add_argument("--out", type=Path, required=True)
    score.add_argument("--vote-plan", type=Path, required=True)
    score.add_argument("--vote-pin", required=True)
    score.add_argument("--vote-captures", type=Path, required=True)
    score.add_argument(
        "--expect-overestimate", choices=("negative", "zero", "positive")
    )
    score.add_argument("--require-matched-reports", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        status, report = command(args)
    except (ValueError, OSError, OverflowError, RecursionError):
        print(
            "partner-switch: invalid study, capture, native action or output",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, allow_nan=False, sort_keys=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
