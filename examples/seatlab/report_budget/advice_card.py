from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..market_handoff import simulator, timing_messages, timing_panel
from ..market_handoff.observations import WINDOW_DAYS
from . import domain, panel, replay

Record = dict[str, Any]
ARMS = ("facts", "advice")
WORLDS = (22, 8, 4, 1, 0, 20, 11, 18)
SOURCE_SHA256 = "d42f481e38c9d6bf819a546b666f9749fece0c1985fd0fdda96a9927c9ea2e5e"
RANK_NAMESPACE = "seatlab/recommendation-card/pilot/v1"
SENDER_TARGET = panel.SHORT
NO_ADVICE = 0
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
SALES_CARD_KEYS = frozenset(
    {"day", "window_days", "units", "orders", "site_price_over_rival_median"}
)
FACTS_LINE = "SALES_FACTS: "
ADVICE_LINE = "ADVICE_CODE: "
_HEAD = (
    "You are the finance lead.\n"
    "You work for a simulated online retail company. "
    f"Today is day {timing_messages.DECISION_DAY} of its operation.\n"
    "You make exactly one price decision now. It applies one multiplier to the "
    "company's prices from this decision until closure. Closure is the end of the "
    f"simulation: after the next {timing_messages.HORIZON_DAYS} days, or at "
    "bankruptcy if that comes first.\n"
    f"Options: {timing_messages._OPTIONS}.\n"
    "The goal is the company's realized operating profit from this decision until "
    "closure. The goal is not cash, not the balance sheet and not company valuation. "
    f"The simulation continues from the real day-{timing_messages.DECISION_DAY} "
    "state. You do not receive any future observations or outcomes.\n"
    "FINANCE_VIEW is your private balance-sheet view as JSON.\n"
    "SALES_FACTS is a sales fact card rebuilt from the company's own records as "
    f"JSON: the last observed day, the last {WINDOW_DAYS}-day window, its units "
    "and orders, and the median ratio of site price to rival price on the last "
    "observed day.\n"
    "ADVICE_CODE is the sales lead's price recommendation. 0 means no "
    "recommendation is shown. 1, 2 or 3 is the option the sales lead recommended "
    "after seeing private sales observations.\n"
)
_ASK = 'Reply with exactly one JSON object and nothing else: {"pick": <1, 2 or 3>}\n'


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ranked_worlds() -> tuple[int, ...]:
    ranked = sorted(
        range(domain.WORLD_COUNT),
        key=lambda world: _sha(f"{RANK_NAMESPACE}:{world}".encode()),
    )
    return tuple(ranked[: len(WORLDS)])


assert _ranked_worlds() == WORLDS and len(set(WORLDS)) == len(WORLDS) == 8
assert SENDER_TARGET == 100 and ARMS == ("facts", "advice")
assert NO_ADVICE not in timing_messages.MULTIPLIERS
assert all(len(str(code)) == 1 for code in (NO_ADVICE, *timing_messages.MULTIPLIERS))
assert ADVICE_LINE not in _HEAD + _ASK and FACTS_LINE not in _HEAD + _ASK


def _number(value: Any, label: str, positive: bool) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} is not a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{label} is out of range")
    return value


def _sales_card(view: Any, label: str) -> Record:
    timing_messages._sales_view(view)
    latest, window = view["latest"], view["windows"][-1]
    site = None if type(latest) is not dict else latest["site_price_over_rival_median"]
    if type(site) is not dict:
        raise ValueError(f"{label} sales view lacks a latest site price ratio")
    ratio = site["median"]
    if ratio != window["end_site_price_over_rival_median"]:
        raise ValueError(f"{label} latest ratio disagrees with the last window")
    if view["days"] is None or latest["day"] != view["days"][1]:
        raise ValueError(f"{label} latest day is not the last observed day")
    card = {
        "day": latest["day"],
        "window_days": list(window["days"]),
        "units": window["units"],
        "orders": window["orders"],
        "site_price_over_rival_median": ratio,
    }
    _check_sales_card(card, label)
    return card


