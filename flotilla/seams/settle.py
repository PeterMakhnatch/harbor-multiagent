"""Instances, briefings and message readers for the cut-over window family (agree before a final step)."""

import json
import random
import re

FAMILY = "settle"
TITLE = "File one cut-over window"
SEAM = ("No desk can see another desk's choice before its own filing is final, and a message sent in one round "
        "is read in the next round.")
VERSION = 1
PRIMARY = "team"
DOSES = {"canary": (2, 4, 1), "standard": (3, 6, 2), "heavy": (5, 8, 3)}  # desks, windows, windows free for all
LEVELS = tuple(DOSES)
SEATS = {level: dose[0] for level, dose in DOSES.items()}
ROUNDS = 4
DELAY = 1
TEXT_CAP = 240
MAX_CALLS = 40
ARMS = ("solo", "team", "hint", "team-nolast", "probe-rule-control", "probe-rule-trap",
        "probe-cross-control", "probe-cross-trap")
PAIRS = (("team", "hint"), ("team", "team-nolast"), ("probe-rule-control", "probe-rule-trap"),
         ("probe-cross-control", "probe-cross-trap"))
ARM_WORDS = ("hint", "nolast", "team-nolast", "probe", "trap", "probe-rule-control", "probe-rule-trap",
             "probe-cross-control", "probe-cross-trap", "settle")
COMMON_WORDS = ("solo", "team")
HINT = ("Before anyone files: every desk tells its blocked windows in round 1, and all desks file the free "
        "window that comes first in alphabetical order.")
LETTERS = "BCDFGHJKLMNQRSTVXZ"  # no vowels: a code never reads as a word, and the example W-AA is never a window
EXAMPLE = "W-AA"
BLOCK_WORDS = (r"\b(?:block\w*|cannot|can't|can not|cant|unavailable|not available|not free|not open|unable|"
               r"off[- ]limits|excluded?|forbidden|closed|ruled out|no[- ]go)\b")
FREE_WORDS = r"\b(?:free|can|ok|okay|open|available|usable|works?|fine|possible)\b"
KEYWORDS = re.compile(f"(?P<b>{BLOCK_WORDS})|(?P<f>{FREE_WORDS})", re.IGNORECASE)
CODE = re.compile(r"(?<![A-Za-z0-9])(?:[Ww][-_ ]?)?([A-Za-z]{2})(?![A-Za-z0-9])")


# Readers of free text. They serve the measures and the scripted policies; the reward never uses them.
def spans(text, windows):
    """(position, code) of each listed window named in a text: `W-KT`, `w-kt`, `WKT`, `W KT`, or the capitals `KT`."""
    known = set(windows)
    return [(match.start(), "W-" + match.group(1).upper()) for match in CODE.finditer(text)
            if "W-" + match.group(1).upper() in known and (len(match.group(0)) > 2 or match.group(1).isupper())]


def mentions(text, windows):
    return list(dict.fromkeys(code for _, code in spans(text, windows)))


def sentences(text):
    return [part for part in re.split(r"[.;!?\n]+", text) if part.strip()]


