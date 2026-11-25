"""Instances for exact-fit catalog and order files."""

import json
import random
import re
from itertools import combinations

FAMILY = "interface"
TITLE = "Replenishment batch"
SEATS = {"canary": 3, "standard": 3, "heavy": 3}
ARMS = ("solo", "solo-seam", "team", "team-hint", "probe-control", "probe-trap-key",
        "probe-trap-unit", "probe-trap-key-hint")
EXTRA_ARMS = ("silent", "team-parallel")
TWINS = (("probe-control", "probe-trap-key"), ("probe-control", "probe-trap-unit"))
PAIRS = (("team", "team-hint"), ("probe-trap-key", "probe-trap-key-hint"))
ARM_WORDS = ("probe-control", "probe-trap-key", "probe-trap-unit", "probe-trap-key-hint",
             "solo-seam", "team-hint", "interface")
HINT = "Before fixing an order, obtain the desk's actual key form and unit from its visible catalog or a direct question."
FORMS = ("SITE/ITEM", "ITEM/SITE", "SITE:ITEM", "ITEM:SITE", "SITE-ITEM", "ITEM-SITE")
CONVENTIONS = tuple((form, unit) for form in FORMS for unit in ("each", "case"))
DOSES = {level: (3, 3, 3, 5, 4, 1) for level in SEATS}
HIDDEN = {"canary": 0, "standard": 1, "heavy": 3}
SUPPORTS = tuple(choices for choices in combinations(CONVENTIONS, 4)
                 if len({form[0] for form, _ in choices}) == 2
                 and len({form[4] for form, _ in choices}) > 1
                 and len({unit for _, unit in choices}) == 2)


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


def support(rng, difficulty, saved):
    if difficulty == "canary":
        form, unit = saved
        return [(f"SITE{form[4]}ITEM", unit), (f"ITEM{form[4]}SITE", unit)]
    return list(rng.choice([choices for choices in SUPPORTS if saved in choices]))


def make_events(rng, groups, target, steps, corrections):
    buckets = [[] for _ in range(steps - 1)]
    updates = [(group["name"], row) for group in groups for row in group["rows"]]
    rng.shuffle(updates)
    positive = [(g, row) for g, row in updates if row["target"] > row["on_hand"] + row["in_transit"]]
    corrected = {(g, row["site"], row["item"]) for g, row in rng.sample(positive, corrections)}
    for i, (group, row) in enumerate(updates):
        event = {"kind": "count", "group": group, "site": row["site"], "item": row["item"],
                 "on_hand": row["on_hand"], "in_transit": row["in_transit"]}
        step = i % (steps - 1)
        if (group, row["site"], row["item"]) in corrected:
            buckets[0].append({**event, "on_hand": row["on_hand"] + row["case"]})
            buckets[-1].append({**event, "kind": "correction"})
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
    rng = random.Random(f"interface:{seed}:{instance}")
    support_rng = random.Random(f"interface-support:{seed}:{instance}:{difficulty}")
    seats = [f"p{i}" for i in range(1, n_seats + 1)]
    buyer = rng.choice(CONVENTIONS)
    names = rng.sample([f"Supplier-{a}{b:02d}" for a in "ABCDEFGH" for b in range(100)], group_count)
    pairs = rng.sample([(f"{a}{i}", f"{b}{j}") for a in "ABCDEFGH" for i in range(10)
                        for b in "JKLMNPQRSTUVWXYZ" for j in range(10)], group_count * high)
    groups = []
    for index, name in enumerate(names):
        saved = rng.choice(CONVENTIONS)
        supported = support(support_rng, difficulty, saved)
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
        groups.append({"name": name, "desk": seats[1 + index % (n_seats - 1)],
                       "support": [list(c) for c in supported], "saved": list(saved), "rows": rows})
    target = rng.choice(names)
    visibility_order = [target, *[name for name in names if name != target]]
    form, unit = buyer
    separator = form[4]
    key_trap = (form.replace(separator, rng.choice([s for s in "/:-" if s != separator])), unit)
    if rng.choice((False, True)):
        a, b = form.split(separator)
        key_trap = (separator.join((b, a)), unit)
    unit_trap = (form, "case" if unit == "each" else "each")
    return {"seats": seats, "difficulty": difficulty, "groups": groups, "buyer": list(buyer), "target": target,
            "hidden": visibility_order[:HIDDEN[difficulty]], "key_trap": list(key_trap), "unit_trap": list(unit_trap),
            "stream": make_events(rng, groups, target, steps, corrections)}


