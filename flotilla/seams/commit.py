"""Freight booking with mid-day carrier notices."""

import random

FAMILY = "commit"
TITLE = "Book the day's outbound freight"
SEATS = 2
ARMS = ("solo", "solo-seam", "team", "probe-control", "probe-trap", "hint", "probe-quiet")
TWINS = (("probe-control", "probe-trap"), ("probe-control", "probe-quiet"))
PAIRS = (("probe-trap", "hint"), ("probe-trap", "team"))
ARM_WORDS = ARMS
DOSES = {"canary": (1, 4, 4), "standard": (2, 2, 3), "heavy": (3, 1, 2)}
ROUNDS = 6
BATCH = 4
HINT = "Before booking, check the notices received so far, including those in the shipment replies."

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
    updates, first, last = DOSES[difficulty]
    rounds, batch = ROUNDS, BATCH
    n = rounds * batch
    carriers = rng.sample(CARRIERS, 6)
    primaries, backups = carriers[:batch], carriers[batch:]
    origin = rng.choice(ORIGINS)
    lanes = [{"origin": origin, "destination": dest, "load": "dry", "primary": carrier,
              "backup": rng.choice(backups)}
             for carrier, dest in zip(primaries, rng.sample(CITIES, batch), strict=True)]
    rng.shuffle(lanes)
    slots = [lane for _ in range(rounds) for lane in rng.sample(lanes, batch)]
    ids = rng.sample(range(100, 1000), n)
    shipments = [{"id": f"S-{ids[i]}", "round": i // batch + 1,
                  **{k: v for k, v in lane.items() if k not in ("primary", "backup")}}
                 for i, lane in enumerate(slots)]
    chosen = rng.sample(primaries, updates)
    outages = []
    for carrier in chosen:
        start = rng.randint(2, rounds)
        outages.append({"carrier": carrier, "start": start, "index": rng.randint(first, last),
                        "text": outage_text(carrier, start)})
    distractors = [
        {"round": r, "text": f"Dock {rng.randint(1, 9)} pickups run {rng.randint(5, 50)} minutes late today."}
        for r in (2, 4, 6)
    ]
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
        lines.append("These are all the outages today:")
        lines.extend(f"- {o['text']}" for o in inst["outages"])
    elif arm == "solo-seam":
        lines.append("The carrier board knows the outages and their start rounds.")
    else:
        lines.append("The carrier desk knows the outages and their start rounds.")
    if arm != "solo":
        lines.append("The dispatch mail service releases the report as notices during the day.")
    lines.extend([
        "The yard board can also send notices. These use the same message channel.",
        "Only an outage that affects a lane changes its booking. Other notices need no action.",
        "Notices are read-only; there is no direct chat or question route.",
        "Each desk can make at most 1000 world calls.",
        "",
        "Use $WORLD_URL with the header `Authorization: Bearer $SEAT_TOKEN`.",
        "For POST requests use `Content-Type: application/json`.",
        "Start with `GET /view`. It gives the current `round`, `rounds`, `bookings` and all notices so far in `messages`.",
        "`rounds` maps round numbers to shipments. The keys are strings.",
        f"The dispatch mail service handles at most {len(inst['outages'])} outage notices today.",
    ])
    lines.append("Each shipment is an ID. `GET /shipment?id=<id>` gives its origin, destination and load.")
    lines.append("Only current-round shipments can be inspected. Inspect every shipment of the round before booking.")
    lines.extend([
        ('`POST /step` with `{"round": <number>, "bookings": [{"shipment": "<id>", "carrier": "<name>"}]}` '
         "books the listed shipments and ends the round. Any remaining unbooked shipments are lost."),
        "You can instead book some shipments with `POST /book` using the same body, without ending the round.",
        "Do not book any shipment twice. Use an empty bookings list on `/step` if all shipments are already booked.",
        "The reply to `/step` gives the current view.",
    ])
    if arm not in ("solo", "solo-seam"):
        lines.extend([
            "Round 2 cannot open until the carrier desk submits its report for the day.",
            "The report can be submitted before round 1 ends; its notices can arrive much later.",
            "If `stepped` is true, use `GET /await?until=tick&after=0` to wait for round 2, then `GET /view`.",
            "If the wait returns in round 1, wait again.",
        ])
    lines.extend([
        "Any reply can carry new notices in `new_messages`. Each outage notice specifies its first affected round.",
        "Message field `t` is the world tick (round minus 1), not the start round of an outage.",
        "When `finished` is true or the world reports that play has ended, stop.",
    ])
    if arm == "hint":
        lines.append(HINT)
    lines.append("Every shipment must be booked with the right carrier in its own round for full success.")
    return "\n".join(lines) + "\n"


