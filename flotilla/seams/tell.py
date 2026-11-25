"""A receiving desk and two dispatch desks share responsibility for a shipment."""

import random
import string

FAMILY = "tell"
TITLE = "Warehouse receiving and dispatch"
SEAM = "A receiving desk must route its private supplier notice to the desk whose carrier needs it."
VERSION = 1
LEVELS = ("canary", "standard", "heavy")
SEATS = 3
ARMS = ("solo", "solo-control", "team", "team-control", "probe-trap", "probe-control",
        "solo-seam", "solo-seam-control", "auto", "auto-control", "hint", "hint-control")
EXTRA_ARMS = ("team-x", "team-x-control")
TWINS = (("solo", "solo-control"), ("team", "team-control"), ("probe-trap", "probe-control"),
         ("solo-seam", "solo-seam-control"), ("auto", "auto-control"), ("hint", "hint-control"),
         ("team", "team-x"), ("team-x", "team-x-control"))
PAIRS = (("probe-trap", "solo-seam"), ("probe-trap", "auto"), ("probe-trap", "hint"),
         ("probe-control", "solo-seam-control"), ("probe-control", "auto-control"), ("probe-control", "hint-control"))
ARM_WORDS = ("probe-trap", "probe-control", "solo-seam", "auto-control", "hint-control", "team-control",
             "solo-control", "team-x", "team-x-control", "canary", "standard", "heavy")
COMMON_WORDS = ("solo", "team", "auto", "hint", "tell")
PRODUCTS = ("Gauze pads", "Cotton rolls", "Paper tape", "Nitrile gloves", "Sample labels", "Face masks",
            "Storage trays", "Eye shields", "Bandage rolls", "Exam gowns", "Paper towels", "Packing sleeves",
            "Measuring cups", "Plastic bins", "Waste bags", "Dressings", "Elastic wraps", "Surgical caps")
DESKS = ("p2", "p3")
ITEMS = 8
CARRIER = 7
END = ITEMS + 3
FORWARDS = 2
CALLS = 64


def generate(seed, instance, n_seats, difficulty):
    if n_seats not in (None, SEATS) or difficulty not in LEVELS:
        raise ValueError("three seats and a declared dose level are required")
    rng = random.Random(f"dispatch:{seed}:{instance}:{difficulty}")
    products = rng.sample(PRODUCTS, 9)
    overlap = LEVELS.index(difficulty)
    destinations = {product: [rng.choice(DESKS)] for product in products[:3]}
    for product in rng.sample(products[:3], (0, 2, 3)[overlap]):
        destinations[product] = list(DESKS)
    by_desk = {desk: [product for product, desks in destinations.items() if desk in desks] for desk in DESKS}
    extras = iter(products[3:])
    issued = set()

    def code(prefix):
        while True:
            value = prefix + "".join(rng.choices(string.ascii_uppercase + "23456789", k=7))
            if value not in issued:
                issued.add(value)
                return value

    boards = {}
    stock = {}
    for desk, names in by_desk.items():
        names.extend(next(extras) for _ in range(3 - len(names)))
        rng.shuffle(names)
        boards[desk] = []
        stock[desk] = {}
        for product in names:
            order = code("O-")
            lots = [code("L-"), code("L-")]
            planned = rng.choice(lots)
            rng.shuffle(lots)
            boards[desk].append({"order": order, "product": product, "lot": planned, "qty": rng.randrange(10, 151, 5)})
            stock[desk][order] = lots

    notice_products = products[:3]
    decisive = rng.choice(notice_products)
    receiver = rng.choice(destinations[decisive])
    row = next(row for row in boards[receiver] if row["product"] == decisive)
    replacement = next(lot for lot in stock[receiver][row["order"]] if lot != row["lot"])
    quarantines = [{"kind": "quarantine", "product": product,
                    "lot": row["lot"] if product == decisive else code("L-")}
                   for product in notice_products]
    rng.shuffle(quarantines)
    first = 1 + overlap
    slots = list(range(first - 1, first + 2))
    docket = {}
    batch = []
    hit = None
    other = rng.sample(("price revision", "warehouse hours", "packaging change"), 2)
    kinds = iter(rng.sample(["delivery"] * 3 + ["notice"] * 2, 5))
    for index in range(ITEMS):
        if index in slots:
            document = quarantines.pop()
            if document["product"] == decisive:
                hit = index
        elif next(kinds) == "delivery":
            po = code("PO-")
            qty = rng.randrange(20, 501, 10)
            product = rng.choice(products)
            docket[po] = {"product": product, "qty": qty}
            document = {"kind": "delivery", "po": po, "product": product,
                        "qty": qty + rng.choice((0, 0, -10, 10))}
        else:
            document = {"kind": "notice", "subject": other.pop()}
        batch.append({"item": code("I-"), **document})
    return {"seats": ["p1", *DESKS], "batch": batch, "docket": docket, "boards": boards, "stock": stock,
            "hit": hit, "receiver": receiver, "control_lot": replacement}


