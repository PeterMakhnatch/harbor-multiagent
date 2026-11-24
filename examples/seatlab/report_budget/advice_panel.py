from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..market_handoff import simulator, timing_panel, timing_seat
from ..market_handoff.timing_messages import read_price
from ..visibility import agy_seat
from . import advice_card, panel

Record = dict[str, Any]
Slot = tuple[int, str]
SCHEMA = "seatlab.report-budget-advice-summary/v1"
DEFAULT_SOURCE = (
    Path(__file__).resolve().parents[1] / "results" / "report-budget" / "rows.json"
)
REGISTERED_WORLDS = (22, 8, 4, 1, 0, 20, 11, 18)
CARDS_SHA256 = "51a324e6ee5214880663393c3c33563bb068bc372f8ce648b44b2f3f099a1b07"
FACTS, ADVICE = advice_card.ARMS
FACTS_CODE = b"0"
CARD_KEYS = frozenset(
    {
        "rank",
        "world",
        "native_id",
        "checkpoint_sha256",
        "sender_session_sha256",
        "sender_answer_sha256",
        "sender_pick",
        "sales_card",
        "finance_view",
    }
)
TURN_KEYS = frozenset(
    {
        "world",
        "rank",
        "arm",
        "status",
        "request",
        "request_sha256",
        "reply",
        "receipt",
        "failure",
        "pick",
        "settlement",
    }
)
STATUSES = ("not_issued", "unanswered", "unobserved", "invalid", "priced")
EMPTY = ("not_issued", "unanswered", "unobserved")
REQUESTED = ("unanswered", "invalid", "priced")
RECEIPT_FIXED = {
    "requested_model": agy_seat.MODEL,
    "served_model": None,
    "tool_steps": 0,
    "credit_fallback": False,
}
MAX_REPLY = timing_seat.MAX_STDOUT
METRIC = (
    "continuation_profit_usd of the exact stock settlement of each committed "
    "finance price; advice minus facts on complete pairs only; a missing price "
    "is unknown, not zero"
)
ATTESTATION = (
    "each receipt is a timing_seat owner receipt: passed admission, requested "
    f"{agy_seat.MODEL}, no served model, zero tools, no paid fallback, positive "
    "client usage, fresh receiver session distinct from every archived sender"
)

assert advice_card.ARMS == ("facts", "advice")
assert tuple(advice_card.WORLDS) == REGISTERED_WORLDS
assert len(set(REGISTERED_WORLDS)) == len(REGISTERED_WORLDS)
assert set(RECEIPT_FIXED) < set(timing_seat.META_KEYS)
assert len(timing_seat.META_KEYS) == len(set(timing_seat.META_KEYS)) == 8
assert set(EMPTY) | set(REQUESTED) == set(STATUSES)


def _contrast(card: Record, label: str) -> dict[str, str]:
    facts = advice_card.prompt(card, FACTS)
    advice = advice_card.prompt(card, ADVICE)
    left, right = facts.encode("utf-8"), advice.encode("utf-8")
    spots = [i for i, (a, b) in enumerate(zip(left, right, strict=False)) if a != b]
    code = str(card["sender_pick"]).encode()
    if (
        len(left) != len(right)
        or len(spots) != 1
        or left[spots[0] : spots[0] + 1] != FACTS_CODE
        or right[spots[0] : spots[0] + 1] != code
    ):
        raise ValueError(
            f"{label} prompts are not the registered one-byte ADVICE_CODE contrast"
        )
    return {FACTS: agy_seat.sha(facts), ADVICE: agy_seat.sha(advice)}


def _bind(item: Record, row: Record, label: str) -> None:
    sales = row["sales"]
    if (
        row["delivered"] is not True
        or row["report"] is None
        or sales is None
        or sales["reply"] is None
    ):
        raise ValueError(f"{label} archived sender lacks a verified delivered report")
    archived = (
        row["native"],
        row["checkpoint"],
        sales["session"],
        agy_seat.sha(sales["reply"]),
        row["report"]["pick"],
    )
    shown = tuple(
        item[name]
        for name in (
            "native_id",
            "checkpoint_sha256",
            "sender_session_sha256",
            "sender_answer_sha256",
            "sender_pick",
        )
    )
    if shown != archived:
        raise ValueError(f"{label} is not bound to its archived short-arm sender row")


