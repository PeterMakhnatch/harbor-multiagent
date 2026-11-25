"""House rules through size-capped shift notes."""

import random
import re
from collections import Counter
from typing import NamedTuple

FAMILY = "digest"
TITLE = "Finishing desk shifts"
SEATS = 2
ARMS = ("solo", "solo-seam", "team", "hint", "probe-writer-trap", "probe-writer-control",
        "probe-worker-trap", "probe-worker-control")


class Dose(NamedTuple):
    rules: int
    products: int
    hops: int
    cap: int
    card_cap: int
    load: int
    final_size: int
    common: int
    late: int


DOSES = {
    "canary": Dose(4, 4, 1, 530, 350, 0, 12, 0, 8),
    "standard": Dose(8, 6, 2, 880, 810, 20, 40, 5, 20),
    "heavy": Dose(12, 8, 4, 1150, 1290, 20, 60, 7, 35),
}
OUTCOMES = ("N", "E", "S", "W", "P", "C", "X", "F", "Q", "hold")
CHECKS = {"credit": ("ok", "stop"), "press": ("free", "busy"),
          "freight": ("booked", "none"), "ink": ("ready", "short")}
PRODUCTS = ("BC", "FL", "PO", "BN", "PC", "BK", "EN", "LB", "CA", "ST", "MB", "BR")
DEFAULTS = {"rush": "no", "proof": "approved", "account": "standard", "stock": "house",
            "finish": "plain", "ship": "pickup", "due": "today", "ink": "process",
            "bind": "stapled", "pack": "loose"}


def seat_count(difficulty):
    return DOSES[difficulty].hops + 1


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
        field, op, value = re.fullmatch(r"(\w+)([=<>])([\w-]+)", clause).groups()
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
    lines.append("First matching rule wins; later clauses on its line are nested exceptions.")
    for rule in rules:
        clauses = [f"{re.sub(r'([=<>])', r' \1 ', c).replace(',', ', ')} => {a}" for c, a in rule["clauses"]]
        lines.append("; ".join(clauses))
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
    if any(lane not in OUTCOMES[:4] for lane in home.values()):
        raise ValueError("invalid home lane")
    rules = []
    for i, line in enumerate(lines[1:]):
        if not line.strip() or (not card and line.startswith("First matching rule wins")):
            continue
        clauses = []
        for clause in line.split(";"):
            condition, action = (part.strip() for part in clause.split("=>"))
            condition = re.sub(r"\s+", "", condition)
            if card:
                for predicate in condition.split(","):
                    match = re.fullmatch(r"(\w+)=([\w-]+)|qty([<>])(\d+)", predicate)
                    if match is None:
                        raise ValueError("invalid card condition")
                validate_card_outcome(action)
            clauses.append([condition, action])
        rules.append({"id": i + 1, "clauses": clauses})
    if card:
        rules.sort(key=lambda r: -len(r["clauses"][0][0].split(",")))
    return home, rules


def validate_card_outcome(action):
    allowed = (*OUTCOMES, "home")
    if action in allowed:
        return
    first, *choices = action.split()
    name = first.removeprefix("check:")
    if not first.startswith("check:") or name not in CHECKS:
        raise ValueError("invalid card outcome")
    mapping = dict(p.split("=") for p in choices)
    if set(mapping) != set(CHECKS[name]) or any(v not in allowed for v in mapping.values()):
        raise ValueError("invalid check replies")


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
    products = rng.sample(PRODUCTS, n_products)
    defaults = dict(list(DEFAULTS.items())[:4] if count == 4 else DEFAULTS)
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
        if len(reference) > cap or len(card) > card_cap:
            raise ValueError("reference note does not fit the dose cap")
        return {"seats": [f"p{i + 1}" for i in range(hops + 1)], "home": home, "rules": rules,
                "batches": batches, "products": products, "fields": list(defaults), "cap": cap,
                "card_cap": card_cap, "reference": reference, "card": card, "difficulty": difficulty}
    raise ValueError("unable to generate balanced distinct cells")


def action_prose(action):
    if action == "hold":
        return "Hold the job."
    if action == "home":
        return "Send the job home."
    if action.startswith("check:"):
        check, *choices = action.split()
        return f"Check {check[6:]} before the final action. " + " ".join(
            f"If the reply is {reply}, {action_prose(outcome)[0].lower() + action_prose(outcome)[1:]}"
            for reply, outcome in (item.split("=") for item in choices))
    return f"Send the job to {action}."


