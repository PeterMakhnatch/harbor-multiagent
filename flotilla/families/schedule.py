"""Family `schedule`: find the one 2-hour window that fits every attendee's calendar.

Hidden profile: each seat knows the calendars of only some attendees. Exactly one window fits all
attendees; with one seat's calendars alone, several windows fit (at least MIN_CANDIDATES), so a
seat that cannot talk must guess. Standard library only; deterministic for a seed.
"""

from __future__ import annotations

import random
import re
from collections import Counter
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
    if difficulty not in SETTINGS:
        raise ValueError(f"unknown difficulty {difficulty!r}; expected one of {sorted(SETTINGS)}")
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
# A format hint that runs to the end of its line, or of the parenthesis it stands in: "Use format: Name:
# Mon 10,11; Tue 14", 'Example: {"Ben": ["Mon 14"]}', "(hours are whole numbers, e.g., Mon 9,12)".
_HINT = re.compile(r"\be\.\s?g\.|\beg\b|\bexamples?\b|\bfor instance\b|\bsuch as\b|\bformat\b|\blike\s*:",
                   re.IGNORECASE)
# A positional week: five hour lists, Monday to Friday, split by slashes ("15/9,13/10,14/13,16/9,11").
_WEEK = re.compile(r"(?<![\w/,\-])((?:\d[\d,\-]*)?(?:/(?:\d[\d,\-]*)?){4})(?![\w/])")
_GROUP = re.compile(r"(?<![A-Za-z])[A-Za-z](?:\s*[,/+&]\s*[A-Za-z]){1,11}(?![A-Za-z])")  # "A,C,K", "g+h+k"
_LETTER = re.compile(r"(?<![A-Za-z])[A-Z](?![a-z])")
_INITIALS = {name[0] for name in NAMES}
# Another speaker: a seat or "you", whose claims a message relays ("p1: Tue 10", "you said Wed 09"); not a
# seat that names itself ("I am p2", "p2 here").
_SPEAKER = re.compile(r"(?<![A-Za-z0-9])(?<!I am )(?<!I'm )(?<!is )[pP]\d(?!\d)(?! here)(?!\s*,)|\byou(?:r)?\b",
                      re.IGNORECASE)  # "p1, Mon 10 works" addresses p1: not a claim
# Words that may stand between an attendee or speaker and the hours that belong to it ("Ben is busy: Mon
# 09", "p1 said Wed 09").
_FILLER = {"is", "are", "was", "busy", "free", "hours", "hour", "hrs", "and", "or", "s", "at", "on", "none",
           "all", "day", "days", "says", "said", "say", "sent", "gave", "give", "gives", "has", "had", "have",
           "reported", "listed", "shared", "claims", "claimed", "claim", "thinks", "thought", "only"}
# A window offered as a candidate: a cue word in its sentence ("Candidates: Tue 09:00", "Thu11 and Thu13 work
# for me", "free", "available", "possible").
_CUE = re.compile(r"\b(?:candidates?|options?|works?|working|fits?|free|available|avail|possible|possibly|open)\b",
                  re.IGNORECASE)
# What may stand between the attendees of one list ("Chen, Hana, Eli, Ivo: Wed 09", "Ana and Gus").
_LIST = re.compile(r"\s*(?:[,/+&]|\band\b|\bor\b)(?:\s*(?:and|or)\b)?\s*", re.IGNORECASE)


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


def without_hints(text: str) -> str:
    """``without_examples``, and also every format hint: from a hint word ("format", "e.g.", "example",
    "such as", "like:") to the end of its line, or to the end of the parenthesis it stands in."""
    lines, carry = [], False
    for line in text.split("\n"):
        if carry:  # the example of a hint that ended its line ("in the format:\n- Name is busy: Mon 9,10")
            lines.append(" " * len(line))
            carry = False
            continue
        while (hint := _HINT.search(line)) is not None:
            opened = line.rfind("(", 0, hint.start())
            inside = opened != -1 and line.find(")", opened, hint.start()) == -1
            end = line.find(")", hint.end()) if inside else -1
            end = len(line) if end == -1 else end
            carry = not inside and line[hint.end() :].strip() in (":", "")
            line = line[: hint.start()] + " " * (end - hint.start()) + line[end:]
        lines.append(line)
    return without_examples("\n".join(lines))


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


