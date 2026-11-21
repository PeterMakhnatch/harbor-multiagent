import argparse
import json
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

from ..visibility import calendar_packet as packet
from ..visibility import spend

RECEIPT_SCHEMA = "seatlab.calendar-booking-route-evidence/v1"
SUMMARY_SCHEMA = "seatlab.calendar-booking-route-summary/v1"
_ROUTE_KINDS = ("empty", "single", "multiple", "no_final")
_STATUSES = ("scored", "invalid", "missing", "not_attempted")
_MODELS = ("deepseek-flash", "deepseek-v4.1-flash-1017")
_PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
_MAX_BYTES = 128 * 1024
_MAX_BUNDLED_BYTES = 256 * 1024
_RESULTS = Path(__file__).resolve().parents[1] / "results"
_TRANSFER_NATIVE = "calendar-native-transfer-48-71.json"
_NATIVE_RECEIPTS = {
    "transfer-fixed": (
        (_TRANSFER_NATIVE, "seatlab.calendar-visible-transfer-receipt/v1", ("lists",)),
    ),
    "role-stress": (
        (
            "calendar-private-views-live.json",
            "seatlab.calendar-private-view-audit/v1",
            ("raw", "exact"),
        ),
        (
            "calendar-role-views-live.json",
            "seatlab.calendar-role-view-audit/v1",
            ("sender-exact", "receiver-exact"),
        ),
    ),
}


class _Decision(NamedTuple):
    run: str
    status: str
    correct: int | None


class _Source(NamedTuple):
    world: str
    arm: str
    route: str
    singleton_correct: bool | None
    first: list[_Decision]


class _Native(NamedTuple):
    trial_uuid: str
    result_sha256: str
    eligible: bool


class _Charge(NamedTuple):
    low: int
    high: int


type _Grid = dict[tuple[str, str], tuple[str, ...]]
type _Charges = dict[str, _Charge | None]
type _NativeSources = dict[tuple[str, str], _Native]


def _registration(study: object) -> tuple[_Grid, set[str]]:
    if study == "transfer-fixed":
        grid = {
            (f"w{world:02d}", "lists"): tuple(
                f"cfxfer-w{world:02d}-r{repeat}-checked" for repeat in range(2)
            )
            for world in range(48, 72)
        }
        return grid, {
            run.removesuffix("-checked") + suffix
            for runs in grid.values()
            for run in runs
            for suffix in ("-checked", "-lists")
        }
    if study == "role-stress":
        return {
            (f"w{world:02d}", arm): (f"crroute-w{world:02d}-{arm}",)
            for world in range(12, 24)
            for arm in ("raw", "exact", "sender-exact", "receiver-exact")
        }, set()
    raise ValueError("unknown booking route study")


def _read(path: Path, limit: int = _MAX_BYTES) -> Any:
    return packet._json(packet._read(path, limit))


def _bundled(filename: str, schema: str) -> dict[str, Any]:
    receipt = packet._object(_read(_RESULTS / filename, _MAX_BUNDLED_BYTES))
    if receipt.get("schema") != schema:
        raise ValueError("unexpected bundled receipt schema")
    return receipt


def _native_sources(study: str, grid: _Grid) -> _NativeSources:
    native: _NativeSources = {}
    for filename, schema, arms in _NATIVE_RECEIPTS[study]:
        receipt = _bundled(filename, schema)
        rows = packet._list(receipt.get("cells"))
        if len(rows) > len(grid) * 2:
            raise ValueError("too many bundled native sources")
        for value in rows:
            row = packet._object(value)
            arm = row.get("arm")
            if filename == _TRANSFER_NATIVE and arm == "checked":
                continue
            world = row.get("world")
            if (
                not isinstance(world, str)
                or not isinstance(arm, str)
                or arm not in arms
            ):
                raise ValueError("unexpected bundled native world or arm")
            key = world, arm
            if key not in grid or key in native:
                raise ValueError("unexpected or repeated bundled native source")
            if study == "transfer-fixed":
                eligible = row.get("status") == "complete"
                if row.get("evidence_complete") is not eligible:
                    raise ValueError("inconsistent bundled native completion")
            else:
                eligible = row.get("complete")
                if type(eligible) is not bool:
                    raise ValueError("invalid bundled native completion")
            trial_uuid = row.get("trial_uuid")
            if not isinstance(trial_uuid, str):
                raise TypeError("invalid bundled native trial UUID")
            native[key] = _Native(
                trial_uuid, packet._check_digest(row.get("result_sha256")), eligible
            )
    if native.keys() != grid.keys():
        raise ValueError("incomplete bundled native census")
    return native


