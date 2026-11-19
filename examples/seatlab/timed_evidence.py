"""Reduce a native timing canary schedule to value-free, allowlisted evidence.

Usage: python timed_evidence.py ROOT

ROOT is a canary output directory that holds an initialized `control_only`
plan.json. The reducer is read-only: it never launches, collects, or rewrites
anything, and it imports no Harbor module (it needs only export_study and its
sibling modules). It does not call canary's collector. The collector
(run_panel.py --collect) rewrites each panel's runs.json from the native
directories. The reducer reads the stored runs.json instead. A stale index is
reported as an export failure; it is never repaired here.

Each completed native slot is re-read in full through export_study.export_bundle
with the panel's own effective plan. The output holds only labels, states, the
planned and recorded timestamps, native trial UUIDs, and SHA-256 digests.
Digests of the world-derived views come from the scorer-validated dossier. The
stock oracle does not acknowledge those views. The output marks them as
world-derived and makes no claim that a seat acknowledged them.
"""

import argparse
import hashlib
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import export_study as study

SCHEMA = "seatlab.timed-evidence/v1"
PLAN_SCHEMA = "seatlab.canary-plan/v1"
PLAN_KEYS = {
    "schema",
    "start",
    "slots",
    "interval_seconds",
    "window_seconds",
    "seed",
    "condition",
    "episodes",
    "harbor",
    "runner",
    "runner_sha256",
    "hosted_route",
    "control_only",
    "jobs",
}
CONDITIONS = ("silent", "stale", "fresh", "reordered", "pooled")
DECISIONS = ("attempted", "missed", "paused", "stopped")
OUTCOMES = ("completed", "unmeasured")
VIEW_NOTE = "world_derived_not_oracle_acknowledged"
PROJECTIONS = (
    "latent",
    "inventory_view",
    "dispatch_view",
    "accepted_report",
    "decision",
    "score",
    "reward",
)
SAFE_REASON = re.compile(r"[A-Za-z0-9 ,.:;()'_-]{1,120}")


class Refusal(ValueError):
    """A condition that makes the whole reduction untrustworthy."""


def _digest(tag: str, value: object) -> str:
    body = study._canonical(value)
    return hashlib.sha256(f"seatlab.timed/{tag}\n".encode() + body).hexdigest()


