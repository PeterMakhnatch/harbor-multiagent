"""Freight booking with mid-day carrier notices."""

import random

FAMILY = "commit"
TITLE = "Book the day's outbound freight"
SEATS = 2
ARMS = ("solo", "solo-seam", "team", "probe-control", "probe-trap", "hint", "probe-quiet")
TWINS = (("probe-control", "probe-trap"), ("probe-control", "probe-quiet"))
PAIRS = (("probe-trap", "hint"),)
DOSES = {"canary": (4, 1), "standard": (8, 2), "heavy": (10, 4)}
HINT = ("Before you book a round, read the `new_messages` of the reply that opened that round; "
        "an outage can start in the middle of the day.")

CARRIERS = (
    "Ridgeline Freight", "Harbor Line", "Keystone Haul", "Northstar Transit", "Bluewater Cargo",
    "Summit Transport", "Redwood Express", "Silver Creek Logistics", "Lakeside Haul", "Pine Valley Freight",
    "Cedar Road Carriers", "Stonebridge Transport",
)
CITIES = ("Albany", "Boston", "Philadelphia", "Baltimore", "Hartford", "Providence", "Buffalo", "Trenton",
          "Syracuse", "Harrisburg", "Richmond", "Pittsburgh", "New Haven", "Rochester")
ORIGINS = ("Newark", "Jersey City", "Elizabeth", "Paterson")


def outage_text(carrier, start):
    return f"{carrier} is out of service from round {start} to the end of the day. Its drivers reached their hours limit."


def generate(seed, instance, n_seats, difficulty):
    if n_seats != SEATS or difficulty not in DOSES:
        raise ValueError("freight booking needs two desks and canary, standard or heavy difficulty")
    rng = random.Random(f"commit:{seed}:{instance}:{difficulty}")
    rounds, batch = DOSES[difficulty]
    n = rounds * batch
    primary_count = 4
    carriers = rng.sample(CARRIERS, 7)
    primaries, backups, other = carriers[:primary_count], carriers[primary_count:6], carriers[6]
    origin = rng.choice(ORIGINS)
    destinations = rng.sample(CITIES, 4 if difficulty == "canary" else 8)
    loads = ("dry", "reefer") if difficulty == "heavy" else ("dry",)
    lane_keys = [(origin, dest, load) for dest in destinations for load in loads]
    rng.shuffle(lane_keys)
    lanes = [{"origin": o, "destination": d, "load": load, "primary": primaries[i % primary_count],
              "backup": rng.choice(backups)} for i, (o, d, load) in enumerate(lane_keys)]
    rng.shuffle(lanes)
    starts = {"canary": [rng.randint(3, 4)], "standard": [rng.randint(4, 6)],
              "heavy": [rng.randint(3, 5), rng.randint(6, 9)]}[difficulty]
    slots = [None] * n
    if difficulty == "canary":
        first = rng.sample(lanes, 2)
        second = rng.sample(first, 2)
        slots = first + second
        selected = rng.randrange(2)
        chosen = [second[selected]["primary"]]
        if selected == 0:
            starts[0] = 3
    else:
        alternate = rng.sample(primaries, 4 if difficulty == "heavy" else 2)
        constraints = [(carrier, starts[0]) for carrier in alternate[:2]]
        chosen = [rng.choice(alternate[:2])]
        if difficulty == "heavy":
            constraints = [(carrier, starts[1]) for carrier in alternate[2:]] + constraints
            chosen.append(rng.choice(alternate[2:]))
        for carrier, start in constraints:
            boundary = (start - 1) * batch
            candidates = [lane for lane in lanes if lane["primary"] == carrier]
            early = rng.choice([i for i in range(boundary) if slots[i] is None])
            late = rng.sample([i for i in range(boundary, n) if slots[i] is None], 3)
            for i in [early, *late]:
                slots[i] = rng.choice(candidates)
    outages = [{"carrier": carrier, "start": start, "text": outage_text(carrier, start)}
               for carrier, start in zip(chosen, starts)]
    ids = rng.sample(range(100, 1000), n)
    shipments = [{"id": f"S-{ids[i]}", "round": i // batch + 1,
                  **{k: v for k, v in (lane or rng.choice(lanes)).items() if k not in ("primary", "backup")}}
                 for i, lane in enumerate(slots)]
    distractors = []
    if difficulty != "canary":
        delivery_rounds = [2, starts[0] + 1] if difficulty == "standard" else sorted(rng.sample(range(2, rounds + 1), 6))
        for i, r in enumerate(delivery_rounds):
            text = (outage_text(other, r) if difficulty == "heavy" and i == 2 else
                    f"{rng.choice(carriers[:6])} pickups at dock {rng.randint(1, 9)} "
                    f"run {rng.randint(5, 50)} minutes late today.")
            distractors.append({"round": r, "text": text})
    return {"seats": ["p1", "p2"], "dose": difficulty, "rounds": rounds, "batch": batch, "lanes": lanes,
            "shipments": shipments, "outages": outages, "distractors": distractors}


