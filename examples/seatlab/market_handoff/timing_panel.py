from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any

from .simulator import NATIVE_DAYS, PIN, PREFIX_DAYS
from .timing_messages import ACTIONS, REASON_MAX_CHARS
from .timing_world import (
    ANALYSIS_WORLDS,
    NAMESPACE,
    PRICE_MULTIPLIERS,
    SETTLE_SCHEMA,
    SHAPES,
)

Record = dict[str, Any]
Slot = tuple[int, int]
Trials = dict[Slot, Record]
Usage = list[tuple[str, int | None]]
SCHEMA = "seatlab.price-timing-summary/v1"
REPEATS = 2
SLOTS = tuple((w, r) for w in range(ANALYSIS_WORLDS) for r in range(REPEATS))
FINANCE = ("finance_a", "finance_b")
ARMS = ("early", "late")
TURNS = (
    ("sales", "sales"),
    ("finance_a", "finance-price"),
    ("finance_a", "finance-receipt"),
    ("finance_b", "finance-price"),
    ("finance_b", "finance-receipt"),
)
TURN_KEYS = frozenset(
    {
        "seat",
        "purpose",
        "session_sha256",
        "prompt_sha256",
        "answer_sha256",
        "client_tokens",
    }
)
TURN_FIXED = {"tool_steps": 0, "credit_fallback": False}
OWNER_KEYS = frozenset(
    {
        "seat",
        "purpose",
        "request_recorded",
        "admission",
        "client_tokens",
        "session_sha256",
        "verified",
    }
)
MAX_OWNER_TURNS = 16
ROW_KEYS = frozenset({"world", "repeat", "early", "status"})
COMPLETE_KEYS = ROW_KEYS | {
    "native_id",
    "checkpoint_sha256",
    "protocol_reward",
    "report",
    "picks",
    "turns",
    "settlements",
}
SETTLE_KEYS = frozenset(
    {
        "pick",
        "checkpoint_sha256",
        "profit_usd",
        "continuation_profit_usd",
        "net_worth_usd",
        "stock_score",
        "bankrupt",
        "end_day",
    }
)
HEX64 = re.compile(r"[0-9a-f]{64}")
MAX_ABS_CENTS = 10**14
MAX_TOKENS = 10**8
SCORE_STEP = Decimal("0.000001")
ALPHA = Fraction(1, 20)
USAGE_SOURCE = (
    "client-reported usage.total_tokens; not a server or provider attestation"
)
OWNER_SOURCE = (
    "owner-directory sessions of incomplete trials; separate from registered turns, "
    "not a price or behavioral success; tokens counted only when verified"
)

assert len(SLOTS) == 16 and len(SHAPES) > ANALYSIS_WORLDS
assert set(PRICE_MULTIPLIERS) == set(ACTIONS) == {1, 2, 3}


def early_seat(world: int, repeat: int) -> str:
    if (
        type(world) is not int
        or not 0 <= world < ANALYSIS_WORLDS
        or repeat not in (0, 1)
    ):
        raise ValueError(f"no registered slot world={world!r} repeat={repeat!r}")
    first = hashlib.sha256(f"{NAMESPACE}:{world}:assignment".encode()).digest()[0]
    return FINANCE[(first % 2) ^ repeat]


def _hex(value: Any, label: str) -> str:
    if type(value) is not str or HEX64.fullmatch(value) is None:
        raise ValueError(f"{label} is not a lowercase sha256 digest")
    return value


def _native(value: Any, label: str) -> str:
    if type(value) is not str or str(uuid.UUID(value)) != value:
        raise ValueError(f"{label} native_id is not a canonical UUID")
    return value


def _number(value: Any, label: str) -> Decimal:
    if type(value) is float:
        number = Decimal(repr(value))
    elif type(value) in (int, Decimal):
        number = Decimal(value)
    else:
        raise ValueError(f"{label} is not a number")
    if not number.is_finite():
        raise ValueError(f"{label} is not finite")
    return number


def _cents(value: Any, label: str) -> int:
    scaled = _number(value, label) * 100
    if abs(scaled) > MAX_ABS_CENTS or scaled != scaled.to_integral_value():
        raise ValueError(f"{label} is not a bounded whole-cent USD amount")
    return int(scaled)


