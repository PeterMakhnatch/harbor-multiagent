"""Analyze the registered voting follow-up without publishing private evidence."""

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from uuid import UUID

from . import analysis, contract
from .schedule import ROLES, TALLY_ARMS, TASK_TREE, Cell, tally_grid

_FIELD_ORDER = {
    "first": ["totals", "choice", "cited"],
    "last": ["choice", "cited", "totals"],
}
_PHASES = ("share", "vote")
_LABELS = ("X", "Y", "Z")
_MEASURES = ("native_vote_reward", "correct_individual_votes")


def _native_fact(fact_id: str, kind: str) -> dict:
    option, points = analysis._FACTS[fact_id]
    return {"id": fact_id, "option": option, "points": points, "kind": kind, "text": ""}


def _dossier(role: str) -> dict:
    # The stock validator binds the seat to these canonical facts. The original
    # prompt projects their structured fields, not the dossier's prose.
    return {
        "seat": role,
        "options": list(analysis._OPTIONS),
        "rubric": "Choose the option with the highest net score.",
        "shared_facts": [_native_fact(fid, "shared") for fid in analysis._SHARED],
        "private_facts": [_native_fact(analysis._OWN[role], "private")],
    }


def _vote_observations(cell_dir: Path, scored: dict) -> None:
    trial = analysis._path(
        cell_dir, f"trials/committee-{scored['cell_id']}", directory=True
    )
    # _inspect has authenticated these events, including each seat's read of
    # all four posts, and joined the native result to the matching seat receipt.
    events = analysis._events(trial)
    messages = [
        {**row["payload"], "seq": row["seq"]}
        for row in events
        if row["event_type"] == "chat_message"
    ]
    chat = {"messages": messages, "unique_speakers": list(ROLES)}
    posted_facts = {analysis._parse(message["text"])["id"] for message in messages}
    ballots = {
        row["actor"]: {key: row["payload"][key] for key in ("choice", "cited_facts")}
        for row in events
        if row["event_type"] == "vote"
    }
    reason = "native_result_invalid"
    result = analysis._object(analysis._json(trial, "result.json", reason), reason)
    natives = analysis._roles(result.get("seat_results"), reason)
    index, arm = scored["map_index"], scored["arm"]
    option_labels = dict(zip(analysis._OPTIONS, analysis._LABELS[index], strict=True))
    for role, seat in zip(ROLES, scored["seats"], strict=True):
        native = natives[role]["agent_result"]["metadata"]["turns"]["vote"]
        receipt = analysis._json(
            trial, f"seats/{role}/agent/committee-turns.json", "seat_receipt_invalid"
        )["turns"]["vote"]
        actual = ballots[role]
        analysis._require(
            analysis._canonical(receipt)
            == analysis._canonical(native | {"action": actual}),
            "seat_receipt_mismatch",
        )
        readout = analysis._object(native.get("readout"), "vote_readout_missing")
        text = readout.get("response_text")
        dossier = _dossier(role)
        try:
            view_sha = analysis._sha(contract.vote_prompt(dossier, chat, index))
            prompt_sha = analysis._sha(contract.tally_prompt(dossier, chat, index, arm))
        except ValueError:
            raise analysis._Excluded("vote_view_invalid") from None
        analysis._require(
            prompt_sha == native["prompt_sha256"], "vote_prompt_hash_mismatch"
        )
        analysis._require(
            view_sha == readout.get("view_sha256"), "vote_view_hash_mismatch"
        )
        analysis._require(isinstance(text, str), "vote_response_invalid")
        try:
            response_sha = analysis._sha(text)
        except UnicodeError:
            raise analysis._Excluded("vote_response_invalid") from None
        analysis._require(
            response_sha == native["response_sha256"], "vote_response_hash_mismatch"
        )
        try:
            action, decoded = contract.tally_action(text, dossier, chat, index, arm)
        except ValueError:
            raise analysis._Excluded("vote_response_invalid") from None
        analysis._require(action == actual, "vote_action_mismatch")
        analysis._require(
            analysis._canonical(decoded) == analysis._canonical(readout),
            "vote_readout_mismatch",
        )

        visible = dict.fromkeys(_LABELS, 0)
        for fid in posted_facts | analysis._SHARED.keys() | {analysis._OWN[role]}:
            option, points = analysis._FACTS[fid]
            visible[option_labels[option]] += points
        totals, order = decoded["totals"], decoded["field_order"]
        vote = seat["vote"]
        own_max, agrees, correct_totals, order_adhered = None, None, None, None
        if totals is not None:
            highest = max(totals.values())
            own_max = [label for label in _LABELS if totals[label] == highest]
            agrees = vote["choice"] in own_max
            correct_totals = {
                label: totals[label] == visible[label] for label in _LABELS
            }
            order_adhered = order == _FIELD_ORDER[arm]
        vote.update(
            correct=actual["choice"] == "Option_A",
            view_sha256=view_sha,
            prompt_sha256=prompt_sha,
            response_sha256=response_sha,
            totals=totals,
            field_order=order,
            visible_truth_totals=visible,
            totals_match_visible=None
            if correct_totals is None
            else all(correct_totals.values()),
            totals_match_by_label=correct_totals,
            order_adhered=order_adhered,
            own_max_labels=own_max,
            choice_agrees_with_own_max=agrees,
        )


