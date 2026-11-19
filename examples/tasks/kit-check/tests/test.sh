#!/bin/bash
set -eu

python3 - <<'PY'
import json
from itertools import permutations
from pathlib import Path

CHOICES = ["K1", "K2", "K3", "K4"]
SNAPSHOT = Path("/logs/world/kit-check.json")
REWARD = Path("/logs/verifier/reward.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def score(snapshot):
    require(snapshot["schema"] == "kit-check/v1", "invalid schema")
    mode = snapshot["mode"]
    require(mode in ("visible", "silent"), "invalid mode")
    gold = list(permutations(CHOICES, 3))
    cases = snapshot["cases"]
    require(isinstance(cases, list) and len(cases) == len(gold), "expected 24 cases")

    seen = set()
    correct_count = 0
    for case in cases:
        seed = case["seed"]
        require(type(seed) is int and 0 <= seed < len(gold), "invalid seed")
        require(seed not in seen, f"repeated seed {seed}")
        seen.add(seed)
        correct, left_distractor, right_distractor = gold[seed]

        reviews = case["reviews"]
        observation = case["observation"]
        require(observation["choices"] == CHOICES, f"seed {seed}: invalid choices")
        for seat, distractor in (("left", left_distractor), ("right", right_distractor)):
            pair = reviews[seat]
            require(
                pair in ([correct, distractor], [distractor, correct]),
                f"seed {seed}: invalid {seat} review",
            )
            observed = pair if mode == "visible" else None
            require(observation[seat] == observed, f"seed {seed}: invalid {seat} observation")

        choice = case["choice"]
        require(choice in CHOICES, f"seed {seed}: invalid or missing choice")
        require(case["correct"] == correct, f"seed {seed}: invalid correct kit")
        success = choice == correct
        require(case["success"] is success, f"seed {seed}: invalid success claim")
        correct_count += success

    return correct_count / len(gold)


try:
    reward = score(json.loads(SNAPSHOT.read_text(encoding="utf-8")))
except (OSError, ValueError, KeyError, TypeError) as error:
    raise SystemExit(f"No kit-check reward: {error}") from error

REWARD.write_text(json.dumps({"reward": reward}) + "\n", encoding="utf-8")
PY
