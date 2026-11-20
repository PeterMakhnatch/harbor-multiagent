"""Read native committee evidence offline and emit only public measurements."""

import argparse
import hashlib
import json
import re
import sqlite3
import stat
from itertools import permutations
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import UUID

from ..visibility import spend
from .schedule import ROLES, TASK_CHECKSUM, TASK_TREE, grid

_TASK = "crucible/epistemic-crucible-hidden_profile_unshared"
_FAMILY = "epistemic-crucible"
_CONDITION = "hidden_profile_unshared"
_IMPORT_PATH = "examples.seatlab.committee.seat:CommitteeSeat"
_AGENT = "seatlab-committee"
_SEAT_SCHEMA = "seatlab.committee-seat/v1"
_MODELS = {
    "deepseek-flash": "deepseek-flash",
    "deepseek-v4.1-flash-1017": "DeepSeek-V4.1-Flash-1017",
}
_OPTIONS = ("Option_A", "Option_B", "Option_C")
_LABELS = tuple(permutations("XYZ"))
_ROLES = ROLES
_PHASES = ("share", "vote")
_SHARED = {
    f"F_SHARED_{i}": (option, 10)
    for i, option in enumerate(
        ("Option_B", "Option_B", "Option_B", "Option_A", "Option_C"), 1
    )
}
_PRIVATE = {
    f"F_PRIV_{i}": (option, points)
    for i, (option, points) in enumerate(
        (("Option_B", -9), ("Option_A", 9), ("Option_B", -9), ("Option_A", 9)), 1
    )
}
_OWN = dict(zip(_ROLES, _PRIVATE, strict=True))
_FACTS = _SHARED | _PRIVATE
_WORLD = "artifacts/logs/world/events.jsonl"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ABSENT = object()
_PRIMARY = "unique_private_facts"
_MEASURES = (
    _PRIMARY,
    "native_vote_reward",
    "correct_individual_votes",
    "strict_group_majority",
    "at_least_three_private_facts",
)


class _Excluded(Exception):
    def __init__(self, reason: str, status: str = "failed"):
        self.reason = reason
        self.status = status


def _require(condition: object, reason: str) -> None:
    if not condition:
        raise _Excluded(reason)


def _path(root: Path, relative: str, *, directory: bool = False) -> Path:
    # All names come from the fixed schedule and the stock roster, not input documents.
    path = root
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        path /= part
        mode = path.lstat().st_mode
        is_dir = directory or index < len(parts) - 1
        if not (stat.S_ISDIR(mode) if is_dir else stat.S_ISREG(mode)):
            raise _Excluded("unsafe_evidence_path")
    return path


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate JSON field")
    return obj


def _parse(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_unique_object)


