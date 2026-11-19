"""Compare a recorded branch simulation. It is not a native trial or a completion reward."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from seatlab.tape import TapeError, decode, digest
from seatlab.world_order import read_world_order

SCHEMA, RESULT, OUTCOMES = (
    "seatlab.pz-branch/v1",
    "seatlab.pz-branch-compare/v1",
    "seatlab.pz-outcomes/v1",
)
BRANCH = frozenset(
    {
        "schema",
        "source_native_trial_id",
        "case_sha256",
        "at_step",
        "matched_prefix_steps",
        "policies",
        "simulated_steps",
        "returns",
        "terminated",
        "truncated",
    }
)
STEP, DEC = (
    frozenset({"step", "decisions"}),
    frozenset({"view", "view_sha256", "action"}),
)
OUT = frozenset(
    {
        "schema",
        "native_trial_id",
        "case_sha256",
        "completion",
        "returns",
        "terminated",
        "truncated",
    }
)
_TAIL = (None, None, None, None, None)


class Reject(ValueError):
    """Corrupt branch or source, or a different case. Disagreement is not corrupt."""


def _fail(msg: str) -> Any:
    raise Reject(msg)


def _eq(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_eq(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_eq(a, b) for a, b in zip(left, right))
    return left == right


def _load(path: Path) -> dict[str, Any]:
    try:
        value = decode(path.read_bytes())
    except (OSError, TapeError) as exc:
        raise Reject(str(exc)) from exc
    return value if isinstance(value, dict) else _fail(f"{path.name}: expected object")


def _cover(value: object, roles: set[str], where: str, flag: bool) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != roles:
        _fail(f"{where} must cover every role")
    if flag:
        if any(type(item) is not bool for item in value.values()):
            _fail(f"{where} must be boolean")
        return dict(value)
    out = {}
    for role, item in value.items():
        if (
            type(item) is bool
            or not isinstance(item, (int, float))
            or (isinstance(item, float) and not math.isfinite(item))
        ):
            _fail(f"{where}.{role}")
        out[role] = item
    return out


def _rows(order: Any) -> list[dict[str, Any]]:
    cursor, rows = dict.fromkeys(order.episodes, 0), []
    for group in order.steps:
        row = {}
        for role in group:
            frame = order.episodes[role]["frames"][cursor[role]]
            cursor[role] += 1
            if frame["basis"] != "bound" or frame["observed"] is None:
                _fail(f"{role} is not a bound actor decision")
            row[role] = frame
        rows.append(row)
    return rows


def _open(path: Path) -> Any:
    try:
        return read_world_order(path / "episodes.jsonl", path / "order.json")
    except (OSError, TapeError) as exc:
        raise Reject(str(exc)) from exc


def _hit(step: int, kind: str, role: str | None = None) -> dict[str, Any]:
    return {"step": step, "kind": kind, "role": role}


def _prefix(
    src: list, dst: list, at: int
) -> tuple[int, bool | None, dict[str, Any] | None]:
    """Count view+action steps before the branch. Check the branch group and views apart."""
    measured = 0
    for index in range(min(len(src), len(dst), at)):
        step, left, right = index + 1, src[index], dst[index]
        branch = step == at
        if tuple(sorted(left)) != tuple(sorted(right)):
            return measured, False if branch else None, _hit(step, "group")
        for role in left:
            if not _eq(left[role]["view"], right[role]["view"]):
                return measured, False if branch else None, _hit(step, "view", role)
            if not branch and not _eq(
                left[role]["observed"]["action"], right[role]["observed"]["action"]
            ):
                return measured, None, _hit(step, "action", role)
        if branch:
            return measured, True, None
        measured += 1
    return measured, None, _hit(measured + 1, "length")


def _bind(raw: object, src: list, at: int, roles: set[str]) -> list:
    if not isinstance(raw, list) or not raw:
        _fail("simulated_steps must be the nonempty suffix from at_step")
    for offset, row in enumerate(raw):
        step = at + offset
        decisions = row.get("decisions") if isinstance(row, dict) else None
        bad_row = (
            not isinstance(row, dict)
            or set(row) != STEP
            or type(row.get("step")) is not int
            or row["step"] != step
        )
        if (
            bad_row
            or not isinstance(decisions, dict)
            or not decisions
            or set(decisions) - roles
        ):
            _fail("simulated_steps must be the World.finish suffix from at_step")
        if step == at and tuple(sorted(decisions)) != tuple(sorted(src[at - 1])):
            _fail("branch step group does not match the source decision")
        for role, item in decisions.items():
            if (
                not isinstance(item, dict)
                or set(item) != DEC
                or not isinstance(item.get("view"), dict)
                or digest(item["view"]) != item.get("view_sha256")
            ):
                _fail(f"step {step}: {role} view digest mismatch")
            if step == at and not _eq(item["view"], src[at - 1][role]["view"]):
                _fail("branch step view does not match the source decision")
    return raw


def _suffix(steps: list, dst: list, at: int):
    compared, inputs, actions = 0, True, True
    for offset, row in enumerate(steps):
        step, decisions = at + offset, row["decisions"]
        if step > len(dst):
            return compared, inputs, actions, _hit(step, "length")
        if tuple(sorted(decisions)) != tuple(sorted(dst[step - 1])):
            return compared, False, actions, _hit(step, "group")
        kind = role = None
        for seat in sorted(dst[step - 1]):
            frame = dst[step - 1][seat]
            view_ok = _eq(decisions[seat]["view"], frame["view"])
            act_ok = _eq(decisions[seat]["action"], frame["observed"]["action"])
            inputs, actions = inputs and view_ok, actions and act_ok
            if kind is None and not view_ok:
                kind, role = "view", seat
            elif kind is None and not act_ok:
                kind, role = "action", seat
        compared += 1
        if kind:
            return compared, inputs, actions, _hit(step, kind, role)
    end = at + len(steps) - 1
    return (
        compared,
        inputs,
        actions,
        None if end == len(dst) else _hit(end + 1, "length"),
    )


def _emit(
    status,
    case,
    source_id,
    target_id,
    at,
    policies,
    measured,
    views,
    counts,
    first,
    inputs,
    actions,
    returns,
    ended,
    cut,
):
    return {
        "schema": RESULT,
        "label": "simulation",
        "status": status,
        "case_sha256": case,
        "source_native_trial_id": source_id,
        "target_native_trial_id": target_id,
        "at_step": at,
        "policies": policies,
        "measured_prefix_steps": measured,
        "branch_views_equal": views,
        "prefix_mismatch": {
            "measured": False,
            "prefix_mismatch": True,
            "missing": None,
        }[status],
        "counts": counts,
        "first_mismatch": first,
        "inputs_equal": inputs,
        "actions_equal": actions,
        "returns_equal": returns,
        "terminated_equal": ended,
        "truncated_equal": cut,
    }


def _target(path: Path):
    if not path.is_dir():
        return None
    try:
        order, outcomes = _open(path), _load(path / "outcomes.json")
        roles = set(order.episodes)
        if (
            set(outcomes) != OUT
            or outcomes.get("schema") != OUTCOMES
            or outcomes.get("case_sha256") != order.case_sha256
            or outcomes.get("native_trial_id") != order.native_trial_id
        ):
            return None
        _cover(outcomes["returns"], roles, "returns", False)
        _cover(outcomes["terminated"], roles, "terminated", True)
        _cover(outcomes["truncated"], roles, "truncated", True)
        return order, _rows(order), outcomes
    except (Reject, OSError, TapeError, ValueError):
        return None


def compare(branch_path: Path, source_path: Path, target_path: Path) -> dict[str, Any]:
    branch = _load(Path(branch_path))
    if set(branch) != BRANCH or branch.get("schema") != SCHEMA:
        _fail("branch must be a seatlab.pz-branch/v1 object")
    at, claimed = branch["at_step"], branch["matched_prefix_steps"]
    if type(at) is not int or at < 1 or type(claimed) is not int or claimed < 0:
        _fail("at_step and matched_prefix_steps must be integers")
    order = _open(Path(source_path))
    source = _rows(order)
    if order.case_sha256 != branch["case_sha256"]:
        _fail("different case rejected")
    if order.native_trial_id != branch["source_native_trial_id"]:
        _fail("source projection is not the recorded source trial")
    if at > len(source):
        _fail("at_step has no native source decision")
    roles, policies = set(order.episodes), branch["policies"]
    if (
        not isinstance(policies, dict)
        or set(policies) != roles
        or any(type(v) is not str or not v for v in policies.values())
    ):
        _fail("policies must declare every role")
    declared = {role: policies[role] for role in sorted(roles)}
    sim = _bind(branch["simulated_steps"], source, at, roles)
    got_r, got_t, got_c = (
        _cover(branch["returns"], roles, "returns", False),
        _cover(branch["terminated"], roles, "terminated", True),
        _cover(branch["truncated"], roles, "truncated", True),
    )
    counts: dict[str, int | None] = {
        "source_steps": len(source),
        "target_steps": None,
        "simulated_steps": len(sim),
        "compared_steps": 0,
    }
    loaded, base = (
        _target(Path(target_path)),
        (order.case_sha256, order.native_trial_id),
    )
    if loaded is None:
        return _emit(
            "missing", *base, None, at, declared, 0, None, counts, None, *_TAIL
        )
    target_order, target, outcomes = loaded
    if target_order.case_sha256 != order.case_sha256:
        _fail("different case rejected")
    if target_order.native_trial_id == order.native_trial_id:
        _fail("target must be a distinct native trial")
    counts["target_steps"], measured, views, boundary = (
        len(target),
        *_prefix(source, target, at),
    )
    ident = target_order.native_trial_id
    if boundary is not None:
        return _emit(
            "prefix_mismatch",
            *base,
            ident,
            at,
            declared,
            measured,
            views,
            counts,
            boundary,
            *_TAIL,
        )
    compared, inputs, actions, first = _suffix(sim, target, at)
    counts["compared_steps"] = compared
    names, pay, ended_b, cut_b = (
        sorted(roles),
        outcomes["returns"],
        outcomes["terminated"],
        outcomes["truncated"],
    )
    returns = {
        role: type(pay.get(role)) is not bool and pay.get(role) == got_r[role]
        for role in names
    }
    ended = {
        role: type(ended_b.get(role)) is bool and got_t[role] is ended_b.get(role)
        for role in names
    }
    cut = {
        role: type(cut_b.get(role)) is bool and got_c[role] is cut_b.get(role)
        for role in names
    }
    return _emit(
        "measured",
        *base,
        ident,
        at,
        declared,
        measured,
        views,
        counts,
        first,
        inputs,
        actions,
        returns,
        ended,
        cut,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("branch", type=Path, metavar="BRANCH.json")
    parser.add_argument("source", type=Path, metavar="SOURCE_PROJECTION")
    parser.add_argument("target", type=Path, metavar="TARGET_PROJECTION")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(compare(args.branch, args.source, args.target), sort_keys=True)
        )
    except Reject as exc:
        print(f"compare_branch.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
