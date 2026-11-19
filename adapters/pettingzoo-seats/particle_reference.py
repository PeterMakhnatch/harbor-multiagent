import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

from mpe2.simple_spread_v3 import parallel_env

ROLES = {"p0": "agent_0", "p1": "agent_1", "p2": "agent_2"}
ABS, REL = 1e-7, 1e-6
DUMP = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": True}
PRESET = Path(__file__).parent / "presets" / "simple-spread.json"
SPEC = json.loads(PRESET.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="mpe2 simple_spread parity")
    parser.add_argument("report")
    parser.add_argument("--p2", required=True, choices=("low", "high"))
    args = parser.parse_args()
    high, gaps = args.p2 == "high", [0.0, 0.0]
    env = parallel_env(N=3, max_cycles=4, continuous_actions=False)
    try:
        obs, _info = env.reset(seed=19)
        totals = {role: 0.0 for role in ROLES}
        ended = dict.fromkeys(ROLES, False)
        cut, steps = dict.fromkeys(ROLES, False), []
        for step in range(1, 5):
            decisions, actions = {}, {}
            for role, agent in ROLES.items():
                space = env.action_space(agent)
                start = int(getattr(space, "start", 0))
                choices = list(range(start, start + int(space.n)))
                action = choices[-1 if high and role == "p2" else 0]
                seen = obs[agent].tolist()
                view = {"legal_actions": choices, "observation": seen}
                decisions[role] = {"action": action, "view": view}
                actions[agent] = action
            steps.append({"decisions": decisions, "step": step})
            obs, rewards, terms, truncs, _info = env.step(actions)
            for role, agent in ROLES.items():
                totals[role] += float(rewards[agent])
                ended[role] = bool(ended[role] or terms[agent])
                cut[role] = bool(cut[role] or truncs[agent])
        if len(steps) != 4 or any(ended.values()) or not all(cut.values()):
            raise RuntimeError("truncated")
        with open(args.report, encoding="utf-8") as handle:
            report = json.load(handle)
        raw = json.dumps(SPEC, allow_nan=False, **DUMP)
        case = hashlib.sha256(raw.encode("ascii")).hexdigest()
        if report.get("case_sha256") != case:
            raise RuntimeError("case_sha256")
        steps_got = report.get("steps")
        if type(steps_got) is not list or len(steps_got) != 4:
            raise RuntimeError("steps")
        for i, (row, exp) in enumerate(zip(steps_got, steps)):
            got_d = row.get("decisions") if type(row) is dict else None
            if type(row) is not dict or row.get("step") != exp["step"]:
                raise RuntimeError(f"steps[{i}].step")
            if type(got_d) is not dict or set(got_d) != set(ROLES):
                raise RuntimeError(f"steps[{i}].decisions")
            for role in ROLES:
                base = f"steps[{i}].decisions.{role}"
                one, expect = got_d[role], exp["decisions"][role]
                view = one.get("view") if type(one) is dict else {}
                legal = view.get("legal_actions")
                ok = type(legal) is list and legal == expect["view"]["legal_actions"]
                if not ok or any(type(x) is not int for x in legal or []):
                    raise RuntimeError(f"{base}.view.legal_actions")
                if type(one.get("action")) is not int or one["action"] != expect["action"]:
                    raise RuntimeError(f"{base}.action")
                left, right = view.get("observation"), expect["view"]["observation"]
                if type(left) is not list or len(left) != len(right):
                    raise RuntimeError(f"{base}.view.observation")
                for j, (a, b) in enumerate(zip(left, right)):
                    where = f"{base}.view.observation[{j}]"
                    bad = type(a) not in (int, float) or type(b) not in (int, float)
                    if type(a) is bool or bad:
                        raise RuntimeError(where)
                    gaps[0] = max(gaps[0], abs(float(a) - float(b)))
                    if not math.isclose(a, b, rel_tol=REL, abs_tol=ABS):
                        raise RuntimeError(where)
        for role, exp_ret in totals.items():
            got_ret = (report.get("returns") or {}).get(role)
            if type(got_ret) not in (int, float):
                raise RuntimeError(f"returns.{role}")
            gaps[1] = max(gaps[1], abs(float(got_ret) - exp_ret))
            if not math.isclose(got_ret, exp_ret, rel_tol=REL, abs_tol=ABS):
                raise RuntimeError(f"returns.{role}")
        for label, exp_map in (("terminated", ended), ("truncated", cut)):
            got_map = report.get(label)
            bad = type(got_map) is not dict
            if bad or any(got_map.get(r) is not exp_map[r] for r in ROLES):
                raise RuntimeError(label)
        out = {"decisions": 12, "steps": 4, "returns": totals}
        out["terminated"], out["truncated"] = ended, cut
        out["max_abs_observation_difference"] = gaps[0]
        out["max_abs_return_difference"] = gaps[1]
        sys.stdout.write(json.dumps(out, allow_nan=False, **DUMP) + "\n")
    except Exception as exc:
        name = exc.args[0] if type(exc) is RuntimeError else "report"
        print(f"mismatch: {name}", file=sys.stderr)
        return 1
    finally:
        env.close()
    return 0

raise SystemExit(main())