def _bind_transfer_grades(
    grid: _Grid, native: _NativeSources, sources: list[_Source]
) -> None:
    receipt = _bundled(
        "calendar-fixed-transfer-48-71.json",
        "seatlab.calendar-transfer-fixed-receiver/v1",
    )
    rows = packet._list(receipt.get("worlds"))
    enrolled = {source.world: source for source in sources}
    if len(rows) != len(enrolled):
        raise ValueError("incomplete bundled checked-grade census")
    for value in rows:
        row = packet._object(value)
        world = row.get("world")
        if not isinstance(world, str) or world not in enrolled:
            raise ValueError("unexpected or repeated bundled checked-grade world")
        source = enrolled.pop(world)
        key = world, source.arm
        trial, digest, _ = native[key]
        if row.get("trial_uuid") != trial or row.get("result_sha256") != digest:
            raise ValueError("bundled checked grades differ from the native source")
        arms = packet._object(row.get("arms"), "checked", "lists")
        checked = packet._object(arms["checked"], "first", "same_slot")
        grades = packet._list(checked["first"])
        runs = grid[key]
        if len(grades) != len(runs):
            raise ValueError("wrong number of bundled checked grades")
        fixed = _first(
            [
                {"run": run, **packet._object(grade, "status", "correct")}
                for run, grade in zip(runs, grades, strict=True)
            ],
            runs,
        )
        if set(fixed) != set(source.first):
            raise ValueError("first decisions differ from the bundled checked grades")


def _first(value: object, expected: tuple[str, ...]) -> list[_Decision]:
    rows = packet._list(value)
    if len(rows) != len(expected):
        raise ValueError("wrong number of first decisions for registered source")
    decisions = []
    seen = set()
    for raw in rows:
        row = packet._object(raw, "run", "status", "correct")
        run, status, correct = row["run"], row["status"], row["correct"]
        if not isinstance(run, str) or run not in expected or run in seen:
            raise ValueError("unexpected or repeated checked run")
        if status not in _STATUSES or (
            not (type(correct) is int and correct in (0, 1))
            if status == "scored"
            else correct is not None
        ):
            raise ValueError("invalid first decision grade or status")
        seen.add(run)
        decisions.append(_Decision(run, status, correct))
    return decisions


def _sources(value: object, grid: _Grid, native: _NativeSources) -> list[_Source]:
    rows = packet._list(value)
    if len(rows) != len(grid):
        raise ValueError("expected the full registered source census")
    seen, trials, digests = set(), set(), set()
    sources = []
    for raw in rows:
        row = packet._object(
            raw,
            "world",
            "arm",
            "trial_uuid",
            "result_sha256",
            "route",
            "singleton_correct",
            "first",
        )
        world, arm = row["world"], row["arm"]
        key = world, arm
        if (
            not isinstance(world, str)
            or not isinstance(arm, str)
            or key not in grid
            or key in seen
        ):
            raise ValueError("unexpected or repeated world and arm")
        trial = row["trial_uuid"]
        digest = packet._check_digest(row["result_sha256"])
        if not isinstance(trial, str) or str(UUID(trial)) != trial or trial in trials:
            raise ValueError("invalid or repeated native trial UUID")
        if digest in digests:
            raise ValueError("repeated native result SHA-256")
        route, correct = row["route"], row["singleton_correct"]
        if route not in _ROUTE_KINDS or (
            type(correct) is not bool if route == "single" else correct is not None
        ):
            raise ValueError("invalid booking route or singleton grade")
        bound = native[key]
        if (trial, digest) != (bound.trial_uuid, bound.result_sha256):
            raise ValueError("source identity differs from its bundled native record")
        if bound.eligible != (route != "no_final"):
            raise ValueError("route eligibility differs from its bundled native record")
        first = _first(row["first"], () if route == "no_final" else grid[key])
        sources.append(_Source(world, arm, route, correct, first))
        seen.add(key)
        trials.add(trial)
        digests.add(digest)
    return sources


