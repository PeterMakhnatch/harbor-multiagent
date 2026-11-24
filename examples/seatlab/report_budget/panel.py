from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any

from ..market_handoff import timing_messages, timing_panel
from ..market_handoff.simulator import PIN
from ..market_handoff.timing_world import SETTLE_SCHEMA
from ..visibility.agy_seat import MODEL
from .domain import SETTLE_KEYS, TARGETS, WORLD_COUNT, read_price, read_sales

Record = dict[str, Any]
Slot = tuple[int, int]
SCHEMA = "seatlab.report-budget-summary/v1"
ROW_SCHEMA = "seatlab.report-budget-row/v1"
SHORT, LONG = TARGETS
SLOTS = tuple((world, target) for world in range(WORLD_COUNT) for target in TARGETS)
ROLES = ("sales", "finance")
ISSUED = ("observed", "unobserved")
UNISSUED = ("not_issued", "predecision")
SALES_STATUSES = ("accepted", "invalid", "unanswered", "unobserved")
PAIRS = ("both", "short_only", "long_only", "neither", "unknown")
ROW_KEYS = frozenset(
    {
        "schema",
        "world",
        "target",
        "control",
        "status",
        "checkpoint_sha256",
        "native_id",
        "protocol_reward",
        "reason",
        "sales_status",
        "report",
        "finance_seen",
        "delivered",
        "finance_pick",
        "settlement",
        "sales",
        "finance",
    }
)
TURN_KEYS = frozenset(
    {
        "source",
        "request",
        "verified",
        "failure",
        "admission",
        "session_sha256",
        "prompt_sha256",
        "answer_sha256",
        "requested_model",
        "served_model",
        "client_tokens",
        "tool_steps",
        "credit_fallback",
        "reply",
        "posted",
        "status",
        "actor_failure",
    }
)
OWNER_FACTS = ("session_sha256", "answer_sha256", "requested_model", "served_model")
OWNER_USAGE = ("client_tokens", "tool_steps", "credit_fallback")
RELEASED = {"requested_model": MODEL, "served_model": None, "tool_steps": 0}
ADMISSION_KEYS = frozenset({"outcome", "stage", "cause"})
PASSED = {"outcome": "passed", "stage": "complete", "cause": "none"}
WIRE_STATUSES = ("accepted", "invalid", "unanswered")
ALPHA = Fraction(1, 20)
MAX_TOKENS = 10**8
MAX_REASON_CHARS = 4096
USAGE_SOURCE = (
    "client-reported usage.total_tokens on verified turns only; "
    "not a billing, provider or server attestation"
)
METRIC = (
    "continuation_profit_usd of the actual stock settlement of each committed "
    "finance price, day 90 to closure; exact cents; a missing price is missing, not zero"
)

assert len(SLOTS) == 48 and SHORT < LONG == timing_messages.REASON_MAX_CHARS
assert set(ISSUED).isdisjoint(UNISSUED)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _optional(value: Any, label: str, kind: type) -> Any:
    if value is not None and type(value) is not kind:
        raise ValueError(f"{label} must be {kind.__name__} or null")
    return value


def _text(value: Any, label: str) -> str | None:
    if value is not None and (
        type(value) is not str or not 0 < len(value) <= MAX_REASON_CHARS
    ):
        raise ValueError(f"{label} must be a bounded string or null")
    return value


def _admission(value: Any, label: str, request: bool) -> None:
    if value is None:
        if request:
            raise ValueError(f"{label} request lacks its passed admission")
        return
    if type(value) is not dict or value.keys() != ADMISSION_KEYS:
        raise ValueError(f"{label} admission must hold {sorted(ADMISSION_KEYS)}")
    if any(item is not None and type(item) is not str for item in value.values()):
        raise ValueError(f"{label} admission fields must be strings or null")
    if request and value != PASSED:
        raise ValueError(f"{label} request was not admitted passed/complete/none")


def _posted(item: Record, label: str) -> None:
    if item["source"] != "model" or type(item["posted"]) is not bool:
        raise ValueError(f"{label} is not a posted-flagged model turn")
    status = item["status"]
    if status not in (None, *WIRE_STATUSES) or (status is not None) != item["posted"]:
        raise ValueError(f"{label} world status disagrees with posted")
    _optional(item["reply"], f"{label} reply", str)
    _text(item["failure"], f"{label} failure")
    _text(item["actor_failure"], f"{label} actor_failure")


