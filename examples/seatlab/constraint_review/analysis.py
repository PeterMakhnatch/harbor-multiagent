import argparse
import json
import re
from fractions import Fraction
from math import comb
from pathlib import Path

_ARMS = ("neutral", "specific")
_REPS = (0, 1)
_STATUSES = ("valid", "invalid_reply", "provider_failure", "not_attempted")
_METRICS = ("initial_violations", "violations", "preserved", "introduced", "optimal")
_TOKENS = ("cache_hit", "cache_miss", "output_tokens")
_PRICES = ("usd_low", "usd_high")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_USD = re.compile(r"([0-9]+)\.([0-9]{9})")
_NANOS = 10**9
_HOLD_NANOS = 10_000_000
_ALPHA = Fraction(1, 20)

_VISIBLE_NATIVE = {
    6901: (
        "54553616cde0674b269416eca4cdd9fc833bb8a026957b9b2db9ad7b4befe7d4",
        "3d4916ad92ed2a3c3379ef53862ee66c10d261fc000df270da1d54ecec4953c2",
    ),
    6902: (
        "cd8135e6ba8708c5c3c2646ddbfeb3be47cd6f59a74962a96d2d922913bc8616",
        "e73876f224834551993d4a656f3264a3761fadcf36c5b9c5ee3cfa1ab3c17af6",
    ),
    6903: (
        "ae045139b3a74f74b3565b59359fcc713045722d06e5e95e3c24d9bcc7b927d5",
        "39efa619f9a5fbf3e9ca200a1fdabae1f7519006aeb0f04f205096d7eeb974f0",
    ),
    6904: (
        "9ec6803e8a55c2f10adaa399178899e188b3c4a4b4d4ef8b9349dbcdbfe7e506",
        "f7f47d2cfd4782d080f58183dfa4563b514b790aa05ae28fb5248166689c6e45",
    ),
    6905: (
        "d29f1c911022b9ccace9d13fdc187c1d3c3a77e611c9c72f45a9f1251258b85e",
        "2a364c036e02d741b7d0550bfa107a64eab934bf459a95e2303b0064d95a453d",
    ),
    6906: (
        "88dc689049cf25e9007f10e9b69323669863d5f1f397503faeb80e979e3b3bb3",
        "3e3470cfad8ed8d9bcaccba076f2db082500712732e0aa114e296366026079d5",
    ),
    6907: (
        "0e1cecee1c19d81474356ed2e0c127f3b3536f8615b0c36c9633e03a50f55e8c",
        "ac6f3530eb44bdda7e2296b9ac666a76938c58a0610c60a17f0f52a86a7fb204",
    ),
    6908: (
        "dfd7030e4e0b4696455397cec505f0bf92b9ce0ae216ff9de08dcdbdda13e841",
        "78795c9dc7c106d52365788a090b360bf293fb06689814478b7741c3e69f5bc7",
    ),
}


def _count(value: object, limit: int) -> bool:
    return type(value) is int and 0 <= value <= limit


def _sources(value: object) -> dict:
    if not isinstance(value, list) or len(value) != len(_VISIBLE_NATIVE):
        raise ValueError("expected all eight registered visible native sources")
    sources = {}
    for source in value:
        if not isinstance(source, dict) or source.keys() != {
            "world",
            "trial_ref",
            "result_sha256",
            "initial_violations",
            "initial_optimal",
        }:
            raise ValueError("invalid source fields")
        world = source["world"]
        if type(world) is not int or world not in _VISIBLE_NATIVE or world in sources:
            raise ValueError(f"unexpected or duplicate source world: {world!r}")
        identity = (source["trial_ref"], source["result_sha256"])
        if (
            identity != _VISIBLE_NATIVE[world]
            or not _count(source["initial_optimal"], 6)
            or not _count(source["initial_violations"], 2)
        ):
            raise ValueError(f"invalid native identity or baseline for world {world}")
        sources[world] = source
    return sources


def _nanos(value: object) -> int:
    match = _USD.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("invalid ledger USD amount")
    return int(match[1]) * _NANOS + int(match[2])


def _usd(nanos: int) -> str:
    dollars, fraction = divmod(nanos, _NANOS)
    return f"{dollars}.{fraction:09d}"


def _check_charge(charge: object) -> None:
    if not isinstance(charge, dict) or charge.keys() != {
        "status",
        *_TOKENS,
        *_PRICES,
        "hold_usd",
    }:
        raise ValueError("invalid charge fields")
    status = charge["status"]
    hold = _nanos(charge["hold_usd"])
    if status == "settled":
        low, high = (_nanos(charge[key]) for key in _PRICES)
        if hold or any(
            type(charge[key]) is not int or charge[key] < 0 for key in _TOKENS
        ):
            raise ValueError("invalid settlement")
        hit, miss, output = (charge[key] for key in _TOKENS)
        expected_low = hit * 3 + miss * 150 + output * 600
        if low != expected_low or high not in (low, 2 * low) or high > _HOLD_NANOS:
            raise ValueError("settlement does not match registered token prices")
    elif status in ("held", "absent"):
        if hold != (_HOLD_NANOS if status == "held" else 0):
            raise ValueError("invalid reservation hold")
        if any(charge[key] is not None for key in (*_TOKENS, *_PRICES)):
            raise ValueError("nonsettled charge must not include token prices or usage")
    else:
        raise ValueError("invalid charge status")