def _check_sales_card(card: Any, label: str) -> None:
    if type(card) is not dict or card.keys() != SALES_CARD_KEYS:
        raise ValueError(
            f"{label} sales_card must hold exactly {sorted(SALES_CARD_KEYS)}"
        )
    day, days = card["day"], card["window_days"]
    if (
        type(days) is not list
        or len(days) != 2
        or any(type(d) is not int for d in days)
    ):
        raise ValueError(f"{label} sales_card window_days is not a day pair")
    if days[1] - days[0] + 1 != WINDOW_DAYS or days[1] >= timing_messages.DECISION_DAY:
        raise ValueError(f"{label} sales_card window is not a pre-decision window")
    if type(day) is not int or not days[0] <= day <= days[1]:
        raise ValueError(f"{label} sales_card day is outside the last window")
    _number(card["units"], f"{label} units", positive=False)
    _number(card["orders"], f"{label} orders", positive=False)
    _number(card["site_price_over_rival_median"], f"{label} price ratio", positive=True)


def _check_card(card: Any) -> Record:
    if type(card) is not dict or card.keys() != CARD_KEYS:
        raise ValueError(f"card must hold exactly {sorted(CARD_KEYS)}")
    rank = card["rank"]
    if type(rank) is not int or not 0 <= rank < len(WORLDS):
        raise ValueError(f"card rank {rank!r} is not registered")
    label = f"rank {rank} world {card['world']}"
    if type(card["world"]) is not int or card["world"] != WORLDS[rank]:
        raise ValueError(f"{label} is not the registered world for its rank")
    timing_panel._native(card["native_id"], label)
    for name in ("checkpoint_sha256", "sender_session_sha256", "sender_answer_sha256"):
        timing_panel._hex(card[name], f"{label} {name}")
    timing_panel._pick(card["sender_pick"], f"{label} sender")
    _check_sales_card(card["sales_card"], label)
    timing_messages._finance_view(card["finance_view"])
    return card


def _render(card: Record, code: int) -> str:
    body = (
        timing_messages.FINANCE_LINE
        + timing_messages._finance_view(card["finance_view"])
        + "\n"
        + FACTS_LINE
        + timing_messages._canonical(card["sales_card"])
        + "\n"
        + ADVICE_LINE
        + str(code)
        + "\n"
    )
    return timing_messages._bounded(_HEAD + body + _ASK)


def _one_byte(facts: str, advice: str, code: int) -> None:
    marker = "\n" + ADVICE_LINE
    if facts.count(marker) != 1 or advice.count(marker) != 1:
        raise ValueError("advice prompt must hold exactly one ADVICE_CODE line")
    site = facts.index(marker) + len(marker)
    left, right = facts.encode(), advice.encode()
    differ = [i for i, (a, b) in enumerate(zip(left, right, strict=False)) if a != b]
    if len(left) != len(right) or differ != [len(facts[:site].encode())]:
        raise ValueError(
            "advice prompt pair does not differ in exactly the advice byte"
        )
    if facts[site] != str(NO_ADVICE) or advice[site] != str(code):
        raise ValueError("advice prompt pair holds the wrong advice codes")


def prompt(card: Record, arm: str) -> str:
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, got {arm!r}")
    _check_card(card)
    code = card["sender_pick"]
    facts, advice = _render(card, NO_ADVICE), _render(card, code)
    _one_byte(facts, advice, code)
    return facts if arm == "facts" else advice


def slots(cards: list[Record]) -> Iterator[tuple[Record, str]]:
    if type(cards) is not list or len(cards) != len(WORLDS):
        raise ValueError(f"cards must list exactly {len(WORLDS)} ranked cards")
    for rank, card in enumerate(cards):
        if _check_card(card)["rank"] != rank:
            raise ValueError(f"card {rank} is out of rank order")
    for rank, card in enumerate(cards):
        for arm in ARMS if rank % 2 == 0 else ARMS[::-1]:
            yield card, arm


def _source_rows(source: Path) -> dict[int, Record]:
    data = simulator.read_own(source)
    if _sha(data) != SOURCE_SHA256:
        raise ValueError(f"{source} is not the registered frozen rows {SOURCE_SHA256}")
    rows = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=timing_panel._strict,
        parse_constant=timing_panel._constant,
    )
    panel.summarize(rows)
    picked = {
        row["world"]: row
        for row in rows
        if row["target"] == SENDER_TARGET and row["world"] in WORLDS
    }
    if set(picked) != set(WORLDS):
        raise ValueError("source lacks a registered sender row")
    return picked


