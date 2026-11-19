"""Score a completed native cursor or hybrid8 exchange against the private calendar case."""

import hashlib
import json
import re
from bisect import bisect_left
from pathlib import Path
from typing import Any, TypeGuard

ROLES = ("m1", "m2", "m3", "m4", "m5")
ROLE_SET = frozenset(ROLES)
MODES = ("cursor", "hybrid8")
MAX_ROUNDS = 720
HYBRID8_SCALAR_ROUNDS = 8
HYBRID8_SUFFIX_ROUND = HYBRID8_SCALAR_ROUNDS + 1
SUFFIX_ENCODING = "suffix-hex"
_LOWER_HEX = re.compile(r"^[0-9a-f]+$")
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
SUFFIX_ROUND_FIELDS = ROUND_FIELDS | {"encoding"}


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


def _suffix_hex_width(horizon: int, bound: int) -> int:
    return (horizon - bound + 3) // 4


def _suffix_mask(seat_starts: list[int], bound: int, horizon: int) -> tuple[int, str]:
    bits = 0
    for start in seat_starts[bisect_left(seat_starts, bound) :]:
        bits |= 1 << (start - bound)
    width = _suffix_hex_width(horizon, bound)
    return bits, f"{bits:0{width}x}"


def _valid_suffix_hex(value: object, bound: int, horizon: int) -> TypeGuard[str]:
    width = _suffix_hex_width(horizon, bound)
    if not isinstance(value, str) or len(value) != width or not _LOWER_HEX.fullmatch(value):
        return False
    bits = int(value, 16)
    return bits >> (horizon - bound) == 0