def _card(item: Any, rank: int, owners: dict[str, set[str]], archive: Record) -> Record:
    label = f"card rank {rank}"
    if type(item) is not dict or item.keys() != CARD_KEYS:
        raise ValueError(f"{label} must hold exactly {sorted(CARD_KEYS)}")
    if (
        type(item["rank"]) is not int
        or type(item["world"]) is not int
        or (item["rank"], item["world"]) != (rank, REGISTERED_WORLDS[rank])
    ):
        raise ValueError(f"{label} is not registered world {REGISTERED_WORLDS[rank]}")
    _bind(item, archive["senders"][REGISTERED_WORLDS[rank]], label)
    held = {
        "native": timing_panel._native(item["native_id"], label),
        "checkpoint": timing_panel._hex(
            item["checkpoint_sha256"], f"{label} checkpoint"
        ),
        "session": timing_panel._hex(item["sender_session_sha256"], f"{label} sender"),
    }
    timing_panel._hex(item["sender_answer_sha256"], f"{label} sender answer")
    timing_panel._pick(item["sender_pick"], f"{label} sender")
    if type(item["sales_card"]) is not dict or type(item["finance_view"]) is not dict:
        raise ValueError(f"{label} sales_card and finance_view must be objects")
    for kind, value in held.items():
        if value in owners[kind]:
            raise ValueError(f"{label} reuses an archived {kind}")
        owners[kind].add(value)
    return {"card": item, "label": label, "prompts": _contrast(item, label)}


def _archive(data: bytes) -> Record:
    if hashlib.sha256(data).hexdigest() != advice_card.SOURCE_SHA256:
        raise ValueError(f"source is not the frozen rows {advice_card.SOURCE_SHA256}")
    rows = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=timing_panel._strict,
        parse_float=Decimal,
        parse_constant=timing_panel._constant,
    )
    slots = panel._parse(rows)
    sessions = frozenset(
        row[role]["session"]
        for row in slots.values()
        for role in panel.ROLES
        if row[role] is not None and row[role]["session"] is not None
    )
    senders = {
        world: slots[(world, advice_card.SENDER_TARGET)] for world in REGISTERED_WORLDS
    }
    return {"sessions": sessions, "senders": senders}


