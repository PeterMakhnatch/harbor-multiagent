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
