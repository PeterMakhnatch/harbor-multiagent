#!/usr/bin/env python3
"""A no-model example of the host decision interface, not an assistant or a judge."""

import json
import sys
from typing import Any, cast

REQUEST_SCHEMA = "harbor.joint-reopen.turn/v1"
REPLY_SCHEMA = "harbor.joint-reopen.choice/v1"
PREFIX = "operator/"
POLICIES = ("read-peer", "own-card", "keep", "stop-vote")
ROLES = ("east", "west")
STAGES = ("endorse", "report", "vote")
OPTIONS = ("A", "B", "C", "D", "E", "F")
REQUEST_KEYS = frozenset({"schema", "requested", "instructions", "view"})
VIEW_KEYS = frozenset(
    {"role", "turn", "stage", "incumbent", "options", "card", "inbox", "choices"}
)
INBOX_KEYS = frozenset({"delivered", "slots"})
MAX_TURNS = 5
MAX_CHOICES = 44
MAX_REPORT = 3
LIMIT = 16 * 1024


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record = dict(pairs)
    if len(record) != len(pairs):
        raise ValueError("duplicate key")
    return record


def _constant(name: str) -> None:
    raise ValueError("non-finite number")


def _slots(value: object, high: int) -> list[str]:
    if type(value) is not list or len(value) > high:
        raise ValueError("slots are not a bounded list")
    if not all(type(item) is str and item in OPTIONS for item in value):
        raise ValueError("slots are not options")
    if value != sorted(set(value)):
        raise ValueError("slots are not sorted and distinct")
    return cast(list[str], value)


def _check(raw: object) -> dict[str, Any]:
    if type(raw) is not dict or set(raw) != VIEW_KEYS:
        raise ValueError("view fields differ")
    view = cast(dict[str, Any], raw)
    if view["role"] not in ROLES or view["stage"] not in STAGES:
        raise ValueError("view role or stage is unknown")
    if type(view["turn"]) is not int or not 1 <= view["turn"] <= MAX_TURNS:
        raise ValueError("view turn is out of range")
    if view["options"] != list(OPTIONS) or view["incumbent"] not in OPTIONS:
        raise ValueError("view options are not public slots")
    if view["card"] is not None:
        _slots(view["card"], MAX_REPORT)
    inbox = view["inbox"]
    if inbox is not None:
        if type(inbox) is not dict or set(inbox) != INBOX_KEYS:
            raise ValueError("view inbox fields differ")
        shown = cast(dict[str, Any], inbox)
        if shown["delivered"] is True:
            _slots(shown["slots"], MAX_REPORT)
        elif shown["delivered"] is not False or shown["slots"] is not None:
            raise ValueError("view inbox is malformed")
    choices = view["choices"]
    if type(choices) is not list or not 1 < len(choices) <= MAX_CHOICES:
        raise ValueError("view choices are not a bounded list")
    if not all(type(choice) is str for choice in choices):
        raise ValueError("view choices are not text")
    return view


def _report(card: list[str] | None) -> str:
    if card is None:
        return "card"
    return "report:" + ("".join(card) or "-")


def _ballot(incumbent: str, card: list[str], inbox: dict[str, Any] | None) -> str:
    if inbox is not None and inbox["delivered"] and inbox["slots"]:
        common = sorted(set(card) & set(inbox["slots"]))
        if incumbent in common:
            return f"vote:{incumbent}"
        others = [slot for slot in common if slot != incumbent]
        if others:
            return f"vote:{others[0]}"
    if incumbent in card:
        return f"vote:{incumbent}"
    others = [slot for slot in card if slot != incumbent]
    if not others:
        raise ValueError("card offers no slot")
    return f"vote:{others[0]}"


def decide(label: str, view: dict[str, Any]) -> str:
    if label not in POLICIES:
        raise ValueError("unsupported scripted participant")
    stage, incumbent, card = view["stage"], view["incumbent"], view["card"]
    if stage == "endorse":
        return f"endorse:{incumbent}"
    if stage == "report":
        return "report:-" if label == "keep" else _report(card)
    if label == "keep":
        return f"vote:{incumbent}"
    if label == "stop-vote":
        return "stop"
    if card is None:
        raise ValueError("vote stage without the own card")
    if label == "read-peer" and view["inbox"] is None:
        return "inbox"
    return _ballot(incumbent, card, view["inbox"] if label == "read-peer" else None)


def main() -> int:
    data = sys.stdin.buffer.read(LIMIT + 1)
    if not data or len(data) > LIMIT:
        return 2
    try:
        request = json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant
        )
        if type(request) is not dict or set(request) != REQUEST_KEYS:
            return 2
        if request["schema"] != REQUEST_SCHEMA:
            return 2
        name = request["requested"]
        if type(name) is not str or not name.startswith(PREFIX):
            return 2
        view = _check(request["view"])
        choice = decide(name.removeprefix(PREFIX), view)
        if choice not in view["choices"]:
            return 2
    except (UnicodeDecodeError, TypeError, ValueError, KeyError, RecursionError):
        return 2
    print(
        json.dumps(
            {
                "schema": REPLY_SCHEMA,
                "requested": name,
                "choice": choice,
                "served_model": None,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