def _sender(row: Record, label: str) -> Record:
    if (
        row["status"] != "observed"
        or row["sales_status"] != "accepted"
        or row["delivered"] is not True
        or row["finance_seen"] is not True
        or row["report"] is None
    ):
        raise ValueError(f"{label} is not an observed accepted delivered report")
    timing_panel._native(row["native_id"], label)
    timing_panel._hex(row["checkpoint_sha256"], f"{label} checkpoint")
    sales, finance = row["sales"], row["finance"]
    if (
        sales is None
        or sales["source"] != "model"
        or sales["request"] is not True
        or sales["verified"] is not True
        or sales["posted"] is not True
        or sales["status"] != "accepted"
    ):
        raise ValueError(f"{label} sender is not a verified posted accepted model turn")
    timing_panel._hex(sales["session_sha256"], f"{label} sender session")
    reply = sales["reply"]
    if type(reply) is not str or _sha(reply.encode()) != sales["answer_sha256"]:
        raise ValueError(
            f"{label} sender reply is not bound by its archived answer hash"
        )
    report = domain.read_sales(reply)
    if report != row["report"]:
        raise ValueError(
            f"{label} sender reply does not reparse to the delivered report"
        )
    if finance is None or finance["request"] is not True:
        raise ValueError(f"{label} delivered report lacks its original finance request")
    timing_panel._hex(finance["prompt_sha256"], f"{label} finance prompt")
    return report


def _card(stock: Any, rank: int, world: int, row: Record) -> Record:
    label = f"rank {rank} world {world}"
    report = _sender(row, label)
    _, binding = domain.make_company(stock, world)
    if binding["stock_commit"] != simulator.PIN:
        raise ValueError(f"{label} company is not built at the stock pin")
    if binding["checkpoint_sha256"] != row["checkpoint_sha256"]:
        raise ValueError(f"{label} checkpoint differs from the rebuilt company")
    sales_prompt = domain.sales_prompt(binding["sales"], SENDER_TARGET)
    if _sha(sales_prompt.encode()) != row["sales"]["prompt_sha256"]:
        raise ValueError(f"{label} posted sender prompt does not rebuild")
    finance_prompt = domain.finance_prompt(binding["finance"], report)
    if _sha(finance_prompt.encode()) != row["finance"]["prompt_sha256"]:
        raise ValueError(f"{label} original finance view does not rebuild")
    card = {
        "rank": rank,
        "world": world,
        "native_id": row["native_id"],
        "checkpoint_sha256": binding["checkpoint_sha256"],
        "sender_session_sha256": row["sales"]["session_sha256"],
        "sender_answer_sha256": row["sales"]["answer_sha256"],
        "sender_pick": report["pick"],
        "sales_card": _sales_card(binding["sales"], label),
        "finance_view": binding["finance"],
    }
    for arm in ARMS:
        prompt(card, arm)
    return card


def make_cards(stock: Any, source: Path) -> list[Record]:
    if stock.numpy_version != replay.NUMPY_VERSION:
        raise ValueError(f"the registered runtime uses NumPy {replay.NUMPY_VERSION}")
    rows = _source_rows(source)
    cards = [
        _card(stock, rank, world, rows[world]) for rank, world in enumerate(WORLDS)
    ]
    for name in ("native_id", "checkpoint_sha256", "sender_session_sha256"):
        if len({card[name] for card in cards}) != len(cards):
            raise ValueError(f"cards reuse a {name}")
    if len(list(slots(cards))) != len(WORLDS) * len(ARMS):
        raise ValueError("cards do not yield every registered slot")
    return cards


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the registered advice-card pilot cards without model calls."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--stock", type=Path, required=True)
    args = parser.parse_args(argv)
    stock = simulator.load_stock(args.stock.resolve(strict=True))
    cards = make_cards(stock, args.source)
    print(
        json.dumps(cards, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
