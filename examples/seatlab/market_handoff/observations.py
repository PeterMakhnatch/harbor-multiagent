from __future__ import annotations

import json
import math
import statistics
from typing import Any

PREFIX_DAYS = 90
WINDOW_DAYS = 30
WINDOW_COUNT = PREFIX_DAYS // WINDOW_DAYS
CHANNELS = ("site", "marketplace")
AD_CHANNELS = ("social", "sponsored")
ROLE_MAX_CHARS = 1500
RATIO_DIGITS = 3
RATING_DIGITS = 2
USD_DIGITS = 2

assert WINDOW_DAYS * WINDOW_COUNT == PREFIX_DAYS


def _finite(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value if math.isfinite(value) else None


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, RATIO_DIGITS)


def _usd(value: float | None) -> float | None:
    return None if value is None else round(float(value), USD_DIGITS)


def _channel_ratios(products: dict[str, Any], channel: str) -> tuple[list[float], int]:
    ratios = []
    quotes = 0
    for product in products.values():
        if not isinstance(product, dict) or product.get("active") is not True:
            continue
        channels = product.get("channels")
        prices = product.get("price")
        if (
            not isinstance(channels, list)
            or channel not in channels
            or not isinstance(prices, dict)
        ):
            continue
        own = _finite(prices.get(channel))
        rivals = [
            p
            for p in map(_finite, product.get("rival_prices") or [])
            if p is not None and p > 0
        ]
        if own is None or own <= 0 or not rivals:
            continue
        ratios.append(own / statistics.median(rivals))
        quotes += len(rivals)
    assert len(ratios) <= len(products)
    return ratios, quotes


def _median_ratio(products: dict[str, Any], channel: str) -> float | None:
    ratios, _ = _channel_ratios(products, channel)
    return round(statistics.median(ratios), RATIO_DIGITS) if ratios else None


def _ratio_summary(ratios: list[float]) -> dict[str, Any] | None:
    if not ratios:
        return None
    return {
        "products": len(ratios),
        "min": round(min(ratios), RATIO_DIGITS),
        "median": round(statistics.median(ratios), RATIO_DIGITS),
        "max": round(max(ratios), RATIO_DIGITS),
    }


def _ratings(products: dict[str, Any]) -> tuple[float | None, float | None]:
    own = []
    rival = []
    for product in products.values():
        if not isinstance(product, dict) or product.get("active") is not True:
            continue
        rating = _finite(product.get("rating"))
        reviews = _finite(product.get("reviews"))
        if rating is not None and reviews is not None and reviews > 0:
            own.append(rating)
        rivals = [
            r for r in map(_finite, product.get("rival_ratings") or []) if r is not None
        ]
        if rivals:
            rival.append(statistics.median(rivals))
    return (
        round(statistics.median(own), RATING_DIGITS) if own else None,
        round(statistics.median(rival), RATING_DIGITS) if rival else None,
    )


def _empty_window(index: int) -> dict[str, Any]:
    return {
        "days": [index * WINDOW_DAYS, (index + 1) * WINDOW_DAYS - 1],
        "obs": 0,
        "outcome_days": 0,
        "units": 0,
        "orders": 0,
        "sessions": 0,
        "returns_units": 0,
        "in_stock": 0,
        "stock_flags": 0,
        "revenue": 0.0,
        "refunds": 0.0,
        "ads": 0.0,
        "end_cash": None,
        "end_credit_used": None,
        "end_site_ratio": None,
    }


def _add_channel(window: dict[str, Any], outcome: Any) -> None:
    if not isinstance(outcome, dict):
        return
    for key in ("units", "orders", "sessions"):
        value = _finite(outcome.get(key))
        if value is not None:
            window[key] += value
    revenue = _finite(outcome.get("revenue"))
    if revenue is not None:
        window["revenue"] += revenue
    flag = outcome.get("in_stock")
    if isinstance(flag, bool):
        window["stock_flags"] += 1
        window["in_stock"] += int(flag)


def _add_outcome(window: dict[str, Any], outcome: dict[str, Any]) -> None:
    for channel in CHANNELS:
        _add_channel(window, outcome.get(channel))
    returned = _finite(outcome.get("returns_units"))
    if returned is not None:
        window["returns_units"] += returned
    refunds = _finite(outcome.get("refunds"))
    if refunds is not None:
        window["refunds"] += refunds
    ads = outcome.get("ads")
    if isinstance(ads, dict):
        for channel in AD_CHANNELS:
            entry = ads.get(channel)
            spend = _finite(entry.get("spend")) if isinstance(entry, dict) else None
            if spend is not None:
                window["ads"] += spend


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sales_latest(day: int, products: dict[str, Any]) -> dict[str, Any]:
    site, site_quotes = _channel_ratios(products, "site")
    market, market_quotes = _channel_ratios(products, "marketplace")
    own_rating, rival_rating = _ratings(products)
    return {
        "day": day,
        "active_products": sum(
            1
            for p in products.values()
            if isinstance(p, dict) and p.get("active") is True
        ),
        "site_price_over_rival_median": _ratio_summary(site),
        "marketplace_price_over_rival_median": _ratio_summary(market),
        "rival_price_quotes": site_quotes + market_quotes,
        "own_rating_median": own_rating,
        "rival_rating_median": rival_rating,
    }