def _charge(row: dict[str, Any]) -> _Charge | None:
    hit, miss, output = row["cache_hit"], row["cache_miss"], row["output"]
    low, high, hold = (
        row["estimated_usd_low"],
        row["estimated_usd_high"],
        row["unsettled_hold_usd"],
    )
    model = row["served_model"]
    if high is None:
        if hit is miss is output is low is model is None and hold == spend._usd(
            spend._HOLD
        ):
            return None
        raise ValueError("invalid cost hold")
    if (
        not isinstance(model, str)
        or model.casefold() not in _MODELS
        or any(type(n) is not int or n < 0 for n in (hit, miss, output))
    ):
        raise ValueError("invalid settled call usage")
    price = 3 * hit + 150 * miss + 600 * output
    upper = price if low == high else 2 * price
    if upper > spend._HOLD or (low, high, hold) != (
        spend._usd(price),
        spend._usd(upper),
        spend._usd(0),
    ):
        raise ValueError("settled call cost does not match token usage")
    return _Charge(price, upper)


def _cost(value: object, expected: set[str]) -> _Charges:
    data = packet._object(
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
        data["schema"] != "seatlab.direct-flash-cost/v1"
        or data["currency"] != "USD"
        or data["price_source"] != _PRICE_SOURCE
    ):
        raise ValueError("unexpected cost schema or pricing metadata")
    rows = packet._list(data["calls"])
    if len(rows) > len(expected):
        raise ValueError("too many cost rows for registered runs")
    charges: _Charges = {}
    low = high = held = 0
    for raw in rows:
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
        run = row["run"]
        if (
            not isinstance(run, str)
            or run not in expected
            or run in charges
            or row["role"] != "cal_decide"
        ):
            raise ValueError("unexpected or repeated cost call")
        charges[run] = charge = _charge(row)
        if charge is None:
            held += 1
        else:
            low += charge.low
            high += charge.high
    if type(data["unsettled_calls"]) is not int or (
        data["unsettled_calls"],
        data["known_usd_low"],
        data["upper_usd_including_holds"],
    ) != (held, spend._usd(low), spend._usd(high + held * spend._HOLD)):
        raise ValueError("cost totals do not match call rows")
    return charges


def _economics(runs: list[str], charges: _Charges) -> dict[str, int | None]:
    low = high = held = missing = 0
    for run in runs:
        charge = charges.get(run)
        if charge is None:
            held += run in charges
            missing += run not in charges
        else:
            low += charge.low
            high += charge.high
    return {
        "settled_calls": len(runs) - held - missing,
        "held_calls": held,
        "missing_calls": missing,
        "known_low_nanodollars": low,
        "settled_high_nanodollars": high,
        "upper_including_holds_nanodollars": (
            None if missing else high + held * spend._HOLD
        ),
    }


