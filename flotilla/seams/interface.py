"""Instances for exact-fit catalog and order files."""

import json
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
    corrected = {(g, row["site"], row["item"]) for g, row in rng.sample(updates, corrections)}
    for i, (group, row) in enumerate(updates):
        event = {"kind": "count", "group": group, "site": row["site"], "item": row["item"],
                 "on_hand": row["on_hand"], "in_transit": row["in_transit"]}
        step = i % (steps - 1)
        if (group, row["site"], row["item"]) in corrected:
            step %= max(1, (steps - 1) // 2)
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
        groups.append({"name": name, "desk": seats[1 + index % (n_seats - 1)],
                       "support": [list(c) for c in supported], "saved": list(saved), "rows": rows})
    target = rng.choice(names)
    form, unit = buyer
    separator = next(s for s in "/:-" if s in form)
    key_trap = (form.replace(separator, rng.choice([s for s in "/:-" if s != separator])), unit)
    if rng.choice((False, True)):
        a, b = form.split(separator)
        key_trap = (separator.join((b, a)), unit)
    unit_trap = (form, "case" if unit == "each" else "each")
    return {"seats": seats, "difficulty": difficulty, "groups": groups, "buyer": list(buyer), "target": target,
            "key_trap": list(key_trap), "unit_trap": list(unit_trap),
            "stream": make_events(rng, groups, target, steps, corrections)}


def group_views(group):
    public = {k: group[k] for k in ("name", "desk")}
    buyer = public | {"rows": [{k: row[k] for k in ("site", "item", "case", "target")} for row in group["rows"]]}
    desk = public | {"support": group["support"], "saved": group["saved"],
                     "rows": [{k: row[k] for k in ("site", "item", "case", "price")} for row in group["rows"]]}
    return buyer, desk


def common_text(groups, owner, messaging, max_text, max_messages):
    intro = """You work on a wholesale replenishment batch. A finance connector imports one catalog and one order for each supplier group.
For each order line it finds the catalog row with the exact same `key` string. It adds quantity × price to the batch total.
It does not reorder key parts, change separators, convert units or accept aliases.

A key contains the site and item codes in one of 6 forms: `SITE/ITEM`, `ITEM/SITE`, `SITE:ITEM`, `ITEM:SITE`, `SITE-ITEM`, `ITEM-SITE`.
For example, for site B4 and item Q7, `ITEM/SITE` means `Q7/B4`.
Each file states its unit in `unit`: `each` or `case`. Use one key form and one unit per file.
Every combination works when both files of a group use the same one.

The batch earns credit for each group with both files correct and all order lines joined to the correct catalog rows.
There is no partial credit inside a group and no correctness feedback.
"""
    ownership = {"both": "You upload each catalog and then the corresponding order.",
                 "export": "The nightly catalog export uploads each catalog. You upload an order after its catalog is in staging.",
                 "seats": "Each catalog is uploaded by its assigned desk. The buyer uploads an order after its catalog is in staging."}
    api = f"""
{ownership[owner]} Uploaded files cannot be changed.

Use `$WORLD_URL` with header `Authorization: Bearer $SEAT_TOKEN` for all requests.
For POST requests use `Content-Type: application/json`.

- `POST /next` with `{{}}` gives work events in `events`, the current `step` and an `end` flag.
- `GET /staging` lists all staged files by group, kind, owner and row count.
- `GET /staging?group=<group>` returns full files for that group in `files`.
- `POST /publish` uploads `{{"kind":"catalog" or "order","group":...,"unit":...,"rows":[...]}}`.
  Catalog rows contain `key` and integer `price_cents`. Order rows contain `key` and integer `qty`.
  A successful upload returns a receipt; a second upload for the same file is refused.
- `POST /done` with `{{}}` ends your work.

Each work reply includes `staging`, a summary of published files. If a catalog has not arrived yet, check again before uploading its order.
"""
    if messaging:
        api += (f"- `POST /messages` with `{{\"to\":\"<seat>\" or \"all\",\"text\":...}}` sends chat "
                f"(at most {max_messages} messages of {max_text} characters each).\n"
                "New chat messages appear in `new_messages` with replies. `GET /messages` lists your messages.\n")
    else:
        api += "Chat is not available.\n"
    registry = ["\nRegistry (site/item, items per case):"]
    for group in groups:
        rows = ", ".join(f"{r['site']}/{r['item']} ({r['case']})" for r in group["rows"])
        desk = f" (catalog desk {group['desk']})" if owner == "seats" else ""
        registry.append(f"- {group['name']}{desk}: {rows}")
    if owner == "seats":
        registry.append("Buyer: p1.")
    return intro + api + "\n".join(registry) + "\n"


def buyer_text(own, hint):
    text = """
You are the buyer. Write one order per supplier group.
For each line, quantity in items = target − on hand − in transit.
Omit lines with quantity 0 or less. For unit `case`, divide the quantity in items by the case size.
Positive quantities in this batch are always whole numbers of cases.
Call `POST /next` until `end` is true. Each count or correction gives a group, site, item, `on_hand` and `in_transit`.
Both counts are in items. Use the last values for each line. A `complete` event means that group's counts are final.
Your order writer supports all 12 combinations.
"""
    text += f"Its saved setting is `{own['saved'][0]}`, unit `{own['saved'][1]}`.\n\nTargets (site/item = items):\n"
    for group in own["groups"]:
        targets = ", ".join(f"{r['site']}/{r['item']}={r['target']}" for r in group["rows"])
        text += f"- {group['name']}: {targets}\n"
    return text + (f"\n{HINT}\n" if hint else "")


def desk_text(groups):
    text = """
You are also the catalog desk for the groups below. Upload one catalog for each group.
Include every listed line. Prices are in cents per item. For unit `case`, multiply the price by the case size.
Select any supported combination for each group's adapter. The saved setting is a valid option.
"""
    for group in groups:
        choices = ", ".join(f"`{form}`, `{unit}`" for form, unit in group["support"])
        saved = f"`{group['saved'][0]}`, `{group['saved'][1]}`"
        prices = ", ".join(f"{r['site']}/{r['item']}={r['price']}" for r in group["rows"])
        text += f"\n- {group['name']}: supported {choices}; saved {saved}.\n  Prices: {prices}\n"
    return text


def extension_arm(inst, arm, max_text, max_messages):
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm}")
    solo = arm == "solo"
    export = arm == "solo-seam"
    probe = arm.startswith("probe-")
    messaging = not (solo or export or arm == "silent")
    owner = "both" if solo else "export" if export else "seats"
    groups = []
    prebuilt = {}
    for original in inst["groups"]:
        group = original.copy()
        if solo or export:
            group["desk"] = "p1" if solo else "export"
        if probe or export:
            convention = inst["buyer"]
            if group["name"] == inst["target"]:
                if arm == "probe-trap-unit":
                    convention = inst["unit_trap"]
                elif arm in ("solo-seam", "probe-trap-key", "probe-trap-key-hint"):
                    convention = inst["key_trap"]
            group["support"] = [inst["buyer"], inst["key_trap"], inst["unit_trap"]]
            prebuilt[group["name"]] = convention
        groups.append(group)
    buyers, desks = zip(*(group_views(g) for g in groups), strict=True)
    buyer = {"role": "all" if solo else "buyer", "saved": inst["buyer"], "groups": list(buyers),
             "messaging": messaging}
    if solo:
        buyer["desks"] = list(desks)
    common = common_text(groups, owner, messaging, max_text, max_messages)
    briefings = {"p1": buyer}
    instructions = {"p1": common + buyer_text(buyer, arm.endswith("-hint"))}
    if solo:
        instructions["p1"] += desk_text(desks)
    scripted = []
    if not solo and not export:
        for seat in inst["seats"][1:]:
            if probe:
                scripted.append(seat)
                briefings[seat] = {"role": "scripted"}
                instructions[seat] = ""
            else:
                own = [g for g in desks if g["desk"] == seat]
                briefings[seat] = {"role": "desk", "groups": own, "messaging": messaging}
                instructions[seat] = common + desk_text(own).replace("You are also", "You are") + "\nEnd your work after uploading your catalogs.\n"
    params = {"groups": groups, "buyer": inst["buyer"], "target": inst["target"], "stream": inst["stream"],
              "prebuilt": prebuilt}
    return {"params": params, "briefings": briefings, "instructions": instructions, "messaging": messaging,
            "scripted_seats": scripted, "scripted_policies": dict.fromkeys(scripted, "nop")}


def qa(task, views):
    params = task["params"]
    for seat, view in views.items():
        own = json.loads(view["env.SEAT_BRIEFING"])
        if own.get("role") == "buyer":
            if any(set(row) != {"site", "item", "case", "target"} for g in own["groups"] for row in g["rows"]):
                yield f"{seat} sees private catalog or count data"
            if any(set(g) != {"name", "desk", "rows"} for g in own["groups"]):
                yield f"{seat} sees private catalog settings"
        if own.get("role") == "desk" and any(g["desk"] != seat for g in own["groups"]):
            yield f"{seat} sees another desk's private adapter"
    if any(row["case"] < 2 for g in params["groups"] for row in g["rows"]):
        yield "case size must distinguish units"
