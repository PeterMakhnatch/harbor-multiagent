"""Family `schedule`: find the one 2-hour window that fits every attendee's calendar.

Hidden profile: each seat knows the calendars of only some attendees. Exactly one window fits all
attendees; with one seat's calendars alone, several windows fit (at least MIN_CANDIDATES), so a
seat that cannot talk must guess. Standard library only; deterministic for a seed.
"""

from __future__ import annotations

import random
import re
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
            "answer_format": _answer_format(target),
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


def _answer_format(*avoid: Window) -> str:
    """The example window is never the instance's answer (or its decoy): an example that equals the
    answer gives it away, for example to a seat that cannot talk."""
    day, start = next(w for w in (("Wed", 14), ("Tue", 10), ("Mon", 11)) if w not in avoid)
    return f'A JSON object: `{{"day": "{day}", "start": "{start:02d}:00"}}` (day is one of Mon, Tue, Wed, Thu, Fri).'


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
    """"14", 14, "14:00" and "14:00:00" mean 14:00; "14:30" is a different time, so it is not 14:00."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"{value:02d}:00"
    if isinstance(value, str):
        match = re.fullmatch(r"(\d{1,2})(?::00(?::00)?)?", value.strip(), flags=re.ASCII)
        if match:
            return f"{int(match.group(1)):02d}:00"
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
                "answer_format": _answer_format(target, decoy),
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


# --- Analysis hooks: flotilla-analyze reads message texts with these (CONTRACT.md 7). They do not
# change scoring.

_DAY = r"\b(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?)(?![a-z])"
_HOUR = r"(\d{1,2})(?!\d)(?::(\d{2}))?(?:\s*([ap])\.?m\b\.?)?"
# A weekday and the hour right after it, in the forms seats write: "Mon 09", "Mon09", "Mon:09",
# "Mon(09", "Mon[9", '"Mon": [9', "Monday 9am", "Thursday at 14:00". Only the first hour after the day
# is read, so a busy list "Mon 09,10" is one mention (Mon, 9).
_DAY_HOUR = re.compile(_DAY + r"\.?[\s\"':=(\[{]*(?:at\s+|@\s*)?" + _HOUR, re.IGNORECASE)
# The compact form of a narrow channel: a capital one- or two-letter day right before a working hour
# ("M9,15 T12 Th10 R15 F10"; R is Thursday). Case matters, so message ids such as "m1" do not match.
_SHORT_DAY_HOUR = re.compile(r"(?<![A-Za-z])(Mo|Tu|We|Th|Fr|M|T|W|R|F)(9|1[0-6])(?!\d)")
_SHORT_DAYS = {"Mo": "Mon", "M": "Mon", "Tu": "Tue", "T": "Tue", "We": "Wed", "W": "Wed", "Th": "Thu",
               "R": "Thu", "Fr": "Fri", "F": "Fri"}
# A written-out answer object in either key order: {"day": "Thu", "start": "14:00"}, day=Thu start=14.
_DAY_START = re.compile(r"\bday\b\W{0,8}" + _DAY + r"\W{0,12}\bstart\b\W{0,8}" + _HOUR, re.IGNORECASE)
_START_DAY = re.compile(r"\bstart\b\W{0,8}" + _HOUR + r"\W{0,12}\bday\b\W{0,8}" + _DAY, re.IGNORECASE)
# A format example inside a request: a parenthesis that starts with an example marker
# ("(e.g., Mon 09,10)") or a quotation right after one ('like "Ben: Mon 9,10"', 'format: "..."').
_MARKER = r"(?:\be\.\s?g\.|\beg\b|\bfor (?:example|instance)\b|\bsuch as\b|\blike\b|\bformat\b)"
_EXAMPLE = re.compile(
    r"\(\s*" + _MARKER + r"[^)]*\)|" + _MARKER + r"\W{0,3}(?:\"[^\"\n]*\"|'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’)",
    re.IGNORECASE,
)


def _clock(hour: str, minutes: str | None, half: str | None) -> int | None:
    """The whole hour a mention names (24-hour clock), or None when it is not a whole hour."""
    value = int(hour)
    if half:
        if not 1 <= value <= 12:
            return None
        value = value % 12 + (12 if half.lower() == "p" else 0)
    if minutes not in (None, "00") or value > 23:
        return None
    return value


def _found(matches: list[tuple[int, str, str, str | None, str | None]]) -> list[tuple[int, Window]]:
    """(position, (day, hour)) of (position, day, hour, minutes, am/pm) matches that name a whole hour."""
    found = []
    for position, day, hour, minutes, half in matches:
        clock = _clock(hour, minutes, half)
        if clock is not None:
            found.append((position, (day[:3].title(), clock)))
    return found


def _day_hour_matches(text: str) -> list[tuple[int, str, str, str | None, str | None]]:
    found = [(m.start(), m[1], m[2], m[3], m[4]) for m in _DAY_HOUR.finditer(text)]
    return found + [(m.start(), _SHORT_DAYS[m[1]], m[2], None, None) for m in _SHORT_DAY_HOUR.finditer(text)]


def without_examples(text: str) -> str:
    """The text with its format examples ("(e.g. Mon 09,10)", 'like "Ben: Mon 9"') blanked out."""
    return _EXAMPLE.sub(" ", text)


def day_hours(text: str) -> list[Window]:
    """(day, whole hour) of every weekday-and-hour mention in the text, in order."""
    return [window for _, window in sorted(_found(_day_hour_matches(text)))]


def mentions(text: str) -> list[Window]:
    """Every (day, whole hour) that the text names: weekday-and-hour mentions and written-out answer
    objects, in the order they appear."""
    found = _found(_day_hour_matches(text))
    found += _found([(m.start(), m[1], m[2], m[3], m[4]) for m in _DAY_START.finditer(text)])
    found += _found([(m.start(), m[4], m[1], m[2], m[3]) for m in _START_DAY.finditer(text)])
    return [window for _, window in sorted(found)]


def carries_own_data(text: str, private: dict[str, Any]) -> bool:
    """Whether a message carries its sender's own calendar information, raw or derived.

    ``private`` is the sender's briefing. True when the text, format examples left out, names at least
    one attendee whose calendar the sender holds and also names a weekday with an hour
    ("Ana: Mon 09,10", "Ana:M9,15 T12", "free for Ana, Ben: Tue 13:00"). A bare day-and-hour test also
    matches format examples inside requests: a request names the receiver's attendees ("Please send
    Ben's hours: Mon 09,10?") or none, and an example in parentheses or quotes after "e.g.", "like" or
    "format" is left out, so neither counts. A written-out answer object ('{"day": "Wed", "start":
    "14:00"}') is not data. Known limits: data written with initials ("A,C,K free: T11") or without any
    attendee name ("my free slots: Mon 11") does not count; a request that names the sender's own
    attendees next to an unmarked example ("I have Ana; send yours as Mon 09,10") counts.
    """
    calendars = private.get("calendars") if isinstance(private, dict) else None
    text = without_examples(text)
    if not isinstance(calendars, dict) or not day_hours(text):
        return False
    return any(re.search(rf"\b{re.escape(str(name))}\b", text, re.IGNORECASE) for name in calendars)


def answer_in_text(text: str, key: dict[str, Any]) -> bool:
    """Whether a message names the answer ``key``: a weekday-and-hour mention or a written-out answer
    object, format examples left out, that ``score`` gives full marks ("Thursday 14:00-16:00",
    "Thu 2pm", 'SUBMITTED: {"day": "Thu", "start": "14:00"}'). A busy list "Thu 13,14" names only
    (Thu, 13)."""
    return any(score({"day": day, "start": f"{hour:02d}:00"}, key)["score"] == 1.0
               for day, hour in mentions(without_examples(text)))
