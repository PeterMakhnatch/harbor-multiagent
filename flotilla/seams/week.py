"""Kestrel Freight: deterministic dispatch work and four context boundaries."""

import random

FAMILY = "week"
TITLE = "Dispatch week at Kestrel Freight and Fulfilment"
SEATS = {"canary": 4, "standard": 5}
ARMS = ("solo", "team", "team-fullrecord", "hint")
ARM_WORDS = ("team-fullrecord", "naive-digest", "naive-interface", "naive-commit", "naive-handoff")
CARRIERS = ("VIR", "BRW", "OST", "PEL")
FORMS = ("C/Z", "Z/C", "C.Z", "Z.C")
CUSTOMERS = ("HAL", "MOR", "GEN")
ZONES = tuple(f"Z{i}" for i in range(1, 7))
FLAGS = ("cold", "haz3", "frag", "ret")
SEAMS = ("digest", "interface", "commit", "handoff", "own")
CANARY = "74c42d4c-e2e8-4b24-9b49-7ecb9680d4ce"


def rate_key(carrier, zone, form):
    return form.replace("C", carrier).replace("Z", zone)


def rule_key(order):
    return "/".join((order["customer"], order["zone"], order["flag"]))


def parse_card(text, cap):
    if not isinstance(text, str) or len(text.encode("utf-8")) > cap:
        return None
    rows = {}
    for line in text.splitlines():
        key, sep, carrier = line.partition(">")
        parts = key.split("/")
        if (not sep or len(parts) != 3 or parts[0] not in CUSTOMERS or parts[1] not in ZONES
                or parts[2] not in FLAGS or carrier not in CARRIERS or key in rows):
            return None
        rows[key] = carrier
    return rows


def encode_card(rules):
    return "\n".join(f"{rule['key']}>{rule['carrier']}" for rule in rules)


def choices(order, card):
    required = card.get(rule_key(order))
    return [c for c in CARRIERS if c in order["eligible"] and (required is None or c == required)]


def public_order(order):
    return {key: order[key] for key in ("id", "hub", "customer", "zone", "flag", "kg", "eligible",
                                          "release", "amend_close", "departure")}


def desk_for(hub, round_no, shift):
    return "S" if hub == "South" else "N1" if round_no <= shift else "N2"


def schedule(difficulty):
    if difficulty == "canary":
        return [("North", 1, 6, True)] * 2 + [("North", r, 12, False) for r in (7, 8) for _ in range(3)]
    rows = []
    for day in range(5):
        start = day * 12 + 1
        if day == 2:
            rows.extend(("North", r, 30, True) for r in (23, 24, 25) for _ in range(3 if r != 25 else 2))
            rows.extend(("South", r, 36, False) for r in (25, 26) for _ in range(2))
        else:
            rows.extend((hub, r, start + 11, False) for hub in ("North", "South")
                        for r in (start, start + 3) for _ in range(3))
    return sorted(rows, key=lambda row: (row[1], row[0]))


def generate(seed, instance, n_seats, difficulty):
    count = SEATS[difficulty]
    if n_seats is not None and n_seats != count:
        raise ValueError(f"{difficulty} requires {count} seats")
    rng = random.Random(f"week:{seed}:{instance}:{difficulty}")
    small = difficulty == "canary"
    rounds, shift, day_length = (12, 6, 6) if small else (60, 30, 12)
    card_cap, note_cap, lines = (160, 200, 6) if small else (300, 300, 10)
    keys = [f"{c}/{z}/{f}" for c in CUSTOMERS for z in ZONES for f in FLAGS]
    rng.shuffle(keys)
    n_rules = 4 if small else 12
    rules = [{"key": key, "carrier": "PEL", "aside": i >= n_rules // 2}
             for i, key in enumerate(keys[:n_rules])]
    kinds = (["digest"] + ["interface"] * 2 + ["commit"] + ["own"] * 2 if small else
             ["digest"] * 6 + ["interface"] * 24 + ["commit"] * 6 + ["own"] * 16)
    rng.shuffle(kinds)
    allocations = schedule(difficulty)
    ids = rng.sample(range(1000, 9999), len(allocations))
    refs = rng.sample(range(10000, 99999), len(allocations))
    orders = []
    digest_index = 0
    for index, (hub, release, departure, crossing) in enumerate(allocations):
        kind = "handoff" if crossing else kinds.pop()
        if kind == "digest":
            key = rules[n_rules // 2 + digest_index % (n_rules // 2)]["key"]
            digest_index += 1
        else:
            key = rng.choice(keys[n_rules:])
        customer, zone, flag = key.split("/")
        eligible = [rng.choice(("BRW",) if small else ("BRW", "OST"))] if kind == "interface" else ["VIR", "PEL"]
        callback = (shift + rng.randint(1, 5) if crossing else departure + rng.randint(2, 5))
        status = ("refused" if index % 2 else "damaged") if crossing and index % 3 else "delivered"
        orders.append({"id": f"K-{ids[index]}", "ref": f"CR-{refs[index]}", "hub": hub, "release": release,
                       "amend_close": release + 2, "departure": departure, "customer": customer, "zone": zone,
                       "flag": flag, "kg": rng.randint(40, 650), "eligible": eligible,
                       "amendment": "PEL" if kind == "commit" else None,
                       "callback": callback if callback <= rounds else None, "status": status})
    if kinds or len(orders) != (8 if small else 60):
        raise ValueError("order allocation mismatch")
    supports = {"VIR": ["C/Z"], "PEL": ["C/Z"], "BRW": rng.sample(list(FORMS[1:]), 2),
                "OST": ["C/Z", "Z.C"] if small else rng.sample(list(FORMS[1:]), 2)}
    questions = [{"id": f"Q{i + 1}", "order": o["id"], "round": max(2, o["release"] + 4)}
                 for i, o in enumerate(rng.sample(orders, 4 if small else 30))]
    if len(encode_card(rules).encode()) > card_cap:
        raise ValueError("card does not fit")
    return {"seats": ["A", "R", "N1", "N2"] + ([] if small else ["S"]), "rounds": rounds,
            "shift": shift, "day_length": day_length, "card_cap": card_cap, "note_cap": note_cap,
            "note_lines": lines, "rules": rules, "supports": supports, "orders": orders, "questions": questions,
            "v_ref": 100 * len(orders) + 10 * len(questions)}