def lane_key(row):
    return row["origin"], row["destination"], row["load"]


def answer_key(inst, outages):
    lanes = {lane_key(lane): lane for lane in inst["lanes"]}
    starts = {o["carrier"]: o["start"] for o in outages}
    return {s["id"]: (lane["backup"] if starts.get(lane["primary"], inst["rounds"] + 1) <= s["round"]
                      else lane["primary"])
            for s in inst["shipments"] for lane in (lanes[lane_key(s)],)}


def booking_instruction(inst, arm, max_text, max_messages):
    origin = inst["lanes"][0]["origin"]
    lines = [
        f"You are the booking desk at the {origin} freight yard.",
        "Today you book one truck for each outbound shipment.",
        f"The day runs in rounds 1 to {inst['rounds']}. Book each shipment in its own round.",
        "A booking is final: the carrier sends a truck at once. A missed round loses its unbooked shipments.",
        "Book the primary carrier of each lane. If it is out of service in that round, book the backup instead.",
        "At most one carrier of a lane is out at a time. An outage lasts to the end of the day.",
        "",
        "| Origin | Destination | Load | Primary | Backup |",
        "|---|---|---|---|---|",
    ]
    lines.extend("| " + " | ".join(lane[k] for k in ("origin", "destination", "load", "primary", "backup")) + " |"
                 for lane in inst["lanes"])
    lines.append("")
    if arm == "solo":
        lines.append("You also run the carrier desk. These are the outages today:")
        lines.extend(f"- {o['text']}" for o in inst["outages"])
    elif arm == "solo-seam":
        lines.append("The carrier board posts outage notices at the start of a round. Each notice names its start round.")
    else:
        lines.extend([
            "Your colleague at the carrier desk knows the outages and their start rounds.",
            "A notice sent before the first round arrives at its start. A notice sent in a round arrives in the next.",
            "The carrier desk may not answer questions. It can finish its work before the day ends.",
        ])
    lines.extend([
        "Only an outage that affects a lane changes its booking. Other notices need no action.",
        f"Messages have a limit of {max_text} characters and {max_messages} sends per desk.",
        "",
        "Use $WORLD_URL with the header `Authorization: Bearer $SEAT_TOKEN`.",
        "For POST requests use `Content-Type: application/json`.",
        "Start with `GET /view`. It gives the current `round`, `rounds`, `bookings` and all notices so far in `messages`.",
        "`rounds` maps round numbers to shipments. The keys are strings.",
    ])
    if inst["dose"] == "canary":
        lines.append("Each shipment has an ID, origin, destination and load in the view.")
    else:
        lines.append("Each shipment is an ID. `GET /shipment?id=<id>` gives its origin, destination and load.")
    lines.extend([
        ('`POST /step` with `{"round": <number>, "bookings": [{"shipment": "<id>", "carrier": "<name>"}]}` '
         "books that round's shipments and ends your part of the round."),
        "The next round opens when both desks have finished their part, or if the carrier desk has finished for the day.",
        "The reply gives the current view. If `stepped` is true, your part is complete but the round remains open.",
        "In that case use `GET /await?until=tick&after=<round minus 1>` to wait, then `GET /view` for the next round.",
        "If the wait returns in the same round, wait again. Do not book that round twice.",
        "Any reply can carry new notices in `new_messages`. Read the text and the start round.",
        "When `finished` is true or the world reports that play has ended, stop.",
    ])
    if arm == "hint":
        lines.append(HINT)
    lines.append("You score 1 if every shipment is booked in its own round with the right carrier. Otherwise you score 0.")
    return "\n".join(lines) + "\n"


