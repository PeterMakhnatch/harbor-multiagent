from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from . import confirmation_summary as summary
from .capsule import view_hash
from .evidence import _by_role, _score
from .live_answers import protocol as calendar
from .visibility import calendar_packet, calendar_protocol, calendar_replica_source

_SCHEMA = "seatlab.confirmation-process/v1"
_CALENDAR = "seatlab.native-calendar-answers/v1"
_NOTICE = "seatlab.native-attention-confirmation-report/v1"
_DISPATCH = "seatlab.native-dispatch_answers-report/v1"
_DISPATCH_SEAT = "seatlab.native-dispatch-answers-seat/v1"
_STAGES = (
    ("inventory", "inventory"),
    ("question", "dispatch"),
    ("answer", "inventory"),
    ("decide", "dispatch"),
)
_CASES = range(6)
_ALIASES = tuple(f"Q{i + 1}" for i in _CASES)
_BAYS = ("A", "B", "C", "D")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    IndexError,
    AttributeError,
    RuntimeError,
)
_MAX_BYTES = 8 * 1024 * 1024


def _need(ok: bool) -> None:
    if not ok:
        raise ValueError("Evidence does not match the registered process")


def _object(data: str | bytes) -> dict[str, Any]:
    value = json.loads(
        data,
        object_pairs_hook=summary._unique_object,
        parse_constant=summary._invalid_constant,
    )
    _need(isinstance(value, dict))
    return value


def _read(path: Path, *, physical: bool = False) -> dict[str, Any]:
    if physical:
        _need(path.resolve() == path)
    if not path.exists():
        raise FileNotFoundError
    _need(path.is_file())
    with path.open("rb") as stream:
        data = stream.read(_MAX_BYTES + 1)
    _need(len(data) <= _MAX_BYTES)
    return _object(data)


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _uuid(value: Any, version: int) -> UUID:
    _need(isinstance(value, str))
    parsed = UUID(value)
    _need(parsed.version == version and str(parsed) == value)
    return parsed


def _claim(seen: set[UUID], value: Any, *, alias: bool = False) -> None:
    origin = _uuid(value, 5 if alias else 4)
    comparable = origin if alias else uuid5(NAMESPACE_URL, f"{_NOTICE}:{origin}")
    _need(comparable not in seen)
    seen.add(comparable)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _coverage(states: Counter[str], eligible: int, joined: int) -> dict[str, Any]:
    registered = sum(states.values())
    status = "complete" if joined == registered else "partial" if joined else "none"
    return {
        "registered_teams": registered,
        "protocol_complete_teams": eligible,
        "observed_teams": joined,
        "status": status,
        "missing_by_reason": {
            reason: n for reason, n in sorted(states.items()) if reason != "observed"
        },
    }


def _bits(value: Any) -> tuple[bool, bool]:
    _need(
        isinstance(value, list)
        and len(value) == 2
        and all(type(bit) is bool for bit in value)
    )
    return value[0], value[1]


def _calendar_rows(
    report: dict[str, Any],
) -> list[tuple[calendar.Cell, dict[str, Any]]]:
    expected = {cell.run: cell for cell in calendar.grids()}
    controls = {cell.run: cell for cell in calendar.grids(control=True)}
    rows = report["cells"]
    _need(report["schema"] == _CALENDAR and isinstance(rows, list))
    _need(len(expected) <= len(rows) <= len(expected) + len(controls))
    result = []
    for row in rows:
        _need(row["schema"] == _CALENDAR and type(row["control"]) is bool)
        cell = (controls if row["control"] else expected).pop(row["run"])
        _need((row["world"], row["arm"]) == (cell.world, cell.arm))
        if not cell.control:
            result.append((cell, row))
    _need(not expected)
    return result