def _check_metrics(metrics: object, source: dict, valid: bool) -> None:
    if not valid:
        if metrics is not None:
            raise ValueError("only valid replies may include metrics")
        return
    if not isinstance(metrics, dict) or metrics.keys() != set(_METRICS):
        raise ValueError("invalid metric fields")
    if any(not _count(metrics[key], 6 if key == "optimal" else 2) for key in _METRICS):
        raise ValueError("invalid metric count")
    initial = source["initial_violations"]
    if (
        initial != metrics["initial_violations"]
        or metrics["preserved"] > initial
        or metrics["introduced"] > 2 - initial
        or metrics["preserved"] + metrics["introduced"] != metrics["violations"]
    ):
        raise ValueError("inconsistent violation counts")


def _rows(value: object, sources: dict) -> dict:
    registered = {
        (world, rep, arm) for world in sources for rep in _REPS for arm in _ARMS
    }
    if not isinstance(value, list) or len(value) != len(registered):
        raise ValueError("expected all 32 registered replay slots")
    rows = {}
    prompts = {}
    for row in value:
        if not isinstance(row, dict) or row.keys() != {
            "world",
            "rep",
            "arm",
            "run",
            "status",
            "prompt_sha256",
            "response_sha256",
            "metrics",
            "charge",
        }:
            raise ValueError("invalid replay row fields")
        world, rep, arm = row["world"], row["rep"], row["arm"]
        if type(world) is not int or type(rep) is not int or arm not in _ARMS:
            raise ValueError("invalid replay slot")
        slot = world, rep, arm
        run = f"creview-s{world}-r{rep}-{arm}"
        if slot not in registered or slot in rows or row["run"] != run:
            raise ValueError(f"unexpected, duplicate, or misnamed replay slot: {run}")
        status = row["status"]
        if status not in _STATUSES:
            raise ValueError(f"invalid replay status: {run}")
        answered = status in ("valid", "invalid_reply")
        prompt, response = row["prompt_sha256"], row["response_sha256"]
        if (answered or prompt is not None) and (
            not isinstance(prompt, str) or _DIGEST.fullmatch(prompt) is None
        ):
            raise ValueError(f"invalid prompt_sha256: {run}")
        if answered:
            if not isinstance(response, str) or _DIGEST.fullmatch(response) is None:
                raise ValueError(f"invalid response_sha256: {run}")
        elif response is not None:
            raise ValueError(f"unexpected response_sha256: {run}")
        if prompt is not None and prompts.setdefault((world, arm), prompt) != prompt:
            raise ValueError(f"prompt hash changed across repetitions: {run}")
        try:
            _check_metrics(row["metrics"], sources[world], status == "valid")
            _check_charge(row["charge"])
            metered = row["charge"]["status"]
            if answered and metered != "settled":
                raise ValueError("answer requires a settlement")
            if status == "not_attempted" and metered != "absent":
                raise ValueError("unattempted slot cannot have a reservation")
        except ValueError as exc:
            raise ValueError(f"{run}: {exc}") from exc
        rows[slot] = row
    for world in sources:
        neutral, specific = (prompts.get((world, arm)) for arm in _ARMS)
        if neutral is not None and neutral == specific:
            raise ValueError(f"arm prompt hashes must differ: world {world}")
    return rows


def _charges(rows: list[dict]) -> dict:
    settled = [row["charge"] for row in rows if row["charge"]["status"] == "settled"]
    held = [row for row in rows if row["charge"]["status"] == "held"]
    absent = [row for row in rows if row["charge"]["status"] == "absent"]
    return {
        "settled_calls": len(settled),
        "held_calls": len(held),
        "missing_reservations": len(absent),
        "held_runs": [row["run"] for row in held],
        "missing_reservation_runs": [row["run"] for row in absent],
        "settled_tokens": {
            key: sum(charge[key] for charge in settled) for key in _TOKENS
        },
        **{
            f"token_priced_{key}": _usd(sum(_nanos(charge[key]) for charge in settled))
            if settled
            else None
            for key in _PRICES
        },
        "unsettled_hold_usd": _usd(
            sum(_nanos(row["charge"]["hold_usd"]) for row in held)
        ),
    }


def _arm(rows: list[dict], sources: dict) -> dict:
    valid = [row for row in rows if row["status"] == "valid"]
    return {
        "registered_responses": len(rows),
        "valid_responses": len(valid),
        "status_counts": {
            status: sum(row["status"] == status for row in rows) for status in _STATUSES
        },
        **{key: sum(row["metrics"][key] for row in valid) for key in _METRICS},
        "initial_optimal": sum(
            sources[row["world"]]["initial_optimal"] for row in valid
        ),
        "charge": _charges(rows),
    }


