"""Shift continuity tasks with independently assigned dispatch and carrier identifiers."""

import json
import random
import re
from itertools import pairwise

FAMILY = "handoff"
TITLE = "Operate a print dispatch desk"
VERSION = 2
SEAM = "A capped note carries unfinished work and unresolved evidence to a fresh context across shifts."
LEVELS = ("canary", "standard", "heavy")
DOSES = {"canary": 3, "standard": 4, "heavy": 8}
SEATS = DOSES
CALL_CAP = 18
NOTE_CAP = 200
ARMS = ("solo", "solo-seam", "team", "hint", "twin", "team-record", "team-fullrecord",
        "probe-hunt-control", "probe-hunt-trap", "probe-ref-control", "probe-ref-trap")
TWINS = (("probe-hunt-control", "probe-hunt-trap"), ("probe-ref-control", "probe-ref-trap"))
PAIRS = tuple(("team", arm) for arm in ("solo-seam", "hint", "twin", "team-record", "team-fullrecord"))
COMMON_WORDS = ("team",)
ARM_WORDS = ("handoff", *(arm for arm in ARMS if arm not in COMMON_WORDS))
TEAM_SENTENCE = "A different operator works each shift."
RESET_SENTENCE = "You are the same operator across shifts, with a fresh session and container each time."
EXTRA_SENTENCES = {
    "hint": "Keep each outstanding batch ID with its carrier reference, and keep the list of possible bad lots in your note.",
    "team-record": "After `POST /close`, `GET /log` also shows every earlier shift's calls and replies.",
    "team-fullrecord": "During work and after closing it, `GET /log` also shows the complete uncapped record of all earlier shifts.",
    "twin": "At handover the desk writes the outgoing note from the full record instead of using your note.",
}

def parse_note(note, lots):
    """Read the documented notebook notation, not arbitrary natural language."""
    bindings = {}
    suspects = None
    cleared = set()
    pairs = []
    try:
        payload = json.loads(note)
    except (ValueError, RecursionError):
        payload = None
    if isinstance(payload, dict):
        pending = payload.get("pending")
        if isinstance(pending, dict):
            pairs = list(pending.items())
        for key in ("suspects", "cleared"):
            values = payload.get(key)
            if isinstance(values, list) and all(isinstance(v, str) for v in values):
                if key == "suspects":
                    suspects = set(values)
                else:
                    cleared = set(values)
    else:
        pairs = re.findall(r"\b([BR]\d{2})(?:\s*(?:->|>|=|:|/)\s*|\s+\(?ref\s+|"
                           r"\s+has\s+carrier\s+ref\s+|\s+)([BR]\d{2})\b", note, re.IGNORECASE)
        for clause in re.split(r"[;\n]", note):
            label = re.search(r"\b(suspects|bad lot is one of|cleared|ruled out)\b", clause, re.IGNORECASE)
            if label:
                values = set(re.findall(r"\bL\d{2}\b", clause[label.end():], re.IGNORECASE))
                if label[0].lower() in ("cleared", "ruled out"):
                    cleared.update(values)
                else:
                    suspects = values if suspects is None else suspects & values
    for first, second in pairs:
        if not isinstance(second, str):
            continue
        first, second = first.upper(), second.upper()
        batch, ref = (first, second) if first.startswith("B") else (second, first)
        if re.fullmatch(r"B\d{2}", batch) and re.fullmatch(r"R\d{2}", ref):
            bindings[batch] = ref
    possible = set(lots) if suspects is None or not suspects else set(lots) & {v.upper() for v in suspects}
    return bindings, possible - {v.upper() for v in cleared}


def write_note(bindings, possible, style="careful", lots=None):
    if style == "json":
        return json.dumps({"pending": bindings, "suspects": sorted(possible)}, separators=(",", ":"))
    order = sorted(bindings, reverse=style == "reverse")
    separator = {"colon": ":", "slash": "/", "lines": " ", "arrow": " -> "}.get(style, "=")
    if style in ("ref-first", "ref-eq"):
        pairs = [f"{bindings[b]}{'>' if style == 'ref-first' else '='}{b}" for b in order]
    elif style == "paren":
        pairs = [f"{b} (ref {bindings[b]})" for b in order]
    else:
        pairs = [f"{b}{separator}{bindings[b]}" for b in order]
    joiner = "\n" if style == "lines" else " "
    label, values = ("Cleared", set(lots) - possible) if style == "cleared" else ("Suspects", possible)
    return "Pending " + joiner.join(pairs) + f"; {label} " + " ".join(sorted(values))


