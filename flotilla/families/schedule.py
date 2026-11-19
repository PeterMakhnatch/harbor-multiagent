"""Family `schedule`: find the one 2-hour window that fits every attendee's calendar.

Hidden profile: each seat knows the calendars of only some attendees. Exactly one window fits all
attendees; with one seat's calendars alone, several windows fit (at least MIN_CANDIDATES), so a
seat that cannot talk must guess. Standard library only; deterministic for a seed.
"""

from __future__ import annotations

import random
from typing import Any

FAMILY = "schedule"
TITLE = "Schedule one meeting"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
HOURS = list(range(9, 17))  # a slot is the hour starting at h; the last slot is 16:00-17:00
LENGTH = 2  # the meeting needs 2 consecutive slots
NAMES = ["Ana", "Ben", "Chen", "Dara", "Eli", "Fay", "Gus", "Hana", "Ivo", "Jun", "Kai", "Lea"]
SETTINGS = {"easy": {"people": 6, "busy": 0.30, "min_candidates": 4},
            "hard": {"people": 8, "busy": 0.25, "min_candidates": 6}}

Window = tuple[str, int]  # (day, start hour)
Calendar = dict[str, list[int]]  # day -> busy hours


def windows() -> list[Window]:
    return [(day, h) for day in DAYS for h in HOURS[: len(HOURS) - LENGTH + 1]]


def fits(calendar: Calendar, window: Window) -> bool:
    day, start = window
    return all(h not in calendar.get(day, []) for h in range(start, start + LENGTH))


def feasible(calendars: dict[str, Calendar]) -> list[Window]:
    return [w for w in windows() if all(fits(c, w) for c in calendars.values())]


def _split(people: list[str], n_seats: int) -> dict[str, list[str]]:
    seats = [f"p{i + 1}" for i in range(n_seats)]
    return {seat: people[i::n_seats] for i, seat in enumerate(seats)}


def generate(seed: int, instance: int, n_seats: int, difficulty: str) -> dict[str, Any]:
    setting = SETTINGS[difficulty]
    rng = random.Random(f"{FAMILY}/{seed}/{instance}/{n_seats}/{difficulty}")
    for _attempt in range(10_000):
        people = rng.sample(NAMES, setting["people"])
        target = rng.choice(windows())
        calendars: dict[str, Calendar] = {
            p: {d: sorted(h for h in HOURS if rng.random() < setting["busy"]) for d in DAYS} for p in people
        }
        day, start = target
        for c in calendars.values():  # everyone is free in the target window
            c[day] = [h for h in c[day] if not start <= h < start + LENGTH]
        for w in windows():  # every other window is blocked by someone
            if w != target and all(fits(c, w) for c in calendars.values()):
                person = rng.choice(people)
                hour = w[1] + rng.randrange(LENGTH)
                if not (w[0] == day and start <= hour < start + LENGTH):
                    calendars[person][w[0]] = sorted({*calendars[person][w[0]], hour})
        if feasible(calendars) != [target]:
            continue
        split = _split(people, n_seats)
        parts = {seat: {p: calendars[p] for p in names} for seat, names in split.items()}
        if n_seats > 1 and min(len(feasible(part)) for part in parts.values()) < setting["min_candidates"]:
            continue
        return {
            "goal": _goal(people),
            "answer_format": _answer_format(),
            "private": {seat: {"calendars": part} for seat, part in parts.items()},
            "private_text": {seat: _render(part) for seat, part in parts.items()},
            "solo_private": {"calendars": calendars},
            "solo_text": _render(calendars),
            "answer_key": {"day": target[0], "start": f"{target[1]:02d}:00"},
        }
    raise RuntimeError("no instance found; relax the settings")


def _goal(people: list[str]) -> str:
    return (
        f"Book one {LENGTH}-hour meeting that all {len(people)} attendees ({', '.join(sorted(people))}) can attend. "
        f"Meetings start on the hour between 09:00 and {HOURS[-LENGTH]:02d}:00, Monday to Friday, last "
        f"{LENGTH} consecutive hours and end by {HOURS[-1] + 1}:00. A calendar lists the hours when a person is "
        "busy; a busy hour `13` means "
        "13:00-14:00. Exactly one window in the week fits every attendee."
    )


def _answer_format() -> str:
    return 'A JSON object: `{"day": "Wed", "start": "14:00"}` (day is one of Mon, Tue, Wed, Thu, Fri).'


def _render(calendars: dict[str, Calendar]) -> str:
    lines = []
    for person in sorted(calendars):
        days = "; ".join(f"{d} {','.join(f'{h:02d}' for h in calendars[person][d]) or '-'}" for d in DAYS)
        lines.append(f"- {person} is busy: {days}")
    return "Calendars you know (busy hours):\n" + "\n".join(lines)


def oracle_answer(private: list[dict[str, Any]]) -> dict[str, Any]:
    calendars: dict[str, Calendar] = {}
    for part in private:
        calendars.update(part["calendars"])
    candidates = feasible(calendars)
    if not candidates:
        return {}
    day, start = candidates[0]
    return {"day": day, "start": f"{start:02d}:00"}


def _norm_start(value: Any) -> str | None:
    if isinstance(value, int):
        return f"{value:02d}:00"
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return f"{int(text):02d}:00"
        hour = text.split(":", 1)[0]
        if hour.isdigit():
            return f"{int(hour):02d}:00"
    return None


def score(answer: dict[str, Any] | None, key: dict[str, Any]) -> dict[str, Any]:
    correct = bool(
        isinstance(answer, dict)
        and isinstance(answer.get("day"), str)
        and answer["day"].strip()[:3].title() == key["day"]
        and _norm_start(answer.get("start")) == key["start"]
    )
    return {"score": 1.0 if correct else 0.0, "fields_correct": int(correct), "fields_total": 1}
