"""Instances for exact-fit catalog and order files."""

import random

FAMILY = "interface"
TITLE = "Replenishment batch"
SEATS = 2
ARMS = ("solo", "solo-seam", "team", "team-hint", "silent", "probe-control", "probe-trap-key",
        "probe-trap-unit", "probe-trap-key-hint")
TWINS = (("probe-control", "probe-trap-key"), ("probe-control", "probe-trap-unit"))
HINT = "Before you upload the order of a group, open that group's catalog in staging and use exactly its key form and its unit."
FORMS = ("SITE/ITEM", "ITEM/SITE", "SITE:ITEM", "ITEM:SITE", "SITE-ITEM", "ITEM-SITE")
CONVENTIONS = tuple((form, unit) for form in FORMS for unit in ("each", "case"))
DOSES = {"canary": (2, 1, 3, 6, 2, 0), "standard": (3, 4, 10, 15, 16, 2),
         "heavy": (5, 8, 15, 20, 56, 12)}


def key_for(row, convention):
    return convention[0].replace("SITE", row["site"]).replace("ITEM", row["item"])


def encode(rows, values, convention, kind):
    field = "price_cents" if kind == "catalog" else "qty"
    result = []
    for row, value in zip(rows, values, strict=True):
        if kind == "order" and value <= 0:
            continue
        if convention[1] == "case":
            value = value * row["case"] if kind == "catalog" else value // row["case"]
        result.append({"key": key_for(row, convention), field: value})
    return result


def support(rng, difficulty):
    if difficulty == "canary":
        separator = rng.choice("/:-")
        unit = rng.choice(("each", "case"))
        return [(f"SITE{separator}ITEM", unit), (f"ITEM{separator}SITE", unit)]
    while True:
        choices = rng.sample(CONVENTIONS, 4)
        if (len({form[0] for form, _ in choices}) == 2 and len({form[4] for form, _ in choices}) > 1
                and len({unit for _, unit in choices}) == 2):
            return choices


def make_events(rng, groups, target, steps, corrections):
    buckets = [[] for _ in range(steps - 1)]
    updates = []
    for group in groups:
        for row in group["rows"]:
            updates.append((group["name"], row))
    rng.shuffle(updates)
    corrected = {tuple((g, row["site"], row["item"])) for g, row in rng.sample(updates, corrections)}
    for i, (group, row) in enumerate(updates):
        event = {"kind": "count", "group": group, "site": row["site"], "item": row["item"],
                 "on_hand": row["on_hand"], "in_transit": row["in_transit"]}
        step = i % max(1, (steps - 1) // 2)
        if (group, row["site"], row["item"]) in corrected:
            buckets[step].append({**event, "on_hand": row["on_hand"] + row["case"]})
            later = rng.randrange(max(step + 1, (steps - 1) // 2), steps - 1)
            buckets[later].append({**event, "kind": "correction"})
        else:
            buckets[step].append(event)
    for group in groups:
        name = group["name"]
        last = max(i for i, bucket in enumerate(buckets) if any(event["group"] == name for event in bucket))
        if name == target:
            last = steps - 2
        buckets[last].append({"kind": "complete", "group": name})
    return buckets + [[]]


def generate(seed, instance, n_seats, difficulty):
    default, group_count, low, high, steps, corrections = DOSES[difficulty]
    n_seats = n_seats or default
    if not 2 <= n_seats <= group_count + 1:
        raise ValueError("Each catalog desk must own at least one supplier group")
    rng = random.Random(f"interface:{seed}:{instance}:{difficulty}")
    seats = [f"p{i}" for i in range(1, n_seats + 1)]
    buyer = rng.choice(CONVENTIONS)
    names = rng.sample([f"Supplier-{a}{b:02d}" for a in "ABCDEFGH" for b in range(100)], group_count)
    pairs = rng.sample([(f"{a}{i}", f"{b}{j}") for a in "ABCDEFGH" for i in range(10)
                        for b in "JKLMNPQRSTUVWXYZ" for j in range(10)], group_count * high)
    groups = []
    for index, name in enumerate(names):
        supported = support(rng, difficulty)
        saved = rng.choice([c for c in supported if c != buyer])
        rows = []
        for line in range(rng.randint(low, high)):
            site, item = pairs.pop()
            case = rng.randint(2, 15)
            need_cases = rng.randint(1, 9) if line == 0 else rng.randint(-2, 9)
            on_hand = rng.randint(3, 16) * case
            in_transit = rng.randint(0, 4) * case
            rows.append({"site": site, "item": item, "case": case, "price": rng.randint(75, 1299),
                         "target": on_hand + in_transit + need_cases * case, "on_hand": on_hand,
                         "in_transit": in_transit})
        rng.shuffle(rows)
        groups.append({"name": name, "desk": seats[1 + index % (n_seats - 1)], "support": supported,
                       "saved": saved, "rows": rows})
    target = rng.choice(names)
    form, unit = buyer
    separator = next(s for s in "/:-" if s in form)
    key_trap = (form.replace(separator, rng.choice([s for s in "/:-" if s != separator])), unit)
    if rng.choice((False, True)):
        a, b = form.split(separator)
        key_trap = (separator.join((b, a)), unit)
    unit_trap = (form, "case" if unit == "each" else "each")
    return {"seats": seats, "difficulty": difficulty, "groups": groups, "buyer": buyer, "target": target,
            "key_trap": key_trap, "unit_trap": unit_trap,
            "stream": make_events(rng, groups, target, steps, corrections)}
