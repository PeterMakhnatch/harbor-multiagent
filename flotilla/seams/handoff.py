"""Shift continuity tasks with independently assigned dispatch and carrier identifiers."""

import random

FAMILY = "handoff"
TITLE = "Operate a print dispatch desk"
SEATS = 3
DOSES = {"canary": 3, "standard": 4, "heavy": 8}
ARMS = ("solo", "solo-seam", "team", "hint", "twin", "team-record",
        "probe-hunt-control", "probe-hunt-trap", "probe-ref-control", "probe-ref-trap")
TWINS = (("probe-hunt-control", "probe-hunt-trap"), ("probe-ref-control", "probe-ref-trap"))


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
    if dose == "canary" and not probe:
        return [(2, 2, 0), (2, 0, 0)]
    if dose == "canary":
        return [(2, 0, 2), (2, 2, 0), (2, 0, 0)]
    if dose == "standard":
        return [(2, 3, 4), (2, 3, 4), (2, 2, 0), (2, 0, 0)]
    return [(2, 3, 4)] * 4 + [(1, 3, 4)] * 2 + [(1, 2, 0), (1, 0, 0)]


def make_case(rng, dose, probe):
    counts = schedule(dose, probe)
    shifts = len(counts)
    lots = identifiers(rng, "L", {"canary": 6, "standard": 10, "heavy": 16}[dose], {12, 27, 35, 48, 53, 66})
    bad, a, b, c, d, *_ = lots
    early = [bad, a] if dose == "canary" and not probe else [bad, a, b]
    common = [bad, b] if dose == "canary" and not probe else [bad, b, c]
    if dose == "heavy":
        early = [bad, a, b, c]
        common = [bad, b, c, d]
    safe = [lot for lot in lots if lot not in set(early + common)]
    if dose == "canary" and not probe:
        safe = [lot for lot in lots if lot not in (bad, a, b)]
    batches = []
    for shift, sizes in enumerate(counts, 1):
        for lag, size in enumerate(sizes):
            for j in range(size):
                due = shift + lag
                returned = j % 2 == 0
                if lag == 0 and shift == shifts:
                    returned = False
                inks = (early if due == 1 else common) if returned else rng.sample(safe, 2)
                batches.append({"shift": shift, "lag": lag, "lots": sorted(inks),
                                "status": "returned" if returned else "delivered"})
        minimum = {"canary": 0, "standard": 7, "heavy": 9}[dose]
        for _ in range(max(0, minimum - sum(sizes))):
            inks = rng.sample(lots, 2)
            batches.append({"shift": shift, "lag": None, "lots": sorted(inks),
                            "status": "returned" if bad in inks else "delivered"})
    decisive = next(batch for batch in batches if batch["shift"] == shifts and batch["lag"] == 0)
    rival = a if dose == "canary" and not probe else c if dose == "heavy" else b
    decisive["lots"] = sorted([rival, rng.choice(safe)])
    if dose == "heavy":
        earlier = next(batch for batch in batches if arrival(batch) == 3 and batch["status"] == "delivered")
        earlier["lots"] = sorted([b, rng.choice(safe)])
    target = next((batch for batch in batches if batch["shift"] == shifts - 2 and batch["lag"] == 2), decisive)
    rng.shuffle(batches)
    ids = identifiers(rng, "B", len(batches), {14, 21, 33, 47, 52, 58})
    refs = identifiers(rng, "R", len(batches), {82, 7, 45, 19, 63, 30})
    for batch, desk_id, ref in zip(batches, ids, refs):
        batch.update(batch=desk_id, reference=ref)
    return {"shifts": shifts, "cap": {"canary": 600, "standard": 400, "heavy": 300}[dose],
            "lots": sorted(lots), "bad": bad, "batches": batches,
            "hunt_target": decisive["batch"], "ref_target": target["batch"]}


def generate(seed, instance, n_seats, difficulty):
    if difficulty not in DOSES or n_seats not in (None, DOSES[difficulty]):
        raise ValueError("use the dose-specific roster: canary=3, standard=4, heavy=8")
    rng = random.Random(f"handoff:{seed}:{instance}:{difficulty}")
    base = make_case(rng, difficulty, False)
    probe = make_case(rng, difficulty, True) if difficulty == "canary" else base
    return {"seats": [f"s{i}" for i in range(1, DOSES[difficulty] + 1)], "base": base, "probe": probe}