def _data_mentions(text: str) -> list[tuple[int, int, Window]]:
    """(start, end, (day, hour)) of every day-and-hour mention that calendar data can hold: weekday-and-hour
    mentions, compact days ("M9", also glued to an initial: "AM9"), and positional weeks (one mention for
    each day that has hours)."""
    found = []
    for m in _DAY_HOUR.finditer(text):
        if (clock := _clock(m[2], m[3], m[4])) is not None:
            found.append((m.start(), m.end(), (m[1][:3].title(), clock)))
    for m in re.finditer(r"(?<![a-z])(?=(Mo|Tu|We|Th|Fr|M|T|W|R|F)(9|1[0-6])(?!\d))", text):
        before = text[m.start() - 1] if m.start() else ""
        glued = before in _INITIALS and (m.start() == 1 or not text[m.start() - 2].isalpha())  # "AM9"
        if not before.isalpha() or glued:
            found.append((m.start(), m.start() + len(m[1]) + len(m[2]), (_SHORT_DAYS[m[1]], int(m[2]))))
    for m in _WEEK.finditer(text):
        groups = m[1].split("/")
        numbers = [int(n) for n in re.findall(r"\d+", m[1])]
        if len(numbers) < 3 or max(numbers) > 23:
            continue
        offset = m.start()
        for day, group in zip(DAYS, groups, strict=True):
            if (first := re.match(r"\d+", group)) is not None:
                found.append((offset, offset + len(group), (day, int(first[0]))))
            offset += len(group) + 1
    return sorted(set(found))


def _references(text: str, own: set[str], mentions: list[tuple[int, int, Window]]) -> list[tuple[int, int, str]]:
    """(start, end, whose) of every attendee reference: a name of the family's pool, an initial right before
    calendar data ("B:M11", "B M12", "AM9", "G:15/9,13/..."), or a group of initials ("A,C,K", "D/F/L").
    ``whose`` is "own" (the sender holds the attendee, or every attendee of a group), "other" (none of
    them) or "mixed"."""
    starts = {start for start, _, _ in mentions}
    found = []
    for name in sorted(set(NAMES) | own):
        for m in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            found.append((m.start(), m.end(), "own" if name in own else "other"))
    held = Counter(name[0].upper() for name in own)  # an initial is the sender's when one of its attendees has it

    def whose(letters: list[str]) -> str:
        mine = {held[c] == 1 for c in letters}
        if any(held[c] > 1 for c in letters) or len(mine) > 1:
            return "mixed"
        return "own" if mine == {True} else "other"

    for m in _LETTER.finditer(text):
        after = re.match(r"[\s:/=]{0,3}", text[m.end():])
        if m[0] == "I" and after is not None and not after[0].strip():
            continue  # the pronoun ("I M9 free"), unless a separator follows: "I:M14"
        if m[0] in _INITIALS and after is not None and m.end() + len(after[0]) in starts:
            found.append((m.start(), m.end(), whose([m[0]])))
    for m in _GROUP.finditer(text):
        letters = [c.upper() for c in m[0] if c.isalpha()]
        if len(set(letters)) == len(letters) and set(letters) <= _INITIALS:
            found.append((m.start(), m.end(), whose(letters)))
    return sorted(found)


