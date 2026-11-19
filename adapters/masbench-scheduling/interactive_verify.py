"""Score a completed native cursor exchange against the private calendar case."""

import hashlib
import json
from bisect import bisect_left
from pathlib import Path
from typing import Any, TypeGuard

ROLES = ("m1", "m2", "m3", "m4", "m5")
ROLE_SET = frozenset(ROLES)
ROUND_LIMIT = 8
CASE_PATH = Path("/opt/calendar/case.json")
WORLD_PATH = Path("/logs/artifacts/world.calendar.json")
REWARD_PATH = Path("/logs/verifier/reward.txt")
WORLD_FIELDS = {
    "schema",
    "case_id",
    "mode",
    "initial_view_sha256",
    "rounds",
    "choice",
    "choice_view_sha256",
    "acknowledged",
}
ROUND_FIELDS = {"round", "bound", "replies", "query_sha256", "bundle_sha256"}


def _canonical(document: object) -> bytes:
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _sha256(document: object) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def _tick(value: object, horizon: int) -> TypeGuard[int]:
    return type(value) is int and 0 <= value < horizon


def _optional_tick(value: object, horizon: int) -> TypeGuard[int | None]:
    return value is None or _tick(value, horizon)


def _exact_fields(
    value: object, fields: set[str] | frozenset[str]
) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and value.keys() == fields


def _sorted_ticks(values: object, horizon: int) -> TypeGuard[list[int]]:
    if not isinstance(values, list):
        return False
    previous = -1
    for value in values:
        if not _tick(value, horizon) or value <= previous:
            return False
        previous = value
    return True


def _feasible_starts(
    busy: list[int], horizon: int, slots_per_day: int, duration: int
) -> list[int]:
    occupied = set(busy)
    return [
        start
        for day in range(0, horizon, slots_per_day)
        for start in range(day, day + slots_per_day - duration + 1)
        if occupied.isdisjoint(range(start, start + duration))
    ]


