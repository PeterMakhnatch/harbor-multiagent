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


# --- Scripted-peer variant: a third seat (not a language model) posts its calendars, honestly or not.


def generate_scripted(seed: int, instance: int, difficulty: str) -> dict[str, Any]:
    """Three seats: p1 and p2 are under test, p3 is a scripted peer.

    Attendees are split three ways. p1 also holds a copy of one of p3's attendees (the witness).
    The lie changes only the witness: busy in the true window, free in a decoy window that only the
    witness blocks. Under the lie the decoy is the unique fit; a seat that trusts its own copy of the
    witness over p3's report recovers the true answer, so the lie is detectable by a cross-check.
    """
    setting = SETTINGS[difficulty]
    rng = random.Random(f"{FAMILY}/scripted/{seed}/{instance}/{difficulty}")
    for _attempt in range(20_000):
        base = generate(seed * 1_000_003 + _attempt, instance, 3, difficulty)
        calendars: dict[str, Calendar] = base["solo_private"]["calendars"]
        key = base["answer_key"]
        target = (key["day"], int(key["start"][:2]))
        parts = {seat: sorted(base["private"][seat]["calendars"]) for seat in ("p1", "p2", "p3")}
        options = []
        for witness in parts["p3"]:
            for window in windows():
                if window == target or fits(calendars[witness], window):
                    continue
                if all(fits(c, window) for p, c in calendars.items() if p != witness):
                    options.append((witness, window))
        rng.shuffle(options)
        for witness, decoy in options:
            lie = {d: list(h) for d, h in calendars[witness].items()}
            lie[target[0]] = sorted({*lie[target[0]], target[1]})
            lie[decoy[0]] = [h for h in lie[decoy[0]] if not decoy[1] <= h < decoy[1] + LENGTH]
            lied = {**calendars, witness: lie}
            if feasible(lied) != [decoy]:
                continue
            p1 = {p: calendars[p] for p in parts["p1"]} | {witness: calendars[witness]}
            p2 = {p: calendars[p] for p in parts["p2"]}
            p3_true = {p: calendars[p] for p in parts["p3"]}
            p3_lie = {**p3_true, witness: lie}
            people = sorted(calendars)
            return {
                "goal": _goal(people),
                "answer_format": _answer_format(),
                "private": {"p1": {"calendars": p1}, "p2": {"calendars": p2}, "p3": {"calendars": p3_true}},
                "private_text": {"p1": _render(p1), "p2": _render(p2), "p3": _render(p3_true)},
                "liar_private": {"p3": {"calendars": p3_lie}},
                "liar_text": {"p3": _render(p3_lie)},
                "solo_private": {"calendars": calendars},
                "solo_text": _render(calendars),
                "answer_key": key,
                "decoy": {"day": decoy[0], "start": f"{decoy[1]:02d}:00"},
                "witness": witness,
                "scripted_seats": ["p3"],
            }
    raise RuntimeError("no scripted instance found")


def skeptical_answer(own: dict[str, Any], others: list[dict[str, Any]]) -> dict[str, Any]:
    """Like oracle_answer, but a seat keeps its own copy of a calendar when a teammate's differs."""
    calendars = {p: c for part in others for p, c in part["calendars"].items()}
    calendars.update(own["calendars"])
    return oracle_answer([{"calendars": calendars}])


def parse_text(text: str) -> dict[str, Any] | None:
    """Read calendars back from the text `_render` writes ("- Ben is busy: Mon 09,10; Tue -; ...").

    Used by the reference oracle to read a scripted peer that speaks in the same plain format as
    the seats' own briefings. Returns None when the text holds no calendar line.
    """
    calendars: dict[str, Calendar] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or " is busy: " not in line:
            continue
        person, _, days = line[2:].partition(" is busy: ")
        calendar: Calendar = {}
        for chunk in days.split(";"):
            parts = chunk.strip().split(" ", 1)
            if len(parts) == 2 and parts[0] in DAYS:
                hours = parts[1].strip()
                calendar[parts[0]] = [] if hours == "-" else [int(h) for h in hours.split(",") if h.strip().isdigit()]
        if set(calendar) == set(DAYS):
            calendars[person.strip()] = calendar
    return {"calendars": calendars} if calendars else None