def _ratio(value: Fraction | None) -> dict | None:
    if value is None:
        return None
    return {"numerator": value.numerator, "denominator": value.denominator}


def _world(source: dict, rows: dict) -> tuple[dict, Fraction | None, Fraction | None]:
    arms = {}
    means = {}
    for arm in _ARMS:
        draws = [rows[source["world"], rep, arm] for rep in _REPS]
        valid = [row["metrics"] for row in draws if row["status"] == "valid"]
        if len(valid) == len(_REPS):
            means[arm] = (
                Fraction(sum(row["violations"] for row in valid), len(_REPS)),
                Fraction(sum(row["optimal"] for row in valid), 6 * len(_REPS)),
            )
        violations, optimal = means.get(arm, (None, None))
        arms[arm] = {
            "valid_responses": len(valid),
            "metered_responses": sum(
                row["charge"]["status"] == "settled" for row in draws
            ),
            "mean_violations": _ratio(violations),
            "fraction_optimal": _ratio(optimal),
        }
    reduction = optimal_effect = None
    if len(means) == len(_ARMS):
        neutral, specific = (means[arm] for arm in _ARMS)
        reduction = neutral[0] - specific[0]
        optimal_effect = specific[1] - neutral[1]
    return (
        {
            **source,
            "arms": arms,
            "violation_reduction": _ratio(reduction),
            "fraction_optimal_effect": _ratio(optimal_effect),
        },
        reduction,
        optimal_effect,
    )


def _sign_test(reductions: list[Fraction]) -> tuple[dict, Fraction]:
    positive = sum(effect > 0 for effect in reductions)
    negative = sum(effect < 0 for effect in reductions)
    non_tied = positive + negative
    p = Fraction(
        sum(comb(non_tied, k) for k in range(positive, non_tied + 1)), 2**non_tied
    )
    return {
        "positive_worlds": positive,
        "negative_worlds": negative,
        "tied_worlds": len(reductions) - non_tied,
        "p_value": _ratio(p),
        "alpha": _ratio(_ALPHA),
    }, p


def summarize(public: dict) -> dict:
    if (
        not isinstance(public, dict)
        or public.keys() != {"schema", "sources", "rows"}
        or public["schema"] != "seatlab.constraint-review/v1"
    ):
        raise ValueError("expected seatlab.constraint-review/v1 receipt")
    sources = _sources(public["sources"])
    rows = _rows(public["rows"], sources)
    ordered = [rows[slot] for slot in sorted(rows)]
    worlds, reductions, optimal_effects = [], [], []
    for world in _VISIBLE_NATIVE:
        report, reduction, optimal_effect = _world(sources[world], rows)
        worlds.append(report)
        if reduction is not None and optimal_effect is not None:
            reductions.append(reduction)
            optimal_effects.append(optimal_effect)
    complete_worlds = len(reductions)
    mean_reduction = (
        sum(reductions, Fraction()) / complete_worlds if complete_worlds else None
    )
    mean_optimal = (
        sum(optimal_effects, Fraction()) / complete_worlds if complete_worlds else None
    )
    sign, p = _sign_test(reductions)
    charge = _charges(ordered)
    registered = len(rows)
    valid = sum(row["status"] == "valid" for row in ordered)
    metered = charge["settled_calls"]
    if valid != registered or metered != registered:
        outcome = "unconfirmed"
    elif (
        mean_reduction is not None
        and mean_optimal is not None
        and (mean_reduction > 0 and p <= _ALPHA and mean_optimal >= 0)
    ):
        outcome = "pass"
    else:
        outcome = "fail"
    return {
        "schema": "seatlab.constraint-review-summary/v1",
        "outcome": outcome,
        "registered_responses": registered,
        "valid_responses": valid,
        "metered_responses": metered,
        "complete_worlds": complete_worlds,
        "arms": {
            arm: _arm([row for row in ordered if row["arm"] == arm], sources)
            for arm in _ARMS
        },
        "worlds": worlds,
        "effect_directions": {
            "violation_reduction": "neutral minus specific",
            "fraction_optimal_effect": "specific minus neutral",
        },
        "mean_violation_reduction": _ratio(mean_reduction),
        "mean_fraction_optimal_effect": _ratio(mean_optimal),
        "sign_test": sign,
        "charge": charge,
    }


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    parsed = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError(f"duplicate JSON key: {key}")
        parsed[key] = value
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize a public constraint-review receipt."
    )
    default = (
        Path(__file__).resolve().parent.parent
        / "results"
        / "constraint-review-live.json"
    )
    parser.add_argument("receipt", nargs="?", type=Path, default=default)
    args = parser.parse_args()
    try:
        receipt = json.loads(
            args.receipt.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys
        )
        summary = summarize(receipt)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"constraint-review: {exc}\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