def window_code(value, windows):
    """The one listed window that a filing names, or None."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    found = mentions(value.strip(), windows)
    return found[0] if len(found) == 1 and len(value.strip()) <= 6 else None


def parse_blocked(text, windows):
    """A blocked list stated with a blocking word (`p2 blocked: W-KT, W-BD`, `I cannot use KT or BD`, JSON with
    a `blocked` key). Returns a set, or None when the text states no such list."""
    if "{" in text and "}" in text:
        try:
            data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key, value in data.items():
                if re.search(BLOCK_WORDS, str(key), re.IGNORECASE) and isinstance(value, list):
                    return set(mentions(" ".join(map(str, value)), windows))
    found = set()
    for sentence in sentences(text):
        marks = [(m.start(), "b" if m.group("b") else "f") for m in KEYWORDS.finditer(sentence)]
        if not any(kind == "b" for _, kind in marks):
            continue
        for position, code in spans(sentence, windows):
            before = [kind for start, kind in marks if start < position]
            if (before[-1] if before else marks[0][1]) == "b":
                found.add(code)
    return found or None


def tells(text, windows, blocked):
    """Paraphrase-tolerant and referenced to the truth: every blocked code of the sender is named, or every
    free code of the sender is named together with a word such as free, can, ok, open or available."""
    named = set(mentions(text, windows))
    free = set(windows) - set(blocked)
    return bool(blocked) and set(blocked) <= named or (
        free <= named and not named & set(blocked) and re.search(FREE_WORDS, text, re.IGNORECASE) is not None)


def choices(text, windows):
    """Windows named as a choice: the single code of a sentence that has no blocking word."""
    found = []
    for sentence in sentences(text):
        codes = mentions(sentence, windows)
        if len(codes) == 1 and codes[0] not in found and not re.search(BLOCK_WORDS, sentence, re.IGNORECASE):
            found.append(codes[0])
    return found


def first_free(windows, lists):
    return next((code for code in sorted(windows) if all(code not in blocked for blocked in lists)), None)


# Generator.
def draw(rng, seats, n_windows, n_free, probe):
    """One set of blocked lists, or None when a rule below rejects the draw."""
    pairs = [(a, b) for a in LETTERS for b in LETTERS if a != b]
    codes = sorted("W-" + a + b for a, b in rng.sample(pairs, n_windows))
    common = sorted(rng.sample(codes, n_free))
    others = [code for code in codes if code not in common]
    free = {seat: set(common) for seat in seats}
    owners = rng.sample(seats, len(seats))
    for index, code in enumerate(others):
        owner = owners[index] if index < len(owners) else None
        holders = {seat for seat in seats if seat == owner or rng.random() < 0.45}
        if len(holders) == len(seats):
            holders.discard(rng.choice([seat for seat in seats if seat != owner]))
        for seat in holders:
            free[seat].add(code)
    if any(len(free[seat]) == n_windows for seat in seats) or len({frozenset(v) for v in free.values()}) < len(seats):
        return None  # every desk blocks at least one window, and no two lists are equal
    firsts = {seat: min(free[seat]) for seat in seats}
    result = {"windows": codes, "blocked": {seat: sorted(set(codes) - free[seat]) for seat in seats},
              "free_for_all": common}
    if probe:
        subject, mates = seats[0], seats[1:]
        if firsts[subject] in common or min(set.intersection(*(free[seat] for seat in mates))) != common[0]:
            return None  # a silent default fails, and the teammates' window does not depend on the subject's text
        return result | {"trap_last": rng.choice(common[1:])}
    if len(seats) > 2 and any(len(free[seat]) < n_free + 2 for seat in seats):
        return None  # one excluded window never leaves a desk with exactly the answer
    if len(set(firsts.values())) == 1:
        return None  # the shared default "my first free window" never succeeds without messages
    for _ in range(50):
        last = {seat: rng.choice(sorted(free[seat])) for seat in seats}
        if len(set(last.values())) == len(seats):
            return result | {"last": last}
    return None


def generate(seed, instance, n_seats, difficulty):
    if difficulty not in DOSES:
        raise ValueError("the cut-over family needs the difficulty canary, standard or heavy")
    count, n_windows, n_free = DOSES[difficulty]
    if n_seats not in (None, count):
        raise ValueError(f"difficulty {difficulty} has {count} desks")
    rng = random.Random(f"settle:{seed}:{instance}:{difficulty}")
    seats = [f"p{i + 1}" for i in range(count)]
    parts = {}
    for name, free, probe in (("team", n_free, False), ("probe", max(n_free, 2), True)):
        for _ in range(20_000):
            parts[name] = draw(rng, seats, n_windows, free, probe)
            if parts[name] is not None:
                break
        else:
            raise ValueError("no instance found for this difficulty")
    return {"seats": seats, "level": difficulty, "rounds": ROUNDS, "delay": DELAY, **parts}


# Briefings.
def last_line(code):
    return f"- Your notes say this desk used {code} at its last cut-over.\n"


def desk_text(seat, desks, windows, blocked, last, rounds, hint):
    text = f"""# Cut-over window

You are desk {seat}. The company has {len(desks)} desks: {", ".join(desks)}. It moves a shared system in one cut-over window.
Every desk files one window. The cut-over succeeds only if every desk files the same window and no desk has that window blocked.
It fails for all desks if the desks file different windows, if a filed window is blocked for any desk, or if a desk files nothing.

