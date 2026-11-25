"""House rules through size-capped shift notes."""

import base64
import json
import random
import re
import zlib
from collections import Counter
from typing import NamedTuple

FAMILY = "digest"
TITLE = "Finishing desk shifts"
ARMS = ("solo", "solo-seam", "team", "team-fullrecord", "hint", "probe-writer-trap", "probe-writer-control",
        "probe-worker-trap", "probe-worker-control")
TWINS = (("probe-worker-trap", "probe-worker-control"),)
PAIRS = (("probe-writer-trap", "probe-writer-control"), ("team", "hint"), ("team", "solo-seam"))
ARM_WORDS = ("digest", *[arm for arm in ARMS if "-" in arm])
SEAM = "A capped note must carry standing rules through successive revisions across fresh contexts."
VERSION = 4
BASE_LEVELS = ("canary", "standard", "heavy")
LEVELS = (*BASE_LEVELS, "standard-order", "heavy-order")
HINT = "When a replacement arrives, replace the old rule in your note instead of appending the new rule."
ORDER_HINT = "Keep the manual's rule numbers and listed order in your note, replacing each changed rule in place."
ORDER_FACTS = "Jobs can match more than one main rule. A replacement keeps the place of the rule it replaces."
TEAM_ADDRESS = "The previous shift left you a note."
SELF_ADDRESS = "You left yourself a note on your last shift."
TEAM_HANDOVER = ("Write the handover note for the next shift's worker. That worker gets only your note, its own "
                 "tickets and its own replacements; it sees today's replacements only if you include them.")
SELF_HANDOVER = ("Write a note for yourself on the next shift; you will lose this shift's context. "
                 "You get only your note, the next shift's tickets and its own replacements.")


class Dose(NamedTuple):
    rules: int
    products: int
    hops: int
    load: int
    final_size: int
    late: int


DOSES = {
    "canary": Dose(3, 4, 2, 4, 24, 12),
    "standard": Dose(4, 4, 3, 4, 24, 12),
    "heavy": Dose(5, 4, 4, 4, 24, 12),
    "standard-order": Dose(4, 4, 3, 4, 24, 12),
    "heavy-order": Dose(5, 4, 4, 4, 24, 12),
}
SEATS = {name: dose.hops + 1 for name, dose in DOSES.items()}
OUTCOMES = ("N", "E", "S", "W", "P", "C", "X", "F", "Q", "hold")
WRONG_SEND_COST = 0.01
CHANCE_CEILING = 0.15
CHECKS = {"credit": ("ok", "stop"), "press": ("free", "busy"),
          "freight": ("booked", "none"), "ink": ("ready", "short")}
PRODUCTS = ("BC", "FL", "PO", "BN", "PC", "BK", "EN", "LB", "CA", "ST", "MB", "BR")
DEFAULTS = {"rush": "no", "proof": "approved", "account": "standard", "stock": "house",
            "finish": "plain", "ship": "pickup", "due": "today", "ink": "process",
            "bind": "stapled", "pack": "loose"}


def seat_count(difficulty):
    return DOSES[difficulty].hops + 1