def condition_prose(condition):
    return " and ".join(re.sub(r"([=<>])", lambda m: {"=": " is ", ">": " is over ", "<": " is under "}[m[0]],
                               part) for part in condition.split(","))


def render_manual(home, rules):
    lines = ["# Finishing desk manual", "",
             ("The shop runs a print finishing service for walk-in and trade customers. "
              "The office maintains the equipment and sets the routing rules for this edition."),
             "The home lane is the normal destination for each product."]
    lines.extend(f"Product {p} has home lane {lane}." for p, lane in home.items())
    lines.extend(["", ("Apply the first numbered rule that fits the ticket. "
                       "Within that rule, apply its deepest matching exception. Use only that clause's action and checks. "
                       "Each exception adds its conditions to all preceding conditions in that rule. "
                       "If no rule applies, send the job home."), ""])
    for index, rule in enumerate(rules, 1):
        lines.append(f"Rule {index}.")
        for depth, (condition, action) in enumerate(rule["clauses"]):
            label = "Main" if not depth else f"Exception {depth}"
            lines.append(f"{label}: When {condition_prose(condition)}, {action_prose(action)}")
            lines.append("This is a standing rule for the desk, not a recommendation for a particular order. "
                         "The office reviews these arrangements when it updates the manual.")
        lines.append("")
    lines.append("The desk is responsible for routing tickets. Cleaning, payroll and equipment repair belong "
                 "to separate office teams. This paragraph adds no routing rule.")
    return "\n".join(lines)


def parse_action(sentence):
    if sentence == "Hold the job.":
        return "hold"
    if sentence == "Send the job home.":
        return "home"
    if match := re.fullmatch(r"Send the job to ([A-Z])\.", sentence):
        return match[1]
    match = re.fullmatch(r"Check (\w+) before the final action\. (.+)", sentence)
    replies = re.findall(r"If the reply is (\w+), ([^.]+\.)", match[2])
    return " ".join([f"check:{match[1]}", *(f"{k}={parse_action(v[0].upper() + v[1:])}" for k, v in replies)])


def parse_manual(text):
    home = dict(re.findall(r"Product (\w+) has home lane ([A-Z])\.", text))
    rules = []
    for line in text.splitlines():
        if match := re.fullmatch(r"Rule (\d+)\.", line):
            rules.append({"id": int(match[1]), "clauses": []})
        elif match := re.fullmatch(r"(?:Main|Exception \d+): When ([^,]+), (.+)", line):
            conditions = match[1].replace(" is over ", ">").replace(" is under ", "<").replace(" is ", "=")
            rules[-1]["clauses"].append([conditions.replace(" and ", ","), parse_action(match[2])])
    return home, rules


def extension_arm(inst, arm, max_text, max_messages):
    if arm not in ARMS:
        raise ValueError("unknown arm")
    seats, batches = inst["seats"], inst["batches"]
    card = arm.startswith("probe-writer-")
    if arm == "solo":
        seats, batches = seats[:1], batches[-1:]
    elif card:
        seats, batches = seats[:2], [batches[0], batches[-1]]
    elif arm == "probe-worker-control":
        final = batches[-1]
        batches = batches[:-1] + [sorted(final, key=lambda row: row["cell"].endswith(".0"))]
    cap = inst["card_cap"] if card else inst["cap"]
    if arm == "probe-writer-control":
        cap *= 4
    roles = {s: "worker" if i == len(seats) - 1 else "planner" if i == 0 else "relay"
             for i, s in enumerate(seats)}
    manual = render_manual(inst["home"], inst["rules"])
    briefings, instructions = {}, {}
    public = public_briefing(inst["fields"])
    for i, seat in enumerate(seats):
        role = roles[seat]
        own = {"role": role, "jobs": len(batches[i])}
        if card:
            own["format"] = "card"
        if i == 0:
            own["manual"] = manual
        briefings[seat] = own
        instructions[seat] = public + role_briefing(own, cap, arm) + ("\n\n" + manual if i == 0 else "")
    scripted = seats[1:] if card else seats[:-1] if arm.startswith("probe-worker-") else []
    return {"briefings": briefings, "instructions": instructions, "initial_active": seats[:1],
            "messaging": False, "scripted_seats": scripted,
            "params": {"batches": dict(zip(seats, batches, strict=True)), "roles": roles, "cap": cap}}