def _calendar_process(
    cell: calendar.Cell, row: dict[str, Any], world: calendar_packet.World
) -> dict[str, Any]:
    _need(row["protocol_bound"] is True and row["native_reward_valid"] is True)
    _need(row["answer_received_by_seat"] is True and row["status"] == "complete")
    _need((row["final_receiver"], row["peer"]) == (cell.receiver, cell.peer))
    source = row["source"]
    _need(
        source["semantics_sha256"]
        == calendar_replica_source._SEMANTICS_SHA256[world.index]
    )
    _need(
        _digest(source["task_sha256"]) and source["task_sha256"] != world.task_checksum
    )
    _need(_digest(row["artifact_sha256"]["result"]))
    question = row["question"]
    _need(isinstance(question, list) and len(question) == 2)
    slots = tuple(calendar_protocol._checked_slot(item["slot"]) for item in question)
    _need(slots[0] != slots[1])
    turns = row["turns"]
    _need(turns["question"]["value"] == list(slots))
    _need(turns["answer"]["value"] == row["answer"])
    _need(
        set(turns) == set(calendar.PHASES)
        and all(turns[phase]["status"] == "accepted" for phase in calendar.PHASES)
    )
    bits = _bits(row["answer"])
    visible = row["final_prompt_answers"] is not None
    _need(
        row["answer_at_final_prompt"] == cell.arm and visible == (cell.arm == "visible")
    )
    if visible:
        _need(_bits(row["final_prompt_answers"]) == bits)
    availability = dict(
        zip(calendar.SEATS, map(calendar_packet.starts, world.tables), strict=True)
    )
    receiver, peer = (set(availability[seat]) for seat in (cell.receiver, cell.peer))
    joint = receiver & peer
    _need(len(joint) == 1)
    asked = bool(joint.intersection(slots))
    positive = {slot for slot, bit in zip(slots, bits, strict=True) if bit}
    correct = tuple(
        bit == (slot in peer) for slot, bit in zip(slots, bits, strict=True)
    )
    win = row["native_reward"]
    _need(type(win) in (int, float) and win in (0, 1) and row["success"] is bool(win))
    return {
        "asked_overlap": asked,
        "wins": int(win),
        "answer_bits": len(bits),
        "peer_truth_correct_bits": sum(correct),
        "peer_truth_correct_first_bits": int(correct[0]),
        "peer_truth_correct_second_bits": int(correct[1]),
        "teams_with_revealed_bits": int(visible),
        "teams_with_true_overlap_revealed_positive": int(
            visible and bool(joint & positive)
        ),
        "teams_with_true_overlap_uniquely_identified": int(
            visible and (positive & receiver) == joint
        ),
    }


def _calendar_panel(report: dict[str, Any], seen: set[UUID]) -> dict[str, Any]:
    packet = calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)
    data = {arm: [] for arm in calendar.ARMS}
    states = {arm: Counter() for arm in calendar.ARMS}
    sources = {}
    reasons = {
        "not_attempted",
        "incomplete_attempt",
        "protocol_failure",
        "missing_native_result",
        "invalid_or_missing_evidence",
        "source_setup_failed",
        "native_config_failed",
        "native_preflight_failed",
        "native_launch_failed",
        "native_nonzero_exit",
        "native_timeout",
        "native_interrupted",
    }
    for cell, row in _calendar_rows(report):
        _need(type(row["protocol_complete"]) is bool)
        if not row["protocol_complete"]:
            status = row["status"]
            states[cell.arm][
                status if status in reasons else "not_protocol_complete"
            ] += 1
            continue
        _claim(seen, row["trial_uuid"])
        task = row["source"]["task_sha256"]
        _need(sources.setdefault(cell.world, task) == task)
        data[cell.arm].append(_calendar_process(cell, row, packet[cell.index]))
        states[cell.arm]["observed"] += 1
    return {arm: _calendar_arm(data[arm], states[arm]) for arm in calendar.ARMS}


