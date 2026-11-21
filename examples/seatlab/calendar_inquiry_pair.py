from collections.abc import Callable
from copy import deepcopy
from typing import Any

from .calendar_inquiry_protocol import Source, advance

type Record = dict[str, Any]
type Respond = Callable[[str, Record], Record | None]


def _groups(stage: str, requests: dict[str, Record]) -> tuple[tuple[str, ...], ...]:
    modes = tuple(requests)
    if len(modes) != 2:
        return tuple((mode,) for mode in modes)
    same = requests[modes[0]] == requests[modes[1]]
    if stage == "first" and not same:
        raise ValueError("first worker request differs between branches")
    if stage == "first" or (stage == "book" and same):
        return (modes,)
    return tuple((mode,) for mode in modes)


def play_pair(source: Source, *, first_timing: str, respond: Respond) -> Record:
    if first_timing not in ("immediate", "delayed"):
        raise ValueError("invalid first timing")
    other = "delayed" if first_timing == "immediate" else "immediate"
    order = (first_timing, other)
    histories: dict[str, list[Record]] = {mode: [] for mode in order}
    outcomes: dict[str, Record] = {}
    turns: list[Record] = []
    for stage in ("first", "second", "book"):
        requests = {}
        for mode in order:
            if mode in outcomes:
                continue
            step = advance(source, mode, histories[mode])
            if "status" in step:
                outcomes[mode] = step
            else:
                requests[mode] = step
        for modes in _groups(stage, requests):
            label = "shared" if len(modes) == 2 else modes[0]
            call = "first" if stage == "first" else f"{stage}-{label}"
            request = requests[modes[0]]
            reply = respond(call, deepcopy(request))
            turns.append(
                {
                    "call": call,
                    "timings": list(modes),
                    "request": request,
                    "reply": reply,
                }
            )
            for mode in modes:
                if reply is None:
                    outcomes[mode] = {
                        "status": "missing",
                        "stage": stage,
                        "correct": None,
                    }
                else:
                    histories[mode].append(reply)
    for mode in order:
        if mode not in outcomes:
            outcomes[mode] = advance(source, mode, histories[mode])
            if "status" not in outcomes[mode]:
                raise ValueError("worker requested more than three turns")
    return {"turns": turns, "outcomes": outcomes}