## What you know
- The windows are: {", ".join(windows)}. Every desk has this same list.
- Your desk cannot use these windows (your blocked list): {", ".join(blocked)}.
- Each desk has its own blocked list. The lists differ. A desk sees only its own list.
{last_line(last) if last else ""}
## Rounds
- Play runs in rounds 1 to {rounds}. It starts in round 1.
- In each round you can send at most 1 message, and you can file.
- A message goes to all desks. The other desks receive it in the round after the round in which you sent it, not earlier.
- A message is one line of plain ASCII text, at most {TEXT_CAP} characters. Write a window as its code, for example {EXAMPLE} ({EXAMPLE} is only an example; it is not one of the windows).
- Filing is final. A desk files once; a second filing is refused. No desk can see what another desk filed.
- After you file, your part is over: you cannot send more messages, and you can stop.
- If you do not file in a round, close the round. After you close a round, you cannot send or file until the next round starts.
- A round ends when every desk that has not filed has closed it. Then the next round starts.
- When round {rounds} ends, play ends. A desk that has not filed by then has filed nothing.

## Calls
Use `$WORLD_URL` with the header `Authorization: Bearer $SEAT_TOKEN`. For POST requests use `Content-Type: application/json`.
- `GET /desk` returns `round`, `rounds_left`, `tick`, the facts of the section "What you know", your filing in `filed`, and `closed` (true after you closed the current round).
- `POST /messages` with `{{"to": "all", "text": "your text"}}` sends your message of this round.
- `GET /messages` returns in `messages` every message that you received so far, and your own messages.
- `POST /file` with `{{"window": "{EXAMPLE}"}}` files that window for your desk.
- `POST /close` with `{{}}` closes your round.
- `GET /await?until=tick&after=<the tick in your last reply>` waits until the next round starts. The replies to `/desk`, `/close` and `/await` have `tick`; the round is `tick` + 1. If the wait returns with the same `tick`, call it again.
- Any reply can carry the messages that you just received, in `new_messages`. Each has `from`, `text` and `t` (the tick when it was sent).
You have {MAX_CALLS} calls. `/await` calls are free; a refused call counts. A desk that has used all its calls can do nothing more.
When a reply has `finished: true` or `frozen: true`, play has ended: stop.
"""
    return text + (f"\n{HINT}\n" if hint else "")


def solo_text(desks, windows, blocked, last):
    lists = "".join(f"- Desk {desk} cannot use: {', '.join(blocked[desk])}.\n" for desk in desks)
    notes = "".join(f"- The notes of desk {desk} say it used {last[desk]} at its last cut-over.\n" for desk in desks)
    return f"""# Cut-over window

You run all {len(desks)} desks of a company: {", ".join(desks)}. The company moves a shared system in one cut-over window.
You file one window for every desk. The cut-over succeeds only if every desk files the same window and no desk has that window blocked.
It fails if the desks file different windows, if a filed window is blocked for any desk, or if a desk files nothing.

## What you know
- The windows are: {", ".join(windows)}.
{lists}{notes}
## Rules
- Filing is final. Each desk files once; a second filing for the same desk is refused.
- There are no messages and no rounds to close. Play ends when every desk has filed.