def arm_case(inst, arm):
    if arm not in ARMS:
        raise ValueError("unknown arm")
    source = inst["probe" if arm.startswith("probe-") else "base"]
    params = source | {"batches": [dict(batch) for batch in source["batches"]]}
    for batch in params["batches"]:
        if arm == "probe-hunt-control" and batch["batch"] == params["hunt_target"]:
            batch["shift"] -= 1
        if arm == "probe-ref-control" and batch["batch"] == params["ref_target"]:
            batch["lag"] = 1
    return params


COMMON = """You operate the dispatch desk of a print shop. There are {shifts} shifts.
Use $WORLD_URL with the header `Authorization: Bearer $SEAT_TOKEN` for all calls.
For POST calls send JSON with the header `Content-Type: application/json`.
Wait until your seat is active: call `GET /whoami` to check the `active` field.

In each shift, print and dispatch every batch in your plan. Settle all carrier results that arrive.
Exactly one ink lot is bad. A batch returns if and only if it uses the bad lot.
Once the evidence leaves only one possible bad lot, file its ID. Only one ticket can be filed for the run.

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
  The note can contain up to {cap} characters. A longer note is refused.

Carrier results arrive 0, 1 or 2 shifts after dispatch, or after the run.
The carrier uses only the carrier reference and the status `returned` or `delivered`.
The `inbox` field in each desk call reply contains results that have not been settled.
A result due in the current shift appears in the reply to the next desk call after dispatch.
Results due from earlier shifts appear with the first desk call. Settled results leave the inbox.
The `note` field holds the previous shift's note (empty in shift 1).
The desk does not keep past results or past lot clearances.
Before closing the shift, settle all results you can match and file the ticket if it is due.
Finish with a note and handover. Your work is checked on correct settlements and the correct ticket.
"""


def extension_arm(inst, arm, max_text, max_messages):
    params = arm_case(inst, arm)
    count = params["shifts"]
    seats = inst["seats"][:1 if arm == "solo" else count]
    common = COMMON.format(shifts=count, cap=params["cap"])
    instructions, briefings = {}, {}
    for index, seat in enumerate(seats, 1):
        if arm == "solo":
            suffix = "You work every shift in order and retain your memory and files. Nobody else reads your notes."
        elif arm == "solo-seam":
            suffix = (f"You work shift {index} of {count}. Your memory and files are reset at each handover; "
                      "your next self receives only your note.")
        else:
            suffix = (f"You work shift {index} of {count}. The previous operator left the note on the desk. "
                      "The next operator starts with only your note; your log closes at handover.")
        if arm == "hint":
            suffix += ("\nKeep each outstanding batch ID with its carrier reference, "
                       "and keep the list of possible bad lots in your note.")
        elif arm == "team-record":
            suffix += "\nAfter `POST /close`, `GET /log` also shows every earlier shift's calls and replies."
        elif arm == "twin":
            suffix += "\nAt handover the desk writes the outgoing note from the full record instead of using your note."
        instructions[seat] = common + "\n" + suffix + "\n"
        briefings[seat] = {"shift": index, "shifts": count, "cap": params["cap"], "continuous": arm == "solo",
                           "record_after_close": arm == "team-record"}
    scripted = seats[:-2] if arm.startswith("probe-") else []
    return {"params": params, "briefings": briefings, "instructions": instructions, "messaging": False,
            "initial_active": seats[:1], "scripted_seats": scripted,
            "scripted_policies": dict.fromkeys(scripted, "careful")}


def qa(task, views):
    import re

    for seat, view in views.items():
        data = view["instruction"] + view["env.SEAT_BRIEFING"]
        if re.search(r"\b[BLR]\d{2}\b", data):
            yield f"{seat} has instance-specific IDs in its briefing"
    params = task["params"]
    results = [b for b in params["batches"] if b["lag"] is not None]
    if candidates(params["lots"], results) != [params["bad"]]:
        yield "full carrier evidence does not uniquely identify a lot"
    if len({b["batch"] for b in params["batches"]}) != len(params["batches"]):
        yield "duplicate batch ID"
    if len({b["reference"] for b in params["batches"]}) != len(params["batches"]):
        yield "duplicate carrier reference"