def _stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise Refusal(f"invalid {field} timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise Refusal(f"invalid {field} timestamp") from None
    if parsed.utcoffset() != timedelta(0):
        raise Refusal(f"invalid {field} timestamp")
    return parsed.astimezone(UTC)


def _optional(root: Path, relative: str) -> tuple[bytes, Any] | None:
    path = study._local(root, relative)
    content = study._optional_bytes(path)
    if content is None:
        return None
    return content, study._decode(content, relative.rsplit("/", 1)[-1])


def _plan(root: Path) -> tuple[dict[str, Any], str]:
    found = _optional(root, "plan.json")
    if found is None:
        raise Refusal("no canary plan: the schedule is not initialized")
    content, value = found
    raw = study._object(value, "canary plan", PLAN_KEYS)
    ints = ("slots", "interval_seconds", "window_seconds", "seed", "episodes", "jobs")
    if (
        raw["schema"] != PLAN_SCHEMA
        or any(type(raw[key]) is not int for key in ints)
        or type(raw["control_only"]) is not bool
        or raw["jobs"] != 1
        or not 1 <= raw["slots"] <= 16
        or not 1 <= raw["window_seconds"] <= min(900, raw["interval_seconds"])
        or raw["condition"] not in CONDITIONS
        or not 1 <= raw["episodes"] <= 256
        or not isinstance(raw["start"], str)
        or _stamp(_time(raw["start"], "plan start")) != raw["start"]
    ):
        raise Refusal("invalid canary plan")
    if raw["control_only"] is not True:
        raise Refusal("this reducer accepts only a control_only schedule")
    return raw, hashlib.sha256(content).hexdigest()


def _slot_window(plan: dict[str, Any], slot: int) -> tuple[datetime, datetime]:
    start = _time(plan["start"], "plan start") + timedelta(
        seconds=(slot - 1) * plan["interval_seconds"]
    )
    return start, start + timedelta(seconds=plan["window_seconds"])


def _record(root: Path, relative: str, states: tuple[str, ...] | None) -> Any:
    found = _optional(root, relative)
    if found is None:
        return None
    value = study._object(found[1], relative)
    at = _time(value.get("at"), relative)
    if states is not None and value.get("state") not in states:
        raise Refusal(f"invalid state in {relative.rsplit('/', 1)[-1]}")
    return value, at


def _bind_panel(plan: dict[str, Any], panel: Path) -> None:
    """The effective panel plan must be the canary's one-cell oracle design."""
    found = _optional(panel, "plan.json")
    if found is None:
        raise Refusal("panel plan is missing")
    effective = study._plan(found[1])
    if (
        effective["policy"] != "oracle"
        or effective["hosted_route"] is not None
        or list(effective["seeds"]) != [plan["seed"]]
        or list(effective["conditions"]) != [plan["condition"]]
        or effective["episodes"] != plan["episodes"]
        or len(effective["cells"]) != 1
    ):
        raise Refusal("panel plan does not match the canary plan")
    native = panel / "cells" / f"seed-{plan['seed']}--{plan['condition']}" / "native"
    if len(list(native.glob("seatlab-*/result.json"))) > 1:
        raise Refusal("multiple native results for one canary arm")


def _projections(measurement: dict[str, Any]) -> dict[str, Any]:
    manifest = measurement["manifest"]
    cases = manifest["cases"]
    latent = study._fingerprint(manifest)
    if latent != measurement["fingerprint"]:
        raise Refusal("latent fingerprint does not match the exported manifest")
    reports = [case["reports"] for case in cases]
    chosen = [case["chosen"] for case in cases]
    ids = [case["id"] for case in cases]
    condition = manifest["settings"]["condition"]
    inventory = (
        None
        if condition == "pooled"
        else {
            "role": "inventory",
            "condition": condition,
            "cases": [
                {
                    "id": case["id"],
                    "reports": [
                        {"revision": 1, "available": case["before"]},
                        {"revision": 2, "available": case["after"]},
                    ],
                }
                for case in cases
            ],
        }
    )
    dispatch = {
        "role": "dispatch",
        "condition": condition,
        "cases": [
            {
                "id": case["id"],
                "utilities": case["utilities"],
                "messages": case["visible"],
                "pooled_available": case["after"] if condition == "pooled" else None,
            }
            for case in cases
        ],
    }
    return {
        "latent_sha256": latent,
        "inventory_view_sha256": None
        if inventory is None
        else hashlib.sha256(study._canonical(inventory)).hexdigest(),
        "dispatch_view_sha256": hashlib.sha256(study._canonical(dispatch)).hexdigest(),
        # A missing report or decision stays missing; no digest is invented.
        "accepted_report_sha256": None
        if any(item is None for item in reports)
        else _digest("accepted-report", list(zip(ids, reports, strict=True))),
        "decision_sha256": None
        if any(item is None for item in chosen)
        else _digest("decision", list(zip(ids, chosen, strict=True))),
        "score_sha256": _digest("score", measurement["score"]),
        "reward_sha256": _digest("reward", measurement["reward"]),
        "manifest_sha256": measurement["manifest_sha256"],
        "native_result_sha256": measurement["native_sha256"]["result"],
        "view_attestation": VIEW_NOTE,
    }


def _reason(error: Exception) -> str:
    text = str(error)
    if isinstance(error, OSError | RuntimeError) or not SAFE_REASON.fullmatch(text):
        return "cannot read stable study inputs"
    return text


def _reread(slot_name: str, panel: Path) -> dict[str, Any]:
    """Fully re-read one native panel with export_study; never raise on content."""
    try:
        bundle = study.export_bundle([(slot_name, panel)])
    except (OSError, RuntimeError, ValueError) as error:
        return {"reread_status": None, "export_error": _reason(error)}
    cell = bundle["panels"][0]["cells"][0]
    if cell["status"] == "measured":
        return {"reread_status": "measured", "measurement": cell["measurement"]}
    attempt = cell["native_attempt"]
    return {
        "reread_status": "unmeasured",
        "absence": cell["absence"],
        "process_state": cell["process"]["state"],
        "attempt_trial_id": None if attempt is None else attempt["trial_id"],
        "attempt_result_sha256": None
        if attempt is None
        else attempt["native_sha256"]["result"],
    }


def _control(
    root: Path, plan: dict[str, Any], slot: int
) -> tuple[dict[str, Any], dict[str, Any] | None, list[tuple[str, str]]]:
    """Return the control-arm report, its measurement, and its (UUID, pin) ids."""
    arm = f"slot-{slot:03d}/control"
    start, cutoff = _slot_window(plan, slot)
    decision = _record(root, f"{arm}/decision.json", DECISIONS)
    base: dict[str, Any] = {"state": "unrecorded"}
    if decision is None:
        return base, None, []
    decided, attempted_at = decision
    base["state"] = decided["state"]
    base["decided_at"] = _stamp(attempted_at)
    if decided["state"] != "attempted":
        return base, None, []

    supervisor = _record(root, f"{arm}/supervisor.json", None)
    outcome = _record(root, f"{arm}/outcome.json", OUTCOMES)
    started = supervisor[1] if supervisor else None
    finished = outcome[1] if outcome else None
    stamps = [x for x in (attempted_at, started, finished) if x is not None]
    receipt = {
        "slot": slot,
        "planned_start": _stamp(start),
        "planned_cutoff": _stamp(cutoff),
        "attempted_at": _stamp(attempted_at),
        "started_at": None if started is None else _stamp(started),
        "outcome_at": None if finished is None else _stamp(finished),
        "attempt_in_window": start <= attempted_at < cutoff,
        "recorded_order_ok": stamps == sorted(stamps),
    }
    receipt["receipt_sha256"] = _digest("slot-receipt", receipt)
    base["receipt"] = receipt
    if outcome is None:
        base["state"] = "in_progress"
        return base, None, []
    base["state"] = outcome[0]["state"]
    base["outcome_unsafe"] = bool(outcome[0].get("unsafe"))
    timing_valid = bool(
        receipt["attempt_in_window"]
        and receipt["recorded_order_ok"]
        and started
        and finished
    )
    if not timing_valid or base["outcome_unsafe"]:
        base["state"] = "invalid_slot_receipt"

    panel = study._local(root, f"{arm}/panel")
    _bind_panel(plan, panel)
    reread = _reread(f"slot-{slot:03d}", panel)
    base["reread_status"] = reread["reread_status"]
    ids: list[tuple[str, str]] = []
    measurement = None
    if reread["reread_status"] is None:
        base["state"] = "export_failed"
        base["export_error"] = reread["export_error"]
    elif reread["reread_status"] == "measured":
        measurement = reread["measurement"]
        base["trial_id"] = measurement["trial_id"]
        ids.append((measurement["trial_id"], measurement["native_sha256"]["result"]))
        base["projection"] = _projections(measurement)
        if outcome[0]["state"] != "completed":
            base["state"] = "outcome_contradicts_reread"
    else:
        base["absence"] = reread["absence"]
        base["process_state"] = reread["process_state"]
        base["trial_id"] = reread["attempt_trial_id"]
        if reread["attempt_trial_id"] is not None:
            ids.append((reread["attempt_trial_id"], reread["attempt_result_sha256"]))
        if outcome[0]["state"] == "completed":
            base["state"] = "outcome_contradicts_reread"
    return base, measurement, ids


def _hosted(root: Path, slot: int) -> dict[str, Any]:
    """The plan has no hosted arm. Any hosted trace contradicts that plan."""
    directory = root / f"slot-{slot:03d}" / "hosted"
    if os.path.lexists(directory):
        raise Refusal("hosted evidence exists, but the plan has no hosted arm")
    return {"plan_state": "not_planned"}


def _compare(entries: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    comparison: dict[str, Any] = {
        "basis": "completed slots, distinct native trial UUIDs and result pins",
        "slots": [slot for slot, _ in entries],
        "distinct_trials": len(entries),
        "view_attestation": VIEW_NOTE,
    }
    if len(entries) < 2:
        comparison.update(
            full_identical=None,
            projections_identical=None,
            reason="fewer than 2 completed distinct trials",
        )
        return comparison
    keys = {
        "latent": "latent_sha256",
        "inventory_view": "inventory_view_sha256",
        "dispatch_view": "dispatch_view_sha256",
        "accepted_report": "accepted_report_sha256",
        "decision": "decision_sha256",
        "score": "score_sha256",
        "reward": "reward_sha256",
    }
    identical: dict[str, bool | None] = {}
    for name in PROJECTIONS:
        values = [item[keys[name]] for _, item in entries]
        identical[name] = (
            None if any(value is None for value in values) else len(set(values)) == 1
        )
    comparison["projections_identical"] = identical
    comparison["full_identical"] = (
        None
        if any(value is None for value in identical.values())
        else all(identical.values())
    )
    return comparison


def reduce_root(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve(strict=True)
    plan, plan_sha = _plan(root)
    slots, tally = [], {}
    seen: dict[str, str] = {}
    pins: dict[str, int] = {}
    completed: list[tuple[int, dict[str, Any]]] = []
    failures = 0
    for slot in range(1, plan["slots"] + 1):
        start, cutoff = _slot_window(plan, slot)
        control, measurement, ids = _control(root, plan, slot)
        for trial_id, pin in ids:
            if trial_id in seen:
                raise Refusal("duplicate native trial UUID across slots")
            if pin in pins:
                raise Refusal("one native result appears in two slots")
            seen[trial_id], pins[pin] = f"slot-{slot:03d}", slot
        tally[control["state"]] = tally.get(control["state"], 0) + 1
        failures += control["state"] in (
            "export_failed",
            "outcome_contradicts_reread",
            "invalid_slot_receipt",
        )
        if control["state"] == "completed" and measurement is not None:
            completed.append((slot, control["projection"]))
        slots.append(
            {
                "slot": slot,
                "planned_start": _stamp(start),
                "planned_cutoff": _stamp(cutoff),
                "control": control,
                "hosted": _hosted(root, slot),
            }
        )
    return {
        "schema": SCHEMA,
        "plan": {
            "plan_sha256": plan_sha,
            "start": plan["start"],
            "slots": plan["slots"],
            "interval_seconds": plan["interval_seconds"],
            "window_seconds": plan["window_seconds"],
            "seed": plan["seed"],
            "condition": plan["condition"],
            "episodes": plan["episodes"],
            "control_only": True,
            "task": study.TASK,
        },
        "sentinels": {
            "stop": os.path.lexists(root / "STOP"),
            # A hosted pause does not change what the plan contains.
            "pause_hosted": os.path.lexists(root / "PAUSE_HOSTED"),
        },
        "counts": {
            "planned_slots": plan["slots"],
            "control": {"planned": plan["slots"], **dict(sorted(tally.items()))},
            "hosted": {"planned": 0, "not_planned": plan["slots"]},
        },
        "integrity_failures": failures,
        "slots": slots,
        "comparison": _compare(completed),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("root", type=Path, metavar="ROOT")
    args = parser.parse_args(argv)
    try:
        value = reduce_root(args.root)
    except (OSError, RuntimeError):
        print("cannot read stable canary inputs", file=sys.stderr)
        return 1
    except ValueError as error:
        message = str(error)
        print(
            message if SAFE_REASON.fullmatch(message) else "invalid canary inputs",
            file=sys.stderr,
        )
        return 1
    print(study._canonical(value).decode("utf-8"))
    return 2 if value["integrity_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
