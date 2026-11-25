"""House rules through size-capped shift notes."""

import math
import random
import re
from collections import Counter

FAMILY = "digest"
TITLE = "Finishing desk shifts"
SEATS = 2
ARMS = ("solo", "solo-seam", "team", "hint", "probe-writer-trap", "probe-writer-control",
        "probe-worker-trap", "probe-worker-control")
DOSES = {
    "canary": (4, 4, 1, 530, 350, 0, 12, 0, 8),
    "standard": (8, 6, 2, 880, 810, 20, 40, 5, 20),
    "heavy": (12, 8, 4, 1150, 1290, 20, 60, 7, 35),
}
OUTCOMES = ("N", "E", "S", "W", "P", "C", "X", "F", "Q", "hold")
CHECKS = {"credit": ("ok", "stop"), "press": ("free", "busy"),
          "freight": ("booked", "none"), "ink": ("ready", "short")}
PRODUCTS = ("BC", "FL", "PO", "BN", "PC", "BK", "EN", "LB")
DEFAULTS = {"rush": "no", "proof": "approved", "account": "standard", "stock": "house",
            "finish": "plain", "ship": "pickup", "due": "today", "ink": "process",
            "bind": "stapled", "pack": "loose"}


def seat_count(difficulty):
    return DOSES[difficulty][2] + 1