def narrow(possible, lots, status):
    return possible & set(lots) if status == "returned" else possible - set(lots)



def identifiers(rng, prefix, count, excluded):
    return rng.sample([f"{prefix}{i:02d}" for i in range(100) if i not in excluded], count)


def candidates(lots, batches):
    possible = set(lots)
    for batch in batches:
        if batch["status"] == "returned":
            possible.intersection_update(batch["lots"])
        else:
            possible.difference_update(batch["lots"])
    return sorted(possible)


def arrival(batch):
    return batch["shift"] + batch["lag"] if batch["lag"] is not None else 100


def schedule(dose, probe):
    shifts = DOSES[dose]
    lag = {"canary": 2 if probe else 1, "standard": 2, "heavy": 3}[dose]
    return [min(lag, shifts - shift) for shift in range(1, shifts)]


def make_case(rng, dose, probe):
    shifts = DOSES[dose]
    lots = identifiers(rng, "L", 32, {12, 27, 35, 48, 53, 66})
    # Choose the complete plan independently of the fault. Each complementary
    # pair reveals one bit of evidence only once its carrier results arrive.
    batches = []
    for shift, lag in enumerate(schedule(dose, probe), 1):
        immediate, delayed = (1, 2) if shift == 1 else (4, 8) if shift == 2 else (
            rng.choice((4, 6, 10, 12, 14)), rng.choice((2, 8, 10, 12, 14)))
        for mask, delay in ((immediate, 0), (delayed, lag)):
            for parity in (0, 1):
                inks = sorted(lot for index, lot in enumerate(lots) if (index & mask).bit_count() % 2 == parity)
                batches.append({"shift": shift, "lag": delay, "lots": inks})
    for mask in (16, 4):
        for parity in (0, 1):
            inks = sorted(lot for index, lot in enumerate(lots) if (index & mask).bit_count() % 2 == parity)
            batches.append({"shift": shifts, "lag": 0, "lots": inks})
    bad = rng.choice(lots)
    for batch in batches:
        batch["status"] = "returned" if bad in batch["lots"] else "delivered"
    target = next(batch for batch in batches if batch["shift"] < shifts - 1 and arrival(batch) == shifts) if (
        probe or dose != "canary") else next(batch for batch in batches if batch["lag"])
    rng.shuffle(batches)
    ids = identifiers(rng, "B", len(batches), {14, 21, 33, 47, 52, 58})
    refs = identifiers(rng, "R", len(batches), {82, 7, 45, 19, 63, 30})
    for batch, desk_id, ref in zip(batches, ids, refs):
        batch.update(batch=desk_id, reference=ref)
    return {"shifts": shifts, "cap": NOTE_CAP, "lots": sorted(lots), "bad": bad,
            "batches": batches, "ref_target": target["batch"]}


def generate(seed, instance, n_seats, difficulty):
    if difficulty not in DOSES or n_seats not in (None, DOSES[difficulty]):
        raise ValueError("use the dose-specific roster: canary=3, standard=4, heavy=8")
    rng = random.Random(f"handoff:{seed}:{instance}:{difficulty}")
    base = make_case(rng, difficulty, False)
    probe = make_case(rng, difficulty, True) if difficulty == "canary" else base
    inst = {"seats": [f"s{i}" for i in range(1, DOSES[difficulty] + 1)], "base": base, "probe": probe}
    for arm in ("team", *[a for a in ARMS if a.startswith("probe-")]):
        validate_case(arm_case(inst, arm), difficulty, arm)
    return inst


def arm_case(inst, arm):
    if arm not in ARMS:
        raise ValueError("unknown arm")
    source = inst["probe" if arm.startswith("probe-") else "base"]
    return source | {"batches": [dict(batch) for batch in source["batches"]]}


def visible_possibilities(lots, known, open_batches, results):
    """A lot is feasible iff enough open batches fit each observed status."""
    returned = sum(b["status"] == "returned" for b in results)
    delivered = len(results) - returned
    feasible = []
    bindings = {"returned": set(), "delivered": set()}
    for lot in candidates(lots, known):
        used = {b["batch"] for b in open_batches if lot in b["lots"]}
        unused = {b["batch"] for b in open_batches} - used
        if len(used) >= returned and len(unused) >= delivered:
            feasible.append(lot)
            bindings["returned"].update(used)
            bindings["delivered"].update(unused)
    return feasible, bindings


