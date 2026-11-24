import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..native_evidence import Incomplete, Invalid, read_bytes, same, strict_object
from .collect import collect
from .register import QUESTION, trusted_json


def schedule_trace(arm: str) -> list[str]:
    if arm == "natural":
        return []
    trace = ["arrived:planner:after_publish", "arrived:dispatcher:before_decide"]
    if arm != "off":
        trace.append("witness:running:running")
    before, after = (
        "release:dispatcher:before_decide",
        "arrived:dispatcher:after_decide",
    )
    if arm == "held-late":
        trace.extend([before, after, "witness:running:running"])
    trace.append("release:planner:after_publish")
    if arm == "racing":
        trace.append(before)
    trace.append("native:planner:clean" if arm == "off" else "witness:clean:running")
    if arm not in {"held-late", "racing"}:
        trace.append(before)
    if arm != "held-late":
        trace.append(after)
    return [*trace, "release:dispatcher:after_decide"]


def seat_receipts(trial: Path, result: dict[str, Any], arm: dict[str, Any]) -> bool:
    for seat, stages in (
        ("planner", ["after_publish"]),
        ("dispatcher", ["before_decide", "after_decide"]),
    ):
        gates = arm[f"{seat}_gates"]
        terminal = "publish" if seat == "planner" else arm["dispatcher_policy"]
        policy = (
            "preferred" if seat == "dispatcher" and terminal == "publish" else terminal
        )
        body = read_bytes(trial, ("seats", seat, "agent", "control-receipt.json"), 4096)
        receipt = strict_object(body, "seat control receipt")
        native = next((r for r in result["seat_results"] if r["seat"] == seat), {})
        agent = native.get("agent_result")
        metadata = agent.get("metadata") if type(agent) is dict else None
        if type(metadata) is not dict:
            return False
        summary = receipt.get("summary")
        expected_stages = [
            {"stage": s, "gated": s in gates, "released": True if s in gates else None}
            for s in stages
        ]
        if not (
            same(receipt, metadata.get("seat_cut_control"))
            and receipt.get("schema") == "harbor.seat-cut.control-receipt/v1"
            and receipt.get("role") == seat
            and receipt.get("policy") == policy
            and receipt.get("outcome") == "complete"
            and receipt.get("failure") is None
            and type(summary) is dict
            and summary.get("terminal") == terminal
            and same(receipt.get("gates"), gates)
            and same(receipt.get("stages"), expected_stages)
        ):
            return False
    return True


def schedule_matches(
    row: dict[str, Any], arm: dict[str, Any], roots: list[Path]
) -> bool:
    trials = [r / row["ticket"] for r in roots if (r / row["ticket"]).is_dir()]
    if len(trials) != 1:
        return False
    try:
        result = strict_object(
            read_bytes(trials[0], ("result.json",), 8 << 20), "native result"
        )
        if not seat_receipts(trials[0], result, arm):
            return False
    except (Incomplete, Invalid):
        return False
    try:
        record = strict_object(
            read_bytes(trials[0], ("seat-cut-schedule.json",), 4096), "schedule"
        )
    except Incomplete:
        return row["team"] == "natural"
    except Invalid:
        return False
    if row["team"] == "natural":
        return False
    return record == {
        "schema": "harbor.seat-cut.schedule/v1",
        "ticket": row["ticket"],
        "trial_id": result["id"],
        "schedule": row["team"],
        "status": "complete",
        "trace": schedule_trace(row["team"]),
    }


def assess(
    row: dict[str, Any], arm: dict[str, Any], roots: list[Path]
) -> dict[str, Any]:
    label = row["team"]
    incumbent = int(row["fixture"] == "planner-fit")
    final = incumbent if arm["dispatcher_policy"] == "leave" else 1 - incumbent
    bound = row["bound"]
    checks = {
        "native_grade": row["native_grade"] == final,
        "eligible": row["status"]
        == ("bounded" if arm["observe"] else "unobserved_control"),
        "scripted_roster": row["roster"] == "scripted",
        "registered_cells": row["rule"] == "observe"
        and row["target"] == "planner"
        and row["peer"] == "dispatcher"
        and row["observe"] is arm["observe"],
        "schedule": row["status"] in {"bounded", "unobserved_control"}
        and schedule_matches(row, arm, roots),
    }
    if label.startswith("held-"):
        expected = final if label == "held-late" else incumbent
        checks["identified_grade"] = bool(
            bound and bound["min_grade"] == bound["max_grade"] == expected
        )
        checks["peer_open"] = bool(bound and bound["peer_open_after_return"])
    elif label == "off":
        checks["no_claim_at_return"] = bound is None
    return {
        "ticket": row["ticket"],
        "team": label,
        "seed": row["seed"],
        "fixture": row["fixture"],
        "status": row["status"],
        "grade_at_return": [bound["min_grade"], bound["max_grade"]] if bound else None,
        "prefix_at_return": [bound["lower_seq"], bound["upper_seq"]] if bound else None,
        "peer_open_after_return": bound["peer_open_after_return"] if bound else None,
        "samples": bound["counts"]["samples"] if bound else None,
        "native_grade": row["native_grade"],
        "checks": len(checks),
        "failed": [key for key, yes in checks.items() if not yes],
    }


def qualify(registration: Path, roots: list[Path]) -> dict[str, Any]:
    report = collect(registration, roots)
    study = trusted_json(QUESTION)
    frozen = trusted_json(QUESTION.with_name("control-registration.json"))
    if (
        not same(report["task"], frozen["task"])
        or report["task_files"] != frozen["task_files"]
    ):
        raise ValueError("qualification requires the frozen task files")
    expected = Counter(
        (label, seed, f)
        for label, arm in study["arms"].items()
        for seed in arm["seeds"]
        for f in study["fixtures"]
    )
    seen = Counter((r["team"], r["seed"], r["fixture"]) for r in report["cells"])
    if seen != expected:
        raise ValueError(
            "registration does not cover the prespecified control population exactly once"
        )
    rows = [assess(row, study["arms"][row["team"]], roots) for row in report["cells"]]
    return {
        "schema": "harbor.seat-cut.qualification/v1",
        "source": "native scripted seats",
        "task": report["task"],
        "task_files": report["task_files"],
        "registered": len(rows),
        "checks": sum(r["checks"] for r in rows),
        "failed_checks": sum(len(r["failed"]) for r in rows),
        "statuses": report["statuses"],
        "unknowns": report["unknowns"],
        "cells": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grade the frozen scripted qualification, not an LLM cohort."
    )
    parser.add_argument("registration", type=Path)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()
    result = qualify(args.registration, args.roots)
    print(json.dumps(result, indent=2))
    raise SystemExit(int(result["failed_checks"] != 0))


if __name__ == "__main__":
    main()
