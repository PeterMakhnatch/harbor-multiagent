import argparse
import json
import os
import stat
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any
from uuid import UUID

from ..receiver_panel import _reject_constant, _unique_keys
from ..visibility import calendar_packet as packet
from ..visibility import spend
from . import protocol as p

_POLICIES = {
    "seatlab.native-sender-policy-observations/v1": (
        "narepair",
        p.REPAIR_ARMS,
        "model_wins",
    ),
    "seatlab.native-question-policy-observations/v1": (
        "naquestion",
        p.QUESTION_ARMS,
        "raw_wins",
    ),
}
_BEHAVIOR = (
    "draft_exact",
    "delivered_exact",
    "target_nominated",
    "question_both_locally_valid",
)
_MINIMUM_NET_GAIN = 2
_ALPHA = Fraction(1, 20)
_MAX_BYTES = 64 * 1024
_PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
_FLASH_MODELS = {"deepseek-flash", "deepseek-v4.1-flash-1017"}
_PRICE_NANODOLLARS = (3, 150, 600)


def _read(path: Path) -> object:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
            raise ValueError("expected a size-limited regular JSON file")
        data = source.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError("JSON exceeds the byte limit")
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique_keys,
        parse_constant=_reject_constant,
    )


def _counts(value: object, expected: dict[str, int], field: str) -> None:
    if (
        not isinstance(value, dict)
        or value.keys() != expected.keys()
        or any(
            type(value[key]) is not int or value[key] != n
            for key, n in expected.items()
        )
    ):
        raise ValueError(f"{field} does not match the recomputed counts")


def _teams(
    value: object, prefix: str, arms: tuple[str, str]
) -> dict[str, dict[str, Any]]:
    expected = {
        f"{prefix}-{world}-{arm}": (world, arm) for world in p.WORLDS for arm in arms
    }
    rows = packet._list(value)
    if len(rows) != len(expected):
        raise ValueError("expected exactly 8 teams per arm")
    teams = {}
    uuids = set()
    hashes = set()
    question = arms == p.QUESTION_ARMS
    fields = (*_BEHAVIOR, "successful_nominated") if question else _BEHAVIOR
    exact_arms = arms if question else arms[1:]
    for index, raw in enumerate(rows):
        row = packet._object(
            raw, "run", "uuid", "world", "arm", "reward", "result_sha256", *fields
        )
        run, trial_uuid, reward = row["run"], row["uuid"], row["reward"]
        if (
            not isinstance(run, str)
            or run in teams
            or expected.get(run) != (row["world"], row["arm"])
        ):
            raise ValueError(f"teams[{index}]: unexpected or repeated run")
        if not isinstance(trial_uuid, str) or str(UUID(trial_uuid)) != trial_uuid:
            raise ValueError(f"{run}: expected a canonical trial UUID")
        digest = packet._check_digest(row["result_sha256"])
        if trial_uuid in uuids or digest in hashes:
            raise ValueError(f"{run}: repeated trial UUID or result SHA-256")
        if type(reward) is not int or reward not in (0, 1):
            raise ValueError(f"{run}: expected an observed binary integer reward")
        if any(type(row[field]) is not bool for field in fields):
            raise ValueError(f"{run}: expected booleans for team behavior")
        if row["arm"] in exact_arms and not row["delivered_exact"]:
            raise ValueError(f"{run}: local delivery is not certified exact")
        if question:
            if row["successful_nominated"] != bool(reward and row["target_nominated"]):
                raise ValueError(
                    f"{run}: successful_nominated does not match reward and nomination"
                )
        elif row["arm"] == arms[0] and row["draft_exact"] != row["delivered_exact"]:
            raise ValueError(f"{run}: the delivered draft must match draft exactness")
        uuids.add(trial_uuid)
        hashes.add(digest)
        teams[run] = row
    return teams