def _finance_latest(day: int, obs: dict[str, Any]) -> dict[str, Any]:
    used = _finite(obs.get("credit_used"))
    limit = _finite(obs.get("credit_limit"))
    return {
        "day": day,
        "cash_usd": _usd(_finite(obs.get("cash"))),
        "credit_used_usd": _usd(used),
        "credit_limit_usd": _usd(limit),
        "credit_used_over_limit": None
        if used is None or limit is None
        else _ratio(used, limit),
        "marketplace_receivable_usd": _usd(_finite(obs.get("marketplace_receivable"))),
        "wholesale_receivable_usd": _usd(_finite(obs.get("wholesale_receivable"))),
    }


class SalesWindow:
    def __init__(self) -> None:
        self._windows = [_empty_window(index) for index in range(WINDOW_COUNT)]
        self._days: list[int] = []
        self._sales_now: dict[str, Any] | None = None
        self._finance_now: dict[str, Any] | None = None
        self._finished = False

    def add(self, obs: dict[str, Any]) -> None:
        assert not self._finished
        assert isinstance(obs, dict)
        day = obs.get("day")
        assert isinstance(day, int) and not isinstance(day, bool)
        assert 0 <= day < PREFIX_DAYS
        assert not self._days or day > self._days[-1]
        assert len(self._days) < PREFIX_DAYS
        products = obs.get("products")
        assert isinstance(products, dict)
        window = self._windows[day // WINDOW_DAYS]
        window["obs"] += 1
        outcomes = [
            p.get("yesterday") for p in products.values() if isinstance(p, dict)
        ]
        outcomes = [o for o in outcomes if isinstance(o, dict) and "returns_units" in o]
        if outcomes:
            window["outcome_days"] += 1
        for outcome in outcomes:
            _add_outcome(window, outcome)
        window["end_cash"] = _finite(obs.get("cash"))
        window["end_credit_used"] = _finite(obs.get("credit_used"))
        window["end_site_ratio"] = _median_ratio(products, "site")
        self._days.append(day)
        self._sales_now = _sales_latest(day, products)
        self._finance_now = _finance_latest(day, obs)

    def _span(self) -> list[int] | None:
        return [self._days[0], self._days[-1]] if self._days else None

    def _sales(self) -> dict[str, Any]:
        windows = []
        for window in self._windows:
            windows.append(
                {
                    "days": list(window["days"]),
                    "obs": window["obs"],
                    "outcome_days": window["outcome_days"],
                    "units": window["units"],
                    "orders": window["orders"],
                    "sessions": window["sessions"],
                    "returns_units": window["returns_units"],
                    "in_stock_channel_days": [
                        window["in_stock"],
                        window["stock_flags"],
                    ],
                    "end_site_price_over_rival_median": window["end_site_ratio"],
                }
            )
        return {"days": self._span(), "windows": windows, "latest": self._sales_now}

    def _finance(self) -> dict[str, Any]:
        windows = []
        for window in self._windows:
            windows.append(
                {
                    "days": list(window["days"]),
                    "obs": window["obs"],
                    "outcome_days": window["outcome_days"],
                    "revenue_usd": _usd(window["revenue"]),
                    "refunds_usd": _usd(window["refunds"]),
                    "paid_ads_usd": _usd(window["ads"]),
                    "refunds_over_revenue": _ratio(
                        window["refunds"], window["revenue"]
                    ),
                    "paid_ads_over_revenue": _ratio(window["ads"], window["revenue"]),
                    "end_cash_usd": _usd(window["end_cash"]),
                    "end_credit_used_usd": _usd(window["end_credit_used"]),
                }
            )
        return {"days": self._span(), "windows": windows, "latest": self._finance_now}

    def finish(self) -> dict[str, dict[str, Any]]:
        assert not self._finished
        self._finished = True
        result = {"sales": self._sales(), "finance": self._finance()}
        for role in result.values():
            assert len(_canonical(role)) <= ROLE_MAX_CHARS
        return result