def _token_usage(cells: list[Cell], ledger_status: str, ledger: dict) -> dict:
    usage = {}
    for phase in (*_PHASES, "all"):
        phases = _PHASES if phase == "all" else (phase,)
        calls = [
            ledger.get((f"{cell['cell_id']}-{stage}", role))
            for cell in cells
            for stage in phases
            for role in ROLES
        ]
        counts = Counter()
        hit = miss = output = 0
        for call in calls:
            if call is None:
                continue
            counts["recorded"] += 1
            if call["estimated_usd_high"] is None:
                counts["held"] += 1
                continue
            tokens = (call["cache_hit"], call["cache_miss"], call["output"])
            if any(type(value) is not int or value < 0 for value in tokens):
                counts["invalid_usage"] += 1
                continue
            counts["measured"] += 1
            hit += tokens[0]
            miss += tokens[1]
            output += tokens[2]
        available = ledger_status == "available"
        usage[phase] = {
            "scheduled_calls": len(calls),
            "recorded_calls": counts["recorded"] if available else None,
            "measured_calls": counts["measured"] if available else None,
            "held_calls": counts["held"] if available else None,
            "invalid_usage_calls": counts["invalid_usage"] if available else None,
            "known_cache_hit_tokens": hit if available else None,
            "known_cache_miss_tokens": miss if available else None,
            "known_input_tokens": hit + miss if available else None,
            "known_output_tokens": output if available else None,
        }
    return usage


def _vote_summary(cells: list[dict]) -> dict:
    votes = [seat["vote"] for cell in cells for seat in cell["seats"]]
    structured = [vote for vote in votes if vote["totals"] is not None]
    unique_max = [vote for vote in structured if len(vote["own_max_labels"]) == 1]
    tied_max = [vote for vote in structured if len(vote["own_max_labels"]) > 1]
    return {
        "observed_votes": len(votes),
        "correct_individual_votes": sum(vote["correct"] for vote in votes),
        "structured_votes": len(structured),
        "observed_field_orders": dict(
            Counter(",".join(vote["field_order"]) for vote in votes)
        ),
        "assigned_order_matches": sum(vote["order_adhered"] for vote in structured),
        "visible_totals_exact_matches": sum(
            vote["totals_match_visible"] for vote in structured
        ),
        "visible_totals_matches_by_label": {
            label: sum(vote["totals_match_by_label"][label] for vote in structured)
            for label in _LABELS
        },
        "choices_at_own_max": sum(
            vote["choice_agrees_with_own_max"] for vote in structured
        ),
        "own_unique_max_votes": len(unique_max),
        "choices_at_own_unique_max": sum(
            vote["choice_agrees_with_own_max"] for vote in unique_max
        ),
        "own_tied_max_votes": len(tied_max),
        "choices_at_own_tied_max": sum(
            vote["choice_agrees_with_own_max"] for vote in tied_max
        ),
    }


def _effects(deltas: list[dict]) -> dict:
    return {
        key: mean(row[key] for row in deltas) if deltas else None for key in _MEASURES
    }


def _paired_summary(pairs: list[dict]) -> dict:
    maps = []
    for index in range(len(analysis._LABELS)):
        deltas = [pair["delta"] for pair in pairs if pair["map_index"] == index]
        maps.append(
            {
                "map_index": index,
                "complete_pairs": len(deltas),
                "effects": _effects(deltas),
            }
        )
    balanced = (
        [row["effects"] for row in maps]
        if all(row["complete_pairs"] for row in maps)
        else []
    )
    return {"complete_pairs": len(pairs), "maps": maps, "effects": _effects(balanced)}