def _calendar_arm(rows: list[dict[str, Any]], states: Counter[str]) -> dict[str, Any]:
    groups = {}
    for label, asked in (("asked_overlap", True), ("did_not_ask_overlap", False)):
        group = [row for row in rows if row["asked_overlap"] is asked]
        wins = sum(row["wins"] for row in group)
        groups[label] = {
            "teams": len(group),
            "wins": wins,
            "win_rate": _rate(wins, len(group)),
            "wins_with_true_overlap_revealed_positive": sum(
                row["wins"] * row["teams_with_true_overlap_revealed_positive"]
                for row in group
            ),
        }
    with_overlap, without = groups.values()
    delta = None
    relation = "not_comparable"
    if with_overlap["teams"] and without["teams"]:
        delta = Fraction(with_overlap["wins"], with_overlap["teams"]) - Fraction(
            without["wins"], without["teams"]
        )
        relation = "higher" if delta > 0 else "lower" if delta < 0 else "equal"
    metrics = (
        "answer_bits",
        "peer_truth_correct_bits",
        "peer_truth_correct_first_bits",
        "peer_truth_correct_second_bits",
        "teams_with_revealed_bits",
        "teams_with_true_overlap_revealed_positive",
        "teams_with_true_overlap_uniquely_identified",
    )
    return {
        "coverage": _coverage(states, len(rows), len(rows)),
        **{key: sum(row[key] for row in rows) for key in metrics},
        **groups,
        "asked_minus_not_asked_win_rate": None if delta is None else float(delta),
        "win_rate_comparison": relation,
    }


def _handoff_rows(report: dict[str, Any], panel: str) -> list[dict[str, Any]]:
    notice = panel == "notices"
    schema = _NOTICE if notice else _DISPATCH
    worlds, control, treatment, _ = summary._STUDIES[panel]
    expected = {
        (seed, arm): {
            "seed": seed,
            "arm": arm,
            "repetition": 0,
            "pairing": "MM",
            "condition": "reordered" if notice else "stale",
            "cell_id": f"attnc-s{seed}-r0-{arm}"
            if notice
            else f"danswer-s{seed}-{arm}",
        }
        for seed in worlds
        for arm in (control, treatment)
    }
    plan = report["plan"]
    _need(
        report["schema"] == schema
        and plan["schema"] == schema.replace("-report/", "-plan/")
    )
    _need(
        plan["transport"] == "direct"
        and type(plan["episodes"]) is int
        and plan["episodes"] == len(_CASES)
    )
    for entries, audit in ((plan["cells"], False), (report["rows"], True)):
        _need(isinstance(entries, list) and len(entries) == len(expected))
        remaining = set(expected)
        for row in entries:
            key = (row["seed"], row["arm"])
            _need(
                type(row["seed"]) is int
                and type(row["repetition"]) is int
                and key in remaining
            )
            remaining.remove(key)
            row_fields = expected[key].keys() | (
                {"state", "outcome"} if audit else set()
            )
            _need(row.keys() == row_fields)
            _need(all(row[field] == value for field, value in expected[key].items()))
    fingerprints = {}
    for row in report["rows"]:
        outcome = row["outcome"]
        state = row["state"]
        _need(
            state
            in {
                "complete",
                "unclaimed",
                "reserved_without_trial",
                "claimed",
                "native_failed",
                "unverified",
            }
        )
        _need(isinstance(outcome, dict) if state == "complete" else outcome is None)
        if outcome is not None:
            fingerprint = outcome["fingerprint"]
            _need(
                _digest(fingerprint)
                and fingerprints.setdefault(row["seed"], fingerprint) == fingerprint
            )
            _need(summary._score(outcome["native_score"], panel, panel) is not None)
    _need(len(set(fingerprints.values())) == len(fingerprints))
    return report["rows"]


def _notice_panel(report: dict[str, Any], seen: set[UUID]) -> dict[str, Any]:
    panels = {}
    rows = _handoff_rows(report, "notices")
    metrics = (
        "opened_slots",
        "opened_report_changes",
        "total_report_changes",
        "opened_sensitive",
    )
    for arm in ("count", "named"):
        counts = dict.fromkeys(metrics, 0)
        states: Counter[str] = Counter()
        for row in rows:
            if row["arm"] != arm:
                continue
            outcome = row["outcome"]
            if outcome is None:
                states[row["state"]] += 1
                continue
            _claim(seen, outcome["trial_alias"], alias=True)
            _need("trial_id" not in outcome)
            expected_runs = [
                f"{row['cell_id']}-{stage}"
                for stage in ("inventory", "select", "decide")
            ]
            _need([turn["run"] for turn in outcome["turns"]] == expected_runs)
            opened = outcome["opened"]
            _need(isinstance(opened, list) and len(opened) == 2)
            _need(opened[0] != opened[1])
            _need(all(alias in _ALIASES for alias in opened))
            change, total, sensitive = (outcome[key] for key in metrics[1:])
            _need(all(type(n) is int for n in (change, total, sensitive)))
            _need(0 <= change <= min(len(opened), total) and 0 <= total <= len(_CASES))
            _need(
                0 <= sensitive <= len(opened)
                and total - change <= len(_CASES) - len(opened)
            )
            for key, n in zip(
                metrics, (len(opened), change, total, sensitive), strict=True
            ):
                counts[key] += n
            states["observed"] += 1
        panels[arm] = {
            "coverage": _coverage(states, states["observed"], states["observed"]),
            **counts,
            "share_of_opens_on_changed_reports": _rate(
                counts["opened_report_changes"], counts["opened_slots"]
            ),
            "share_of_changed_reports_opened": _rate(
                counts["opened_report_changes"], counts["total_report_changes"]
            ),
        }
    return panels


