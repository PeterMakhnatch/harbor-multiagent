"""Recompute a native prefix's future with fixed policies. Not a native trial."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from seatlab.tape import write_lines
from seatlab.world_order import read_world_order

SCHEMA = "seatlab.pz-branch/v1"
_NAMES = ("low", "high", "rps-copy", "rps-beat-last")
_ROOT = Path(__file__).resolve().parent


def _sha(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _policies(items: list[str] | None, roles: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in items or []:
        role, sep, name = item.partition("=")
        if not sep or not role or not name or role in found or name not in _NAMES:
            raise ValueError(f"invalid policy {item!r}")
        found[role] = name
    if set(found) != roles:
        raise ValueError("missing policy")
    return found


def _frame(order: Any, role: str, index: int) -> dict[str, Any]:
    turn = sum(role in group for group in order.steps[: index + 1])
    frames = order.episodes[role]["frames"]
    if not 1 <= turn <= len(frames) or frames[turn - 1]["basis"] != "bound":
        raise ValueError(f"step {index + 1} missing bound frame for {role}")
    return frames[turn - 1]


def _views(live: dict[str, Any], order: Any, index: int) -> None:
    if tuple(sorted(live)) != order.steps[index]:
        raise ValueError(f"step {index + 1} role group mismatch")
    for role, view in live.items():
        frame = _frame(order, role, index)
        if frame["view"] != view or frame["view_sha256"] != _sha(view):
            raise ValueError(f"step {index + 1} bound view mismatch for {role}")


def _runtime() -> tuple[Any, Any]:
    runtime = str(_ROOT / "runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    from actor import choose
    from engine import World

    return World, choose


def _play(
    world: Any, order: Any, at_step: int, policies: dict[str, str], choose: Any
) -> dict:
    for index in range(at_step - 1):
        live = world.pending()
        _views(live, order, index)
        actions = {}
        for role in order.steps[index]:
            observed = _frame(order, role, index).get("observed") or {}
            action = observed.get("action") if isinstance(observed, dict) else None
            legal = live[role].get("legal_actions")
            if (
                type(action) is not int
                or not isinstance(legal, list)
                or action not in legal
            ):
                raise ValueError(
                    f"step {index + 1} recorded action for {role} is not legal"
                )
            actions[role] = action
        world.advance(actions)
    live = world.pending()
    _views(live, order, at_step - 1)
    while live:
        world.advance(
            {role: choose(policies[role], view) for role, view in live.items()}
        )
        live = world.pending()
    done = world.finish()
    return {
        "schema": SCHEMA,
        "source_native_trial_id": order.native_trial_id,
        "case_sha256": done["case_sha256"],
        "at_step": at_step,
        "matched_prefix_steps": at_step - 1,
        "policies": {role: policies[role] for role in sorted(policies)},
        "simulated_steps": done["steps"][at_step - 1 :],
        "returns": done["returns"],
        "terminated": done["terminated"],
        "truncated": done["truncated"],
    }


def run(
    spec_path: Path,
    source: Path,
    at_step: int,
    policies: list[str] | None,
    trust: str,
    out: Path,
) -> dict[str, Any]:
    if os.path.lexists(out):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), out)
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid spec: {exc}") from exc
    factory = spec.get("factory") if isinstance(spec, dict) else None
    if type(factory) is not str or factory != trust:
        raise ValueError("trust-factory does not match spec")
    roles = spec.get("roles")
    if not isinstance(roles, dict):
        raise ValueError("spec roles are missing")
    chosen = _policies(policies, set(roles))
    order = read_world_order(source / "episodes.jsonl", source / "order.json")
    if _sha(spec) != order.case_sha256:
        raise ValueError("case digest does not hash the spec")
    if UUID(order.native_trial_id).int == 0:
        raise ValueError("source native trial id must be nonzero")
    if set(roles) != set(order.episodes):
        raise ValueError("spec roles do not match the source")
    if type(at_step) is not int or not 1 <= at_step <= len(order.steps):
        raise ValueError("at-step is outside the source")
    World, choose = _runtime()
    world = World(spec)
    try:
        report = _play(world, order, at_step, chosen, choose)
        if not out.parent.is_dir():
            raise ValueError(f"output parent does not exist: {out.parent}")
        write_lines(out, [report])
    finally:
        world.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("--at-step", type=int, required=True)
    parser.add_argument("--policy", action="append")
    parser.add_argument("--trust-factory", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        run(
            args.spec,
            args.source,
            args.at_step,
            args.policy,
            args.trust_factory,
            args.out,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"branch.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