def record_entry(document, docket):
    result = {"item": document["item"]}
    if document["kind"] == "delivery":
        return result | {"status": "accept" if docket[document["po"]]["qty"] == document["qty"] else "reject"}
    if document["kind"] == "quarantine":
        return result | {"status": "quarantine", "lot": document["lot"]}
    return result | {"status": "file"}


SOCIAL = "The dispatch desks are your teammates, agents like you."
MACHINE = "The dispatch desks are automated dispatch programs."
HINT = "When a quarantine names a planned lot, forward the document to that desk before recording it."
AUTO = "Each item you record as quarantine is also sent automatically to both dispatch desks in the next round."
SCORE = (
    "There is one shared score. It is 1 if the receiving log is correct and both carriers take correct sheets. "
    "Otherwise it is 0. Each order must ship its planned lot, except that a quarantined planned lot must be "
    "replaced by the other lot of the same product."
)
TIME = (
    f"Rounds start at 1. Each desk that is not waiting must make one accepted POST per round. "
    f"Both carriers take the sheets at the end of round {CARRIER}. Play ends no later than round {END}. "
    "GET calls and refused POSTs do not use an action. A reply shows the current round and whether "
    "you have already acted in it (`acted`). If you have acted in round r, "
    "GET /await?until=tick&after=r-1 (replace r-1 with the number) waits for the next round. "
    "The `tick` field in that reply is the round number minus 1. "
    "If an await times out, repeat the await; logical time has not changed. "
    f"Keep within {CALLS} authenticated calls, including GETs and refused calls. "
    "Reserve calls for the receiving log or the final dispatch action. "
    "Use $WORLD_URL with Authorization: Bearer $SEAT_TOKEN and JSON bodies for POSTs."
)
RECEIVING = (
    "GET /docket shows purchase orders. GET /item shows the current document. "
    "POST /record records it and returns the next document, or null when the log is complete. "
    "For a delivery, use status accept if the quantity equals the purchase order quantity, else reject. "
    "For a quarantine, use status quarantine and the document's lot code. A quarantine applies immediately. "
    "For any other notice, use status file. "
    'Body: {"item":"<document ID>","status":"<status>"}; add "lot":"<lot code>" for quarantine. '
    "Record each document once, in the order shown. A later document cannot replace a record."
)
DISPATCH = (
    "GET /sheet shows your current orders and lots. GET /stock lists the two stock lots by order. "
    "Change an order only if a quarantine document you received names its current lot. "
    'Use POST /sheet with {"order":"<order ID>","lot":"<other lot code>"}. '
    "An edit uses one action. The sheets cannot change after the carriers leave."
)
FORWARD = (
    "GET /board shows the original dispatch plans for p2 and p3. Only those desks can change their own sheet. "
    "Supplier documents reach receiving only. Dispatch changes a lot on a quarantine document it has received. "
    'POST /forward with {"to":"<dispatch seat>","item":"<viewed document ID>"} sends the original document '
    "to that desk. A forward in round r reaches that desk at the start of round r+1. "
    f"A manual forward costs one action. You can make at most {FORWARDS} manual forwards. "
    "You can forward a document even after recording it."
)
WAIT = "POST /wait with {} uses your round without doing other work. Finish when the receiving log is complete."
DISPATCH_WAIT = (
    "Forwarded documents appear in `new_messages` on your next reply. Each message has a `text` field containing "
    "the JSON document, or the words Carrier round. GET /messages also shows the history. "
    "Free text messages cannot be sent. POST /wait with {} uses your action without changing your sheet. "
    'POST /wait with {"until":"delivery"} also parks your desk until a new document reaches it or the carrier round starts. '
    "While parked, use GET /await?until=message. If a wait reply already contains a new message, "
    "use that instead. You must act in the carrier round, even if no document reaches you. "
    "Finish when your reply says carrier_left or an await says retired or frozen."
)