def _verified(item: Record, label: str) -> None:
    reply, tokens = item["reply"], item["client_tokens"]
    if reply is None or _sha(reply) != timing_panel._hex(
        item["answer_sha256"], f"{label} answer"
    ):
        raise ValueError(f"{label} verified turn lacks a reply bound by answer_sha256")
    timing_panel._hex(item["session_sha256"], f"{label} session")
    if type(tokens) is not int or not 0 < tokens <= MAX_TOKENS:
        raise ValueError(f"{label} verified turn lacks a client token count")
    if (
        any(
            type(item[name]) is not type(value) or item[name] != value
            for name, value in RELEASED.items()
        )
        or item["credit_fallback"] is not False
    ):
        raise ValueError(
            f"{label} turn is not the released model without tools or fallback"
        )


def _turn(item: Any, label: str) -> Record | None:
    if item is None:
        return None
    if type(item) is not dict or item.keys() != TURN_KEYS:
        raise ValueError(f"{label} must be null or hold exactly {sorted(TURN_KEYS)}")
    request, verified = item["request"], item["verified"]
    if type(request) is not bool or type(verified) is not bool:
        raise ValueError(f"{label} request and verified must be booleans")
    _posted(item, label)
    _admission(item["admission"], label, request)
    if request:
        timing_panel._hex(item["prompt_sha256"], f"{label} prompt")
    elif verified or item["prompt_sha256"] is not None:
        raise ValueError(
            f"{label} records a verified answer or prompt without a request"
        )
    if verified:
        _verified(item, label)
    elif item["reply"] is not None or any(
        item[name] is not None for name in OWNER_FACTS + OWNER_USAGE
    ):
        raise ValueError(
            f"{label} carries a reply, receipt or usage that was not verified"
        )
    return {
        "request": request,
        "verified": verified,
        "posted": item["posted"],
        "status": item["status"],
        "prompt": item["prompt_sha256"],
        "session": item["session_sha256"],
        "reply": item["reply"],
        "tokens": item["client_tokens"],
    }