def verify(
    case: dict[str, Any],
    world: dict[str, Any],
    *,
    expected_mode: str | None = None,
) -> dict[str, Any]:
    """Raise ValueError on invalid native evidence, even if its choice scores 0.

    Resource fields use the definitions in cursor.py. One round trip is a bound
    and a complete reply set; one seat exchange is that bound and a reply for
    one seat, including m1. Each non-null scalar reply is a scalar disclosure.
    Each distinct (seat, tick) scalar pair counts once, no matter how often it
    recurs. Request bytes sum compact JSON encodings of the bound once per seat
    for scalar rounds, and `["suffix-hex",bound]` once per seat for a hybrid8
    suffix round. Response bytes sum compact JSON encodings of each integer,
    null, or fixed-width suffix hex mask reply. Payload bytes add those two
    counts. For hybrid8, suffix_exchanges, suffix_masks, and
    suffix_bit_positions record the optional round-9 suffix exchange (0 if
    resolved in scalar rounds). These omit envelopes, hashes, relay copies, and
    all other transport overhead. They do not measure latency or guarantee
    privacy.

    The broker records only a completed native exchange. The verifier checks
    that record against private inputs; a digest alone does not prove that a
    participant read a file.
    """
    if expected_mode is not None and expected_mode not in MODES:
        raise ValueError(f"unsupported interactive calendar mode: {expected_mode!r}")
    if (
        not isinstance(world, dict)
        or world.keys() != WORLD_FIELDS
        or world["schema"] != "seatlab.calendar-interactive-world/v1"
        or world["mode"] not in MODES
        or (expected_mode is not None and world["mode"] != expected_mode)
        or world["acknowledged"] is not True
    ):
        raise ValueError("missing or invalid interactive completion record")
    mode: str = world["mode"]
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
                "mode": mode,
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
    max_rounds = (
        min(horizon, HYBRID8_SUFFIX_ROUND)
        if mode == "hybrid8"
        else min(horizon, MAX_ROUNDS)
    )
    if not isinstance(rounds, list) or not 1 <= len(rounds) <= max_rounds:
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
    suffix_exchanges = suffix_masks = suffix_bit_positions = 0
    previous_scalar: dict[str, int] | None = None
    for number, row in enumerate(rounds, 1):
        is_suffix = mode == "hybrid8" and number == HYBRID8_SUFFIX_ROUND
        expected_fields = SUFFIX_ROUND_FIELDS if is_suffix else ROUND_FIELDS
        if (
            not _exact_fields(row, expected_fields)
            or type(row["round"]) is not int
            or row["round"] != number
        ):
            raise ValueError(f"invalid native round {number}")
        if not _tick(row["bound"], horizon) or row["bound"] != bound:
            raise ValueError(f"unexpected bound in native round {number}")
        replies = row["replies"]
        if not _exact_fields(replies, ROLE_SET):
            raise ValueError(f"invalid reply roles in native round {number}")

        if is_suffix:
            if row["encoding"] != SUFFIX_ENCODING:
                raise ValueError(f"invalid suffix encoding in round {number}")
            common_bits = (1 << (horizon - bound)) - 1
            for role in ROLES:
                mask = replies[role]
                if not _valid_suffix_hex(mask, bound, horizon):
                    raise ValueError(f"invalid suffix mask from {role} in round {number}")
                expected_bits, expected_hex = _suffix_mask(starts[role], bound, horizon)
                if mask != expected_hex:
                    raise ValueError(f"wrong suffix mask from {role} in round {number}")
                if previous_scalar is not None:
                    prior_tick = previous_scalar[role]
                    if prior_tick >= bound and not (expected_bits & (1 << (prior_tick - bound))):
                        raise ValueError(
                            f"suffix mask from {role} contradicts prior scalar start"
                        )
                common_bits &= expected_bits
                responses += len(_canonical(mask))
            query = {"round": number, "bound": bound, "encoding": SUFFIX_ENCODING}
            if row["query_sha256"] != _sha256(query):
                raise ValueError(f"query digest mismatch in round {number}")
            bundle = _sha256({**query, "replies": replies})
            if row["bundle_sha256"] != bundle:
                raise ValueError(f"round bundle digest mismatch in round {number}")
            requests += len(ROLES) * len(_canonical([SUFFIX_ENCODING, bound]))
            suffix_exchanges = 1
            suffix_masks = len(ROLES)
            suffix_bit_positions = len(ROLES) * (horizon - bound)

            if number != len(rounds):
                raise ValueError("native rounds continue after a terminal reply set")
            if world["choice_view_sha256"] != bundle:
                raise ValueError("choice acknowledgment does not match terminal bundle")
            if common_bits == 0:
                terminal_choice: int | None = None
            else:
                lowest_bit = (common_bits & -common_bits).bit_length() - 1
                terminal_choice = bound + lowest_bit
            if start != terminal_choice:
                raise ValueError("native choice does not match terminal replies")
            break

        replies_at_bound: list[int] = []
        current_scalar: dict[str, int] = {}
        for role in ROLES:
            tick = replies[role]
            if not _optional_tick(tick, horizon):
                raise ValueError(f"invalid reply tick from {role} in round {number}")
            seat_starts = starts[role]
            index = bisect_left(seat_starts, bound)
            expected = seat_starts[index] if index < len(seat_starts) else None
            if tick != expected:
                raise ValueError(f"wrong next start from {role} in round {number}")
            if previous_scalar is not None:
                prior_tick = previous_scalar[role]
                if prior_tick >= bound and tick != prior_tick:
                    raise ValueError(
                        f"reply from {role} in round {number} contradicts prior start"
                    )
            responses += len(_canonical(tick))
            if tick is not None:
                replies_at_bound.append(tick)
                current_scalar[role] = tick
                disclosures += 1
                distinct.add((role, tick))

        query = {"round": number, "bound": bound}
        if row["query_sha256"] != _sha256(query):
            raise ValueError(f"query digest mismatch in round {number}")
        bundle = _sha256({**query, "replies": replies})
        if row["bundle_sha256"] != bundle:
            raise ValueError(f"round bundle digest mismatch in round {number}")
        requests += len(ROLES) * len(_canonical(bound))

        exhausted = len(replies_at_bound) < len(ROLES)
        unanimous = not exhausted and all(
            tick == replies_at_bound[0] for tick in replies_at_bound
        )
        if exhausted or unanimous:
            if number != len(rounds):
                raise ValueError("native rounds continue after a terminal reply set")
            if world["choice_view_sha256"] != bundle:
                raise ValueError("choice acknowledgment does not match terminal bundle")
            terminal_choice = None if exhausted else replies_at_bound[0]
            if start != terminal_choice:
                raise ValueError("native choice does not match terminal replies")
            break
        previous_scalar = current_scalar
        bound = max(replies_at_bound)
    else:
        raise ValueError("native choice precedes a terminal reply set")

    # Check the actual recorded choice against the complete intersection, not
    # merely the last reported values or the original reference answer key.
    common = set(starts[ROLES[0]])
    common.intersection_update(*(starts[role] for role in ROLES[1:]))
    earliest = min(common, default=None)
    if start != earliest:
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
    resources: dict[str, int] = {
        "round_trips": len(rounds),
        "seat_exchanges": len(rounds) * len(ROLES),
        "scalar_disclosures": disclosures,
        "distinct_disclosed_starts": len(distinct),
        "request_payload_bytes": requests,
        "response_payload_bytes": responses,
        "payload_bytes": requests + responses,
    }
    if mode == "hybrid8":
        resources["suffix_exchanges"] = suffix_exchanges
        resources["suffix_masks"] = suffix_masks
        resources["suffix_bit_positions"] = suffix_bit_positions
    return {
        "case_id": case_id,
        "mode": mode,
        "choice": start,
        "official_exact_match": official_match,
        "physically_feasible": feasible,
        "reward": int(official_match),
        "resources": resources,
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
            or config["mode"] not in MODES
        ):
            raise ValueError("expected a private interactive calendar case")
        world = json.loads(
            WORLD_PATH.read_bytes(),
            object_pairs_hook=_unique_keys,
            parse_constant=_reject_constant,
        )
        outcome = verify(config["case"], world, expected_mode=config["mode"])
    except (OSError, ValueError, RecursionError) as exc:
        raise SystemExit(f"invalid interactive calendar evidence: {exc}") from exc
    REWARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    REWARD_PATH.write_text(f"{outcome['reward']}\n", encoding="ascii")
    print(json.dumps(outcome, sort_keys=True))


if __name__ == "__main__":
    main()