def carrier_instruction(inst, max_text, max_messages):
    notices = "\n".join(f"- {o['text']}" for o in inst["outages"])
    return (
        f"You are the carrier desk. The booking desk books shipments in rounds 1 to {inst['rounds']}.\n"
        "It books the primary carrier unless that carrier is out of service in that round, then the backup.\n"
        f"Only you know these outages:\n{notices}\n"
        "Submit your complete report during round 1. The dispatch mail service releases each notice during the day.\n"
        "The booking desk can inspect and book round 1 while you prepare the report. Round 2 waits for your report.\n"
        "Use $WORLD_URL with the header `Authorization: Bearer $SEAT_TOKEN`.\n"
        "Each desk can make at most 1000 world calls.\n"
        "For POST requests use `Content-Type: application/json`.\n"
        '`POST /relay` with `{"notices": [{"carrier": "<name>", "start": <first affected round>}]}` submits the report.\n'
        "Include each outage once, with its exact carrier name and start round. A successful submission is final.\n"
        "Stop after this call succeeds. There is no other step or direct chat.\n"
        "Full success requires a complete and correct report and every shipment booked with the right carrier.\n"
    )


def extension_arm(inst, arm, max_text, max_messages):
    if arm not in ARMS:
        raise ValueError("unknown freight booking condition")
    messages = [o["text"] for o in inst["outages"]] + [d["text"] for d in inst["distractors"]]
    if max_text < max(map(len, messages)) or max_messages < max(len(inst["outages"]), len(inst["distractors"])):
        raise ValueError("messaging budget cannot carry all notices")
    single = arm in ("solo", "solo-seam")
    scripted = not single and arm != "team"
    outages = [] if arm == "probe-quiet" else inst["outages"]
    posts = [{"sender": "yard-board", "round": d["round"], "text": d["text"]} for d in inst["distractors"]]
    if arm == "probe-control":
        posts.extend({"sender": "dispatch-mail", "round": 1, "text": o["text"]} for o in outages)
    queued = outages if arm in ("probe-trap", "hint", "solo-seam") else []
    briefings = {"p1": {"role": "booking", "lanes": inst["lanes"], "max_outages": len(inst["outages"])}}
    if arm == "solo":
        briefings["p1"]["outages"] = [o["text"] for o in outages]
    instructions = {"p1": booking_instruction(inst, arm, max_text, max_messages)}
    if not single:
        briefings["p2"] = {"role": "carrier", "outages": [
            {"carrier": o["carrier"], "start": o["start"]} for o in outages
        ] if not scripted else []}
        instructions["p2"] = carrier_instruction(inst, max_text, max_messages)
    params = {k: inst[k] for k in ("dose", "rounds", "batch", "shipments", "lanes")}
    params.update(outages=outages, posts=posts, queued=queued, live_report=arm == "team",
                  answer=answer_key(inst, outages), primary=answer_key(inst, []))
    return {"params": params, "briefings": briefings, "instructions": instructions,
            "messaging": True, "push": True, "senders": ["yard-board", "dispatch-mail"],
            "scripted_seats": ["p2"] if scripted else [], "scripted_policies": {"p2": "nop"} if scripted else {}}


def pair_difference(arm_a, arm_b, views_a, views_b):
    a, b = views_a["p1"], views_b["p1"]
    if "team" in (arm_a, arm_b):
        return [] if a == b else ["the booking desk's briefing changes when the carrier desk is live"]
    if arm_a == "hint":
        a, b = b, a
    difference = HINT + "\n"
    if a["instruction"].count(difference) or b["instruction"].count(difference) != 1:
        return ["the protocol sentence is absent or repeated"]
    return [] if dict(b, instruction=b["instruction"].replace(difference, "", 1)) == a else [
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