def group_views(group):
    public = {k: group[k] for k in ("name", "desk", "hidden")}
    buyer = public | {"rows": [{k: row[k] for k in ("site", "item", "case", "target")} for row in group["rows"]]}
    desk = public | {"support": group["support"], "saved": group["saved"],
                     "rows": [{k: row[k] for k in ("site", "item", "case", "price")} for row in group["rows"]]}
    return buyer, desk


def questions(message, groups):
    if message["from"] != "p1":
        return []
    text = message["text"].casefold()
    if not re.search(r"\b(convention|format|key|form|unit|separator|order)\b", text):
        return []
    if not re.search(r"\?|\b(what|which|how|tell|share|confirm|send|specify|provide|need)\b", text):
        return []
    return [g["name"] for g in groups if message["to"] == g["desk"] and g["name"].casefold() in text]


def describe(group, convention):
    return f"{group['name']}: key form {convention[0]}, unit {convention[1]}."


def announced(group, convention, text):
    if group["name"].casefold() not in text.casefold():
        return False
    form, unit = convention
    text = text.casefold()
    unit_pattern = r"\b(each|per item|items|individual units)\b" if unit == "each" else r"\b(case|cases|per case)\b"
    if not re.search(unit_pattern, text):
        return False
    if any(key_for(row, convention).casefold() in text for row in group["rows"]):
        return True
    left, right = form[:4].casefold(), form[5:].casefold()
    separator = {"/": r"/|slash", ":": r":|colon", "-": r"-|hyphen|dash"}[form[4]]
    return bool(re.search(rf"\b{left}\b\s*(?:(?:then|followed by|separated by|with|a|the)\s+)*"
                          rf"(?:{separator})\s*(?:(?:then|followed by|and)\s+)*\b{right}\b", text))