def _report(sources: list[_Source], charges: _Charges) -> dict[str, Any]:
    source_counts = dict.fromkeys(_ROUTE_KINDS, 0)
    status_counts = dict.fromkeys(_STATUSES, 0)
    fallback_counts = dict.fromkeys(("empty", "multiple"), 0)
    matched_worlds = set()
    skipped, fallback = [], []
    always = routed = harmful = beneficial = both_wrong = 0
    false_sources = false_calls = 0
    for source in sources:
        source_counts[source.route] += 1
        false_sources += source.singleton_correct is False
        if source.route == "single":
            skipped.extend(decision.run for decision in source.first)
            false_calls += len(source.first) if source.singleton_correct is False else 0
        elif source.route != "no_final":
            fallback.extend(decision.run for decision in source.first)
            fallback_counts[source.route] += len(source.first)
        for decision in source.first:
            status_counts[decision.status] += 1
            if decision.correct is None:
                continue
            matched_worlds.add(source.world)
            always += decision.correct
            if source.route != "single":
                routed += decision.correct
            else:
                routed += source.singleton_correct is True
                harmful += decision.correct == 1 and source.singleton_correct is False
                beneficial += decision.correct == 0 and source.singleton_correct is True
                both_wrong += (
                    decision.correct == 0 and source.singleton_correct is False
                )
    return {
        "source_counts": source_counts,
        "deployment_calls": len(skipped) + len(fallback),
        "status_counts": status_counts,
        "matched_calls": status_counts["scored"],
        "matched_worlds": len(matched_worlds),
        "always_correct": always,
        "routed_correct": routed,
        "harmful_overrides": harmful,
        "beneficial_overrides": beneficial,
        "both_wrong_when_triggered": both_wrong,
        "false_singleton_sources": false_sources,
        "false_singleton_calls": false_calls,
        "skipped_calls": len(skipped),
        "fallback_calls": len(fallback),
        "fallback_calls_by_route": fallback_counts,
        "cost": {
            "would_omit": _economics(skipped, charges),
            "fallback": _economics(fallback, charges),
        },
    }


def summarize(receipt: object, cost: object) -> dict[str, Any]:
    data = packet._object(receipt, "schema", "study", "sources")
    if data["schema"] != RECEIPT_SCHEMA:
        raise ValueError("unexpected booking route evidence schema")
    study = data["study"]
    grid, expected = _registration(study)
    native = _native_sources(study, grid)
    sources = _sources(data["sources"], grid, native)
    if study == "transfer-fixed":
        _bind_transfer_grades(grid, native, sources)
    else:
        expected = {decision.run for source in sources for decision in source.first}
    charges = _cost(cost, expected)
    primary = _report(sources, charges)
    held = sum(charge is None for charge in charges.values())
    coverage = {
        "expected_calls": len(expected),
        "settled_calls": len(charges) - held,
        "held_calls": held,
        "missing_calls": len(expected) - len(charges),
    }
    complete = (
        primary["matched_calls"] == primary["deployment_calls"]
        and not coverage["missing_calls"]
        and not held
    )
    verdict = "inconclusive"
    if complete:
        passes = (
            0 < primary["deployment_calls"] <= 2 * primary["skipped_calls"]
            and not primary["harmful_overrides"]
        )
        verdict = "pass" if passes else "not_established"
    return {
        "schema": SUMMARY_SCHEMA,
        "study": study,
        "evidence_complete": complete,
        "verdict": verdict,
        "primary": primary,
        "arms": {
            arm: _report([source for source in sources if source.arm == arm], charges)
            for arm in dict.fromkeys(arm for _, arm in grid)
        },
        "cost_coverage": coverage,
        "proof_scope": (
            "Binds source identity and final-decision eligibility to bundled public "
            "records, and transfer checked grades to the published replay. Accepts "
            "route classifications and other grades from the owners. Does not "
            "authenticate private prompts, replies, or source files."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize registered calendar booking route evidence.",
        allow_abbrev=False,
    )
    parser.add_argument("receipt", type=Path, metavar="RECEIPT")
    parser.add_argument("--cost", type=Path, required=True, metavar="COST")
    args = parser.parse_args(argv)
    try:
        result = summarize(_read(args.receipt), _read(args.cost))
    except (OSError, TypeError, ValueError, RecursionError):
        parser.error("invalid or unreadable calendar booking route evidence or cost")
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