def _cards(items: Any, archive: Record) -> list[Record]:
    if type(items) is not list or len(items) != len(REGISTERED_WORLDS):
        raise ValueError(f"cards must list exactly {len(REGISTERED_WORLDS)} companies")
    canonical = json.dumps(
        items, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if hashlib.sha256((canonical + "\n").encode()).hexdigest() != CARDS_SHA256:
        raise ValueError(f"cards are not the registered card list {CARDS_SHA256}")
    owners: dict[str, set[str]] = {
        "native": set(),
        "checkpoint": set(),
        "session": set(),
    }
    return [_card(item, rank, owners, archive) for rank, item in enumerate(items)]


def _grid(cards: list[Record]) -> list[tuple[Record, str]]:
    expected = [
        (card, arm)
        for card in cards
        for arm in (advice_card.ARMS[:: 1 if card["card"]["rank"] % 2 == 0 else -1])
    ]
    shown = list(advice_card.slots([card["card"] for card in cards]))
    if len(shown) != len(expected) or any(
        (got["world"], got_arm) != (want["card"]["world"], want_arm)
        for (got, got_arm), (want, want_arm) in zip(shown, expected, strict=True)
    ):
        raise ValueError("advice_card.slots disagrees with the registered order")
    return expected


def _failure(value: Any, label: str, request: bool) -> None:
    if value is None:
        return
    if type(value) is not str or not 0 < len(value) <= panel.MAX_REASON_CHARS:
        raise ValueError(f"{label} failure must be a bounded string or null")
    if request and value.startswith("admission_"):
        raise ValueError(f"{label} records an admission failure after a request")


def _receipt(value: Any, label: str, digest: str, reply: str) -> Record:
    if type(value) is not dict or value.keys() != set(timing_seat.META_KEYS):
        raise ValueError(f"{label} receipt must hold exactly {timing_seat.META_KEYS}")
    session = timing_panel._hex(value["session_sha256"], f"{label} receiver session")
    prompt = timing_panel._hex(value["prompt_sha256"], f"{label} receipt prompt")
    answer = timing_panel._hex(value["answer_sha256"], f"{label} receipt answer")
    if prompt != digest or answer != agy_seat.sha(reply):
        raise ValueError(f"{label} receipt is not bound to the request and reply")
    if any(
        type(value[name]) is not type(fixed) or value[name] != fixed
        for name, fixed in RECEIPT_FIXED.items()
    ):
        raise ValueError(f"{label} receipt is not the released model without tools")
    tokens = value["client_tokens"]
    if type(tokens) is not int or not 0 < tokens <= panel.MAX_TOKENS:
        raise ValueError(f"{label} receipt lacks positive client usage")
    return {"session": session, "answer": answer, "tokens": tokens}


def _answered(item: Record, card: Record, label: str) -> Record:
    reply = item["reply"]
    if type(reply) is not str or not 0 < len(reply) <= MAX_REPLY:
        raise ValueError(f"{label} answered turn lacks a bounded verified reply")
    if item["failure"] is not None:
        raise ValueError(f"{label} answered turn also records a failure")
    meta = _receipt(item["receipt"], label, item["request_sha256"], reply)
    try:
        price = read_price(reply)
    except ValueError:
        price = None
    pick, settled = item["pick"], item["settlement"]
    if item["status"] == "invalid":
        if price is not None or pick is not None or settled is not None:
            raise ValueError(f"{label} invalid turn holds a readable price")
        return meta | {"pick": None, "settlement": None}
    if price is None or type(pick) is not int or pick != price:
        raise ValueError(f"{label} pick is not the verified reply's price")
    checkpoint = card["card"]["checkpoint_sha256"]
    return meta | {
        "pick": pick,
        "settlement": panel._settlement(settled, label, pick, checkpoint),
    }


def _turn(item: Any, card: Record, arm: str) -> Record:
    world, rank = card["card"]["world"], card["card"]["rank"]
    label = f"world {world} rank {rank} {arm}"
    if type(item) is not dict or item.keys() != TURN_KEYS:
        raise ValueError(f"{label} turn must hold exactly {sorted(TURN_KEYS)}")
    if (type(item["world"]), type(item["rank"])) != (int, int) or (
        item["world"],
        item["rank"],
        item["arm"],
    ) != (world, rank, arm):
        raise ValueError(f"{label} is not the registered grid slot")
    status, request, digest = item["status"], item["request"], item["request_sha256"]
    if status not in STATUSES or type(request) is not bool:
        raise ValueError(f"{label} status or request flag is not registered")
    _failure(item["failure"], label, request)
    if request and digest != card["prompts"][arm]:
        raise ValueError(f"{label} recorded request is not prompt(card, {arm!r})")
    if not request and digest is not None:
        raise ValueError(f"{label} records a request digest without a request")
    if (status == "not_issued" and request) or (status in REQUESTED and not request):
        raise ValueError(f"{label} status {status} disagrees with its request flag")
    base = {"label": label, "status": status, "request": request, "digest": digest}
    if status in EMPTY:
        held = [
            k for k in ("reply", "receipt", "pick", "settlement") if item[k] is not None
        ]
        if held:
            raise ValueError(f"{label} is {status} yet records {held}")
        empty = {"session": None, "answer": None, "tokens": None}
        return base | empty | {"pick": None, "settlement": None}
    return base | _answered(item, card, label)


def _turns(items: Any, cards: list[Record], archive: Record) -> dict[Slot, Record]:
    grid = _grid(cards)
    if type(items) is not list or len(items) != len(grid):
        raise ValueError(f"turns must list exactly {len(grid)} registered slots")
    archived = archive["sessions"]
    sessions: set[str] = set()
    turns: dict[Slot, Record] = {}
    for item, (card, arm) in zip(items, grid, strict=True):
        turn = _turn(item, card, arm)
        session = turn["session"]
        if session is not None and session in archived:
            raise ValueError(f"{turn['label']} reuses an archived verified session")
        if session is not None and session in sessions:
            raise ValueError(f"{turn['label']} reuses a receiver session")
        if session is not None:
            sessions.add(session)
        turns[(card["card"]["world"], arm)] = turn
    assert len(turns) == len(grid)
    return turns


def _company(card: Record, turns: dict[Slot, Record]) -> Record:
    world = card["card"]["world"]
    arms = {arm: turns[(world, arm)] for arm in advice_card.ARMS}
    facts, advice = arms[FACTS], arms[ADVICE]
    complete = facts["pick"] is not None and advice["pick"] is not None
    changed = delta = None
    if complete:
        changed = facts["pick"] != advice["pick"]
        if not changed and facts["settlement"] != advice["settlement"]:
            raise ValueError(f"world {world} settles one pick two ways")
        delta = (
            advice["settlement"]["continuation"] - facts["settlement"]["continuation"]
        )
    return {"card": card["card"], "arms": arms, "changed": changed, "delta": delta}


def _arm_view(turn: Record, recommended: int) -> Record:
    pick = turn["pick"]
    return {
        "status": turn["status"],
        "pick": "unknown" if pick is None else pick,
        "matches_recommendation": None if pick is None else pick == recommended,
        "request_sha256": turn["digest"],
        "receiver_session_sha256": turn["session"],
        "answer_sha256": turn["answer"],
        "continuation_profit_usd": None
        if pick is None
        else timing_panel._usd(turn["settlement"]["continuation"]),
    }


def _company_view(company: Record) -> Record:
    card, delta = company["card"], company["delta"]
    recommended = card["sender_pick"]
    return {
        "world": card["world"],
        "rank": card["rank"],
        "first_arm": advice_card.ARMS[card["rank"] % 2],
        "native_id": card["native_id"],
        "checkpoint_sha256": card["checkpoint_sha256"],
        "sender_session_sha256": card["sender_session_sha256"],
        "sender_answer_sha256": card["sender_answer_sha256"],
        "sender_pick": recommended,
        "pair": "unknown" if delta is None else "complete",
        "price_changed": company["changed"],
        "advice_minus_facts_usd": None if delta is None else timing_panel._usd(delta),
        "arms": {
            arm: _arm_view(turn, recommended) for arm, turn in company["arms"].items()
        },
    }


def _usage(cards: list[Record], turns: dict[Slot, Record]) -> Record:
    fields = ("slots", "issued", "requests", "verified", "prices", "unknown_prices")
    table = {
        arm: dict.fromkeys(fields, 0) | {"known_client_tokens": 0}
        for arm in advice_card.ARMS
    }
    for (_, arm), turn in sorted(turns.items()):
        entry = table[arm]
        entry["slots"] += 1
        entry["issued"] += turn["status"] != "not_issued"
        entry["requests"] += turn["request"]
        entry["verified"] += turn["tokens"] is not None
        entry["prices"] += turn["pick"] is not None
        entry["unknown_prices"] += turn["pick"] is None
        entry["known_client_tokens"] += turn["tokens"] or 0
    totals = {
        f: sum(e[f] for e in table.values()) for f in (*fields, "known_client_tokens")
    }
    return {
        "archived_native_senders": len(cards),
        "new_native_teams": 0,
        "new_sales_requests": 0,
        "finance": totals
        | {"requests_with_unknown_tokens": totals["requests"] - totals["verified"]},
        "by_arm": table,
        "status": {s: sum(t["status"] == s for t in turns.values()) for s in STATUSES},
        "attestation": ATTESTATION,
        "usage_source": panel.USAGE_SOURCE,
        "billing": "not claimed",
    }


def _recommendation(companies: list[Record]) -> Record:
    out: Record = {}
    for arm in advice_card.ARMS:
        priced = [c for c in companies if c["arms"][arm]["pick"] is not None]
        out[arm] = {
            "matched": sum(
                c["arms"][arm]["pick"] == c["card"]["sender_pick"] for c in priced
            ),
            "denominator_priced": len(priced),
        }
    complete = [c for c in companies if c["delta"] is not None]
    out["complete_pairs"] = {
        "denominator": len(complete),
        **{
            f"{arm}_matched": sum(
                c["arms"][arm]["pick"] == c["card"]["sender_pick"] for c in complete
            )
            for arm in advice_card.ARMS
        },
    }
    return out


def _profit(companies: list[Record]) -> Record:
    deltas = [c["delta"] for c in companies if c["delta"] is not None]
    total = sum(deltas)
    return {
        "metric": METRIC,
        "complete_pairs": len(deltas),
        "sum_advice_minus_facts_usd": timing_panel._usd(total) if deltas else None,
        "mean_advice_minus_facts_usd": timing_panel._usd(Fraction(total, len(deltas)))
        if deltas
        else None,
        "advice_wins": sum(d > 0 for d in deltas),
        "facts_wins": sum(d < 0 for d in deltas),
        "ties": sum(d == 0 for d in deltas),
    }


def _readiness(companies: list[Record], usage: Record) -> Record:
    complete = sum(c["delta"] is not None for c in companies)
    changed = sum(c["changed"] is True for c in companies)
    verified = usage["finance"]["verified"]
    feasible = complete == len(REGISTERED_WORLDS) and verified == 2 * complete
    return {
        "complete_pairs": complete,
        "required_pairs": len(REGISTERED_WORLDS),
        "one_byte_contrast_verified": len(companies),
        "exact_settlements": usage["finance"]["prices"],
        "feasible": feasible,
        "price_changes": changed,
        "larger_trial_on_new_companies_warranted": feasible and changed >= 1,
        "new_native_teams": 0,
        "value_claim": False,
        "value_claim_reason": (
            "descriptive feasibility pilot; a positive profit difference does not "
            "warrant exposing recommendations by default"
        ),
    }


def summarize(cards: Any, turns: Any, source: bytes) -> Record:
    archive = _archive(source)
    parsed = _cards(cards, archive)
    grid = _turns(turns, parsed, archive)
    companies = [_company(card, grid) for card in parsed]
    usage = _usage(parsed, grid)
    missing = [
        {
            "world": c["card"]["world"],
            "rank": c["card"]["rank"],
            "arms": {
                arm: t["status"] for arm, t in c["arms"].items() if t["pick"] is None
            },
        }
        for c in companies
        if c["delta"] is None
    ]
    return {
        "schema": SCHEMA,
        "source": {
            "sha256": advice_card.SOURCE_SHA256,
            "sender_target": advice_card.SENDER_TARGET,
            "archived_verified_sessions": len(archive["sessions"]),
        },
        "cards_sha256": CARDS_SHA256,
        "worlds": list(REGISTERED_WORLDS),
        "arms": list(advice_card.ARMS),
        "usage": usage,
        "missing_pairs": missing,
        "recommendation_agreement": _recommendation(companies),
        "price_changes": sum(c["changed"] is True for c in companies),
        "profit": _profit(companies),
        "companies": [_company_view(c) for c in companies],
        "readiness": _readiness(companies, usage),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize the registered facts versus advice finance replay."
    )
    parser.add_argument("cards", type=Path)
    parser.add_argument("turns", type=Path)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args(argv)
    source = simulator.read_own(args.source)
    cards = json.loads(
        args.cards.read_text(encoding="utf-8"),
        object_pairs_hook=timing_panel._strict,
        parse_float=float,
        parse_constant=timing_panel._constant,
    )
    turns = json.loads(
        args.turns.read_text(encoding="utf-8"),
        object_pairs_hook=timing_panel._strict,
        parse_float=Decimal,
        parse_constant=timing_panel._constant,
    )
    print(json.dumps(summarize(cards, turns, source), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