def _score(value: Any, label: str) -> Fraction:
    number = _number(value, label)
    if not 0 <= number <= 1 or number != number.quantize(SCORE_STEP):
        raise ValueError(f"{label} is not a stock score in [0, 1] with 6 digits")
    return Fraction(number)


def _pick(value: Any, label: str) -> int:
    if type(value) is not int or value not in PRICE_MULTIPLIERS:
        raise ValueError(
            f"{label} pick {value!r} is not one of {sorted(PRICE_MULTIPLIERS)}"
        )
    return value


def _report(value: Any, label: str) -> int:
    if type(value) is not dict or value.keys() != {"pick", "reason"}:
        raise ValueError(f"{label} report must hold exactly pick and reason")
    reason = value["reason"]
    if type(reason) is not str or not 0 < len(reason) <= REASON_MAX_CHARS:
        raise ValueError(f"{label} report reason is not 1..{REASON_MAX_CHARS} chars")
    return _pick(value["pick"], label)


def _settlement(
    item: Any, label: str, pick: int | None, checkpoint: str | None
) -> Record:
    if type(item) is not dict or not SETTLE_KEYS <= item.keys():
        raise ValueError(f"{label} lacks settlement keys {sorted(SETTLE_KEYS)}")
    if (
        item.get("schema", SETTLE_SCHEMA) != SETTLE_SCHEMA
        or item.get("source", PIN) != PIN
    ):
        raise ValueError(f"{label} is not a {SETTLE_SCHEMA} settlement at {PIN}")
    chosen = _pick(item["pick"], label)
    seal = _hex(item["checkpoint_sha256"], f"{label} checkpoint")
    if (pick is not None and chosen != pick) or (
        checkpoint is not None and seal != checkpoint
    ):
        raise ValueError(f"{label} settles a different pick or checkpoint")
    if (
        "price_multiplier" in item
        and item["price_multiplier"] != PRICE_MULTIPLIERS[chosen]
    ):
        raise ValueError(f"{label} multiplier disagrees with its pick")
    bankrupt, end = item["bankrupt"], item["end_day"]
    if (
        type(bankrupt) is not bool
        or type(end) is not int
        or not PREFIX_DAYS < end <= NATIVE_DAYS
    ):
        raise ValueError(f"{label} bankruptcy or end_day is malformed")
    if not bankrupt and end != NATIVE_DAYS:
        raise ValueError(f"{label} solvent branch stopped before day {NATIVE_DAYS}")
    if "bankrupt_day" in item and item["bankrupt_day"] != (
        end - 1 if bankrupt else None
    ):
        raise ValueError(f"{label} bankrupt_day disagrees with end_day")
    settled = {
        "pick": chosen,
        "continuation": _cents(
            item["continuation_profit_usd"], f"{label} continuation"
        ),
        "profit": _cents(item["profit_usd"], f"{label} profit"),
        "net_worth": _cents(item["net_worth_usd"], f"{label} net worth"),
        "score": _score(item["stock_score"], f"{label} stock score"),
        "bankrupt": bankrupt,
    }
    if bankrupt and (settled["net_worth"] or settled["score"]):
        raise ValueError(f"{label} bankrupt branch reports value or score")
    return settled


def _turn(
    item: Any, label: str, early: str
) -> tuple[tuple[str, str], str, str, int | None]:
    if type(item) is not dict or not TURN_KEYS <= item.keys():
        raise ValueError(f"{label} turn keys must be {sorted(TURN_KEYS)}")
    key = (item["seat"], item["purpose"])
    if key not in TURNS:
        raise ValueError(f"{label} turn {key!r} is not a registered seat turn")
    for name in ("session_sha256", "prompt_sha256", "answer_sha256"):
        _hex(item[name], f"{label} turn {name}")
    if any(
        name in item and item[name] is not value for name, value in TURN_FIXED.items()
    ):
        raise ValueError(f"{label} turn used tools or a credit fallback")
    models = [item.get(name) for name in ("requested_model", "served_model")]
    if any(model is not None and type(model) is not str for model in models):
        raise ValueError(f"{label} turn model fields are malformed")
    tokens = item["client_tokens"]
    if tokens is not None and (type(tokens) is not int or not 0 < tokens <= MAX_TOKENS):
        raise ValueError(f"{label} client_tokens is not a positive int or null")
    role = "sales" if key[0] == "sales" else ARMS[key[0] != early]
    return key, item["session_sha256"], f"{role}:{key[1]}", tokens