def common_text(groups, owner, messaging, max_text, max_messages):
    intro = """You work on a wholesale replenishment batch. A finance connector imports one catalog and one order for each supplier group.
For each order line it finds the catalog row with the exact same `key` string. It adds quantity × price to the batch total.
It does not reorder key parts, change separators, convert units or accept aliases. The units of the two files must agree.

A key contains the site and item codes in one of 6 forms: `SITE/ITEM`, `ITEM/SITE`, `SITE:ITEM`, `ITEM:SITE`, `SITE-ITEM`, `ITEM-SITE`.
Each file states its unit in `unit`: `each` or `case`. Use one key form and one unit per file.
The catalog desk can choose any setting supported by its equipment. Uploaded files are final and cannot change.

Each group requires both files to be correct. Missing or wrong files earn no credit for that group.
There is no partial credit inside a group and no correctness feedback.
"""
    ownership = {"both": "You own both files of every group.",
                 "export": "An automatic export supplies the catalog files; you own the orders.",
                 "seats": "Each assigned desk owns its catalogs; the buyer owns the orders."}
    api = f"""
{ownership[owner]}
Groups marked sealed have their catalog held outside staging until the order is final. For other groups, the catalog enters staging when uploaded.
Use `$WORLD_URL` with header `Authorization: Bearer $SEAT_TOKEN` for all requests.
For POST requests use `Content-Type: application/json`. Each seat has 1,000 authenticated calls.
- `POST /next` with `{{}}` gives buyer work events in `events`, `step` and `end`.
- `POST /prepare` with `{{}}`: the buyer ends its preparation phase (only after `end` is true).
- `GET /staging` lists visible files, the names of deposited catalogs in `ready`, the buyer's `prepared` flag, and desks whose work has `finished`.
- `GET /staging?group=<group>` returns the visible full files for that group in `files`.
- `GET /await?until=tick&after=N` waits until a logical tick greater than N. Work replies include `tick`.
- `POST /publish` uploads `{{"kind":"catalog" or "order","group":...,"unit":...,"rows":[...]}}`.
  Catalog rows contain `key` and integer `price_cents`. Order rows contain `key` and integer `qty`.
  Uploads must have at least one row. A successful upload returns a receipt; a second upload of the same file is refused.
- `POST /done` with `{{}}` permanently ends your work.

Each work reply includes a `staging` summary, `ready`, `prepared`, `finished` and `tick`.
"""
    if messaging:
        api += (f"- `POST /messages` with {{\"to\":\"<seat>\" or \"all\",\"text\":...}} sends chat "
                f"(at most {max_messages} messages of {max_text} characters each).\n"
                "New chat messages appear in `new_messages` with replies. `GET /messages` lists your messages.\n"
                "For a direct question about a setting, address its desk and name the supplier group.\n")
    else:
        api += "Chat is not available.\n"
    if owner == "export":
        api += "For sealed groups the export supplies its chosen form and unit in `export_settings` in work replies.\n"
    registry = ["\nRegistry (site, item, items per case):"]
    for group in groups:
        rows = "; ".join(f"site {r['site']}, item {r['item']}, case {r['case']}" for r in group["rows"])
        desk = f", catalog desk {group['desk']}" if owner == "seats" else ""
        visibility = "sealed" if group["hidden"] else "visible"
        registry.append(f"- {group['name']} ({visibility}{desk}): {rows}")
    return intro + api + "\n".join(registry) + "\n"


def buyer_text(own, hint):
    text = """
You are the buyer. Write one order per supplier group.
For each line, quantity in items = target − on hand − in transit.
Omit lines with quantity 0 or less. For unit `case`, divide the quantity in items by the case size.
Positive quantities in this batch are always whole numbers of cases.
Call `POST /next` until `end` is true. A count or correction replaces both absolute counts for a line, in items.
A `complete` event means that group's counts are final. Send `/prepare` after the last work event.
Orders can be uploaded after preparation, once the group's catalog is deposited and its desk has finished.
Your order writer supports all 12 combinations.
"""
    text += f"Its saved setting is `{own['saved'][0]}`, unit `{own['saved'][1]}`.\n\nTargets (items):\n"
    for group in own["groups"]:
        targets = "; ".join(f"site {r['site']}, item {r['item']}: {r['target']}" for r in group["rows"])
        text += f"- {group['name']}: {targets}\n"
    return text + (f"\n{HINT}\n" if hint else "")


def desk_text(groups, messaging, solo):
    text = """
You are also the catalog desk for the groups below. Upload one catalog for each group.
Include every listed line. Prices are in cents per item. For unit `case`, multiply the price by the case size.
Select any supported combination for each group's equipment.
"""
    if not solo:
        text += "Wait for `prepared` to be true before ending your work with `/done`. Finish only after depositing all your catalogs.\n"
        if messaging:
            text += "The buyer can send questions during preparation. You can reply before ending your work.\n"
    for group in groups:
        choices = ", ".join(f"`{form}`, `{unit}`" for form, unit in group["support"])
        saved = f"`{group['saved'][0]}`, `{group['saved'][1]}`"
        prices = "; ".join(f"site {r['site']}, item {r['item']}: {r['price']}" for r in group["rows"])
        text += f"\n- {group['name']}: supported {choices}; saved {saved}.\n  Prices: {prices}\n"
    return text