def rule_pool(rng, dose, products):
    thresholds = [rng.randrange(v // 2, v + v // 2 + 1, max(1, v // 10))
                  for v in (10000, 100, 5000, 500, 50, 50000)]
    a, b, c, d, e, f = thresholds
    ship = rng.choice(("courier", "delivery"))
    pool = [
        [("proof=pending", "hold"), ("account=key,rush=no", "P"), (f"qty>{a}", "Q"), ("due=later", "C")],
        [("account=new", "check:credit ok=home stop=C"), (f"qty<{b}", "C"), (f"ship={ship}", "hold"),
         (f"product={products[0]}", "X")],
        [("stock=custom", "X"), (f"product={products[3]}", "W"), ("rush=yes", "hold")],
        [(f"rush=yes,qty>{c}", "check:press free=home busy=X"),
         (f"product={products[2]}", "check:press free=home busy=Q")],
        [("ship=freight", "check:freight booked=F none=hold"), (f"qty<{d}", "C"), ("account=key", "F")],
        [("finish=laminate", "S"), ("rush=yes", "X"), (f"product={products[0]}", "Q")],
        [("finish=foil", "X"), ("due=later", "F"), ("account=key", "P")],
        [("ink=spot", "check:ink ready=home short=hold"), ("account=key", "P")],
        [("bind=perfect", "X"), (f"qty<{e}", "E"), ("rush=yes", "Q")],
        [("pack=boxed", "Q"), ("due=later", "F")],
        [(f"qty>{f}", "F"), (f"product={products[6] if len(products) > 6 else 'EN'}", "S")],
        [("account=gov", "check:credit ok=home stop=hold"),
         (f"product={products[7] if len(products) > 7 else 'LB'}", "C")],
    ]
    # Give each shop an independent assignment of physical lane and desk codes.
    destinations = list(OUTCOMES[:-1])
    shuffled = destinations.copy()
    rng.shuffle(shuffled)
    mapping = dict(zip(destinations, shuffled, strict=True)) | {"home": "home", "hold": "hold"}
    result = []
    for i, rule in enumerate(pool[:DOSES[dose][0]]):
        depth = 2 + (dose != "canary" and i in (0, 1, 2, 4))
        depth += (dose == "heavy" and i in (5, 6, 8)) + (dose == "heavy" and i in (0, 1))
        clauses = []
        for condition, action in rule[:depth]:
            if action.startswith("check:"):
                check, *options = action.split()
                action = " ".join([check, *(f"{k}={mapping[v]}" for k, v in (p.split("=") for p in options))])
            else:
                action = mapping[action]
            clauses.append([condition, action])
        result.append({"id": i + 1, "clauses": clauses})
    rng.shuffle(result)
    return result


def matches(job, condition):
    for clause in condition.split(","):
        field, op, value = re.fullmatch(r"(\w+)([=<>])(\w+)", clause).groups()
        if field not in job:
            return False
        if op == "=" and str(job[field]) != value:
            return False
        if op == ">" and not job[field] > int(value):
            return False
        if op == "<" and not job[field] < int(value):
            return False
    return True


def decision(rules, job):
    for rule in rules:
        clauses = rule["clauses"]
        if matches(job, clauses[0][0]):
            level = 0
            for condition, _ in clauses[1:]:
                if not matches(job, condition):
                    break
                level += 1
            return rule["id"], level, clauses[level][1]
    return 0, 0, "home"


def resolve(action, home, job, replies):
    check = None
    if action.startswith("check:"):
        first, *options = action.split()
        check = first[6:]
        action = dict(o.split("=") for o in options)[replies[check]]
    return [check] if check else [], home[job["product"]] if action == "home" else action


def note_text(home, rules):
    lines = ["home " + " ".join(f"{k}={v}" for k, v in home.items())]
    lines.extend("; ".join(f"{condition}=>{action}" for condition, action in rule["clauses"]) for rule in rules)
    return "\n".join(lines)


def card_text(home, rules):
    lines = ["home " + " ".join(f"{k}={v}" for k, v in home.items())]
    for rule in rules:
        prefix = []
        for condition, action in rule["clauses"]:
            prefix.append(condition)
            lines.append(f"{','.join(prefix)}=>{action}")
    return "\n".join(lines)


def parse_note(text, card=False):
    lines = text.strip().splitlines()
    head, *pairs = lines[0].split()
    if head != "home":
        raise ValueError("missing home table")
    home = dict(p.split("=") for p in pairs)
    rules = []
    for i, line in enumerate(lines[1:]):
        if not line.strip():
            continue
        clauses = []
        for clause in line.split(";"):
            condition, action = (part.strip() for part in clause.split("=>"))
            clauses.append([condition, action])
        rules.append({"id": i + 1, "clauses": clauses})
    if card:
        rules.sort(key=lambda r: -len(r["clauses"][0][0].split(",")))
    return home, rules


def make_job(rng, products, defaults, rules, home, target, reply_choice=None):
    rid, depth = target
    rule = next((r for r in rules if r["id"] == rid), None)
    condition = [] if rule is None else [part for c, _ in rule["clauses"][:depth + 1] for part in c.split(",")]
    for _ in range(1000):
        job = defaults | {"product": rng.choice(products), "qty": rng.randint(200, 3000)}
        if rng.random() < 0.25:
            job["account"] = "key"
        for part in condition:
            field, op, value = re.fullmatch(r"(\w+)([=<>])(\w+)", part).groups()
            job[field] = value if op == "=" else (int(value) + rng.randint(1, 2000) if op == ">"
                                                   else rng.randint(1, int(value) - 1))
        fitting = [r for r in rules if matches(job, r["clauses"][0][0])]
        if len(fitting) != bool(rid):
            continue
        actual_rid, actual_depth, action = decision(rules, job)
        if (actual_rid, actual_depth) != target:
            continue
        replies = {k: rng.choice(v) for k, v in CHECKS.items()}
        if reply_choice is not None:
            replies[reply_choice[0]] = reply_choice[1]
        required, outcome = resolve(action, home, job, replies)
        if rid and not required and outcome == home[job["product"]]:
            continue
        if depth and any(resolve(a, home, job, replies)[1] == outcome and
                         set(required) <= set(resolve(a, home, job, replies)[0])
                         for _, a in rule["clauses"][:depth]):
            continue
        return {"job": job, "replies": replies, "cell": f"{rid}.{depth}",
                "checks": required, "outcome": outcome}
    raise ValueError(f"cannot produce distinguishable job for {target}")


def batch(rng, products, defaults, rules, home, cells, size, shift, late=0):
    main, rare = [], []
    for cell in cells:
        rid, depth = cell
        action = next((r["clauses"][depth][1] for r in rules if r["id"] == rid), "home")
        if not depth and action.startswith("check:"):
            check = action.split()[0][6:]
            main.extend(make_job(rng, products, defaults, rules, home, cell, (check, reply)) for reply in CHECKS[check])
        else:
            (rare if depth else main).append(make_job(rng, products, defaults, rules, home, cell))
    while len(main) + len(rare) < size:
        cell = rng.choice([c for c in cells if not c[1]])
        main.append(make_job(rng, products, defaults, rules, home, cell))
    rng.shuffle(main)
    rng.shuffle(rare)
    # Keep exception jobs behind a prefix of routine work, without changing IDs in paired builds.
    if late:
        before = min(late, len(main))
        tail = main[before:] + rare
        rng.shuffle(tail)
        jobs = main[:before] + tail
    else:
        jobs = main + rare
        rng.shuffle(jobs)
    for i, row in enumerate(jobs):
        row["job"]["id"] = f"J{shift + 1}-{i + 1:02d}-{rng.randrange(100, 1000)}"
    return jobs


def generate(seed, instance, n_seats, difficulty):
    count, n_products, hops, cap, card_cap, load, final_size, common, late = DOSES[difficulty]
    if n_seats != hops + 1:
        raise ValueError(f"{difficulty} requires {hops + 1} seats")
    rng = random.Random(f"digest:{seed}:{instance}:{difficulty}")
    products = list(PRODUCTS[:n_products])
    rng.shuffle(products)
    defaults = dict(list(DEFAULTS.items())[:4] if count == 4 else DEFAULTS)
    if count == 8:
        defaults = {k: v for k, v in defaults.items() if k not in ("bind", "pack")}
    for _ in range(100):
        home = {product: rng.choice(OUTCOMES[:4]) for product in products}
        rules = rule_pool(rng, difficulty, products)
        cells = [(0, 0), *((r["id"], depth) for r in rules for depth in range(len(r["clauses"])))]
        try:
            final = batch(rng, products, defaults, rules, home, cells, final_size, hops, late)
        except ValueError:
            continue
        totals = Counter(row["cell"] for row in final)
        weights = Counter()
        for row in final:
            weights[row["outcome"]] += 1 / totals[row["cell"]] / len(totals)
        if max(weights.values()) > 0.2 + 1e-9:
            continue
        early = [(0, 0), *((i, 0) for i in range(1, common + 1))]
        batches = [batch(rng, products, defaults, rules, home, early, load + (10 if count == 12 and i == 0 else 0), i)
                   if load else [] for i in range(hops)] + [final]
        reference = note_text(home, rules)
        card = card_text(home, rules)
        # Preserve the stated caps as a lower bound; long generated paths require more room.
        cap = max(cap, math.ceil(len(reference) * 1.2))
        card_cap = max(card_cap, math.ceil(len(card) * 1.1))
        return {"seats": [f"p{i + 1}" for i in range(hops + 1)], "home": home, "rules": rules,
                "batches": batches, "products": products, "fields": list(defaults), "cap": cap,
                "card_cap": card_cap, "reference": reference, "card": card, "difficulty": difficulty}
    raise ValueError("unable to generate balanced distinct cells")