def _turns(
    items: Any, label: str, early: str, complete: bool
) -> tuple[list[str], Usage]:
    if type(items) is not list or len(items) > len(TURNS):
        raise ValueError(f"{label} turns must be a list of at most {len(TURNS)}")
    parsed = [_turn(item, label, early) for item in items]
    keys = [key for key, _, _, _ in parsed]
    if len(set(keys)) != len(keys) or (complete and set(keys) != set(TURNS)):
        raise ValueError(f"{label} turns repeat a seat turn or miss one")
    for seat in FINANCE:
        order = [purpose for owner, purpose in keys if owner == seat]
        if order and order[0] != "finance-price":
            raise ValueError(f"{label} {seat} acknowledged before its price")
    return [session for _, session, _, _ in parsed], [
        (role, n) for _, _, role, n in parsed
    ]


def _slot(row: Any) -> tuple[int, int]:
    if type(row) is not dict or not ROW_KEYS <= row.keys():
        raise ValueError(f"row lacks {sorted(ROW_KEYS)}")
    slot = (row["world"], row["repeat"])
    if type(slot[1]) is not int or row["early"] != early_seat(*slot):
        raise ValueError(f"row {slot!r} early seat is not the preregistered assignment")
    if row.get("control", False) is not False or row.get("kind", "live") != "live":
        raise ValueError(f"row {slot!r} is not a live study cell")
    if row["status"] not in ("complete", "incomplete"):
        raise ValueError(f"row {slot!r} status must be complete or incomplete")
    return slot


def _complete(row: Record, label: str) -> Record:
    if not COMPLETE_KEYS <= row.keys():
        raise ValueError(
            f"{label} complete row lacks {sorted(COMPLETE_KEYS - row.keys())}"
        )
    reward = row["protocol_reward"]
    if type(reward) not in (int, float, Decimal) or reward != 1:
        raise ValueError(f"{label} complete row protocol_reward is not 1")
    checkpoint = _hex(row["checkpoint_sha256"], f"{label} checkpoint")
    picks, settlements = row["picks"], row["settlements"]
    if type(picks) is not dict or picks.keys() != set(FINANCE):
        raise ValueError(f"{label} picks must be keyed {FINANCE}")
    if type(settlements) is not dict or settlements.keys() != set(FINANCE):
        raise ValueError(f"{label} settlements must be keyed {FINANCE}")
    early = row["early"]
    late = FINANCE[early == FINANCE[0]]
    sessions, usage = _turns(row["turns"], label, early, True)
    arms = {
        arm: _settlement(
            settlements[seat], f"{label} {seat}", _pick(picks[seat], label), checkpoint
        )
        for arm, seat in zip(ARMS, (early, late))
    }
    return {
        "native_id": _native(row["native_id"], label),
        "checkpoint": checkpoint,
        "sessions": sessions,
        "usage": usage,
        "owner": [],
        "report_pick": _report(row["report"], label),
        "arms": arms,
        "delta": arms["early"]["continuation"] - arms["late"]["continuation"],
    }


def _partial(row: Record, label: str) -> Record:
    early = row["early"]
    picks = row.get("picks") or {}
    settlements = row.get("settlements") or {}
    if type(picks) is not dict or not picks.keys() <= set(FINANCE):
        raise ValueError(f"{label} partial picks must be keyed within {FINANCE}")
    if type(settlements) is not dict or not settlements.keys() <= set(FINANCE):
        raise ValueError(f"{label} partial settlements must be keyed within {FINANCE}")
    checkpoint = row.get("checkpoint_sha256")
    checkpoint = None if checkpoint is None else _hex(checkpoint, f"{label} checkpoint")
    native = row.get("native_id")
    sessions, usage = _turns(row.get("turns") or [], label, early, False)
    owner = _owner(row.get("owner_turns_observed") or [], label, early)
    late = FINANCE[early == FINANCE[0]]
    seats = dict(zip(ARMS, (early, late)))
    chosen = {
        arm: _pick(picks[seat], label) for arm, seat in seats.items() if seat in picks
    }
    arms = {
        arm: _settlement(
            settlements[seat], f"{label} {seat}", chosen.get(arm), checkpoint
        )
        for arm, seat in seats.items()
        if seat in settlements
    }
    report = row.get("report")
    reward = row.get("protocol_reward")
    if reward is not None and (
        type(reward) not in (int, float, Decimal) or reward not in (0, 1)
    ):
        raise ValueError(f"{label} protocol reward must be zero, one or unknown")
    return {
        "native_id": None if native is None else _native(native, label),
        "checkpoint": checkpoint,
        "sessions": sessions,
        "usage": usage,
        "owner": owner,
        "report_pick": None if report is None else _report(report, label),
        "picks": {arm: chosen.get(arm, arms.get(arm, {}).get("pick")) for arm in ARMS},
        "arms": arms,
        "grade": reward,
    }