def _reward(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if type(value) not in (int, float, Decimal) or value not in (0, 1):
        raise ValueError(f"{label} protocol_reward must be 0, 1 or null")
    return int(value)


def _report(value: Any, label: str) -> Record | None:
    if value is None:
        return None
    try:
        return timing_messages._report(value)
    except ValueError as error:
        raise ValueError(
            f"{label} report is not a wire-valid report: {error}"
        ) from error


def _settlement(
    item: Any, label: str, pick: int | None, checkpoint: str | None
) -> Record:
    if type(item) is not dict or item.keys() != SETTLE_KEYS:
        raise ValueError(f"{label} settlement must hold exactly {sorted(SETTLE_KEYS)}")
    if item["schema"] != SETTLE_SCHEMA or item["source"] != PIN:
        raise ValueError(f"{label} settlement is not a {SETTLE_SCHEMA} record at {PIN}")
    return timing_panel._settlement(item, f"{label} settlement", pick, checkpoint)


def _header(row: Any) -> tuple[Slot, str]:
    if type(row) is not dict or row.keys() != ROW_KEYS:
        raise ValueError(f"row must hold exactly {sorted(ROW_KEYS)}")
    slot = (row["world"], row["target"])
    if any(type(part) is not int for part in slot) or slot not in SLOTS:
        raise ValueError(f"row {slot!r} is not a registered held-out slot")
    label = f"world {slot[0]} target {slot[1]}"
    if row["schema"] != ROW_SCHEMA:
        raise ValueError(f"{label} is not a {ROW_SCHEMA} row")
    if row["control"] is not False:
        raise ValueError(f"{label} is a control row; controls are not panel slots")
    if row["status"] not in ISSUED + UNISSUED:
        raise ValueError(f"{label} status {row['status']!r} is not registered")
    if row["sales_status"] not in SALES_STATUSES:
        raise ValueError(f"{label} sales_status {row['sales_status']!r} is unknown")
    reason = _optional(row["reason"], f"{label} reason", str)
    if reason is not None and not 0 < len(reason) <= MAX_REASON_CHARS:
        raise ValueError(f"{label} reason is empty or unbounded")
    if row["status"] != "observed" and reason is None:
        raise ValueError(f"{label} is {row['status']} without a reason")
    return slot, label


def _row(item: Any) -> Record:
    slot, label = _header(item)
    checkpoint = item["checkpoint_sha256"]
    checkpoint = None if checkpoint is None else timing_panel._hex(checkpoint, label)
    native = item["native_id"]
    pick = item["finance_pick"]
    pick = None if pick is None else timing_panel._pick(pick, f"{label} finance")
    settled = item["settlement"]
    row = {
        "world": slot[0],
        "target": slot[1],
        "label": label,
        "status": item["status"],
        "checkpoint": checkpoint,
        "native": None if native is None else timing_panel._native(native, label),
        "reward": _reward(item["protocol_reward"], label),
        "sales_status": item["sales_status"],
        "report": _report(item["report"], label),
        "finance_seen": _optional(item["finance_seen"], f"{label} finance_seen", bool),
        "delivered": _optional(item["delivered"], f"{label} delivered", bool),
        "pick": pick,
        "settlement": None
        if settled is None
        else _settlement(settled, label, pick, checkpoint),
        "sales": _turn(item["sales"], f"{label} sales"),
        "finance": _turn(item["finance"], f"{label} finance"),
    }
    if row["status"] in UNISSUED:
        _unissued(row)
    else:
        _issued(row)
    return row


def _unissued(row: Record) -> None:
    held = (
        "native",
        "reward",
        "report",
        "finance_seen",
        "delivered",
        "pick",
        "settlement",
    )
    present = [name for name in held if row[name] is not None]
    present += [
        role for role in ROLES if row[role] is not None and row[role]["request"]
    ]
    if present or row["sales_status"] != "unobserved":
        raise ValueError(
            f"{row['label']} is {row['status']} yet records {present or row['sales_status']}"
        )


def _issued(row: Record) -> None:
    label = row["label"]
    if row["status"] == "observed" and (
        row["native"] is None
        or row["checkpoint"] is None
        or row["sales_status"] == "unobserved"
    ):
        raise ValueError(
            f"{label} is observed without native id, checkpoint or sales outcome"
        )
    _sales_consistent(row)
    _finance_consistent(row)


def _sales_consistent(row: Record) -> None:
    label, status, report = row["label"], row["sales_status"], row["report"]
    turn, delivered = row["sales"], row["delivered"]
    if (report is not None) != (status == "accepted"):
        raise ValueError(f"{label} must hold a report exactly when sales was accepted")
    reply = None if turn is None else turn["reply"]
    if reply is not None and status in ("accepted", "invalid"):
        try:
            parsed = read_sales(reply)
        except ValueError:
            parsed = None
        if parsed != report:
            raise ValueError(f"{label} verified sales reply does not parse to {status}")
    if status == "unobserved" and delivered is not None:
        raise ValueError(f"{label} claims a delivery outcome with unobserved sales")
    if status != "unobserved" and (
        turn is None or not turn["request"] or turn["status"] != status
    ):
        raise ValueError(f"{label} sales outcome lacks its proven posted request")
    if delivered is True and (status != "accepted" or reply is None):
        raise ValueError(
            f"{label} is delivered without a verified accepted sales reply"
        )
    if delivered is False and (turn is None or not turn["request"]):
        raise ValueError(f"{label} is undelivered without a proven sales request")


def _finance_consistent(row: Record) -> None:
    label, turn, seen = row["label"], row["finance"], row["finance_seen"]
    pick, reward = row["pick"], row["reward"]
    requested = turn is not None and turn["request"]
    if (seen is True and not requested) or (requested and seen is False):
        raise ValueError(f"{label} finance_seen disagrees with the finance request")
    if row["delivered"] is True and (
        seen is not True or not requested or turn["prompt"] is None
    ):
        raise ValueError(
            f"{label} is delivered without finance seeing it and a provable request"
        )
    if pick is None and row["settlement"] is not None:
        raise ValueError(f"{label} settles a price finance never committed")
    if pick is not None:
        reply = turn["reply"] if requested else None
        try:
            price = None if reply is None else read_price(reply)
        except ValueError:
            price = None
        if price != pick or seen is not True or turn["status"] != "accepted":
            raise ValueError(f"{label} finance_pick is not a verified accepted reply")
        if row["settlement"] is None or row["checkpoint"] is None:
            raise ValueError(f"{label} committed price lacks its stock settlement")
    if (
        row["status"] == "observed"
        and reward is not None
        and reward != (pick is not None)
    ):
        raise ValueError(f"{label} protocol reward disagrees with the committed price")


def _cross(slots: dict[Slot, Record], row: Record, owners: Record) -> None:
    slot, label = (row["world"], row["target"]), row["label"]
    if slot in slots:
        raise ValueError(f"{label} appears twice")
    slots[slot] = row
    if row["native"] is not None:
        if row["native"] in owners["natives"]:
            raise ValueError(f"{label} reuses a native trial id")
        owners["natives"].add(row["native"])
    sessions = [row[r]["session"] for r in ROLES if row[r] is not None]
    for session in (s for s in sessions if s is not None):
        if session in owners["sessions"]:
            raise ValueError(f"{label} reuses a seat session")
        owners["sessions"].add(session)
    checkpoint = row["checkpoint"]
    if (
        checkpoint is not None
        and owners["checkpoints"].setdefault(checkpoint, slot[0]) != slot[0]
    ):
        raise ValueError(f"{label} shares a checkpoint with another company")


def _parse(rows: Any) -> dict[Slot, Record]:
    if type(rows) is not list or len(rows) != len(SLOTS):
        raise ValueError(f"panel must list exactly {len(SLOTS)} registered slots")
    slots: dict[Slot, Record] = {}
    owners: Record = {"natives": set(), "sessions": set(), "checkpoints": {}}
    for item in rows:
        _cross(slots, _row(item), owners)
    if set(slots) != set(SLOTS):
        raise ValueError("panel misses a registered slot")
    for world in range(WORLD_COUNT):
        pair = [slots[(world, target)] for target in TARGETS]
        if len({r["checkpoint"] for r in pair if r["checkpoint"] is not None}) > 1:
            raise ValueError(f"world {world} arms start from different checkpoints")
        statuses = {r["status"] for r in pair}
        if "predecision" in statuses and statuses & set(ISSUED):
            raise ValueError(f"world {world} both ended before and reached decision")
    return slots


def _upper_tail(wins: int, trials: int) -> Fraction:
    assert 0 <= wins <= trials
    return Fraction(sum(comb(trials, k) for k in range(wins, trials + 1)), 2**trials)


def _pair_kind(short: bool | None, long: bool | None) -> str:
    if short is None or long is None:
        return "unknown"
    if short and long:
        return "both"
    return "short_only" if short else "long_only" if long else "neither"


def _primary(slots: dict[Slot, Record]) -> Record:
    kinds = [
        _pair_kind(slots[(w, SHORT)]["delivered"], slots[(w, LONG)]["delivered"])
        for w in range(WORLD_COUNT)
    ]
    counts = {kind: kinds.count(kind) for kind in PAIRS}
    wins, losses = counts["short_only"], counts["long_only"]
    p = None if counts["unknown"] else _upper_tail(wins, wins + losses)
    if p is None:
        verdict = "inconclusive"
        reason = f"{counts['unknown']} of {WORLD_COUNT} delivery pairs are unknown"
    elif wins > losses and p <= ALPHA:
        verdict, reason = "improvement", "short delivers more often and p <= alpha"
    else:
        verdict, reason = "no_established_gain", "all pairs observed; test not met"
    return {
        "endpoint": "delivered: verified accepted sales report reached a proven finance request",
        "pairs": counts,
        "delivered": {
            str(t): {
                str(value).lower() if value is not None else "unknown": sum(
                    slots[(w, t)]["delivered"] is value for w in range(WORLD_COUNT)
                )
                for value in (True, False, None)
            }
            for t in TARGETS
        },
        "test": "exact one-sided conditional sign test over discordant pairs",
        "discordant": wins + losses,
        "p_one_sided": None if p is None else f"{p.numerator}/{p.denominator}",
        "p_float": None if p is None else float(p),
        "alpha": f"{ALPHA.numerator}/{ALPHA.denominator}",
        "verdict": verdict,
        "reason": reason,
    }


def _commitments(slots: dict[Slot, Record], target: int) -> Record:
    rows = [slots[(w, target)] for w in range(WORLD_COUNT)]
    settled = [r["settlement"] for r in rows if r["settlement"] is not None]
    n = len(settled)
    return {
        "issued": sum(r["status"] != "not_issued" for r in rows),
        "committed": n,
        "missing": WORLD_COUNT - n,
        "mean_continuation_profit_usd": timing_panel._usd(
            Fraction(sum(s["continuation"] for s in settled), n)
        )
        if n
        else None,
        "bankruptcies": sum(s["bankrupt"] for s in settled),
    }


def _secondary(slots: dict[Slot, Record]) -> Record:
    deltas = {
        w: slots[(w, SHORT)]["settlement"]["continuation"]
        - slots[(w, LONG)]["settlement"]["continuation"]
        for w in range(WORLD_COUNT)
        if all(slots[(w, t)]["settlement"] is not None for t in TARGETS)
    }
    values = list(deltas.values())
    mean = Fraction(sum(values), len(values)) if values else None
    met = mean is not None and len(values) == WORLD_COUNT and mean >= 0
    return {
        "metric": METRIC,
        "commitments": {str(t): _commitments(slots, t) for t in TARGETS},
        "settled_pairs": len(values),
        "short_wins": sum(v > 0 for v in values),
        "long_wins": sum(v < 0 for v in values),
        "ties": sum(v == 0 for v in values),
        "mean_short_minus_long_usd": None if mean is None else timing_panel._usd(mean),
        "pair_deltas_usd": {str(w): timing_panel._usd(v) for w, v in deltas.items()},
        "economic_guard": {
            "met": met,
            "requires": f"all {WORLD_COUNT} pairs settled and mean short-long >= 0",
        },
    }


def _usage(slots: dict[Slot, Record]) -> Record:
    fields = ("issued", "requests", "verified", "unknown_tokens", "known_client_tokens")
    table = {f"{role}:{t}": dict.fromkeys(fields, 0) for role in ROLES for t in TARGETS}
    for (_, target), row in sorted(slots.items()):
        for role in ROLES:
            entry, turn = table[f"{role}:{target}"], row[role]
            entry["issued"] += row["status"] != "not_issued"
            if turn is None or not turn["request"]:
                continue
            known = turn["verified"] and turn["tokens"] is not None
            entry["requests"] += 1
            entry["verified"] += turn["verified"]
            entry["unknown_tokens"] += not known
            entry["known_client_tokens"] += turn["tokens"] if known else 0
    return {
        "source": USAGE_SOURCE,
        "billing": "not claimed",
        "totals": {f: sum(e[f] for e in table.values()) for f in fields},
        "by_role_target": table,
    }


def _arms(slots: dict[Slot, Record]) -> Record:
    out = {}
    for target in TARGETS:
        rows = [slots[(w, target)] for w in range(WORLD_COUNT)]
        out[str(target)] = {
            "status": {
                s: sum(r["status"] == s for r in rows) for s in ISSUED + UNISSUED
            },
            "sales_status": {
                s: sum(r["sales_status"] == s for r in rows) for s in SALES_STATUSES
            },
            "finance_seen": sum(r["finance_seen"] is True for r in rows),
            "protocol_reward": {
                str(v): sum(r["reward"] == v for r in rows) for v in (1, 0)
            }
            | {"unknown": sum(r["reward"] is None for r in rows)},
        }
    return out


def summarize(rows: list[Record]) -> Record:
    slots = _parse(rows)
    primary, secondary = _primary(slots), _secondary(slots)
    return {
        "schema": SCHEMA,
        "slots": len(SLOTS),
        "worlds": WORLD_COUNT,
        "targets": list(TARGETS),
        "arms": _arms(slots),
        "primary": primary,
        "secondary": secondary,
        "recommend_short_for_this_cohort": (
            primary["verdict"] == "improvement" and secondary["economic_guard"]["met"]
        ),
        "usage": _usage(slots),
        "checkpoints_matched_within_world": sum(
            slots[(w, SHORT)]["checkpoint"] is not None
            and slots[(w, SHORT)]["checkpoint"] == slots[(w, LONG)]["checkpoint"]
            for w in range(WORLD_COUNT)
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize the paired 100 vs 160 sales report budget panel."
    )
    parser.add_argument("rows", type=Path)
    args = parser.parse_args(argv)
    rows = json.loads(
        args.rows.read_text(encoding="utf-8"),
        object_pairs_hook=timing_panel._strict,
        parse_float=Decimal,
        parse_constant=timing_panel._constant,
    )
    print(json.dumps(summarize(rows), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
