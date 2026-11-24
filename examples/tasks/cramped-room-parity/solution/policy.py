"""Apply the scripted actions for this seat through the world HTTP API."""

import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen

WORLD = "http://world:8080"
FINAL_TICK = 38
N, S, E, W = (0, -1), (0, 1), (1, 0), (-1, 0)
WAIT, USE = (0, 0), "interact"

# Ordered by chef0, chef1. No game engine runs in either player container.
JOINT_ACTIONS = (
    (
        (N, E),
        (W, USE),
        (USE, W),
        (WAIT, N),
        (WAIT, USE),  # chef1 adds the first onion.
        (E, S),
        (N, W),
        (USE, S),  # chef0 adds the second onion.
        (W, USE),  # chef1 collects a dish.
        (W, E),
        (USE, WAIT),
        (E, WAIT),
        (N, WAIT),
        (USE, WAIT),  # chef0 adds the third onion.
        (USE, WAIT),  # chef0 starts cooking.
        (W, N),
    )
    + ((WAIT, WAIT),) * 18
    + (
        (WAIT, USE),  # chef1 plates the soup.
        (WAIT, E),
        (WAIT, S),
        (WAIT, USE),  # chef1 serves the soup.
    )
)


def seconds_left(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SystemExit("Timed out before completing the kitchen replay")
    return remaining


def wait_for_tick(expected, deadline):
    while True:
        with urlopen(
            f"{WORLD}/state", timeout=min(2, seconds_left(deadline))
        ) as response:
            tick = json.load(response)["tick"]
        if type(tick) is not int or not 0 <= tick <= expected:
            raise SystemExit(f"Expected tick {expected}, got {tick!r}")
        if tick == expected:
            return
        time.sleep(min(0.05, seconds_left(deadline)))


def main():
    seat = os.environ.get("SEAT")
    if seat not in ("chef0", "chef1"):
        raise SystemExit("SEAT must be chef0 or chef1")
    seat_index = ("chef0", "chef1").index(seat)
    policy = os.environ.get("KITCHEN_POLICY", "tape")
    if policy not in ("tape", "wait"):
        raise SystemExit("KITCHEN_POLICY must be tape or wait")
    deadline = time.monotonic() + 45
    for tick, joint_action in enumerate(JOINT_ACTIONS):
        wait_for_tick(tick, deadline)
        action = WAIT if policy == "wait" else joint_action[seat_index]
        request = Request(
            f"{WORLD}/action/{seat}",
            data=json.dumps({"tick": tick, "action": action}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=min(2, seconds_left(deadline))) as response:
            if response.status != 200:
                raise SystemExit(
                    f"{seat} action at tick {tick}: HTTP {response.status}"
                )
    wait_for_tick(FINAL_TICK, deadline)
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": f"cramped-room-{seat}",
        "agent": {"name": "oracle", "version": "1.0.0"},
        "steps": [
            {
                "step_id": 1,
                "source": "user",
                "message": f"Replay the scripted actions for {seat}.",
            },
            {
                "step_id": 2,
                "source": "agent",
                "message": f"{seat} submitted {len(JOINT_ACTIONS)} actions and observed tick {FINAL_TICK}.",
            },
        ],
    }
    Path("/logs/agent/trajectory.json").write_text(
        json.dumps(trajectory) + "\n", encoding="utf-8"
    )
    print(f"{seat} finished the scripted replay at tick {FINAL_TICK}")


if __name__ == "__main__":
    main()
