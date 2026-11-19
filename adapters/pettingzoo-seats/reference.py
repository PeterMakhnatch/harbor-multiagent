"""Cross-implementation/transport parity using the same underlying third-party game, not independent validation of PettingZoo."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping

import numpy as np
from pettingzoo.classic.connect_four_v3 import env as connect_four
from pettingzoo.classic.rps_v2 import parallel_env as rps

ROLES = {"p0": "player_0", "p1": "player_1"}
NAMES = {agent: role for role, agent in ROLES.items()}
POLICIES = ("low", "high", "rps-copy", "rps-beat-last")
LIMIT = 128
PARITY = (
    "cross-implementation/transport parity using the same underlying "
    "third-party game, not independent validation of PettingZoo"
)


class Mismatch(Exception):
    def __init__(self, loc: str) -> None:
        self.loc = loc


def canon(obj: object) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(obj: object) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


def norm(value: object) -> object:
    if isinstance(value, np.ndarray):
        return norm(value.tolist())
    if isinstance(value, np.generic):
        return norm(value.item())
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not np.isfinite(value):
            raise RuntimeError("non-finite number")
        return value
    if isinstance(value, Mapping):
        return {str(key): norm(item) for key, item in value.items()}
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


def choose(policy: str, choices: list[int], obs: object) -> int:
    if not choices:
        raise RuntimeError("no legal action")
    if policy in ("low", "high"):
        return choices[0 if policy == "low" else -1]
    seen = obs if type(obs) is int and 0 <= obs <= 2 else None
    action = 0 if seen is None else seen if policy == "rps-copy" else (seen + 1) % 3
    if action not in choices:
        raise RuntimeError("policy action is not legal")
    return action


def frame(obs: object, choices: list[int], action: int) -> dict[str, object]:
    body = {"observation": obs, "legal_actions": choices}
    return {"view": body, "view_sha256": digest(body), "action": action}


def mark(ended: dict, cut: dict, terms: Mapping, truncs: Mapping) -> None:
    for agent, flag in terms.items():
        ended[NAMES[agent]] = bool(flag)
        cut[NAMES[agent]] = bool(truncs[agent])


def fresh() -> tuple:
    return (
        {role: 0 for role in ROLES},
        dict.fromkeys(ROLES, False),
        dict.fromkeys(ROLES, False),
        [],
    )


def simultaneous(env: object, policies: dict[str, str], seed: int) -> tuple:
    obs, info = env.reset(seed=seed)
    totals, ended, cut, steps = fresh()
    for step in range(1, LIMIT + 1):
        if not obs:
            break
        decisions, actions = {}, {}
        for role, agent in ROLES.items():
            seen = norm(obs[agent])
            choices = legal(env.action_space(agent), seen, info.get(agent, {}))
            action = choose(policies[role], choices, seen)
            decisions[role], actions[agent] = frame(seen, choices, action), action
        steps.append({"step": step, "decisions": decisions})
        obs, rewards, terms, truncs, info = env.step(actions)
        for role, agent in ROLES.items():
            if agent in rewards:
                totals[role] += num(rewards[agent])
        mark(ended, cut, terms, truncs)
        if all(ended[role] or cut[role] for role in ROLES):
            break
    else:
        raise RuntimeError("world-step limit")
    return steps, totals, ended, cut


def turn_taking(env: object, policies: dict[str, str], seed: int) -> tuple:
    if env.metadata.get("is_parallelizable"):
        raise RuntimeError("turn-taking game is parallelizable")
    env.reset(seed=seed)
    totals, ended, cut, steps = fresh()
    while env.agents:
        agent = env.agent_selection
        if env.terminations[agent] or env.truncations[agent]:
            mark(ended, cut, env.terminations, env.truncations)
            env.step(None)
            continue
        if len(steps) >= LIMIT:
            raise RuntimeError("world-step limit")
        role, seen = NAMES[agent], norm(env.observe(agent))
        choices = legal(env.action_space(agent), seen, env.infos.get(agent, {}))
        action = choose(policies[role], choices, seen)
        before = {name: num(reward) for name, reward in env.rewards.items()}
        env.step(action)
        for name, reward in env.rewards.items():
            totals[NAMES[name]] += num(reward) - before.get(name, 0)
        mark(ended, cut, env.terminations, env.truncations)
        steps.append(
            {"step": len(steps) + 1, "decisions": {role: frame(seen, choices, action)}}
        )
    return steps, totals, ended, cut


def trajectory(steps: list[dict]) -> str:
    rows = []
    for item in steps:
        for role in ROLES:
            decision = item["decisions"].get(role)
            if decision is None:
                continue
            rows.append(
                {
                    "step": item["step"],
                    "role": role,
                    "observation": decision["view"]["observation"],
                    "legal_actions": decision["view"]["legal_actions"],
                    "action": decision["action"],
                }
            )
    return digest(rows)


def diff(got: object, exp: object, loc: str) -> str | None:
    if isinstance(exp, bool) or isinstance(got, bool):
        return None if type(got) is bool and got is exp else loc
    if isinstance(exp, dict):
        if not isinstance(got, dict):
            return loc
        for key in exp:
            if key not in got:
                return f"{loc}.{key}"
            found = diff(got[key], exp[key], f"{loc}.{key}")
            if found:
                return found
        return loc if set(got) - set(exp) else None
    if isinstance(exp, list):
        if not isinstance(got, list) or len(got) != len(exp):
            return loc
        for index, (left, right) in enumerate(zip(got, exp)):
            found = diff(left, right, f"{loc}[{index}]")
            if found:
                return found
        return None
    if isinstance(exp, (int, float)) and isinstance(got, (int, float)):
        return None if got == exp else loc
    return None if type(got) is type(exp) and got == exp else loc


def need(ok: object, loc: str) -> None:
    if not ok:
        raise Mismatch(loc)


def compare(
    report: object, steps: list, returns: dict, ended: dict, cut: dict, case: str
) -> None:
    need(
        isinstance(report, dict) and report.get("schema") == "seatlab.pz-native/v1",
        "schema",
    )
    need(report.get("case_sha256") == case, "case_sha256")
    need(report.get("roles") == ROLES, "roles")
    got_steps = report.get("steps")
    need(isinstance(got_steps, list) and len(got_steps) == len(steps), "steps")
    for index, (got, exp) in enumerate(zip(got_steps, steps)):
        loc, got_d = (
            f"steps[{index}]",
            got.get("decisions") if isinstance(got, dict) else None,
        )
        need(isinstance(got, dict) and got.get("step") == exp["step"], f"{loc}.step")
        need(
            isinstance(got_d, dict) and set(got_d) == set(exp["decisions"]),
            f"{loc}.decisions",
        )
        for role, exp_d in exp["decisions"].items():
            base, got_dcs = f"{loc}.decisions.{role}", got_d[role]
            view, expect = (
                got_dcs.get("view") if isinstance(got_dcs, dict) else None,
                exp_d["view"],
            )
            need(isinstance(view, dict) and set(view) == set(expect), f"{base}.view")
            found = diff(
                view.get("observation"),
                expect["observation"],
                f"{base}.view.observation",
            )
            found = found or diff(
                view.get("legal_actions"),
                expect["legal_actions"],
                f"{base}.view.legal_actions",
            )
            if found:
                raise Mismatch(found)
            need(
                type(got_dcs.get("action")) is int
                and got_dcs["action"] == exp_d["action"],
                f"{base}.action",
            )
            need(
                got_dcs.get("view_sha256") == exp_d["view_sha256"],
                f"{base}.view_sha256",
            )
    for label, exp_map in (
        ("returns", returns),
        ("terminated", ended),
        ("truncated", cut),
    ):
        got_map = report.get(label)
        need(isinstance(got_map, dict), label)
        for role in ROLES:
            need(role in got_map, f"{label}.{role}")
            if label == "returns":
                got_num = got_map[role]
                need(
                    type(got_num) is not bool
                    and isinstance(got_num, (int, float))
                    and got_num == exp_map[role],
                    f"{label}.{role}",
                )
            else:
                need(got_map[role] is exp_map[role], f"{label}.{role}")
        need(set(got_map) == set(ROLES), label)


def world_spec(game: str, seed: int) -> dict[str, object]:
    rock = game == "rps"
    factory = (
        "pettingzoo.classic.rps_v2:parallel_env"
        if rock
        else "pettingzoo.classic.connect_four_v3:env"
    )
    return {
        "schema": "seatlab.pz-world/v1",
        "api": "parallel" if rock else "aec-turns",
        "factory": factory,
        "kwargs": {"max_cycles": 6} if rock else {},
        "roles": ROLES,
        "seed": seed,
        "max_world_steps": LIMIT,
    }


def play(game: str, policies: dict[str, str], seed: int) -> tuple:
    env = rps(max_cycles=6) if game == "rps" else connect_four()
    try:
        return (simultaneous if game == "rps" else turn_taking)(env, policies, seed)
    finally:
        env.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=PARITY)
    parser.add_argument("game", choices=("rps", "connect-four"))
    parser.add_argument("--policies", required=True)
    parser.add_argument("--seed", type=int, default=19)
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    names = [item.strip() for item in args.policies.split(",") if item.strip()]
    if len(names) == 1:
        names *= 2
    if len(names) != 2 or any(name not in POLICIES for name in names):
        parser.error("policies must be low, high, rps-copy, rps-beat-last")
    policies, case = (
        dict(zip(ROLES, names, strict=True)),
        world_spec(args.game, args.seed),
    )
    try:
        steps, returns, ended, cut = play(args.game, policies, args.seed)
        if not steps or not all(ended[role] or cut[role] for role in ROLES):
            raise RuntimeError("incomplete world")
        if args.report:
            with open(args.report, encoding="utf-8") as handle:
                compare(json.load(handle), steps, returns, ended, cut, digest(case))
    except Mismatch as exc:
        print(f"mismatch: {exc.loc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError):
        print("mismatch: report", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    summary = {
        "schema": "seatlab.pz-reference/v1",
        "parity": PARITY,
        "game": args.game,
        "seed": args.seed,
        "policies": policies,
        "case_sha256": digest(case),
        "step_count": len(steps),
        "grouping": "simultaneous" if args.game == "rps" else "turn-taking",
        "groups": [
            [role for role in ROLES if role in item["decisions"]] for item in steps
        ],
        "returns": returns,
        "end_states": {"terminated": ended, "truncated": cut},
        "trajectory_sha256": trajectory(steps),
    }
    sys.stdout.write(canon(summary).decode("ascii") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
