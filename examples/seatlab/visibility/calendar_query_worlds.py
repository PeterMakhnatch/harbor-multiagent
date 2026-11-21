from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .calendar_packet import PACKET, PACKET_SHA256, load, starts


def verify_slots(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    shared_by_world = {}
    for world in load(PACKET, PACKET_SHA256):
        left, right = world.tables
        common = set(starts(left)).intersection(starts(right))
        if len(common) != 1:
            raise ValueError(f"w{world.index:02d}: expected exactly one shared start")
        shared_by_world[world.index] = common.pop()

    verified_submissions = verified_successes = scored_without_slot = unscored = 0
    for row in rows:
        index = row["world"]
        for name in ("blind", "peer"):
            arm = row["arms"][name]
            reward, slot = arm["reward"], arm["slot"]
            if reward is None:
                unscored += 1
            elif slot is None:
                scored_without_slot += 1
            else:
                feasible = slot == shared_by_world[index]
                if reward != feasible:
                    raise ValueError(
                        f"w{index:02d} d{row['direction']} {name}: reward mismatch"
                    )
                verified_submissions += 1
                verified_successes += feasible

    return {
        "schema": "seatlab.calendar-query-world-check/v1",
        "packet_sha256": PACKET_SHA256,
        "verified_successes": verified_successes,
        "verified_submissions": verified_submissions,
        "scored_without_slot": scored_without_slot,
        "unscored": unscored,
    }
