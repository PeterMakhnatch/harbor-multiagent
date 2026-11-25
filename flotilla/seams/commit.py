"""Freight booking with mid-day carrier notices."""

import random

FAMILY = "commit"
TITLE = "Book the day's outbound freight"
SEATS = 2
ARMS = ("solo", "solo-seam", "team", "probe-control", "probe-trap", "hint", "probe-quiet")
TWINS = (("probe-control", "probe-trap"), ("probe-control", "probe-quiet"), ("probe-control", "team"))
DOSES = {"canary": (4, 1), "standard": (8, 2), "heavy": (10, 4)}
HINT = "Before you book a round, read the `new_messages` of the reply that opened that round; an outage can start in the middle of the day."

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
    outages = [{"carrier": carrier, "start": start, "text": outage_text(carrier, start)}
               for carrier, start in zip(rng.sample(primaries, len(starts)), starts)]
    slots = [None] * n
    for outage in reversed(outages):
        boundary = (outage["start"] - 1) * batch
        candidates = [lane for lane in lanes if lane["primary"] == outage["carrier"]]
        early = rng.choice([i for i in range(boundary) if slots[i] is None])
        late = rng.sample([i for i in range(boundary, n) if slots[i] is None], 1 if difficulty == "canary" else 3)
        for i in [early, *late]:
            slots[i] = rng.choice(candidates)
    ids = rng.sample(range(100, 1000), n)
    shipments = [{"id": f"S-{ids[i]}", "round": i // batch + 1,
                  **{k: v for k, v in (lane or rng.choice(lanes)).items() if k not in ("primary", "backup")}}
                 for i, lane in enumerate(slots)]
    distractors = []
    if difficulty != "canary":
        delivery_rounds = [2, starts[0] + 1] if difficulty == "standard" else sorted(rng.sample(range(2, rounds + 1), 6))
        for i, r in enumerate(delivery_rounds):
            text = (outage_text(other, r) if difficulty == "heavy" and i == 2 else
                    f"{rng.choice(carriers[:6])} pickups at dock {rng.randint(1, 9)} run {rng.randint(5, 50)} minutes late today.")
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