def extension_arm(inst, arm, max_text, max_messages):
    if arm not in (*ARMS, *EXTRA_ARMS):
        raise ValueError(f"unknown arm {arm}")
    solo = arm == "solo"
    export = arm == "solo-seam"
    probe = arm.startswith("probe-")
    messaging = not (solo or export or arm == "silent")
    text_limit, message_limit = min(max_text, 1000), min(max_messages, 20)
    if messaging and (text_limit < 80 or message_limit < len(inst["groups"])):
        raise ValueError("insufficient message budget for catalog questions and replies")
    owner = "both" if solo else "export" if export else "seats"
    groups = []
    prebuilt = {}
    for original in inst["groups"]:
        group = original | {"hidden": arm == "team-parallel" or original["name"] in inst["hidden"]}
        if solo or export:
            group["desk"] = "p1" if solo else "export"
        if probe:
            convention = inst["buyer"]
            if group["name"] == inst["target"]:
                if arm == "probe-trap-unit":
                    convention = inst["unit_trap"]
                elif arm in ("probe-trap-key", "probe-trap-key-hint"):
                    convention = inst["key_trap"]
            group["support"] = [inst["buyer"], inst["key_trap"], inst["unit_trap"]]
            group["saved"] = convention
        if export:
            prebuilt[group["name"]] = group["saved"]
        groups.append(group)
    buyers, desks = zip(*(group_views(g) for g in groups), strict=True)
    buyer = {"role": "all" if solo else "buyer", "saved": inst["buyer"], "groups": list(buyers),
             "messaging": messaging}
    if solo:
        buyer["desks"] = list(desks)
    common = common_text(groups, owner, messaging, text_limit, message_limit)
    briefings = {"p1": buyer}
    instructions = {"p1": common + buyer_text(buyer, arm.endswith("-hint"))}
    if solo:
        instructions["p1"] += desk_text(desks, messaging, solo)
    scripted = []
    if not solo and not export:
        for seat in inst["seats"][1:]:
            own = [g for g in desks if g["desk"] == seat]
            briefings[seat] = {"role": "desk", "groups": own, "messaging": messaging}
            instructions[seat] = common + desk_text(own, messaging, solo).replace("You are also", "You are")
            if probe:
                scripted.append(seat)
    params = {"groups": groups, "buyer": inst["buyer"], "target": inst["target"], "stream": inst["stream"],
              "prebuilt": prebuilt, "text_limit": text_limit, "message_limit": message_limit}
    return {"params": params, "briefings": briefings, "instructions": instructions, "messaging": messaging,
            "push": messaging, "scripted_seats": scripted, "scripted_policies": dict.fromkeys(scripted, "careful")}


def pair_difference(first, second, views_a, views_b):
    normalized = {seat: dict(view) for seat, view in views_b.items()}
    instruction = normalized["p1"]["instruction"]
    suffix = "\n" + HINT + "\n"
    if not instruction.endswith(suffix):
        return ["missing protocol sentence"]
    normalized["p1"]["instruction"] = instruction.removesuffix(suffix)
    return [] if normalized == views_a else ["more than the protocol sentence differs"]


def qa(task, views):
    params = task["params"]
    for seat, view in views.items():
        own = json.loads(view["env.SEAT_BRIEFING"])
        if own.get("role") == "buyer":
            if any(set(row) != {"site", "item", "case", "target"} for g in own["groups"] for row in g["rows"]):
                yield f"{seat} sees private catalog or count data"
            if any(set(g) != {"name", "desk", "hidden", "rows"} for g in own["groups"]):
                yield f"{seat} sees private catalog settings"
        if own.get("role") == "desk" and any(g["desk"] != seat for g in own["groups"]):
            yield f"{seat} sees another desk's private equipment"
    if any(row["case"] < 2 for g in params["groups"] for row in g["rows"]):
        yield "case size must distinguish units"