def _dispatch_trial(
    root: Path, row: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    outcome = row["outcome"]
    directory = root / "cells" / row["cell_id"]
    name = f"seatlab-team-{sha256(os.fsencode(directory)).hexdigest()[:24]}"
    trial = directory / "trials" / name
    result = _read(trial / "result.json", physical=True)
    world = _read(trial / "artifacts/logs/world/handoff.json", physical=True)
    _need(_uuid(result["id"], 4) == _uuid(outcome["trial_id"], 4))
    _need(result["trial_name"] == name and result["trial_uri"] == trial.as_uri())
    _need(
        result["reward_valid"] is True
        and isinstance(result["finished_at"], str)
        and bool(result["finished_at"])
        and result["exception_info"] is None
    )
    settings = world["settings"]
    _need(
        settings["seed"] == row["seed"]
        and settings["condition"] == "stale"
        and settings["episodes"] == len(_CASES)
    )
    score = _score(world)
    rewards = result["verifier_result"]["rewards"]
    _need(rewards.keys() == {"reward"} and type(rewards["reward"]) in (int, float))
    _need(
        rewards["reward"] == outcome["native_score"] == score["optimal"] / len(_CASES)
    )
    _need(rewards == _read(trial / "verifier/reward.json", physical=True))
    latent = [
        {key: case[key] for key in ("id", "utilities", "before", "after")}
        for case in world["cases"]
    ]
    _need(view_hash(latent) == outcome["fingerprint"])
    return result, world


def _dispatch_turns(
    result: dict[str, Any], row: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    records = _by_role(result["seat_results"], "seat_results")
    metadata, turns = {}, {}
    audited = row["outcome"]["turns"]
    _need(isinstance(audited, list) and len(audited) == len(_STAGES))
    for role, seat in records.items():
        _need(seat["under_test"] is True and seat["exception_info"] is None)
        _need(seat["agent_info"]["name"] == "seatlab-dispatch-answers")
        data = seat["agent_result"]["metadata"]
        _need(
            (data["schema"], data["cell_id"], data["role"], data["stage"])
            == (_DISPATCH_SEAT, row["cell_id"], role, "complete")
        )
        metadata[role] = data
        stages = [
            (i, stage) for i, (stage, owner) in enumerate(_STAGES) if owner == role
        ]
        raw = data["turns"]
        _need(isinstance(raw, list) and len(raw) == len(stages))
        for (i, stage), turn in zip(stages, raw, strict=True):
            identity = {
                "stage": stage,
                "run": f"{row['cell_id']}-{stage}",
                "role": role,
                "status": "received",
            }
            _need({key: turn[key] for key in identity} == identity)
            _need(audited[i] == {**identity, "usage": turn["usage"]})
            text = turn["text"]
            _need(
                isinstance(text, str)
                and sha256(text.encode()).hexdigest() == turn["response_sha256"]
            )
            turns[stage] = text
    return metadata, turns


def _reply(text: str, key: str) -> Any:
    value = _object(text)
    _need(value.keys() == {key})
    return value[key]


def _accepted_inventory(rows: Any) -> list[dict[str, Any]]:
    _need(isinstance(rows, list) and len(rows) == len(_CASES))
    by_id = {}
    for row in rows:
        _need(row.keys() == {"id", "reports"} and type(row["id"]) is int)
        case_id, reports = row["id"], row["reports"]
        _need(case_id in _CASES and case_id not in by_id)
        _need(isinstance(reports, list) and len(reports) == 2)
        revisions = {}
        for report in reports:
            _need(report.keys() == {"revision", "available"})
            rev, bays = report["revision"], report["available"]
            _need(type(rev) is int and rev in (1, 2) and rev not in revisions)
            _need(isinstance(bays, list) and len(bays) == 2 and bays[0] != bays[1])
            _need(all(bay in _BAYS for bay in bays))
            revisions[rev] = {"revision": rev, "available": sorted(bays)}
        by_id[case_id] = [revisions[1], revisions[2]]
    return [{"id": case_id, "reports": by_id[case_id]} for case_id in _CASES]


def _dispatch_process(root: Path, row: dict[str, Any]) -> dict[str, int]:
    result, world = _dispatch_trial(root, row)
    metadata, turns = _dispatch_turns(result, row)
    inventory, dispatch = metadata["inventory"], metadata["dispatch"]
    accepted = [
        {"id": case["id"], "reports": case["reports"]} for case in world["cases"]
    ]
    submitted = _accepted_inventory(_reply(turns["inventory"], "rows"))
    _need(submitted == accepted and view_hash(accepted) == inventory["decision_sha256"])
    ordered = sorted(
        _CASES,
        key=lambda i: sha256(
            f"seatlab-attn-v1:{row['seed']}:{row['repetition']}:{i}".encode()
        ).digest(),
    )
    aliases = dict(zip(_ALIASES, ordered, strict=True))
    asked = _reply(turns["question"], "ask")
    _need(isinstance(asked, list) and len(asked) == 2)
    question = []
    for item in asked:
        _need(item.keys() == {"shipment", "bay"} and item["bay"] in _BAYS)
        _need(item["shipment"] in _ALIASES)
        question.append((item["shipment"], item["bay"]))
    _need(question[0][0] != question[1][0])
    bits = _bits(_reply(turns["answer"], "answers"))
    final = [{"id": case["id"], "bay": case["chosen"]} for case in world["cases"]]
    _dispatch_binding(row, inventory, dispatch, question, bits, final)
    own = [
        bay in accepted[aliases[alias]]["reports"][1]["available"]
        for alias, bay in question
    ]
    stock = [bay in world["cases"][aliases[alias]]["after"] for alias, bay in question]
    prior = [
        bay in accepted[aliases[alias]]["reports"][0]["available"]
        for alias, bay in question
    ]
    chosen = [
        world["cases"][aliases[alias]]["chosen"] == bay for alias, bay in question
    ]
    return {
        "yes_bits": sum(bits),
        "own_revision_2_correct_bits": sum(
            bit == fact for bit, fact in zip(bits, own, strict=True)
        ),
        "stock_truth_correct_bits": sum(
            bit == fact for bit, fact in zip(bits, stock, strict=True)
        ),
        "own_revision_2_vs_stock_truth_disagreement_bits": sum(
            own_fact != stock_fact
            for own_fact, stock_fact in zip(own, stock, strict=True)
        ),
        "queried_eligibility_changed": sum(
            a != b for a, b in zip(prior, stock, strict=True)
        ),
        "queried_bay_chosen_on_generated_yes": sum(
            yes and used for yes, used in zip(bits, chosen, strict=True)
        ),
        "queried_bay_chosen_on_generated_no": sum(
            not yes and used for yes, used in zip(bits, chosen, strict=True)
        ),
    }


def _dispatch_binding(
    row: dict[str, Any],
    inventory: dict[str, Any],
    dispatch: dict[str, Any],
    question: list[tuple[str, str]],
    bits: tuple[bool, bool],
    final: list[dict[str, Any]],
) -> None:
    outcome = row["outcome"]
    nonce = dispatch["public_receipt_nonce"]
    _need(_digest(nonce))
    visible = row["arm"] == "visible"
    q_hash, a_hash = view_hash(question), view_hash((question, bits))
    _need(inventory["question_sha256"] == dispatch["question_sha256"] == q_hash)
    _need(inventory["answer_sha256"] == a_hash)
    _need(dispatch["delivered_answer_sha256"] == (a_hash if visible else None))
    _need(view_hash(final) == dispatch["decision_sha256"])
    payloads = (("question", question), ("answer", (question, bits)), ("final", final))
    for stage, value in payloads:
        digest = view_hash(("seatlab.dispatch-answers-receipt/v1", nonce, stage, value))
        _need(outcome[f"{stage}_sha256"] == digest)
    expected_delivery = outcome["answer_sha256"] if visible else None
    _need(outcome["delivered_answer_sha256"] == expected_delivery)


def _dispatch_panel(
    report: dict[str, Any], root: Path | None, seen: set[UUID]
) -> dict[str, Any]:
    rows = _handoff_rows(report, "dispatch_answers")
    panels = {}
    for arm in ("visible", "withheld"):
        states: Counter[str] = Counter()
        counts = dict.fromkeys(
            (
                "yes_bits",
                "own_revision_2_correct_bits",
                "stock_truth_correct_bits",
                "own_revision_2_vs_stock_truth_disagreement_bits",
            ),
            0,
        )
        post_hoc = dict.fromkeys(
            (
                "queried_eligibility_changed",
                "queried_bay_chosen_on_generated_yes",
                "queried_bay_chosen_on_generated_no",
            ),
            0,
        )
        matrix = {
            key: {"perfect_reward": 0, "less_than_perfect_reward": 0}
            for key in ("both_bits_correct", "at_least_one_wrong_bit")
        }
        eligible = 0
        for row in rows:
            if row["arm"] != arm:
                continue
            outcome = row["outcome"]
            if outcome is None:
                states[row["state"]] += 1
                continue
            eligible += 1
            _claim(seen, outcome["trial_id"])
            if root is None:
                states["private_study_not_provided"] += 1
                continue
            try:
                observed = _dispatch_process(root, row)
            except FileNotFoundError:
                states["private_evidence_missing"] += 1
                continue
            except _ERRORS:
                states["private_evidence_unverified"] += 1
                continue
            states["observed"] += 1
            for key in counts:
                counts[key] += observed[key]
            for key in post_hoc:
                post_hoc[key] += observed[key]
            both_correct = observed["own_revision_2_correct_bits"] == 2
            fact = "both_bits_correct" if both_correct else "at_least_one_wrong_bit"
            perfect = outcome["native_score"] == 1
            reward = "perfect_reward" if perfect else "less_than_perfect_reward"
            matrix[fact][reward] += 1
        n = states["observed"]
        panels[arm] = {
            "coverage": _coverage(states, eligible, n),
            "answer_bits": 2 * n,
            "no_bits": 2 * n - counts["yes_bits"],
            **counts,
            "own_revision_2_correctness_by_native_team_reward": matrix,
            "post_hoc_question_use": post_hoc,
        }
    return panels


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("Invalid process reader arguments")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        description="Summarize process observations from 3 original confirmation audits.",
        allow_abbrev=False,
    )
    parser.add_argument("--calendar-report", type=Path, required=True)
    parser.add_argument("--notice-report", type=Path, required=True)
    parser.add_argument("--dispatch-report", type=Path, required=True)
    parser.add_argument(
        "--dispatch-study", type=Path, help="Original private dispatch study directory"
    )
    try:
        args = parser.parse_args(argv)
        root = args.dispatch_study
        if root is not None:
            _need(root.is_absolute() and root.resolve() == root)
        seen: set[UUID] = set()
        result = {
            "schema": _SCHEMA,
            "scope": (
                "Descriptive only. No mediation or detection claims. "
                "Missing evidence is not a wrong answer."
            ),
            "calendar_answers": _calendar_panel(_read(args.calendar_report), seen),
            "notices": _notice_panel(_read(args.notice_report), seen),
            "dispatch_answers": _dispatch_panel(
                _read(args.dispatch_report), root, seen
            ),
        }
    except _ERRORS:
        print(json.dumps({"schema": _SCHEMA, "status": "invalid_or_unavailable_input"}))
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
