"""Render saved shipping decision cards and their first accepted-event difference."""

from __future__ import annotations

from typing import Any

SCHEMA = "seatlab.decision-card.report/v1"
FAMILY = {"stock": "stock", "inflight": "inflight", "decision-receipt": "inflight"}
WORLD_NAMES = {
    "stock": "stock shipping",
    "inflight": "in-flight shipping",
    "decision-receipt": "decision-receipt shipping",
}
BEFORE_LABELS = {
    "stock": "Stock baseline, staged before relief",
    "inflight": "In-flight state, settled before drain",
}
LIMITS = (
    "Events show what the world accepted, not polls or rejected requests. The first difference is not a cause.",
    "A citation names a reference in the accepted decision. It does not show what the receiver knew or why it acted.",
    "A stock baseline staged before relief and an in-flight state settled before drain are different measurements.",
    "A derived twin replays the same accepted events under the opposite supplier state. It is not a trial.",
)
TWIN_LIMIT = "Derived twins are not trials. No new team ran."
COUNT_ERROR = "a decision report needs one or two cards"


def _step(event: dict[str, Any]) -> tuple[str, str, str | None]:
    return event["seat"], event["kind"], event["choice"]


def _first_difference(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> int | None:
    for index in range(max(len(left), len(right))):
        if index >= len(left) or index >= len(right):
            return index + 1
        if _step(left[index]) != _step(right[index]):
            return index + 1
    return None


def _sides(left: object, right: object) -> dict[str, Any]:
    return {"left": left, "right": right, "same": left == right}


def compare(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    comparable = FAMILY[left["world"]] == FAMILY[right["world"]]
    position = (
        _first_difference(left["events"], right["events"]) if comparable else None
    )

    def at(events: list[dict[str, Any]]) -> dict[str, Any] | None:
        if position is None or position > len(events):
            return None
        return events[position - 1]

    return {
        "comparable": comparable,
        "basis": "accepted-world-events" if comparable else "different-world-families",
        "events_match": (position is None) if comparable else None,
        "first_different_event": position,
        "left_event": at(left["events"]),
        "right_event": at(right["events"]),
        "accepted_events": {"left": len(left["events"]), "right": len(right["events"])},
        "world": _sides(left["world"], right["world"]),
        "state": _sides(
            {"axis": left["axis"], "value": left["state"]},
            {"axis": right["axis"], "value": right["state"]},
        ),
        "channel": _sides(left["channel"], right["channel"]),
        "citation": _sides(left["citation"], right["citation"]),
    }


def report(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    if type(envelopes) is not list or len(envelopes) not in (1, 2):
        raise ValueError(COUNT_ERROR)
    cards = [envelope["card"] for envelope in envelopes]
    return {
        "schema": SCHEMA,
        "cards": envelopes,
        "comparison": compare(cards[0], cards[1]) if len(cards) == 2 else None,
        "limits": list(LIMITS),
    }


def _show(value: object) -> str:
    if value is None:
        return "unrecorded"
    if type(value) is list:
        return ", ".join(str(item) for item in value) or "none"
    return str(value)


def _event_text(event: dict[str, Any] | None) -> str:
    if event is None:
        return "no event"
    return " ".join(str(part) for part in _step(event) if part is not None)


def _source_text(number: int, envelope: dict[str, Any]) -> str:
    if envelope["source"] == "supplied-cut":
        return f"Card {number}: supplied cut file; no native enrollment or native grade established"
    grade = _show(envelope["native_grade"])
    return f"Card {number}: public scripted ticket {envelope['ticket']}, recorded native grade {grade}"


def _card_text(number: int, envelope: dict[str, Any]) -> list[str]:
    card = envelope["card"]
    before, final, twin = card["before"], card["final"], card["twin"]
    citation = card["citation"]
    if citation["status"] == "unrecorded":
        cited = "not recorded by this world"
    elif citation["reference"] is None:
        cited = citation["status"]
    else:
        cited = f"{citation['status']} ({citation['reference']} reference)"
    lines = [
        _source_text(number, envelope),
        f"  World: {WORLD_NAMES[card['world']]}, {card['axis']}={card['state']}, channel={card['channel']}",
        f"  Accepted events ({len(card['events'])}):",
        *(
            f"    {index}. {_event_text(event)}"
            for index, event in enumerate(card["events"], 1)
        ),
        f"  Citation at the decision: {cited}",
        f"  {BEFORE_LABELS[FAMILY[card['world']]]}: parcels {_show(before['parcels'])}, grade {_show(before['grade'])}",
        f"  Final: parcels {_show(final['parcels'])}, outcome {_show(final['outcome'])}, grade {_show(final['grade'])}",
        (
            f"  Derived twin ({card['axis']}={twin['state']}, not a trial): parcels {_show(twin['parcels'])}, "
            f"outcome {_show(twin['outcome'])}, grade {_show(twin['grade'])}"
        ),
    ]
    if not card["events"]:
        lines.insert(3, "    none")
    return lines


def _event_line(comparison: dict[str, Any]) -> str:
    if not comparison["comparable"]:
        return (
            "Accepted events: not compared. One card is a stock world and one an in-flight world. "
            "They vary different supplier axes, so no event position aligns."
        )
    position = comparison["first_different_event"]
    counts = comparison["accepted_events"]
    if position is None:
        if counts["left"] == 0:
            return "Accepted events: match. Neither card has an accepted event."
        return f"Accepted events: match. All {counts['left']} are the same, in the same order."
    left, right = comparison["left_event"], comparison["right_event"]
    if left is None or right is None:
        only = "card 2" if left is None else "card 1"
        prefix = f"the first {position - 1} match; " if position > 1 else ""
        return (
            f"Accepted events: {prefix}event {position} exists only in {only}: "
            f"{_event_text(right if left is None else left)}."
        )
    return (
        f"Accepted events: first difference at event {position}: card 1 {_event_text(left)}; "
        f"card 2 {_event_text(right)}."
    )


def _comparison_text(comparison: dict[str, Any]) -> list[str]:
    state, citation = comparison["state"], comparison["citation"]
    left, right = state["left"], state["right"]
    states = (
        f"State: same ({left['axis']}={left['value']})."
        if state["same"]
        else f"State: differs (card 1 {left['axis']}={left['value']}; card 2 {right['axis']}={right['value']})."
    )
    cites = (
        f"Citation: same ({citation['left']['status']})."
        if citation["same"]
        else f"Citation: differs (card 1 {citation['left']['status']}; card 2 {citation['right']['status']})."
    )
    channel = comparison["channel"]
    channels = (
        "Channel: same."
        if channel["same"]
        else f"Channel: differs (card 1 {channel['left']}; card 2 {channel['right']})."
    )
    return [
        "Comparison:",
        f"  {_event_line(comparison)}",
        f"  {states}",
        f"  {cites}",
        f"  {channels}",
    ]


def text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for number, envelope in enumerate(result["cards"], 1):
        lines.extend(_card_text(number, envelope))
    if result["comparison"] is not None:
        lines.extend(_comparison_text(result["comparison"]))
    lines.extend(f"Limit: {limit}" for limit in result["limits"])
    return "\n".join(lines)


def audit_text(result: dict[str, Any]) -> str:
    return "\n".join(
        (
            f"Twin check: {result['verdict']}",
            (
                f"  {result['pairs']} observed pairs, {result['directions_checked']} directional checks "
                "(not independent samples)."
            ),
            f"  Verified: {result['grade_matches']} grades and {result['ledger_matches']} ledgers.",
            f"  {result['grade_flips']} twin grades change and {result['grade_stable']} stay fixed.",
            f"  {TWIN_LIMIT}",
        )
    )