def _owner(
    items: Any, label: str, early: str
) -> list[tuple[str, bool, bool, int | None, str | None]]:
    if type(items) is not list or len(items) > MAX_OWNER_TURNS:
        raise ValueError(
            f"{label} owner_turns_observed must be a list of at most {MAX_OWNER_TURNS}"
        )
    parsed = []
    for item in items:
        if type(item) is not dict or not OWNER_KEYS <= item.keys():
            raise ValueError(f"{label} owner turn lacks {sorted(OWNER_KEYS)}")
        key = (item["seat"], item["purpose"])
        recorded, verified, tokens = (
            item["request_recorded"],
            item["verified"],
            item["client_tokens"],
        )
        session = item["session_sha256"]
        if key not in TURNS or type(recorded) is not bool or type(verified) is not bool:
            raise ValueError(f"{label} owner turn {key!r} is malformed")
        if tokens is not None and (
            type(tokens) is not int or not 0 < tokens <= MAX_TOKENS
        ):
            raise ValueError(
                f"{label} owner client_tokens is not a positive int or null"
            )
        if session is not None:
            _hex(session, f"{label} owner session")
        role = "sales" if key[0] == "sales" else ARMS[key[0] != early]
        parsed.append((f"{role}:{key[1]}", recorded, verified, tokens, session))
    owned = [entry[4] for entry in parsed if entry[4] is not None]
    if len(set(owned)) != len(owned):
        raise ValueError(f"{label} owner turns repeat a session")
    return parsed


def _parse(rows: Any) -> tuple[Trials, Trials]:
    if type(rows) is not list or len(rows) != len(SLOTS):
        raise ValueError(f"panel must list exactly {len(SLOTS)} registered slots")
    complete: Trials = {}
    partial: Trials = {}
    sessions: dict[str, Slot] = {}
    natives: set[str] = set()
    checkpoints: dict[str, int] = {}
    company_checkpoints: dict[int, str] = {}
    for row in rows:
        slot = _slot(row)
        if slot in complete or slot in partial:
            raise ValueError(f"slot {slot!r} appears twice")
        label = f"world {slot[0]} repeat {slot[1]}"
        trial = (
            _complete(row, label)
            if row["status"] == "complete"
            else _partial(row, label)
        )
        (complete if row["status"] == "complete" else partial)[slot] = trial
        if trial["native_id"] is not None:
            if trial["native_id"] in natives:
                raise ValueError(f"{label} reuses a native_id")
            natives.add(trial["native_id"])
        if any(s in sessions for s in trial["sessions"]) or len(
            set(trial["sessions"])
        ) != len(trial["sessions"]):
            raise ValueError(f"{label} reuses a turn session")
        sessions |= dict.fromkeys(trial["sessions"], slot)
        for owned in (entry[4] for entry in trial["owner"] if entry[4] is not None):
            if sessions.setdefault(owned, slot) != slot:
                raise ValueError(f"{label} owner session belongs to another trial")
        checkpoint = trial["checkpoint"]
        if checkpoint is not None:
            if checkpoints.setdefault(checkpoint, slot[0]) != slot[0]:
                raise ValueError(f"{label} shares a checkpoint with another company")
            if company_checkpoints.setdefault(slot[0], checkpoint) != checkpoint:
                raise ValueError(f"{label} differs from the company's other repetition")
    if set(complete) | set(partial) != set(SLOTS):
        raise ValueError("panel misses a registered slot")
    return complete, partial


def _fixed(value: Fraction, places: int) -> str:
    with localcontext() as context:
        context.prec = 60
        exact = Decimal(value.numerator) / Decimal(value.denominator)
        return str(exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))


