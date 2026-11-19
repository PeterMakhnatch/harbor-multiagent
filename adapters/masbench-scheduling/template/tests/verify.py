"""Verify completion evidence and score the actual choice against official gold."""

import hashlib
import json
from pathlib import Path
from typing import Any, TypeGuard

ROLES = ("m1", "m2", "m3", "m4", "m5")
WORLD_FIELDS = {
    "schema",
    "case_id",
    "mode",
    "initial_view_sha256",
    "reports",
    "proposal_sha256",
    "choice",
    "acknowledged",
}


def _sha256(document):
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _in_horizon(tick: object, horizon: int) -> TypeGuard[int]:
    return type(tick) is int and 0 <= tick < horizon


def _sorted_ticks(ticks: object, horizon: int) -> bool:
    if not isinstance(ticks, list):
        return False
    previous = -1
    for tick in ticks:
        if not _in_horizon(tick, horizon) or tick <= previous:
            return False
        previous = tick
    return True


def verify_world(
    case: dict[str, Any], world: dict[str, Any], *, mode: str | None = None
) -> dict[str, str | int | bool | None]:
    """Raise ValueError when evidence is invalid; never turn missing data into 0."""
    if (
        not isinstance(world, dict)
        or world.keys() != WORLD_FIELDS
        or world["schema"] != "seatlab.calendar-world/v1"
        or world["acknowledged"] is not True
    ):
        raise ValueError("missing or invalid calendar completion record")
    try:
        case_id = case["case_id"]
        horizon = case["horizon"]
        slots_per_day = case["slots_per_day"]
        duration = case["duration"]
        members = case["members"]
        gold = case["gold"]
    except (KeyError, TypeError) as exc:
        raise ValueError("incomplete private calendar case") from exc
    if (
        case.get("schema") != "seatlab.calendar-case/v1"
        or not isinstance(case_id, str)
        or any(type(value) is not int for value in (duration, slots_per_day, horizon))
        or not 0 < duration <= slots_per_day <= horizon
        or horizon % slots_per_day != 0
        or not isinstance(members, dict)
        or set(members) != set(ROLES)
        or not all(_sorted_ticks(members[role], horizon) for role in ROLES)
        or not _sorted_ticks(gold, horizon)
    ):
        raise ValueError("invalid private calendar case")
    actual_mode = world["mode"]
    if (
        world["case_id"] != case_id
        or actual_mode not in ("full", "first8")
        or (mode is not None and actual_mode != mode)
    ):
        raise ValueError("calendar completion record is for another case or mode")

    expected_inputs = {
        role: _sha256(
            {
                "role": role,
                "mode": actual_mode,
                "horizon": horizon,
                "duration": duration,
                "slots_per_day": slots_per_day,
                "busy": members[role],
            }
        )
        for role in ROLES
    }
    if world["initial_view_sha256"] != expected_inputs:
        raise ValueError("calendar input acknowledgments do not match the five views")
    reports = world["reports"]
    if (
        not isinstance(reports, dict)
        or set(reports) != set(ROLES)
        or not all(_sorted_ticks(report, horizon) for report in reports.values())
    ):
        raise ValueError("missing or invalid calendar reports")
    proposals = {"role": "m1", "mode": actual_mode, "reports": reports}
    if world["proposal_sha256"] != _sha256(proposals):
        raise ValueError("leader acknowledgment does not match the reported proposals")

    start = world["choice"]
    if start is not None and not _in_horizon(start, horizon):
        raise ValueError("invalid calendar choice")
    official_match = start is not None and start in gold
    feasible = None
    if start is not None:
        end = start + duration
        feasible = (
            end <= horizon
            and start // slots_per_day == (end - 1) // slots_per_day
            and all(
                not any(start <= tick < end for tick in busy)
                for busy in members.values()
            )
        )
    return {
        "case_id": case_id,
        "mode": actual_mode,
        "choice": start,
        "official_exact_match": official_match,
        "physically_feasible": feasible,
        "reward": int(official_match),
    }


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def read_json(path: Path):
    return json.loads(path.read_bytes(), object_pairs_hook=_unique_keys)


def main():
    reward = Path("/logs/verifier/reward.txt")
    reward.unlink(missing_ok=True)
    try:
        config = read_json(Path("/opt/calendar/case.json"))
        evidence = read_json(Path("/logs/world/calendar.json"))
        outcome = verify_world(config["case"], evidence, mode=config["mode"])
    except (KeyError, OSError, TypeError, ValueError, RecursionError) as exc:
        raise SystemExit(f"invalid calendar evidence: {exc}") from exc
    reward.parent.mkdir(parents=True, exist_ok=True)
    reward.write_text(f"{outcome['reward']}\n")
    print(json.dumps(outcome, sort_keys=True))


if __name__ == "__main__":
    main()