def carrier_instruction(inst, max_text, max_messages):
    notices = "\n".join(f"- {o['text']}" for o in inst["outages"])
    return (
        f"You are the carrier desk. The booking desk books shipments in rounds 1 to {inst['rounds']}.\n"
        "It books the primary carrier unless that carrier is out of service in that round, then the backup.\n"
        f"Only you know these outages:\n{notices}\n"
        "Send each notice at least one round before the outage starts. You can send all notices in round 1.\n"
        "A message reaches the booking desk at the start of the next round.\n"
        f"Messages have a limit of {max_text} characters and {max_messages} sends per desk.\n"
        "Use $WORLD_URL with the header `Authorization: Bearer $SEAT_TOKEN`.\n"
        "For POST requests use `Content-Type: application/json`.\n"
        "`GET /view` shows the round and the bookings so far.\n"
        '`POST /messages` with `{"to": "p1", "text": "<notice>"}` sends one notice. Copy its text as given.\n'
        '`POST /step` with `{"round": <number>}` ends your part of the round. '
        "The world waits for both desks.\n"
        'If `stepped` is true, use `GET /await?until=tick&after=<round minus 1>` and then `GET /view`.\n'
        '`POST /step` with `{"round": <number>, "done": true}` finishes your part of the whole day. '
        "Stop after this call succeeds.\n"
        "You score 1 if every shipment is booked in its own round with the right carrier. Otherwise you score 0.\n"
    )


def extension_arm(inst, arm, max_text, max_messages):
    if arm not in ARMS:
        raise ValueError("unknown freight booking condition")
    if max_text < max(len(o["text"]) for o in inst["outages"]) or max_messages < len(inst["outages"]):
        raise ValueError("messaging budget cannot carry all outages")
    single = arm in ("solo", "solo-seam")
    scripted = not single and arm != "team"
    outages = [] if arm == "probe-quiet" else inst["outages"]
    sender = "carrier-board" if single else "p2"
    posts = [{"sender": sender, "send_round": d["round"] - 1, "delay": 1, "text": d["text"]}
             for d in inst["distractors"]]
    if arm in ("probe-control", "probe-trap", "hint", "solo-seam"):
        for outage in outages:
            send_round, delay = ((1, 0) if arm == "probe-control" else
                                 (outage["start"], 0) if arm == "solo-seam" else (outage["start"] - 1, 1))
            posts.append({"sender": sender, "send_round": send_round, "delay": delay, "text": outage["text"]})
    briefings = {"p1": {"role": "booking", "lanes": inst["lanes"]}}
    if arm == "solo":
        briefings["p1"]["outages"] = [o["text"] for o in outages]
    instructions = {"p1": booking_instruction(inst, arm, max_text, max_messages)}
    if not single:
        briefings["p2"] = {"role": "carrier", "outages": [o["text"] for o in outages] if not scripted else []}
        instructions["p2"] = carrier_instruction(inst, max_text, max_messages)
    params = {k: inst[k] for k in ("dose", "rounds", "batch", "shipments", "lanes")}
    params.update(outages=outages, posts=posts, answer=answer_key(inst, outages), primary=answer_key(inst, []))
    return {"params": params, "briefings": briefings, "instructions": instructions,
            "messaging": True, "push": True, "senders": ["carrier-board"] if single else [],
            "scripted_seats": ["p2"] if scripted else [], "scripted_policies": {"p2": "nop"} if scripted else {}}


def pair_difference(arm_a, arm_b, views_a, views_b):
    a, b = views_a["p1"], views_b["p1"]
    if arm_a == "hint":
        a, b = b, a
    return [] if dict(b, instruction=b["instruction"].replace(HINT + "\n", "", 1)) == a else [
        "the briefing pair differs by more than the protocol sentence"
    ]


def qa(task, views):
    params = task["params"]
    if task["condition"] != "solo":
        for outage in params["outages"]:
            for value in views["p1"].values():
                if outage["text"] in value:
                    yield "booking desk sees an outage before delivery"
    for shipment in params["shipments"]:
        if shipment["id"] in views["p1"]["instruction"]:
            yield "shipment data appears in the briefing rather than the view"