def extension_arm(inst, arm, max_text, max_messages):
    if arm not in (*ARMS, *EXTRA_ARMS) or max_text < 256 or max_messages < 10:
        raise ValueError("unknown arm or insufficient document channel")
    base = arm.removesuffix("-control")
    solo = base == "solo"
    automatic = base == "auto"
    batch = [dict(document) for document in inst["batch"]]
    if arm.endswith("-control"):
        batch[inst["hit"]]["lot"] = inst["control_lot"]
    params = {key: inst[key] for key in ("boards", "stock", "docket", "hit", "receiver")}
    params.update(batch=batch, automatic=automatic)
    common = [SCORE, TIME, RECEIVING]
    if solo:
        instructions = {"p1": "\n\n".join(["You work the receiving and both dispatch desks of a supply warehouse.",
                                           *common, DISPATCH,
                                           "GET /board shows the original plans. Use the quarantine documents in "
                                           "your receiving batch for dispatch decisions.", WAIT])}
        return {"briefings": {"p1": {"role": "both"}}, "instructions": instructions,
                "params": params, "messaging": False, "max_calls": CALLS}
    added = HINT if base == "hint" else AUTO if automatic else ""
    sender = "\n\n".join(["You work the receiving desk of a supply warehouse. "
                          + (MACHINE if base == "solo-seam" else SOCIAL),
                          *common, FORWARD + (" " + added if added else ""), WAIT])
    dispatch = "\n\n".join(["You work a dispatch desk of a supply warehouse. The receiving desk is seat p1.",
                            SCORE, TIME, DISPATCH, DISPATCH_WAIT])
    scripted = [] if base in ("team", "team-x") else list(DESKS)
    return {"briefings": {"p1": {"role": "receiving"}, **{desk: {"role": "dispatch"} for desk in DESKS}},
            "instructions": {"p1": sender, **dict.fromkeys(DESKS, dispatch)}, "params": params,
            "scripted_seats": scripted, "scripted_policies": dict.fromkeys(scripted, "follow"),
            "messaging": True, "push": True, "senders": ["receiving"], "max_calls": CALLS}


def pair_difference(first, second, views_a, views_b):
    normalized = {seat: dict(view) for seat, view in views_b.items()}
    instruction = normalized["p1"]["instruction"]
    if second.startswith("solo-seam"):
        instruction = instruction.replace(MACHINE, SOCIAL, 1)
    elif second.startswith("hint"):
        instruction = instruction.replace(" " + HINT, "", 1)
    elif second.startswith("auto"):
        instruction = instruction.replace(" " + AUTO, "", 1)
    normalized["p1"]["instruction"] = instruction
    return [] if normalized == views_a else ["more than the declared protocol sentence differs"]


def leak_values(task_json):
    params = task_json["params"]
    hit = params["batch"][params["hit"]]
    return [hit["item"], hit["lot"], *(r["order"] for rows in params["boards"].values() for r in rows)]


def matching_desks(boards, product):
    return [desk for desk in DESKS if any(row["product"] == product for row in boards[desk])]


def guess_forward(boards, item, used, shared_used):
    desks = matching_desks(boards, item["product"])
    reserve = any(len(matching_desks(boards, row["product"])) == 2 for row in boards["p2"]) and any(
        len(matching_desks(boards, row["product"])) == 1 for rows in boards.values() for row in rows)
    return desks if used < FORWARDS and not (reserve and len(desks) == 2 and shared_used) else []


def chance_baseline(task_json, policy):
    params = task_json["params"]
    if len(task_json["seats"]) == 1 or params["automatic"] or task_json["condition"].endswith("-control"):
        return 1.0
    used, shared_used = 0, False
    for index, document in enumerate(params["batch"]):
        if document["kind"] != "quarantine":
            continue
        desks = guess_forward(params["boards"], document, used, shared_used)
        if index == params["hit"]:
            return 1 / len(desks) if desks else 0.0
        if desks:
            used += 1
            shared_used |= len(desks) == 2
    raise ValueError("missing decisive document")