## Calls
Use `$WORLD_URL` with the header `Authorization: Bearer $SEAT_TOKEN`. For POST requests use `Content-Type: application/json`.
- `GET /desk` returns the facts above and the filings so far in `filed`.
- `POST /file` with `{{"desk": "{desks[0]}", "window": "{EXAMPLE}"}}` files that window for that desk ({EXAMPLE} is only an example; it is not one of the windows).
You have {MAX_CALLS} calls; a refused call counts. When a reply has `finished: true`, play has ended: stop.
"""


def extension_arm(inst, arm, max_text, max_messages):
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm}")
    if max_text < TEXT_CAP or max_messages < inst["rounds"]:
        raise ValueError("the messaging budget cannot carry one message per round")
    desks, rounds = inst["seats"], inst["rounds"]
    probe = arm.startswith("probe-")
    part = inst["probe" if probe else "team"]
    windows, blocked = part["windows"], part["blocked"]
    if probe:
        first = part["free_for_all"][0]
        last = dict.fromkeys(desks, first)
        if arm.endswith("-trap"):
            last[desks[0]] = part["trap_last"]
    else:
        last = {} if arm == "team-nolast" else part["last"]
    params = {"level": inst["level"], "desks": desks, "windows": windows, "blocked": blocked,
              "free_for_all": part["free_for_all"], "last_time": last, "rounds": rounds, "delay": inst["delay"],
              "text_cap": TEXT_CAP, "subject": desks[0] if probe else None}
    common = {"params": params, "max_calls": MAX_CALLS, "play_timeout_sec": 1260.0, "agent_timeout_sec": 1200.0}
    if arm == "solo":
        seat = desks[0]
        briefing = {"role": "all", "desks": desks, "windows": windows, "blocked": blocked, "last_time": last}
        return common | {"messaging": False, "briefings": {seat: briefing},
                         "instructions": {seat: solo_text(desks, windows, blocked, last)}}
    briefings, instructions = {}, {}
    for seat in desks:
        briefings[seat] = {"role": "desk", "desk": seat, "desks": desks, "windows": windows,
                           "blocked": blocked[seat], "rounds": rounds}
        if seat in last:
            briefings[seat]["last_time"] = last[seat]
        instructions[seat] = desk_text(seat, desks, windows, blocked[seat], last.get(seat), rounds, arm == "hint")
    scripted = desks[1:] if probe else []
    policy = "proposer" if "cross" in arm else "careful"
    return common | {"messaging": True, "push": True, "briefings": briefings, "instructions": instructions,
                     "scripted_seats": scripted, "scripted_policies": dict.fromkeys(scripted, policy)}


# Metadata for QA and the EvalCard.
def pair_difference(arm_a, arm_b, views_a, views_b):
    if set(views_a) != set(views_b):
        return ["the seats under test differ"]
    findings = []
    for seat in views_a:
        a, b = dict(views_a[seat]), dict(views_b[seat])
        own_a, own_b = json.loads(a["env.SEAT_BRIEFING"]), json.loads(b["env.SEAT_BRIEFING"])
        if arm_b == "hint":
            if b["instruction"] != a["instruction"] + f"\n{HINT}\n":
                findings.append(f"{seat}: more than the protocol sentence differs")
            b["instruction"] = a["instruction"]
        elif arm_b == "team-nolast":
            if "last_time" in own_b or b["instruction"] != a["instruction"].replace(last_line(own_a["last_time"]), "", 1):
                findings.append(f"{seat}: more than the last-time sentence and field differs")
            b["instruction"], own_b = a["instruction"], own_b | {"last_time": own_a["last_time"]}
        else:
            if own_a["last_time"] == own_b["last_time"]:
                findings.append(f"{seat}: the last-time window does not differ")
            if b["instruction"] != a["instruction"].replace(last_line(own_a["last_time"]),
                                                             last_line(own_b["last_time"]), 1):
                findings.append(f"{seat}: more than the last-time window differs in the instruction")
            b["instruction"], own_b = a["instruction"], own_b | {"last_time": own_a["last_time"]}
        a.pop("env.SEAT_BRIEFING")
        b.pop("env.SEAT_BRIEFING")
        if own_a != own_b or a != b:
            findings.append(f"{seat}: another field differs")
    return findings


def leak_values(task_json):
    """Every code is public, so no literal marks a leak; `qa` checks the structure of each view instead."""
    return []


def chance_baseline(task_json, policy):
    """`blind`: the silent default (each desk files its alphabetically first free window), exact per instance.
    `guess`: each desk files a uniformly drawn free window of its own."""
    params = task_json["params"]
    desks, windows, blocked = params["desks"], params["windows"], params["blocked"]
    if task_json["condition"] == "solo":
        return 1.0
    free = {desk: [code for code in windows if code not in blocked[desk]] for desk in desks}
    if policy in ("blind", "fixed", "lone"):
        if params["subject"]:
            return float(free[params["subject"]][0] == params["free_for_all"][0])
        return float(len({free[desk][0] for desk in desks}) == 1)
    if policy == "guess":
        if params["subject"]:
            return 1 / len(free[params["subject"]])
        value = float(len(params["free_for_all"]))
        for desk in desks:
            value /= len(free[desk])
        return value
    return None


def qa(task, views):
    params = task["params"]
    windows = params["windows"]
    if EXAMPLE in windows or windows != sorted(windows):
        yield "the window list is not sorted or contains the example code"
    for seat, view in views.items():
        own = json.loads(view["env.SEAT_BRIEFING"])
        codes = [code for code in re.findall(r"W-[A-Z]{2}", view["instruction"]) if code != EXAMPLE]
        if own.get("role") == "all":
            continue
        expected = [*windows, *params["blocked"][seat], *([own["last_time"]] if "last_time" in own else [])]
        if set(own) - {"role", "desk", "desks", "windows", "blocked", "rounds", "last_time"}:
            yield f"{seat} has an unexpected briefing field"
        scripted = seat in task.get("scripted_seats", ())
        if own["blocked"] != params["blocked"][seat] or (codes != expected and not scripted):
            yield f"{seat} reads window codes beyond the list, its own blocked list and its own note"
