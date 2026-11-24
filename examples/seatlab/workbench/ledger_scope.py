import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, Inexact, Rounded, localcontext
from typing import Any

from .account_contract import money
from .generation_receipts import (
    BASIS,
    PRECISION,
    _Ledger,
    _ledger,
    _ledger_summary,
    route_label,
)
from .seat_stream import Malformed, Record, _require

SCHEMA = "seatlab.ledger-scope/v1"
MAX_FAMILIES = 16
MAX_LABELS = 256
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
UNRESOLVED = ("failed", "billing_unavailable", "no_outcome")
OUTCOMES = ("settled", *UNRESOLVED)
CAVEAT = (
    "Each section regroups the same local ledger calls. Sections are alternative scopes: "
    "they are not additive across sections, and no section is fallback proof for another. "
    "Reported routes cover settled calls only. Known USD is the returned generation "
    "total_cost of settled calls, each local call counted once. Failed, billing-unavailable "
    "and no-outcome calls carry no known charge here. Reservations and harness estimates "
    "are never added. A family is only the exact first slash-delimited segment of the local "
    "identity that matches a tag the operator supplied. It does not identify a campaign, "
    "trial, seat or team. Records only, not signed. No claim of completeness or of live "
    "prices."
)
HEADER = (
    f"{'started':>8} {'settled':>8} {'failed':>7} {'billing_unavailable':>20} "
    f"{'no_outcome':>11} {'known_usd':>24}  scope"
)

Route = tuple[str | None, str | None]


@dataclass
class _Tally:
    started: int = 0
    outcomes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OUTCOMES, 0))
    usd: Decimal = field(default_factory=Decimal)


@dataclass
class _Scan:
    total: _Tally
    families: dict[str | None, _Tally]
    requested: dict[str | None, _Tally]
    reported: dict[Route, _Tally]


def _tags(families: object) -> frozenset[str]:
    _require(type(families) is list, "invalid family tag list")
    assert type(families) is list
    _require(len(families) <= MAX_FAMILIES, f"more than {MAX_FAMILIES} family tags")
    tags: set[str] = set()
    for tag in families:
        if type(tag) is not str or TAG.fullmatch(tag) is None:
            raise Malformed("invalid family tag")
        _require(tag not in tags, "repeated family tag")
        tags.add(tag)
    return frozenset(tags)


def _family(identity: object, tags: frozenset[str]) -> str | None:
    if type(identity) is not str or "/" not in identity:
        return None
    tag = identity.split("/", 1)[0]
    return tag if tag in tags else None


def _outcome(ledger: _Ledger, call: str) -> str:
    if call in ledger.settled:
        return "settled"
    if call in ledger.failed:
        return "failed"
    if call in ledger.unavailable:
        return "billing_unavailable"
    return "no_outcome"


def _add(tally: _Tally, outcome: str, usd: Decimal) -> None:
    assert outcome in OUTCOMES and usd >= 0
    assert outcome == "settled" or usd == 0
    tally.started += 1
    tally.outcomes[outcome] += 1
    tally.usd += usd


def _slot[K](grid: dict[K, _Tally], key: K, reason: str) -> _Tally:
    tally = grid.get(key)
    if tally is None:
        _require(len(grid) < MAX_LABELS, reason)
        tally = grid[key] = _Tally()
    return tally


def _scan(ledger: _Ledger, tags: frozenset[str], routes: bool) -> _Scan:
    scan = _Scan(_Tally(), {tag: _Tally() for tag in tags}, {}, {})
    scan.families[None] = _Tally()
    requested_reason = f"more than {MAX_LABELS} requested route labels"
    reported_reason = f"more than {MAX_LABELS} reported route labels"
    for call, row in ledger.started.items():
        outcome = _outcome(ledger, call)
        usd = ledger.settled[call][1] if outcome == "settled" else Decimal(0)
        _add(scan.total, outcome, usd)
        _add(scan.families[_family(row.get("identity"), tags)], outcome, usd)
        if not routes:
            continue
        label = route_label(row.get("model"))
        _add(_slot(scan.requested, label, requested_reason), outcome, usd)
        if outcome == "settled":
            _require(call in ledger.routes, "settled call has no route record")
            reported = _slot(scan.reported, ledger.routes[call], reported_reason)
            _add(reported, outcome, usd)
    return scan


def _counts(tally: _Tally) -> Record:
    return {
        "started": tally.started,
        "settled": tally.outcomes["settled"],
        "unresolved": {outcome: tally.outcomes[outcome] for outcome in UNRESOLVED},
        "known_usd": money(tally.usd),
    }


def _order(label: str | None) -> tuple[bool, str]:
    return label is None, label or ""


