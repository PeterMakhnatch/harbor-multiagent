#!/bin/bash
set -eu

exec python3 - <<'PY'
import json
from pathlib import Path

from overcooked_ai_py.mdp.actions import Action
from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld

FINAL_TICK = 38


def decode_action(value, tick):
    if value == Action.INTERACT:
        return Action.INTERACT
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(type(component) is int for component in value)
    ):
        direction = tuple(value)
        if direction in Action.MOTION_ACTIONS:
            return direction
    raise SystemExit(f"Invalid action at tick {tick}: {value!r}")


lines = Path("/logs/world/kitchen.jsonl").read_bytes().splitlines(keepends=True)
if len(lines) != FINAL_TICK:
    raise SystemExit(f"Expected {FINAL_TICK} transitions, got {len(lines)}")

mdp = OvercookedGridworld.from_layout_name("cramped_room")
state = mdp.get_standard_start_state()
hits_goal = True
for tick, line in enumerate(lines, 1):
    row = json.loads(line)
    actions = row.get("joint_action") if isinstance(row, dict) else None
    if not isinstance(actions, list) or len(actions) != 2:
        raise SystemExit(f"Missing joint action for two chefs at tick {tick}")
    joint_action = tuple(decode_action(action, tick) for action in actions)
    state, info = mdp.get_state_transition(state, joint_action)
    sparse = info["sparse_reward_by_agent"]
    deliveries = info["event_infos"]["soup_delivery"]
    expected = {
        "tick": tick,
        "joint_action": joint_action,
        "state": state.to_dict(),
        "reward": sum(sparse),
        "sparse_reward_by_agent": sparse,
        "shaped_reward_by_agent": info["shaped_reward_by_agent"],
        "soup_delivery_by_agent": deliveries,
    }
    canonical = json.dumps(expected, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if state.timestep != tick or line != (canonical + "\n").encode():
        raise SystemExit(f"Native replay mismatch at tick {tick}")

    final = tick == FINAL_TICK
    hits_goal = hits_goal and sparse == [0, 20 if final else 0] and deliveries == [False, final]

Path("/logs/verifier/reward.json").write_text(json.dumps({"reward": float(hits_goal)}) + "\n")
PY