def shift_view(params, shift):
    batches = params["batches"]
    known = [b for b in batches if b["shift"] == shift and b["lag"] == 0]
    open_batches = [b for b in batches if b["shift"] < shift <= arrival(b)]
    results = [b for b in open_batches if arrival(b) == shift]
    return known, open_batches, results


def chance_baseline(task_json, policy):
    """Exact expectation for the blind policy's final conditional assignment."""
    if policy != "blind":
        return None
    if task_json["condition"].endswith("-trap"):
        return 0.0  # the blind writer leaves no note and the script does not guess
    if task_json["condition"] != "team":
        return None
    params = task_json["params"]
    batches = params["batches"]
    known = [b for b in batches if b["shift"] == params["shifts"]]
    delayed = [b for b in batches if b["lag"]]
    possible, _ = visible_possibilities(params["lots"], known, delayed, delayed)
    matches = len(batches) - len(delayed)
    for lot in possible:
        for batch in delayed:
            if (lot in batch["lots"]) == (batch["status"] == "returned"):
                size = sum((lot in other["lots"]) == (batch["status"] == "returned") for other in delayed)
                matches += 1 / (size * len(possible))
    return 0.5 * (matches / len(batches) + 1 / len(possible))


def leak_values(task_json):
    params = task_json["params"]
    return [params["bad"], *(batch[key] for batch in params["batches"] for key in ("batch", "reference"))]