def verify(case: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
    """Raise ValueError on invalid native evidence, even if its choice scores 0.

    Resource fields use the definitions in cursor.py. One round trip is a bound
    and a complete reply set; one seat exchange is that bound and a reply for
    one seat, including m1. Each non-null reply is a scalar disclosure. Each
    distinct (seat, tick) pair counts once, no matter how often it recurs.
    Request bytes sum compact JSON encodings of the bound once per seat.
    Response bytes sum compact JSON encodings of each integer or null reply.
    Payload bytes add those two counts. These omit envelopes, hashes, relay
    copies, and all other transport overhead. They do not measure latency or
    guarantee privacy.

    The broker records only a completed native exchange. The verifier checks
    that record against private inputs; a digest alone does not prove that a
    participant read a file.
    """
    if (
        not isinstance(world, dict)
        or world.keys() != WORLD_FIELDS
        or world["schema"] != "seatlab.calendar-interactive-world/v1"
        or world["mode"] != "cursor"
        or world["acknowledged"] is not True
    ):
        raise ValueError("missing or invalid interactive completion record")
    if not isinstance(case, dict) or case.get("schema") != "seatlab.calendar-case/v1":
        raise ValueError("invalid private calendar case")
    case_id = case.get("case_id")
    horizon = case.get("horizon")
    slots_per_day = case.get("slots_per_day")
    duration = case.get("duration")
    members = case.get("members")
    gold = case.get("gold")
    if (
        not isinstance(case_id, str)
        or not case_id
        or type(duration) is not int
        or type(slots_per_day) is not int
        or type(horizon) is not int
        or not 0 < duration <= slots_per_day <= horizon
        or horizon % slots_per_day != 0
        or not isinstance(members, dict)
        or members.keys() != ROLE_SET
        or not all(_sorted_ticks(busy, horizon) for busy in members.values())
        or not _sorted_ticks(gold, horizon)
    ):
        raise ValueError("invalid private calendar case")
    if world["case_id"] != case_id:
        raise ValueError("interactive completion record is for another case")

    views = {
        role: _sha256(
            {
                "role": role,
                "mode": "cursor",
                "horizon": horizon,
                "duration": duration,
                "slots_per_day": slots_per_day,
                "busy": members[role],
            }
        )
        for role in ROLES
    }
    if world["initial_view_sha256"] != views:
        raise ValueError("initial acknowledgments do not match the five private views")
    rounds = world["rounds"]
    if not isinstance(rounds, list) or not 1 <= len(rounds) <= min(
        horizon, ROUND_LIMIT
    ):
        raise ValueError("invalid number of native rounds")
    start = world["choice"]
    if not _optional_tick(start, horizon):
        raise ValueError("invalid native choice")

    starts = {
        role: _feasible_starts(members[role], horizon, slots_per_day, duration)
        for role in ROLES
    }
    bound = 0
    disclosures = requests = responses = 0
    distinct: set[tuple[str, int]] = set()
    for number, row in enumerate(rounds, 1):
        if (
            not _exact_fields(row, ROUND_FIELDS)
            or type(row["round"]) is not int
            or row["round"] != number
        ):
            raise ValueError(f"invalid native round {number}")
        if not _tick(row["bound"], horizon) or row["bound"] != bound:
            raise ValueError(f"unexpected bound in native round {number}")
        replies = row["replies"]
        if not _exact_fields(replies, ROLE_SET):
            raise ValueError(f"invalid reply roles in native round {number}")

        replies_at_bound: list[int] = []
        for role in ROLES:
            tick = replies[role]
            if not _optional_tick(tick, horizon):
                raise ValueError(f"invalid reply tick from {role} in round {number}")
            seat_starts = starts[role]
            index = bisect_left(seat_starts, bound)
            expected = seat_starts[index] if index < len(seat_starts) else None
            if tick != expected:
                raise ValueError(f"wrong next start from {role} in round {number}")
            responses += len(_canonical(tick))
            if tick is not None:
                replies_at_bound.append(tick)
                disclosures += 1
                distinct.add((role, tick))

        query = {"round": number, "bound": bound}
        if row["query_sha256"] != _sha256(query):
            raise ValueError(f"query digest mismatch in round {number}")
        bundle = _sha256({**query, "replies": replies})
        if row["bundle_sha256"] != bundle:
            raise ValueError(f"round bundle digest mismatch in round {number}")
        requests += len(ROLES) * len(_canonical(bound))

        if len(replies_at_bound) < len(ROLES):
            termination, terminal_choice = "exhausted", None
        elif all(tick == replies_at_bound[0] for tick in replies_at_bound):
            termination, terminal_choice = "unanimous", replies_at_bound[0]
        elif number == ROUND_LIMIT:
            termination, terminal_choice = "budget", None
        else:
            bound = max(replies_at_bound)
            continue

        if number != len(rounds):
            raise ValueError("native rounds continue after a terminal reply set")
        if world["choice_view_sha256"] != bundle:
            raise ValueError("choice acknowledgment does not match terminal bundle")
        if start != terminal_choice:
            raise ValueError("native choice does not match terminal replies or budget")
        break
    else:
        raise ValueError("native choice precedes a terminal reply set")

    # A budget cutoff can leave common starts undiscovered. In all other
    # cases, check the choice against the full physical intersection.
    if termination != "budget":
        common = set(starts[ROLES[0]])
        common.intersection_update(*(starts[role] for role in ROLES[1:]))
        if start != min(common, default=None):
            raise ValueError("native choice is not the earliest common start")

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
        "mode": "cursor",
        "choice": start,
        "termination": termination,
        "official_exact_match": official_match,
        "physically_feasible": feasible,
        "reward": int(official_match),
        "resources": {
            "round_trips": len(rounds),
            "seat_exchanges": len(rounds) * len(ROLES),
            "scalar_disclosures": disclosures,
            "distinct_disclosed_starts": len(distinct),
            "request_payload_bytes": requests,
            "response_payload_bytes": responses,
            "payload_bytes": requests + responses,
        },
    }


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def main() -> None:
    REWARD_PATH.unlink(missing_ok=True)
    try:
        config = json.loads(
            CASE_PATH.read_bytes(),
            object_pairs_hook=_unique_keys,
            parse_constant=_reject_constant,
        )
        if (
            not isinstance(config, dict)
            or config.keys() != {"mode", "case"}
            or config["mode"] != "cursor"
        ):
            raise ValueError("expected a private cursor case")
        world = json.loads(
            WORLD_PATH.read_bytes(),
            object_pairs_hook=_unique_keys,
            parse_constant=_reject_constant,
        )
        outcome = verify(config["case"], world)
    except (OSError, ValueError, RecursionError) as exc:
        raise SystemExit(f"invalid interactive calendar evidence: {exc}") from exc
    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_PATH.write_text(f"{outcome['reward']}\n", encoding="ascii")
    print(json.dumps(outcome, sort_keys=True))


if __name__ == "__main__":
    main()