def rule_pool(rng, dose, products):
    a, b, c = [rng.randrange(v // 2, v + v // 2 + 1, max(1, v // 10)) for v in (10000, 100, 5000)]
    ship = rng.choice(("courier", "delivery"))
    pool = [
        [("proof=pending", "hold"), ("account=key,rush=no", "P"), (f"qty>{a}", "Q")],
        [("account=new", "check:credit ok=home stop=C"), (f"qty<{b}", "C"), (f"ship={ship}", "hold")],
        [("stock=custom", "X"), (f"product={products[3]}", "W")],
        [(f"rush=yes,qty>{c}", "check:press free=home busy=X"),
         (f"product={products[2]}", "check:press free=home busy=Q")],
    ]
    # Give each shop an independent assignment of physical lane and desk codes.
    destinations = list(OUTCOMES)
    if dose.rules == 3:
        pool[2].append(("pack=boxed", "C"))
        pool = pool[:3]
    elif dose.rules == 5:
        pool.append([("bind=perfect", "F")])
    shuffled = destinations.copy()
    rng.shuffle(shuffled)
    mapping = dict(zip(destinations, shuffled, strict=True)) | {"home": "home"}
    result = []
    for i, rule in enumerate(pool):
        clauses = []
        for condition, action in rule:
            if action.startswith("check:"):
                check, *options = action.split()
                action = " ".join([check, *(f"{k}={mapping[v]}" for k, v in (p.split("=") for p in options))])
            else:
                action = mapping[action]
            clauses.append([condition, action])
        result.append({"id": i + 1, "clauses": clauses})
    # The listed order sets precedence, independently of the rule numbers.
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


NESTING = ("Use the first matching main rule in the listed order; each indented exception adds its condition to "
           "its parent, and the deepest matching clause replaces the parent's action and checks.")


def rule_lines(rules, alternate=False):
    for rule in rules:
        for depth, (condition, action) in enumerate(rule["clauses"]):
            label = str(rule["id"]) if depth == 0 else ".".join([str(rule["id"]), *["1"] * depth])
            if alternate:
                yield f"{'  ' * depth}{label} {condition}->{action}"
            else:
                yield f"{'  ' * depth}{label}:{condition}=>{action}"


def note_text(home, rules):
    alternate = sum(map(ord, next(iter(home), ""))) % 2 == 0
    return "\n".join(["home " + " ".join(f"{k}={v}" for k, v in home.items()),
                      *rule_lines(rules, alternate)])


def card_text(home, rules, ordered=False):
    lines = ["home " + " ".join(f"{k}={v}" for k, v in home.items())]
    for rule in rules:
        prefix, block = [], []
        for condition, action in rule["clauses"]:
            prefix.append(condition)
            block.append(f"{','.join(prefix)}=>{action}")
        lines.extend(reversed(block) if ordered else block)
    return "\n".join(lines)


def card_parts(text, ordered=False):
    home, rules, errors = {}, [], []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            if i == 1:
                head, *pairs = line.split()
                if head != "home":
                    raise ValueError("missing home table")
                home = dict(p.split("=") for p in pairs)
                if not home or any(lane not in OUTCOMES[:4] for lane in home.values()):
                    raise ValueError("invalid home table")
            else:
                condition, action = (part.strip() for part in line.split("=>"))
                condition = re.sub(r"\s+", "", condition)
                for predicate in condition.split(","):
                    if not re.fullmatch(r"\w+=[\w-]+|qty[<>]\d+", predicate):
                        raise ValueError("invalid condition")
                validate_card_outcome(action)
                rules.append({"id": i, "clauses": [[condition, action]]})
        except (ValueError, IndexError):
            errors.append(i)
    if not text.strip():
        errors.append(1)
    if not ordered:
        rules.sort(key=lambda r: -len(r["clauses"][0][0].split(",")))
    return home, rules, errors


def parse_note(text, card=False, ordered=False):
    if card:
        home, rules, _ = card_parts(text, ordered)
        return home, rules
    lines = text.splitlines()
    head, *pairs = lines[0].split()
    if head != "home":
        raise ValueError("missing home table")
    home = dict(p.split("=") for p in pairs)
    rules = []
    for line in lines[1:]:
        if not line.strip() or line == NESTING:
            continue
        if "=>" in line:
            label, clause = line.strip().split(":", 1)
            condition, action = (p.strip() for p in clause.split("=>"))
        else:
            label, clause = line.strip().split(" ", 1)
            condition, action = (p.strip() for p in clause.split("->"))
        rid = int(label.split(".")[0])
        if "." not in label:
            rules.append({"id": rid, "clauses": []})
        if not rules or rules[-1]["id"] != rid:
            raise ValueError("missing parent rule")
        rules[-1]["clauses"].append([condition, action])
    # The later complete block with a given number supersedes the earlier block.
    current = {rule["id"]: rule for rule in rules}
    return home, list(current.values())


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
            (rare if depth else main).extend(make_job(rng, products, defaults, rules, home, cell)
                                             for _ in range(2 if depth else 1))
    while len(main) + len(rare) < size:
        cell = rng.choice([c for c in cells if not c[1]])
        main.append(make_job(rng, products, defaults, rules, home, cell))
    rng.shuffle(main)
    rng.shuffle(rare)
    if late:
        jobs = main + rare
    else:
        jobs = main + rare
        rng.shuffle(jobs)
    for row in jobs:
        row["job"]["id"] = f"J{shift + 1}-{rng.getrandbits(64):016x}"
    return jobs


def overlap_jobs(rng, final, rules, home):
    """Add a second, later matching rule without changing the scored cell or action."""
    candidates = []
    for row in final:
        rid, depth = map(int, row["cell"].split("."))
        if not rid:
            continue
        index = next(i for i, r in enumerate(rules) if r["id"] == rid)
        options = []
        for later in rules[index + 1:]:
            job = row["job"].copy()
            for part in later["clauses"][0][0].split(","):
                field, op, value = re.fullmatch(r"(\w+)([=<>])(\w+)", part).groups()
                job[field] = value if op == "=" else (max(job[field], int(value) + 1) if op == ">"
                                                       else min(job[field], int(value) - 1))
            fitting = [r["id"] for r in rules if matches(job, r["clauses"][0][0])]
            if fitting != [rid, later["id"]]:
                continue
            actual_rid, actual_depth, action = decision(rules, job)
            if (actual_rid, actual_depth) != (rid, depth):
                continue
            if resolve(action, home, job, row["replies"]) != (row["checks"], row["outcome"]):
                continue
            alternative = decision([later], job)[2]
            if resolve(alternative, home, job, row["replies"])[1] == row["outcome"]:
                continue
            options.append((job, fitting))
        if options:
            reversed_options = [option for option in options if option[1][0] > option[1][1]]
            candidates.append((row, rng.choice(reversed_options or options)))
    if len(candidates) < 8:
        return False
    rng.shuffle(candidates)
    selected = candidates[:8]
    if not any(ids[0] > ids[1] for _, (_, ids) in selected):
        return False
    for row, (job, ids) in selected:
        row.update(job=job, overlap=ids)
    return True


def merge_amendments(rules, amendments):
    by_id = {r["id"]: r for r in amendments}
    merged = [by_id.pop(r["id"], r) for r in rules]
    return merged + list(by_id.values())


def amend_rules(rng, initial, current, ids, home):
    amendments = []
    for rid in ids:
        rule = next(r for r in current if r["id"] == rid)
        original = next(r for r in initial if r["id"] == rid)
        clauses = [c.copy() for c in rule["clauses"]]
        forbidden = set(home.values())
        for _, action in original["clauses"] + rule["clauses"]:
            forbidden.update(re.findall(r"\b[A-Z]\b", action))
        destinations = [a for a in OUTCOMES[:-1] if a not in forbidden]
        if not destinations:
            raise ValueError("no distinct amended destination")
        clauses[-1][1] = rng.choice(destinations)
        amendments.append({"id": rid, "clauses": clauses})
    return amendments


def generate(seed, instance, n_seats, difficulty):
    dose = DOSES[difficulty]
    ordered = difficulty.endswith("-order")
    if n_seats is not None and n_seats != dose.hops + 1:
        raise ValueError(f"{difficulty} requires {dose.hops + 1} seats")
    rng = random.Random(f"digest:{seed}:{instance}:{difficulty}")
    products = rng.sample(PRODUCTS, dose.products)
    defaults = DEFAULTS.copy()
    for _ in range(200):
        home = {product: rng.choice(OUTCOMES[:4]) for product in products}
        initial = rule_pool(rng, dose, products)
        if ordered:
            rng.shuffle(initial)
            for rule, number in zip(initial, rng.sample(range(11, 100, 3), dose.rules), strict=True):
                rule["id"] = number
        rules = initial
        ids = rng.sample([r["id"] for r in initial], dose.rules)
        amendments = [[]]
        early = [(0, 0)]
        batches = [batch(rng, products, defaults, rules, home, early, dose.load, 0)]
        try:
            for hop in range(1, dose.hops):
                changes = amend_rules(rng, initial, rules, [ids[hop - 1]], home)
                amendments.append(changes)
                rules = merge_amendments(rules, changes)
                batches.append(batch(rng, products, defaults, rules, home, early, dose.load, hop))
            cells = [(0, 0), *((r["id"], d) for r in rules for d in range(len(r["clauses"])))]
            final = batch(rng, products, defaults, rules, home, cells, dose.final_size, dose.hops, dose.late)
            if ordered and not overlap_jobs(rng, final, rules, home):
                continue
        except ValueError:
            continue
        totals = Counter(row["cell"] for row in final)
        weights = Counter()
        for row in final:
            weights[row["outcome"]] += 1 / totals[row["cell"]] / len(totals)
        if max(weights.values()) > 0.2 + 1e-9:
            continue
        reference = note_text(home, rules)
        card = card_text(home, rules, ordered)
        cap = (13 * len(reference) + 99) // 100 * 10
        current = initial
        lengths = [len(note_text(home, current))]
        for changes in amendments[1:]:
            current = merge_amendments(current, changes)
            lengths.append(len(note_text(home, current)))
        append_size = lengths[0] + sum(len("\n".join(rule_lines(changes))) + 1 for changes in amendments[1:])
        if max(lengths) > cap or (difficulty != "canary" and append_size <= cap):
            continue
        if any(len(base64.b85encode(zlib.compress(render_manual(home, state).encode("ascii")))) <= cap
               for state in (initial, rules)):
            continue
        return {"seats": [f"p{i + 1}" for i in range(dose.hops + 1)], "home": home, "rules": rules,
                "initial_rules": initial, "amendments": amendments, "batches": batches + [final],
                "products": products, "fields": list(defaults), "cap": cap,
                "card_cap": (11 * len(card) + 9) // 10, "reference": reference, "card": card, "difficulty": difficulty}
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


def render_rules(rules, replacement=False):
    lines = []
    for rule in rules:
        lines.append(f"{'Replacement for rule' if replacement else 'Rule'} {rule['id']}.")
        for depth, (condition, action) in enumerate(rule["clauses"]):
            label = "Main" if not depth else "Exception " + ".".join(["1"] * depth)
            phrase = "For tickets where" if not depth else "When"
            lines.append(f"{'  ' * depth}{label}: {phrase} {condition_prose(condition)}, {action_prose(action)}")
        lines.append("")
    return "\n".join(lines)


def render_manual(home, rules):
    lines = ["# Finishing desk manual", "",
             ("The shop runs a print finishing service for walk-in and trade customers. "
              "The office maintains the equipment and sets the routing rules for this edition."),
             "The home lane is the normal destination for each product."]
    lines.extend(f"Product {p} has home lane {lane}." for p, lane in home.items())
    lines.extend(["", NESTING, "If no rule applies, send the job home.", ""])
    lines.append(render_rules(rules))
    lines.append("The desk is next to the loading bay. The office handles payroll and equipment maintenance.")
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
        if match := re.fullmatch(r"(?:Rule|Replacement for rule) (\d+)\.", line):
            rules.append({"id": int(match[1]), "clauses": []})
        elif match := re.fullmatch(r"(?:Main|Exception [\d.]+): (?:When|For tickets where) ([^,]+), (.+)", line.strip()):
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
    fullrecord = arm == "team-fullrecord"
    cap = None if fullrecord else inst["card_cap"] if card else inst["cap"]
    if arm == "probe-writer-control":
        cap *= 4
    roles = {s: "worker" if i == len(seats) - 1 else "planner" if i == 0 else "relay"
             for i, s in enumerate(seats)}
    manual = render_manual(inst["home"], inst["rules"] if arm == "solo" or card else inst["initial_rules"])
    ordered = inst["difficulty"].endswith("-order")
    amendments = {s: render_rules(inst["amendments"][i], replacement=True)
                  for i, s in enumerate(seats) if roles[s] == "relay"}
    briefings, instructions = {}, {}
    public = public_briefing(inst["fields"], ordered)
    for i, seat in enumerate(seats):
        role = roles[seat]
        own = {"role": role, "jobs": len(batches[i])}
        if ordered:
            own["ordered"] = True
        if card:
            own["format"] = "card"
        if fullrecord:
            own["fullrecord"] = True
        if i == 0:
            own["manual"] = manual
        if seat in amendments:
            own["amendment"] = amendments[seat]
        briefings[seat] = own
        instructions[seat] = public + role_briefing(own, cap, arm) + (
            "\n\n" + manual if i == 0 else "") + (
            "\n\n# Today's replacement rules\n" + amendments[seat] if seat in amendments else "")
    scripted = seats[1:] if card else seats[:-1] if arm.startswith("probe-worker-") else []
    return {"briefings": briefings, "instructions": instructions, "initial_active": seats[:1],
            "messaging": False, "push": False, "scripted_seats": scripted,
            "agent_timeout_sec": 900.0, "play_timeout_sec": 900.0 * len(seats) + 60.0,
            "max_calls": 200,
            "start_after": {s: [seats[i - 1]] for i, s in enumerate(seats) if i},
            "params": {"batches": dict(zip(seats, batches, strict=True)), "roles": roles, "cap": cap,
                       "card": card, "fullrecord": fullrecord, "manual": manual, "amendments": amendments,
                       "ordered": ordered,
                       "rule_heads": [[r["id"], re.match(r"\w+", r["clauses"][0][0])[0]]
                                      for r in inst["initial_rules"]],
                       "wrong_send_cost": WRONG_SEND_COST}}


def public_briefing(fields, ordered=False):
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
        "freight (booked or none) and ink (ready or short). A correct destination without its required check is wrong. "
        "Extra checks are allowed. Each final action is permanent. Correct jobs earn 1 point, "
        f"an incorrect send costs {WRONG_SEND_COST} points, and an incorrect hold or an unfinished job earns 0. "
        "The reward is the mean of final rule cell scores, with a floor of 0.\n"
        + NESTING + " A later complete rule with the same number replaces the earlier rule.\n"
        + (ORDER_FACTS + "\n" if ordered else "") +
        "Free-form notes are allowed unless your role specifies a rule card.\n"
        "Use `$WORLD_URL` with `Authorization: Bearer $SEAT_TOKEN`. For POST use `Content-Type: application/json`.\n"
        "Chat is disabled. You can make up to 200 world calls during your shift, including refused calls. "
        "GET /await, GET /whoami and GET /health do not count. GET /await?until=active waits for your shift. "
        "Start work when the reply's `active` field is the string `active`. If frozen, end your shift. "
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
        text = "You are the day planner. Use the manual below and work your own jobs first."
    else:
        address = SELF_ADDRESS if arm == "solo-seam" else TEAM_ADDRESS
        access = ("GET /note also returns the original manual, all earlier replacements and earlier notes. "
                  "The latest replacement of a rule takes precedence." if own.get("fullrecord") else
                  "You can see the incoming note but not the original manual or any earlier shift's records.")
        text = (f"You work this desk shift. {address} {access} Work every job. "
                "If no rule applies, send the job to its home lane. If no home lane is known, hold it.")
    if "amendment" in own:
        text += " Each rule below replaces the rule with the same number, effective now; all other rules stay."
    if role == "worker":
        return text
    if arm == "solo-seam":
        text += " " + SELF_HANDOVER
    elif own.get("format") == "card":
        text += (" Write a rule card. Line 1 is `home` followed by product=lane pairs separated by spaces. "
                 "Each further line has conditions joined by commas, then `=>`, then an outcome. "
                 "A condition is field=value, qty>N or qty<N. An outcome is a destination code, `hold`, `home`, "
                 "or `check:<what>` followed by reply=outcome pairs separated by spaces. "
                 + ("The first matching line decides. List each rule's deepest exceptions before its main clause. "
                    if own.get("ordered") else
                    "The matching line with the most conditions decides; on a tie, the earlier line wins. ") +
                 "A job matching no line goes home. The desk terminal applies your card exactly. "
                 "The terminal skips unreadable lines. The note check lists their line numbers.")
    elif own.get("fullrecord"):
        text += " Write the handover note for the next shift; the full record remains accessible."
    else:
        text += " " + TEAM_HANDOVER
    text += ("\nThe note has no family length cap; use printable ASCII and newlines." if cap is None else
             f"\nThe note can hold at most {cap} bytes of printable ASCII, including newlines.")
    if arm == "hint":
        if own.get("ordered"):
            text += "\n" + ORDER_HINT
        elif role == "relay":
            text += "\n" + HINT
    return text + ("\nPOST /note/check with `{\"text\":\"...\"}` measures its size without sending it. "
                   "POST /note/seal with the same body sends it once and ends your shift. "
                   "Empty or whitespace-only notes and characters outside printable ASCII and newline are rejected. "
                   + ("A note over the cap is rejected. " if cap is not None else "") +
                   "Finish your own jobs before sealing. Reserve the last 2 calls you need for checking and sealing.")


def leak_values(task):
    return [row["job"]["id"] for rows in task["params"]["batches"].values() for row in rows]


def chance_baseline(task, policy):
    if policy not in ("blind", "random", "uniform_random"):
        return None
    if task["params"]["card"]:
        rows = task["params"]["batches"][task["seats"][-1]]
        totals = Counter(row["cell"] for row in rows)
        return sum(1 / totals[row["cell"]] / len(totals) for row in rows
                   if row["outcome"] == "hold" and not row["checks"])
    return CHANCE_CEILING


def qa(task, views):
    cap = task["params"]["cap"]
    if cap is not None and cap <= 0:
        yield "invalid note cap"
    for seat, view in views.items():
        own = json.loads(view["env.SEAT_BRIEFING"])
        if ("manual" in own) != (seat == task["seats"][0]):
            yield f"{seat}: inappropriate manual access"


def pair_difference(first, second, views_a, views_b):
    normalized = {seat: dict(view) for seat, view in views_b.items()}
    for seat, view in normalized.items():
        instruction = view["instruction"]
        if second == "hint":
            own = json.loads(view["env.SEAT_BRIEFING"])
            sentence = ORDER_HINT if own.get("ordered") else HINT
            expected = int(own["role"] != "worker" if own.get("ordered") else own["role"] == "relay")
            if instruction.count("\n" + sentence) != expected:
                yield f"{seat}: incorrect hint sentence count"
            instruction = instruction.replace("\n" + sentence, "")
        elif second == "solo-seam":
            instruction = instruction.replace(SELF_ADDRESS, TEAM_ADDRESS).replace(SELF_HANDOVER, TEAM_HANDOVER)
        elif second == "probe-writer-control":
            pattern = r"The note can hold at most (\d+) bytes of printable ASCII, including newlines\."
            a = re.search(pattern, views_a[seat]["instruction"])
            b = re.search(pattern, instruction)
            if a is None or b is None or int(b[1]) != 4 * int(a[1]):
                yield f"{seat}: expected fourfold cap"
            else:
                instruction = instruction.replace(b[0], a[0], 1)
        view["instruction"] = instruction
    if normalized != views_a:
        yield f"{first} and {second} differ beyond the declared treatment"