def _lists(text: str, references: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """References with every list of attendees joined into one ("Chen, Hana, Eli, Ivo: Wed 09" belongs to
    all four: "mixed" when they are not all the sender's or all not), and the other speakers added."""
    merged: list[tuple[int, int, str]] = []
    speakers = [(m.start(), m.end(), "other") for m in _SPEAKER.finditer(text)]
    for start, end, kind in sorted(references + speakers):
        if merged and start < merged[-1][1]:  # inside the previous reference (an initial inside a group)
            continue
        if merged and _LIST.fullmatch(text[merged[-1][1] : start]):
            first, _, previous = merged.pop()
            kind, start = (previous if previous == kind else "mixed"), first
        merged.append((start, end, kind))
    return merged


def _attached(text: str, reference: tuple[int, int, str], mention: tuple[int, int, Window],
              mentions: list[tuple[int, int, Window]]) -> bool:
    """Whether a mention belongs to the reference before it: between them stand only other mentions,
    day words, hours, punctuation and filler words ("Ben is busy: Mon 15; Tue -; Wed 10"), and no
    sentence ends."""
    gap = list(text[reference[1] : mention[0]])
    for start, end, _ in mentions:
        for i in range(max(start, reference[1]), min(end, mention[0])):
            gap[i - reference[1]] = " "
    between = "".join(gap)
    if re.search(r"[?!]|\.(?:\s|$)", between):
        return False
    between = re.sub(_DAY, " ", between, flags=re.IGNORECASE)
    between = re.sub(r"(?<![A-Za-z])(?:Mo|Tu|We|Th|Fr|M|T|W|R|F)(?![A-Za-z])", " ", between)  # "T-", "Th"
    words = re.findall(r"[a-z]+", between.lower())
    return all(word in _FILLER for word in words)


def carries_own_data(text: str, private: dict[str, Any]) -> bool:
    """Whether a message carries its sender's own calendar information, raw or derived.

    ``private`` is the sender's briefing. Format examples and hints are left out first ("(e.g. Mon 09,10)",
    'like "Ben: Mon 9"', and from "format", "e.g.", "example", "such as" or "like:" to the end of the line
    or parenthesis). Then a day-and-hour mention that belongs to an attendee the sender does not hold
    ("Your Ben: Mon 9", "Please send Ben's hours: Mon 9?") does not count. The message carries data when
    at least one mention is left and either it names one of the sender's attendees (a name, an initial
    right before data such as "B:M11" or "AM9", or a group of the sender's initials such as "A,C,K"), or
    at least 3 mentions are left (a calendar or a candidate list without names: "M9-10 T16 W9,11",
    "pls check Mon11, Mon12, Tue13 for Ben"), or a left mention is offered as the sender's candidate
    window: its sentence has a cue word ("Candidates: Tue 09:00 or Thu 14:00", "Thu11 and Thu13 both
    work for me", "free", "available", "possible") or is a bare list of 2 or more windows ("Mon09 Tue13").
    A request names the receiver's attendees, or no one and no offered window, so it does not count. A
    written-out answer object is not data. Known limits: a team conclusion with a cue word counts ("the
    only common free slot is Tue 13:00"); a request that names the sender's own attendees next to an
    unmarked example ("I have Ana; send yours as Mon 09,10") counts.
    """
    calendars = private.get("calendars") if isinstance(private, dict) else None
    if not isinstance(calendars, dict):
        return False
    text = without_hints(text)
    mentions = _data_mentions(text)
    own = {str(name) for name in calendars}
    references = _references(text, own, mentions)
    owners = _lists(text, references)
    left = []
    for mention in mentions:
        before = [r for r in owners if r[1] <= mention[0]]
        if not (before and before[-1][2] == "other" and _attached(text, before[-1], mention, mentions)):
            left.append(mention)
    if not left:
        return False
    if any(whose == "own" for _, _, whose in references) or len(left) >= 3:
        return True
    return any(_offered(text, mention, mentions) for mention in left)


def _offered(text: str, mention: tuple[int, int, Window], mentions: list[tuple[int, int, Window]]) -> bool:
    """Whether a mention is offered as the sender's candidate window: its sentence has a cue word
    ("Candidates: Tue 09:00 or Thu 14:00", "Thu11 and Thu13 both work for me"), or the sentence is a bare
    list of 2 or more windows ("Mon09, Tue13")."""
    start = max(text.rfind(c, 0, mention[0]) for c in ".!?\n") + 1
    ends = [i for i in (text.find(c, mention[1]) for c in ".!?\n") if i != -1]
    end = min(ends) if ends else len(text)
    sentence = text[start:end]
    if _CUE.search(sentence):
        return True
    inside = [m for m in mentions if start <= m[0] and m[1] <= end]
    rest = list(sentence)
    for m_start, m_end, _ in inside:
        for i in range(m_start - start, m_end - start):
            rest[i] = " "
    words = re.findall(r"[a-z]+", re.sub(_DAY, " ", "".join(rest), flags=re.IGNORECASE).lower())
    return len(inside) >= 2 and all(word in ("and", "or", "both") for word in words)


def answer_in_text(text: str, key: dict[str, Any]) -> bool:
    """Whether a message names the answer ``key``: a weekday-and-hour mention or a written-out answer
    object, format examples left out, that ``score`` gives full marks ("Thursday 14:00-16:00",
    "Thu 2pm", 'SUBMITTED: {"day": "Thu", "start": "14:00"}'). A busy list "Thu 13,14" names only
    (Thu, 13)."""
    return any(score({"day": day, "start": f"{hour:02d}:00"}, key)["score"] == 1.0
               for day, hour in mentions(without_examples(text)))