def _arm_counts(
    report: dict[str, Any], arms: tuple[str, str], teams: dict[str, dict[str, Any]]
) -> None:
    stock = packet._object(report["stock"], *arms)
    behavior = packet._object(report["behavior"], *arms)
    for arm in arms:
        rows = [row for row in teams.values() if row["arm"] == arm]
        wins = sum(row["reward"] for row in rows)
        _counts(
            stock[arm],
            {
                "attempted": len(rows),
                "enrolled": len(rows),
                "missing_or_unverified_reward": 0,
                "protocol_complete": len(rows),
                "unattempted": 0,
                "verified_stock_losses": len(rows) - wins,
                "verified_stock_wins": wins,
            },
            f"stock.{arm}",
        )
        counts = {field: sum(row[field] for row in rows) for field in _BEHAVIOR}
        counts["reward"] = wins
        if arms == p.QUESTION_ARMS:
            counts["successful_nominated"] = sum(
                row["reward"] > 0 and row["target_nominated"] for row in rows
            )
        _counts(behavior[arm], counts, f"behavior.{arm}")


def _ratio(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _paired(
    report: dict[str, Any], differences: list[int], baseline_wins_key: str
) -> None:
    local_wins, baseline_wins = differences.count(1), differences.count(-1)
    discordant = local_wins + baseline_wins
    tail = sum(comb(discordant, k) for k in range(min(local_wins, baseline_wins) + 1))
    exact_p = min(Fraction(1), Fraction(2 * tail, 2**discordant))
    paired = packet._object(report["paired"]).copy()
    if paired.pop("control", None) is not False:
        raise ValueError("paired.control must be false")
    _counts(
        paired.pop("two_sided_sign_p", None), _ratio(exact_p), "paired.two_sided_sign_p"
    )
    _counts(
        paired,
        {
            "local_wins": local_wins,
            baseline_wins_key: baseline_wins,
            "ties": differences.count(0),
            "matched": len(differences),
            "worlds_enrolled": len(p.WORLDS),
        },
        "paired",
    )
    _counts(report["maximum_two_sided_p"], _ratio(_ALPHA), "maximum_two_sided_p")
    minimum = report["minimum_net_gain"]
    if type(minimum) is not int or minimum != _MINIMUM_NET_GAIN:
        raise ValueError("unexpected minimum_net_gain")
    passed = local_wins - baseline_wins >= _MINIMUM_NET_GAIN and exact_p <= _ALPHA
    if report["verdict"] != ("passed" if passed else "did_not_pass"):
        raise ValueError("verdict does not match the registered paired decision rule")


def _observations(
    value: object,
) -> tuple[dict[str, Any], tuple[str, str], str, set[str]]:
    report = packet._object(
        value,
        "schema",
        "registration",
        "complete",
        "teams",
        "stock",
        "paired",
        "behavior",
        "verified_deliveries",
        "verified_turns",
        "verdict",
        "minimum_net_gain",
        "maximum_two_sided_p",
        "source_scope",
    )
    schema = report["schema"]
    if not isinstance(schema, str) or schema not in _POLICIES:
        raise ValueError("unrecognized observations schema")
    if report["complete"] is not True:
        raise ValueError("observations panel is not complete")
    for field in ("registration", "source_scope"):
        if not isinstance(report[field], str) or not report[field].strip():
            raise ValueError(f"expected nonempty {field}")
    prefix, arms, baseline_wins_key = _POLICIES[schema]
    teams = _teams(report["teams"], prefix, arms)
    _counts(
        {field: report[field] for field in ("verified_deliveries", "verified_turns")},
        {
            "verified_deliveries": 2 * len(teams),
            "verified_turns": len(p.PHASES) * len(teams),
        },
        "delivery and turn certificates",
    )
    _arm_counts(report, arms, teams)
    baseline, local = arms
    differences = [
        teams[f"{prefix}-{world}-{local}"]["reward"]
        - teams[f"{prefix}-{world}-{baseline}"]["reward"]
        for world in p.WORLDS
    ]
    _paired(report, differences, baseline_wins_key)
    return report, arms, baseline_wins_key, set(teams)


def _charge(row: dict[str, Any], run: str, role: str) -> int:
    model = row["served_model"]
    usage = (row["cache_hit"], row["cache_miss"], row["output"])
    if (
        not isinstance(model, str)
        or model.casefold() not in _FLASH_MODELS
        or any(type(n) is not int or n < 0 for n in usage)
    ):
        raise ValueError(f"{run}/{role}: expected settled Flash token usage")
    price = sum(n * rate for n, rate in zip(usage, _PRICE_NANODOLLARS, strict=True))
    if price > spend._HOLD or (
        row["estimated_usd_low"],
        row["estimated_usd_high"],
        row["unsettled_hold_usd"],
    ) != (spend._usd(price), spend._usd(price), spend._usd(0)):
        raise ValueError(f"{run}/{role}: settled price does not match token usage")
    return price


def _cost(value: object, runs: set[str]) -> int:
    report = packet._object(
        value,
        "schema",
        "calls",
        "currency",
        "known_usd_low",
        "price_source",
        "unsettled_calls",
        "upper_usd_including_holds",
    )
    if (
        report["schema"] != "seatlab.direct-flash-cost/v1"
        or report["currency"] != "USD"
        or report["price_source"] != _PRICE_SOURCE
    ):
        raise ValueError("unrecognized cost schema or pricing metadata")
    unsettled = report["unsettled_calls"]
    if type(unsettled) is not int or unsettled != 0:
        raise ValueError("all cost calls must be settled")
    expected = {(run, role) for run in runs for role in p.LEDGER_ROLES.values()}
    calls = packet._list(report["calls"])
    if len(calls) != len(expected):
        raise ValueError("cost scope must include exactly 3 calls per observed run")
    total = 0
    for index, raw in enumerate(calls):
        row = packet._object(
            raw,
            "run",
            "role",
            "served_model",
            "cache_hit",
            "cache_miss",
            "output",
            "estimated_usd_low",
            "estimated_usd_high",
            "unsettled_hold_usd",
        )
        run, role = row["run"], row["role"]
        if (
            not isinstance(run, str)
            or not isinstance(role, str)
            or (run, role) not in expected
        ):
            raise ValueError(
                f"calls[{index}]: unexpected or repeated run/role in cost scope"
            )
        expected.remove((run, role))
        total += _charge(row, run, role)
    usd = spend._usd(total)
    if report["known_usd_low"] != usd or report["upper_usd_including_holds"] != usd:
        raise ValueError("cost totals do not match the settled calls")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check native calendar policy arithmetic, not authenticity or role isolation."
    )
    parser.add_argument("observations", type=Path)
    parser.add_argument("cost", type=Path)
    args = parser.parse_args(argv)
    try:
        report, arms, baseline_wins_key, runs = _observations(_read(args.observations))
    except (OSError, ValueError, RecursionError) as exc:
        parser.error(f"observations {args.observations}: {exc}")
    try:
        total = _cost(_read(args.cost), runs)
    except (OSError, ValueError, RecursionError) as exc:
        parser.error(f"cost {args.cost}: {exc}")
    for arm in arms:
        stock = report["stock"][arm]
        print(f"{arm} stock: {stock['verified_stock_wins']}/{stock['enrolled']}")
    paired = report["paired"]
    exact_p = paired["two_sided_sign_p"]
    print(
        f"Pairs: {paired['matched']}; local wins: {paired['local_wins']}; "
        f"{arms[0]} wins: {paired[baseline_wins_key]}; ties: {paired['ties']}; "
        f"two-sided sign p: {exact_p['numerator']}/{exact_p['denominator']}"
    )
    print(f"Verdict: {report['verdict']}")
    print(
        f"Actual total: ${spend._usd(total)} USD ({report['verified_turns']} settled calls)"
    )
    print("Checks arithmetic, not authenticity or role isolation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