def _usd(cents: Fraction | int) -> str:
    return _fixed(Fraction(cents) / 100, 4)


def _sign(value: Fraction | int) -> str:
    return "positive" if value > 0 else "negative" if value < 0 else "zero"


def _wlt(values: Sequence[Fraction | int]) -> dict[str, int]:
    return {
        "early_wins": sum(v > 0 for v in values),
        "late_wins": sum(v < 0 for v in values),
        "ties": sum(v == 0 for v in values),
    }


def _arm(settled: list[Record], picks: list[int], report_picks: list[int]) -> Record:
    n = len(settled)
    if n == 0:
        return {"pairs": 0}
    return {
        "pairs": n,
        "picks": {ACTIONS[p]: picks.count(p) for p in PRICE_MULTIPLIERS},
        "agrees_with_sales": sum(p == q for p, q in zip(picks, report_picks)),
        "mean_continuation_profit_usd": _usd(
            Fraction(sum(s["continuation"] for s in settled), n)
        ),
        "mean_operating_profit_usd": _usd(
            Fraction(sum(s["profit"] for s in settled), n)
        ),
        "mean_net_worth_usd": _usd(Fraction(sum(s["net_worth"] for s in settled), n)),
        "mean_stock_score": _fixed(
            sum((s["score"] for s in settled), Fraction(0)) / n, 8
        ),
        "bankruptcies": sum(s["bankrupt"] for s in settled),
    }


def _sign_test(company_deltas: list[Fraction]) -> Fraction:
    signed = [d for d in company_deltas if d != 0]
    wins = sum(d > 0 for d in signed)
    if not signed:
        return Fraction(1)
    return Fraction(
        sum(comb(len(signed), k) for k in range(wins, len(signed) + 1)),
        2 ** len(signed),
    )


def _compare(complete: Trials) -> Record:
    pairs = [complete[slot] for slot in SLOTS if slot in complete]
    companies: dict[int, Fraction] = {}
    for world in range(ANALYSIS_WORLDS):
        slots = [(world, r) for r in range(REPEATS)]
        if all(slot in complete for slot in slots):
            companies[world] = Fraction(
                sum(complete[slot]["delta"] for slot in slots), REPEATS
            )
    return {
        "complete_pairs": len(pairs),
        "pair_level": _wlt([t["delta"] for t in pairs]),
        "company_level": _wlt(list(companies.values())),
        "price_choices_differ": sum(
            t["arms"]["early"]["pick"] != t["arms"]["late"]["pick"] for t in pairs
        ),
        "arms": {
            arm: _arm(
                [t["arms"][arm] for t in pairs],
                [t["arms"][arm]["pick"] for t in pairs],
                [t["report_pick"] for t in pairs],
            )
            for arm in ARMS
        },
        "companies": [
            {
                "world": world,
                "shape": SHAPES[world],
                "pair_deltas_usd": [
                    _usd(complete[(world, r)]["delta"]) for r in range(REPEATS)
                ],
                "mean_delta_usd": _usd(delta),
            }
            for world, delta in companies.items()
        ],
        "_company_deltas": list(companies.values()),
    }


def _registered(compared: Record, complete: bool) -> Record:
    deltas = compared.pop("_company_deltas")
    if not complete:
        return {
            "verdict": "inconclusive",
            "reason": "at least one registered pair is incomplete",
        }
    mean = sum(deltas, Fraction(0)) / len(deltas)
    p = _sign_test(deltas)
    differs = compared["price_choices_differ"] > 0
    benefit = mean > 0 and differs and p <= ALPHA
    return {
        "verdict": "benefit" if benefit else "no_established_benefit",
        "primary_mean_delta_usd": _usd(mean),
        "primary_mean_sign": _sign(mean),
        "sign_test_one_sided_p": f"{p.numerator}/{p.denominator}",
        "sign_test_p_float": float(p),
        "sign_test_nonzero_companies": sum(d != 0 for d in deltas),
        "any_price_choice_differs": differs,
        "alpha": f"{ALPHA.numerator}/{ALPHA.denominator}",
    }


