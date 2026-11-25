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