def _route_order(route: Route) -> tuple[bool, str, bool, str]:
    return (*_order(route[0]), *_order(route[1]))


def _check(summary: Record, scan: _Scan, routes: bool) -> None:
    total = scan.total
    assert summary["calls"] == total.started
    assert summary["settled_calls"] == total.outcomes["settled"]
    assert summary["unsettled_calls"] == {k: total.outcomes[k] for k in UNRESOLVED}
    for grid in (scan.families, scan.requested):
        assert not grid or sum(t.started for t in grid.values()) == total.started
        assert not grid or sum((t.usd for t in grid.values()), Decimal(0)) == total.usd
    reported = sum(t.started for t in scan.reported.values())
    assert reported == (total.outcomes["settled"] if routes else 0)
    assert routes or not scan.requested


def _routes(scan: _Scan) -> tuple[list[Record], list[Record]]:
    requested = [
        {"model": label, **_counts(scan.requested[label])}
        for label in sorted(scan.requested, key=_order)
    ]
    reported = [
        {
            "model": route[0],
            "provider": route[1],
            "settled": scan.reported[route].outcomes["settled"],
            "known_usd": money(scan.reported[route].usd),
        }
        for route in sorted(scan.reported, key=_route_order)
    ]
    return requested, reported


def _project(ledger: _Ledger, tags: frozenset[str], routes: bool) -> Record:
    scan = _scan(ledger, tags, routes)
    summary = _ledger_summary(ledger)
    _check(summary, scan, routes)
    requested, reported = _routes(scan) if routes else (None, None)
    return {
        "schema": SCHEMA,
        "basis": BASIS,
        "currency": "USD",
        "ledger": summary,
        "all_calls": _counts(scan.total),
        "families": [
            {"family": tag, **_counts(scan.families[tag])}
            for tag in sorted(scan.families, key=_order)
        ],
        "requested_routes": requested,
        "reported_routes": reported,
        "caveat": CAVEAT,
    }


def summarize(path: str, *, families: list[str], routes: bool) -> Record:
    _require(type(path) is str, "invalid ledger path")
    _require(type(routes) is bool, "invalid route option")
    tags = _tags(families)
    try:
        with localcontext() as context:
            context.prec = PRECISION
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            return _project(_ledger(path, include_routes=routes), tags, routes)
    except DecimalException as error:
        raise Malformed("USD arithmetic exceeds exact supported range") from error


def _label(value: str | None) -> str:
    return json.dumps(value, ensure_ascii=True)


def _line(counts: Record, scope: str) -> str:
    unresolved = counts["unresolved"]
    return (
        f"{counts['started']:>8} {counts['settled']:>8} {unresolved['failed']:>7} "
        f"{unresolved['billing_unavailable']:>20} {unresolved['no_outcome']:>11} "
        f"{counts['known_usd']:>24}  {scope}"
    )


def _settled_line(row: Record) -> str:
    scope = f"model={_label(row['model'])} provider={_label(row['provider'])}"
    return (
        f"{'-':>8} {row['settled']:>8} {'-':>7} {'-':>20} {'-':>11} "
        f"{row['known_usd']:>24}  {scope}"
    )


def _route_lines(report: Record) -> list[str]:
    if report["requested_routes"] is None:
        assert report["reported_routes"] is None
        return ["", "routes: not collected"]
    return [
        "",
        "[requested route: start model label; null = absent or unusable]",
        HEADER,
        *(
            _line(row, f"model={_label(row['model'])}")
            for row in report["requested_routes"]
        ),
        "",
        "[reported route: settled receipt model and provider; null = absent or unusable]",
        HEADER,
        *(_settled_line(row) for row in report["reported_routes"]),
    ]


def render(report: dict[str, Any]) -> str:
    _require(
        type(report) is dict and report.get("schema") == SCHEMA,
        "invalid ledger scope report",
    )
    ledger = report["ledger"]
    lines = [
        f"ledger scope ({SCHEMA}): records only, not signed; no live price claim",
        f"basis {report['basis']}; currency {report['currency']}; exact decimal",
        (
            f"ledger rows {ledger['rows']}; "
            f"repeated start rows {ledger['repeated_start_rows']}; "
            f"repeated settlement rows {ledger['repeated_settlement_rows']}; "
            f"reservation holds {ledger['reservation_holds']}"
        ),
        "Sections describe the same calls. Do not add them or infer a fallback.",
        "",
        "[all calls]",
        HEADER,
        _line(report["all_calls"], "all"),
        "",
        "[family: exact first slash segment named by operator; null = other]",
        HEADER,
        *(_line(row, f"family={_label(row['family'])}") for row in report["families"]),
        *_route_lines(report),
        "",
        report["caveat"],
    ]
    return "\n".join(lines) + "\n"