def validate_case(params, dose, arm):
    batches, lots = params["batches"], params["lots"]
    shifts = params["shifts"]
    if any((params["bad"] in b["lots"]) != (b["status"] == "returned") for b in batches):
        raise ValueError("inconsistent carrier results")
    if any(len(b["lots"]) != len(lots) // 2 for b in batches):
        raise ValueError("batch size differs")
    for shift in range(1, shifts + 1):
        planned = [b for b in batches if b["shift"] == shift]
        if len(planned) != 4 or sum(b["status"] == "returned" for b in planned) != 2:
            raise ValueError("shift workload or result mix differs")
        known, pending, results = shift_view(params, shift)
        possible, bindings = visible_possibilities(lots, known, pending, results)
        if len(possible) < 2 or any(len(bindings[b["status"]]) < 2 for b in results):
            raise ValueError("receiving view resolves a hidden item")
        past = [b for b in batches if arrival(b) <= shift]
        outstanding = [b for b in batches if b["shift"] <= shift < arrival(b)]
        if len(write_note({b["batch"]: b["reference"] for b in outstanding}, candidates(lots, past))) > params["cap"]:
            raise ValueError("canonical note exceeds the cap")
    if candidates(lots, batches) != [params["bad"]]:
        raise ValueError("lot does not have a unique diagnosis")
    if len(candidates(lots, [b for b in batches if arrival(b) < shifts])) == 1:
        raise ValueError("diagnosis pins before the last shift")


COMMON = """You operate the dispatch desk of a print shop. There are {shifts} shifts.
Use $WORLD_URL with the header `Authorization: Bearer $SEAT_TOKEN` for all calls.
For POST calls send JSON with the header `Content-Type: application/json`.
Call `GET /await?until=active` to wait for your shift. Proceed when `active` is `active`.
If the call returns `frozen`, `retired`, or `timeout` without activation, stop.
Your seat can make {budget} active world calls, including GET /await, GET /whoami and refused calls.
The field `calls_left` in desk replies gives the number remaining after that call.
Reserve your last 3 world calls and last 3 agent turns for handover. Only handover is allowed in the last 3 calls.
A missing handover makes the chain incomplete; it is not treated as a lost note.

In each shift, print and dispatch every batch in your plan. Settle all carrier results that arrive.
Exactly one ink lot is bad. A batch returns if and only if it uses the bad lot.
Once the evidence leaves only one possible bad lot, file its ID. A ticket filed before then earns no credit.
Only one ticket can be filed for the run.

Calls:
- `GET /desk`: your shift, the ink lots, your plan, open batches from earlier shifts, and any filed ticket.
  The plan shows each batch's ID, lots and state. Open batches have been dispatched but not settled.
- `POST /print {{"batch":"Bxx"}}`: print one planned batch.
- `POST /dispatch {{"batches":["Bxx", "..."]}}`: dispatch printed batches.
  The reply maps each batch ID to its carrier reference. The desk does not store these references.
- `POST /settle {{"items":[{{"reference":"Rxx","batch":"Bxx","action":"close"}}]}}`:
  settle one or more results. Use `close` for delivered and `reissue` for returned.
  Use the matching reference and batch exactly once. You cannot change a settlement.
  The reply records acceptance, not whether your settlement is correct.
- `POST /ticket {{"lot":"Lxx"}}`: file the bad-lot ticket. You cannot change it.
- `GET /log`: see the calls and replies of this shift.
- `POST /close {{}}`: end the work part of this shift. You cannot print, dispatch, settle or file a ticket after this.
- `POST /handover {{"note":"..."}}`: end this shift. This also closes work if not already closed.
  The note can contain up to {cap} characters. A longer note is refused. Write the note as free text.

Carrier results arrive 0, 1, 2 or 3 shifts after dispatch.
The carrier uses only the carrier reference and the status `returned` or `delivered`.
The `inbox` field in each desk call reply contains results that have not been settled.
A result due in the current shift appears in the next reply from a desk route after dispatch.
Results due from earlier shifts appear in the first reply from a desk route. Settled results leave the inbox.
The `note` field holds the previous shift's note (empty in shift 1).
The desk does not keep past results or past lot clearances.
Before closing the shift, settle all results you can match and file the ticket if the evidence pins the lot.
End work with `POST /close`. Then write your note and call `POST /handover`, including in the last shift.
The score checks correct settlements and the correct ticket. Do not guess before the evidence pins the lot.
"""


def extension_arm(inst, arm, max_text, max_messages):
    params = arm_case(inst, arm)
    count = params["shifts"]
    seats = inst["seats"][:1 if arm == "solo" else count]
    budget = CALL_CAP * (count if arm == "solo" else 1)
    common = COMMON.format(shifts=count, cap=params["cap"], budget=budget)
    instructions, briefings = {}, {}
    for index, seat in enumerate(seats, 1):
        if arm == "solo":
            suffix = "You work every shift in order and retain your memory and files. Nobody else reads your notes."
        else:
            framing = RESET_SENTENCE if arm == "solo-seam" else TEAM_SENTENCE
            suffix = (f"You work shift {index} of {count}. At each handover, the current session closes. "
                      f"The next session starts with your note. Your own log closes at handover.\n{framing}")
        if arm in EXTRA_SENTENCES:
            suffix += "\n" + EXTRA_SENTENCES[arm]
        instructions[seat] = common + "\n" + suffix + "\n"
        briefings[seat] = {"shift": index, "shifts": count, "cap": params["cap"], "continuous": arm == "solo"}
    scripted = seats[:-2] + seats[-1:] if arm.startswith("probe-") else []
    return {"params": params, "briefings": briefings, "instructions": instructions, "messaging": False,
            "initial_active": seats[:1], "scripted_seats": scripted, "max_calls": budget,
            "start_after": {seat: [preceding] for preceding, seat in pairwise(seats)},
            "scripted_policies": dict.fromkeys(scripted, "careful")}


def pair_difference(first, second, views_a, views_b):
    if first != "team" or second not in {b for _, b in PAIRS}:
        yield "unknown pair"
        return
    normalized = {seat: dict(view) for seat, view in views_b.items()}
    for seat, view in normalized.items():
        sentence = RESET_SENTENCE if second == "solo-seam" else EXTRA_SENTENCES[second]
        if view["instruction"].count(sentence) != 1:
            yield f"{seat} lacks its one declared sentence"
            continue
        if second == "solo-seam":
            view["instruction"] = view["instruction"].replace(sentence, TEAM_SENTENCE, 1)
        else:
            view["instruction"] = view["instruction"].replace("\n" + sentence, "", 1)
    if normalized != views_a:
        yield "more than the declared sentence differs"

def qa(task, views):
    for seat, view in views.items():
        data = view["instruction"] + view["env.SEAT_BRIEFING"]
        if re.search(r"\b[BLR]\d{2}\b", data):
            yield f"{seat} has instance-specific IDs in its briefing"
    params = task["params"]
    try:
        validate_case(params, task["difficulty"], task["condition"])
    except ValueError as error:
        yield str(error)
    if len({b["batch"] for b in params["batches"]}) != len(params["batches"]):
        yield "duplicate batch ID"
    if len({b["reference"] for b in params["batches"]}) != len(params["batches"]):
        yield "duplicate carrier reference"
