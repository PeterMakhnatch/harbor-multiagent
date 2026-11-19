"""Analytical rps_v2 window reference. Replays the public game. No model claim."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping

import numpy as np
from pettingzoo.classic.rps_v2 import parallel_env

ROLES = {"p0": "player_0", "p1": "player_1"}
NAMES = {agent: role for role, agent in ROLES.items()}
WINDOW = "seatlab.actor-window/v1"
NATIVE = "seatlab.pz-native/v1"
SEED, CYCLES, LIMIT, P1_DEPTH = 19, 12, 128, 2


class Mismatch(Exception):
    def __init__(self, loc: str) -> None:
        self.loc = loc


def canon(value: object) -> bytes:
    """Sorted compact ASCII JSON. No framing newline."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(value: object) -> str:
    return hashlib.sha256(canon(value)).hexdigest()


def norm(value: object) -> object:
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float:
        if not np.isfinite(value):
            raise RuntimeError("non-finite number")
        return value
    if isinstance(value, np.ndarray):
        return norm(value.tolist())
    if isinstance(value, np.generic):
        return norm(value.item())
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise RuntimeError("observation keys")
        return {key: norm(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [norm(item) for item in value]
    raise RuntimeError("value is not JSON-safe")


def num(value: object) -> int | float:
    out = norm(value)
    if isinstance(out, bool) or not isinstance(out, (int, float)):
        raise RuntimeError("reward is not numeric")
    return out


def legal(space: object, obs: object, info: object) -> list[int]:
    start, size = int(getattr(space, "start", 0)), int(space.n)
    mask = obs.get("action_mask") if isinstance(obs, dict) else None
    if mask is None and isinstance(info, Mapping):
        mask = info.get("action_mask")
    if mask is None:
        return list(range(start, start + size))
    bits = norm(mask)
    if not isinstance(bits, list) or len(bits) != size:
        raise RuntimeError("action mask length")
    return [start + i for i, bit in enumerate(bits) if bit]


def actor_window(current: dict, history: list, depth: int) -> dict:
    """Last K own records, oldest first. Depth 0 is empty, not history[-0:]."""
    if type(depth) is not int or not 0 <= depth <= 16:
        raise ValueError("depth")
    prior = history[-depth:] if depth else []
    return {
        "schema": WINDOW,
        "current": current,
        "history": [{"view": item["view"], "action": item["action"]} for item in prior],
    }


def predict(window: dict) -> int:
    """Predictor reads only this window. Start sentinel 3 predicts 0."""
    obs = window["current"]["observation"]
    if type(obs) is not int or obs not in (0, 1, 2, 3):
        raise RuntimeError("observation")
    if obs == 3 or obs != 0:
        guess = 0
    else:
        history = window["history"]
        last = history[-1]["view"]["observation"] if history else None
        guess = 2 if last == 1 else 1
    return (guess + 1) % 3


def phase(index: int, first: int) -> int:
    """Analytical cycle by turn index, not the actor's history implementation."""
    if index % 2 == 0:
        return 0
    return first if index % 4 == 1 else 3 - first


def _mark(ended: dict, cut: dict, terms: Mapping, truncs: Mapping) -> None:
    for label, flags, sink in (
        ("terminations", terms, ended),
        ("truncations", truncs, cut),
    ):
        for agent, flag in flags.items():
            role = NAMES.get(agent)
            if role is None:
                raise RuntimeError(f"unknown agent in {label}")
            if flag:
                sink[role] = True


def play(cycle: int, depth: int) -> dict:
    """Run the public env. Does not read a native report."""
    if cycle not in (1, 2) or depth not in (0, 1, 4):
        raise ValueError("cycle or depth")
    env = parallel_env(max_cycles=CYCLES)
    try:
        obs, info = env.reset(seed=SEED)
        totals = {role: 0 for role in ROLES}
        ended, cut = dict.fromkeys(ROLES, False), dict.fromkeys(ROLES, False)
        history = {role: [] for role in ROLES}
        sequences = {role: [] for role in ROLES}
        byte_sums = {role: 0 for role in ROLES}
        degrees = {"p0": depth, "p1": P1_DEPTH}
        steps = []
        for step in range(1, LIMIT + 1):
            if not obs or all(ended[role] or cut[role] for role in ROLES):
                break
            decisions, actions = {}, {}
            for role, agent in ROLES.items():
                if agent not in obs:
                    raise RuntimeError(f"missing observation for {agent}")
                seen = norm(obs[agent])
                view = {
                    "observation": seen,
                    "legal_actions": legal(
                        env.action_space(agent), seen, info.get(agent, {})
                    ),
                }
                framed = actor_window(view, history[role], degrees[role])
                raw = canon(framed)
                action = (
                    predict(framed)
                    if role == "p0"
                    else phase(len(sequences[role]), cycle)
                )
                if action not in view["legal_actions"]:
                    raise RuntimeError("policy action is not legal")
                decisions[role] = {
                    "view": view,
                    "view_sha256": digest(view),
                    "action": action,
                    "window_bytes": len(raw),
                    "window_sha256": hashlib.sha256(raw).hexdigest(),
                }
                actions[agent] = action
                sequences[role].append(action)
                byte_sums[role] += len(raw)
            steps.append({"step": step, "group": ["p0", "p1"], "decisions": decisions})
            obs, rewards, terms, truncs, info = env.step(actions)
            for agent, reward in rewards.items():
                role = NAMES.get(agent)
                if role is None:
                    raise RuntimeError(f"reward for unknown agent {agent}")
                totals[role] += num(reward)
            for role in ROLES:
                decisions_role = decisions[role]
                history[role].append(
                    {"view": decisions_role["view"], "action": decisions_role["action"]}
                )
            _mark(ended, cut, terms, truncs)
        else:
            raise RuntimeError("world-step limit")
    finally:
        env.close()
    if len(steps) != CYCLES or not all(ended[role] or cut[role] for role in ROLES):
        raise RuntimeError("incomplete world")
    return {
        "schema": "seatlab.window-reference/v1",
        "cycle": cycle,
        "depth": depth,
        "seed": SEED,
        "p0_actions": sequences["p0"],
        "p1_actions": sequences["p1"],
        "payoffs": totals,
        "flags": {"terminated": ended, "truncated": cut},
        "degrees": degrees,
        "window_bytes": byte_sums,
        "groups": [step["group"] for step in steps],
        "steps": steps,
    }


def need(ok: object, loc: str) -> None:
    if not ok:
        raise Mismatch(loc)


def compare(report: object, result: dict) -> None:
    """Stop at the first group, view, action, return, or end-flag discrepancy."""
    need(isinstance(report, dict) and report.get("schema") == NATIVE, "schema")
    need(report.get("roles") == ROLES, "roles")
    got_steps = report.get("steps")
    exp_steps = result["steps"]
    need(isinstance(got_steps, list) and len(got_steps) == len(exp_steps), "steps")
    for index, (got, exp) in enumerate(zip(got_steps, exp_steps)):
        loc = f"steps[{index}]"
        decisions = got.get("decisions") if isinstance(got, dict) else None
        need(isinstance(got, dict) and got.get("step") == exp["step"], f"{loc}.step")
        need(
            isinstance(decisions, dict) and set(decisions) == set(exp["group"]),
            f"{loc}.group",
        )
        for role in exp["group"]:
            base, item, exp_d = (
                f"{loc}.decisions.{role}",
                decisions[role],
                exp["decisions"][role],
            )
            need(
                isinstance(item, dict) and item.get("view") == exp_d["view"],
                f"{base}.view",
            )
            need(
                type(item.get("action")) is int and item["action"] == exp_d["action"],
                f"{base}.action",
            )
            need(item.get("view_sha256") == exp_d["view_sha256"], f"{base}.view_sha256")
    for label, exp_map in (
        ("returns", result["payoffs"]),
        ("terminated", result["flags"]["terminated"]),
        ("truncated", result["flags"]["truncated"]),
    ):
        got_map = report.get(label)
        need(isinstance(got_map, dict) and set(got_map) == set(ROLES), label)
        for role in ROLES:
            got, exp = got_map[role], exp_map[role]
            if label == "returns":
                need(
                    type(got) is not bool
                    and isinstance(got, (int, float))
                    and got == exp,
                    f"{label}.{role}",
                )
            else:
                need(got is exp, f"{label}.{role}")


def native_report(result: dict) -> dict:
    return {
        "schema": NATIVE,
        "roles": dict(ROLES),
        "steps": [
            {
                "step": step["step"],
                "decisions": {
                    role: {
                        "view": decision["view"],
                        "view_sha256": decision["view_sha256"],
                        "action": decision["action"],
                    }
                    for role, decision in step["decisions"].items()
                },
            }
            for step in result["steps"]
        ],
        "returns": dict(result["payoffs"]),
        "terminated": dict(result["flags"]["terminated"]),
        "truncated": dict(result["flags"]["truncated"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Independent public-game reference for the frozen window cells"
    )
    parser.add_argument("--cycle", required=True, type=int, choices=(1, 2))
    parser.add_argument("--depth", required=True, type=int, choices=(0, 1, 4))
    parser.add_argument("--world")
    args = parser.parse_args(argv)
    try:
        result = play(args.cycle, args.depth)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.world:
        try:
            with open(args.world, encoding="utf-8") as handle:
                compare(json.load(handle), result)
        except Mismatch as exc:
            print(f"mismatch: {exc.loc}", file=sys.stderr)
            return 1
        except (OSError, json.JSONDecodeError, ValueError):
            print("mismatch: report", file=sys.stderr)
            return 1
    sys.stdout.write(canon(result).decode("ascii") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
