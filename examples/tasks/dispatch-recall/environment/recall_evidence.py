from __future__ import annotations

from typing import Any

CUT_SCHEMA = "harbor.dispatch-recall.cut/v1"
SCHEMA = "harbor.dispatch-recall.diagnosis/v1"
CUT_KEYS = frozenset(
    {
        "schema",
        "seed",
        "transition",
        "timing",
        "bays",
        "initial_bay",
        "current_bay",
        "actions",
        "windows",
        "reward",
    }
)
TRANSITIONS = ("stay", "move")
TIMINGS = ("immediate", "deferred", "withheld")
TURNS = ("stage", "report", "commit", "close", "done")
ORDER = (
    ("loader", "stage"),
    ("depot", "report"),
    ("loader", "commit"),
    ("loader", "close"),
)
WINDOWS = (("commit", "precommit", 2), ("close", "postcommit", 3))
ELIGIBLE = {"commit": ("immediate",), "close": ("immediate", "deferred")}
BLANK = "--"
BAY_WIDTH = 2
BAY_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
SEED_LIMIT = 2**63
CAVEAT = (
    "Accepted physical actions and server-prepared window replies only. A requested "
    "window means the server prepared the loader reply, not that it was received or "
    "read; an accepted commit or close always redeems its window. No attention, belief "
    "or causal claim. Missing means absent from this cut, which may be a prefix."
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _choice(value: object, choices: tuple[str, ...], name: str) -> str:
    require(type(value) is str and value in choices, f"{name} is not a legal value")
    return str(value)


def _bay(value: object) -> str:
    require(
        type(value) is str
        and len(value) == BAY_WIDTH
        and all(char in BAY_ALPHABET for char in value),
        "bay names must be 2 uppercase ASCII letters or digits",
    )
    return str(value)


def _layout(cut: object) -> tuple[tuple[str, str], str, str]:
    if type(cut) is not dict:
        raise ValueError("cut must be a dict")
    require(set(cut) == CUT_KEYS, "cut keys differ from the cut schema")
    require(cut["schema"] == CUT_SCHEMA, "cut schema differs")
    seed = cut["seed"]
    require(
        type(seed) is int and 0 <= seed < SEED_LIMIT,
        "seed must be an int in [0, 2**63)",
    )
    bays = cut["bays"]
    if type(bays) is not list or len(bays) != 2:
        raise ValueError("bays must be a list of 2 names")
    low, high = _bay(bays[0]), _bay(bays[1])
    require(low < high, "bays must be distinct and sorted")
    pair = (low, high)
    initial = _choice(cut["initial_bay"], pair, "initial bay")
    current = _choice(cut["current_bay"], pair, "current bay")
    return pair, initial, current


def _actions(raw: object, bays: tuple[str, str]) -> tuple[str | None, ...]:
    if type(raw) is not list:
        raise ValueError("actions must be a list")
    require(len(raw) <= len(ORDER), "more accepted actions than turns")
    values: list[str | None] = []
    for index, action in enumerate(raw):
        role, op = ORDER[index]
        if type(action) is not dict:
            raise ValueError(f"action {index} must be a dict")
        require(
            action.get("role") == role and action.get("op") == op,
            f"action {index} must be the {role} {op}; turns are out of order or repeated",
        )
        keys = {"role", "op"} if op == "close" else {"role", "op", "bay"}
        require(set(action) == keys, f"action {index} keys differ for {op}")
        if op == "close" or (op == "report" and action["bay"] is None):
            values.append(None)
        else:
            values.append(_choice(action["bay"], bays, f"{op} bay"))
    return tuple(values)


def _notice(window: str, timing: str, report: str | None) -> str:
    if report is None or timing not in ELIGIBLE[window]:
        return BLANK
    return report


def _windows(
    raw: object, timing: str, values: tuple[str | None, ...]
) -> dict[str, Any]:
    if type(raw) is not dict:
        raise ValueError("windows must be a dict")
    require(set(raw) == {"commit", "close"}, "windows must be exactly commit and close")
    accepted = len(values)
    for name, _, opens_after in WINDOWS:
        window = raw[name]
        if window is None:
            require(accepted <= opens_after, f"{name} accepted without its window")
            continue
        require(type(window) is dict, f"{name} window must be a dict or None")
        require(set(window) == {"notice", "redeemed"}, f"{name} window keys differ")
        require(accepted >= opens_after, f"{name} window opened before its turn")
        require(type(window["redeemed"]) is bool, f"{name} redeemed must be a bool")
        require(
            window["redeemed"] == (accepted > opens_after),
            f"{name} redemption contradicts the accepted actions",
        )
        require(
            window["notice"] == _notice(name, timing, values[1]),
            f"{name} notice contradicts the report and timing",
        )
    return raw


def _physical(
    cut: dict[str, Any],
    initial: str,
    current: str,
    values: tuple[str | None, ...],
) -> None:
    staged_move = len(values) >= 1 and cut["transition"] == "move"
    require(
        (current != initial) == staged_move,
        "current bay contradicts the transition and staging",
    )
    reward = cut["reward"]
    if len(values) < 3:
        require(reward is None, "reward exists before a commit")
        return
    require(type(reward) is int and reward in (0, 1), "reward must be int 0 or 1")
    require(reward == int(values[2] == current), "reward contradicts committed bay")


def _place(bay: str | None, initial: str) -> str | None:
    if bay is None:
        return None
    return "initial" if bay == initial else "other"


def _sender(
    values: tuple[str | None, ...], initial: str, current: str
) -> dict[str, Any]:
    if len(values) < 2:
        action, filed, correctness = "pending", None, "no_value"
    elif values[1] is None:
        action, filed, correctness = "silence", None, "no_value"
    else:
        action, filed = "report", values[1]
        correctness = "correct" if filed == current else "incorrect"
    return {
        "opportunity": "available" if values else "not_reached",
        "action": action,
        "value": _place(filed, initial),
        "correctness": correctness,
    }


def _delivery(
    windows: dict[str, Any], timing: str, values: tuple[str | None, ...], current: str
) -> dict[str, Any]:
    delivery: dict[str, Any] = {}
    for name, label, opens_after in WINDOWS:
        window = windows[name]
        content: str | None = None
        shows_actual: bool | None = None
        if window is None:
            state = "not_reached" if len(values) < opens_after else "unrequested"
        else:
            state = "redeemed" if window["redeemed"] else "requested"
            if window["notice"] != BLANK:
                content = "report"
                shows_actual = window["notice"] == current
            elif values[1] is None:
                content = "blank_after_silence"
            else:
                content = "blank_by_timing"
        delivery[label] = {
            "eligible": timing in ELIGIBLE[name],
            "window": state,
            "content": content,
            "shows_actual": shows_actual,
        }
    return delivery


def _loader(
    cut: dict[str, Any],
    values: tuple[str | None, ...],
    initial: str,
    current: str,
) -> dict[str, Any]:
    planned = values[0] if values else None
    committed = values[2] if len(values) >= 3 else None
    notice = (cut["windows"]["commit"] or {}).get("notice", BLANK)
    if committed is None:
        plan, outcome, agrees = "pending", "missing", None
    else:
        plan = "kept" if committed == planned else "changed"
        if committed == current:
            outcome = "correct"
        else:
            outcome = "stale" if cut["transition"] == "move" else "switched"
        agrees = None if notice == BLANK else committed == notice
    return {
        "planned": _place(planned, initial),
        "committed": _place(committed, initial),
        "plan": plan,
        "outcome": outcome,
        "agrees_with_precommit_notice": agrees,
        "closed": len(values) == len(ORDER),
    }


def diagnose(cut: dict[str, Any]) -> dict[str, Any]:
    bays, initial, current = _layout(cut)
    timing = _choice(cut["timing"], TIMINGS, "timing")
    _choice(cut["transition"], TRANSITIONS, "transition")
    values = _actions(cut["actions"], bays)
    windows = _windows(cut["windows"], timing, values)
    _physical(cut, initial, current, values)
    open_window = next(
        (
            name
            for name, _, opens_after in WINDOWS
            if windows[name] is not None and len(values) == opens_after
        ),
        None,
    )
    return {
        "schema": SCHEMA,
        "condition": {"transition": cut["transition"], "timing": timing},
        "progress": {
            "accepted": len(values),
            "turn": TURNS[len(values)],
            "open_window": open_window,
        },
        "sender": _sender(values, initial, current),
        "delivery": _delivery(windows, timing, values, current),
        "loader": _loader(cut, values, initial, current),
        "reward": cut["reward"],
        "caveat": CAVEAT,
    }