def public_briefing(fields):
    choices = {"finish": "finish (plain, laminate or foil)", "ship": "ship (pickup, delivery, courier or freight)",
               "due": "due (today or later)", "ink": "ink (process or spot)", "bind": "bind (stapled or perfect)",
               "pack": "pack (loose or boxed)"}
    extra = ", ".join(choices[f] for f in fields if f in choices)
    accounts = "standard, key, new or gov" if "pack" in fields else "standard, key or new"
    return (
        "You work the finishing desk at Harbor Print. Each ticket has an ID, product code and quantity "
        f"(`qty`). It also has rush (yes or no), proof (approved or pending), account ({accounts}), "
        "and stock (house or custom)"
        + (f", and {extra}" if extra else "") + ".\n"
        "Every job must end with one final action: send it to a lane or desk (N, E, S, W, P, C, X, F, Q), "
        "or hold it. Some jobs need a check first. Check systems are credit (ok or stop), press (free or busy), "
        "freight (booked or none) and ink (ready or short). Extra checks are allowed. Each final action is permanent.\n"
        "Use `$WORLD_URL` with `Authorization: Bearer $SEAT_TOKEN`. For POST use `Content-Type: application/json`.\n"
        "If your shift has not started, wait until GET /whoami returns `active` equal to `active`. "
        "GET /work returns your role, up to 5 open jobs, and your incoming note on the first read. "
        "POST /check with `{\"job\":\"example\",\"what\":\"credit\"}` returns the check reply for an open job on your page. "
        "POST /act with `{\"actions\":[{\"job\":\"example\",\"do\":\"send\",\"to\":\"C\"}]}` sends one job. "
        "For a hold action, use `\"do\":\"hold\"` and omit `to`. Replace the example ID with an open job ID. "
        "You can send 1 to 5 actions per call, only for your current page. GET /work then shows the next jobs. "
        "Work until GET /work returns `done: true`. GET /note shows your incoming note again at any time.\n\n")


def role_briefing(own, cap, arm):
    role = own["role"]
    if role == "worker" and "manual" in own:
        return "You work the desk alone today. Use the manual below for all your jobs. No handover note is needed."
    if role == "planner":
        text = "You are the day planner. The manual below is yours alone. Work your own jobs first."
    else:
        address = ("You left yourself a note on your last shift." if arm == "solo-seam" else
                   "The previous shift left you a note.")
        text = (f"You work this desk shift. {address} The note is your only copy of the house rules. "
                "You cannot see the manual or ask questions. Work every job. "
                "If no rule in the note applies, send the job to its home lane. If no home lane is known, hold it.")
    if role == "worker":
        return text
    if arm == "solo-seam":
        text += " Write a note for yourself on the next shift; you will lose this shift's context."
    elif own.get("format") == "card":
        text += (" Write a rule card. Line 1 is `home` followed by product=lane pairs separated by spaces. "
                 "Each further line has conditions joined by commas, then `=>`, then an outcome. "
                 "A condition is field=value, qty>N or qty<N. An outcome is a destination code, `hold`, `home`, "
                 "or `check:<what>` followed by reply=outcome pairs separated by spaces. "
                 "The matching line with the most conditions decides; on a tie, the earlier line wins. "
                 "A job matching no line goes home. The desk terminal applies your card exactly. "
                 "If it cannot parse the card, it holds the jobs.")
    else:
        text += " Write the handover note for the next shift's worker. That worker has only your note and its jobs."
    text += f"\nThe note can hold at most {cap} characters, including newlines."
    if arm == "hint":
        text += ("\nRules that seldom apply matter as much as common ones: keep every rule and every exception "
                 "in the note, and cut background and reasons first.")
    return text + ("\nPOST /note/check with `{\"text\":\"...\"}` measures its size without sending it. "
                   "POST /note/seal with the same body sends it once and ends your shift. "
                   "A note over the cap is rejected. Finish your own jobs before sealing.")


def qa(task, views):
    import json

    final = task["params"]["batches"][task["seats"][-1]]
    ids = [r["job"]["id"] for r in final]
    for seat, view in views.items():
        own = json.loads(view["env.SEAT_BRIEFING"])
        if ("manual" in own) != (seat == task["seats"][0]):
            yield f"{seat}: inappropriate manual access"
        if any(job in view["instruction"] or job in view["env.SEAT_BRIEFING"] for job in ids):
            yield f"{seat}: leaked final ticket"