def _json(root: Path, relative: str, reason: str, *, optional: bool = False) -> Any:
    try:
        return _parse(_path(root, relative).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if optional:
            return _ABSENT
        raise _Excluded(reason) from None
    except (OSError, ValueError):
        raise _Excluded(reason) from None


def _object(value: Any, reason: str) -> dict:
    if not isinstance(value, dict):
        raise _Excluded(reason)
    return value


def _canonical(action: dict) -> str:
    return json.dumps(action, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _roles(rows: Any, reason: str) -> dict[str, dict]:
    _require(isinstance(rows, list) and len(rows) == len(_ROLES), reason)
    by_role = {}
    for row in rows:
        role = _object(row, reason).get("seat")
        _require(role in _ROLES and role not in by_role, reason)
        by_role[role] = row
    return by_role


def _roster_config(config: Any, attempt: dict) -> str:
    reason = "native_config_mismatch"
    config = _object(config, reason)
    task = _object(config.get("task"), reason)
    path = task.get("path")
    _require(
        isinstance(path, str)
        and Path(path).is_absolute()
        and ".." not in Path(path).parts
        and Path(path).name == _CONDITION
        and all(
            task.get(key) is None for key in ("name", "git_url", "git_commit_id", "ref")
        )
        and config.get("trial_name") == attempt["trial_name"]
        and not any(
            config.get(key)
            for key in (
                "install_only",
                "source_trial",
                "extra_instruction_paths",
                "extra_instructions",
            )
        )
        and not _object(config.get("verifier", {}), reason).get("disable"),
        reason,
    )
    for role, participant in _roles(config.get("participants"), reason).items():
        _require(
            participant.get("import_path") == _IMPORT_PATH
            and participant.get("model_name") == "deepseek/deepseek-flash"
            and participant.get("kwargs")
            == {
                "role": role,
                "run_id": attempt["cell_id"],
                "map_index": attempt["map_index"],
                "arm": attempt["arm"],
            }
            and not any(participant.get(key) for key in ("env", "load_trajectory")),
            reason,
        )
    return path


def _native_result(
    trial: Path, attempt: dict, task_path: str
) -> tuple[UUID, dict, dict]:
    reason = "native_result_invalid"
    result = _object(_json(trial, "result.json", reason), reason)
    try:
        trial_id = UUID(result["id"])
    except (AttributeError, KeyError, TypeError, ValueError):
        raise _Excluded(reason) from None
    _require(
        trial_id.int
        and result.get("schema_version", 1) == 1
        and result.get("trial_name") == attempt["trial_name"]
        and result.get("task_name") == _TASK
        and result.get("task_checksum") == TASK_CHECKSUM
        and result.get("task_id") == {"path": task_path}
        and _roster_config(result.get("config"), attempt) == task_path,
        reason,
    )
    reason = "native_trial_failed_or_incomplete"
    _require(
        result.get("exception_info") is None
        and result.get("reward_valid") is True
        and isinstance(result.get("finished_at"), str)
        and result["finished_at"],
        reason,
    )
    contexts = {}
    for role, seat in _roles(result.get("seat_results"), reason).items():
        end = _object(seat.get("agent_execution"), reason).get("finished_at")
        _require(
            seat.get("under_test") is True
            and seat.get("exception_info") is None
            and isinstance(end, str)
            and end,
            reason,
        )
        reason_model = "native_seat_model_mismatch"
        info = _object(seat.get("agent_info"), reason_model)
        _require(
            info.get("name") == _AGENT
            and info.get("model_info")
            == {
                "provider": "deepseek",
                "name": "deepseek-flash",
            },
            reason_model,
        )
        context = _object(seat.get("agent_result"), "seat_context_missing")
        contexts[role] = _object(context.get("metadata"), "seat_context_missing")
    verifier = _object(result.get("verifier_result"), "native_reward_missing")
    return trial_id, contexts, _object(verifier.get("rewards"), "native_reward_missing")


def _events(trial: Path) -> list[dict]:
    manifest = _json(trial, "artifacts/manifest.json", "world_artifact_missing")
    _require(isinstance(manifest, list), "world_artifact_invalid")
    matches = [
        entry
        for entry in manifest
        if isinstance(entry, dict) and entry.get("source") == "/logs/world/events.jsonl"
    ]
    _require(
        len(matches) == 1
        and matches[0].get("destination") == _WORLD
        and matches[0].get("status") == "ok"
        and matches[0].get("type") == "file"
        and matches[0].get("service") in (None, "main"),
        "world_artifact_invalid",
    )
    try:
        lines = _path(trial, _WORLD).read_text(encoding="utf-8").splitlines()
        return [_object(_parse(line), "world_events_invalid") for line in lines]
    except (OSError, ValueError):
        raise _Excluded("world_events_missing_or_invalid") from None


def _native_actions(trial: Path) -> dict[str, dict]:
    reason = "world_events_invalid"
    by_type: dict[str, dict[str, dict]] = {
        key: {}
        for key in (
            "dossier_read",
            "dossier_truth",
            "chat_message",
            "vote",
            "seat_done",
        )
    }
    reads: dict[str, list[dict]] = {role: [] for role in _ROLES}
    events = _events(trial)
    _require(events, reason)
    for seq, event in enumerate(events, 1):
        _require(
            type(event.get("seq")) is int
            and event["seq"] == seq
            and event.get("task_family") == _FAMILY
            and event.get("condition") == _CONDITION
            and type(event.get("seed")) is int
            and event["seed"] == 42,
            reason,
        )
        kind, role = event.get("event_type"), event.get("actor")
        payload = _object(event.get("payload"), reason)
        recipients, scope = event.get("recipients"), event.get("visibility")
        authenticated = event.get("authenticated")
        if seq == 1 or kind == "world_started":
            _require(
                seq == 1
                and kind == "world_started"
                and role == "world"
                and authenticated is False
                and scope == "world_private"
                and recipients == []
                and payload == {"mode": "hidden_profile", "seats": list(_ROLES)},
                reason,
            )
        elif kind == "dossier_truth":
            _require(
                role == "world"
                and authenticated is False
                and scope == "world_private"
                and isinstance(recipients, list)
                and len(recipients) == 1,
                reason,
            )
            role = recipients[0]
        elif kind in (
            "dossier_read",
            "message_read",
            "chat_message",
            "vote",
            "seat_done",
        ):
            public = kind == "chat_message"
            _require(
                authenticated is True
                and scope == ("public" if public else "world_audit")
                and recipients == (list(_ROLES) if public else []),
                reason,
            )
        if kind == "message_read":
            _require(role in _ROLES and payload.get("channel") == "public", reason)
            reads[role].append(event)
        elif isinstance(kind, str) and kind in by_type:
            bucket = by_type[kind]
            _require(role in _ROLES and role not in bucket, reason)
            bucket[role] = event

    _require(
        all(len(bucket) == len(_ROLES) for bucket in by_type.values()),
        "world_actions_incomplete",
    )
    posts = by_type["chat_message"]
    post_seqs = sorted(event["seq"] for event in posts.values())
    actions = {}
    for role in _ROLES:
        read, truth, post, vote, done = (bucket[role] for bucket in by_type.values())
        _require(
            read["payload"] == {}
            and truth["payload"]
            == {
                "read_seq": read["seq"],
                "shared_ids": list(_SHARED),
                "private_ids": [_OWN[role]],
            }
            and read["seq"] + 1 == truth["seq"] < post["seq"]
            and any(
                post_seqs[-1] < seen["seq"] < vote["seq"]
                and seen["payload"].get("msg_seqs") == post_seqs
                for seen in reads[role]
            )
            and vote["seq"] < done["seq"]
            and done["payload"] == {"seat": role},
            "world_role_visibility_mismatch",
        )
        text = post["payload"].get("text")
        _require(
            post["payload"].keys() == {"seat", "text"}
            and post["payload"]["seat"] == role
            and isinstance(text, str),
            "world_share_invalid",
        )
        try:
            fact = _object(_parse(text), "world_share_invalid")
        except ValueError:
            raise _Excluded("world_share_invalid") from None
        fact_id = fact.get("id")
        _require(
            isinstance(fact_id, str) and (fact_id in _SHARED or fact_id == _OWN[role]),
            "world_share_outside_dossier",
        )
        option, points = _FACTS[fact_id]
        _require(
            text == _canonical({"id": fact_id, "option": option, "points": points}),
            "world_share_invalid",
        )
        ballot = vote["payload"]
        choice, cited = ballot.get("choice"), ballot.get("cited_facts")
        _require(
            ballot.keys() == {"seat", "choice", "cited_facts"}
            and ballot["seat"] == role
            and choice in _OPTIONS
            and isinstance(cited, list)
            and cited
            and all(isinstance(fid, str) for fid in cited),
            "world_vote_invalid",
        )
        actions[role] = {
            "share": {"text": text},
            "vote": {"choice": choice, "cited_facts": cited},
            "fact_id": fact_id,
        }
    public = {row["fact_id"] for row in actions.values()}
    for role, row in actions.items():
        cited = row["vote"]["cited_facts"]
        allowed = public | set(_SHARED) | {_OWN[role]}
        _require(
            len(set(cited)) == len(cited) and set(cited) <= allowed,
            "world_citation_not_visible",
        )
    return actions


def _ledger(spend_dir: Path | None) -> tuple[str, dict[tuple[str, str], dict]]:
    if spend_dir is None:
        return "not_supplied", {}
    try:
        spend_dir = spend_dir.absolute()
        _path(spend_dir.parent, spend_dir.name + "/direct-flash.sqlite3")
        query = spend.report(spend_dir)
        if query.get("schema") != "seatlab.direct-flash-cost/v1":
            return "unavailable", {}
        return "available", {
            (call["run"], call["role"]): call for call in query["calls"]
        }
    except (OSError, ValueError, _Excluded, sqlite3.Error):
        return "unavailable", {}


def _attestations(cell_id: str, ledger_status: str, ledger: dict) -> dict[str, dict]:
    rows = {}
    for role in _ROLES:
        phases = {}
        for phase in _PHASES:
            call = ledger.get((f"{cell_id}-{phase}", role))
            if call is not None:
                status = "settled" if call["estimated_usd_high"] is not None else "held"
            else:
                status = (
                    "no_paid_call_recorded"
                    if ledger_status == "available"
                    else "unknown"
                )
            phases[phase] = {"receipt": "not_observed", "paid_call": status}
        rows[role] = phases
    return rows


def _receipts(trial: Path, attested: dict[str, dict]) -> dict[str, dict]:
    receipts = {}
    for role, phases in attested.items():
        path = f"seats/{role}/agent/committee-turns.json"
        try:
            receipt = _json(trial, path, "seat_receipt_invalid", optional=True)
        except _Excluded as exc:
            if exc.reason == "unsafe_evidence_path":
                raise
            for turn in phases.values():
                turn["receipt"] = "invalid"
            continue
        turns = receipt.get("turns") if isinstance(receipt, dict) else None
        for phase, turn in phases.items():
            if receipt is _ABSENT or isinstance(turns, dict) and phase not in turns:
                status = "missing"
            elif not isinstance(turns, dict) or not isinstance(turns[phase], dict):
                status = "invalid"
            else:
                status = (
                    "present" if turns[phase].get("action") is not None else "no_action"
                )
            turn["receipt"] = status
        if isinstance(receipt, dict):
            receipts[role] = receipt
    return receipts


def _attest(
    attempt: dict,
    role: str,
    context: dict,
    receipt: Any,
    actions: dict,
    evidence: dict,
    ledger_status: str,
    ledger: dict,
) -> None:
    reason = "seat_receipt_mismatch"
    identity = {
        "schema": _SEAT_SCHEMA,
        "role": role,
        "run_id": attempt["cell_id"],
        "map_index": attempt["map_index"],
        "arm": attempt["arm"],
        "phase": "done",
        "status": "complete",
    }
    if any(turn["receipt"] == "missing" for turn in evidence.values()):
        raise _Excluded("seat_receipt_missing")
    receipt = _object(receipt, reason)
    for source in (context, receipt):
        _require(
            all(source.get(key) == value for key, value in identity.items()), reason
        )
        _require(set(_object(source.get("turns"), reason)) == set(_PHASES), reason)
    for phase in _PHASES:
        turn = _object(context["turns"][phase], reason)
        posted = actions[phase]
        recorded = _object(receipt["turns"][phase], reason)
        served = turn.get("served_model")
        hashes = [
            turn.get(key)
            for key in ("prompt_sha256", "response_sha256", "action_sha256")
        ]
        _require(
            turn.get("requested_model") == "deepseek-flash"
            and isinstance(served, str)
            and served.casefold() in _MODELS
            and turn.get("status") == "submitted"
            and all(
                isinstance(value, str) and _DIGEST.fullmatch(value) for value in hashes
            )
            and turn["action_sha256"] == _sha(_canonical(posted))
            and recorded == (turn | {"action": posted}),
            reason,
        )
        if ledger_status == "available":
            call = ledger.get((f"{attempt['cell_id']}-{phase}", role))
            _require(
                evidence[phase]["paid_call"] == "settled" and call is not None,
                "paid_call_not_settled",
            )
            _require(call["served_model"] == served, "paid_call_model_mismatch")
        evidence[phase].update(
            receipt="attested", served_model=_MODELS[served.casefold()]
        )


def _public_seats(
    attested: dict, actions: dict | None = None, map_index: int = 0
) -> list[dict]:
    if actions is None:
        return [
            {"seat": f"S{i}", "turns": attested[role]}
            for i, role in enumerate(_ROLES, 1)
        ]
    labels = dict(zip(_OPTIONS, _LABELS[map_index], strict=True))
    ordered = sorted(
        _FACTS, key=lambda fid: (_sha(f"seatlab-committee-v1:{map_index}:{fid}"), fid)
    )
    aliases = {fid: f"K{i}" for i, fid in enumerate(ordered, 1)}
    seats = []
    for i, role in enumerate(_ROLES, 1):
        row = {"seat": f"S{i}", "turns": attested[role]}
        action = actions[role]
        row["share"] = {
            "fact": aliases[action["fact_id"]],
            "private": action["fact_id"] in _PRIVATE,
        }
        row["vote"] = {
            "choice": labels[action["vote"]["choice"]],
            "cited": [aliases[fid] for fid in action["vote"]["cited_facts"]],
        }
        seats.append(row)
    return seats


def _inspect(
    cell_dir: Path,
    cell: dict,
    attested: dict,
    ledger_status: str,
    ledger: dict,
    used_trials: set[UUID],
) -> dict:
    attempt = _json(cell_dir, "attempt.json", "attempt_invalid", optional=True)
    if attempt is _ABSENT:
        raise _Excluded("not_attempted", "blocked")
    expected = cell | {
        "schema": "seatlab.committee-attempt/v1",
        "task_git_tree": TASK_TREE,
        "trial_name": f"committee-{cell['cell_id']}",
    }
    _require(
        attempt == expected
        and type(attempt["map_index"]) is int
        and type(attempt["repetition"]) is int,
        "attempt_mismatch",
    )
    trial = None
    receipts = {}
    try:
        trial = _path(cell_dir, f"trials/{attempt['trial_name']}", directory=True)
        _require(
            sum(stat.S_ISDIR(path.lstat().st_mode) for path in trial.parent.iterdir())
            == 1,
            "multiple_native_trials",
        )
        receipts = _receipts(trial, attested)
    except FileNotFoundError:
        pass
    outcome = _json(cell_dir, "outcome.json", "launch_outcome_invalid", optional=True)
    if outcome is not _ABSENT:
        outcome = _object(outcome, "launch_outcome_invalid")
        _require(
            outcome.get("schema") == "seatlab.committee-launch/v1",
            "launch_outcome_invalid",
        )
        _require(
            type(outcome.get("returncode")) is int
            and outcome["returncode"] == 0
            and outcome.get("error") is None,
            "native_launch_failed",
        )
    config = _json(cell_dir, "config.json", "native_config_invalid", optional=True)
    if config is _ABSENT or trial is None:
        raise _Excluded("native_inputs_pending", "blocked")
    task_path = _roster_config(config, attempt)
    trial_id, contexts, rewards = _native_result(trial, attempt, task_path)
    actions = _native_actions(trial)
    for role in _ROLES:
        _attest(
            attempt,
            role,
            contexts[role],
            receipts.get(role),
            actions[role],
            attested[role],
            ledger_status,
            ledger,
        )
    correct = sum(action["vote"]["choice"] == "Option_A" for action in actions.values())
    majority = correct >= 3
    native_values = {
        "reward": float(majority),
        "group_correct": float(majority),
        "task_utility": float(majority),
        "individual_accuracy": correct / len(_ROLES),
    }
    for metric, value in native_values.items():
        _require(
            type(rewards.get(metric)) in (int, float) and rewards[metric] == value,
            "native_reward_mismatch",
        )
    _require(trial_id not in used_trials, "duplicate_native_trial")
    used_trials.add(trial_id)
    private_count = len({row["fact_id"] for row in actions.values()} & _PRIVATE.keys())
    return cell | {
        _PRIMARY: private_count,
        "at_least_three_private_facts": private_count >= 3,
        "native_vote_reward": rewards["reward"],
        "correct_individual_votes": correct,
        "strict_group_majority": majority,
        "seats": _public_seats(attested, actions, cell["map_index"]),
    }


def _effects(deltas: list[dict]) -> dict:
    return {
        metric: mean(delta[metric] for delta in deltas) if deltas else None
        for metric in _MEASURES
    }


def analyze(root: Path, *, spend_dir: Path | None = None) -> dict:
    """Join authentic native actions and receipts without publishing private evidence."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError
    ledger_status, ledger = _ledger(spend_dir)
    cells, excluded, pairs = [], [], []
    used_trials: set[UUID] = set()
    for scheduled in grid():
        pair_id = f"hp-m{scheduled['map_index']}-r{scheduled['repetition']}"
        cell = {**scheduled, "pair_id": pair_id}
        attested = _attestations(cell["cell_id"], ledger_status, ledger)
        try:
            try:
                cell_dir = _path(root, f"cells/{cell['cell_id']}", directory=True)
            except FileNotFoundError:
                raise _Excluded("not_attempted", "blocked") from None
            scored = _inspect(
                cell_dir, scheduled, attested, ledger_status, ledger, used_trials
            )
        except _Excluded as exc:
            excluded.append(
                cell
                | {
                    "status": exc.status,
                    "reason": exc.reason,
                    "seats": _public_seats(attested),
                }
            )
        except OSError:
            excluded.append(
                cell
                | {
                    "status": "failed",
                    "reason": "evidence_unreadable",
                    "seats": _public_seats(attested),
                }
            )
        else:
            cells.append(scored | {"pair_id": pair_id})

    scored_by_id = {cell["cell_id"]: cell for cell in cells}
    for map_index in range(len(_LABELS)):
        for repetition in range(2):
            pair_id = f"hp-m{map_index}-r{repetition}"
            open_id, novelty_id = f"{pair_id}-open", f"{pair_id}-novelty"
            open_cell, novelty_cell = (
                scored_by_id.get(open_id),
                scored_by_id.get(novelty_id),
            )
            delta = (
                {
                    metric: novelty_cell[metric] - open_cell[metric]
                    for metric in _MEASURES
                }
                if open_cell is not None and novelty_cell is not None
                else None
            )
            pairs.append(
                {
                    "pair_id": pair_id,
                    "map_index": map_index,
                    "repetition": repetition,
                    "open_cell_id": open_id,
                    "novelty_cell_id": novelty_id,
                    "delta": delta,
                }
            )
    deltas = [pair["delta"] for pair in pairs if pair["delta"] is not None]
    maps = []
    for map_index in range(len(_LABELS)):
        map_deltas = [
            pair["delta"]
            for pair in pairs
            if pair["map_index"] == map_index and pair["delta"] is not None
        ]
        maps.append(
            {
                "map_index": map_index,
                "complete_pairs": len(map_deltas),
                "effects": _effects(map_deltas),
            }
        )
    balanced = (
        [row["effects"] for row in maps]
        if all(row["complete_pairs"] for row in maps)
        else []
    )
    return {
        "schema": "seatlab.committee-analysis/v1",
        "task": _TASK,
        "task_git_tree": TASK_TREE,
        "spend_evidence": ledger_status,
        "cells": cells,
        "excluded_cells": excluded,
        "pairs": pairs,
        "maps": maps,
        "summary": {
            "estimand": "novelty_minus_open",
            "weighting": "equal_label_map_means",
            "primary_measure": _PRIMARY,
            "interpretation": "Descriptive effects from label maps and repeats on one latent world.",
            "scheduled_cells": len(cells) + len(excluded),
            "scored_cells": len(cells),
            "complete_pairs": len(deltas),
            "effects": _effects(balanced),
            "complete_pair_effects": _effects(deltas),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--spend-dir", type=Path)
    args = parser.parse_args()
    try:
        report = analyze(args.root, spend_dir=args.spend_dir)
    except OSError:
        parser.error("the study root is unavailable")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