def _usage(trials: list[Record]) -> Record:
    roles: dict[str, Record] = {}
    for role, tokens in (u for trial in trials for u in trial["usage"]):
        entry = roles.setdefault(
            role, {"turns": 0, "unknown_turns": 0, "known_tokens": 0}
        )
        entry["turns"] += 1
        entry["unknown_turns"] += tokens is None
        entry["known_tokens"] += tokens or 0
    for entry in roles.values():
        entry["tokens"] = (
            entry["known_tokens"] if entry["unknown_turns"] == 0 else "unknown"
        )
    unknown = sum(e["unknown_turns"] for e in roles.values())
    known = sum(e["known_tokens"] for e in roles.values())
    return {
        "source": USAGE_SOURCE,
        "observed_turns": sum(e["turns"] for e in roles.values()),
        "total_tokens": known if unknown == 0 else "unknown",
        "known_tokens": known,
        "unknown_turns": unknown,
        "unrecorded_failed_turns": "unknown",
        "by_role": dict(sorted(roles.items())),
    }


def _owner_usage(partial: Trials) -> Record:
    roles: dict[str, Record] = {}
    for role, recorded, verified, tokens, _ in (
        e for trial in partial.values() for e in trial["owner"]
    ):
        entry = roles.setdefault(
            role,
            {
                "observed": 0,
                "requests_recorded": 0,
                "verified": 0,
                "verified_unknown_tokens": 0,
                "verified_tokens": 0,
            },
        )
        entry["observed"] += 1
        entry["requests_recorded"] += recorded
        entry["verified"] += verified
        entry["verified_unknown_tokens"] += verified and tokens is None
        entry["verified_tokens"] += tokens if verified and tokens is not None else 0
    unknown = sum(e["verified_unknown_tokens"] for e in roles.values())
    known = sum(e["verified_tokens"] for e in roles.values())
    return {
        "source": OWNER_SOURCE,
        "observed": sum(e["observed"] for e in roles.values()),
        "verified": sum(e["verified"] for e in roles.values()),
        "verified_tokens": known if unknown == 0 else "unknown",
        "verified_known_tokens": known,
        "verified_unknown_tokens": unknown,
        "unverified_tokens": "not counted",
        "by_role": dict(sorted(roles.items())),
    }


def _observed(partial: Trials) -> list[Record]:
    rows = []
    for (world, repeat), trial in sorted(partial.items()):
        arms = trial["arms"]
        both = set(arms) == set(ARMS)
        rows.append(
            {
                "world": world,
                "repeat": repeat,
                "report_pick": trial["report_pick"],
                "picks": trial["picks"],
                "settled_arms": sorted(arms),
                "observed_delta_usd": _usd(
                    arms["early"]["continuation"] - arms["late"]["continuation"]
                )
                if both
                else None,
                "verified_turns": len(trial["usage"]),
                "owner_turns_observed": len(trial["owner"]),
                "owner_turns_verified": sum(entry[2] for entry in trial["owner"]),
                "grade": trial["grade"],
            }
        )
    return rows


def summarize(rows: list[Record]) -> Record:
    complete, partial = _parse(rows)
    compared = _compare(complete)
    registered = _registered(compared, not partial)
    return {
        "schema": SCHEMA,
        "slots": len(SLOTS),
        "complete": len(complete),
        "incomplete": [{"world": w, "repeat": r} for w, r in sorted(partial)],
        "registered": registered,
        "complete_pairs_descriptive": compared,
        "incomplete_pairs_observed": _observed(partial),
        "usage": _usage([*complete.values(), *partial.values()]),
        "owner_usage_incomplete": _owner_usage(partial),
        "checkpoints_match_across_repeats": sum(
            complete[(w, 0)]["checkpoint"] == complete[(w, 1)]["checkpoint"]
            for w in range(ANALYSIS_WORLDS)
            if (w, 0) in complete and (w, 1) in complete
        ),
    }


def _strict(pairs: list[tuple[str, Any]]) -> Record:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate JSON key")
    return dict(pairs)


def _constant(name: str) -> Any:
    raise ValueError(f"JSON constant {name} is not allowed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize the registered price-timing panel."
    )
    parser.add_argument("rows", type=Path)
    args = parser.parse_args(argv)
    rows = json.loads(
        args.rows.read_text(encoding="utf-8"),
        object_pairs_hook=_strict,
        parse_float=Decimal,
        parse_constant=_constant,
    )
    print(json.dumps(summarize(rows), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