def _contrast(comparator: str, scheduled: list[Cell], scored: list[dict]) -> dict:
    by_cell = {(row["map_index"], row["repetition"], row["arm"]): row for row in scored}
    pairs = []
    for cell in scheduled:
        if cell["arm"] != "first":
            continue
        index, rep = cell["map_index"], cell["repetition"]
        pair_id = f"tv-m{index}-r{rep}"
        first = by_cell.get((index, rep, "first"))
        other = by_cell.get((index, rep, comparator))
        row = {
            "pair_id": pair_id,
            "map_index": index,
            "repetition": rep,
            "first_cell_id": cell["cell_id"],
            "comparator_cell_id": f"{pair_id}-{comparator}",
            "delta": None,
            "all_vote_inputs_match": None,
            "all_submitted_facts_match_by_seat": None,
            "seats": [],
        }
        if first is not None and other is not None:
            seats = []
            for left, right in zip(first["seats"], other["seats"], strict=True):
                seats.append(
                    {
                        "seat": left["seat"],
                        "vote_input_matches": left["vote"]["view_sha256"]
                        == right["vote"]["view_sha256"],
                        "submitted_fact_matches": left["share"]["fact"]
                        == right["share"]["fact"],
                        "first_correct": left["vote"]["correct"],
                        "comparator_correct": right["vote"]["correct"],
                    }
                )
            row.update(
                delta={key: first[key] - other[key] for key in _MEASURES},
                all_vote_inputs_match=all(seat["vote_input_matches"] for seat in seats),
                all_submitted_facts_match_by_seat=all(
                    seat["submitted_fact_matches"] for seat in seats
                ),
                seats=seats,
            )
        pairs.append(row)
    complete = [row for row in pairs if row["delta"] is not None]
    matched = [row for row in complete if row["all_vote_inputs_match"]]
    return {
        "pairs": pairs,
        "all_complete": _paired_summary(complete),
        "input_matched": _paired_summary(matched),
    }


def analyze(root: Path, *, spend_dir: Path | None = None) -> dict:
    """Verify the native and codec evidence for each scheduled tally cell."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError
    try:
        ledger_status, ledger = analysis._ledger(spend_dir)
    except (ArithmeticError, TypeError):
        ledger_status, ledger = "unavailable", {}
    scheduled = tally_grid()
    cells, excluded = [], []
    used_trials: set[UUID] = set()
    for cell in scheduled:
        attested = analysis._attestations(cell["cell_id"], ledger_status, ledger)
        inspected_ids = used_trials.copy()
        try:
            try:
                cell_dir = analysis._path(
                    root, f"cells/{cell['cell_id']}", directory=True
                )
            except FileNotFoundError:
                raise analysis._Excluded("not_attempted", "blocked") from None
            scored = analysis._inspect(
                cell_dir, cell, attested, ledger_status, ledger, inspected_ids
            )
            _vote_observations(cell_dir, scored)
        except analysis._Excluded as exc:
            status, reason = exc.status, exc.reason
        except OSError:
            status, reason = "failed", "evidence_unreadable"
        except (KeyError, TypeError, ValueError, RecursionError):
            status, reason = "failed", "evidence_malformed"
        else:
            cells.append(scored)
            used_trials = inspected_ids
            continue
        excluded.append(
            cell
            | {
                "status": status,
                "reason": reason,
                "seats": analysis._public_seats(attested),
            }
        )
    by_arm = {}
    for arm in TALLY_ARMS:
        arm_schedule = [row for row in scheduled if row["arm"] == arm]
        arm_cells = [row for row in cells if row["arm"] == arm]
        arm_excluded = [row for row in excluded if row["arm"] == arm]
        by_arm[arm] = {
            "scheduled_cells": len(arm_schedule),
            "scored_cells": len(arm_cells),
            "excluded_by_status": dict(Counter(row["status"] for row in arm_excluded)),
            "votes": _vote_summary(arm_cells),
            "tokens": _token_usage(arm_schedule, ledger_status, ledger),
        }
    return {
        "schema": "seatlab.committee-tally-analysis/v1",
        "task": analysis._TASK,
        "task_git_tree": TASK_TREE,
        "spend_evidence": ledger_status,
        "cells": cells,
        "excluded_cells": excluded,
        "contrasts": {
            f"first_minus_{arm}": _contrast(arm, scheduled, cells)
            for arm in ("plain", "last")
        },
        "summary": {
            "primary_contrast": "first_minus_plain",
            "primary_measure": "native_vote_reward",
            "weighting": "equal_label_map_means",
            "interpretation": "Descriptive effects from label maps and repeats on one latent world.",
            "scheduled_cells": len(scheduled),
            "scored_cells": len(cells),
            "excluded_by_reason": dict(Counter(row["reason"] for row in excluded)),
            "tokens": _token_usage(scheduled, ledger_status, ledger),
            "by_arm": by_arm,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--spend-dir", type=Path)
    args = parser.parse_args()
    try:
        report = analyze(args.study, spend_dir=args.spend_dir)
    except OSError:
        parser.error("the study root is unavailable")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
