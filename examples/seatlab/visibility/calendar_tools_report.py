"""Compare two live, role-local tool policies. Native outcomes and billing remain separate."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from . import (
    calendar_client,
    calendar_direct_match,
    calendar_list_run,
    calendar_protocol,
    calendar_tools_evidence,
    calendar_tools_protocol,
    calendar_tools_summary,
    spend,
)

_STAGES = (("cal_tool_pick", "select"), ("cal_tool_reply", "final"))
_SCORED = ("complete", "model_invalid_scored_zero")
_Row = dict[str, Any]
_Key = tuple[str, str]
_Proof = calendar_tools_evidence._Policy
_PhaseCounts = dict[str, Counter[str]]


def _blocked(reason: Literal["missing", "invalid"]) -> _Proof:
    return {"native": None, "actions": {}, "protocol_status": reason}


def _identity(
    proofs: list[tuple[calendar_protocol.Block, str, _Proof]],
) -> tuple[set[str], set[int]]:
    seen: Counter[str] = Counter()
    sources: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for block, _policy, proof in proofs:
        native = proof["native"]
        if native is None:
            continue
        if all(
            isinstance(native.get(k), str)
            for k in ("native_sha256", "semantics_sha256")
        ):
            sources[block.world].add(
                (native["native_sha256"], native["semantics_sha256"])
            )
        for arm in native["arms"].values():
            if isinstance(digest := arm.get("trial_id_sha256"), str):
                seen[digest] += 1
    return {digest for digest, n in seen.items() if n > 1}, {
        w for w, ids in sources.items() if len(ids) > 1
    }


def _row(
    block: calendar_protocol.Block,
    policy: str,
    room: str,
    proof: _Proof,
    aliases: set[str],
    mixed: set[int],
) -> _Row:
    native = proof["native"] or {"arms": {}, "turns": {}}
    arm = native["arms"].get(room, {})
    run = calendar_tools_protocol.native_id(block)
    required = [
        f"{run}:cal_initial_p1",
        f"{run}:cal_initial_p2",
        f"{run}-{room}:cal_recheck",
    ]
    decision = f"{run}-{room}:cal_decide"
    if arm.get("slot") is not None or decision in native["turns"]:
        required.append(decision)
    unavailable = (
        "invalid_evidence"
        if proof["protocol_status"] == "invalid"
        else "missing_evidence"
    )
    status = arm.get("status", unavailable)
    digest = arm.get("trial_id_sha256")
    reward = None
    slot = None
    if digest in aliases or block.world in mixed:
        status = "invalid_evidence"
    elif status in _SCORED:
        if arm.get("native_timeline_verified") is True and isinstance(digest, str):
            reward, slot = arm.get("reward"), arm.get("slot")
        else:
            status = "invalid_evidence"
    bound = (
        status in _SCORED
        and proof["protocol_status"] != "invalid"
        and all(
            proof["actions"].get(key, {}).get("status") == "bound" for key in required
        )
    )
    return {
        "world": block.world,
        "direction": block.direction,
        "repetition": block.repetition,
        "room": room,
        "policy": policy,
        "native_status": status,
        "protocol_bound": bound,
        "reward": reward,
        "slot": slot,
    }


def _billing(
    spend_dir: Path | None, *, design: str = "availability"
) -> tuple[_Row | None, dict[_Key, _Row]]:
    prefix = calendar_tools_protocol.spend_prefix(design)
    if spend_dir is None:
        return None, {}
    try:
        ledger = spend.report(spend_dir.absolute(), run_prefix=prefix)
        index = {(row["run"], row["role"]): row for row in ledger["calls"]}
        if len(index) != len(ledger["calls"]):
            return None, {}
    except (ArithmeticError, OSError, sqlite3.Error, KeyError, ValueError):
        return None, {}
    return ledger, index


def _meters(
    directory: Path,
    block: calendar_protocol.Block,
    policy: str,
    proof: _Proof,
    index: dict[_Key, _Row],
) -> tuple[set[_Key], Counter[str]]:
    matched: set[_Key] = set()
    issues: Counter[str] = Counter()
    registered = {
        calendar_tools_protocol.action_id(block, policy, identifier): identifier
        for identifier in calendar_client._request_plan(block)
    }
    expected = {f"{run}_{role}.json" for run in registered for role, _ in _STAGES}
    try:
        with calendar_list_run._root(directory / "meter") as fd:
            found = set(os.listdir(fd))
            issues["unregistered_meter_files"] += len(found - expected)
            for run, identifier in registered.items():
                action = proof["actions"].get(identifier)
                for role, stage in _STAGES:
                    name = f"{run}_{role}.json"
                    if name not in found:
                        if (action is not None and action["status"] == "bound") or (
                            run,
                            role,
                        ) in index:
                            issues["missing_meter"] += 1
                        continue
                    if (
                        action is None
                        or action["status"] != "bound"
                        or action["run"] != run
                    ):
                        issues["unbound_action"] += 1
                        continue
                    if stage == "select":
                        prompt, response = (
                            action["select_prompt_sha256"],
                            action["select_response_sha256"],
                        )
                    else:
                        prompt, response = (
                            action["final_prompt_sha256"],
                            action["final_response_sha256"],
                        )
                    if prompt is None or response is None:
                        issues["unbound_action"] += 1
                        continue
                    try:
                        receipt = calendar_list_run._read(fd, name)
                        status = calendar_direct_match.match(
                            receipt,
                            index.get((run, role)),
                            run=run,
                            role=role,
                            prompt_digest=prompt,
                            response_digest=response,
                        )
                    except (KeyError, OSError, ValueError):
                        status = "invalid_meter"
                    if status == "matched":
                        matched.add((run, role))
                    else:
                        issues[status] += 1
    except FileNotFoundError:
        if proof["actions"] or any(
            (run, role) in index for run in registered for role, _ in _STAGES
        ):
            issues["missing_meter_directory"] += 1
    except (OSError, ValueError):
        issues["invalid_meter_directory"] += 1
    return matched, +issues


def _count_actions(
    block: calendar_protocol.Block,
    proof: _Proof,
    by_phase: _PhaseCounts,
    by_actor: dict[str, _PhaseCounts] | None,
) -> None:
    for action in proof["actions"].values():
        if action.get("status") != "bound":
            continue
        role = action["role"]
        phase = (
            "opening"
            if role.startswith("cal_initial_")
            else "recheck"
            if role == "cal_recheck"
            else "decision"
        )
        groups = [by_phase]
        if by_actor is not None:
            seat = action["seat"]
            assert seat in (block.sender, block.receiver)
            actor = "sender" if seat == block.sender else "receiver"
            groups.append(by_actor[actor])
        tool_status = action["tool_status"]
        assert tool_status is not None
        exact = action["exact_local_list"]
        for group in groups:
            counts = group.setdefault(phase, Counter())
            counts["bound"] += 1
            counts[tool_status] += 1
            if type(exact) is bool:
                counts["observed_lists"] += 1
                counts["exact_local_lists"] += int(exact)


def analyze(
    study: Path, spend_dir: Path | None = None, *, design: str = "availability"
) -> _Row:
    arms = calendar_tools_protocol.design_arms(design)
    availability = design == "availability"
    study = study.absolute()
    ledger, index = (
        _billing(spend_dir) if availability else _billing(spend_dir, design=design)
    )
    proofs: list[tuple[calendar_protocol.Block, str, _Proof]] = []
    matched: set[_Key] = set()
    issues: Counter[str] = Counter()
    action_order = ("on", "off") if availability else arms
    actions: dict[str, _PhaseCounts] = {policy: {} for policy in action_order}
    by_actor: dict[str, _PhaseCounts] | None = (
        None if availability else {"sender": {}, "receiver": {}}
    )
    for block in calendar_protocol.blocks():
        for policy in calendar_tools_protocol.policies(block, design=design):
            directory = study / calendar_tools_protocol.native_id(block) / policy
            try:
                with calendar_list_run._root(directory):
                    proof = calendar_tools_evidence.read_policy(
                        directory, block, policy, design=design
                    )
            except FileNotFoundError:
                proof = _blocked("missing")
            except (OSError, ValueError):
                proof = _blocked("invalid")
            proofs.append((block, policy, proof))
            if ledger is not None:
                bound, problems = _meters(directory, block, policy, proof, index)
                matched.update(bound)
                issues.update(problems)
            _count_actions(block, proof, actions[policy], by_actor)
    aliases, mixed = _identity(proofs)
    rows = [
        _row(b, p, room, e, aliases, mixed) for b, p, e in proofs for room in b.arms
    ]
    held = {k for k, v in index.items() if v.get("estimated_usd_high") is None}
    result: _Row = {
        "schema": "seatlab.calendar-local-tools/v1"
        if availability
        else "seatlab.calendar-role-tools/v1",
        "scope": "Registered fresh teams in 12 fixed worlds. Both rooms share one policy's opening lists. Host receipts are not provider invoices.",
        "outcomes": calendar_tools_summary.summarize(rows)
        if availability
        else calendar_tools_summary.summarize(rows, policies=arms),
        "rows": rows,
        "actions": actions,
        "reused_trial_id_sha256": sorted(aliases),
        "mixed_source_worlds": sorted(mixed),
        "cost": {
            "ledger_status": "available"
            if ledger is not None
            else "not_requested"
            if spend_dir is None
            else "unavailable",
            "matched_calls": len(matched) if ledger is not None else None,
            "charged_only_calls": len(index.keys() - matched - held)
            if ledger is not None
            else None,
            "held_calls": len(held) if ledger is not None else None,
            "issues": dict(issues) if ledger is not None else None,
            "spend": {
                key: ledger[key]
                for key in (
                    "currency",
                    "known_usd_low",
                    "upper_usd_including_holds",
                    "price_source",
                )
            }
            if ledger is not None
            else None,
        },
        "policies": [
            {
                "world": b.world,
                "direction": b.direction,
                "repetition": b.repetition,
                "policy": p,
                **proof,
            }
            for b, p, proof in proofs
        ],
    }
    if by_actor is not None:
        result["by_actor"] = by_actor
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--spend-dir", type=Path)
    parser.add_argument(
        "--design", choices=("availability", "role-access"), default="availability"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            analyze(args.study, args.spend_dir, design=args.design),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
